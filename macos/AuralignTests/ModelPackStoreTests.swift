// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import CryptoKit
import Darwin
import XCTest
@testable import Auralign

final class ModelPackStoreTests: XCTestCase {
    private let catalogDigest = String(repeating: "a", count: 64)

    func testPackPublishesOnlyAfterExactSealedVerification() async throws {
        let fixture = try makeFixture()
        defer { removeFixture(fixture.root) }
        let store = ModelPackStore(modelsRoot: fixture.root)

        _ = try await store.prepareStaging(
            for: fixture.pack,
            catalogDigest: catalogDigest
        )
        let temporary = try await store.temporaryDownloadURL(
            for: fixture.file,
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        try fixture.data.write(to: temporary)
        try await store.acceptDownloadedFile(
            at: temporary,
            file: fixture.file,
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )

        let installed = try await store.sealAndPublish(
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        let result = await store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: catalogDigest
        )

        XCTAssertTrue(result.isReady, result.problem ?? "")
        let expectedInstalled = try await store.installedPackURL(fixture.pack)
        XCTAssertEqual(installed, expectedInstalled)
        XCTAssertEqual(
            mode(installed),
            0o500
        )
        XCTAssertEqual(
            mode(installed.appendingPathComponent(fixture.file.relativePath)),
            0o400
        )
        XCTAssertTrue(
            FileManager.default.fileExists(
                atPath: installed
                    .appendingPathComponent(ModelPackStore.completionName)
                    .path
            )
        )
        let pythonReferenceSHA256 = [
            "THIRD-PARTY-NOTICES.txt":
                "9350b83ce46ad765517e2bece186a5d791b6e5a56a9e332dc79bb923495ec0d2",
            "licenses/MIT.txt":
                "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
            "model-pack-complete.json":
                "874adcc930c5cbab2ff41ce3b6653f6adbab02e8b2c253b0c414598e4313d787",
            "model-pack-manifest.json":
                "91eaea36334336c3374ec05624f326ba0b321eef98eababd34c959433dc81fa7",
            "model-pack-provenance.json":
                "017e4f64af5bfbabcdbd8e675bbe86a8a8165e68b515f2a1b9aeead88b9767a4",
            "model-pack-verification.json":
                "22d223ca876975f6f51b70ea2188ffef5e8e1b18e71c9f6d923b90c7530ec2cc",
            "model-pack.cdx.json":
                "b454da7e664ea5f7a9b8f7b8cfc8c241a5ae7feba64e5c45c37ace946c26fe08",
        ]
        for (path, expectedSHA256) in pythonReferenceSHA256 {
            let data = try Data(
                contentsOf: installed.appendingPathComponent(path)
            )
            XCTAssertEqual(
                sha256(data),
                expectedSHA256,
                "Swift metadata must byte-match Python for \(path)"
            )
        }
    }

    func testStoreRejectsWrongBytesAndPrefixConfusableTemporaryFile() async throws {
        let fixture = try makeFixture()
        defer { removeFixture(fixture.root) }
        let store = ModelPackStore(modelsRoot: fixture.root)
        _ = try await store.prepareStaging(
            for: fixture.pack,
            catalogDigest: catalogDigest
        )

        let temporary = try await store.temporaryDownloadURL(
            for: fixture.file,
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        try Data("wrong".utf8).write(to: temporary)
        await XCTAssertThrowsErrorAsync {
            try await store.acceptDownloadedFile(
                at: temporary,
                file: fixture.file,
                pack: fixture.pack,
                catalogDigest: catalogDigest
            )
        }

        let externalRoot = fixture.root.deletingLastPathComponent()
            .appendingPathComponent(
                fixture.root.lastPathComponent + ".evil",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: externalRoot,
            withIntermediateDirectories: false
        )
        XCTAssertEqual(chmod(externalRoot.path, 0o700), 0)
        let external = externalRoot.appendingPathComponent(
            ".model.bin.download"
        )
        try fixture.data.write(to: external)
        defer { removeFixture(externalRoot) }
        await XCTAssertThrowsErrorAsync {
            try await store.acceptDownloadedFile(
                at: external,
                file: fixture.file,
                pack: fixture.pack,
                catalogDigest: catalogDigest
            )
        }
        XCTAssertEqual(try Data(contentsOf: external), fixture.data)
    }

    func testLinkedIntermediateAncestorCannotRedirectDestructiveCleanup() async throws {
        let container = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-model-pack-ancestor-\(UUID().uuidString)",
                isDirectory: true
            )
        let namespace = container.appendingPathComponent(
            "namespace",
            isDirectory: true
        )
        let savedNamespace = container.appendingPathComponent(
            "namespace.saved",
            isDirectory: true
        )
        let external = container.appendingPathComponent(
            "external",
            isDirectory: true
        )
        let root = namespace.appendingPathComponent(
            "Models",
            isDirectory: true
        )
        let fixture = try makeFixture(root: root)
        defer {
            var information = stat()
            if lstat(namespace.path, &information) == 0,
               (information.st_mode & S_IFMT) == S_IFLNK {
                _ = Darwin.unlink(namespace.path)
            }
            removeFixture(container)
        }
        try FileManager.default.createDirectory(
            at: container,
            withIntermediateDirectories: false
        )
        XCTAssertEqual(chmod(container.path, 0o700), 0)
        let store = ModelPackStore(modelsRoot: root)
        let staging = try await store.prepareStaging(
            for: fixture.pack,
            catalogDigest: catalogDigest
        )
        try FileManager.default.createDirectory(
            at: external,
            withIntermediateDirectories: false
        )
        XCTAssertEqual(chmod(external.path, 0o700), 0)
        let sentinel = external.appendingPathComponent("do-not-delete.txt")
        let sentinelData = Data("external sentinel".utf8)
        try sentinelData.write(to: sentinel)

        try FileManager.default.moveItem(
            at: namespace,
            to: savedNamespace
        )
        try FileManager.default.createSymbolicLink(
            at: namespace,
            withDestinationURL: external
        )

        await XCTAssertThrowsErrorAsync {
            try await store.discardStaging(
                for: fixture.pack,
                catalogDigest: catalogDigest
            )
        }
        XCTAssertEqual(try Data(contentsOf: sentinel), sentinelData)
        XCTAssertTrue(
            FileManager.default.fileExists(
                atPath: savedNamespace
                    .appendingPathComponent("Models/.staging")
                    .appendingPathComponent(staging.lastPathComponent)
                    .path
            )
        )
    }

    func testWritableIntermediateAncestorIsRejected() async throws {
        let container = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-model-pack-writable-\(UUID().uuidString)",
                isDirectory: true
            )
        defer { removeFixture(container) }
        let insecure = container.appendingPathComponent(
            "insecure",
            isDirectory: true
        )
        try FileManager.default.createDirectory(
            at: insecure,
            withIntermediateDirectories: true
        )
        XCTAssertEqual(chmod(container.path, 0o700), 0)
        XCTAssertEqual(chmod(insecure.path, 0o777), 0)
        let root = insecure.appendingPathComponent(
            "Models",
            isDirectory: true
        )
        let store = ModelPackStore(modelsRoot: root)

        await XCTAssertThrowsErrorAsync {
            try await store.prepareRoots()
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }

    func testDownloadPathSwapDuringHashFailsClosed() async throws {
        let data = Data(repeating: 0x41, count: 64 * 1024 * 1024)
        let fixture = try makeFixture(data: data)
        defer { removeFixture(fixture.root) }
        let store = ModelPackStore(modelsRoot: fixture.root)
        _ = try await store.prepareStaging(
            for: fixture.pack,
            catalogDigest: catalogDigest
        )
        let temporary = try await store.temporaryDownloadURL(
            for: fixture.file,
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        let decoy = temporary.deletingLastPathComponent()
            .appendingPathComponent(".swap-decoy")
        try data.write(to: temporary)
        try Data(repeating: 0x42, count: data.count).write(to: decoy)

        let swap = Task.detached {
            usleep(5_000)
            return temporary.path.withCString { first in
                decoy.path.withCString { second in
                    Darwin.renameatx_np(
                        AT_FDCWD,
                        first,
                        AT_FDCWD,
                        second,
                        UInt32(RENAME_SWAP)
                    )
                }
            }
        }
        await XCTAssertThrowsErrorAsync {
            try await store.acceptDownloadedFile(
                at: temporary,
                file: fixture.file,
                pack: fixture.pack,
                catalogDigest: catalogDigest
            )
        }
        let swapResult = await swap.value
        XCTAssertEqual(swapResult, 0)
        let stagedIsValid = await store.stagedFileIsValid(
            fixture.file,
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        XCTAssertFalse(stagedIsValid)
    }

    func testInstalledPackRejectsCorruptionWrongModeAndUnexpectedEntry() async throws {
        let fixture = try await makePublishedFixture()
        defer { removeFixture(fixture.root) }
        let payload = fixture.installed.appendingPathComponent(
            fixture.file.relativePath
        )

        XCTAssertEqual(chmod(payload.path, 0o600), 0)
        try Data("tampered payload".utf8).write(to: payload)
        XCTAssertEqual(chmod(payload.path, 0o400), 0)
        var result = await fixture.store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: catalogDigest
        )
        XCTAssertFalse(result.isReady)

        XCTAssertEqual(chmod(payload.path, 0o600), 0)
        try fixture.data.write(to: payload)
        result = await fixture.store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: catalogDigest
        )
        XCTAssertFalse(result.isReady, "writable payloads must be rejected")
        XCTAssertEqual(chmod(payload.path, 0o400), 0)

        XCTAssertEqual(chmod(fixture.installed.path, 0o700), 0)
        let unexpected = fixture.installed.appendingPathComponent("unexpected.bin")
        try Data("unexpected".utf8).write(to: unexpected)
        XCTAssertEqual(chmod(unexpected.path, 0o400), 0)
        XCTAssertEqual(chmod(fixture.installed.path, 0o500), 0)
        result = await fixture.store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: catalogDigest
        )
        XCTAssertFalse(result.isReady)
    }

    func testInstalledPackRejectsMissingFileAndSymbolicLinkReplacement() async throws {
        let fixture = try await makePublishedFixture()
        defer { removeFixture(fixture.root) }
        let payload = fixture.installed.appendingPathComponent(
            fixture.file.relativePath
        )
        let payloadParent = payload.deletingLastPathComponent()
        XCTAssertEqual(chmod(payloadParent.path, 0o700), 0)
        try FileManager.default.removeItem(at: payload)
        try FileManager.default.createSymbolicLink(
            at: payload,
            withDestinationURL: URL(fileURLWithPath: "/dev/null")
        )
        XCTAssertEqual(chmod(payloadParent.path, 0o500), 0)

        let result = await fixture.store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: catalogDigest
        )
        XCTAssertFalse(result.isReady)
    }

    func testInstalledPackRejectsHardlinkLikePythonVerifier() async throws {
        let fixture = try await makePublishedFixture()
        defer { removeFixture(fixture.root) }
        let payload = fixture.installed.appendingPathComponent(
            fixture.file.relativePath
        )
        let payloadParent = payload.deletingLastPathComponent()
        let external = fixture.root.deletingLastPathComponent()
            .appendingPathComponent(
                "auralign-model-pack-hardlink-\(UUID().uuidString)"
            )
        try fixture.data.write(to: external)
        XCTAssertEqual(chmod(external.path, 0o400), 0)
        defer {
            _ = chmod(external.path, 0o600)
            try? FileManager.default.removeItem(at: external)
        }

        XCTAssertEqual(chmod(payloadParent.path, 0o700), 0)
        try FileManager.default.removeItem(at: payload)
        XCTAssertEqual(Darwin.link(external.path, payload.path), 0)
        XCTAssertEqual(chmod(payloadParent.path, 0o500), 0)

        let result = await fixture.store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: catalogDigest
        )
        XCTAssertFalse(result.isReady)
        XCTAssertTrue(
            ModelPackStore.verificationChecks.contains("no-hardlinks"),
            "Swift must enforce the same no-hardlinks acceptance contract as Python."
        )
    }

    private func makePublishedFixture() async throws -> PublishedFixture {
        let fixture = try makeFixture()
        let store = ModelPackStore(modelsRoot: fixture.root)
        _ = try await store.prepareStaging(
            for: fixture.pack,
            catalogDigest: catalogDigest
        )
        let temporary = try await store.temporaryDownloadURL(
            for: fixture.file,
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        try fixture.data.write(to: temporary)
        try await store.acceptDownloadedFile(
            at: temporary,
            file: fixture.file,
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        let installed = try await store.sealAndPublish(
            pack: fixture.pack,
            catalogDigest: catalogDigest
        )
        return PublishedFixture(
            root: fixture.root,
            data: fixture.data,
            file: fixture.file,
            pack: fixture.pack,
            store: store,
            installed: installed
        )
    }

    private func makeFixture(
        root requestedRoot: URL? = nil,
        data: Data = Data("exact model bytes".utf8)
    ) throws -> PackFixture {
        let root = requestedRoot
            ?? FileManager.default.temporaryDirectory.appendingPathComponent(
                "auralign-model-pack-store-\(UUID().uuidString)",
                isDirectory: true
            )
        let file = ModelDeliveryFile(
            id: "asr/fixture/model.bin",
            modelKey: "fixture",
            relativePath: "models/fixture/model.bin",
            sourceURL: try XCTUnwrap(
                URL(string: "https://huggingface.co/example/model.bin")
            ),
            expectedSize: Int64(data.count),
            sha256: sha256(data)
        )
        var authority = ModelDeliveryModel(
            key: "fixture",
            role: "asr",
            modelID: "fixture",
            aliases: ["fixture-alias"],
            directoryAliases: [],
            displayName: "Fixture Model",
            description: "Focused exact-byte pack fixture.",
            quality: "Test",
            speed: "Test",
            memory: "Test",
            accessibilityLabel: "Fixture model",
            revision: String(repeating: "a", count: 40),
            delivery: "native-download",
            packID: "com.auralign.studio.modelpack.fixture.v1",
            packVersion: "1.0.0",
            packRelativePath: "models/fixture",
            requiredForPresets: ["max"],
            totalSize: Int64(data.count),
            files: [
                ModelDeliveryFile(
                    id: "model.bin",
                    modelKey: "fixture",
                    relativePath: "model.bin",
                    sourceURL: file.sourceURL,
                    expectedSize: Int64(data.count),
                    sha256: sha256(data)
                ),
            ],
            licenseExpression: "MIT",
            attribution: "Test attribution",
            gated: false,
            redistributionStatus: "permitted_for_test",
            reviewStatus: "verified_for_test",
            reviewedAt: "2026-07-25",
            acquisitionStatus: "approved_exact_pinned_files",
            runtimeStatus: "approved_local",
            productClassification: "production",
            backendCompatibility: "focused test backend",
            modificationStatus: "unmodified focused fixture",
            sourceURL: URL(string: "https://huggingface.co/example")!,
            termsURL: URL(string: "https://huggingface.co/example")!
        )
        authority.packSBOM = authority.expectedPackSBOM
        let pack = ModelDeliveryPack(
            authority: authority,
            contract: testPackContract(),
            id: "com.auralign.studio.modelpack.fixture.v1",
            version: "1.0.0",
            displayName: "Fixture Model",
            description: "Focused exact-byte pack fixture.",
            accessibilityLabel: "Fixture model",
            modelKeys: ["fixture"],
            requiredForOfflineSetup: true,
            requiredForPresets: ["max"],
            quality: "Test",
            speed: "Test",
            memory: "Test",
            licenseExpressions: ["MIT"],
            attributions: ["Test attribution"],
            sourceURLs: ["https://huggingface.co/example"],
            files: [file]
        )
        return PackFixture(root: root, data: data, file: file, pack: pack)
    }

    private func testPackContract() -> ModelPackContract {
        ModelPackContract(
            schemaVersion: "1.0",
            layoutVersion: "1",
            filenames: .init(
                completion: "model-pack-complete.json",
                licensesDirectory: "licenses",
                manifest: "model-pack-manifest.json",
                notices: "THIRD-PARTY-NOTICES.txt",
                provenance: "model-pack-provenance.json",
                sbom: "model-pack.cdx.json",
                verification: "model-pack-verification.json"
            ),
            filesystem: .init(
                directoryMode: "0500",
                fileMode: "0400",
                stagingRootMode: "0700",
                publishedRootMode: "0500",
                publication: "exclusive-rename-then-read-only-root-seal"
            ),
            licenses: [
                "Apache-2.0": .init(
                    filename: "Apache-2.0.txt",
                    sha256:
                        "074e6e32c86a4c0ef8b3ed25b721ca23aca83df277cd88106ef7177c354615ff"
                ),
                "CC-BY-4.0": .init(
                    filename: "CC-BY-4.0.txt",
                    sha256:
                        "d557539df68e771cc1eedcc91d13f70fca930e508d11eedcafa4b15db49e3744"
                ),
                "MIT": .init(
                    filename: "MIT.txt",
                    sha256:
                        "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5"
                ),
            ],
            sbom: .init(bomFormat: "CycloneDX", specVersion: "1.6")
        )
    }

    private func mode(_ url: URL) -> mode_t? {
        var information = stat()
        guard lstat(url.path, &information) == 0 else { return nil }
        return information.st_mode & 0o777
    }

    private func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private func removeFixture(_ root: URL) {
        guard FileManager.default.fileExists(atPath: root.path) else { return }
        _ = chmod(root.path, 0o700)
        if let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        ) {
            let urls = enumerator.compactMap { $0 as? URL }
            for url in urls.reversed() {
                var information = stat()
                guard lstat(url.path, &information) == 0 else { continue }
                if (information.st_mode & S_IFMT) == S_IFDIR {
                    _ = chmod(url.path, 0o700)
                } else if (information.st_mode & S_IFMT) == S_IFREG {
                    _ = chmod(url.path, 0o600)
                }
            }
        }
        try? FileManager.default.removeItem(at: root)
    }
}

private struct PackFixture {
    var root: URL
    var data: Data
    var file: ModelDeliveryFile
    var pack: ModelDeliveryPack
}

private struct PublishedFixture {
    var root: URL
    var data: Data
    var file: ModelDeliveryFile
    var pack: ModelDeliveryPack
    var store: ModelPackStore
    var installed: URL
}

private func XCTAssertThrowsErrorAsync(
    _ expression: () async throws -> Void,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        try await expression()
        XCTFail("Expected expression to throw.", file: file, line: line)
    } catch {
        // Expected.
    }
}
