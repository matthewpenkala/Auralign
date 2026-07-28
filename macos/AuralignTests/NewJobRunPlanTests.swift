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

    func testAlignmentCanvasProjectionUsesOnlyRealStageAndProbeEvidence() throws {
        let projection = AlignmentCanvasProjection(
            stages: [
                PipelineStage(
                    id: "canonical_audio",
                    title: "Prepare audio",
                    status: .done(seconds: 1.2),
                    detail: "Ready"
                ),
                PipelineStage(
                    id: "asr",
                    title: "Transcribe",
                    status: .active,
                    detail: "MLX"
                ),
                PipelineStage(
                    id: "diarization",
                    title: "Separate speakers",
                    status: .skipped(reason: "Disabled in configuration")
                ),
                PipelineStage(
                    id: "alignment",
                    title: "Align words",
                    status: .pending
                ),
                PipelineStage(
                    id: "export",
                    title: "Export",
                    status: .pending
                ),
            ],
            posture: .running,
            probe: MediaProbe(
                durationSeconds: 65,
                sampleRate: 48_000,
                channels: 1,
                codec: "pcm_s16le",
                formatName: "wav"
            )
        )

        XCTAssertEqual(
            try XCTUnwrap(
                projection.segments.first { $0.phase == .source }
            ).status,
            .complete
        )
        XCTAssertEqual(
            try XCTUnwrap(
                projection.segments.first { $0.phase == .recognition }
            ).status,
            .active
        )
        XCTAssertEqual(
            try XCTUnwrap(
                projection.segments.first { $0.phase == .speakers }
            ).status,
            .skipped
        )
        XCTAssertEqual(projection.completedFraction, 0.25)
        XCTAssertEqual(projection.sourceDurationText, "1:05")
        XCTAssertEqual(projection.sourceFacts, ["mono", "48 kHz", "pcm_s16le"])
        XCTAssertTrue(projection.accessibilitySummary.contains("Recognition: Active."))
        XCTAssertTrue(projection.accessibilitySummary.contains("Speakers: Disabled."))
    }

    func testAlignmentCanvasMarksUnreachedRunnablePhasesAsHalted() throws {
        let projection = AlignmentCanvasProjection(
            stages: [
                PipelineStage(
                    id: "asr",
                    title: "Transcribe",
                    status: .pending
                )
            ],
            posture: .failed,
            probe: nil
        )

        XCTAssertEqual(
            try XCTUnwrap(
                projection.segments.first { $0.phase == .recognition }
            ).status,
            .halted
        )
        XCTAssertEqual(projection.completedFraction, 0)
        XCTAssertNil(projection.sourceDurationSeconds)
        XCTAssertTrue(projection.sourceFacts.isEmpty)
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
