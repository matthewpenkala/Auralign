// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class ModelDeliveryCatalogTests: XCTestCase {
    private func loadCatalog() throws -> ModelDeliveryCatalog {
        let testBundle = Bundle(for: Self.self)
        if testBundle.url(
            forResource: "model_delivery_catalog",
            withExtension: "json"
        ) != nil {
            return try ModelDeliveryCatalog.loadBundled(bundle: testBundle)
        }
        return try ModelDeliveryCatalog.loadBundled(bundle: .main)
    }

    func testBundledCatalogBindsExactDeliveryContract() throws {
        let catalog = try loadCatalog()

        XCTAssertEqual(
            catalog.digest,
            "649ecb638a1168922898360687b3a43990b9511d452f1e3474d8e1bf2e79ac79"
        )
        XCTAssertEqual(
            catalog.schema,
            ModelDeliveryCatalog.schemaVersion
        )
        XCTAssertEqual(catalog.productVersion, "0.3.0")
        XCTAssertEqual(catalog.models.count, 19)
        XCTAssertEqual(catalog.packs.count, 16)
        XCTAssertEqual(
            Set(catalog.bootstrap.modelKeys),
            ["community-1", "en", "ecapa"]
        )
        XCTAssertEqual(
            catalog.normalPresets,
            [
                "max": "large-v3",
                "balanced": "turbo",
                "fast": "distil-large-v3.5",
                "cpu": "turbo",
            ]
        )
        XCTAssertEqual(
            Set(catalog.requiredPacks.flatMap(\.requiredForPresets)),
            ["max", "balanced", "fast", "cpu"]
        )
        XCTAssertEqual(catalog.requiredPacks.count, 3)
        XCTAssertTrue(
            catalog.models
                .filter { $0.delivery == "native-download" }
                .allSatisfy {
                    $0.files.allSatisfy {
                        $0.sourceURL?.scheme == "https"
                            && $0.sourceURL?.host == "huggingface.co"
                    }
                }
        )
    }

    func testCatalogRejectsPathTraversalWrongHostFloatingRevisionAndDigest() throws {
        let catalog = try loadCatalog()

        var traversal = catalog
        traversal.models[0].files[0].relativePath = "../escape.bin"
        XCTAssertThrowsError(try traversal.validate())

        var wrongHost = catalog
        wrongHost.models[0].files[0].sourceURL = try XCTUnwrap(
            URL(string: "https://example.invalid/model.bin")
        )
        XCTAssertThrowsError(try wrongHost.validate())

        var floatingRevision = catalog
        floatingRevision.models[0].revision = "main"
        XCTAssertThrowsError(try floatingRevision.validate())

        var wrongDigest = catalog
        wrongDigest.models[0].files[0].sha256 = String(repeating: "0", count: 64)
        XCTAssertThrowsError(try wrongDigest.validate())
    }

    func testCatalogRejectsPresetAndBootstrapDrift() throws {
        let catalog = try loadCatalog()

        var wrongPreset = catalog
        wrongPreset.normalPresets["max"] = "community-1"
        XCTAssertThrowsError(try wrongPreset.validate())

        var missingPresetBinding = catalog
        let largeV3Index = try XCTUnwrap(
            missingPresetBinding.models.firstIndex(where: { $0.key == "large-v3" })
        )
        missingPresetBinding.models[largeV3Index].requiredForPresets = []
        XCTAssertThrowsError(try missingPresetBinding.validate())

        var wrongBootstrap = catalog
        wrongBootstrap.bootstrap.modelKeys = ["community-1", "en", "large-v3"]
        XCTAssertThrowsError(try wrongBootstrap.validate())
    }

    func testRedirectPolicyAllowsOnlyExactSignedEndpoints() throws {
        let catalog = try loadCatalog()

        for endpoint in catalog.redirectHostSuffixes {
            XCTAssertTrue(
                ModelDeliveryCatalog.isAllowedRedirectHost(
                    endpoint,
                    suffixes: catalog.redirectHostSuffixes
                ),
                endpoint
            )
        }

        for unlisted in [
            "models.huggingface.co",
            "evil.huggingface.co",
            "subdomain.us.aws.cdn.hf.co",
            "subdomain.us.gcp.cdn.hf.co",
            "subdomain.transfer.xethub.hf.co",
            "unreviewed.hf.co",
            "hf.co.example.invalid",
            "example.invalid",
        ] {
            XCTAssertFalse(
                ModelDeliveryCatalog.isAllowedRedirectHost(
                    unlisted,
                    suffixes: catalog.redirectHostSuffixes
                ),
                unlisted
            )
        }
    }
}
