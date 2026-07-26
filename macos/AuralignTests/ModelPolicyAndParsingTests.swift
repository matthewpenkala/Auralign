// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class ModelPolicyAndParsingTests: XCTestCase {
    private func loadManifest() throws -> ConfigManifest {
        let url = try XCTUnwrap(
            Bundle(for: Self.self).url(forResource: "config_manifest", withExtension: "json")
                ?? Bundle.main.url(forResource: "config_manifest", withExtension: "json")
        )
        return try JSONDecoder().decode(ConfigManifest.self, from: Data(contentsOf: url))
    }

    func testEveryBundledModelCarriesAuthoritativePolicyMetadata() throws {
        let manifest = try loadManifest()
        XCTAssertEqual(manifest.modelManifest.count, 19)
        for entry in manifest.modelManifest {
            XCTAssertNotNil(entry.aliases, entry.id)
            XCTAssertTrue(entry.hasValidFileContract, entry.id)
            XCTAssertFalse(entry.directoryName.isEmpty, entry.id)
            XCTAssertFalse(entry.runtimeFiles.isEmpty, entry.id)
            XCTAssertEqual(
                entry.runtimeFiles,
                entry.requiredFiles + entry.anyRequiredFiles,
                entry.id
            )
            XCTAssertFalse(entry.revision?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.licenseExpression?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.runtimeStatus?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.redistributionStatus?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.reviewStatus?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.reviewedAt?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.productClassification.isEmpty, entry.id)
            XCTAssertFalse(entry.backendCompatibility.isEmpty, entry.id)
            XCTAssertFalse(entry.modificationStatus.isEmpty, entry.id)
            XCTAssertFalse(entry.sourceURL?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.termsURL?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.attribution?.isEmpty ?? true, entry.id)
            XCTAssertEqual(entry.productSelectable, entry.isRuntimeSelectable, entry.id)
            if entry.isRuntimeSelectable {
                XCTAssertTrue(
                    ["production", "optional"].contains(
                        entry.productClassification
                    ),
                    entry.id
                )
                XCTAssertNil(entry.blocker, entry.id)
            } else {
                XCTAssertEqual(entry.productClassification, "blocked", entry.id)
                XCTAssertFalse(entry.blocker?.isEmpty ?? true, entry.id)
                XCTAssertEqual(
                    Set(entry.expectedSHA256.keys),
                    Set(entry.expectedSize.keys),
                    entry.id
                )
                XCTAssertNil(entry.packageBundleId, entry.id)
                XCTAssertNil(entry.packageBundleVersion, entry.id)
                XCTAssertNil(entry.packageRelativePath, entry.id)
                XCTAssertEqual(entry.delivery, "unavailable", entry.id)
            }
        }

        let selectable = manifest.modelManifest.filter(\.isRuntimeSelectable)
        XCTAssertEqual(selectable.count, 19)
        XCTAssertEqual(Set(selectable.map(\.id)).count, 19)
        for entry in selectable {
            XCTAssertEqual(Set(entry.expectedSHA256.keys), Set(entry.runtimeFiles), entry.id)
            XCTAssertEqual(Set(entry.expectedSize.keys), Set(entry.runtimeFiles), entry.id)
            XCTAssertTrue(entry.expectedSize.values.allSatisfy { $0 > 0 }, entry.id)
            if entry.delivery == "system-component" {
                XCTAssertEqual(
                    entry.packageBundleId,
                    EngineLocation.modelBundleIdentifier,
                    entry.id
                )
                XCTAssertEqual(
                    entry.packageBundleVersion,
                    EngineLocation.modelBundleVersion,
                    entry.id
                )
            } else {
                XCTAssertEqual(entry.delivery, "native-download", entry.id)
                XCTAssertTrue(
                    entry.packageBundleId?.hasPrefix(
                        "com.auralign.studio.modelpack."
                    ) == true,
                    entry.id
                )
                XCTAssertEqual(
                    entry.packageBundleVersion,
                    "1.0.0",
                    entry.id
                )
            }
            XCTAssertEqual(
                entry.packageRelativePath,
                "models/\(entry.directoryName)",
                entry.id
            )
        }
    }

    func testModelFileContractRejectsUnsafeOrContradictoryPaths() {
        XCTAssertFalse(
            ModelManifestEntry.isValidFileContract(
                directoryName: "../model",
                requiredFiles: ["config.json"],
                anyRequiredFiles: [],
                runtimeFiles: ["config.json"]
            )
        )
        XCTAssertFalse(
            ModelManifestEntry.isValidFileContract(
                directoryName: "model",
                requiredFiles: ["config.json"],
                anyRequiredFiles: ["config.json"],
                runtimeFiles: ["config.json"]
            )
        )
        XCTAssertFalse(
            ModelManifestEntry.isValidFileContract(
                directoryName: "model",
                requiredFiles: ["../config.json"],
                anyRequiredFiles: [],
                runtimeFiles: ["../config.json"]
            )
        )
        XCTAssertFalse(
            ModelManifestEntry.isValidFileContract(
                directoryName: "model",
                requiredFiles: ["config.json"],
                anyRequiredFiles: ["weights.bin"],
                runtimeFiles: ["weights.bin", "config.json"]
            )
        )
    }

    func testModelSelectionAcceptsReviewedPacksAndRejectsUnknownValues() throws {
        let manifest = try loadManifest()
        let mlx = try XCTUnwrap(
            manifest.modelManifest.first { $0.key == "mlx-tiny" }
        )
        XCTAssertTrue(mlx.isRuntimeSelectable)
        XCTAssertEqual(mlx.policyLabel, "Optional · local pack")

        let reviewed = try XCTUnwrap(
            manifest.modelManifest.first { $0.key == "large-v3" }
        )
        XCTAssertTrue(reviewed.isRuntimeSelectable)
        XCTAssertEqual(reviewed.policyLabel, "Production · local pack")

        let gated = try XCTUnwrap(
            manifest.modelManifest.first { $0.key == "community-1" }
        )
        XCTAssertTrue(gated.isRuntimeSelectable)
        XCTAssertEqual(gated.policyLabel, "Production · bundled")
        XCTAssertTrue(gated.policyExplanation.contains("controlled release-machine"))

        let defaults = manifest.defaultConfiguration()
        XCTAssertEqual(
            Set(manifest.requiredModelEntries(for: defaults).map(\.id)),
            [
                "asr/large-v3",
                "diarization/community-1",
                "alignment/en",
                "speaker-embedding/ecapa",
            ]
        )
        XCTAssertTrue(manifest.modelPolicyBlockers(for: defaults).isEmpty)

        let mlxConfig = defaults
            .setting("asr.backend", to: .string("mlx-whisper"))
            .setting("asr.mlx_model", to: .string("mlx-tiny"))
        XCTAssertTrue(manifest.modelPolicyBlockers(for: mlxConfig).isEmpty)

        let unknownConfig = mlxConfig.setting(
            "asr.mlx_model",
            to: .string("not-in-the-authoritative-manifest")
        )
        XCTAssertEqual(
            manifest.modelPolicyBlockers(for: unknownConfig).count,
            1
        )
    }

    func testModelPickerChoicesExposeEveryReviewedProductionSelection() throws {
        let manifest = try loadManifest()

        XCTAssertEqual(
            manifest.modelChoices(role: "asr").map(\.key),
            ["large-v3", "turbo", "distil-large-v3", "distil-large-v3.5"]
        )
        XCTAssertEqual(
            manifest.modelChoices(role: "asr-mlx").map(\.key),
            [
                "mlx-tiny",
                "mlx-tiny.en",
                "mlx-base",
                "mlx-base.en",
                "mlx-small",
                "mlx-small.en",
                "mlx-medium",
                "mlx-medium.en",
                "mlx-large",
                "mlx-large-v2",
                "mlx-large-v3",
                "mlx-large-v3-turbo",
            ]
        )
        XCTAssertEqual(
            manifest.modelChoices(role: "diarization").map(\.key),
            ["community-1"]
        )
        XCTAssertEqual(manifest.modelChoices(role: "alignment").map(\.key), ["en"])
        XCTAssertEqual(
            manifest.modelChoices(role: "speaker-embedding").map(\.key),
            ["ecapa"]
        )

        let reviewed = try XCTUnwrap(
            manifest.resolveModel(role: "asr-mlx", value: "mlx-large-v3")
        )
        XCTAssertTrue(reviewed.isRuntimeSelectable)
        XCTAssertNil(
            manifest.resolveModel(role: "asr-mlx", value: "not-in-the-manifest")
        )
    }

    func testRuntimeSelectionRecomputesEveryProductAuthorityFailClosed() throws {
        let manifest = try loadManifest()
        let reviewed = try XCTUnwrap(
            manifest.resolveModel(role: "asr", value: "large-v3")
        )
        XCTAssertTrue(reviewed.isRuntimeSelectable)

        var changed = reviewed
        changed.productSelectable = false
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.expectedSHA256.removeValue(forKey: try XCTUnwrap(reviewed.runtimeFiles.first))
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.expectedSHA256["unexpected.bin"] = String(repeating: "0", count: 64)
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.expectedSize.removeValue(forKey: try XCTUnwrap(reviewed.runtimeFiles.first))
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.expectedSize[try XCTUnwrap(reviewed.runtimeFiles.first)] = 0
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.expectedSHA256[try XCTUnwrap(reviewed.runtimeFiles.first)] = "NOT-A-DIGEST"
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.productClassification = "blocked"
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.blocker = "unresolved"
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.backendCompatibility = ""
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.modificationStatus = "unreviewed"
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.packageBundleId = "com.example.untrusted"
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.packageBundleVersion = "floating"
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.packageRelativePath = "models/wrong"
        XCTAssertFalse(changed.isRuntimeSelectable)

        changed = reviewed
        changed.delivery = "unavailable"
        XCTAssertFalse(changed.isRuntimeSelectable)
    }

    func testFloatListParserAcceptsOnlyCompleteFiniteLists() {
        XCTAssertEqual(FloatListParser.parse("0, 0.2, -1.5"), .success([0, 0.2, -1.5]))
        XCTAssertEqual(FloatListParser.parse(" 2.5 "), .success([2.5]))

        assertFailure(FloatListParser.parse(""))
        assertFailure(FloatListParser.parse("0, nope, 0.4"))
        assertFailure(FloatListParser.parse("0,,0.4"))
        assertFailure(FloatListParser.parse("0,"))
        assertFailure(FloatListParser.parse("nan"))
        assertFailure(FloatListParser.parse("inf"))
    }

    private func assertFailure(
        _ result: FloatListParseResult,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        guard case .failure(let message) = result else {
            return XCTFail("Expected parsing to fail.", file: file, line: line)
        }
        XCTAssertFalse(message.isEmpty, file: file, line: line)
    }
}
