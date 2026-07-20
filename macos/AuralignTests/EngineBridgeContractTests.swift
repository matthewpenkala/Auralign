// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class EngineBridgeContractTests: XCTestCase {
    func testOversizedRequestsFailBeforeEitherProcessLaunch() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-oversized-request-\(UUID().uuidString)",
                isDirectory: true
            )
        let marker = root.appendingPathComponent("launched")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        let fixture = try makeBridge(
            script: """
            #!/bin/sh
            /usr/bin/touch '\(marker.path)'
            /bin/cat >/dev/null
            """
        )
        defer {
            try? FileManager.default.removeItem(at: fixture.root)
            try? FileManager.default.removeItem(at: root)
        }
        let oversized = JSONValue.string(
            String(
                repeating: "x",
                count: EngineBridge.maximumRequestBytes + 1
            )
        )

        do {
            _ = try await fixture.bridge.run(
                command: ["validate"],
                input: oversized,
                timeout: 5
            )
            XCTFail("oversized one-shot input must fail before launch")
        } catch let error as EngineBridge.BridgeError {
            XCTAssertTrue(error.message.contains("1 MB safety limit"))
        }
        XCTAssertThrowsError(
            try fixture.bridge.startTranscription(payload: oversized)
        ) { error in
            XCTAssertTrue(error.localizedDescription.contains("1 MB safety limit"))
        }
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: marker.path),
            "neither oversized request may launch the bridge executable"
        )
    }

    func testOneShotProcessRejectsDuplicateSuccessEvents() async throws {
        let fixture = try makeBridge(
            script: """
            #!/bin/sh
            /bin/echo '{"event":"handshake","payload":{}}'
            /bin/echo '{"event":"handshake","payload":{}}'
            """
        )
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        do {
            _ = try await fixture.bridge.run(
                command: ["handshake"],
                timeout: 5
            )
            XCTFail("duplicate one-shot responses must fail closed")
        } catch let error as EngineBridge.BridgeError {
            XCTAssertTrue(error.message.contains("returned 2 events"))
            XCTAssertTrue(error.message.contains("exactly one"))
        }
    }

    func testOneShotProcessRejectsValidEventForAnotherCommand() async throws {
        let fixture = try makeBridge(
            script: """
            #!/bin/sh
            /bin/echo '{"event":"doctor","payload":{}}'
            """
        )
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        do {
            _ = try await fixture.bridge.run(
                command: ["handshake"],
                timeout: 5
            )
            XCTFail("a response for another command must fail closed")
        } catch let error as EngineBridge.BridgeError {
            XCTAssertTrue(error.message.contains("unexpected event"))
            XCTAssertTrue(error.message.contains("handshake"))
        }
    }

    func testOneShotProcessRejectsStreamingVocabulary() async throws {
        let fixture = try makeBridge(
            script: """
            #!/bin/sh
            /bin/echo '{"event":"job_accepted","payload":{}}'
            """
        )
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        do {
            _ = try await fixture.bridge.run(
                command: ["handshake"],
                timeout: 5
            )
            XCTFail("a streaming event must not cross the one-shot boundary")
        } catch let error as EngineBridge.BridgeError {
            XCTAssertEqual(
                error.message,
                "Engine bridge produced malformed protocol output."
            )
        }
    }

    func testStreamingProcessRejectsOneShotVocabulary() async throws {
        let fixture = try makeBridge(
            script: """
            #!/bin/sh
            /bin/cat >/dev/null
            /bin/echo '{"event":"handshake","payload":{}}'
            """
        )
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let job = try fixture.bridge.startTranscription(
            payload: .object(["fixture": .bool(true)])
        )
        var events: [BridgeEvent] = []

        for await event in job.events {
            events.append(event)
        }

        XCTAssertEqual(events.map(\.name), ["bridge_error", "process_exited"])
        XCTAssertEqual(
            events.first?.payload.value(at: "message")?.stringValue,
            "The engine bridge produced malformed protocol output."
        )
        let exited = await job.waitForExit(timeout: 0.1)
        XCTAssertTrue(exited)
    }

    private func makeBridge(script: String) throws -> (
        bridge: EngineBridge,
        root: URL
    ) {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-bridge-contract-\(UUID().uuidString)",
                isDirectory: true
            )
        let package = root.appendingPathComponent(
            "src/local_transcript_max",
            isDirectory: true
        )
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
        return (
            EngineBridge(
                location: EngineLocation(repositoryRoot: root)
            ),
            root
        )
    }
}
