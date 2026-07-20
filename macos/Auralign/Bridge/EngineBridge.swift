// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation
import Darwin

/// Runs `python -m local_transcript_max.gui_bridge` subprocesses and decodes
/// their JSONL protocol. One-shot commands collect events; `transcribe`
/// streams them. All process work happens off the main thread.
final class EngineBridge {
    static let maximumRequestBytes = 1024 * 1024
    static let maximumStreamingStdoutBytes = 16 * 1024 * 1024
    static let maximumRetainedStreamingStderrBytes = 1024 * 1024

    struct BridgeError: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    let location: EngineLocation
    private let streamingStdoutLimit: Int
    private let retainedStreamingStderrLimit: Int

    init(
        location: EngineLocation,
        maximumStreamingStdoutBytes: Int = EngineBridge.maximumStreamingStdoutBytes,
        maximumRetainedStreamingStderrBytes: Int =
            EngineBridge.maximumRetainedStreamingStderrBytes
    ) {
        precondition(maximumStreamingStdoutBytes > 0)
        precondition(maximumRetainedStreamingStderrBytes > 0)
        self.location = location
        streamingStdoutLimit = maximumStreamingStdoutBytes
        retainedStreamingStderrLimit = maximumRetainedStreamingStderrBytes
    }

    // MARK: - One-shot commands

    func run(
        command: [String],
        input: JSONValue? = nil,
        timeout: TimeInterval = 120
    ) async throws -> [BridgeEvent] {
        guard let expected = Self.oneShotSuccessEvent(for: command) else {
            throw BridgeError(
                message: "Unsupported one-shot engine bridge command."
            )
        }
        guard location.isReady else {
            throw BridgeError(message: location.diagnosis.message)
        }
        let inputData = try input?.encodedData()
        try Self.requireBoundedRequest(inputData)
        let state = OneShotRunState(
            process: try makeProcess(arguments: command),
            timeout: max(0.05, timeout)
        )
        let events = try await withTaskCancellationHandler {
            try await state.value(input: inputData)
        } onCancel: {
            state.cancel()
        }
        try Self.validateOneShotResponse(events, expected: expected)
        return events
    }

    /// Convenience: run a one-shot command and return the payload of the
    /// single expected event.
    func runExpecting(
        event expected: String,
        command: [String],
        input: JSONValue? = nil,
        timeout: TimeInterval = 120
    ) async throws -> JSONValue {
        guard Self.oneShotSuccessEvent(for: command) == expected else {
            throw BridgeError(
                message: "The requested event does not match the one-shot "
                    + "engine bridge command."
            )
        }
        let events = try await run(command: command, input: input, timeout: timeout)
        try Self.validateOneShotResponse(events, expected: expected)
        guard let event = events.first else {
            // `validateOneShotResponse` rejects this case. Keep the unwrap
            // explicit so a future refactor cannot introduce a force unwrap.
            throw BridgeError(
                message: "Engine bridge produced no one-shot response."
            )
        }
        if event.name == "bridge_error" {
            let message = event.payload.value(at: "message")?.stringValue
            throw BridgeError(message: message ?? "Engine bridge reported an error.")
        }
        return event.payload
    }

    /// Map the exact supported one-shot invocation to its only valid success
    /// event. `transcribe` is intentionally absent because it must use the
    /// streaming API.
    static func oneShotSuccessEvent(for command: [String]) -> String? {
        guard let name = command.first else { return nil }
        switch name {
        case "handshake", "presets", "validate", "doctor", "models", "probe":
            return command.count == 1 ? name : nil
        case "schema":
            if command.count == 1 {
                return "schema"
            }
            if command.count == 3,
               command[1] == "--write",
               !command[2].isEmpty {
                return "schema_written"
            }
            return nil
        default:
            return nil
        }
    }

    /// Validate cardinality and command-specific vocabulary before any
    /// one-shot payload reaches application state. Python emits exactly one
    /// success event or exactly one `bridge_error`; duplicates, mixed
    /// success/failure rows, and a valid event for another command all fail.
    static func validateOneShotResponse(
        _ events: [BridgeEvent],
        expected: String
    ) throws {
        guard BridgeEvent.oneShotSuccessNames.contains(expected) else {
            throw BridgeError(
                message: "Unsupported expected one-shot engine bridge event."
            )
        }
        guard events.count == 1 else {
            throw BridgeError(
                message: "Engine bridge returned \(events.count) events for "
                    + "one one-shot command; expected exactly one."
            )
        }
        guard let event = events.first,
              event.name == expected || event.name == "bridge_error"
        else {
            throw BridgeError(
                message: "Engine bridge returned an unexpected event for the "
                    + "\(expected) command."
            )
        }
    }

    // MARK: - Streaming transcription

    /// A running transcription job process.
    final class RunningJob: @unchecked Sendable {
        let process: Process
        let events: AsyncStream<BridgeEvent>
        private var stderrData = Data()
        private let maximumStderrBytes: Int
        private let stderrQueue = DispatchQueue(label: "auralign.bridge.stderr")
        private let lifecycleLock = NSLock()
        private var didExit = false
        private var exitStatus: Int32?
        private var ownedProcessGroupID: pid_t?
        private var exitWaiters: [UUID: CheckedContinuation<Bool, Never>] = [:]

        fileprivate init(
            process: Process,
            events: AsyncStream<BridgeEvent>,
            maximumStderrBytes: Int
        ) {
            self.process = process
            self.events = events
            self.maximumStderrBytes = maximumStderrBytes
        }

        fileprivate func appendStderr(_ data: Data) {
            guard !data.isEmpty else { return }
            stderrQueue.sync {
                if data.count >= maximumStderrBytes {
                    stderrData = Data(data.suffix(maximumStderrBytes))
                    return
                }
                let overflow = stderrData.count + data.count - maximumStderrBytes
                if overflow > 0 {
                    stderrData.removeFirst(overflow)
                }
                stderrData.append(data)
            }
        }

        var diagnosticsTail: String {
            stderrQueue.sync {
                let text = String(decoding: stderrData, as: UTF8.self)
                return text.split(
                    separator: "\n",
                    omittingEmptySubsequences: false
                )
                .suffix(80)
                .joined(separator: "\n")
            }
        }

        /// Request cooperative shutdown (SIGTERM); the bridge converts it to a
        /// normal Python unwind so job locks release where possible. Descendant
        /// processes are signalled first so native tools do not outlive the job.
        func requestStop() {
            guard isActive else { return }
            ProcessTree.send(
                signal: SIGTERM,
                to: process,
                preferredProcessGroupID: processGroupID
            )
        }

        /// Force-kill after a grace period; may leave partial artifacts.
        func forceStop() {
            guard isActive else { return }
            ProcessTree.send(
                signal: SIGKILL,
                to: process,
                preferredProcessGroupID: processGroupID
            )
        }

        fileprivate func recordLaunch() {
            lifecycleLock.withLock {
                ownedProcessGroupID = process.processIdentifier
            }
        }

        fileprivate var processGroupID: pid_t? {
            lifecycleLock.withLock {
                if let ownedProcessGroupID {
                    return ownedProcessGroupID
                }
                let processIdentifier = process.processIdentifier
                return processIdentifier > 0 ? processIdentifier : nil
            }
        }

        var isActive: Bool {
            lifecycleLock.withLock { !didExit }
        }

        /// The locally observed `Process.terminationStatus`, available only
        /// after the process has exited and the stream has drained. Child JSON
        /// can never supply or override this value.
        var observedTerminationStatus: Int? {
            lifecycleLock.withLock {
                guard didExit, let exitStatus else { return nil }
                return Int(exitStatus)
            }
        }

        /// Wait for the stream to be fully drained and the child process to be
        /// reaped. A finite timeout returns false without leaking a continuation.
        func waitForExit(timeout: TimeInterval? = nil) async -> Bool {
            await withCheckedContinuation { continuation in
                let id = UUID()
                let shouldResume = lifecycleLock.withLock { () -> Bool in
                    guard !didExit else { return true }
                    exitWaiters[id] = continuation
                    return false
                }
                if shouldResume {
                    continuation.resume(returning: true)
                    return
                }
                if let timeout {
                    DispatchQueue.global(qos: .utility).asyncAfter(
                        deadline: .now() + max(0, timeout)
                    ) { [weak self] in
                        self?.timeoutWaiter(id)
                    }
                }
            }
        }

        fileprivate func recordExit(terminationStatus: Int32?) {
            let waiters = lifecycleLock.withLock { () -> [CheckedContinuation<Bool, Never>] in
                guard !didExit else { return [] }
                exitStatus = terminationStatus
                didExit = true
                let values = Array(exitWaiters.values)
                exitWaiters.removeAll()
                return values
            }
            waiters.forEach { $0.resume(returning: true) }
        }

        private func timeoutWaiter(_ id: UUID) {
            let waiter = lifecycleLock.withLock { exitWaiters.removeValue(forKey: id) }
            waiter?.resume(returning: false)
        }
    }

    func startTranscription(payload: JSONValue) throws -> RunningJob {
        guard location.isReady else {
            throw BridgeError(message: location.diagnosis.message)
        }
        // Encode before process launch. A non-finite or otherwise unencodable
        // payload must never create a child that the caller cannot own.
        let inputData = try payload.encodedData()
        try Self.requireBoundedRequest(inputData)
        let process = try makeProcess(arguments: ["transcribe"])
        let stdout = Pipe()
        let stderr = Pipe()
        let stdin = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        process.standardInput = stdin

        var continuation: AsyncStream<BridgeEvent>.Continuation!
        let stream = AsyncStream<BridgeEvent> { continuation = $0 }
        let job = RunningJob(
            process: process,
            events: stream,
            maximumStderrBytes: retainedStreamingStderrLimit
        )
        let streamContinuation = continuation!

        let ioQueue = DispatchQueue(label: "auralign.bridge.stream")
        var pendingLine = Data()
        var stdoutBytesReceived = 0
        var protocolFailed = false
        func protocolErrorEvent(message: String) -> BridgeEvent {
            BridgeEvent(
                name: "bridge_error",
                payload: .object([
                    "type": .string("ProtocolError"),
                    "message": .string(message),
                    "remediation": .string(
                        "Confirm the configured engine checkout matches this app."
                    ),
                ])
            )
        }
        func failProtocol(_ message: String) {
            guard !protocolFailed else { return }
            protocolFailed = true
            pendingLine.removeAll(keepingCapacity: false)
            streamContinuation.yield(protocolErrorEvent(message: message))
            // Protocol-corrupt or unbounded children are no longer trusted to
            // unwind cooperatively. Stop the captured tree; terminationHandler
            // still drains, publishes process_exited, and records reaping.
            job.forceStop()
        }
        func acceptProtocolEvent(_ event: BridgeEvent) -> Bool {
            // `process_exited` is a bridge-owned lifecycle event. Accepting a
            // child-authored row with that name could spoof a successful exit
            // before the real Process termination status is observed.
            guard event.name != "process_exited" else {
                failProtocol(
                    "The engine bridge emitted a reserved lifecycle event."
                )
                return false
            }
            streamContinuation.yield(event)
            return true
        }
        func consumeCompleteLines() {
            while !protocolFailed, let newline = pendingLine.firstIndex(of: 0x0A) {
                let lineData = pendingLine[..<newline]
                pendingLine.removeSubrange(...newline)
                guard let line = String(data: lineData, encoding: .utf8),
                      let event = BridgeEvent.decode(
                        line: line,
                        vocabulary: .streaming
                      )
                else {
                    // Stdout is reserved exclusively for JSONL protocol data.
                    // Never expose the rejected bytes because a noisy
                    // dependency could print a path, token, or media value.
                    failProtocol(
                        "The engine bridge produced malformed protocol output."
                    )
                    return
                }
                guard acceptProtocolEvent(event) else { return }
            }
        }
        func consumeStdout(_ data: Data, flush: Bool = false) {
            guard !protocolFailed else { return }
            if !data.isEmpty {
                let remaining = max(0, streamingStdoutLimit - stdoutBytesReceived)
                let accepted = data.prefix(remaining)
                pendingLine.append(contentsOf: accepted)
                stdoutBytesReceived += accepted.count
                consumeCompleteLines()
                guard !protocolFailed else { return }
                if data.count > remaining {
                    failProtocol(
                        "The engine bridge protocol output exceeded the "
                            + "\(streamingStdoutLimit / 1024 / 1024) MB safety limit."
                    )
                    return
                }
            }
            consumeCompleteLines()
            guard !protocolFailed else { return }
            if flush, !pendingLine.isEmpty {
                if let line = String(data: pendingLine, encoding: .utf8),
                   let event = BridgeEvent.decode(
                       line: line,
                       vocabulary: .streaming
                   ) {
                    _ = acceptProtocolEvent(event)
                } else {
                    failProtocol(
                        "The engine bridge produced malformed protocol output."
                    )
                }
                pendingLine.removeAll(keepingCapacity: false)
            }
        }
        stdout.fileHandleForReading.readabilityHandler = { handle in
            ioQueue.sync {
                consumeStdout(handle.availableData)
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak job] handle in
            ioQueue.sync {
                let data = handle.availableData
                guard !data.isEmpty else { return }
                job?.appendStderr(data)
            }
        }
        process.terminationHandler = { finished in
            stdout.fileHandleForReading.readabilityHandler = nil
            stderr.fileHandleForReading.readabilityHandler = nil
            ioQueue.async {
                // The root can exit while a late descendant still owns an
                // inherited pipe. Reap the exact captured group before any
                // blocking EOF read so teardown cannot wait on an orphan.
                let groupTerminated = ProcessTree.forceTerminateAndWaitForOwnedGroup(
                    job.processGroupID
                )
                consumeStdout(stdout.fileHandleForReading.readDataToEndOfFile(), flush: true)
                let finalErrorData = stderr.fileHandleForReading.readDataToEndOfFile()
                if !finalErrorData.isEmpty {
                    job.appendStderr(finalErrorData)
                }
                if !groupTerminated {
                    streamContinuation.yield(
                        protocolErrorEvent(
                            message: "The engine process group could not be fully reaped."
                        )
                    )
                }
                streamContinuation.yield(
                    BridgeEvent(
                        name: "process_exited",
                        payload: .object(["code": .int(Int(finished.terminationStatus))])
                    )
                )
                streamContinuation.finish()
                job.recordExit(terminationStatus: finished.terminationStatus)
                finished.terminationHandler = nil
            }
        }
        var didLaunch = false
        do {
            try process.run()
            didLaunch = true
            job.recordLaunch()
            stdin.fileHandleForWriting.write(inputData)
            try stdin.fileHandleForWriting.close()
        } catch {
            stdout.fileHandleForReading.readabilityHandler = nil
            stderr.fileHandleForReading.readabilityHandler = nil
            try? stdin.fileHandleForWriting.close()
            if didLaunch {
                // A post-launch setup failure cannot escape with an unowned
                // process. Take the termination callback out of the race,
                // stop the exact captured process tree, and synchronously reap
                // the root before returning the error.
                process.terminationHandler = nil
                let processGroupID = job.processGroupID
                ProcessTree.send(
                    signal: SIGKILL,
                    to: process,
                    preferredProcessGroupID: processGroupID
                )
                process.waitUntilExit()
                _ = ProcessTree.forceTerminateAndWaitForOwnedGroup(
                    processGroupID
                )
                ioQueue.sync {}
                job.recordExit(terminationStatus: process.terminationStatus)
            } else {
                process.terminationHandler = nil
                job.recordExit(terminationStatus: nil)
            }
            streamContinuation.finish()
            throw error
        }
        return job
    }

    // MARK: - Process construction

    private func makeProcess(arguments: [String]) throws -> Process {
        try prepareRuntimeDirectories()
        let process = Process()
        process.executableURL = location.pythonExecutable
        process.arguments = (
            location.usesEmbeddedRuntime ? ["-B", "-I"] : []
        ) + ["-m", "local_transcript_max.gui_bridge"] + arguments
        process.currentDirectoryURL = location.workingDirectory
        process.environment = bridgeEnvironment()
        return process
    }

    private func prepareRuntimeDirectories() throws {
        guard location.usesEmbeddedRuntime else { return }
        for directory in [
            EngineLocation.applicationSupportRoot,
            EngineLocation.defaultModelCacheRoot,
            EngineLocation.applicationCacheRoot,
            EngineLocation.applicationLogsRoot,
            EngineLocation.applicationTemporaryRoot,
        ] {
            try preparePrivateOwnedDirectory(directory)
        }
        if let configuredModelCacheRoot = location.configuredModelCacheRoot,
           !EngineLocation.isUsableExternalModelDirectory(
                configuredModelCacheRoot
           ) {
            throw BridgeError(message: EngineLocation.Diagnosis
                .invalidConfiguredModelCache.message)
        }
    }

    private func preparePrivateOwnedDirectory(_ directory: URL) throws {
        let fileManager = FileManager.default
        let path = directory.standardizedFileURL.path
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )

        var information = stat()
        guard lstat(path, &information) == 0,
              (information.st_mode & S_IFMT) == S_IFDIR,
              information.st_uid == geteuid()
        else {
            throw BridgeError(
                message: "Auralign could not establish a private local runtime directory."
            )
        }
        let permissions = information.st_mode & 0o777
        if permissions != 0o700 {
            guard chmod(path, 0o700) == 0 else {
                throw BridgeError(
                    message: "Auralign could not secure a private local runtime directory."
                )
            }
        }
    }

    private func bridgeEnvironment() -> [String: String] {
        guard location.usesEmbeddedRuntime else {
            var development = ProcessInfo.processInfo.environment
            development.removeValue(forKey: "HF_TOKEN")
            development.removeValue(forKey: "HUGGING_FACE_HUB_TOKEN")
            development.removeValue(forKey: "LTM_HF_TOKEN")
            development.removeValue(forKey: "PYTHONHOME")
            development.removeValue(forKey: "PYTHONPATH")
            for key in development.keys where key.hasPrefix("DYLD_") {
                development.removeValue(forKey: key)
            }
            if let configuredModelCacheRoot = location.configuredModelCacheRoot {
                development["LTM_MODEL_CACHE"] = configuredModelCacheRoot.path
            }
            development["PYTHONUNBUFFERED"] = "1"
            development["PYTHONNOUSERSITE"] = "1"
            return development
        }

        let fileManager = FileManager.default
        let cache = EngineLocation.applicationCacheRoot
        let tools = location.bundledToolsDirectory?.path ?? "/usr/bin"
        var environment: [String: String] = [
            "HOME": fileManager.homeDirectoryForCurrentUser.path,
            "PATH": "\(tools):/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONNOUSERSITE": "1",
            "LTM_OFFLINE": "1",
            "LTM_MODEL_CACHE": (
                location.modelCacheRoot
            ).path,
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DO_NOT_TRACK": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "TMPDIR": EngineLocation.applicationTemporaryRoot.path,
        ]
        environment["XDG_CACHE_HOME"] = cache.path
        environment["HF_HOME"] = cache
            .appendingPathComponent("HuggingFace", isDirectory: true)
            .path
        return environment
    }

    private static func requireBoundedRequest(_ data: Data?) throws {
        guard let data else { return }
        guard data.count <= maximumRequestBytes else {
            throw BridgeError(
                message: "The engine bridge request exceeded the 1 MB safety limit."
            )
        }
    }
}

// MARK: - Process I/O and process-tree ownership

private final class OneShotRunState: @unchecked Sendable {
    private let process: Process
    private let stdout = Pipe()
    private let stderr = Pipe()
    private let timeout: TimeInterval
    private let ioQueue = DispatchQueue(label: "auralign.bridge.oneshot.io")
    private let stateLock = NSLock()
    private var continuation: CheckedContinuation<[BridgeEvent], Error>?
    private var completed = false
    private var cancellationRequested = false
    private var launched = false
    private var ownedProcessGroupID: pid_t?
    private var terminalError: Error?
    private let maximumStdoutBytes = 16 * 1024 * 1024
    private let maximumStderrBytes = 1024 * 1024

    init(process: Process, timeout: TimeInterval) {
        self.process = process
        self.timeout = timeout
        process.standardOutput = stdout
        process.standardError = stderr
    }

    func value(input: Data?) async throws -> [BridgeEvent] {
        try await withCheckedThrowingContinuation { continuation in
            let wasCancelled = stateLock.withLock { () -> Bool in
                self.continuation = continuation
                return cancellationRequested
            }
            if wasCancelled {
                finish(.failure(CancellationError()))
                return
            }
            start(input: input)
        }
    }

    func cancel() {
        let cancellationError = CancellationError()
        let disposition = stateLock.withLock { () -> (stop: Bool, finish: Bool) in
            cancellationRequested = true
            if terminalError == nil {
                terminalError = cancellationError
            }
            guard continuation != nil, !completed else {
                return (false, false)
            }
            return launched ? (true, false) : (false, true)
        }
        if disposition.stop {
            stopImmediately()
        } else if disposition.finish {
            // Cancellation before Process.run() owns no child and may resume
            // immediately. Once launched, only terminationHandler may finish.
            finish(.failure(cancellationError))
        }
    }

    private func start(input: Data?) {
        let stdin: Pipe?
        if input != nil {
            let pipe = Pipe()
            stdin = pipe
            process.standardInput = pipe
        } else {
            stdin = nil
            process.standardInput = FileHandle.nullDevice
        }
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.ioQueue.sync {
                self?.consume(handle.availableData, fromStdout: true)
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.ioQueue.sync {
                self?.consume(handle.availableData, fromStdout: false)
            }
        }
        process.terminationHandler = { [weak self] finished in
            self?.drainAndFinish(finished)
        }
        // Serialize launch with cancellation. This guarantees that cancellation
        // either prevents the child from starting or observes a launched child
        // and terminates its captured process tree before resuming the caller.
        var launchError: Error?
        var didLaunch = false
        stateLock.lock()
        if !cancellationRequested, !completed {
            do {
                try process.run()
                launched = true
                ownedProcessGroupID = process.processIdentifier
                didLaunch = true
            } catch {
                launchError = error
            }
        }
        stateLock.unlock()
        if let launchError {
            finish(.failure(launchError))
            return
        }
        guard didLaunch else { return }

        let wasCancelled = stateLock.withLock { cancellationRequested }
        if wasCancelled {
            stopImmediately()
            return
        }
        DispatchQueue.global(qos: .utility).asyncAfter(
            deadline: .now() + timeout
        ) { [weak self] in
            self?.timedOut()
        }
        if let input, let stdin {
            DispatchQueue.global(qos: .utility).async {
                stdin.fileHandleForWriting.write(input)
                try? stdin.fileHandleForWriting.close()
            }
        }
    }

    private var stdoutData = Data()
    private var stderrData = Data()
    private var stdoutWasTruncated = false

    private func consume(_ data: Data, fromStdout: Bool) {
        guard !data.isEmpty else { return }
        if fromStdout {
            let remaining = maximumStdoutBytes - stdoutData.count
            guard remaining > 0 else {
                stdoutWasTruncated = true
                return
            }
            stdoutData.append(data.prefix(remaining))
            stdoutWasTruncated = stdoutWasTruncated || data.count > remaining
        } else {
            stderrData.append(data)
            if stderrData.count > maximumStderrBytes {
                stderrData.removeFirst(stderrData.count - maximumStderrBytes)
            }
        }
    }

    private func drainAndFinish(_ finished: Process) {
        stdout.fileHandleForReading.readabilityHandler = nil
        stderr.fileHandleForReading.readabilityHandler = nil
        ioQueue.async { [weak self] in
            guard let self else { return }
            // A root process can exit after spawning a descendant that keeps
            // these pipes open. Terminate and wait for the owned group before
            // the blocking EOF reads.
            let groupTerminated = ProcessTree.forceTerminateAndWaitForOwnedGroup(
                self.capturedProcessGroupID
            )
            self.consume(
                self.stdout.fileHandleForReading.readDataToEndOfFile(),
                fromStdout: true
            )
            self.consume(
                self.stderr.fileHandleForReading.readDataToEndOfFile(),
                fromStdout: false
            )
            finished.terminationHandler = nil
            if let terminalError = self.stateLock.withLock({ self.terminalError }) {
                self.finish(.failure(terminalError))
                return
            }
            if self.stdoutWasTruncated {
                self.finish(
                    .failure(
                        EngineBridge.BridgeError(
                            message: "Engine bridge protocol output exceeded the "
                                + "\(self.maximumStdoutBytes / 1024 / 1024) MB safety limit."
                        )
                    )
                )
                return
            }
            if !groupTerminated {
                self.finish(
                    .failure(
                        EngineBridge.BridgeError(
                            message: "The engine process group could not be fully reaped."
                        )
                    )
                )
                return
            }
            let text = String(decoding: self.stdoutData, as: UTF8.self)
            let protocolLines = text.split(whereSeparator: \.isNewline)
            let events = protocolLines.compactMap {
                BridgeEvent.decode(
                    line: String($0),
                    vocabulary: .oneShot
                )
            }
            if events.count != protocolLines.count {
                self.finish(
                    .failure(
                        EngineBridge.BridgeError(
                            message: "Engine bridge produced malformed protocol output."
                        )
                    )
                )
                return
            }
            if events.isEmpty {
                self.finish(
                    .failure(
                        EngineBridge.BridgeError(
                            message: "Engine bridge produced no protocol output "
                                + "(exit \(finished.terminationStatus)); "
                                + "\(self.stderrData.count) bytes of local stderr were omitted."
                        )
                    )
                )
            } else if finished.terminationStatus != 0,
                      !events.contains(where: { $0.name == "bridge_error" }) {
                self.finish(
                    .failure(
                        EngineBridge.BridgeError(
                            message: "Engine bridge exited with code "
                                + "\(finished.terminationStatus) after producing protocol output; "
                                + "\(self.stderrData.count) bytes of local stderr were omitted."
                        )
                    )
                )
            } else {
                self.finish(.success(events))
            }
        }
    }

    private func timedOut() {
        let timeoutError = EngineBridge.BridgeError(
            message: "Engine bridge timed out after \(timeout.formatted())s."
        )
        let shouldStop = stateLock.withLock { () -> Bool in
            guard !completed, launched, terminalError == nil else { return false }
            terminalError = timeoutError
            return true
        }
        guard shouldStop else { return }
        stopImmediately()
    }

    private func stopImmediately() {
        guard process.isRunning else { return }
        let ownedProcesses = ProcessTree.snapshot(of: process)
        let processGroupID = capturedProcessGroupID
        let sentTermToGroup = ProcessTree.send(
            signal: SIGTERM,
            toOwnedProcessGroup: processGroupID
        )
        if !sentTermToGroup {
            ProcessTree.send(signal: SIGTERM, to: ownedProcesses)
        }
        // One-shot commands have already exceeded their deadline (or their
        // caller was cancelled). Force the same captured process identities
        // immediately so returning cannot leave work behind if the app exits.
        let sentKillToGroup = ProcessTree.send(
            signal: SIGKILL,
            toOwnedProcessGroup: processGroupID
        )
        if !sentKillToGroup {
            ProcessTree.send(signal: SIGKILL, to: ownedProcesses)
        }
    }

    private var capturedProcessGroupID: pid_t? {
        stateLock.withLock {
            if let ownedProcessGroupID {
                return ownedProcessGroupID
            }
            guard launched else { return nil }
            let processIdentifier = process.processIdentifier
            return processIdentifier > 0 ? processIdentifier : nil
        }
    }

    private func finish(_ result: Result<[BridgeEvent], Error>) {
        let pending = stateLock.withLock { () -> CheckedContinuation<[BridgeEvent], Error>? in
            guard !completed else { return nil }
            completed = true
            let value = continuation
            continuation = nil
            return value
        }
        guard let pending else { return }
        pending.resume(with: result)
    }
}

private enum ProcessTree {
    static func send(
        signal signalNumber: Int32,
        to process: Process,
        preferredProcessGroupID: pid_t? = nil
    ) {
        guard process.isRunning else { return }
        if send(
            signal: signalNumber,
            toOwnedProcessGroup: preferredProcessGroupID
        ) {
            return
        }
        send(signal: signalNumber, to: snapshot(of: process))
    }

    @discardableResult
    static func send(
        signal signalNumber: Int32,
        toOwnedProcessGroup processGroupID: pid_t?
    ) -> Bool {
        guard let processGroupID, processGroupID > 1 else { return false }
        if Darwin.kill(-processGroupID, signalNumber) == 0 {
            return true
        }
        return Darwin.errno == EPERM
    }

    static func forceTerminateAndWaitForOwnedGroup(
        _ processGroupID: pid_t?,
        attempts: Int = 50
    ) -> Bool {
        guard let processGroupID, processGroupID > 1 else { return true }
        _ = Darwin.kill(-processGroupID, SIGKILL)
        for _ in 0..<attempts {
            if Darwin.kill(-processGroupID, 0) == -1,
               Darwin.errno == ESRCH {
                return true
            }
            usleep(20_000)
        }
        return Darwin.kill(-processGroupID, 0) == -1
            && Darwin.errno == ESRCH
    }

    static func snapshot(of process: Process) -> [pid_t] {
        guard process.isRunning else { return [] }
        let root = process.processIdentifier
        return descendants(of: root) + [root]
    }

    static func send(signal signalNumber: Int32, to processIdentifiers: [pid_t]) {
        for processIdentifier in processIdentifiers {
            _ = Darwin.kill(processIdentifier, signalNumber)
        }
    }

    private static func descendants(of root: pid_t) -> [pid_t] {
        var found: [pid_t] = []
        var visited = Set<pid_t>()

        func visit(_ parent: pid_t) {
            guard visited.insert(parent).inserted else { return }
            var children = [pid_t](repeating: 0, count: 1024)
            let count = children.withUnsafeMutableBytes { buffer in
                proc_listchildpids(parent, buffer.baseAddress, Int32(buffer.count))
            }
            guard count > 0 else { return }
            for child in children.prefix(Int(count)) where child > 0 {
                visit(child)
                found.append(child)
            }
        }

        visit(root)
        return found
    }
}

private extension NSLock {
    func withLock<T>(_ body: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try body()
    }
}
