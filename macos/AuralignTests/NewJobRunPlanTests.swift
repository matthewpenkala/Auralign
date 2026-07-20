// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class NewJobRunPlanTests: XCTestCase {
    func testProjectionUsesEffectiveConfigurationWithoutInventingFacts() {
        var config = JSONValue.object([:])
        config = config.setting("asr.language", to: .string("en"))
        config = config.setting("diarization.num_speakers", to: .int(3))
        config = config.setting("offline", to: .bool(true))
        let projection = NewJobRunPlanProjection(
            effectiveConfig: config,
            preset: "balanced",
            overrideCount: 2
        )

        XCTAssertEqual(projection.preset, "balanced")
        XCTAssertEqual(projection.language, "en")
        XCTAssertEqual(projection.speakerCount, "3")
        XCTAssertTrue(projection.offline)
        XCTAssertEqual(projection.overrideCount, 2)
    }

    func testProjectionPreservesAuthoritativeCoreStageOrder() throws {
        let projection = NewJobRunPlanProjection(
            effectiveConfig: .object([:]),
            preset: "max",
            overrideCount: 0
        )
        let ids = projection.stages.map(\.id)

        XCTAssertLessThan(
            try XCTUnwrap(ids.firstIndex(of: "asr")),
            try XCTUnwrap(ids.firstIndex(of: "alignment"))
        )
        XCTAssertLessThan(
            try XCTUnwrap(ids.firstIndex(of: "alignment")),
            try XCTUnwrap(ids.firstIndex(of: "diarization"))
        )
        XCTAssertLessThan(
            try XCTUnwrap(ids.firstIndex(of: "diarization")),
            try XCTUnwrap(ids.firstIndex(of: "fusion"))
        )
    }

    func testProjectionUsesAutomaticLabelsWhenOptionalValuesAreAbsent() {
        let projection = NewJobRunPlanProjection(
            effectiveConfig: .object([:]),
            preset: "cpu",
            overrideCount: 0
        )

        XCTAssertEqual(projection.language, "Automatic")
        XCTAssertEqual(projection.speakerCount, "Automatic")
        XCTAssertFalse(projection.offline)
    }

    func testProjectionLabelsDisabledDiarizationWithoutInventingSpeakerWork() {
        let config = JSONValue.object([:])
            .setting("diarization.enabled", to: .bool(false))
            .setting("diarization.num_speakers", to: .int(3))
        let projection = NewJobRunPlanProjection(
            effectiveConfig: config,
            preset: "balanced",
            overrideCount: 0
        )

        XCTAssertEqual(projection.speakerCount, "Disabled")
        XCTAssertEqual(
            projection.stages.first(where: { $0.id == "diarization" })?.status,
            .skipped(reason: "Disabled in configuration")
        )
    }

    func testPrivacySafePresentationReplacesKnownLocalLocations() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let input = home.appendingPathComponent("Private/secret.wav")
        let output = home.appendingPathComponent("Private/Output", isDirectory: true)
        let raw = "Could not inspect \(input.path) while writing \(output.path)."

        let presented = PrivacySafePresentation.message(
            raw,
            replacing: [
                (input, "the selected media"),
                (output, "the selected output folder"),
            ]
        )

        XCTAssertFalse(presented.contains(home.path))
        XCTAssertFalse(presented.contains("secret.wav"))
        XCTAssertEqual(
            presented,
            "Could not inspect the selected media while writing "
                + "the selected output folder."
        )
    }

    func testJobDirectoryMustBeImmediateChildOfSelectedOutputRoot() {
        let output = URL(fileURLWithPath: "/tmp/auralign-output", isDirectory: true)

        XCTAssertTrue(
            JobController.jobDirectory(
                output.appendingPathComponent("job", isDirectory: true),
                belongsTo: output
            )
        )
        XCTAssertFalse(
            JobController.jobDirectory(
                output.deletingLastPathComponent()
                    .appendingPathComponent("escaped", isDirectory: true),
                belongsTo: output
            )
        )
        XCTAssertFalse(JobController.jobDirectory(output, belongsTo: output))
    }
}
