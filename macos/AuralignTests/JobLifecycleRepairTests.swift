// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Darwin
import XCTest
@testable import Auralign

final class JobLifecycleRepairTests: XCTestCase {
    private var temporaryDirectories: [URL] = []

    override func tearDown() {
        for directory in temporaryDirectories {
            try? FileManager.default.removeItem(at: directory)
        }
        temporaryDirectories = []
        super.tearDown()
    }

    func testOneShotDrainsLargeStderrConcurrently() async throws {
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /usr/bin/dd if=/dev/zero bs=1024 count=1024 1>&2 2>/dev/null
            /bin/echo '{"event":"handshake","payload":{"protocol":1}}'
            """
        )

        let events = try await bridge.run(command: ["handshake"], timeout: 5)

        XCTAssertEqual(events.map(\.name), ["handshake"])
    }

    func testOneShotRejectsMalformedStdoutAlongsideExpectedEvent() async throws {
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/echo 'library noise that could contain sensitive data'
            /bin/echo '{"event":"handshake","payload":{"protocol":1}}'
            """
        )

        do {
            _ = try await bridge.runExpecting(
                event: "handshake",
                command: ["handshake"],
                timeout: 5
            )
            XCTFail("expected malformed protocol output to fail closed")
        } catch let error as EngineBridge.BridgeError {
            XCTAssertEqual(
                error.message,
                "Engine bridge produced malformed protocol output."
            )
            XCTAssertFalse(error.message.contains("sensitive"))
        }
    }

    func testOneShotRejectsExpectedEventFollowedByNonzeroExit() async throws {
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/echo '{"event":"handshake","payload":{"protocol":1}}'
            exit 9
            """
        )

        do {
            _ = try await bridge.runExpecting(
                event: "handshake",
                command: ["handshake"],
                timeout: 5
            )
            XCTFail("expected nonzero exit to invalidate the event")
        } catch let error as EngineBridge.BridgeError {
            XCTAssertTrue(error.message.contains("exited with code 9"))
        }
    }

    func testOneShotTimeoutReturnsWithoutWaitingForBlockingReader() async throws {
        let directory = try makeTemporaryDirectory()
        let rootMarker = directory.appendingPathComponent("timeout-root.pid")
        let childMarker = directory.appendingPathComponent("timeout-child.pid")
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            trap '' TERM
            /bin/echo $$ >"\(rootMarker.path)"
            /bin/sleep 30 &
            child=$!
            /bin/echo "$child" >"\(childMarker.path)"
            wait "$child"
            """
        )
        let started = Date()
        let task = Task {
            try await bridge.run(command: ["handshake"], timeout: 1)
        }
        let rootPID = try await waitForPID(from: rootMarker)
        let childPID = try await waitForPID(from: childMarker)

        do {
            _ = try await task.value
            XCTFail("expected timeout")
        } catch let error as EngineBridge.BridgeError {
            XCTAssertTrue(error.message.contains("timed out"))
        }

        XCTAssertLessThan(Date().timeIntervalSince(started), 3.0)
        XCTAssertEqual(Darwin.kill(rootPID, 0), -1)
        XCTAssertEqual(Darwin.errno, ESRCH)
        XCTAssertEqual(Darwin.kill(childPID, 0), -1)
        XCTAssertEqual(Darwin.errno, ESRCH)
    }

    func testPreCancelledOneShotReturnsWithoutLaunchingOrHanging() async throws {
        let directory = try makeTemporaryDirectory()
        let launchMarker = directory.appendingPathComponent("pre-cancelled-launched")
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /usr/bin/touch "\(launchMarker.path)"
            /bin/sleep 30
            """
        )
        let started = Date()
        let task = Task {
            await Task.yield()
            return try await bridge.run(command: ["handshake"], timeout: 30)
        }
        task.cancel()

        do {
            _ = try await task.value
            XCTFail("expected cancellation")
        } catch is CancellationError {
            // Expected.
        }

        XCTAssertLessThan(Date().timeIntervalSince(started), 1.0)
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: launchMarker.path),
            "pre-launch cancellation must not start the bridge executable"
        )
    }

    func testPostLaunchOneShotCancellationReturnsOnlyAfterReapingTree() async throws {
        let directory = try makeTemporaryDirectory()
        let rootMarker = directory.appendingPathComponent("cancel-root.pid")
        let childMarker = directory.appendingPathComponent("cancel-child.pid")
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            trap '' TERM
            /bin/echo $$ >"\(rootMarker.path)"
            /bin/sleep 30 &
            child=$!
            /bin/echo "$child" >"\(childMarker.path)"
            wait "$child"
            """
        )
        let task = Task {
            try await bridge.run(command: ["handshake"], timeout: 30)
        }
        let rootPID = try await waitForPID(from: rootMarker)
        let childPID = try await waitForPID(from: childMarker)

        task.cancel()
        do {
            _ = try await task.value
            XCTFail("expected cancellation")
        } catch is CancellationError {
            // Expected only after the exact launched tree has exited.
        }

        XCTAssertEqual(Darwin.kill(rootPID, 0), -1)
        XCTAssertEqual(Darwin.errno, ESRCH)
        XCTAssertEqual(Darwin.kill(childPID, 0), -1)
        XCTAssertEqual(Darwin.errno, ESRCH)
    }

    func testStreamingTerminationKillsChildSpawnedByTermHandler() async throws {
        let directory = try makeTemporaryDirectory()
        let rootMarker = directory.appendingPathComponent("group-root.pid")
        let childMarker = directory.appendingPathComponent("term-child.pid")
        let python = """
        import os
        import signal
        import subprocess
        import time

        os.setpgrp()

        def stop(signum, frame):
            del signum, frame
            child = subprocess.Popen(["/bin/sleep", "30"])
            with open(\(String(reflecting: childMarker.path)), "w", encoding="utf-8") as handle:
                handle.write(str(child.pid))
            raise SystemExit(130)

        signal.signal(signal.SIGTERM, stop)
        # Publish readiness only after the adversarial handler is installed;
        # otherwise the test can race its own setup and deliver SIGTERM while
        # Python still has the default disposition.
        with open(\(String(reflecting: rootMarker.path)), "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        while True:
            time.sleep(0.1)
        """
        let bridge = try makeBridge(
            script: """
            #!/usr/bin/python3
            \(python)
            """
        )
        let job = try bridge.startTranscription(
            payload: .object(["test": .bool(true)])
        )
        for _ in 0..<100 {
            if FileManager.default.fileExists(atPath: rootMarker.path) {
                break
            }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: rootMarker.path))

        let started = Date()
        job.requestStop()
        var events: [BridgeEvent] = []
        for await event in job.events {
            events.append(event)
        }
        let exited = await job.waitForExit(timeout: 3)
        XCTAssertTrue(exited)
        XCTAssertEqual(events.last?.name, "process_exited")
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: childMarker.path),
            "the adversarial TERM handler must have spawned its descendant"
        )
        let childPID = try pid(from: childMarker)
        for _ in 0..<50 where Darwin.kill(childPID, 0) == 0 {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(Darwin.kill(childPID, 0), -1)
        XCTAssertEqual(Darwin.errno, ESRCH)
        XCTAssertLessThan(
            Date().timeIntervalSince(started),
            3,
            "teardown must not wait for a descendant-held pipe to reach natural EOF"
        )
    }

    func testOneShotRootExitKillsDescendantHoldingPipesBeforeDrain() async throws {
        let directory = try makeTemporaryDirectory()
        let childMarker = directory.appendingPathComponent("one-shot-child.pid")
        let bridge = try makeBridge(
            script: """
            #!/usr/bin/python3
            import os
            import subprocess

            os.setpgrp()
            child = subprocess.Popen(["/bin/sleep", "30"])
            with open(\(String(reflecting: childMarker.path)), "w", encoding="utf-8") as handle:
                handle.write(str(child.pid))
            print('{"event":"handshake","payload":{"protocol":1}}', flush=True)
            """
        )

        let started = Date()
        let events = try await bridge.run(command: ["handshake"], timeout: 5)

        XCTAssertEqual(events.map(\.name), ["handshake"])
        XCTAssertLessThan(
            Date().timeIntervalSince(started),
            3,
            "one-shot completion must kill pipe-holding descendants before EOF drainage"
        )
        let childPID = try pid(from: childMarker)
        XCTAssertEqual(Darwin.kill(childPID, 0), -1)
        XCTAssertEqual(Darwin.errno, ESRCH)
    }

    func testStreamingDrainsFinalProtocolLineWithoutNewlineBeforeExit() async throws {
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /usr/bin/printf '{"event":"result","payload":{"words":3}}'
            """
        )
        let job = try bridge.startTranscription(payload: .object(["test": .bool(true)]))
        var events: [BridgeEvent] = []

        for await event in job.events {
            events.append(event)
        }

        XCTAssertEqual(events.map(\.name), ["result", "process_exited"])
        XCTAssertEqual(events.first?.payload.value(at: "words"), .int(3))
        let exited = await job.waitForExit(timeout: 0.1)
        XCTAssertTrue(exited)
    }

    func testStreamingAcceptsExactlyTheConfiguredStdoutBoundary() async throws {
        let protocolLine = JSONValue.object([
            "event": .string("result"),
            "payload": .object([
                "blob": .string(String(repeating: "x", count: 256)),
            ]),
        ])
        .canonicalJSONString()
        let limit = Data(protocolLine.utf8).count
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /usr/bin/printf '%s' '\(protocolLine)'
            """,
            maximumStreamingStdoutBytes: limit
        )
        let job = try bridge.startTranscription(payload: .object(["test": .bool(true)]))
        var events: [BridgeEvent] = []

        for await event in job.events {
            events.append(event)
        }

        XCTAssertEqual(events.map(\.name), ["result", "process_exited"])
        XCTAssertEqual(
            events.first?.payload.value(at: "blob")?.stringValue?.count,
            256
        )
        let reaped = await job.waitForExit(timeout: 0.1)
        XCTAssertTrue(reaped)
    }

    func testStreamingStopsAndReapsOnNewlineFreeStdoutOverLimit() async throws {
        let limit = 128
        let untrustedOutput = String(repeating: "sensitive", count: 20)
        XCTAssertGreaterThan(Data(untrustedOutput.utf8).count, limit)
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            trap '' TERM
            /bin/cat >/dev/null
            /usr/bin/printf '%s' '\(untrustedOutput)'
            while :; do :; done
            """,
            maximumStreamingStdoutBytes: limit
        )
        let job = try bridge.startTranscription(payload: .object(["test": .bool(true)]))
        var events: [BridgeEvent] = []

        for await event in job.events {
            events.append(event)
        }

        XCTAssertEqual(events.map(\.name), ["bridge_error", "process_exited"])
        let message = events.first?.payload.value(at: "message")?.stringValue ?? ""
        XCTAssertTrue(message.contains("safety limit"))
        XCTAssertFalse(message.contains("sensitive"))
        let reaped = await job.waitForExit(timeout: 0.1)
        XCTAssertTrue(reaped)
        XCTAssertFalse(job.isActive)
    }

    func testStreamingRetainsOnlyBoundedStderrSuffixWithoutNewlines() async throws {
        let stderr = String(repeating: "0123456789", count: 40)
        let retainedBytes = 64
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /usr/bin/printf '%s' '\(stderr)' >&2
            /usr/bin/printf '%s' '{"event":"result","payload":{}}'
            """,
            maximumRetainedStreamingStderrBytes: retainedBytes
        )
        let job = try bridge.startTranscription(payload: .object(["test": .bool(true)]))

        for await _ in job.events {}

        let reaped = await job.waitForExit(timeout: 0.1)
        XCTAssertTrue(reaped)
        XCTAssertLessThanOrEqual(job.diagnosticsTail.utf8.count, retainedBytes)
        XCTAssertEqual(job.diagnosticsTail, String(stderr.suffix(retainedBytes)))
    }

    func testStreamingConvertsMalformedStdoutToRedactedProtocolError() async throws {
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /bin/echo '/private/media/interview.wav secret-token'
            """
        )
        let job = try bridge.startTranscription(payload: .object(["test": .bool(true)]))
        var events: [BridgeEvent] = []

        for await event in job.events {
            events.append(event)
        }

        XCTAssertEqual(events.map(\.name), ["bridge_error", "process_exited"])
        let message = events.first?.payload.value(at: "message")?.stringValue ?? ""
        XCTAssertEqual(message, "The engine bridge produced malformed protocol output.")
        XCTAssertFalse(message.contains("/private"))
        XCTAssertFalse(message.contains("secret-token"))
        let reaped = await job.waitForExit(timeout: 0.1)
        XCTAssertTrue(reaped)
        XCTAssertFalse(job.isActive)
    }

    func testUnencodableStreamingPayloadNeverLaunchesAChild() throws {
        let directory = try makeTemporaryDirectory()
        let marker = directory.appendingPathComponent("launched")
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /usr/bin/touch '\(marker.path)'
            /bin/cat >/dev/null
            """
        )

        XCTAssertThrowsError(
            try bridge.startTranscription(payload: .double(.nan))
        )
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: marker.path),
            "encoding failure must occur before process launch"
        )
    }

    func testSourceSnapshotDetectsInPlaceMutationAndReplacement() throws {
        let directory = try makeTemporaryDirectory()
        let input = directory.appendingPathComponent("sample.wav")
        try Data("first".utf8).write(to: input)
        let first = try XCTUnwrap(SourceFileSnapshot.capture(at: input))

        try Data("second-longer".utf8).write(to: input)
        let modified = try XCTUnwrap(SourceFileSnapshot.capture(at: input))
        XCTAssertNotEqual(first, modified)
        XCTAssertEqual(first.identity, modified.identity)

        try FileManager.default.removeItem(at: input)
        try Data("replacement".utf8).write(to: input)
        let replaced = try XCTUnwrap(SourceFileSnapshot.capture(at: input))
        XCTAssertNotEqual(modified.identity, replaced.identity)
    }

    func testCoreOnlyProbePayloadIsBoundToTheLocalSourceSnapshot() throws {
        let directory = try makeTemporaryDirectory()
        let input = directory.appendingPathComponent("sample.wav")
        try Data("source".utf8).write(to: input)
        let snapshot = try XCTUnwrap(SourceFileSnapshot.capture(at: input))
        let payload: JSONValue = .object([
            "duration_seconds": .double(1.25),
            "sample_rate": .int(16_000),
            "channels": .int(1),
            "codec": .string("pcm_s16le"),
            "format_name": .string("wav"),
        ])

        let probe = try AppModel.validateMediaProbePayload(
            payload,
            sourceSnapshot: snapshot
        )

        XCTAssertEqual(probe.durationSeconds, 1.25)
        XCTAssertNil(
            payload.value(at: "path"),
            "a privacy-bounded probe response must not echo a local path"
        )

        try Data("changed".utf8).write(to: input)
        XCTAssertThrowsError(
            try AppModel.validateMediaProbePayload(
                payload,
                sourceSnapshot: snapshot
            )
        )
    }

    @MainActor
    func testControllerRejectsSourceMutationBeforeLaunch() throws {
        let directory = try makeTemporaryDirectory()
        let input = directory.appendingPathComponent("sample.wav")
        let marker = directory.appendingPathComponent("launched")
        try Data("first".utf8).write(to: input)
        let inspected = try XCTUnwrap(SourceFileSnapshot.capture(at: input))
        try Data("changed-after-probe".utf8).write(to: input)
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /usr/bin/touch '\(marker.path)'
            /bin/cat >/dev/null
            """
        )
        let controller = JobController()

        controller.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: inspected,
            outputRoot: directory,
            jobDirectory: directory.appendingPathComponent("job-output"),
            config: .object([:]),
            configFingerprint: "stale-probe",
            debugReport: false,
            expectations: JobExpectations(config: .object([:]))
        )

        XCTAssertFalse(FileManager.default.fileExists(atPath: marker.path))
        guard case .failed(let failure) = controller.phase else {
            XCTFail("stale probe evidence must fail before launch")
            return
        }
        XCTAssertEqual(failure.errorType, "InvalidInput")
        XCTAssertNil(controller.jobDirectory)
        XCTAssertNil(controller.lastRunFingerprint)
    }

    @MainActor
    func testChildCannotSpoofSuccessfulProcessExitBeforeActualFailure() async throws {
        let controller = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /bin/echo '{"event":"result","payload":{"speaker_turns":0,"words":3,"segments":1}}'
            /bin/echo '{"event":"process_exited","payload":{"code":0}}'
            exit 7
            """
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("a child-authored lifecycle event must never publish success")
            return
        }
        XCTAssertEqual(failure.errorType, "ProtocolError")
        XCTAssertNil(controller.lastRunFingerprint)
        XCTAssertFalse(controller.ownsActiveProcess)
    }

    func testForceStopReapsBlockingBridgeProcess() async throws {
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            trap '' TERM
            /bin/cat >/dev/null
            /bin/sh -c 'trap "" TERM; while :; do :; done' &
            child=$!
            /usr/bin/printf '{"event":"job_accepted","payload":{"pid":%s}}\\n' "$child"
            wait "$child"
            """
        )
        let job = try bridge.startTranscription(payload: .object(["test": .bool(true)]))
        var iterator = job.events.makeAsyncIterator()
        let accepted = await iterator.next()
        XCTAssertEqual(accepted?.name, "job_accepted")
        let childPID = try XCTUnwrap(accepted?.payload.value(at: "pid")?.intValue)

        job.requestStop()
        let exitedCooperatively = await job.waitForExit(timeout: 0.1)
        XCTAssertFalse(exitedCooperatively)
        job.forceStop()

        let exitedAfterForce = await job.waitForExit(timeout: 3)
        XCTAssertTrue(exitedAfterForce)
        XCTAssertFalse(job.isActive)
        for _ in 0..<50 where Darwin.kill(pid_t(childPID), 0) == 0 {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(Darwin.kill(pid_t(childPID), 0), -1)
    }

    @MainActor
    func testControllerTracksRealPostExportStagesAndImmutableConfiguration() async throws {
        let directory = try makeTemporaryDirectory()
        let jobDirectory = directory
            .standardizedFileURL
            .resolvingSymlinksInPath()
            .appendingPathComponent("job-output", isDirectory: true)
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            \(validAcceptanceScript(outputDirectory: jobDirectory))
            /bin/echo '{"event":"diarization_start","payload":{"backend":"off"}}'
            /bin/echo '{"event":"diarization_done","payload":{"turns":0}}'
            /bin/echo '{"event":"export_start","payload":{}}'
            /bin/echo '{"event":"export_done","payload":{}}'
            /bin/echo '{"event":"speaker_id_start","payload":{}}'
            /bin/echo '{"event":"speaker_id_done","payload":{"duration_seconds":0.1}}'
            /bin/echo '{"event":"cleanup_start","payload":{}}'
            /bin/echo '{"event":"cleanup_done","payload":{"duration_seconds":0.2}}'
            /bin/echo '{"event":"model_release_start","payload":{}}'
            /bin/echo '{"event":"model_release_done","payload":{"duration_seconds":0.3}}'
            /bin/echo '{"event":"complete","payload":{}}'
            \(validResultScript(outputDirectory: jobDirectory))
            """
        )
        let input = directory.appendingPathComponent("sample.wav")
        try Data().write(to: input)
        var config = JSONValue.object([:])
        config = config.setting("preset", to: .string("max"))
        config = config.setting("offline", to: .bool(true))
        config = config.setting("asr.backend", to: .string("mlx-whisper"))
        config = config.setting("asr.model", to: .string("large-v3"))
        config = config.setting("asr.mlx_model", to: .string("mlx-community/whisper-large-v3-mlx"))
        config = config.setting("diarization.enabled", to: .bool(false))
        config = config.setting("diarization.backend", to: .string("off"))
        config = config.setting("alignment.enabled", to: .bool(false))
        config = config.setting("speaker_id.mode", to: .string("auto"))
        config = config.setting("cleanup.mode", to: .string("local-llm"))
        config = config.setting("runtime.model_lifecycle", to: .string("per-job"))
        config = config.setting("speaker_identity.mode", to: .string("off"))
        let controller = JobController()

        controller.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: try XCTUnwrap(
                SourceFileSnapshot.capture(at: input)
            ),
            outputRoot: directory,
            jobDirectory: jobDirectory,
            config: config,
            configFingerprint: "captured",
            debugReport: false,
            expectations: JobExpectations(config: config)
        )
        for _ in 0..<100 where controller.ownsActiveProcess {
            try await Task.sleep(nanoseconds: 20_000_000)
        }

        XCTAssertFalse(controller.ownsActiveProcess)
        XCTAssertEqual(controller.runConfiguration?.preset, "max")
        XCTAssertEqual(
            controller.runConfiguration?.asrModel,
            "mlx-community/whisper-large-v3-mlx"
        )
        XCTAssertEqual(controller.runConfiguration?.alignment, "off")
        XCTAssertEqual(controller.lastRunFingerprint, "captured")
        XCTAssertStageDone("speaker_id", in: controller.stages)
        XCTAssertStageDone("cleanup", in: controller.stages)
        XCTAssertStageDone("model_release", in: controller.stages)
        let diarization = try XCTUnwrap(
            controller.stages.first { $0.id == "diarization" }
        )
        if case .skipped = diarization.status {
            // Expected: no-op backend events must not overwrite the skip.
        } else {
            XCTFail("disabled diarization was relabelled as work")
        }
    }

    @MainActor
    func testControllerRejectsResultAfterBridgeFailure() async throws {
        let controller = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /bin/echo '{"event":"bridge_error","payload":{"type":"ProtocolError","message":"malformed"}}'
            /bin/echo '{"event":"result","payload":{"speaker_turns":0,"words":3,"segments":1}}'
            """
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("a later result must not conceal a bridge failure")
            return
        }
        XCTAssertEqual(failure.errorType, "ProtocolError")
    }

    @MainActor
    func testControllerRejectsResultWhenProcessExitsNonzero() async throws {
        let controller = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            __VALID_RESULT__
            exit 7
            """
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("a nonzero process exit must invalidate the result")
            return
        }
        XCTAssertEqual(failure.errorType, "EngineExit")
        XCTAssertTrue(failure.message.contains("code 7"))
        XCTAssertNil(controller.lastRunFingerprint)
    }

    @MainActor
    func testControllerKeepsResultPrivateUntilZeroExitAndReaping() async throws {
        let directory = try makeTemporaryDirectory()
        let releaseGate = directory.appendingPathComponent("allow-exit")
        let jobDirectory = directory
            .standardizedFileURL
            .resolvingSymlinksInPath()
            .appendingPathComponent("job-output", isDirectory: true)
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            \(validAcceptanceScript(outputDirectory: jobDirectory))
            \(validResultScript(outputDirectory: jobDirectory))
            while [ ! -f '\(releaseGate.path)' ]; do /bin/sleep 0.02; done
            """
        )
        let input = directory.appendingPathComponent("sample.wav")
        try Data().write(to: input)
        let controller = JobController()
        controller.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: try XCTUnwrap(
                SourceFileSnapshot.capture(at: input)
            ),
            outputRoot: directory,
            jobDirectory: jobDirectory,
            config: .object([:]),
            configFingerprint: "pending-exit",
            debugReport: false,
            expectations: JobExpectations(config: .object([:]))
        )

        for _ in 0..<200 {
            if controller.recentEvents.contains(where: { $0.hasPrefix("result") }) {
                break
            }
            try await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertTrue(controller.recentEvents.contains(where: { $0.hasPrefix("result") }))
        XCTAssertTrue(controller.ownsActiveProcess)
        XCTAssertNil(controller.finishedAt)
        XCTAssertNil(controller.lastRunFingerprint)
        if case .resultPendingExit = controller.phase {
            // Expected: the result remains private, while the visible phase
            // truthfully reports that process finalization is still pending.
        } else {
            XCTFail("result did not enter the explicit pending-exit state")
        }

        try Data().write(to: releaseGate)
        for _ in 0..<100 where controller.ownsActiveProcess {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertFalse(controller.ownsActiveProcess)
        XCTAssertNotNil(controller.finishedAt)
        XCTAssertEqual(controller.lastRunFingerprint, "pending-exit")
        if case .completed = controller.phase {
            // Expected.
        } else {
            XCTFail("zero exit with a result should complete")
        }
    }

    @MainActor
    func testControllerFailureAfterResultStillWins() async throws {
        let controller = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            __VALID_RESULT__
            /bin/echo '{"event":"failed","payload":{"type":"LateFailure","message":"finalization failed"}}'
            """
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("a failure after result must invalidate pending success")
            return
        }
        XCTAssertEqual(failure.errorType, "LateFailure")
        XCTAssertNil(controller.lastRunFingerprint)
    }

    @MainActor
    func testControllerRejectsDuplicateAndMalformedAcceptance() async throws {
        let duplicate = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            __VALID_ACCEPTANCE__
            __VALID_ACCEPTANCE__
            """
        )
        guard case .failed(let duplicateFailure) = duplicate.phase else {
            XCTFail("duplicate acceptance must fail closed")
            return
        }
        XCTAssertEqual(duplicateFailure.errorType, "ProtocolError")

        let malformed = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /bin/echo '{"event":"job_accepted","payload":{"schema_version":"gui_job_acceptance.v2","output_directory":"/private/tmp/wrong","output_identity":{"device":1,"inode":2}}}'
            """
        )
        guard case .failed(let malformedFailure) = malformed.phase else {
            XCTFail("malformed acceptance must fail closed")
            return
        }
        XCTAssertEqual(malformedFailure.errorType, "ProtocolError")
        XCTAssertNil(malformedFailure.outputDirectory)
    }

    @MainActor
    func testControllerRejectsAcceptanceResultIdentityDisagreement() async throws {
        let controller = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            __VALID_ACCEPTANCE__
            bad_inode=$((job_inode + 1))
            __MISMATCHED_RESULT__
            """
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("result identity drift must fail closed")
            return
        }
        XCTAssertEqual(failure.errorType, "ProtocolError")
        XCTAssertNil(controller.lastRunFingerprint)
    }

    @MainActor
    func testReplacedAcceptedDirectoryIsNeverPublishedAsPartialOutput() async throws {
        let controller = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            __VALID_ACCEPTANCE__
            /bin/rm -rf "$AURALIGN_JOB_DIR"
            /bin/mkdir -m 700 "$AURALIGN_JOB_DIR"
            /usr/bin/printf 'partial' >"$AURALIGN_JOB_DIR/partial.txt"
            /bin/echo '{"event":"failed","payload":{"type":"SyntheticFailure","message":"synthetic failure"}}'
            """
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("replaced accepted directory must remain a failure")
            return
        }
        XCTAssertEqual(failure.errorType, "SyntheticFailure")
        XCTAssertNil(failure.outputDirectory)
        XCTAssertEqual(failure.partialArtifacts, [])
        XCTAssertFalse(controller.hasResultSurface)
    }

    @MainActor
    func testFailedSecondStartClearsEveryPriorResultAuthority() async throws {
        let directory = try makeTemporaryDirectory()
        let input = directory.appendingPathComponent("sample.wav")
        try Data("source".utf8).write(to: input)
        let sourceSnapshot = try XCTUnwrap(SourceFileSnapshot.capture(at: input))
        let firstJob = directory.appendingPathComponent("first-job")
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            \(validAcceptanceScript(outputDirectory: firstJob))
            \(validResultScript(outputDirectory: firstJob))
            """
        )
        let controller = JobController()
        controller.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: sourceSnapshot,
            outputRoot: directory,
            jobDirectory: firstJob,
            config: .object([:]),
            configFingerprint: "first-success",
            debugReport: false,
            expectations: JobExpectations(config: .object([:]))
        )
        for _ in 0..<100 where controller.ownsActiveProcess {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        guard case .completed = controller.phase else {
            XCTFail("first run should establish result authority")
            return
        }
        XCTAssertEqual(controller.lastRunFingerprint, "first-success")

        let outside = directory
            .deletingLastPathComponent()
            .appendingPathComponent("outside-\(UUID().uuidString)")
        controller.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: sourceSnapshot,
            outputRoot: directory,
            jobDirectory: outside,
            config: .object([:]),
            configFingerprint: "second-attempt",
            debugReport: false,
            expectations: JobExpectations(config: .object([:]))
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("invalid second plan must fail")
            return
        }
        XCTAssertEqual(failure.errorType, "InvalidJobDirectory")
        XCTAssertNil(controller.lastRunFingerprint)
        XCTAssertNil(controller.jobDirectory)
        XCTAssertNil(controller.runConfiguration)
        XCTAssertFalse(controller.hasResultSurface)
        XCTAssertFalse(controller.revealOutputDirectory())
    }

    @MainActor
    func testApplicationTerminationPathForceStopsAndFinalizesCancellation() async throws {
        let directory = try makeTemporaryDirectory()
        let jobDirectory = directory.appendingPathComponent("job-output")
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            trap '' TERM
            /bin/cat >/dev/null
            \(validAcceptanceScript(outputDirectory: jobDirectory))
            while :; do :; done
            """
        )
        let input = directory.appendingPathComponent("sample.wav")
        try Data().write(to: input)
        let config = JSONValue.object([:])
        let controller = JobController()
        controller.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: try XCTUnwrap(
                SourceFileSnapshot.capture(at: input)
            ),
            outputRoot: directory,
            jobDirectory: jobDirectory,
            config: config,
            configFingerprint: "termination",
            debugReport: false,
            expectations: JobExpectations(config: config)
        )
        for _ in 0..<50 {
            if controller.recentEvents.contains("job_accepted") { break }
            try await Task.sleep(nanoseconds: 20_000_000)
        }

        await controller.stopForApplicationTermination(gracePeriod: 0.1)

        XCTAssertFalse(controller.ownsActiveProcess)
        if case .cancelled = controller.phase {
            // Expected.
        } else {
            XCTFail("termination should finalize as cancellation")
        }
    }

    func testCopiedDiagnosticsProjectionOmitsPathsAndSecrets() {
        let event = BridgeEvent(
            name: "preflight_start",
            payload: .object([
                "input": .string("/private/media/interview.wav"),
                "out": .string("/private/results"),
                "token": .string("secret-value"),
                "backend": .string("mlx-whisper"),
            ])
        )

        let summary = JobController.diagnosticSummary(event)

        XCTAssertEqual(summary, #"preflight_start {"backend":"mlx-whisper"}"#)
        XCTAssertFalse(summary.contains("/private"))
        XCTAssertFalse(summary.contains("secret-value"))
    }

    func testCopiedDiagnosticsRejectsUntrustedNamesAndStringValuesAndStaysBounded() {
        let secret = "/private/media/interview.wav?token=secret-value"
        let unknown = BridgeEvent(
            name: secret,
            payload: .object(["backend": .string(secret)])
        )
        XCTAssertEqual(JobController.diagnosticSummary(unknown), "unrecognized_event")

        let known = BridgeEvent(
            name: "asr_start",
            payload: .object([
                "backend": .string(secret),
                "type": .string(String(repeating: "x", count: 10_000)),
                "segments": .int(3),
            ])
        )
        let summary = JobController.diagnosticSummary(known)
        XCTAssertEqual(summary, #"asr_start {"segments":3}"#)
        XCTAssertFalse(summary.contains("/private"))
        XCTAssertFalse(summary.contains("secret"))
        XCTAssertLessThanOrEqual(summary.utf8.count, 1_024)
    }

    @MainActor
    func testControllerRejectsHugeFiniteDurationAndMalformedResultCounts() async throws {
        let controller = try await runController(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            __VALID_ACCEPTANCE__
            /bin/echo '{"event":"asr_done","payload":{"duration_seconds":1e100,"segments":3}}'
            /bin/echo '{"event":"result","payload":{"speaker_turns":1,"words":1e100,"segments":1}}'
            """
        )

        guard case .failed(let failure) = controller.phase else {
            XCTFail("malformed result counts must fail closed")
            return
        }
        XCTAssertEqual(failure.errorType, "ProtocolError")
        XCTAssertEqual(JobController.timeText(1e100), "invalid duration")
    }

    func testApplicationInstanceLockAllowsOnlyOneOwner() throws {
        let directory = try makeTemporaryDirectory()
        let lockURL = directory.appendingPathComponent("application.lock")
        let first = ApplicationInstanceLock(lockURL: lockURL)
        let second = ApplicationInstanceLock(lockURL: lockURL)

        XCTAssertTrue(first.acquire())
        XCTAssertTrue(first.acquire(), "acquisition must be idempotent for the owner")
        XCTAssertFalse(second.acquire(), "a second application process must fail closed")
        XCTAssertEqual(
            second.ownerProcessIdentifier(),
            ProcessInfo.processInfo.processIdentifier
        )
    }

    func testApplicationInstanceLockUsesStableUserPrivateLocation() throws {
        let lockURL = ApplicationInstanceLock.standardLockURL()
        XCTAssertFalse(
            lockURL.path.hasPrefix(
                FileManager.default.temporaryDirectory.standardizedFileURL.path
            )
        )
        XCTAssertTrue(
            lockURL.path.hasSuffix(
                "Library/Application Support/com.auralign.studio/application-instance.lock"
            )
        )
    }

    func testApplicationInstanceLockReportsExactCrossProcessOwner() throws {
        let directory = try makeTemporaryDirectory()
        let lockURL = directory.appendingPathComponent("cross-process.lock")
        let readyURL = directory.appendingPathComponent("cross-process.ready")
        let holder = Process()
        holder.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        holder.arguments = [
            "python3",
            "-c",
            """
            import fcntl, os, sys, time
            descriptor = os.open(
                sys.argv[1],
                os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
                0o600,
            )
            fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ready = os.open(
                sys.argv[2],
                os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
                0o600,
            )
            os.write(ready, str(os.getpid()).encode("ascii"))
            os.close(ready)
            time.sleep(30)
            """,
            lockURL.path,
            readyURL.path,
        ]
        holder.standardOutput = FileHandle.nullDevice
        holder.standardError = FileHandle.nullDevice
        try holder.run()
        defer {
            if holder.isRunning {
                holder.terminate()
            }
            holder.waitUntilExit()
        }
        for _ in 0..<200 {
            if FileManager.default.fileExists(atPath: readyURL.path) {
                break
            }
            Thread.sleep(forTimeInterval: 0.01)
        }
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: readyURL.path),
            "the independent lock-holder process did not become ready"
        )
        guard FileManager.default.fileExists(atPath: readyURL.path) else {
            return
        }
        let recordedPID = try pid(from: readyURL)
        XCTAssertEqual(recordedPID, holder.processIdentifier)

        let contender = ApplicationInstanceLock(lockURL: lockURL)
        XCTAssertFalse(contender.acquire())
        XCTAssertEqual(
            contender.ownerProcessIdentifier(),
            holder.processIdentifier,
            "F_GETLK must report the kernel owner, not another same-bundle guess"
        )
    }

    @MainActor
    func testAppModelActivationRequiresAndRetainsApplicationOwnership() throws {
        let defaultsName = "AuralignOwnershipTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: defaultsName))
        defaults.removePersistentDomain(forName: defaultsName)
        defer { defaults.removePersistentDomain(forName: defaultsName) }
        defaults.set(
            "/AURALIGN_SYNTHETIC/missing-engine",
            forKey: EngineLocation.engineRootDefaultsKey
        )
        let model = AppModel(userDefaults: defaults)

        XCTAssertFalse(model.hasApplicationOwnership)
        XCTAssertFalse(model.connectEngine())
        XCTAssertTrue(model.activateForApplicationOwnership())
        XCTAssertTrue(model.hasApplicationOwnership)
        XCTAssertNotNil(model.startupError)
        XCTAssertTrue(model.activateForApplicationOwnership())
        XCTAssertNil(model.bridge)
        XCTAssertFalse(model.isConnectingEngine)
    }

    @MainActor
    func testAppModelBlocksEveryWorkMutationWhileOwningAProcess() async throws {
        let defaultsName = "AuralignTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: defaultsName))
        defaults.removePersistentDomain(forName: defaultsName)
        defer { defaults.removePersistentDomain(forName: defaultsName) }
        let model = AppModel(userDefaults: defaults)
        model.selectedSection = .processing
        let originalLocation = model.engineLocation
        let directory = try makeTemporaryDirectory()
        let jobDirectory = directory.appendingPathComponent("job-output")
        let bridge = try makeBridge(
            script: """
            #!/bin/sh
            trap '' TERM
            /bin/cat >/dev/null
            \(validAcceptanceScript(outputDirectory: jobDirectory))
            while :; do :; done
            """
        )
        let input = directory.appendingPathComponent("sample.wav")
        try Data().write(to: input)
        model.job.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: try XCTUnwrap(
                SourceFileSnapshot.capture(at: input)
            ),
            outputRoot: directory,
            jobDirectory: jobDirectory,
            config: .object([:]),
            configFingerprint: "locked",
            debugReport: false,
            expectations: JobExpectations(config: .object([:]))
        )
        for _ in 0..<100 {
            if model.job.recentEvents.contains("job_accepted") { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertTrue(model.workMutationLocked)
        let attemptedRoot = directory.appendingPathComponent("different-engine")
        let attemptedInput = directory.appendingPathComponent("different.wav")
        let attemptedOutput = directory.appendingPathComponent("different-output")
        let presetBeforeAttempt = model.config.selectedPreset
        let overridesBeforeAttempt = model.config.overrides
        XCTAssertFalse(model.updateEngineRoot(attemptedRoot))
        XCTAssertFalse(model.connectEngine())
        XCTAssertFalse(model.resetJob())
        XCTAssertFalse(model.beginNewJob())
        XCTAssertFalse(model.selectInput(attemptedInput))
        XCTAssertFalse(model.selectOutputRoot(attemptedOutput))
        XCTAssertFalse(model.setWriteDebugReport(true))
        XCTAssertFalse(model.config.selectPreset("fast"))
        XCTAssertFalse(model.config.setOverride("asr.language", to: .string("fr")))
        XCTAssertFalse(model.config.clearAllOverrides())
        await model.refreshReadiness()
        XCTAssertNil(
            defaults.string(forKey: EngineLocation.engineRootDefaultsKey),
            "a blocked engine-root attempt must not persist"
        )
        XCTAssertEqual(model.engineLocation, originalLocation)
        XCTAssertEqual(model.selectedSection, .processing)
        XCTAssertNil(model.inputURL)
        XCTAssertNil(model.outputRootURL)
        XCTAssertFalse(model.writeDebugReport)
        XCTAssertEqual(model.config.selectedPreset, presetBeforeAttempt)
        XCTAssertEqual(model.config.overrides, overridesBeforeAttempt)
        XCTAssertNil(model.doctorError, "blocked readiness must be a no-op")

        await model.job.stopForApplicationTermination(gracePeriod: 0.05)
        XCTAssertFalse(model.job.ownsActiveProcess)
    }

    private func makeBridge(
        script: String,
        maximumStreamingStdoutBytes: Int = EngineBridge.maximumStreamingStdoutBytes,
        maximumRetainedStreamingStderrBytes: Int =
            EngineBridge.maximumRetainedStreamingStderrBytes
    ) throws -> EngineBridge {
        let root = try makeTemporaryDirectory()
        let package = root.appendingPathComponent("src/local_transcript_max", isDirectory: true)
        let executable = root.appendingPathComponent(".venv/bin/python")
        try FileManager.default.createDirectory(
            at: package,
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: executable.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data((script + "\n").utf8).write(to: executable)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: executable.path
        )
        return EngineBridge(
            location: EngineLocation(repositoryRoot: root),
            maximumStreamingStdoutBytes: maximumStreamingStdoutBytes,
            maximumRetainedStreamingStderrBytes: maximumRetainedStreamingStderrBytes
        )
    }

    private func pid(from url: URL) throws -> pid_t {
        let text = try String(contentsOf: url, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return try XCTUnwrap(pid_t(text))
    }

    private func waitForPID(from url: URL) async throws -> pid_t {
        for _ in 0..<100 {
            if let text = try? String(contentsOf: url, encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines),
               let value = pid_t(text),
               value > 0 {
                return value
            }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        throw NSError(
            domain: "AuralignTests",
            code: 1,
            userInfo: [
                NSLocalizedDescriptionKey:
                    "The fixture did not publish a complete positive PID."
            ]
        )
    }

    private func makeTemporaryDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("auralign-lifecycle-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: directory.path
        )
        temporaryDirectories.append(directory)
        return directory
    }

    @MainActor
    private func runController(script: String) async throws -> JobController {
        let directory = try makeTemporaryDirectory()
        let jobDirectory = directory
            .standardizedFileURL
            .resolvingSymlinksInPath()
            .appendingPathComponent("job-output", isDirectory: true)
        let preparedScript = script
            .replacingOccurrences(
                of: "__VALID_ACCEPTANCE__",
                with: validAcceptanceScript(outputDirectory: jobDirectory)
            )
            .replacingOccurrences(
                of: "__VALID_RESULT__",
                with: validAcceptanceScript(outputDirectory: jobDirectory)
                    + "\n"
                    + validResultScript(outputDirectory: jobDirectory)
            )
            .replacingOccurrences(
                of: "__MISMATCHED_RESULT__",
                with: validResultScript(outputDirectory: jobDirectory)
                    .replacingOccurrences(
                        of: "\"$job_inode\"",
                        with: "\"$bad_inode\""
                    )
            )
            .replacingOccurrences(
                of: "$AURALIGN_JOB_DIR",
                with: jobDirectory.path
            )
        let bridge = try makeBridge(script: preparedScript)
        let input = directory.appendingPathComponent("sample.wav")
        try Data().write(to: input)
        let controller = JobController()
        controller.start(
            bridge: bridge,
            input: input,
            expectedSourceSnapshot: try XCTUnwrap(
                SourceFileSnapshot.capture(at: input)
            ),
            outputRoot: directory,
            jobDirectory: jobDirectory,
            config: .object([:]),
            configFingerprint: "protocol-test",
            debugReport: false,
            expectations: JobExpectations(config: .object([:]))
        )
        for _ in 0..<100 where controller.ownsActiveProcess {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        return controller
    }

    /// Produces exact acceptance evidence for the fresh planned directory.
    private func validAcceptanceScript(outputDirectory: URL) -> String {
        let path = outputDirectory.path
        return """
        /bin/mkdir -m 700 '\(path)'
        job_device=$(/usr/bin/stat -f %d '\(path)')
        job_inode=$(/usr/bin/stat -f %i '\(path)')
        /usr/bin/printf '{"event":"job_accepted","payload":{"schema_version":"gui_job_acceptance.v1","output_directory":"\(path)","output_identity":{"device":%s,"inode":%s}}}\\n' "$job_device" "$job_inode"
        """
    }

    /// Produces the exact successful result envelope used by controller tests.
    /// The caller first creates and accepts the planned directory. Empty
    /// artifact projections keep these tests
    /// focused on lifecycle ordering; artifact reconciliation has dedicated
    /// adversarial coverage in `JobResultContractTests` and
    /// `JobArtifactAccessTests`.
    private func validResultScript(outputDirectory: URL) -> String {
        let path = outputDirectory.path
        return """
        job_device=$(/usr/bin/stat -f %d '\(path)')
        job_inode=$(/usr/bin/stat -f %i '\(path)')
        /usr/bin/printf '{"event":"result","payload":{"schema_version":"gui_job_result.v1","output_directory":"\(path)","output_identity":{"device":%s,"inode":%s},"artifact_scope":"immediate_regular_files","artifact_count":0,"language":null,"speaker_turns":0,"words":3,"segments":1,"artifacts":[],"qa":{"status":"missing","source_artifact":"qa_report.json","byte_limit":1048576,"overall_quality_summary":null,"flag_count":null,"error_flag_count":null,"warning_flag_count":null,"info_flag_count":null,"changed_span_count":null,"flags":[],"omitted_flag_count":0},"transcript_preview":{"status":"missing","source_artifact":"transcript.txt","byte_limit":65536,"text":null,"displayed_bytes":0,"source_bytes":null,"truncated":false}}}\\n' "$job_device" "$job_inode"
        """
    }

    private func XCTAssertStageDone(
        _ id: String,
        in stages: [PipelineStage],
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        guard let stage = stages.first(where: { $0.id == id }) else {
            XCTFail("missing stage \(id)", file: file, line: line)
            return
        }
        if case .done = stage.status {
            return
        }
        XCTFail("stage \(id) is not done", file: file, line: line)
    }
}
