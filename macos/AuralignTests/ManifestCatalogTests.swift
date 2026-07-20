// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

/// Swift-side coverage/drift checks over the bundled engine manifest and the
/// curated catalog. The Python test suite independently verifies both files
/// against the live Pydantic models.
final class ManifestCatalogTests: XCTestCase {
    private enum ControlProjection: String {
        case toggle
        case enumPicker
        case integerEditor
        case floatEditor
        case textEditor
        case modelPicker
        case pathPicker
        case pathListEditor
        case numericSequenceEditor
    }

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

    private func controlProjection(for field: ConfigField) -> ControlProjection {
        switch field.kind {
        case .bool:
            return .toggle
        case .enumeration:
            return .enumPicker
        case .int:
            return .integerEditor
        case .float:
            return .floatEditor
        case .string:
            return field.modelRole == nil ? .textEditor : .modelPicker
        case .path:
            return .pathPicker
        case .pathList:
            return .pathListEditor
        case .floatList, .floatOrFloatList:
            return .numericSequenceEditor
        }
    }

    func testManifestDecodesWithKnownKindsOnly() throws {
        let manifest = try loadManifest()
        XCTAssertEqual(manifest.schemaVersion, "gui_config_manifest.v1")
        XCTAssertFalse(manifest.fields.isEmpty)
        XCTAssertFalse(manifest.modelManifest.isEmpty)
    }

    func testManifestHasExactShippedFieldShapeAndControlProjection() throws {
        let manifest = try loadManifest()
        let keys = manifest.fields.map(\.key)
        let kindCounts = Dictionary(
            grouping: manifest.fields,
            by: { $0.kind.rawValue }
        ).mapValues { $0.count }
        let controlCounts = Dictionary(
            grouping: manifest.fields,
            by: { controlProjection(for: $0).rawValue }
        ).mapValues { $0.count }

        XCTAssertEqual(keys.count, 112)
        XCTAssertEqual(Set(keys).count, 112, "manifest field keys must be unique")
        XCTAssertEqual(
            kindCounts,
            [
                "bool": 30,
                "enum": 19,
                "float": 17,
                "float_or_float_list": 1,
                "int": 18,
                "path": 11,
                "path_list": 1,
                "string": 15,
            ]
        )
        XCTAssertEqual(
            controlCounts,
            [
                "toggle": 30,
                "enumPicker": 19,
                "integerEditor": 18,
                "floatEditor": 17,
                "textEditor": 10,
                "modelPicker": 5,
                "pathPicker": 11,
                "pathListEditor": 1,
                "numericSequenceEditor": 1,
            ],
            "every shipped field kind must project to the expected native editor family"
        )
        XCTAssertEqual(
            Set(manifest.fields.compactMap { field in
                field.modelRole == nil ? nil : field.key
            }),
            [
                "asr.model",
                "asr.mlx_model",
                "diarization.model",
                "alignment.model",
                "speaker_id.model",
            ]
        )
    }

    func testCatalogExactlyCoversManifest() throws {
        let manifest = try loadManifest()
        let catalog = try loadCatalog()
        let manifestKeys = Set(manifest.fields.map(\.key))
        let catalogKeys = Set(catalog.fields.map(\.key))
        XCTAssertEqual(manifest.fields.count, 112)
        XCTAssertEqual(catalog.fields.count, 112)
        XCTAssertEqual(manifestKeys.count, manifest.fields.count, "duplicate manifest key")
        XCTAssertEqual(catalogKeys.count, catalog.fields.count, "duplicate catalog key")
        XCTAssertEqual(
            manifestKeys.subtracting(catalogKeys),
            [],
            "engine fields missing from the catalog"
        )
        XCTAssertEqual(
            catalogKeys.subtracting(manifestKeys),
            [],
            "catalog names unknown engine fields"
        )
    }

    func testCatalogHasExactSectionsDispositionsAndReadonlyField() throws {
        let catalog = try loadCatalog()
        let dispositions = Dictionary(
            grouping: catalog.fields,
            by: { $0.disposition.rawValue }
        ).mapValues { $0.count }
        let sectionIDs = catalog.sections.map(\.id)

        XCTAssertEqual(catalog.catalogVersion, "gui_field_catalog.v1")
        XCTAssertEqual(
            sectionIDs,
            [
                "general",
                "audio",
                "asr",
                "diarization",
                "alignment",
                "qa_redecode",
                "speakers",
                "identity",
                "cleanup",
                "subtitles",
            ]
        )
        XCTAssertEqual(Set(sectionIDs).count, 10, "catalog section IDs must be unique")
        XCTAssertEqual(
            dispositions,
            [
                "primary": 35,
                "advanced": 76,
                "readonly": 1,
            ]
        )
        XCTAssertEqual(
            catalog.fields.filter { $0.disposition == .readonly }.map(\.key),
            ["redecode.write_repaired_transcript"]
        )
        for sectionID in sectionIDs {
            XCTAssertFalse(catalog.entries(inSection: sectionID).isEmpty, sectionID)
        }
    }

    func testCatalogSectionsAndRequirementsResolve() throws {
        let catalog = try loadCatalog()
        let sectionIDs = Set(catalog.sections.map(\.id))
        let keys = Set(catalog.fields.map(\.key))
        for entry in catalog.fields {
            XCTAssertTrue(sectionIDs.contains(entry.section), entry.key)
            for requirement in entry.requires ?? [] {
                XCTAssertTrue(keys.contains(requirement.key), entry.key)
            }
        }
    }

    func testEnumFieldsDeclareChoices() throws {
        let manifest = try loadManifest()
        for field in manifest.fields where field.kind == .enumeration {
            XCTAssertFalse(field.choices?.isEmpty ?? true, field.key)
        }
    }

    func testDefaultConfigurationContainsEveryField() throws {
        let manifest = try loadManifest()
        let defaults = manifest.defaultConfiguration()
        for field in manifest.fields {
            XCTAssertNotNil(defaults.value(at: field.key), field.key)
        }
    }
}
