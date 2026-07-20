// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

@MainActor
final class ConfigStoreTests: XCTestCase {
    private func makeStore() throws -> ConfigStore {
        let bundle = Bundle(for: ManifestCatalogTests.self)
        let manifestURL = try XCTUnwrap(
            bundle.url(forResource: "config_manifest", withExtension: "json")
                ?? Bundle.main.url(forResource: "config_manifest", withExtension: "json")
        )
        let catalogURL = try XCTUnwrap(
            bundle.url(forResource: "FieldCatalog", withExtension: "json")
                ?? Bundle.main.url(forResource: "FieldCatalog", withExtension: "json")
        )
        let manifest = try JSONDecoder().decode(
            ConfigManifest.self, from: Data(contentsOf: manifestURL)
        )
        let catalog = try JSONDecoder().decode(
            FieldCatalog.self, from: Data(contentsOf: catalogURL)
        )
        return ConfigStore(manifest: manifest, catalog: catalog)
    }

    func testEffectiveValueTracksOverrides() throws {
        let store = try makeStore()
        XCTAssertEqual(store.effectiveValue("asr.beam_size"), .int(5))
        XCTAssertFalse(store.isModified("asr.beam_size"))
        store.setOverride("asr.beam_size", to: .int(8))
        XCTAssertEqual(store.effectiveValue("asr.beam_size"), .int(8))
        XCTAssertTrue(store.isModified("asr.beam_size"))
        XCTAssertEqual(store.effectiveConfig.value(at: "asr.beam_size"), .int(8))
        store.clearOverride("asr.beam_size")
        XCTAssertEqual(store.effectiveValue("asr.beam_size"), .int(5))
    }

    func testSettingPresetValueRemovesOverride() throws {
        let store = try makeStore()
        store.setOverride("qa.enabled", to: .bool(false))
        XCTAssertTrue(store.isModified("qa.enabled"))
        store.setOverride("qa.enabled", to: store.presetValue("qa.enabled"))
        XCTAssertFalse(store.isModified("qa.enabled"))
        XCTAssertTrue(store.modifiedKeys.isEmpty)
    }

    func testPresetSwitchKeepsOverridesButChangesBaseline() throws {
        let store = try makeStore()
        XCTAssertTrue(store.selectPreset("max"))
        store.setOverride("asr.language", to: .string("en"))
        XCTAssertTrue(store.selectPreset("fast"))
        XCTAssertEqual(store.effectiveValue("asr.language"), .string("en"))
        XCTAssertEqual(
            store.effectiveConfig.value(at: "preset"),
            .string("fast")
        )
    }

    func testSectionResetClearsOnlyThatSection() throws {
        let store = try makeStore()
        store.setOverride("audio.denoise", to: .string("mild"))
        store.setOverride("asr.beam_size", to: .int(9))
        store.clearSection("audio")
        XCTAssertFalse(store.isModified("audio.denoise"))
        XCTAssertTrue(store.isModified("asr.beam_size"))
    }

    func testImportConfigurationDiffsAgainstBaseline() throws {
        let store = try makeStore()
        var config = store.baseline
        config = config.setting("asr.beam_size", to: .int(12))
        config = config.setting("diarization.num_speakers", to: .int(3))
        store.importConfiguration(config)
        XCTAssertEqual(Set(store.modifiedKeys), ["asr.beam_size", "diarization.num_speakers"])
    }

    func testFingerprintChangesWithEdits() throws {
        let store = try makeStore()
        let before = store.effectiveConfigFingerprint
        store.setOverride("subtitle_export.max_line_width", to: .int(30))
        XCTAssertNotEqual(before, store.effectiveConfigFingerprint)
    }

    func testMutationGuardFailsClosedForEveryPublicConfigurationEdit() throws {
        let store = try makeStore()
        XCTAssertTrue(store.selectPreset("balanced"))
        XCTAssertTrue(store.setOverride("asr.language", to: .string("en")))
        let originalPreset = store.selectedPreset
        let originalOverrides = store.overrides
        store.setMutationGuard { false }

        XCTAssertFalse(store.selectPreset("fast"))
        XCTAssertFalse(store.selectPreset("not-a-preset"))
        XCTAssertFalse(store.setOverride("asr.language", to: .string("fr")))
        XCTAssertFalse(store.clearOverride("asr.language"))
        XCTAssertFalse(store.clearSection("asr"))
        XCTAssertFalse(store.clearAllOverrides())
        XCTAssertFalse(store.importConfiguration(.object([:])))

        XCTAssertEqual(store.selectedPreset, originalPreset)
        XCTAssertEqual(store.overrides, originalOverrides)
    }

    func testManagedValuesOverrideEditsAndImportsAndRemainUnmodified() throws {
        let store = try makeStore()
        XCTAssertTrue(store.setOverride("offline", to: .bool(false)))
        XCTAssertTrue(
            store.setOverride("model_cache", to: .string("/tmp/untrusted-models"))
        )

        store.setManagedValues([
            "offline": .bool(true),
            "model_cache": .string("/managed/models"),
        ])

        XCTAssertEqual(store.effectiveValue("offline"), .bool(true))
        XCTAssertEqual(store.effectiveValue("model_cache"), .string("/managed/models"))
        XCTAssertEqual(store.effectiveConfig.value(at: "offline"), .bool(true))
        XCTAssertEqual(
            store.effectiveConfig.value(at: "model_cache"),
            .string("/managed/models")
        )
        XCTAssertFalse(store.isModified("offline"))
        XCTAssertFalse(store.isModified("model_cache"))
        XCTAssertTrue(store.isManaged("offline"))
        XCTAssertTrue(store.isManaged("model_cache"))
        XCTAssertNil(store.overrides["offline"])
        XCTAssertNil(store.overrides["model_cache"])

        XCTAssertFalse(store.setOverride("offline", to: .bool(false)))
        XCTAssertFalse(store.clearOverride("offline"))
        XCTAssertTrue(
            store.importConfiguration(
                store.baseline
                    .setting("offline", to: .bool(false))
                    .setting("model_cache", to: .string("/tmp/imported"))
                    .setting("asr.language", to: .string("en"))
            )
        )
        XCTAssertEqual(store.effectiveValue("offline"), .bool(true))
        XCTAssertEqual(store.effectiveValue("model_cache"), .string("/managed/models"))
        XCTAssertEqual(store.effectiveValue("asr.language"), .string("en"))
        XCTAssertNil(store.overrides["offline"])
        XCTAssertNil(store.overrides["model_cache"])
    }
}
