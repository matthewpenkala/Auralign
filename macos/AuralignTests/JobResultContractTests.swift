// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class JobResultContractTests: XCTestCase {
    func testSharedPythonSwiftGoldenFixtureParses() throws {
        let fixtureURL = try XCTUnwrap(
            Bundle(for: Self.self).url(
                forResource: "gui_job_result_v1",
                withExtension: "json"
            )
        )
        let fixture = try JSONValue.decode(from: Data(contentsOf: fixtureURL))
        let expectedDirectory = URL(
            fileURLWithPath: "/private/tmp/Auralign Shared Fixture",
            isDirectory: true
        )

        let envelope = try PendingJobResultEnvelope.parse(
            fixture,
            configFingerprint: "shared-fixture",
            expectedOutputDirectory: expectedDirectory
        )

        XCTAssertEqual(envelope.outputDirectory, expectedDirectory)
        XCTAssertEqual(envelope.outputIdentity, .init(device: 14, inode: 21))
        XCTAssertEqual(envelope.qa.status, .available)
        XCTAssertEqual(envelope.transcriptPreview.text, "Synthetic preview")
    }

    func testExactAcceptanceEnvelopeAndIdentityParse() throws {
        let directory = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        let acceptance = try JobAcceptanceEnvelope.parse(
            acceptancePayload(outputDirectory: directory),
            expectedOutputDirectory: directory
        )

        XCTAssertEqual(acceptance.outputDirectory, directory)
        XCTAssertEqual(acceptance.outputIdentity, .init(device: 14, inode: 21))
    }

    func testAcceptanceRejectsSchemaPathTypeAndShapeDrift() {
        let directory = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        let other = URL(fileURLWithPath: "/private/tmp/Other Job", isDirectory: true)

        var wrongSchema = try! XCTUnwrap(
            acceptancePayload(outputDirectory: directory).objectValue
        )
        wrongSchema["schema_version"] = .string("gui_job_acceptance.v2")
        XCTAssertThrowsError(
            try JobAcceptanceEnvelope.parse(
                .object(wrongSchema),
                expectedOutputDirectory: directory
            )
        )

        XCTAssertThrowsError(
            try JobAcceptanceEnvelope.parse(
                acceptancePayload(outputDirectory: other),
                expectedOutputDirectory: directory
            )
        )

        var wrongType = try! XCTUnwrap(
            acceptancePayload(outputDirectory: directory).objectValue
        )
        wrongType["output_identity"] = .string("14:21")
        XCTAssertThrowsError(
            try JobAcceptanceEnvelope.parse(
                .object(wrongType),
                expectedOutputDirectory: directory
            )
        )

        var extra = try! XCTUnwrap(
            acceptancePayload(outputDirectory: directory).objectValue
        )
        extra["unexpected"] = .bool(true)
        XCTAssertThrowsError(
            try JobAcceptanceEnvelope.parse(
                .object(extra),
                expectedOutputDirectory: directory
            )
        )
    }

    func testExactEnvelopeParsesWithoutReadingTheFilesystem() throws {
        let directory = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        let envelope = try PendingJobResultEnvelope.parse(
            payload(outputDirectory: directory),
            configFingerprint: "configuration-fingerprint",
            expectedOutputDirectory: directory
        )

        XCTAssertEqual(envelope.outputDirectory, directory)
        XCTAssertEqual(envelope.outputIdentity, .init(device: 14, inode: 21))
        XCTAssertEqual(envelope.artifacts.map(\.name), ["qa_report.json", "transcript.txt"])
        XCTAssertEqual(envelope.qa.status, .available)
        XCTAssertEqual(envelope.qa.warningFlagCount, 1)
        XCTAssertEqual(envelope.transcriptPreview.text, "Synthetic preview")
        XCTAssertFalse(envelope.transcriptPreview.truncated)
    }

    func testEnvelopeRejectsExtraMissingAndWrongTypedFields() {
        let directory = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        var extra = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        extra["unexpected"] = .bool(true)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(extra),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )

        var missing = extra
        missing.removeValue(forKey: "unexpected")
        missing.removeValue(forKey: "qa")
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(missing),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )

        var wrongType = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        wrongType["words"] = .bool(true)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(wrongType),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )
    }

    func testEnvelopeRejectsDirectoryArtifactAndProjectionDrift() {
        let expected = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        let other = URL(fileURLWithPath: "/private/tmp/Other Job", isDirectory: true)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                payload(outputDirectory: other),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: expected
            )
        )

        var artifactDrift = try! XCTUnwrap(payload(outputDirectory: expected).objectValue)
        artifactDrift["artifact_count"] = .int(1)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(artifactDrift),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: expected
            )
        )

        var previewDrift = try! XCTUnwrap(payload(outputDirectory: expected).objectValue)
        var preview = try! XCTUnwrap(previewDrift["transcript_preview"]?.objectValue)
        preview["displayed_bytes"] = .int(1)
        previewDrift["transcript_preview"] = .object(preview)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(previewDrift),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: expected
            )
        )
    }

    func testUnavailableQAAndMissingTranscriptCannotCarryFacts() {
        let directory = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        var root = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        var qa = try! XCTUnwrap(root["qa"]?.objectValue)
        qa["status"] = .string("missing")
        root["qa"] = .object(qa)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(root),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )

        root = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        var preview = try! XCTUnwrap(root["transcript_preview"]?.objectValue)
        preview["status"] = .string("missing")
        root["transcript_preview"] = .object(preview)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(root),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )
    }

    func testAvailableAndDisabledQARequireNonemptyReportArtifact() {
        let directory = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        var root = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        var artifacts = try! XCTUnwrap(root["artifacts"]?.arrayValue)
        var qaArtifact = try! XCTUnwrap(artifacts[0].objectValue)
        qaArtifact["size_bytes"] = .int(0)
        artifacts[0] = .object(qaArtifact)
        root["artifacts"] = .array(artifacts)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(root),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )

        var disabled = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        var disabledArtifacts = try! XCTUnwrap(disabled["artifacts"]?.arrayValue)
        var disabledQAArtifact = try! XCTUnwrap(disabledArtifacts[0].objectValue)
        disabledQAArtifact["size_bytes"] = .int(0)
        disabledArtifacts[0] = .object(disabledQAArtifact)
        disabled["artifacts"] = .array(disabledArtifacts)
        disabled["qa"] = disabledQAProjection()
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(disabled),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )
    }

    func testAvailableQARejectsInconsistentSeverityTotalsAndSummary() {
        let directory = URL(fileURLWithPath: "/private/tmp/Auralign Job", isDirectory: true)
        var root = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        var qa = try! XCTUnwrap(root["qa"]?.objectValue)
        qa["warning_flag_count"] = .int(0)
        root["qa"] = .object(qa)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(root),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )

        root = try! XCTUnwrap(payload(outputDirectory: directory).objectValue)
        qa = try! XCTUnwrap(root["qa"]?.objectValue)
        qa["overall_quality_summary"] = .string("no_deterministic_warnings")
        root["qa"] = .object(qa)
        XCTAssertThrowsError(
            try PendingJobResultEnvelope.parse(
                .object(root),
                configFingerprint: "fingerprint",
                expectedOutputDirectory: directory
            )
        )
    }

    private func acceptancePayload(outputDirectory: URL) -> JSONValue {
        .object([
            "schema_version": .string("gui_job_acceptance.v1"),
            "output_directory": .string(outputDirectory.path),
            "output_identity": .object([
                "device": .int(14),
                "inode": .int(21),
            ]),
        ])
    }

    private func disabledQAProjection() -> JSONValue {
        .object([
            "status": .string("disabled"),
            "source_artifact": .string("qa_report.json"),
            "byte_limit": .int(1_048_576),
            "overall_quality_summary": .null,
            "flag_count": .int(0),
            "error_flag_count": .int(0),
            "warning_flag_count": .int(0),
            "info_flag_count": .int(0),
            "changed_span_count": .int(0),
            "flags": .array([]),
            "omitted_flag_count": .int(0),
        ])
    }

    private func payload(outputDirectory: URL) -> JSONValue {
        .object([
            "schema_version": .string("gui_job_result.v1"),
            "output_directory": .string(outputDirectory.path),
            "output_identity": .object([
                "device": .int(14),
                "inode": .int(21),
            ]),
            "artifact_scope": .string("immediate_regular_files"),
            "artifact_count": .int(2),
            "language": .string("en"),
            "speaker_turns": .int(4),
            "words": .int(32),
            "segments": .int(7),
            "artifacts": .array([
                .object([
                    "name": .string("qa_report.json"),
                    "size_bytes": .int(128),
                    "identity": .object([
                        "device": .int(14),
                        "inode": .int(22),
                    ]),
                ]),
                .object([
                    "name": .string("transcript.txt"),
                    "size_bytes": .int(17),
                    "identity": .object([
                        "device": .int(14),
                        "inode": .int(23),
                    ]),
                ]),
            ]),
            "qa": .object([
                "status": .string("available"),
                "source_artifact": .string("qa_report.json"),
                "byte_limit": .int(1_048_576),
                "overall_quality_summary": .string("review_warnings"),
                "flag_count": .int(1),
                "error_flag_count": .int(0),
                "warning_flag_count": .int(1),
                "info_flag_count": .int(0),
                "changed_span_count": .int(0),
                "flags": .array([
                    .object([
                        "code": .string("alignment_gap"),
                        "severity": .string("warning"),
                        "message": .string("Review one deterministic warning."),
                    ]),
                ]),
                "omitted_flag_count": .int(0),
            ]),
            "transcript_preview": .object([
                "status": .string("available"),
                "source_artifact": .string("transcript.txt"),
                "byte_limit": .int(65_536),
                "text": .string("Synthetic preview"),
                "displayed_bytes": .int(17),
                "source_bytes": .int(17),
                "truncated": .bool(false),
            ]),
        ])
    }
}
