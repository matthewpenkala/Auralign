// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class ParameterLibraryTests: XCTestCase {
    private func loadManifest() throws -> ConfigManifest {
        let url = try XCTUnwrap(
            Bundle(for: Self.self).url(forResource: "config_manifest", withExtension: "json")
                ?? Bundle.main.url(forResource: "config_manifest", withExtension: "json")
        )
        return try JSONDecoder().decode(ConfigManifest.self, from: Data(contentsOf: url))
    }

    private func loadCatalog() throws -> FieldCatalog {
        let url = try XCTUnwrap(
            Bundle(for: Self.self).url(forResource: "FieldCatalog", withExtension: "json")
                ?? Bundle.main.url(forResource: "FieldCatalog", withExtension: "json")
        )
        return try JSONDecoder().decode(FieldCatalog.self, from: Data(contentsOf: url))
    }

    func testEveryExactDottedKeyFindsExactlyItsOwnField() throws {
        let manifest = try loadManifest()
        let catalog = try loadCatalog()

        for field in manifest.fields {
            let matches = catalog.fields.filter { entry in
                guard let candidate = manifest.field(entry.key) else { return false }
                return ParameterLibraryProjection.matchesSearch(
                    query: field.key,
                    field: candidate,
                    entry: entry,
                    manifest: manifest
                )
            }
            XCTAssertEqual(
                matches.map(\.key),
                [field.key],
                "exact-key search was not unique for \(field.key)"
            )
        }
    }

    func testSearchIncludesHelpEnumChoicesAndReviewedModelAliases() throws {
        let manifest = try loadManifest()
        let catalog = try loadCatalog()

        let enumField = try XCTUnwrap(manifest.fields.first {
            $0.kind == .enumeration && !($0.choices ?? []).isEmpty
        })
        let enumEntry = try XCTUnwrap(catalog.fieldsByKey[enumField.key])
        let enumChoice = try XCTUnwrap(enumField.choices?.first)
        XCTAssertTrue(
            ParameterLibraryProjection.matchesSearch(
                query: enumChoice,
                field: enumField,
                entry: enumEntry,
                manifest: manifest
            )
        )

        let modelField = try XCTUnwrap(manifest.fields.first { $0.modelRole != nil })
        let modelEntry = try XCTUnwrap(catalog.fieldsByKey[modelField.key])
        let role = try XCTUnwrap(modelField.modelRole)
        let reviewedModel = try XCTUnwrap(manifest.modelChoices(role: role).first)
        XCTAssertTrue(
            ParameterLibraryProjection.matchesSearch(
                query: reviewedModel.modelId,
                field: modelField,
                entry: modelEntry,
                manifest: manifest
            )
        )
    }

    func testRequiredFiltersProjectCatalogDispositionAndState() throws {
        let catalog = try loadCatalog()
        XCTAssertEqual(ParameterLibraryFilter.allCases.count, 6)
        XCTAssertEqual(
            catalog.fields.filter {
                ParameterLibraryProjection.matches(
                    filter: .advanced,
                    entry: $0,
                    isModified: false,
                    hasErrors: false
                )
            }.count,
            83
        )
        XCTAssertEqual(
            catalog.fields.filter {
                ParameterLibraryProjection.matches(
                    filter: .readOnly,
                    entry: $0,
                    isModified: false,
                    hasErrors: false
                )
            }.map(\.key),
            ["redecode.write_repaired_transcript"]
        )
        let sample = try XCTUnwrap(catalog.fields.first)
        XCTAssertTrue(
            ParameterLibraryProjection.matches(
                filter: .modified,
                entry: sample,
                isModified: true,
                hasErrors: false
            )
        )
        XCTAssertTrue(
            ParameterLibraryProjection.matches(
                filter: .invalid,
                entry: sample,
                isModified: false,
                hasErrors: true
            )
        )
    }
}
