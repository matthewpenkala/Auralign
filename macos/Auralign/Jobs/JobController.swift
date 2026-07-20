// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import Darwin
import Foundation
import SwiftUI

@MainActor
protocol WorkspaceOpening {
    func open(_ url: URL) -> Bool
}

@MainActor
struct SystemWorkspaceOpener: WorkspaceOpening {
    func open(_ url: URL) -> Bool {
        NSWorkspace.shared.open(url)
    }
}

/// One pipeline stage row in the processing view. Stages come from real
/// bridge events only; nothing here is simulated.
struct PipelineStage: Identifiable, Equatable {
    enum Status: Equatable {
        case pending
        case active
        case done(seconds: Double?)
        case skipped(reason: String)
    }

    var id: String
    var title: String
    var status: Status = .pending
    var detail: String = ""
}

struct JobArtifact: Identifiable, Equatable {
    var name: String
    var sizeBytes: Int64
    var url: URL
    var identity: ArtifactFileIdentity

    var id: String { name }

    var sizeText: String {
        ByteCountFormatter.string(fromByteCount: sizeBytes, countStyle: .file)
    }
}

/// A bounded local-file freshness snapshot. This detects replacement and
/// ordinary in-place mutation between media inspection, launch, and Results.
/// It is intentionally not presented as a cryptographic content hash.
struct SourceFileSnapshot: Equatable {
    var canonicalURL: URL
    var identity: ArtifactFileIdentity
    var sizeBytes: Int64
    var modificationSeconds: Int64
    var modificationNanoseconds: Int64

    static func capture(at requestedURL: URL?) -> SourceFileSnapshot? {
        guard let requestedURL, requestedURL.isFileURL else { return nil }
        let canonicalURL = requestedURL
            .standardizedFileURL
            .resolvingSymlinksInPath()
            .standardizedFileURL
        guard (canonicalURL.path as NSString).isAbsolutePath else { return nil }

        let descriptor = canonicalURL.path.withCString {
            Darwin.open(
                $0,
                O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC
            )
        }
        guard descriptor >= 0 else { return nil }
        defer { Darwin.close(descriptor) }

        var descriptorStatus = stat()
        guard Darwin.fstat(descriptor, &descriptorStatus) == 0,
              (descriptorStatus.st_mode & S_IFMT) == S_IFREG,
              descriptorStatus.st_size >= 0,
              descriptorStatus.st_dev >= 0,
              descriptorStatus.st_ino >= 0
        else { return nil }

        var pathStatus = stat()
        let pathResult = canonicalURL.path.withCString {
            Darwin.lstat($0, &pathStatus)
        }
        guard pathResult == 0,
              (pathStatus.st_mode & S_IFMT) == S_IFREG,
              pathStatus.st_dev == descriptorStatus.st_dev,
              pathStatus.st_ino == descriptorStatus.st_ino,
              pathStatus.st_size == descriptorStatus.st_size,
              pathStatus.st_mtimespec.tv_sec == descriptorStatus.st_mtimespec.tv_sec,
              pathStatus.st_mtimespec.tv_nsec == descriptorStatus.st_mtimespec.tv_nsec
        else { return nil }

        return SourceFileSnapshot(
            canonicalURL: canonicalURL,
            identity: ArtifactFileIdentity(
                device: UInt64(descriptorStatus.st_dev),
                inode: UInt64(descriptorStatus.st_ino)
            ),
            sizeBytes: Int64(descriptorStatus.st_size),
            modificationSeconds: Int64(descriptorStatus.st_mtimespec.tv_sec),
            modificationNanoseconds: Int64(descriptorStatus.st_mtimespec.tv_nsec)
        )
    }
}

struct JobResult: Equatable {
    var outputDirectory: URL
    var sourceURL: URL
    var sourceSnapshot: SourceFileSnapshot?
    var language: String?
    var speakerTurns: Int
    var words: Int
    var segments: Int
    var artifacts: [JobArtifact]
    var configFingerprint: String
    var warnings: [String]
    var qa: ResultQAProjection
    var transcriptPreview: ResultTranscriptPreview
}

struct JobFailure: Equatable {
    var errorType: String
    var message: String
    var remediation: String?
    var diagnostics: String
    var partialArtifacts: [JobArtifact]
    var outputDirectory: URL?
}

/// Immutable display projection of the effective configuration captured when
/// the job started. Processing never reads the mutable editor configuration.
struct JobConfigurationSummary: Equatable {
    var preset: String
    var asrModel: String
    var asrBackend: String
    var diarization: String
    var alignment: String
    var offline: Bool

    init(config: JSONValue) {
        preset = config.value(at: "preset")?.stringValue ?? "custom"
        asrBackend = config.value(at: "asr.backend")?.stringValue ?? "unknown"
        if asrBackend == "mlx-whisper" {
            asrModel = config.value(at: "asr.mlx_model")?.stringValue
                ?? config.value(at: "asr.model")?.stringValue
                ?? "unknown"
        } else {
            asrModel = config.value(at: "asr.model")?.stringValue ?? "unknown"
        }
        let diarizationEnabled = config.value(at: "diarization.enabled")?.boolValue ?? false
        diarization = diarizationEnabled
            ? (config.value(at: "diarization.backend")?.stringValue ?? "unknown")
            : "off"
        alignment = (config.value(at: "alignment.enabled")?.boolValue ?? false)
            ? (config.value(at: "alignment.backend")?.stringValue ?? "unknown")
            : "off"
        offline = config.value(at: "offline")?.boolValue ?? false
    }
}

/// Owns one transcription job's lifecycle: launch, event consumption,
/// cancellation, and result collection.
@MainActor
final class JobController: ObservableObject {
    private enum TerminalIntent: Equatable {
        case failure
        case cancellation
    }

    enum Phase: Equatable {
        case idle
        case running
        /// The engine published a provisional result, but its process still
        /// owns the job and must exit successfully and be reaped before any
        /// result becomes final.
        case resultPendingExit
        case stopping
        case completed(JobResult)
        case failed(JobFailure)
        case cancelled(outputDirectory: URL?, partialArtifacts: [JobArtifact])
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var stages: [PipelineStage] = []
    @Published private(set) var warnings: [String] = []
    @Published private(set) var startedAt: Date?
    @Published private(set) var finishedAt: Date?
    @Published private(set) var recentEvents: [String] = []
    @Published private(set) var jobDirectory: URL?
    @Published private(set) var runConfiguration: JobConfigurationSummary?
    @Published private(set) var artifactActionError: String?
    /// Fingerprint of the configuration the last completed run used.
    @Published private(set) var lastRunFingerprint: String?

    private var runningJob: EngineBridge.RunningJob?
    private var consumeTask: Task<Void, Never>?
    private var forceStopTask: Task<Void, Never>?
    private var sawFailureEvent: JobFailure?
    private var terminalIntent: TerminalIntent?
    private var sawResultEvent = false
    /// A bridge result is not success until the child exits zero and the
    /// streaming process has been reaped. Keep the candidate private so
    /// Results, fingerprints, and completion UI cannot outrun ownership.
    private var resultPendingExit: PendingJobResultEnvelope?
    private var runSourceURL: URL?
    private var runSourceSnapshot: SourceFileSnapshot?
    private var acceptedOutputIdentity: ArtifactFileIdentity?
    private var artifactAccess: JobArtifactAccess?
    private var verifiedJobOutput: VerifiedJobOutput?
    private var finalDiagnosticsTail = ""
    private var presentationNow: Date?
    private let workspaceOpener: any WorkspaceOpening
    #if DEBUG
    /// Presentation-only ownership sentinel. It never represents or controls
    /// a Process and is set only by the dual-gated fixture factory.
    private var fixtureOwnsActiveProcess = false
    #endif

    init(workspaceOpener: (any WorkspaceOpening)? = nil) {
        self.workspaceOpener = workspaceOpener ?? SystemWorkspaceOpener()
    }

    var isRunning: Bool {
        if case .running = phase { return true }
        if case .resultPendingExit = phase { return true }
        if case .stopping = phase { return true }
        return false
    }

    /// The window and menu use one process-ownership projection so a second
    /// cancellation request cannot race termination/reaping.
    var showsCancellationAuthority: Bool {
        guard ownsActiveProcess else { return false }
        switch phase {
        case .running, .resultPendingExit, .stopping:
            return true
        case .idle, .completed, .failed, .cancelled:
            return false
        }
    }

    var canRequestCancellation: Bool {
        guard ownsActiveProcess else { return false }
        switch phase {
        case .running, .resultPendingExit:
            return true
        case .idle, .stopping, .completed, .failed, .cancelled:
            return false
        }
    }

    var hasResultSurface: Bool {
        switch phase {
        case .completed:
            return true
        case .failed(let failure):
            return failure.outputDirectory != nil || !failure.partialArtifacts.isEmpty
        case .cancelled(let outputDirectory, let partialArtifacts):
            return outputDirectory != nil || !partialArtifacts.isEmpty
        case .idle, .running, .resultPendingExit, .stopping:
            return false
        }
    }

    /// A result line can precede actual process exit. The controller retains
    /// ownership until process_exited is drained and the child is reaped.
    var ownsActiveProcess: Bool {
        #if DEBUG
        return runningJob != nil || fixtureOwnsActiveProcess
        #else
        return runningJob != nil
        #endif
    }

    var elapsed: TimeInterval? {
        guard let startedAt else { return nil }
        return (finishedAt ?? presentationNow ?? Date()).timeIntervalSince(startedAt)
    }

    // MARK: - Job directory naming

    /// Predictable, unique job directory inside the chosen output root:
    /// `<media-stem> 2026-07-18 14.30.05`. The engine independently enforces
    /// that the directory is fresh and empty.
    nonisolated static func proposedJobDirectory(
        outputRoot: URL,
        mediaURL: URL,
        now: Date = Date()
    ) -> URL {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH.mm.ss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let stem = mediaURL.deletingPathExtension().lastPathComponent
        let name = "\(stem) \(formatter.string(from: now))"
        return outputRoot.appendingPathComponent(name, isDirectory: true)
    }

    nonisolated static func jobDirectory(
        _ jobDirectory: URL,
        belongsTo outputRoot: URL
    ) -> Bool {
        let normalizedRoot = outputRoot
            .standardizedFileURL
            .resolvingSymlinksInPath()
        let normalizedJob = jobDirectory.standardizedFileURL
        let normalizedParent = normalizedJob
            .deletingLastPathComponent()
            .resolvingSymlinksInPath()
        return !normalizedJob.lastPathComponent.isEmpty
            && normalizedJob != normalizedRoot
            && normalizedParent == normalizedRoot
    }

    // MARK: - Start / stop

    func start(
        bridge: EngineBridge,
        input: URL,
        expectedSourceSnapshot: SourceFileSnapshot,
        outputRoot: URL,
        jobDirectory requestedJobDirectory: URL,
        config: JSONValue,
        configFingerprint: String,
        debugReport: Bool,
        expectations: JobExpectations
    ) {
        guard !ownsActiveProcess else { return }
        clearRunContextForNewAttempt()
        guard Self.jobDirectory(requestedJobDirectory, belongsTo: outputRoot) else {
            finishedAt = Date()
            phase = .failed(
                JobFailure(
                    errorType: "InvalidJobDirectory",
                    message: "The planned job folder is outside the selected output folder.",
                    remediation: "Choose the output folder again and start a new job.",
                    diagnostics: "",
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            )
            return
        }
        let canonicalRoot = outputRoot.standardizedFileURL.resolvingSymlinksInPath()
        let jobDir = canonicalRoot.appendingPathComponent(
            requestedJobDirectory.lastPathComponent,
            isDirectory: true
        )
        let access: JobArtifactAccess
        do {
            access = try JobArtifactAccess.retain(
                outputRoot: canonicalRoot,
                plannedJobDirectory: jobDir
            )
        } catch {
            finishedAt = Date()
            phase = .failed(
                JobFailure(
                    errorType: "OutputAuthorizationError",
                    message: "The selected output folder could not be authorized safely.",
                    remediation: "Choose the output folder again and start a fresh job.",
                    diagnostics: "",
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            )
            return
        }
        artifactAccess = access
        verifiedJobOutput = nil
        artifactActionError = nil
        jobDirectory = jobDir
        guard let sourceSnapshot = SourceFileSnapshot.capture(at: input),
              sourceSnapshot == expectedSourceSnapshot
        else {
            artifactAccess = nil
            jobDirectory = nil
            finishedAt = Date()
            phase = .failed(
                JobFailure(
                    errorType: "InvalidInput",
                    message: "The selected media changed after it was inspected.",
                    remediation: "Inspect the media again before starting a fresh job.",
                    diagnostics: "",
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            )
            return
        }
        let canonicalInput = sourceSnapshot.canonicalURL
        runSourceURL = canonicalInput
        runSourceSnapshot = sourceSnapshot
        runConfiguration = JobConfigurationSummary(config: config)
        stages = expectations.expectedStages()
        warnings = []
        recentEvents = []
        sawFailureEvent = nil
        terminalIntent = nil
        sawResultEvent = false
        resultPendingExit = nil
        finalDiagnosticsTail = ""
        presentationNow = nil
        #if DEBUG
        fixtureOwnsActiveProcess = false
        #endif
        startedAt = Date()
        finishedAt = nil
        phase = .running

        let payload = JSONValue.object([
            "input": .string(canonicalInput.path),
            "output_root": .string(canonicalRoot.path),
            "out_dir": .string(jobDir.path),
            "config": config,
            "debug_report": .bool(debugReport),
        ])
        do {
            let job = try bridge.startTranscription(payload: payload)
            runningJob = job
            consumeTask = Task { [weak self] in
                for await event in job.events {
                    await self?.handle(event: event, configFingerprint: configFingerprint)
                }
            }
        } catch {
            finishedAt = Date()
            runningJob = nil
            resultPendingExit = nil
            acceptedOutputIdentity = nil
            runSourceURL = nil
            runSourceSnapshot = nil
            artifactAccess = nil
            verifiedJobOutput = nil
            phase = .failed(
                JobFailure(
                    errorType: "LaunchError",
                    message: error.localizedDescription,
                    remediation: "Check the engine location in Settings.",
                    diagnostics: "",
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            )
        }
    }

    func cancel() {
        cancel(gracePeriod: 20)
    }

    private func cancel(gracePeriod: TimeInterval) {
        guard let runningJob else { return }
        guard isRunning else { return }
        guard phase != .stopping else { return }
        if terminalIntent == nil {
            terminalIntent = .cancellation
        }
        phase = .stopping
        runningJob.requestStop()
        forceStopTask?.cancel()
        forceStopTask = Task { [weak self] in
            try? await Task.sleep(
                nanoseconds: UInt64(max(0, gracePeriod) * 1_000_000_000)
            )
            guard !Task.isCancelled else { return }
            guard let self, self.runningJob === runningJob else { return }
            if runningJob.isActive {
                runningJob.forceStop()
            }
        }
    }

    /// Own application termination explicitly: request a cooperative unwind,
    /// force-stop the entire child tree after a short grace period, and do not
    /// allow the application to exit until the bridge process is reaped.
    func stopForApplicationTermination(gracePeriod: TimeInterval = 5) async {
        guard let runningJob else {
            #if DEBUG
            fixtureOwnsActiveProcess = false
            #endif
            return
        }
        let consumer = consumeTask
        let forceTimer = forceStopTask
        if terminalIntent == nil {
            terminalIntent = .cancellation
        }
        phase = .stopping
        forceTimer?.cancel()
        runningJob.requestStop()
        if !(await runningJob.waitForExit(timeout: gracePeriod)) {
            runningJob.forceStop()
            _ = await runningJob.waitForExit()
        }
        if let forceTimer {
            await forceTimer.value
        }
        if let consumer {
            await consumer.value
        }
        // `process_exited` normally finalizes inside the consumer. This fallback
        // closes ownership even if a malformed/truncated stream ended without
        // the reserved terminal event after the root was reaped.
        if self.runningJob === runningJob {
            finalizeAfterExit(
                code: runningJob.observedTerminationStatus ?? -1
            )
        }
    }

    func reset() {
        guard !ownsActiveProcess else { return }
        phase = .idle
        stages = []
        warnings = []
        recentEvents = []
        jobDirectory = nil
        runConfiguration = nil
        startedAt = nil
        finishedAt = nil
        lastRunFingerprint = nil
        resultPendingExit = nil
        acceptedOutputIdentity = nil
        runSourceURL = nil
        runSourceSnapshot = nil
        artifactAccess = nil
        verifiedJobOutput = nil
        artifactActionError = nil
        terminalIntent = nil
        sawResultEvent = false
        finalDiagnosticsTail = ""
        presentationNow = nil
        #if DEBUG
        fixtureOwnsActiveProcess = false
        #endif
    }

    @discardableResult
    func openArtifact(_ artifact: JobArtifact) -> Bool {
        performArtifactAction(artifact) { [workspaceOpener] url in
            guard workspaceOpener.open(url) else {
                throw ResultContractError("macOS could not open the artifact.")
            }
        }
    }

    @discardableResult
    func revealArtifact(_ artifact: JobArtifact) -> Bool {
        performArtifactAction(artifact) { url in
            NSWorkspace.shared.activateFileViewerSelecting([url])
        }
    }

    @discardableResult
    func copyArtifactPath(_ artifact: JobArtifact) -> Bool {
        performArtifactAction(artifact) { url in
            NSPasteboard.general.clearContents()
            guard NSPasteboard.general.setString(url.path, forType: .string) else {
                throw ResultContractError("macOS could not copy the artifact path.")
            }
        }
    }

    @discardableResult
    func revealOutputDirectory() -> Bool {
        artifactActionError = nil
        guard let artifactAccess, let verifiedJobOutput else {
            artifactActionError = "The job folder is no longer available."
            return false
        }
        do {
            try artifactAccess.withRevalidatedDirectory(verifiedJobOutput) { url in
                NSWorkspace.shared.activateFileViewerSelecting([url])
            }
            return true
        } catch {
            artifactActionError =
                "The job folder changed after it was verified and was not revealed."
            return false
        }
    }

    func clearArtifactActionError() {
        artifactActionError = nil
    }

    private func performArtifactAction(
        _ artifact: JobArtifact,
        action: (URL) throws -> Void
    ) -> Bool {
        artifactActionError = nil
        guard let artifactAccess,
              let verifiedJobOutput,
              let verified = verifiedJobOutput.artifacts.first(where: {
                  $0.name == artifact.name
                      && $0.sizeBytes == artifact.sizeBytes
                      && $0.identity == artifact.identity
              })
        else {
            artifactActionError = "The selected artifact is no longer available."
            return false
        }
        do {
            try artifactAccess.withRevalidatedArtifact(
                verified,
                in: verifiedJobOutput,
                perform: action
            )
            return true
        } catch {
            artifactActionError =
                "The selected artifact changed after it was verified; the action was not performed."
            return false
        }
    }

    var diagnosticsTail: String {
        runningJob?.diagnosticsTail ?? finalDiagnosticsTail
    }

    /// Privacy-safe text suitable for the clipboard. Raw stderr remains local
    /// in the view and is deliberately omitted because third-party libraries
    /// can print paths, credentials, or media-derived values.
    var copyableDiagnostics: String {
        var parts = [
            "Auralign redacted diagnostics",
            recentEvents.joined(separator: "\n"),
        ]
        if !diagnosticsTail.isEmpty {
            parts.append("[engine stderr omitted from copied diagnostics]")
        }
        return parts.filter { !$0.isEmpty }.joined(separator: "\n---\n")
    }

    // MARK: - Event handling

    private func handle(event: BridgeEvent, configFingerprint: String) async {
        appendRecent(event)
        let mayPrecedeAcceptance: Set<String> = [
            "job_accepted",
            "config_invalid",
            "failed",
            "bridge_error",
            "process_exited",
        ]
        if acceptedOutputIdentity == nil,
           !mayPrecedeAcceptance.contains(event.name) {
            recordFailure(
                JobFailure(
                    errorType: "ProtocolError",
                    message: "The engine emitted job activity before accepting the output folder.",
                    remediation: "Confirm the configured engine checkout matches this app.",
                    diagnostics: diagnosticsTail,
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            )
            return
        }
        if sawResultEvent {
            switch event.name {
            case "failed", "bridge_error", "process_exited":
                break
            default:
                recordFailure(
                    JobFailure(
                        errorType: "ProtocolError",
                        message: "The engine emitted lifecycle data after its result.",
                        remediation: "Confirm the configured engine checkout matches this app.",
                        diagnostics: diagnosticsTail,
                        partialArtifacts: [],
                        outputDirectory: jobDirectory
                    )
                )
                return
            }
        }
        switch event.name {
        case "job_accepted":
            guard acceptedOutputIdentity == nil, let jobDirectory else {
                recordFailure(
                    JobFailure(
                        errorType: "ProtocolError",
                        message: "The engine returned duplicate job-acceptance evidence.",
                        remediation: "Confirm the configured engine checkout matches this app.",
                        diagnostics: diagnosticsTail,
                        partialArtifacts: [],
                        outputDirectory: nil
                    )
                )
                return
            }
            do {
                let acceptance = try JobAcceptanceEnvelope.parse(
                    event.payload,
                    expectedOutputDirectory: jobDirectory
                )
                acceptedOutputIdentity = acceptance.outputIdentity
            } catch {
                recordFailure(
                    JobFailure(
                        errorType: "ProtocolError",
                        message: "The engine returned malformed job-acceptance evidence.",
                        remediation: "Confirm the configured engine checkout matches this app.",
                        diagnostics: diagnosticsTail,
                        partialArtifacts: [],
                        outputDirectory: nil
                    )
                )
            }
        case "config_invalid":
            let errors = ConfigFieldError.parse(from: event.payload)
            let text = (errors ?? []).map {
                "\($0.dottedLocation): \($0.message)"
            }
                .joined(separator: "\n")
            recordFailure(
                JobFailure(
                    errorType: "ConfigurationInvalid",
                    message: errors == nil
                        ? "The engine returned malformed configuration-error evidence."
                        : (text.isEmpty ? "The engine rejected the configuration." : text),
                    remediation: "Fix the flagged fields and start again.",
                    diagnostics: diagnosticsTail,
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            )
        case "preflight_start": markActive("preflight")
        case "preflight_done": markDone("preflight", seconds: nil)
        case "inspect_start": markActive("inspect")
        case "inspect_done":
            markDone("inspect", seconds: nil)
            if let duration = Self.safeDuration(
                event.payload.value(at: "duration_seconds")?.doubleValue
            ) {
                updateDetail("inspect", "media \(Self.timeText(duration))")
            }
        case "canonical_audio_start": markActive("canonical_audio")
        case "canonical_audio_done": markDone("canonical_audio", seconds: nil)
        case "vad_start": markActive("vad")
        case "vad_done":
            markDone("vad", seconds: event.payload.value(at: "duration_seconds")?.doubleValue)
            if let regions = event.payload.value(at: "regions")?.intValue {
                updateDetail("vad", "\(regions) speech regions")
            }
        case "asr_start": markActive("asr")
        case "asr_done":
            markDone("asr", seconds: event.payload.value(at: "duration_seconds")?.doubleValue)
            let segments = event.payload.value(at: "segments")?.intValue
            let language = event.payload.value(at: "language")?.stringValue
            updateDetail(
                "asr",
                [segments.map { "\($0) segments" }, language.map { "language \($0)" }]
                    .compactMap { $0 }.joined(separator: " · ")
            )
        case "alignment_start": markActive("alignment")
        case "alignment_done":
            markDone(
                "alignment",
                seconds: event.payload.value(at: "duration_seconds")?.doubleValue
            )
            let backend = event.payload.value(at: "backend")?.stringValue
            let words = event.payload.value(at: "words")?.intValue
            updateDetail(
                "alignment",
                [backend, words.map { "\($0) words" }].compactMap { $0 }
                    .joined(separator: " · ")
            )
            if let alignmentWarnings = event.payload.value(at: "warnings")?.intValue,
               alignmentWarnings > 0 {
                warnings.append("Alignment reported \(alignmentWarnings) warning(s).")
            }
        case "diarization_start": markActive("diarization", preservingSkipped: true)
        case "pyannote_progress":
            updateDetail("diarization", "processing…", preservingSkipped: true)
        case "diarization_done":
            markDone(
                "diarization",
                seconds: event.payload.value(at: "duration_seconds")?.doubleValue,
                preservingSkipped: true
            )
            if let turns = event.payload.value(at: "turns")?.intValue {
                updateDetail("diarization", "\(turns) turns", preservingSkipped: true)
            }
        case "fusion_done":
            markDone("fusion", seconds: nil)
            if let turns = event.payload.value(at: "speaker_turns")?.intValue {
                updateDetail("fusion", "\(turns) speaker turns")
            }
        case "redecode_start": markActive("redecode")
        case "redecode_done": markDone("redecode", seconds: nil)
        case "export_start": markActive("export")
        case "export_done": markDone("export", seconds: nil)
        case "speaker_id_start": markActive("speaker_id")
        case "speaker_id_done":
            markDone(
                "speaker_id",
                seconds: event.payload.value(at: "duration_seconds")?.doubleValue
            )
        case "cleanup_start": markActive("cleanup")
        case "cleanup_done":
            markDone(
                "cleanup",
                seconds: event.payload.value(at: "duration_seconds")?.doubleValue
            )
        case "model_release_start": markActive("model_release")
        case "model_release_done":
            markDone(
                "model_release",
                seconds: event.payload.value(at: "duration_seconds")?.doubleValue
            )
        case "speaker_identity_start": markActive("speaker_identity")
        case "speaker_identity_done":
            markDone(
                "speaker_identity",
                seconds: event.payload.value(at: "duration_seconds")?.doubleValue
            )
        case "failed":
            let type = event.payload.value(at: "type")?.stringValue ?? "EngineError"
            let message = event.payload.value(at: "message")?.stringValue
                ?? "The engine reported a failure."
            recordFailure(
                JobFailure(
                    errorType: type,
                    message: message,
                    remediation: "Enable the debug report for a redacted local trace.",
                    diagnostics: diagnosticsTail,
                    partialArtifacts: [],
                    outputDirectory: jobDirectory
                )
            )
        case "bridge_error":
            let type = event.payload.value(at: "type")?.stringValue ?? "BridgeError"
            let message = event.payload.value(at: "message")?.stringValue
                ?? "The engine bridge reported an error."
            recordFailure(
                JobFailure(
                    errorType: type,
                    message: message,
                    remediation: event.payload.value(at: "remediation")?.stringValue,
                    diagnostics: diagnosticsTail,
                    partialArtifacts: [],
                    outputDirectory: jobDirectory
                )
            )
        case "complete":
            markDone("complete", seconds: nil)
            let duration = event.payload.value(at: "duration_seconds")?.doubleValue
                .map { "total \(Self.timeText($0))" }
            let factor = event.payload.value(at: "real_time_factor")?.doubleValue
                .map { String(format: "%.2fx real time", $0) }
            updateDetail(
                "complete",
                [duration, factor].compactMap { $0 }.joined(separator: " · ")
            )
        case "result":
            sawResultEvent = true
            guard sawFailureEvent == nil else { return }
            guard let jobDirectory, let acceptedOutputIdentity else {
                recordFailure(
                    JobFailure(
                        errorType: "ProtocolError",
                        message: "The engine returned a result without accepted output identity.",
                        remediation: "Start a fresh job.",
                        diagnostics: diagnosticsTail,
                        partialArtifacts: [],
                        outputDirectory: nil
                    )
                )
                return
            }
            do {
                resultPendingExit = try PendingJobResultEnvelope.parse(
                    event.payload,
                    configFingerprint: configFingerprint,
                    expectedOutputDirectory: jobDirectory
                )
                guard resultPendingExit?.outputIdentity == acceptedOutputIdentity else {
                    throw ResultContractError(
                        "The result directory identity did not match job acceptance."
                    )
                }
                if case .running = phase {
                    phase = .resultPendingExit
                }
            } catch {
                recordFailure(
                    JobFailure(
                        errorType: "ProtocolError",
                        message: "The engine returned malformed result evidence.",
                        remediation: "Confirm the configured engine checkout matches this app.",
                        diagnostics: diagnosticsTail,
                        partialArtifacts: [],
                        outputDirectory: jobDirectory
                    )
                )
            }
        case "process_exited":
            forceStopTask?.cancel()
            // EngineBridge publishes process_exited only after draining protocol
            // output, then records the reaped exit. Wait for that record before
            // releasing ownership or publishing any terminal phase.
            if let exitingJob = runningJob {
                _ = await exitingJob.waitForExit()
                guard runningJob === exitingJob else { return }
                finalizeAfterExit(
                    code: exitingJob.observedTerminationStatus ?? -1
                )
            } else {
                finalizeAfterExit(code: -1)
            }
        default:
            break
        }
    }

    private func recordFailure(_ failure: JobFailure) {
        guard sawFailureEvent == nil else { return }
        sawFailureEvent = failure
        if terminalIntent == nil {
            terminalIntent = .failure
        }
    }

    private func finalizeAfterExit(code: Int) {
        finalDiagnosticsTail = runningJob?.diagnosticsTail ?? finalDiagnosticsTail
        let pendingResult = resultPendingExit
        let pendingFailure = sawFailureEvent
        let pendingIntent = terminalIntent
        let access = artifactAccess
        let acceptedIdentity = acceptedOutputIdentity
        let sourceURL = runSourceURL
        let sourceSnapshot = runSourceSnapshot

        // Ownership is the action boundary. Clear it before publishing a
        // terminal phase so observers can never see success while a child is
        // still owned.
        forceStopTask?.cancel()
        forceStopTask = nil
        runningJob = nil
        consumeTask = nil
        sawFailureEvent = nil
        terminalIntent = nil
        sawResultEvent = false
        resultPendingExit = nil
        acceptedOutputIdentity = nil
        finishedAt = Date()

        if pendingIntent == .failure {
            var failure = pendingFailure
                ?? JobFailure(
                    errorType: "EngineFailure",
                    message: "The engine reported a failure.",
                    remediation: nil,
                    diagnostics: "",
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            let partial = verifiedPartialOutput(
                using: access,
                expectedIdentity: acceptedIdentity
            )
            failure.diagnostics = finalDiagnosticsTail
            failure.partialArtifacts = Self.jobArtifacts(from: partial)
            failure.outputDirectory = partial?.outputDirectory
            verifiedJobOutput = partial
            lastRunFingerprint = nil
            phase = .failed(failure)
            return
        }
        if pendingIntent == .cancellation {
            let partial = verifiedPartialOutput(
                using: access,
                expectedIdentity: acceptedIdentity
            )
            verifiedJobOutput = partial
            lastRunFingerprint = nil
            phase = .cancelled(
                outputDirectory: partial?.outputDirectory,
                partialArtifacts: Self.jobArtifacts(from: partial)
            )
            return
        }
        guard code == 0 else {
            let partial = verifiedPartialOutput(
                using: access,
                expectedIdentity: acceptedIdentity
            )
            verifiedJobOutput = partial
            lastRunFingerprint = nil
            phase = .failed(
                JobFailure(
                    errorType: "EngineExit",
                    message: "The engine process exited with code \(code).",
                    remediation: "Open Technical Details for the captured stderr tail.",
                    diagnostics: finalDiagnosticsTail,
                    partialArtifacts: Self.jobArtifacts(from: partial),
                    outputDirectory: partial?.outputDirectory
                )
            )
            return
        }
        guard let pendingResult else {
            let partial = verifiedPartialOutput(
                using: access,
                expectedIdentity: acceptedIdentity
            )
            verifiedJobOutput = partial
            lastRunFingerprint = nil
            phase = .failed(
                JobFailure(
                    errorType: "ProtocolError",
                    message: "The engine exited without publishing a result.",
                    remediation: nil,
                    diagnostics: finalDiagnosticsTail,
                    partialArtifacts: Self.jobArtifacts(from: partial),
                    outputDirectory: partial?.outputDirectory
                )
            )
            return
        }
        guard let access, let sourceURL else {
            lastRunFingerprint = nil
            phase = .failed(
                JobFailure(
                    errorType: "ResultContractError",
                    message: "The completed result lost its local authorization context.",
                    remediation: "Preserve the partial output and start a fresh job.",
                    diagnostics: finalDiagnosticsTail,
                    partialArtifacts: [],
                    outputDirectory: nil
                )
            )
            return
        }
        do {
            let output = try access.materializeCompleted(pendingResult)
            verifiedJobOutput = output
            let result = JobResult(
                outputDirectory: output.outputDirectory,
                sourceURL: sourceURL,
                sourceSnapshot: sourceSnapshot,
                language: pendingResult.language,
                speakerTurns: pendingResult.speakerTurns,
                words: pendingResult.words,
                segments: pendingResult.segments,
                artifacts: Self.jobArtifacts(from: output),
                configFingerprint: pendingResult.configFingerprint,
                warnings: warnings,
                qa: pendingResult.qa,
                transcriptPreview: pendingResult.transcriptPreview
            )
            lastRunFingerprint = pendingResult.configFingerprint
            phase = .completed(result)
        } catch {
            let partial = verifiedPartialOutput(
                using: access,
                expectedIdentity: acceptedIdentity
            )
            verifiedJobOutput = partial
            lastRunFingerprint = nil
            phase = .failed(
                JobFailure(
                    errorType: "ResultContractError",
                    message: "The completed files no longer match the engine result.",
                    remediation: "Preserve the partial output and start a fresh job.",
                    diagnostics: finalDiagnosticsTail,
                    partialArtifacts: Self.jobArtifacts(from: partial),
                    outputDirectory: partial?.outputDirectory
                )
            )
        }
    }

    private func verifiedPartialOutput(
        using access: JobArtifactAccess?,
        expectedIdentity: ArtifactFileIdentity?
    ) -> VerifiedJobOutput? {
        guard let access, let expectedIdentity else { return nil }
        return try? access.inventoryPartialAfterExit(
            expectedIdentity: expectedIdentity
        )
    }

    private func clearRunContextForNewAttempt() {
        consumeTask?.cancel()
        forceStopTask?.cancel()
        consumeTask = nil
        forceStopTask = nil
        runningJob = nil
        phase = .idle
        stages = []
        warnings = []
        recentEvents = []
        jobDirectory = nil
        runConfiguration = nil
        startedAt = nil
        finishedAt = nil
        lastRunFingerprint = nil
        resultPendingExit = nil
        acceptedOutputIdentity = nil
        runSourceURL = nil
        runSourceSnapshot = nil
        artifactAccess = nil
        verifiedJobOutput = nil
        artifactActionError = nil
        sawFailureEvent = nil
        terminalIntent = nil
        sawResultEvent = false
        finalDiagnosticsTail = ""
        presentationNow = nil
        #if DEBUG
        fixtureOwnsActiveProcess = false
        #endif
    }

    private nonisolated static func jobArtifacts(
        from output: VerifiedJobOutput?
    ) -> [JobArtifact] {
        (output?.artifacts ?? []).map {
            JobArtifact(
                name: $0.name,
                sizeBytes: $0.sizeBytes,
                url: $0.url,
                identity: $0.identity
            )
        }
    }

    // MARK: - Stage helpers

    private func markActive(_ id: String, preservingSkipped: Bool = false) {
        updateStage(id, preservingSkipped: preservingSkipped) { $0.status = .active }
    }

    private func markDone(
        _ id: String,
        seconds: Double?,
        preservingSkipped: Bool = false
    ) {
        updateStage(id, preservingSkipped: preservingSkipped) {
            $0.status = .done(seconds: Self.safeDuration(seconds))
        }
    }

    private func updateDetail(
        _ id: String,
        _ detail: String,
        preservingSkipped: Bool = false
    ) {
        updateStage(id, preservingSkipped: preservingSkipped) { $0.detail = detail }
    }

    private func updateStage(
        _ id: String,
        preservingSkipped: Bool = false,
        _ mutate: (inout PipelineStage) -> Void
    ) {
        guard let index = stages.firstIndex(where: { $0.id == id }) else { return }
        if preservingSkipped, case .skipped = stages[index].status {
            return
        }
        mutate(&stages[index])
    }

    private func appendRecent(_ event: BridgeEvent) {
        recentEvents.append(Self.diagnosticSummary(event))
        if recentEvents.count > 200 {
            recentEvents.removeFirst(recentEvents.count - 200)
        }
    }

    nonisolated static func diagnosticSummary(_ event: BridgeEvent) -> String {
        guard BridgeEvent.allowedNames.contains(event.name) else {
            return "unrecognized_event"
        }
        let safeKeys: Set<String> = [
            "action", "artifact_count", "backend", "code", "codec", "device",
            "duration_seconds", "exclusive_turns", "ffmpeg", "language", "mode",
            "real_time_factor", "regions", "segments", "speaker_turns", "turns",
            "type", "validation_valid", "warnings", "words",
        ]
        guard let object = event.payload.objectValue else { return event.name }
        let projection = object.reduce(into: [String: JSONValue]()) { result, entry in
            if safeKeys.contains(entry.key),
               let safeValue = safeDiagnosticValue(entry.value) {
                result[entry.key] = safeValue
            }
        }
        guard !projection.isEmpty else { return event.name }
        let line = "\(event.name) \(JSONValue.object(projection).canonicalJSONString())"
        return String(line.prefix(1_024))
    }

    private nonisolated static func safeDiagnosticValue(_ value: JSONValue) -> JSONValue? {
        switch value {
        case .bool, .int:
            return value
        case .double(let number):
            guard number.isFinite, abs(number) <= 1_000_000_000_000 else {
                return nil
            }
            return value
        case .string(let text):
            guard !text.isEmpty,
                  text.utf8.count <= 64,
                  text.unicodeScalars.allSatisfy({
                      CharacterSet.alphanumerics.contains($0)
                          || "._:+-".unicodeScalars.contains($0)
                  })
            else {
                return nil
            }
            return value
        case .null, .array, .object:
            return nil
        }
    }

    private nonisolated static func safeDuration(_ seconds: Double?) -> Double? {
        guard let seconds,
              seconds.isFinite,
              seconds >= 0,
              seconds <= 315_576_000
        else {
            return nil
        }
        return seconds
    }

    nonisolated static func timeText(_ seconds: Double) -> String {
        guard let seconds = safeDuration(seconds) else {
            return "invalid duration"
        }
        if seconds >= 60 {
            let minutes = floor(seconds / 60)
            let remainder = floor(seconds.truncatingRemainder(dividingBy: 60))
            return String(format: "%.0fm %.0fs", minutes, remainder)
        }
        return String(format: "%.1fs", seconds)
    }

    nonisolated static func regularFileIdentity(
        at url: URL?
    ) -> ArtifactFileIdentity? {
        guard let url else { return nil }
        let resolved = url.standardizedFileURL.resolvingSymlinksInPath()
        var metadata = Darwin.stat()
        let status = resolved.withUnsafeFileSystemRepresentation { path -> Int32 in
            guard let path else { return -1 }
            return Darwin.lstat(path, &metadata)
        }
        guard status == 0,
              (metadata.st_mode & S_IFMT) == S_IFREG,
              metadata.st_dev >= 0
        else {
            return nil
        }
        return ArtifactFileIdentity(
            device: UInt64(metadata.st_dev),
            inode: UInt64(metadata.st_ino)
        )
    }

    #if DEBUG
    /// One-shot seed into the production presentation contract. No bridge,
    /// subprocess, filesystem read, or lifecycle transition is fabricated.
    func applyPresentationFixture(
        phase: Phase,
        ownsActiveProcess: Bool = false,
        stages: [PipelineStage] = [],
        warnings: [String] = [],
        startedAt: Date? = nil,
        finishedAt: Date? = nil,
        recentEvents: [String] = [],
        jobDirectory: URL? = nil,
        runConfiguration: JobConfigurationSummary? = nil,
        lastRunFingerprint: String? = nil,
        diagnosticsTail: String = "",
        now: Date? = nil
    ) {
        consumeTask?.cancel()
        forceStopTask?.cancel()
        consumeTask = nil
        forceStopTask = nil
        runningJob = nil
        sawFailureEvent = nil
        terminalIntent = nil
        sawResultEvent = false
        artifactAccess = nil
        verifiedJobOutput = nil
        artifactActionError = nil
        fixtureOwnsActiveProcess = ownsActiveProcess
        self.phase = phase
        self.stages = stages
        self.warnings = warnings
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.recentEvents = recentEvents
        self.jobDirectory = jobDirectory
        self.runConfiguration = runConfiguration
        self.lastRunFingerprint = lastRunFingerprint
        resultPendingExit = nil
        finalDiagnosticsTail = diagnosticsTail
        presentationNow = now
    }

    /// Allows a fixture-backed app to follow the real application termination
    /// path after a deterministic presentation capture. This releases only
    /// the synthetic ownership sentinel; an actual RunningJob, if one somehow
    /// exists, remains owned and must still be cancelled and reaped normally.
    func releasePresentationFixtureOwnershipForTermination() {
        fixtureOwnsActiveProcess = false
    }
    #endif
}

/// Which stages a job is expected to run, derived from the effective
/// configuration so skipped stages are shown honestly.
struct JobExpectations {
    var diarizationEnabled: Bool
    var alignmentEnabled: Bool
    var redecodeEnabled: Bool
    var speakerIDEnabled: Bool
    var cleanupEnabled: Bool
    var perJobModelRelease: Bool
    var speakerIdentityEnabled: Bool

    init(config: JSONValue) {
        let diarizationBackend = config.value(at: "diarization.backend")?.stringValue ?? "off"
        diarizationEnabled = (config.value(at: "diarization.enabled")?.boolValue ?? false)
            && diarizationBackend != "off"
        let alignmentBackend = config.value(at: "alignment.backend")?.stringValue ?? "off"
        alignmentEnabled = (config.value(at: "alignment.enabled")?.boolValue ?? false)
            && alignmentBackend != "off"
        let redecodeMode = config.value(at: "redecode.mode")?.stringValue ?? "off"
        redecodeEnabled = redecodeMode != "off"
            || (config.value(at: "redecode.write_report")?.boolValue ?? false)
        speakerIDEnabled = config.value(at: "speaker_id.mode")?.stringValue == "auto"
        cleanupEnabled = (config.value(at: "cleanup.mode")?.stringValue ?? "off") != "off"
        perJobModelRelease = config.value(at: "runtime.model_lifecycle")?.stringValue
            == "per-job"
        let identityMode = config.value(at: "speaker_identity.mode")?.stringValue ?? "off"
        speakerIdentityEnabled = identityMode != "off"
    }

    func expectedStages() -> [PipelineStage] {
        var stages: [PipelineStage] = [
            PipelineStage(id: "preflight", title: "Preflight checks"),
            PipelineStage(id: "inspect", title: "Media inspection"),
            PipelineStage(id: "canonical_audio", title: "Canonical audio"),
            PipelineStage(id: "vad", title: "Voice activity"),
            PipelineStage(id: "asr", title: "Speech recognition"),
        ]
        if alignmentEnabled {
            stages.append(PipelineStage(id: "alignment", title: "Word alignment"))
        } else {
            stages.append(
                PipelineStage(
                    id: "alignment",
                    title: "Word alignment",
                    status: .skipped(reason: "Disabled in configuration")
                )
            )
        }
        if diarizationEnabled {
            stages.append(PipelineStage(id: "diarization", title: "Speaker diarization"))
        } else {
            stages.append(
                PipelineStage(
                    id: "diarization",
                    title: "Speaker diarization",
                    status: .skipped(reason: "Disabled in configuration")
                )
            )
        }
        stages.append(PipelineStage(id: "fusion", title: "Speaker fusion & QA"))
        if redecodeEnabled {
            stages.append(PipelineStage(id: "redecode", title: "Targeted re-decode"))
        }
        stages.append(PipelineStage(id: "export", title: "Exports"))
        if speakerIDEnabled {
            stages.append(PipelineStage(id: "speaker_id", title: "Speaker matching"))
        }
        if cleanupEnabled {
            stages.append(PipelineStage(id: "cleanup", title: "Transcript cleanup"))
        }
        if perJobModelRelease {
            stages.append(PipelineStage(id: "model_release", title: "Release runtime models"))
        }
        if speakerIdentityEnabled {
            stages.append(PipelineStage(id: "speaker_identity", title: "Identity resolution"))
        }
        stages.append(PipelineStage(id: "complete", title: "Completion metadata"))
        return stages
    }
}
