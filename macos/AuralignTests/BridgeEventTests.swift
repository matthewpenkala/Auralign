// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class BridgeEventTests: XCTestCase {
    func testDecodesEventLine() throws {
        let line = #"{"event": "asr_done", "payload": {"segments": 4, "language": "en"}}"#
        let event = try XCTUnwrap(BridgeEvent.decode(line: line))
        XCTAssertEqual(event.name, "asr_done")
        XCTAssertEqual(event.payload.value(at: "segments"), .int(4))
        XCTAssertEqual(event.payload.value(at: "language"), .string("en"))
    }

    func testRejectsNonProtocolLines() {
        XCTAssertNil(BridgeEvent.decode(line: "some library noise"))
        XCTAssertNil(BridgeEvent.decode(line: "{\"payload\": {}}"))
    }

    func testRejectsMissingNullScalarAndArrayPayloads() {
        XCTAssertNil(
            BridgeEvent.decode(line: #"{"event":"handshake"}"#)
        )
        XCTAssertNil(
            BridgeEvent.decode(
                line: #"{"event":"handshake","payload":null}"#
            )
        )
        XCTAssertNil(
            BridgeEvent.decode(
                line: #"{"event":"handshake","payload":"ready"}"#
            )
        )
        XCTAssertNil(
            BridgeEvent.decode(
                line: #"{"event":"handshake","payload":42}"#
            )
        )
        XCTAssertNil(
            BridgeEvent.decode(
                line: #"{"event":"handshake","payload":[]}"#
            )
        )
        XCTAssertNotNil(
            BridgeEvent.decode(
                line: #"{"event":"handshake","payload":{}}"#
            )
        )
    }

    func testWireVocabulariesSeparateOneShotAndStreamingEvents() {
        let handshake = #"{"event":"handshake","payload":{}}"#
        let accepted = #"{"event":"job_accepted","payload":{}}"#
        let failure = #"{"event":"bridge_error","payload":{}}"#
        let localExit = #"{"event":"process_exited","payload":{"code":0}}"#

        XCTAssertNotNil(
            BridgeEvent.decode(line: handshake, vocabulary: .oneShot)
        )
        XCTAssertNil(
            BridgeEvent.decode(line: handshake, vocabulary: .streaming)
        )
        XCTAssertNil(
            BridgeEvent.decode(line: accepted, vocabulary: .oneShot)
        )
        XCTAssertNotNil(
            BridgeEvent.decode(line: accepted, vocabulary: .streaming)
        )
        XCTAssertNotNil(
            BridgeEvent.decode(line: failure, vocabulary: .oneShot)
        )
        XCTAssertNotNil(
            BridgeEvent.decode(line: failure, vocabulary: .streaming)
        )
        XCTAssertNil(BridgeEvent.decode(line: localExit))
        XCTAssertEqual(
            BridgeEvent.oneShotNames.intersection(BridgeEvent.streamingNames),
            ["bridge_error"]
        )
    }

    func testOneShotInvocationMappingIsExact() {
        XCTAssertEqual(
            EngineBridge.oneShotSuccessEvent(for: ["handshake"]),
            "handshake"
        )
        XCTAssertEqual(
            EngineBridge.oneShotSuccessEvent(for: ["schema"]),
            "schema"
        )
        XCTAssertEqual(
            EngineBridge.oneShotSuccessEvent(
                for: ["schema", "--write", "/tmp/manifest.json"]
            ),
            "schema_written"
        )
        XCTAssertNil(
            EngineBridge.oneShotSuccessEvent(for: ["transcribe"])
        )
        XCTAssertNil(
            EngineBridge.oneShotSuccessEvent(for: ["handshake", "unexpected"])
        )
        XCTAssertNil(
            EngineBridge.oneShotSuccessEvent(for: ["schema", "--write"])
        )
    }

    func testOneShotResponseRejectsDuplicatesAndUnexpectedEvents() {
        let handshake = BridgeEvent(name: "handshake", payload: .object([:]))
        let doctor = BridgeEvent(name: "doctor", payload: .object([:]))
        let streaming = BridgeEvent(name: "job_accepted", payload: .object([:]))
        let failure = BridgeEvent(name: "bridge_error", payload: .object([:]))

        XCTAssertNoThrow(
            try EngineBridge.validateOneShotResponse(
                [handshake],
                expected: "handshake"
            )
        )
        XCTAssertNoThrow(
            try EngineBridge.validateOneShotResponse(
                [failure],
                expected: "handshake"
            )
        )
        XCTAssertThrowsError(
            try EngineBridge.validateOneShotResponse(
                [handshake, handshake],
                expected: "handshake"
            )
        ) { error in
            XCTAssertTrue(error.localizedDescription.contains("exactly one"))
        }
        XCTAssertThrowsError(
            try EngineBridge.validateOneShotResponse(
                [doctor],
                expected: "handshake"
            )
        ) { error in
            XCTAssertTrue(error.localizedDescription.contains("unexpected event"))
        }
        XCTAssertThrowsError(
            try EngineBridge.validateOneShotResponse(
                [streaming],
                expected: "handshake"
            )
        )
        XCTAssertThrowsError(
            try EngineBridge.validateOneShotResponse(
                [doctor, handshake],
                expected: "handshake"
            )
        )
    }

    func testParsesValidationErrors() throws {
        let line = #"""
        {"event": "validate", "payload": {"ok": false, "errors": [
            {"loc": ["diarization"], "message": "min_speakers must be less than or equal to max_speakers", "type": "value_error"},
            {"loc": ["asr", "beam_size"], "message": "Input should be a valid integer", "type": "int_type"}
        ]}}
        """#
        let event = try XCTUnwrap(
            BridgeEvent.decode(line: line.replacingOccurrences(of: "\n", with: " "))
        )
        let errors = try XCTUnwrap(ConfigFieldError.parse(from: event.payload))
        XCTAssertEqual(errors.count, 2)
        XCTAssertEqual(errors[0].dottedLocation, "diarization")
        XCTAssertEqual(errors[1].dottedLocation, "asr.beam_size")
    }

    func testValidationErrorsRejectMalformedRowsAsOneAuthoritativeArray() {
        let malformed = JSONValue.object([
            "errors": .array([
                .object([
                    "loc": .array([.string("asr"), .string("beam_size")]),
                    "message": .string("Invalid beam size"),
                    "type": .string("value_error"),
                ]),
                .object([
                    "loc": .array([.string("cleanup"), .int(1)]),
                    "message": .string("Malformed location"),
                    "type": .string("value_error"),
                ]),
            ]),
        ])

        XCTAssertNil(ConfigFieldError.parse(from: malformed))
        XCTAssertEqual(
            ConfigFieldError.parse(from: .object(["errors": .array([])])),
            []
        )
        XCTAssertNil(ConfigFieldError.parse(from: .object([:])))
        XCTAssertNil(
            ConfigFieldError.parse(
                from: .object([
                    "errors": .array([
                        .object([
                            "loc": .array([.string("asr"), .string("beam_size")]),
                            "message": .string("Missing authoritative type"),
                        ]),
                    ]),
                ])
            )
        )
        let duplicate = JSONValue.object([
            "errors": .array([
                .object([
                    "loc": .array([.string("asr"), .string("beam_size")]),
                    "message": .string("Duplicate"),
                    "type": .string("value_error"),
                ]),
                .object([
                    "loc": .array([.string("asr"), .string("beam_size")]),
                    "message": .string("Duplicate"),
                    "type": .string("value_error"),
                ]),
            ]),
        ])
        XCTAssertNil(ConfigFieldError.parse(from: duplicate))
    }

    func testParsesDoctorSummary() throws {
        let line = #"{"event":"doctor","payload":{"ok":false,"offline":true,"checks":[{"name":"ffmpeg","status":"ok","message":"/opt/ffmpeg"},{"name":"startup:asr:mlx-whisper","status":"error","message":"backend unavailable"},{"name":"model:asr:large-v3","status":"error","message":"directory missing"}],"startup_evidence":{"schema_version":"doctor_startup_evidence.v1","requested":true,"complete":true,"expected_ids":["startup:asr:mlx-whisper"],"observed_ids":["startup:asr:mlx-whisper"],"expected_count":1,"observed_count":1}}}"#
        let event = try XCTUnwrap(BridgeEvent.decode(line: line))
        let summary = try XCTUnwrap(DoctorSummary.parse(from: event.payload))
        XCTAssertFalse(summary.ok)
        XCTAssertTrue(summary.offline)
        XCTAssertEqual(
            summary.errors.map(\.name),
            ["startup:asr:mlx-whisper", "model:asr:large-v3"]
        )
        XCTAssertTrue(summary.startupEvidence?.complete == true)
    }

    func testRejectsMalformedOrUnknownDoctorChecks() {
        let malformed = JSONValue.object([
            "ok": .bool(true),
            "checks": .array([
                .object([
                    "name": .string("future"),
                    "status": .string("maybe"),
                    "message": .string("unknown status"),
                ]),
            ]),
        ])

        XCTAssertNil(DoctorSummary.parse(from: malformed))
    }

    func testDoctorSummaryRejectsMalformedOrIncompleteStartupEvidence() {
        let malformed = JSONValue.object([
            "ok": .bool(true),
            "offline": .bool(true),
            "checks": .array([]),
            "startup_evidence": .object([
                "schema_version": .string("doctor_startup_evidence.v1"),
                "requested": .bool(true),
                "complete": .bool(true),
                "expected_ids": .array([.string("startup:asr:mlx-whisper")]),
                "observed_ids": .array([]),
                "expected_count": .int(1),
                "observed_count": .int(0),
            ]),
        ])
        XCTAssertNil(DoctorSummary.parse(from: malformed))

        let missingOffline = JSONValue.object([
            "ok": .bool(true),
            "checks": .array([]),
            "startup_evidence": .object([:]),
        ])
        XCTAssertNil(DoctorSummary.parse(from: missingOffline))

        let reordered = JSONValue.object([
            "ok": .bool(true),
            "offline": .bool(false),
            "checks": .array([]),
            "startup_evidence": .object([
                "schema_version": .string("doctor_startup_evidence.v1"),
                "requested": .bool(true),
                "complete": .bool(true),
                "expected_ids": .array([
                    .string("startup:asr:a"),
                    .string("startup:alignment:b"),
                ]),
                "observed_ids": .array([
                    .string("startup:alignment:b"),
                    .string("startup:asr:a"),
                ]),
                "expected_count": .int(2),
                "observed_count": .int(2),
            ]),
        ])
        XCTAssertNil(DoctorSummary.parse(from: reordered))
    }

    func testMediaProbeRequiresUsableCoreMetadata() {
        XCTAssertNil(MediaProbe.parse(from: .object([:])))
        XCTAssertNil(
            MediaProbe.parse(
                from: .object([
                    "duration_seconds": .double(1),
                    "sample_rate": .int(0),
                    "channels": .int(1),
                ])
            )
        )
        XCTAssertNotNil(
            MediaProbe.parse(
                from: .object([
                    "duration_seconds": .double(1),
                    "sample_rate": .int(16_000),
                    "channels": .int(1),
                ])
            )
        )
        XCTAssertNil(
            MediaProbe.parse(
                from: .object([
                    "duration_seconds": .double(1e100),
                    "sample_rate": .int(16_000),
                    "channels": .int(1),
                ])
            )
        )
        XCTAssertNil(
            MediaProbe.parse(
                from: .object([
                    "duration_seconds": .double(1),
                    "sample_rate": .double(16_000),
                    "channels": .int(1),
                ])
            )
        )
    }

    func testJSONValuePathReadWrite() {
        var config = JSONValue.object([:])
        config = config.setting("asr.beam_size", to: .int(7))
        config = config.setting("diarization.num_speakers", to: .null)
        XCTAssertEqual(config.value(at: "asr.beam_size"), .int(7))
        XCTAssertEqual(config.value(at: "diarization.num_speakers"), .null)
        XCTAssertNil(config.value(at: "asr.missing"))
        XCTAssertEqual(
            config.canonicalJSONString(),
            #"{"asr":{"beam_size":7},"diarization":{"num_speakers":null}}"#
        )
    }

    func testJSONIntegerProjectionCannotTrapOrRelaxProtocolCounts() {
        XCTAssertNil(JSONValue.double(1e100).intValue)
        XCTAssertNil(JSONValue.double(.infinity).intValue)
        XCTAssertNil(JSONValue.double(-.infinity).intValue)
        XCTAssertNil(JSONValue.double(.nan).intValue)
        XCTAssertNil(JSONValue.double(1.5).intValue)
        XCTAssertEqual(JSONValue.double(42).intValue, 42)
        XCTAssertNil(JSONValue.double(42).strictIntValue)
        XCTAssertEqual(JSONValue.int(42).strictIntValue, 42)
    }

    func testDoctorCompletenessCountsRejectFloatingPointNumbers() {
        let evidence = JSONValue.object([
            "schema_version": .string("doctor_startup_evidence.v1"),
            "requested": .bool(true),
            "complete": .bool(true),
            "expected_ids": .array([.string("startup:asr:test")]),
            "observed_ids": .array([.string("startup:asr:test")]),
            "expected_count": .double(1),
            "observed_count": .int(1),
        ])

        XCTAssertNil(DoctorStartupEvidence.parse(from: evidence))
    }

    func testJobDirectoryNamingIsPredictable() {
        let directory = JobController.proposedJobDirectory(
            outputRoot: URL(fileURLWithPath: "/tmp/out"),
            mediaURL: URL(fileURLWithPath: "/media/Interview Final.mov"),
            now: Date(timeIntervalSince1970: 1_752_868_205)
        )
        XCTAssertTrue(directory.lastPathComponent.hasPrefix("Interview Final "))
        XCTAssertEqual(directory.deletingLastPathComponent().path, "/tmp/out")
    }

    func testExpectationsFollowConfiguration() {
        var config = JSONValue.object([:])
        config = config.setting("diarization.enabled", to: .bool(false))
        config = config.setting("diarization.backend", to: .string("off"))
        config = config.setting("alignment.enabled", to: .bool(true))
        config = config.setting("alignment.backend", to: .string("auto"))
        config = config.setting("redecode.mode", to: .string("low-confidence"))
        config = config.setting("speaker_identity.mode", to: .string("off"))
        let expectations = JobExpectations(config: config)
        XCTAssertFalse(expectations.diarizationEnabled)
        XCTAssertTrue(expectations.redecodeEnabled)
        let stages = expectations.expectedStages()
        let diarization = stages.first { $0.id == "diarization" }
        if case .skipped = diarization?.status {} else {
            XCTFail("diarization should be marked skipped")
        }
        let stageIDs = stages.map(\.id)
        XCTAssertLessThan(
            try XCTUnwrap(stageIDs.firstIndex(of: "alignment")),
            try XCTUnwrap(stageIDs.firstIndex(of: "diarization"))
        )
        XCTAssertTrue(stages.contains { $0.id == "redecode" })
        XCTAssertFalse(stages.contains { $0.id == "speaker_identity" })
    }

    func testDisabledAlignmentRemainsInOrdinalPlanAsSkipped() throws {
        var config = JSONValue.object([:])
        config = config.setting("alignment.enabled", to: .bool(false))
        config = config.setting("alignment.backend", to: .string("off"))
        let alignment = try XCTUnwrap(
            JobExpectations(config: config)
                .expectedStages()
                .first { $0.id == "alignment" }
        )
        if case .skipped(let reason) = alignment.status {
            XCTAssertEqual(reason, "Disabled in configuration")
        } else {
            XCTFail("disabled alignment must remain visible as a skipped ordinal stage")
        }
    }
}
