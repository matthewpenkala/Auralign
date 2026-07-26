// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import CryptoKit
import Foundation
import XCTest
@testable import Auralign

final class ModelDownloadServiceTests: XCTestCase {
    func testServiceResumesThenAcceptsOnlyAfterFinalPackVerification() async throws {
        let fixture = try makeCatalogFixture()
        defer { removeFixture(fixture.root) }
        let store = ModelPackStore(modelsRoot: fixture.root)
        let transport = FakeModelTransport(
            bytes: fixture.data,
            pauseFirstAttempt: true
        )
        let service = ModelDownloadService(
            store: store,
            transport: transport
        )
        let events = ModelEventRecorder()

        do {
            try await service.install(
                pack: fixture.pack,
                catalog: fixture.catalog,
                onEvent: { event in events.record(event) }
            )
            XCTFail("The first synthetic transfer should pause.")
        } catch let error as ModelTransferError {
            guard case .paused = error else {
                return XCTFail("Expected paused, got \(error)")
            }
        }
        XCTAssertFalse(events.values.contains("accepted"))

        try await service.install(
            pack: fixture.pack,
            catalog: fixture.catalog,
            onEvent: { event in events.record(event) }
        )

        XCTAssertTrue(transport.receivedResumeDataOnSecondAttempt)
        XCTAssertEqual(events.values.last, "accepted")
        let result = await store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: fixture.catalog.digest
        )
        XCTAssertTrue(result.isReady, result.problem ?? "")
        let journalData = await store.readJournalData()
        XCTAssertNil(journalData)
    }

    func testServiceRejectsMismatchedTransferWithoutPublishing() async throws {
        let fixture = try makeCatalogFixture()
        defer { removeFixture(fixture.root) }
        let store = ModelPackStore(modelsRoot: fixture.root)
        let transport = FakeModelTransport(
            bytes: Data("mismatched bytes".utf8),
            pauseFirstAttempt: false
        )
        let service = ModelDownloadService(
            store: store,
            transport: transport
        )
        let events = ModelEventRecorder()

        do {
            try await service.install(
                pack: fixture.pack,
                catalog: fixture.catalog,
                onEvent: { event in events.record(event) }
            )
            XCTFail("Mismatched bytes must fail exact verification.")
        } catch {
            XCTAssertFalse(events.values.contains("accepted"))
        }

        let result = await store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: fixture.catalog.digest
        )
        XCTAssertFalse(result.isReady)
        XCTAssertEqual(result.problem, "not-installed")
    }

    func testServiceRejectsCatalogMutationBeforeNetworkTransfer() async throws {
        let fixture = try makeCatalogFixture()
        defer { removeFixture(fixture.root) }
        var mutated = fixture.catalog
        mutated.normalPresets["max"] = "bootstrap-0"
        let transport = FakeModelTransport(
            bytes: fixture.data,
            pauseFirstAttempt: false
        )
        let service = ModelDownloadService(
            store: ModelPackStore(modelsRoot: fixture.root),
            transport: transport
        )

        do {
            try await service.install(
                pack: fixture.pack,
                catalog: mutated,
                onEvent: { _ in }
            )
            XCTFail("A mutated signed-catalog projection must fail closed.")
        } catch {
            XCTAssertEqual(transport.attemptCount, 0)
        }
    }

    func testServiceRepairsOnlyAnInvalidSameVersionPack() async throws {
        let fixture = try makeCatalogFixture()
        defer { removeFixture(fixture.root) }
        let store = ModelPackStore(modelsRoot: fixture.root)
        let firstTransport = FakeModelTransport(
            bytes: fixture.data,
            pauseFirstAttempt: false
        )
        let firstService = ModelDownloadService(
            store: store,
            transport: firstTransport
        )
        try await firstService.install(
            pack: fixture.pack,
            catalog: fixture.catalog,
            onEvent: { _ in }
        )

        let installed = try await store.installedPackURL(fixture.pack)
        let payload = installed.appendingPathComponent(
            fixture.pack.files[0].relativePath
        )
        XCTAssertEqual(chmod(payload.path, 0o600), 0)

        let repairTransport = FakeModelTransport(
            bytes: fixture.data,
            pauseFirstAttempt: false
        )
        let repairService = ModelDownloadService(
            store: store,
            transport: repairTransport
        )
        let events = ModelEventRecorder()
        try await repairService.install(
            pack: fixture.pack,
            catalog: fixture.catalog,
            onEvent: { event in events.record(event) }
        )

        XCTAssertEqual(repairTransport.attemptCount, 1)
        XCTAssertEqual(events.values.last, "accepted")
        let repaired = await store.verifyInstalledPack(
            fixture.pack,
            catalogDigest: fixture.catalog.digest
        )
        XCTAssertTrue(repaired.isReady, repaired.problem ?? "")
    }

    func testServiceReusesAValidSameVersionPackWithoutTransfer() async throws {
        let fixture = try makeCatalogFixture()
        defer { removeFixture(fixture.root) }
        let store = ModelPackStore(modelsRoot: fixture.root)
        let firstTransport = FakeModelTransport(
            bytes: fixture.data,
            pauseFirstAttempt: false
        )
        let service = ModelDownloadService(
            store: store,
            transport: firstTransport
        )
        try await service.install(
            pack: fixture.pack,
            catalog: fixture.catalog,
            onEvent: { _ in }
        )

        let noTransfer = FakeModelTransport(
            bytes: fixture.data,
            pauseFirstAttempt: false
        )
        let restoringService = ModelDownloadService(
            store: store,
            transport: noTransfer
        )
        let events = ModelEventRecorder()
        try await restoringService.install(
            pack: fixture.pack,
            catalog: fixture.catalog,
            onEvent: { event in events.record(event) }
        )

        XCTAssertEqual(noTransfer.attemptCount, 0)
        XCTAssertEqual(events.values, ["preparing", "accepted"])
    }

    private func makeCatalogFixture() throws -> CatalogFixture {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-model-download-service-\(UUID().uuidString)",
                isDirectory: true
            )
        let data = Data("exact native model download bytes".utf8)
        let digest = sha256(data)
        let revision = String(repeating: "a", count: 40)
        let bootstrapKeys = (0..<3).map { "bootstrap-\($0)" }
        var models: [ModelDeliveryModel] = []

        for key in bootstrapKeys {
            models.append(
                model(
                    key: key,
                    delivery: "system-component",
                    packID: "com.auralign.studio.models.bootstrap.v1",
                    packVersion: "1.0.0",
                    packRelativePath: "models/\(key)",
                    requiredForPresets: [],
                    revision: revision,
                    data: data,
                    digest: digest
                )
            )
        }
        for index in 0..<16 {
            let key = "fixture-\(index)"
            models.append(
                model(
                    key: key,
                    delivery: "native-download",
                    packID: "com.auralign.studio.modelpack.\(key).v1",
                    packVersion: "1.0.0",
                    packRelativePath: "models/\(key)",
                    requiredForPresets: index == 0 ? ["max"] : [],
                    revision: revision,
                    data: data,
                    digest: digest
                )
            )
        }

        let catalog = ModelDeliveryCatalog(
            schema: ModelDeliveryCatalog.schemaVersion,
            catalogVersion: "1.0.0",
            productVersion: "0.3.0",
            allowedInitialHosts: ["huggingface.co"],
            redirectHostSuffixes: [
                "huggingface.co",
                "us.aws.cdn.hf.co",
            ],
            bootstrap: .init(
                bundleID: "com.auralign.studio.models.bootstrap.v1",
                bundleVersion: "1.0.0",
                modelKeys: bootstrapKeys
            ),
            packContract: testPackContract(),
            normalPresets: ["max": "fixture-0"],
            models: models
        )
        try catalog.validate()
        let pack = try XCTUnwrap(
            catalog.packs.first(where: { $0.modelKeys == ["fixture-0"] })
        )
        return CatalogFixture(
            root: root,
            data: data,
            catalog: catalog,
            pack: pack
        )
    }

    private func model(
        key: String,
        delivery: String,
        packID: String,
        packVersion: String,
        packRelativePath: String,
        requiredForPresets: [String],
        revision: String,
        data: Data,
        digest: String
    ) -> ModelDeliveryModel {
        var result = ModelDeliveryModel(
            key: key,
            role: "asr",
            modelID: "test/\(key)",
            aliases: [],
            directoryAliases: [],
            displayName: "Test \(key)",
            description: "Deterministic focused test model.",
            quality: "Test",
            speed: "Test",
            memory: "Test",
            accessibilityLabel: "Test \(key)",
            revision: revision,
            delivery: delivery,
            packID: packID,
            packVersion: packVersion,
            packRelativePath: packRelativePath,
            requiredForPresets: requiredForPresets,
            totalSize: Int64(data.count),
            files: [
                ModelDeliveryFile(
                    id: "model.bin",
                    modelKey: "",
                    relativePath: "model.bin",
                    sourceURL: URL(
                        string:
                            "https://huggingface.co/test/\(key)/resolve/"
                            + "\(revision)/model.bin"
                    )!,
                    expectedSize: Int64(data.count),
                    sha256: digest
                ),
            ],
            licenseExpression: "MIT",
            attribution: "Focused test attribution.",
            gated: false,
            redistributionStatus: "permitted_for_test",
            reviewStatus: "verified_for_test",
            reviewedAt: "2026-07-25",
            acquisitionStatus: "approved_exact_pinned_files",
            runtimeStatus: "approved_local",
            productClassification: "production",
            backendCompatibility: "focused test backend",
            modificationStatus: "unmodified focused fixture",
            sourceURL: URL(string: "https://huggingface.co/test/\(key)")!,
            termsURL: URL(string: "https://huggingface.co/test/\(key)")!
        )
        result.packSBOM = result.expectedPackSBOM
        return result
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

    private func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private func removeFixture(_ root: URL) {
        guard FileManager.default.fileExists(atPath: root.path) else { return }
        if let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        ) {
            let urls = enumerator.compactMap { $0 as? URL }
            for url in ([root] + urls).reversed() {
                _ = chmod(url.path, 0o700)
            }
        }
        try? FileManager.default.removeItem(at: root)
    }
}

private struct CatalogFixture {
    var root: URL
    var data: Data
    var catalog: ModelDeliveryCatalog
    var pack: ModelDeliveryPack
}

private final class FakeModelTransport:
    ModelFileDownloading,
    @unchecked Sendable
{
    private let lock = NSLock()
    private let bytes: Data
    private let pauseFirstAttempt: Bool
    private var attempts = 0
    private var sawResumeData = false

    init(bytes: Data, pauseFirstAttempt: Bool) {
        self.bytes = bytes
        self.pauseFirstAttempt = pauseFirstAttempt
    }

    var attemptCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return attempts
    }

    var receivedResumeDataOnSecondAttempt: Bool {
        lock.lock()
        defer { lock.unlock() }
        return sawResumeData
    }

    func download(
        identifier: String,
        request: URLRequest,
        resumeData: Data?,
        destination: URL,
        allowedRedirectHostSuffixes: [String],
        progress: @escaping @Sendable (ModelDownloadProgress) -> Void
    ) async throws {
        _ = identifier
        _ = request
        _ = allowedRedirectHostSuffixes
        let attempt = locked {
            attempts += 1
            let attempt = attempts
            if attempt > 1, resumeData != nil {
                sawResumeData = true
            }
            return attempt
        }

        if pauseFirstAttempt, attempt == 1 {
            throw ModelTransferError.paused(
                Data("synthetic-resume-data".utf8)
            )
        }
        try FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try bytes.write(to: destination)
        progress(
            ModelDownloadProgress(
                completedBytes: Int64(bytes.count),
                totalBytes: Int64(bytes.count)
            )
        )
    }

    func pause(identifier: String) {
        _ = identifier
    }

    func cancel(identifier: String) {
        _ = identifier
    }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}

private final class ModelEventRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String] = []

    var values: [String] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func record(_ event: ModelDownloadEvent) {
        let value: String
        switch event {
        case .preparing: value = "preparing"
        case .downloading: value = "downloading"
        case .paused: value = "paused"
        case .verifying: value = "verifying"
        case .publishing: value = "publishing"
        case .accepted: value = "accepted"
        }
        lock.lock()
        storage.append(value)
        lock.unlock()
    }
}
