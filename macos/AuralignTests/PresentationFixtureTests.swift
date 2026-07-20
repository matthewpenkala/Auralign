// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

#if DEBUG

import XCTest
@testable import Auralign

@MainActor
final class PresentationFixtureTests: XCTestCase {
    private func activatedFixture(_ fixture: PresentationFixture) -> AppModel {
        let model = AppModel.makePresentationFixture(fixture)
        XCTAssertFalse(
            model.hasApplicationOwnership,
            "fixture construction must remain inert until the delegate owns the process lock"
        )
        XCTAssertTrue(model.activateForApplicationOwnership())
        return model
    }

    func testFixtureSelectionRequiresEnvironmentAndNamedArgument() {
        XCTAssertNil(
            PresentationFixture.select(
                environment: [:],
                arguments: ["Auralign", "--ui-fixture", "running"]
            )
        )
        XCTAssertNil(
            PresentationFixture.select(
                environment: ["AURALIGN_ENABLE_UI_TEST_FIXTURES": "1"],
                arguments: ["Auralign"]
            )
        )
        XCTAssertEqual(
            PresentationFixture.select(
                environment: ["AURALIGN_ENABLE_UI_TEST_FIXTURES": "1"],
                arguments: ["Auralign", "--ui-fixture", "running"]
            ),
            .running
        )
    }

    func testAccessibilityOverridesRequireBothGatesAndExactBooleanValues() {
        let valid: [String: String] = [
            PresentationFixture.enableEnvironmentKey: "1",
            "AURALIGN_ENABLE_DEBUG_AUTOMATION": "1",
            PresentationAccessibilityOverrides.reduceTransparencyKey: "0",
            PresentationAccessibilityOverrides.increaseContrastKey: "1",
            PresentationAccessibilityOverrides.reduceMotionKey: "0",
            PresentationAccessibilityOverrides.differentiateWithoutColorKey: "1",
        ]
        XCTAssertEqual(
            PresentationAccessibilityOverrides.authorized(environment: valid),
            PresentationAccessibilityOverrides(
                reduceTransparency: false,
                increaseContrast: true,
                reduceMotion: false,
                differentiateWithoutColor: true
            )
        )

        var missingFixtureGate = valid
        missingFixtureGate.removeValue(
            forKey: PresentationFixture.enableEnvironmentKey
        )
        XCTAssertNil(
            PresentationAccessibilityOverrides.authorized(
                environment: missingFixtureGate
            )
        )
        var malformed = valid
        malformed[
            PresentationAccessibilityOverrides.reduceMotionKey
        ] = "false"
        XCTAssertNil(
            PresentationAccessibilityOverrides.authorized(
                environment: malformed
            )
        )
        XCTAssertNil(
            PresentationAccessibilityOverrides.authorized(environment: [:])
        )
    }

    func testAppModelConstructionIsInertUntilApplicationOwnership() throws {
        let suite = "AuralignOwnershipTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(
            URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .path,
            forKey: EngineLocation.engineRootDefaultsKey
        )

        let model = AppModel(userDefaults: defaults)

        XCTAssertFalse(model.hasApplicationOwnership)
        XCTAssertFalse(model.isConnectingEngine)
        XCTAssertNil(model.bridge)
        XCTAssertNil(model.handshake)
        XCTAssertFalse(model.connectEngine())
    }

    func testFixtureRequestDistinguishesOrdinaryValidAndInvalidLaunches() {
        XCTAssertEqual(
            PresentationFixture.request(
                environment: [:],
                arguments: ["Auralign"]
            ),
            .none
        )
        XCTAssertEqual(
            PresentationFixture.request(
                environment: ["AURALIGN_ENABLE_UI_TEST_FIXTURES": "1"],
                arguments: ["Auralign", "--ui-fixture", "running"]
            ),
            .fixture(.running)
        )
        XCTAssertEqual(
            PresentationFixture.request(
                environment: [:],
                arguments: ["Auralign", "--ui-fixture", "running"]
            ),
            .invalid
        )
        XCTAssertEqual(
            DebugPresentationMode.resolve(
                environment: ["AURALIGN_PROTOTYPE_DIRECTION": "signal-path-workbench"],
                arguments: ["Auralign"]
            ),
            .invalid,
            "retired tournament selectors must never fall through to live startup"
        )
        XCTAssertEqual(
            PresentationFixture.request(
                environment: ["AURALIGN_ENABLE_UI_TEST_FIXTURES": "1"],
                arguments: ["Auralign", "--ui-fixture", "unknown"]
            ),
            .invalid
        )
    }

    func testSettingsSeparatesRepositoryAvailabilityFromBridgeConnection() {
        let connected = AppModel.makePresentationFixture(.settingsValid)
        XCTAssertTrue(connected.enginePresentationStatus.isReady)
        XCTAssertEqual(
            connected.engineConnectionPresentation.state,
            .connected
        )
        XCTAssertTrue(
            connected.engineConnectionPresentation.message.contains(
                "local-transcript-max"
            )
        )

        let invalid = AppModel.makePresentationFixture(.settingsInvalid)
        XCTAssertFalse(invalid.enginePresentationStatus.isReady)
        XCTAssertEqual(
            invalid.engineConnectionPresentation.state,
            .failed
        )

        let unavailable = AppModel.makePresentationFixture(.runtimeUnavailable)
        XCTAssertFalse(unavailable.enginePresentationStatus.isReady)
        XCTAssertEqual(
            unavailable.engineConnectionPresentation.state,
            .failed
        )
    }

    func testFixtureSelectionFailsClosedForUnknownMissingOrDuplicateNames() {
        let enabled = ["AURALIGN_ENABLE_UI_TEST_FIXTURES": "1"]
        XCTAssertNil(
            PresentationFixture.select(
                environment: enabled,
                arguments: ["Auralign", "--ui-fixture", "not-a-fixture"]
            )
        )
        XCTAssertNil(
            PresentationFixture.select(
                environment: enabled,
                arguments: ["Auralign", "--ui-fixture"]
            )
        )
        XCTAssertNil(
            PresentationFixture.select(
                environment: enabled,
                arguments: [
                    "Auralign",
                    "--ui-fixture",
                    "running",
                    "--ui-fixture",
                    "completed",
                ]
            )
        )
    }

    func testRequiredFixtureFamiliesRemainNamed() {
        let names = Set(PresentationFixture.allCases.map(\.rawValue))
        XCTAssertTrue(
            Set([
                "initial",
                "no-input",
                "selected-run-plan",
                "invalid-input",
                "runtime-unavailable",
                "python-incompatible",
                "model-unavailable-gated",
                "readiness-unknown",
                "readiness-checking",
                "readiness-stale",
                "readiness-ready",
                "readiness-warnings",
                "readiness-blocked",
                "invalid-configuration",
                "invalid-hidden-advanced",
                "partial-evidence",
                "running",
                "result-pending-exit",
                "stopping",
                "cancelled",
                "failure",
                "completed",
                "results-rich",
                "results-stale",
                "settings-valid",
                "settings-invalid",
                "settings-locked",
                "resource-failure",
            ]).isSubset(of: names)
        )
    }

    func testEveryFixtureBuildsAnIsolatedSyntheticProductionModel() {
        let privateHome = FileManager.default.homeDirectoryForCurrentUser.path

        for fixture in PresentationFixture.allCases {
            let model = AppModel.makePresentationFixture(fixture)

            XCTAssertTrue(model.isPresentationFixture, fixture.rawValue)
            XCTAssertFalse(model.hasApplicationOwnership, fixture.rawValue)
            XCTAssertTrue(
                model.engineLocation.repositoryRoot.path.hasPrefix(
                    "/AURALIGN_SYNTHETIC/"
                ),
                fixture.rawValue
            )
            XCTAssertFalse(
                model.engineLocation.repositoryRoot.path.hasPrefix(privateHome),
                fixture.rawValue
            )
            if let input = model.inputURL {
                XCTAssertTrue(
                    input.path.hasPrefix("/AURALIGN_SYNTHETIC/"),
                    fixture.rawValue
                )
                XCTAssertFalse(input.path.hasPrefix(privateHome), fixture.rawValue)
            }
            if let output = model.outputRootURL {
                XCTAssertTrue(
                    output.path.hasPrefix("/AURALIGN_SYNTHETIC/"),
                    fixture.rawValue
                )
                XCTAssertFalse(output.path.hasPrefix(privateHome), fixture.rawValue)
            }
            if fixture == .resourceFailure {
                XCTAssertNotNil(model.loadFailure)
            } else {
                XCTAssertNil(model.loadFailure, fixture.rawValue)
            }
        }
    }

    func testFixtureExecutionBoundaryRejectsEveryExternalMutation() async {
        let model = activatedFixture(.selectedRunPlan)
        let originalInput = model.inputURL
        let originalOutput = model.outputRootURL
        let originalLocation = model.engineLocation
        let originalPreset = model.config.selectedPreset
        let originalOverrides = model.config.overrides

        XCTAssertFalse(
            model.selectInput(URL(fileURLWithPath: "/tmp/not-synthetic.wav"))
        )
        XCTAssertFalse(
            model.selectOutputRoot(URL(fileURLWithPath: "/tmp/not-synthetic-output"))
        )
        XCTAssertFalse(model.setWriteDebugReport(true))
        XCTAssertFalse(
            model.updateEngineRoot(URL(fileURLWithPath: "/tmp/not-synthetic-engine"))
        )
        XCTAssertFalse(model.connectEngine())
        XCTAssertFalse(model.resetJob())
        XCTAssertFalse(model.beginNewJob())
        XCTAssertFalse(model.config.selectPreset("fast"))
        XCTAssertFalse(model.config.setOverride("asr.language", to: .string("fr")))
        await model.refreshReadiness()

        XCTAssertEqual(model.inputURL, originalInput)
        XCTAssertEqual(model.outputRootURL, originalOutput)
        XCTAssertEqual(model.engineLocation, originalLocation)
        XCTAssertEqual(model.config.selectedPreset, originalPreset)
        XCTAssertEqual(model.config.overrides, originalOverrides)
        XCTAssertFalse(model.writeDebugReport)
        XCTAssertFalse(model.job.ownsActiveProcess)
    }

    func testMalformedFixtureRequestBuildsOnlyAnInertSyntheticFatalModel() {
        let model = AppModel.makeInvalidPresentationFixtureRequest()

        XCTAssertTrue(model.isPresentationFixture)
        XCTAssertNotNil(model.loadFailure)
        XCTAssertNil(model.bridge)
        XCTAssertNil(model.handshake)
        XCTAssertTrue(
            model.engineLocation.repositoryRoot.path.hasPrefix(
                "/AURALIGN_SYNTHETIC/"
            )
        )
        XCTAssertFalse(model.connectEngine())
        XCTAssertFalse(
            model.updateEngineRoot(URL(fileURLWithPath: "/tmp/real-checkout"))
        )
    }

    func testReadinessFixturesUseTheStrictProductionProjection() {
        let ready = activatedFixture(.readinessReady)
        XCTAssertEqual(ready.readinessDecision.level, .ready)
        XCTAssertEqual(ready.readinessDecision.recovery, .none)
        XCTAssertNil(ready.readinessEvidenceIssue)
        XCTAssertEqual(ready.startBlocker, .noInput)

        let missing = activatedFixture(.modelUnavailableGated)
        XCTAssertEqual(missing.readinessDecision.level, .blocked)
        guard case .showModel = missing.readinessDecision.recovery else {
            return XCTFail("An unresolved selected model must provide direct recovery.")
        }
        XCTAssertNotNil(missing.readinessEvidenceIssue)
        XCTAssertEqual(
            missing.startBlocker,
            .noInput,
            "Runtime readiness must not replace the next New Job input action."
        )

        let partial = activatedFixture(.partialEvidence)
        XCTAssertEqual(partial.readinessDecision.level, .blocked)
        XCTAssertEqual(partial.readinessDecision.recovery, .refresh)
        XCTAssertEqual(partial.doctorEvidenceFailure?.kind, .partial)
        XCTAssertEqual(partial.modelEvidenceFailure?.kind, .malformed)

        for fixture in [
            PresentationFixture.readinessWarnings,
            .readinessBlocked,
        ] {
            let decision = AppModel.makePresentationFixture(fixture).readinessDecision
            XCTAssertFalse(
                decision.cause?.contains("fixture:readiness") ?? false,
                "Summary copy must not expose raw check identifiers."
            )
        }
    }

    func testRichResultsAreCurrentAndStaleResultsRemainStale() {
        let rich = AppModel.makePresentationFixture(.resultsRich)
        XCTAssertFalse(rich.resultsAreStale)
        XCTAssertFalse(rich.resultDrift.sourceChanged)
        XCTAssertFalse(rich.resultDrift.configurationChanged)

        let stale = AppModel.makePresentationFixture(.resultsStale)
        XCTAssertTrue(stale.resultsAreStale)
        XCTAssertFalse(stale.resultDrift.sourceChanged)
        XCTAssertTrue(stale.resultDrift.configurationChanged)
    }

    func testEveryReadinessFixtureHasOneExactDecisionAndRecovery() {
        let expectations: [(
            PresentationFixture,
            AppModel.ReadinessLevel,
            ReadinessDecision.Recovery
        )] = [
            (.runtimeUnavailable, .unavailable, .openSettings),
            (.pythonIncompatible, .unavailable, .openSettings),
            (.readinessUnknown, .unknown, .refresh),
            (.readinessChecking, .checking, .none),
            (.readinessStale, .stale, .refresh),
            (.readinessReady, .ready, .none),
            (
                .readinessWarnings,
                .warnings,
                .showCheck("fixture:readiness")
            ),
            (
                .readinessBlocked,
                .blocked,
                .showCheck("fixture:readiness")
            ),
        ]

        for (fixture, level, recovery) in expectations {
            let model = AppModel.makePresentationFixture(fixture)
            XCTAssertEqual(model.readinessDecision.level, level, fixture.rawValue)
            XCTAssertEqual(model.readinessDecision.recovery, recovery, fixture.rawValue)
            XCTAssertFalse(
                model.readinessDecision.announcementToken.contains(
                    "/AURALIGN_SYNTHETIC/"
                ),
                fixture.rawValue
            )
        }
    }

    func testUnknownAndStaleFixturesRetainNoCurrentModelRows() {
        let unknown = AppModel.makePresentationFixture(.readinessUnknown)
        XCTAssertTrue(unknown.modelStatuses.isEmpty)
        XCTAssertNil(unknown.doctor)

        let stale = AppModel.makePresentationFixture(.readinessStale)
        XCTAssertTrue(stale.modelStatuses.isEmpty)
        XCTAssertNotNil(stale.doctor)
        XCTAssertEqual(stale.readinessDecision.level, .stale)
    }

    func testRuntimeToolbarStatusPresentsRuntimeTruthInsteadOfNewJobInput() {
        for fixture in [
            PresentationFixture.readinessUnknown,
            .readinessStale,
            .readinessWarnings,
            .readinessBlocked,
            .modelUnavailableGated,
            .partialEvidence,
        ] {
            let model = AppModel.makePresentationFixture(fixture)
            let toolbar = PrimaryJobToolbar(model: model)
            XCTAssertEqual(
                toolbar.primaryActionStatus,
                model.readinessDecision.headline,
                fixture.rawValue
            )
            XCTAssertFalse(
                toolbar.primaryActionStatus.contains("Choose an audio or video file"),
                fixture.rawValue
            )
        }
    }

    func testEveryFixtureUsesACompactGlobalStatusLabel() {
        for fixture in PresentationFixture.allCases {
            let model = AppModel.makePresentationFixture(fixture)
            let presentation = PrimaryJobToolbar(model: model)
            XCTAssertFalse(
                presentation.compactStatusLabel.isEmpty,
                fixture.rawValue
            )
            XCTAssertLessThanOrEqual(
                presentation.compactStatusLabel.count,
                20,
                fixture.rawValue
            )
            XCTAssertFalse(
                presentation.statusSymbol.isEmpty,
                fixture.rawValue
            )
            XCTAssertFalse(
                presentation.primaryActionStatus.isEmpty,
                fixture.rawValue
            )
        }
    }

    func testOwnedFixturePhasesRemainLockedWithoutControllingAProcess() {
        for fixture in [
            PresentationFixture.running,
            .resultPendingExit,
            .stopping,
            .settingsLocked,
        ] {
            let model = activatedFixture(fixture)
            XCTAssertTrue(model.workMutationLocked, fixture.rawValue)
            let before = model.job.phase
            model.job.cancel()
            XCTAssertEqual(model.job.phase, before, fixture.rawValue)
            XCTAssertFalse(model.beginNewJob(), fixture.rawValue)
        }
    }

    func testOwnedJobFixturesRetainTheirCapturedSourceAndDestination() {
        for fixture in [
            PresentationFixture.running,
            .resultPendingExit,
            .stopping,
            .settingsLocked,
        ] {
            let model = AppModel.makePresentationFixture(fixture)
            XCTAssertTrue(model.job.ownsActiveProcess, fixture.rawValue)
            XCTAssertNotNil(model.inputURL, fixture.rawValue)
            XCTAssertNotNil(model.inputProbeSnapshot, fixture.rawValue)
            XCTAssertNotNil(model.outputRootURL, fixture.rawValue)
        }
    }
}

#endif
