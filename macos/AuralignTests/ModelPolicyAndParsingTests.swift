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
            XCTAssertFalse(entry.sourceURL?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.termsURL?.isEmpty ?? true, entry.id)
            XCTAssertFalse(entry.attribution?.isEmpty ?? true, entry.id)
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

    func testModelSelectionFailsClosedOnBlockedComplianceStatus() throws {
        let manifest = try loadManifest()
        let blocked = try XCTUnwrap(
            manifest.modelManifest.first { $0.key == "mlx-tiny" }
        )
        XCTAssertFalse(blocked.isRuntimeSelectable)
        XCTAssertEqual(blocked.policyLabel, "Local evaluation only")

        let reviewed = try XCTUnwrap(
            manifest.modelManifest.first { $0.key == "mlx-large-v3" }
        )
        XCTAssertTrue(reviewed.isRuntimeSelectable)
        XCTAssertEqual(reviewed.policyLabel, "Verified")

        let gated = try XCTUnwrap(
            manifest.modelManifest.first { $0.key == "community-1" }
        )
        XCTAssertTrue(gated.isRuntimeSelectable)
        XCTAssertEqual(gated.policyLabel, "Verified · gated terms")

        let defaults = manifest.defaultConfiguration()
        XCTAssertEqual(
            Set(manifest.requiredModelEntries(for: defaults).map(\.id)),
            [
                "asr/large-v3",
                "diarization/community-1",
                "speaker-embedding/ecapa",
            ]
        )
        XCTAssertTrue(manifest.modelPolicyBlockers(for: defaults).isEmpty)

        let blockedConfig = defaults
            .setting("asr.backend", to: .string("mlx-whisper"))
            .setting("asr.mlx_model", to: .string("mlx-tiny"))
        XCTAssertEqual(manifest.modelPolicyBlockers(for: blockedConfig).count, 1)
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
