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
        case stringListEditor
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

    private func loadDeliveryCatalog() throws -> ModelDeliveryCatalog {
        let testBundle = Bundle(for: Self.self)
        if testBundle.url(
            forResource: "model_delivery_catalog",
            withExtension: "json"
        ) != nil {
            return try ModelDeliveryCatalog.loadBundled(bundle: testBundle)
        }
        return try ModelDeliveryCatalog.loadBundled(bundle: .main)
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
        case .stringList:
            return .stringListEditor
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

        XCTAssertEqual(keys.count, 123)
        XCTAssertEqual(Set(keys).count, 123, "manifest field keys must be unique")
        XCTAssertEqual(
            kindCounts,
            [
                "bool": 31,
                "enum": 24,
                "float": 19,
                "float_or_float_list": 1,
                "int": 18,
                "path": 13,
                "path_list": 1,
                "string": 15,
                "string_list": 1,
            ]
        )
        XCTAssertEqual(
            controlCounts,
            [
                "toggle": 31,
                "enumPicker": 24,
                "integerEditor": 18,
                "floatEditor": 19,
                "textEditor": 10,
                "modelPicker": 5,
                "pathPicker": 13,
                "pathListEditor": 1,
                "stringListEditor": 1,
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

        let properNouns = try XCTUnwrap(
            manifest.fields.first { $0.key == "cleanup.proper_nouns" }
        )
        XCTAssertEqual(properNouns.kind, .stringList)
        XCTAssertEqual(properNouns.default, .array([]))
        XCTAssertEqual(properNouns.maxLength, 128)
    }

    func testCatalogExactlyCoversManifest() throws {
        let manifest = try loadManifest()
        let catalog = try loadCatalog()
        let manifestKeys = Set(manifest.fields.map(\.key))
        let catalogKeys = Set(catalog.fields.map(\.key))
        XCTAssertEqual(manifest.fields.count, 123)
        XCTAssertEqual(catalog.fields.count, 123)
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

    func testNormalProductSurfaceHidesOnlyUnsupportedRuntimePaths() throws {
        let manifest = try loadManifest()
        let catalog = try loadCatalog()
        let hidden = catalog.fields.filter { !$0.isProductVisible }

        XCTAssertEqual(
            Set(hidden.map(\.key)),
            [
                "asr.vad_filter",
                "asr.mlx_model_path",
                "cleanup.presentation_scorers.sat.mode",
                "cleanup.presentation_scorers.sat.model_dir",
                "cleanup.presentation_scorers.sat.tokenizer_dir",
                "cleanup.presentation_scorers.sat.signal_weight",
                "cleanup.presentation_scorers.token_classifier.mode",
                "cleanup.presentation_scorers.token_classifier.backend",
            ]
        )
        XCTAssertEqual(catalog.fields.filter(\.isProductVisible).count, 115)
        XCTAssertEqual(
            manifest.field("asr.backend")?.choices,
            ["faster-whisper", "mlx-whisper"]
        )
        XCTAssertEqual(
            manifest.field("cleanup.presentation_scorers.sat.mode")?.choices,
            ["off", "optional", "required"]
        )
        XCTAssertEqual(
            manifest.field("cleanup.presentation_scorers.token_classifier.mode")?.choices,
            ["off"]
        )
        XCTAssertEqual(
            manifest.field("cleanup.presentation_scorers.token_classifier.backend")?.choices,
            ["none"]
        )

        let asrModel = try XCTUnwrap(manifest.field("asr.model"))
        let asrCatalog = try XCTUnwrap(catalog.fieldsByKey["asr.model"])
        let corpus = ParameterLibraryProjection.searchCorpus(
            field: asrModel,
            entry: asrCatalog,
            manifest: manifest
        )
        XCTAssertTrue(corpus.contains("large-v3"))
        XCTAssertTrue(corpus.contains("turbo"))
        XCTAssertTrue(corpus.contains("distil-large-v3"))
    }

    func testEveryPresetHasTruthfulAccessibleProductPresentation() throws {
        let manifest = try loadManifest()
        let catalog = try loadCatalog()
        let preset = try XCTUnwrap(manifest.field("preset"))
        let entry = try XCTUnwrap(catalog.fieldsByKey["preset"])
        let presentations = try XCTUnwrap(entry.choicePresentations)

        XCTAssertEqual(
            Set(presentations.keys),
            Set(preset.choices ?? [])
        )
        let expectedModels = [
            "max": "Whisper Large v3",
            "balanced": "Whisper Large v3 Turbo",
            "fast": "Distil Large v3.5",
            "cpu": "Whisper Large v3 Turbo",
        ]
        for (key, presentation) in presentations {
            XCTAssertFalse(presentation.label.isEmpty, key)
            XCTAssertFalse(presentation.help.isEmpty, key)
            XCTAssertFalse(presentation.accessibilityLabel.isEmpty, key)
            XCTAssertTrue(
                presentation.help.contains(
                    try XCTUnwrap(expectedModels[key])
                ),
                key
            )
        }
    }

    func testBackendAndModelPickersMatchSignedDeliveryCatalog() throws {
        let manifest = try loadManifest()
        let catalog = try loadCatalog()
        let delivery = try loadDeliveryCatalog()

        XCTAssertEqual(
            manifest.field("asr.backend")?.choices,
            ["faster-whisper", "mlx-whisper"]
        )
        XCTAssertTrue(
            try XCTUnwrap(catalog.fieldsByKey["asr.mlx_model"])
                .isProductVisible
        )
        XCTAssertFalse(
            try XCTUnwrap(catalog.fieldsByKey["asr.mlx_model_path"])
                .isProductVisible
        )

        for role in ["asr", "asr-mlx"] {
            let manifestKeys = Set(
                manifest.modelChoices(role: role).map(\.key)
            )
            let deliveryModels = delivery.models.filter { $0.role == role }
            XCTAssertEqual(
                manifestKeys,
                Set(deliveryModels.map(\.key)),
                role
            )
            for model in deliveryModels {
                XCTAssertFalse(model.displayName.isEmpty, model.id)
                XCTAssertFalse(model.description.isEmpty, model.id)
                XCTAssertFalse(model.accessibilityLabel.isEmpty, model.id)
                XCTAssertFalse(model.quality.isEmpty, model.id)
                XCTAssertFalse(model.speed.isEmpty, model.id)
                XCTAssertFalse(model.memory.isEmpty, model.id)
            }
        }
        XCTAssertEqual(
            delivery.models.filter { $0.role == "asr-mlx" }.count,
            12
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
                "primary": 39,
                "advanced": 83,
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
