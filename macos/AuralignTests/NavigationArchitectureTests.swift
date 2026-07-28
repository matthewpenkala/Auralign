// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

@MainActor
final class NavigationArchitectureTests: XCTestCase {
    func testTaskGroupsCoverEveryCatalogSectionExactlyOnce() throws {
        let url = try XCTUnwrap(
            Bundle(for: Self.self).url(forResource: "FieldCatalog", withExtension: "json")
                ?? Bundle.main.url(forResource: "FieldCatalog", withExtension: "json")
        )
        let catalog = try JSONDecoder().decode(
            FieldCatalog.self,
            from: Data(contentsOf: url)
        )
        let grouped = PipelineNavigationGroup.allCases.flatMap(\.sectionIDs)

        XCTAssertEqual(grouped.count, Set(grouped).count, "a section appears in two groups")
        XCTAssertEqual(Set(grouped), Set(catalog.sections.map(\.id)))
    }

    func testNavigationGroupsHaveVisibleTextAndSymbols() {
        for group in PipelineNavigationGroup.allCases {
            XCTAssertFalse(group.title.isEmpty)
            XCTAssertFalse(group.symbol.isEmpty)
            XCTAssertFalse(group.sectionIDs.isEmpty)
        }
    }

    func testExpertWideLayoutFitsItsDeclaredMinimumColumns() {
        let requiredWidth: CGFloat = 210 + 1 + 420 + 1 + 270
        XCTAssertGreaterThanOrEqual(
            AuralignBreakpoint.parameterLibrary,
            requiredWidth + 38
        )
    }

    func testAccessibilityIdentifierFamiliesAreStableAndStructural() {
        XCTAssertEqual(
            AccessibilityIdentifier.fieldControl("qa.enabled"),
            "field.qa.enabled.control"
        )
        XCTAssertEqual(
            AccessibilityIdentifier.readinessModel(role: "asr", key: "large-v3"),
            "readiness.model.asr.large-v3"
        )
        XCTAssertEqual(
            AccessibilityIdentifier.artifact(index: 4),
            "results.artifact.4"
        )
    }

    func testHiddenAdvancedErrorNavigationPreservesExactTargetContract() throws {
        let model = AppModel(
            userDefaults: try XCTUnwrap(
                UserDefaults(suiteName: "AuralignNavigationTests.\(UUID().uuidString)")
            )
        )
        let error = ConfigFieldError(
            location: ["asr", "temperature"],
            message: "Synthetic invalid draft retained"
        )
        model.config.applyPresentationFixture(fieldErrors: [error])

        XCTAssertTrue(model.navigateToFirstConfigurationError())
        XCTAssertEqual(model.selectedSection, .configuration("asr"))
        let request = try XCTUnwrap(model.fieldNavigationRequest)
        XCTAssertEqual(request.sectionID, "asr")
        XCTAssertEqual(request.fieldKey, "asr.temperature")
        XCTAssertEqual(request.message, error.message)
        XCTAssertTrue(request.revealAdvanced)
        XCTAssertEqual(request.returnSection, .newJob)
        XCTAssertNil(model.consumedFieldNavigationRequestID)
        model.consumeFieldNavigationRequest(request.id)
        XCTAssertEqual(model.consumedFieldNavigationRequestID, request.id)
        XCTAssertEqual(
            model.keyboardFocusedConfigurationControlIdentifier,
            AccessibilityIdentifier.fieldControl(request.fieldKey)
        )
        XCTAssertTrue(model.returnFromConfigurationError())
        XCTAssertEqual(model.selectedSection, .newJob)
        XCTAssertNil(model.fieldNavigationRequest)
        XCTAssertNil(model.keyboardFocusedConfigurationControlIdentifier)
    }

    func testSectionLevelErrorDoesNotInventAnExactField() throws {
        let model = AppModel(
            userDefaults: try XCTUnwrap(
                UserDefaults(suiteName: "AuralignNavigationTests.\(UUID().uuidString)")
            )
        )
        let error = ConfigFieldError(
            location: ["diarization"],
            message: "Synthetic cross-field constraint"
        )

        XCTAssertTrue(model.navigateToConfigurationError(error))
        XCTAssertEqual(model.selectedSection, .configuration("diarization"))
        XCTAssertNil(model.fieldNavigationRequest)
        let request = try XCTUnwrap(model.sectionNavigationRequest)
        XCTAssertEqual(request.sectionID, "diarization")
        XCTAssertEqual(request.message, error.message)
        XCTAssertEqual(request.returnSection, .newJob)
        model.consumeSectionNavigationRequest(request.id)
        XCTAssertEqual(model.consumedSectionNavigationRequestID, request.id)
        XCTAssertTrue(model.returnFromConfigurationError())
        XCTAssertEqual(model.selectedSection, .newJob)
        XCTAssertNil(model.sectionNavigationRequest)
    }
}
