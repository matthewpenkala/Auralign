// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest

@MainActor
final class AuralignFixtureUITests: XCTestCase {
    private var app: XCUIApplication?

    private func element(
        _ identifier: String,
        in application: XCUIApplication
    ) -> XCUIElement {
        application.descendants(matching: .any)[identifier]
    }

    private func assertSinglePrimaryAction(
        in application: XCUIApplication,
        labelContains text: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let actions = application.descendants(matching: .any).matching(
            identifier: "toolbar.primary-action"
        )
        XCTAssertEqual(actions.count, 1, file: file, line: line)
        XCTAssertTrue(
            actions.firstMatch.label.localizedCaseInsensitiveContains(text),
            file: file,
            line: line
        )
    }

    private func accessibilityText(_ element: XCUIElement) -> String {
        let value = element.value.map { String(describing: $0) } ?? ""
        return "\(element.label) \(value)"
    }

    override func tearDown() {
        app?.terminate()
        app = nil
        super.tearDown()
    }

    func testSelectedRunPlanLaunchesAsClearlySyntheticEvidence() {
        let launched = FixtureApplication.launch("selected-run-plan")
        app = launched

        XCTAssertTrue(launched.windows.firstMatch.waitForExistence(timeout: 3))
        let banner = launched.descendants(matching: .any)[
            "fixture.synthetic-evidence"
        ]
        XCTAssertTrue(banner.exists)
        XCTAssertTrue(
            accessibilityText(banner).contains(
                "Synthetic UI Fixture — Not live engine evidence"
            )
        )
        assertSinglePrimaryAction(in: launched, labelContains: "Start")
    }

    func testResourceFailureIsTheOnlyWorkspaceSurface() {
        let launched = FixtureApplication.launch("resource-failure")
        app = launched

        XCTAssertTrue(
            launched.descendants(matching: .any)[
                "bootstrap.resource-failure"
            ].waitForExistence(timeout: 3)
        )
        XCTAssertFalse(
            launched.descendants(matching: .any)[
                "toolbar.primary-action"
            ].exists
        )
        XCTAssertTrue(launched.buttons["Quit Auralign"].exists)
    }

    func testResultPendingExitKeepsOneCancelAuthority() {
        let launched = FixtureApplication.launch("result-pending-exit")
        app = launched

        let primary = launched.descendants(matching: .any)[
            "toolbar.primary-action"
        ]
        XCTAssertTrue(primary.waitForExistence(timeout: 3))
        XCTAssertTrue(primary.label.contains("Cancel"))
        XCTAssertEqual(
            launched.buttons.matching(
                NSPredicate(format: "label CONTAINS[c] %@", "Cancel")
            ).count,
            1
        )
        XCTAssertTrue(
            launched.staticTexts["Finalizing engine process…"].exists
        )
    }

    func testParameterLibraryExposesAllFieldsAndExactKeySearch() {
        let launched = FixtureApplication.launch("selected-run-plan")
        app = launched

        let destination = element("nav.parameter-library", in: launched)
        XCTAssertTrue(destination.waitForExistence(timeout: 3))
        destination.click()

        let count = element("parameters.count", in: launched)
        XCTAssertTrue(count.waitForExistence(timeout: 3))
        XCTAssertTrue(accessibilityText(count).contains("112 of 112"))

        let search = element("parameters.search", in: launched)
        XCTAssertTrue(search.exists)
        search.click()
        search.typeText("redecode.write_repaired_transcript")
        XCTAssertTrue(
            element(
                "parameters.row.redecode.write_repaired_transcript",
                in: launched
            ).waitForExistence(timeout: 3)
        )
    }

    func testHiddenAdvancedErrorRevealsExactEditorAndReturnPath() {
        let launched = FixtureApplication.launch("invalid-hidden-advanced")
        app = launched

        let returnButton = element(
            "configuration.return-to-origin",
            in: launched
        )
        XCTAssertTrue(returnButton.waitForExistence(timeout: 5))
        returnButton.click()
        let firstIssue = element("validation.first-error", in: launched)
        XCTAssertTrue(firstIssue.waitForExistence(timeout: 5))
        firstIssue.click()

        let controlID = "field.asr.temperature.control"
        let control = element(controlID, in: launched)
        XCTAssertTrue(control.waitForExistence(timeout: 5))
        XCTAssertTrue(
            element("field.asr.temperature.error.engine.0", in: launched).exists
        )
        XCTAssertTrue(element("group.asr.advanced", in: launched).exists)
        XCTAssertTrue(
            element("configuration.return-to-origin", in: launched).exists
        )
        // macOS does not expose `hasFocus` as an XCUIElement property. A
        // non-mutating cursor round trip is the public behavioral proof that
        // the exact editor—not merely its containing row—owns keyboard focus.
        control.typeKey(XCUIKeyboardKey.leftArrow, modifierFlags: [])
        control.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
        XCTAssertEqual(
            control.value as? String,
            "1e",
            "focus verification must not alter the retained invalid draft"
        )
    }

    func testParameterAndRuntimeDestinationsRemainKeyboardReachable() {
        let launched = FixtureApplication.launch("results-rich")
        app = launched

        let newJob = element("nav.new-job", in: launched)
        XCTAssertTrue(newJob.waitForExistence(timeout: 3))
        newJob.click()
        XCTAssertTrue(element("job.run-plan", in: launched).waitForExistence(timeout: 3))

        launched.typeKey("r", modifierFlags: [.command, .shift])
        XCTAssertTrue(
            element("results.transcript", in: launched)
                .waitForExistence(timeout: 3),
            "Shift-Command-R must route to the existing Results surface"
        )

        let runtime = element("nav.runtime-models", in: launched)
        runtime.click()
        XCTAssertTrue(
            element("readiness.summary", in: launched)
                .waitForExistence(timeout: 3)
        )
    }

    func testSettingsLockedStateExplainsDisabledRepositoryMutation() {
        let launched = FixtureApplication.launch("settings-locked")
        app = launched

        launched.typeKey(",", modifierFlags: .command)
        XCTAssertTrue(
            element("settings.engine.lock-reason", in: launched)
                .waitForExistence(timeout: 5)
        )
        let choose = element("settings.engine.choose", in: launched)
        XCTAssertTrue(choose.exists)
        XCTAssertFalse(choose.isEnabled)
        XCTAssertTrue(element("settings.engine.repository", in: launched).exists)
        XCTAssertTrue(element("settings.engine.connection", in: launched).exists)
    }

    func testSettingsValidAndInvalidStatesRemainDistinct() {
        var launched = FixtureApplication.launch("settings-valid")
        app = launched
        launched.typeKey(",", modifierFlags: .command)
        let validConnection = element("settings.engine.connection", in: launched)
        XCTAssertTrue(validConnection.waitForExistence(timeout: 5))
        XCTAssertTrue(
            accessibilityText(validConnection)
                .localizedCaseInsensitiveContains("local-transcript-max")
        )

        launched.terminate()
        launched = FixtureApplication.launch("settings-invalid")
        app = launched
        launched.typeKey(",", modifierFlags: .command)
        let invalidEnvironment = element(
            "settings.engine.repository-environment",
            in: launched
        )
        XCTAssertTrue(invalidEnvironment.waitForExistence(timeout: 5))
        XCTAssertTrue(
            accessibilityText(invalidEnvironment)
                .localizedCaseInsensitiveContains("missing")
        )
    }

    func testRunningAndStoppingExposeOneCancelAuthority() {
        var launched = FixtureApplication.launch("running")
        app = launched
        assertSinglePrimaryAction(in: launched, labelContains: "Cancel")
        XCTAssertTrue(element("activity.stages", in: launched).exists)
        XCTAssertTrue(element("activity.summary", in: launched).exists)
        let asrStage = element("activity.stage.asr", in: launched)
        XCTAssertTrue(asrStage.exists)
        XCTAssertTrue(
            accessibilityText(asrStage)
                .localizedCaseInsensitiveContains("active")
        )

        launched.terminate()
        launched = FixtureApplication.launch("stopping")
        app = launched
        assertSinglePrimaryAction(in: launched, labelContains: "Cancel")
        XCTAssertTrue(launched.staticTexts["Stopping…"].waitForExistence(timeout: 3))
    }

    func testCancelledFailedAndCompletedRemainDistinct() {
        var launched = FixtureApplication.launch("cancelled")
        app = launched
        XCTAssertTrue(launched.staticTexts["Cancelled"].waitForExistence(timeout: 3))
        XCTAssertTrue(
            element("activity.partial-reveal", in: launched)
                .waitForExistence(timeout: 3)
        )

        launched.terminate()
        launched = FixtureApplication.launch("failure")
        app = launched
        XCTAssertTrue(launched.staticTexts["Job failed"].waitForExistence(timeout: 3))
        XCTAssertTrue(element("activity.failure", in: launched).exists)

        launched.terminate()
        launched = FixtureApplication.launch("completed")
        app = launched
        XCTAssertTrue(launched.staticTexts["Completed"].waitForExistence(timeout: 3))
        XCTAssertTrue(
            element("activity.view-results", in: launched)
                .waitForExistence(timeout: 3)
        )
    }

    func testStaleResultsExposeTranscriptArtifactsAndFileActions() {
        let launched = FixtureApplication.launch("results-stale")
        app = launched

        XCTAssertTrue(element("results.stale", in: launched).waitForExistence(timeout: 3))
        XCTAssertTrue(
            element("results.transcript", in: launched)
                .waitForExistence(timeout: 3)
        )
        let compactDetails = element("results.compact-details", in: launched)
        if compactDetails.waitForExistence(timeout: 1) {
            compactDetails.click()
            // A compact Results layout can place the disclosure below the
            // visible transcript. Right Arrow is idempotent when it is
            // already expanded and expands it when the initial click only
            // brought the control on screen.
            compactDetails.typeKey(.rightArrow, modifierFlags: [])
        }
        let completedScroll = element("results.root", in: launched)
        XCTAssertTrue(completedScroll.waitForExistence(timeout: 3))
        let qa = element("results.qa", in: launched)
        var qaScrollAttempts = 0
        while !qa.exists && qaScrollAttempts < 6 {
            completedScroll.swipeUp()
            qaScrollAttempts += 1
        }
        XCTAssertTrue(
            qa.waitForExistence(timeout: 3)
        )
        for index in 0..<3 {
            let row = element("results.artifact.\(index)", in: launched)
            let actions = element(
                "results.artifact.\(index).actions",
                in: launched
            )
            var scrollAttempts = 0
            while !row.exists && scrollAttempts < 4 {
                completedScroll.swipeUp()
                scrollAttempts += 1
            }
            XCTAssertTrue(row.waitForExistence(timeout: 3))
            XCTAssertTrue(
                actions.waitForExistence(timeout: 3)
            )
        }
    }
}
