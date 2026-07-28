// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest

@MainActor
enum FixtureApplication {
    static func launch(
        _ fixture: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.terminate()
        app.launchEnvironment["AURALIGN_ENABLE_UI_TEST_FIXTURES"] = "1"
        app.launchArguments += [
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_US",
            "--ui-fixture", fixture,
        ]
        app.launch()

        let mainWindow = app.windows.matching(
            identifier: "com.auralign.studio.window.main"
        ).firstMatch
        let banner = mainWindow.descendants(matching: .any).matching(
            identifier: "fixture.synthetic-evidence"
        ).firstMatch
        XCTAssertTrue(
            banner.waitForExistence(timeout: 5),
            "The dual-gated synthetic evidence banner did not appear.",
            file: file,
            line: line
        )
        return app
    }
}
