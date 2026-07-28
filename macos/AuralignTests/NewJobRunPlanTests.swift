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

        let geometry = AlignmentFieldGeometry(projection: projection)
        XCTAssertFalse(geometry.widthsAreMeasured)
        XCTAssertEqual(geometry.cells.count, AlignmentCanvasPhase.allCases.count)
        for cell in geometry.cells {
            XCTAssertEqual(cell.end - cell.start, 0.2, accuracy: 0.000_001)
        }
    }

    func testAlignmentFieldEnergyPolicyDoesNotInventPreRunOrFinishedActivity() {
        let stages = [
            PipelineStage(
                id: "canonical_audio",
                title: "Prepare audio",
                status: .done(seconds: 1.2)
            ),
            PipelineStage(
                id: "asr",
                title: "Transcribe",
                status: .active
            ),
            PipelineStage(
                id: "alignment",
                title: "Align words",
                status: .pending
            ),
        ]

        let preview = AlignmentFieldEnergyPolicy(
            projection: AlignmentCanvasProjection(
                stages: stages,
                posture: .ready,
                probe: nil
            )
        )
        XCTAssertTrue(preview.entries.allSatisfy { $0.level == .quiet })
        XCTAssertFalse(preview.hasActiveEnergy)

        let running = AlignmentFieldEnergyPolicy(
            projection: AlignmentCanvasProjection(
                stages: stages,
                posture: .running,
                probe: nil
            )
        )
        XCTAssertEqual(running.level(for: .source), .settled)
        XCTAssertEqual(running.level(for: .recognition), .active)
        XCTAssertEqual(running.level(for: .refinement), .quiet)
        XCTAssertTrue(running.hasActiveEnergy)

        let finished = AlignmentFieldEnergyPolicy(
            projection: AlignmentCanvasProjection(
                stages: stages,
                posture: .completed,
                probe: nil
            )
        )
        XCTAssertEqual(finished.level(for: .source), .settled)
        XCTAssertEqual(finished.level(for: .recognition), .quiet)
        XCTAssertFalse(finished.hasActiveEnergy)
    }

    func testAlignmentFieldEnergyPatternIsDeterministicAndCategoricallyBounded() {
        let projection = AlignmentCanvasProjection(
            stages: [
                PipelineStage(
                    id: "canonical_audio",
                    title: "Prepare audio",
                    status: .done(seconds: 1.2)
                ),
                PipelineStage(
                    id: "asr",
                    title: "Transcribe",
                    status: .active
                ),
                PipelineStage(
                    id: "alignment",
                    title: "Align words",
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
        let geometry = AlignmentFieldGeometry(projection: projection)
        let policy = AlignmentFieldEnergyPolicy(projection: projection)
        let first = AlignmentFieldEnergyPattern(
            geometry: geometry,
            projection: projection,
            policy: policy
        )
        let second = AlignmentFieldEnergyPattern(
            geometry: geometry,
            projection: projection,
            policy: policy
        )

        XCTAssertEqual(first, second)
        XCTAssertEqual(
            first.particles.filter { $0.level == .settled }.count,
            3
        )
        XCTAssertEqual(
            first.particles.filter { $0.level == .active }.count,
            9
        )
        XCTAssertTrue(
            first.particles.allSatisfy {
                $0.phase == .source || $0.phase == .recognition
            }
        )

        for particle in first.particles {
            guard let cell = geometry.cells.first(where: {
                $0.phase == particle.phase
            }) else {
                XCTFail("Particle has no phase cell")
                continue
            }
            XCTAssertGreaterThanOrEqual(particle.unitX, cell.start)
            XCTAssertLessThanOrEqual(particle.unitX, cell.end)
            XCTAssertGreaterThanOrEqual(particle.unitY, -0.58)
            XCTAssertLessThanOrEqual(particle.unitY, 0.58)
            XCTAssertGreaterThan(particle.radius, 0)
            XCTAssertGreaterThanOrEqual(particle.aspect, 1)
        }
    }

    func testAlignmentFieldMotionRequiresEveryDecorativeMotionGate() {
        XCTAssertEqual(AlignmentFieldMotion.maximumRefreshRate, 30)
        XCTAssertLessThanOrEqual(
            1 / AlignmentFieldMotion.refreshInterval,
            30
        )
        XCTAssertTrue(
            AlignmentFieldMotion.allowsAnimation(
                hasActiveEnergy: true,
                reduceMotion: false,
                windowIsActive: true,
                captureAllowsMotion: true
            )
        )
        XCTAssertFalse(
            AlignmentFieldMotion.allowsAnimation(
                hasActiveEnergy: false,
                reduceMotion: false,
                windowIsActive: true,
                captureAllowsMotion: true
            )
        )
        XCTAssertFalse(
            AlignmentFieldMotion.allowsAnimation(
                hasActiveEnergy: true,
                reduceMotion: true,
                windowIsActive: true,
                captureAllowsMotion: true
            )
        )
        XCTAssertFalse(
            AlignmentFieldMotion.allowsAnimation(
                hasActiveEnergy: true,
                reduceMotion: false,
                windowIsActive: false,
                captureAllowsMotion: true
            )
        )
        XCTAssertFalse(
            AlignmentFieldMotion.allowsAnimation(
                hasActiveEnergy: true,
                reduceMotion: false,
                windowIsActive: true,
                captureAllowsMotion: false
            )
        )
    }

    func testOpticalMotionIsBoundedToAnActiveCoreOperation() {
        XCTAssertEqual(AuralignOpticalMotionPolicy.maximumRefreshRate, 15)
        XCTAssertEqual(AuralignOpticalMotionPolicy.loopPeriod, 8)
        XCTAssertLessThanOrEqual(
            1 / AuralignOpticalMotionPolicy.refreshInterval,
            15
        )
        XCTAssertTrue(
            AuralignOpticalMotionPolicy.allowsCoreLensAnimation(
                isActiveOperation: true,
                reduceMotion: false,
                windowIsActive: true,
                metalIsEnabled: true,
                captureAllowsMotion: true
            )
        )
        for denied in [
            (false, false, true, true, true),
            (true, true, true, true, true),
            (true, false, false, true, true),
            (true, false, true, false, true),
            (true, false, true, true, false),
        ] {
            XCTAssertFalse(
                AuralignOpticalMotionPolicy.allowsCoreLensAnimation(
                    isActiveOperation: denied.0,
                    reduceMotion: denied.1,
                    windowIsActive: denied.2,
                    metalIsEnabled: denied.3,
                    captureAllowsMotion: denied.4
                )
            )
        }
    }

    func testOpticalRendererTierHonorsAccessibleFallbacks() {
        XCTAssertEqual(
            AuralignOpticalRenderingPolicy.effectTier(
                reduceTransparency: false,
                increaseContrast: false,
                differentiateWithoutColor: false
            ).rawValue,
            "swiftuiMetal"
        )
        for denied in [
            (true, false, false),
            (false, true, false),
            (false, false, true),
        ] {
            XCTAssertEqual(
                AuralignOpticalRenderingPolicy.effectTier(
                    reduceTransparency: denied.0,
                    increaseContrast: denied.1,
                    differentiateWithoutColor: denied.2
                ).rawValue,
                "static"
            )
        }
        XCTAssertEqual(
            AuralignOpticalRenderingPolicy.resolvedIntensity(
                1,
                windowIsActive: false
            ),
            0.42,
            accuracy: 0.0001
        )
        XCTAssertEqual(AuralignOpticalIntensity.subtleLift, 1.06)
        XCTAssertEqual(
            AuralignOpticalRenderingPolicy.resolvedIntensity(
                0.9,
                windowIsActive: true
            ),
            0.954,
            accuracy: 0.0001
        )
        XCTAssertEqual(
            AuralignOpticalRenderingPolicy.resolvedIntensity(
                0.9,
                windowIsActive: false
            ),
            0.40068,
            accuracy: 0.0001
        )
    }

    func testAlignmentCanvasSanitizesEvidenceBeforeProjection() throws {
        let projection = AlignmentCanvasProjection(
            stages: [
                PipelineStage(
                    id: "asr",
                    title: "Transcribe",
                    status: .active,
                    detail: "/Users/operator/private/source.wav"
                )
            ],
            posture: .running,
            probe: nil,
            privacySafeTransform: { message in
                message.replacingOccurrences(
                    of: "/Users/operator/private/source.wav",
                    with: "the selected media"
                )
            }
        )

        let recognition = try XCTUnwrap(
            projection.segments.first { $0.phase == .recognition }
        )
        XCTAssertEqual(recognition.evidence, ["Transcribe — the selected media"])
        XCTAssertFalse(recognition.evidence.joined().contains("/Users/"))
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
