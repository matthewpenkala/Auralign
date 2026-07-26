// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import CryptoKit
import Darwin
import XCTest
@testable import Auralign

@MainActor
final class EngineStateContractTests: XCTestCase {
    func testEngineLocationDistinguishesUnconfiguredOverrideAndBuildDefault() {
        let unconfigured = EngineLocation.resolve(
            override: nil,
            embeddedRepositoryRoot: nil
        )
        XCTAssertEqual(unconfigured.origin, .unconfigured)
        XCTAssertEqual(unconfigured.diagnosis, .unconfigured)
        XCTAssertFalse(unconfigured.isConfigured)
        XCTAssertFalse(unconfigured.isReady)
        XCTAssertEqual(unconfigured.repositoryDisplayPath, "Not selected")
        XCTAssertFalse(unconfigured.repositoryDisplayPath.contains("/dev/null"))
        XCTAssertFalse(unconfigured.pythonDisplayPath.contains("/dev/null"))

        let embedded = URL(fileURLWithPath: "/example/build-default", isDirectory: true)
        let buildDefault = EngineLocation.resolve(
            override: nil,
            embeddedRepositoryRoot: embedded
        )
        XCTAssertEqual(buildDefault.origin, .embeddedBuildDefault)
        XCTAssertEqual(buildDefault.repositoryRoot, embedded)
        XCTAssertTrue(buildDefault.isConfigured)

        let selected = EngineLocation.resolve(
            override: "/example/operator-selected",
            embeddedRepositoryRoot: embedded
        )
        XCTAssertEqual(selected.origin, .persistedOverride)
        XCTAssertEqual(
            selected.repositoryRoot.path,
            "/example/operator-selected"
        )
    }

    func testExternalModelDirectoryMustExistBeReadableAndNotBeASymlink() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-model-location-\(UUID().uuidString)",
                isDirectory: true
            )
        let directory = root.appendingPathComponent("models", isDirectory: true)
        let symlink = root.appendingPathComponent("models-link", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        try FileManager.default.createSymbolicLink(
            at: symlink,
            withDestinationURL: directory
        )
        defer { try? FileManager.default.removeItem(at: root) }

        XCTAssertTrue(EngineLocation.isUsableExternalModelDirectory(directory))
        XCTAssertFalse(EngineLocation.isUsableExternalModelDirectory(symlink))
        XCTAssertFalse(
            EngineLocation.isUsableExternalModelDirectory(
                root.appendingPathComponent("missing", isDirectory: true)
            )
        )
    }

    func testRelativePersistedModelDirectoryFailsClosed() throws {
        let suite = "auralign-engine-state-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set("relative/models", forKey: EngineLocation.modelCacheDefaultsKey)

        let selected = EngineLocation.configuredModelCacheRoot(
            userDefaults: defaults
        )

        XCTAssertNotNil(selected.root)
        XCTAssertFalse(selected.pathWasAbsolute)
    }

    func testPackagedRuntimeIgnoresDevelopmentModelOverride() throws {
        XCTAssertEqual(
            EngineLocation.trustedModelComponentRoot.path,
            "/Library/Application Support/com.auralign.studio/Models/"
                + EngineLocation.modelBundleIdentifier
        )

        let trustedRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-trusted-model-\(UUID().uuidString)",
                isDirectory: true
            )
            .appendingPathComponent(
                EngineLocation.modelBundleIdentifier,
                isDirectory: true
            )
        let arbitraryOverride = URL(
            fileURLWithPath: "/tmp/operator-selected-models",
            isDirectory: true
        )
        let location = EngineLocation(
            repositoryRoot: EngineLocation.applicationSupportRoot,
            origin: .embeddedRuntime,
            embeddedRuntimeRoot: URL(
                fileURLWithPath: "/Applications/Auralign.app/Contents/Frameworks/"
                    + EngineLocation.runtimeBundleName
                    + "/Contents/Resources/Runtime",
                isDirectory: true
            ),
            configuredModelCacheRoot: arbitraryOverride,
            installedModelComponentRoot: trustedRoot
        )

        XCTAssertEqual(
            location.modelCacheRoot,
            EngineLocation.defaultModelCacheRoot
        )
        XCTAssertNotEqual(location.modelCacheRoot, arbitraryOverride)
        XCTAssertEqual(location.modelComponentRoot, trustedRoot)
    }

    func testTrustedModelBundleDiagnosisFailsClosedOnMissingAndTamperedState() throws {
        let parent = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-trusted-model-\(UUID().uuidString)",
                isDirectory: true
            )
        let root = parent.appendingPathComponent(
            EngineLocation.modelBundleIdentifier,
            isDirectory: true
        )
        defer {
            makeWritableForRemoval(root)
            try? FileManager.default.removeItem(at: parent)
        }

        XCTAssertEqual(
            EngineLocation.trustedModelBundleDiagnosis(
                at: root,
                expectedOwnerUID: geteuid()
            ),
            .missingTrustedModelBundle
        )

        try makeTrustedModelComponent(at: root)
        XCTAssertEqual(
            EngineLocation.trustedModelBundleDiagnosis(
                at: root,
                expectedOwnerUID: geteuid()
            ),
            .ready
        )

        let punktFile = root.appendingPathComponent(
            "models/runtime-assets/nltk_data/tokenizers/punkt_tab/english/"
                + "abbrev_types.txt"
        )
        XCTAssertEqual(chmod(punktFile.path, 0o644), 0)
        XCTAssertEqual(
            EngineLocation.trustedModelBundleDiagnosis(
                at: root,
                expectedOwnerUID: geteuid()
            ),
            .invalidTrustedModelBundle
        )
        XCTAssertEqual(chmod(punktFile.path, 0o444), 0)

        let verification = root.appendingPathComponent(
            "model-bundle-verification.json"
        )
        XCTAssertEqual(chmod(verification.path, 0o644), 0)
        XCTAssertEqual(
            EngineLocation.trustedModelBundleDiagnosis(
                at: root,
                expectedOwnerUID: geteuid()
            ),
            .invalidTrustedModelBundle
        )
    }

    private func makeTrustedModelComponent(at root: URL) throws {
        let fileManager = FileManager.default
        let modelsRoot = root.appendingPathComponent("models", isDirectory: true)
        let licensesRoot = root.appendingPathComponent("licenses", isDirectory: true)
        let runtimeAssetRoot = modelsRoot
            .appendingPathComponent("runtime-assets", isDirectory: true)
            .appendingPathComponent("nltk_data", isDirectory: true)
        let punktRoot = runtimeAssetRoot
            .appendingPathComponent("tokenizers", isDirectory: true)
            .appendingPathComponent("punkt_tab", isDirectory: true)
        let punktLanguageRoot = punktRoot
            .appendingPathComponent("english", isDirectory: true)
        try fileManager.createDirectory(
            at: modelsRoot,
            withIntermediateDirectories: true
        )
        try fileManager.createDirectory(
            at: licensesRoot,
            withIntermediateDirectories: true
        )
        try fileManager.createDirectory(
            at: punktLanguageRoot,
            withIntermediateDirectories: true
        )

        let modelDirectories = [
            ("community-1", "pyannote--speaker-diarization-community-1"),
            ("en", "facebook--wav2vec2-base-960h"),
            ("ecapa", "speechbrain--spkrec-ecapa-voxceleb"),
        ]
        var modelRecords: [[String: Any]] = []
        var directories = [
            root,
            modelsRoot,
            licensesRoot,
            modelsRoot.appendingPathComponent("runtime-assets", isDirectory: true),
            runtimeAssetRoot,
            runtimeAssetRoot.appendingPathComponent("tokenizers", isDirectory: true),
            punktRoot,
            punktLanguageRoot,
        ]
        var files: [URL] = []
        for (key, directoryName) in modelDirectories {
            let directory = modelsRoot.appendingPathComponent(
                directoryName,
                isDirectory: true
            )
            try fileManager.createDirectory(
                at: directory,
                withIntermediateDirectories: true
            )
            directories.append(directory)
            let data = Data("fixture-\(key)".utf8)
            let file = directory.appendingPathComponent("model.bin")
            try data.write(to: file, options: .withoutOverwriting)
            files.append(file)
            modelRecords.append([
                "key": key,
                "directory_name": directoryName,
                "product_classification": "production",
                "blocker": NSNull(),
                "backend_compatibility": "focused Swift contract fixture",
                "modification_status": "unmodified exact pinned runtime files",
                "package_bundle_id": EngineLocation.modelBundleIdentifier,
                "package_relative_path": "models/\(directoryName)",
                "files": [
                    [
                        "path": "models/\(directoryName)/model.bin",
                        "size": data.count,
                        "sha256": sha256(data),
                    ],
                ],
            ])
        }

        let emptySHA256 =
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        let punktFilenames = [
            "abbrev_types.txt",
            "collocations.tab",
            "ortho_context.tab",
            "sent_starters.txt",
        ]
        var punktFileRecords: [[String: Any]] = []
        for filename in punktFilenames {
            let file = punktLanguageRoot.appendingPathComponent(filename)
            try Data().write(to: file, options: .withoutOverwriting)
            files.append(file)
            punktFileRecords.append([
                "path": "models/runtime-assets/nltk_data/tokenizers/"
                    + "punkt_tab/english/\(filename)",
                "size": 0,
                "sha256": emptySHA256,
            ])
        }
        let punktMarkerData = try canonicalJSON([
            "schema_version": "ltm-generated-punkt.v1",
            "generator": "nltk.tokenize.punkt.save_punkt_params",
            "nltk_version": "3.9.4",
            "training_data": "none",
            "files": Dictionary(
                uniqueKeysWithValues: punktFilenames.map { ($0, emptySHA256) }
            ),
        ])
        let punktMarker = runtimeAssetRoot.appendingPathComponent(
            ".ltm-generated-punkt.json"
        )
        try punktMarkerData.write(to: punktMarker, options: .withoutOverwriting)
        files.append(punktMarker)
        let runtimeAssets: [[String: Any]] = [
            [
                "key": "nltk-punkt-tab-english-corpus-free",
                "runtime_cache_relative_root": "runtime-assets/nltk_data",
                "package_relative_root": "models/runtime-assets/nltk_data",
                "schema_version": "ltm-generated-punkt.v1",
                "generator": "nltk.tokenize.punkt.save_punkt_params",
                "nltk_version": "3.9.4",
                "training_data": "none",
                "marker": [
                    "path": "models/runtime-assets/nltk_data/"
                        + ".ltm-generated-punkt.json",
                    "size": punktMarkerData.count,
                    "sha256": sha256(punktMarkerData),
                ],
                "files": punktFileRecords,
            ],
        ]

        for name in [
            "THIRD-PARTY-NOTICES.txt",
            "model-bundle-provenance.json",
            "licenses/MIT.txt",
            "licenses/Apache-2.0.txt",
            "licenses/CC-BY-4.0.txt",
        ] {
            let file = root.appendingPathComponent(name)
            try Data("fixture\n".utf8).write(to: file, options: .withoutOverwriting)
            files.append(file)
        }

        let modelKeys = modelDirectories.map { $0.0 }
        let manifestData = try canonicalJSON([
            "schema_version": "1.0",
            "layout_version": "1",
            "bundle_id": EngineLocation.modelBundleIdentifier,
            "bundle_version": EngineLocation.modelBundleVersion,
            "model_keys": modelKeys,
            "models": modelRecords,
            "runtime_assets": runtimeAssets,
        ])
        let manifest = root.appendingPathComponent("model-bundle-manifest.json")
        try manifestData.write(to: manifest, options: .withoutOverwriting)
        files.append(manifest)

        let verificationData = try canonicalJSON([
            "schema_version": "1.0",
            "record_type": "auralign-model-bundle-verification",
            "bundle_id": EngineLocation.modelBundleIdentifier,
            "bundle_version": EngineLocation.modelBundleVersion,
            "state": "verified",
            "model_keys": modelKeys,
            "runtime_asset_keys": ["nltk-punkt-tab-english-corpus-free"],
            "manifest_sha256": sha256(manifestData),
            "checks": [
                "canonical-json",
                "exact-model-authority",
                "exact-payload-allowlist",
                "exact-sha256-and-size",
                "immutable-file-modes",
                "single-owner-tree",
                "single-filesystem-tree",
                "no-symbolic-links",
                "no-hardlinks",
                "no-private-acquisition-metadata",
                "locked-nltk-punkt-contract",
            ],
        ])
        let verification = root.appendingPathComponent(
            "model-bundle-verification.json"
        )
        try verificationData.write(to: verification, options: .withoutOverwriting)
        files.append(verification)

        let completionData = try canonicalJSON([
            "schema_version": "1.0",
            "record_type": "auralign-model-bundle-completion",
            "bundle_id": EngineLocation.modelBundleIdentifier,
            "bundle_version": EngineLocation.modelBundleVersion,
            "state": "complete",
            "manifest_sha256": sha256(manifestData),
            "verification_record_sha256": sha256(verificationData),
        ])
        let completion = root.appendingPathComponent("model-bundle-complete.json")
        try completionData.write(to: completion, options: .withoutOverwriting)
        files.append(completion)

        for file in files {
            guard chmod(file.path, 0o444) == 0 else {
                throw POSIXError(.EPERM)
            }
        }
        for directory in directories.reversed() {
            guard chmod(directory.path, 0o555) == 0 else {
                throw POSIXError(.EPERM)
            }
        }
    }

    private func canonicalJSON(_ object: Any) throws -> Data {
        var data = try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys]
        )
        data.append(0x0A)
        return data
    }

    private func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private func makeWritableForRemoval(_ root: URL) {
        let fileManager = FileManager.default
        if let enumerator = fileManager.enumerator(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey]
        ) {
            for case let url as URL in enumerator {
                var information = stat()
                if lstat(url.path, &information) == 0,
                   (information.st_mode & S_IFMT) == S_IFDIR {
                    _ = chmod(url.path, 0o700)
                }
            }
        }
        _ = chmod(root.path, 0o700)
    }

    private func loadManifestAndCatalog() throws -> (ConfigManifest, FieldCatalog) {
        let bundle = Bundle(for: Self.self)
        let manifestURL = try XCTUnwrap(
            bundle.url(forResource: "config_manifest", withExtension: "json")
                ?? Bundle.main.url(forResource: "config_manifest", withExtension: "json")
        )
        let catalogURL = try XCTUnwrap(
            bundle.url(forResource: "FieldCatalog", withExtension: "json")
                ?? Bundle.main.url(forResource: "FieldCatalog", withExtension: "json")
        )
        let manifest = try JSONDecoder().decode(
            ConfigManifest.self,
            from: Data(contentsOf: manifestURL)
        )
        let catalog = try JSONDecoder().decode(
            FieldCatalog.self,
            from: Data(contentsOf: catalogURL)
        )
        return (manifest, catalog)
    }

    private func handshake(
        manifest: ConfigManifest,
        protocolVersion: String = "auralign_gui_bridge.v1",
        packageVersion: String? = nil,
        schemaVersion: String? = nil,
        acceptanceSchemaVersion: String? = nil,
        resultSchemaVersion: String? = nil,
        commands: Set<String>? = nil,
        python: String = "3.11.13"
    ) -> JSONValue {
        .object([
            "protocol": .string(protocolVersion),
            "package_version": .string(packageVersion ?? manifest.packageVersion),
            "manifest_schema_version": .string(schemaVersion ?? manifest.schemaVersion),
            "job_acceptance_schema_version": .string(
                acceptanceSchemaVersion ?? AppModel.supportedJobAcceptanceSchema
            ),
            "job_result_schema_version": .string(
                resultSchemaVersion ?? AppModel.supportedJobResultSchema
            ),
            "commands": .array(
                (commands ?? AppModel.requiredBridgeCommands)
                    .sorted()
                    .map(JSONValue.string)
            ),
            "python": .string(python),
        ])
    }

    private func modelStatusRow(role: String, key: String) -> JSONValue {
        .object([
            "key": .string(key),
            "role": .string(role),
            "model_id": .string("example/\(key)"),
            "aliases": .array([]),
            "directory_name": .string("example--\(key)"),
            "required_files": .array([.string("config.json")]),
            "any_required_files": .array([.string("weights.bin")]),
            "runtime_files": .array([
                .string("config.json"),
                .string("weights.bin"),
            ]),
            "expected_sha256": .object([
                "config.json": .string(String(repeating: "a", count: 64)),
                "weights.bin": .string(String(repeating: "b", count: 64)),
            ]),
            "gated": .bool(false),
            "optional": .bool(false),
            "pullable": .bool(true),
            "revision": .string(String(repeating: "0", count: 40)),
            "license_expression": .string("Apache-2.0"),
            "acquisition_status": .string("connected_pull"),
            "runtime_status": .string("approved_local_runtime"),
            "redistribution_status": .string("upstream_terms"),
            "review_status": .string("verified"),
            "reviewed_at": .string("2026-07-19"),
            "product_classification": .string("production"),
            "product_selectable": .bool(true),
            "blocker": .null,
            "backend_compatibility": .string("focused Swift contract fixture"),
            "modification_status": .string("unmodified fixture"),
            "package_bundle_id": .string(EngineLocation.modelBundleIdentifier),
            "package_relative_path": .string("models/example--\(key)"),
            "source_url": .string("https://example.invalid/source"),
            "terms_url": .string("https://example.invalid/terms"),
            "attribution": .string("Example model"),
            "status_id": .string("\(role):\(key)"),
            "present": .bool(true),
            "partial": .bool(false),
            "revision_verified": .bool(true),
            "selected_for_config": .bool(true),
            "problem": .null,
        ])
    }

    private func modelStatusRow(entry: ModelManifestEntry) -> JSONValue {
        .object([
            "key": .string(entry.key),
            "role": .string(entry.role),
            "model_id": .string(entry.modelId),
            "aliases": .array((entry.aliases ?? []).map(JSONValue.string)),
            "directory_name": .string(entry.directoryName),
            "required_files": .array(entry.requiredFiles.map(JSONValue.string)),
            "any_required_files": .array(entry.anyRequiredFiles.map(JSONValue.string)),
            "runtime_files": .array(entry.runtimeFiles.map(JSONValue.string)),
            "expected_sha256": .object(
                entry.expectedSHA256.mapValues(JSONValue.string)
            ),
            "gated": .bool(entry.gated),
            "optional": .bool(entry.optional),
            "pullable": .bool(entry.pullable),
            "revision": .string(entry.revision ?? ""),
            "license_expression": .string(entry.licenseExpression ?? ""),
            "acquisition_status": .string(entry.acquisitionStatus ?? ""),
            "runtime_status": .string(entry.runtimeStatus ?? ""),
            "redistribution_status": .string(entry.redistributionStatus ?? ""),
            "review_status": .string(entry.reviewStatus ?? ""),
            "reviewed_at": .string(entry.reviewedAt ?? ""),
            "product_classification": .string(entry.productClassification),
            "product_selectable": .bool(entry.productSelectable),
            "blocker": entry.blocker.map(JSONValue.string) ?? .null,
            "backend_compatibility": .string(entry.backendCompatibility),
            "modification_status": .string(entry.modificationStatus),
            "package_bundle_id":
                entry.packageBundleId.map(JSONValue.string) ?? .null,
            "package_relative_path":
                entry.packageRelativePath.map(JSONValue.string) ?? .null,
            "source_url": .string(entry.sourceURL ?? ""),
            "terms_url": .string(entry.termsURL ?? ""),
            "attribution": .string(entry.attribution ?? ""),
            "status_id": .string("\(entry.role):\(entry.key)"),
            "present": .bool(true),
            "partial": .bool(false),
            "revision_verified": .bool(true),
            "selected_for_config": .bool(true),
            "problem": .null,
        ])
    }

    private func modelStatusPayload(
        rows: [JSONValue],
        identifiers: [String],
        complete: Bool = true
    ) -> JSONValue {
        .object([
            "schema_version": .string("gui_model_status.v1"),
            "complete": .bool(complete),
            "expected_ids": .array(identifiers.map(JSONValue.string)),
            "observed_ids": .array(identifiers.map(JSONValue.string)),
            "expected_count": .int(identifiers.count),
            "observed_count": .int(identifiers.count),
            "models": .array(rows),
        ])
    }

    private func startupEvidence(
        _ identifier: String,
        complete: Bool = true
    ) -> DoctorStartupEvidence {
        DoctorStartupEvidence(
            requested: true,
            complete: complete,
            expectedIDs: [identifier],
            observedIDs: complete ? [identifier] : []
        )
    }

    func testCompatibleHandshakeRequiresTheCompleteContract() throws {
        let (manifest, _) = try loadManifestAndCatalog()
        XCTAssertNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest),
                manifest: manifest
            )
        )
    }

    func testHandshakeRejectsProtocolPackageSchemaAndCommandDrift() throws {
        let (manifest, _) = try loadManifestAndCatalog()
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest, protocolVersion: "auralign_gui_bridge.v2"),
                manifest: manifest
            )
        )
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest, packageVersion: "999.0.0"),
                manifest: manifest
            )
        )
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest, schemaVersion: "gui_config_manifest.v2"),
                manifest: manifest
            )
        )
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(
                    manifest: manifest,
                    acceptanceSchemaVersion: "gui_job_acceptance.v2"
                ),
                manifest: manifest
            )
        )
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(
                    manifest: manifest,
                    resultSchemaVersion: "gui_job_result.v2"
                ),
                manifest: manifest
            )
        )
        var incompleteCommands = AppModel.requiredBridgeCommands
        incompleteCommands.remove("transcribe")
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest, commands: incompleteCommands),
                manifest: manifest
            )
        )
    }

    func testHandshakeRejectsUnsupportedPython() throws {
        let (manifest, _) = try loadManifestAndCatalog()
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest, python: "3.12.9"),
                manifest: manifest
            )
        )
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest, python: "3.13.0"),
                manifest: manifest
            )
        )
        XCTAssertNotNil(
            AppModel.handshakeCompatibilityIssue(
                handshake(manifest: manifest, python: "unknown"),
                manifest: manifest
            )
        )
    }

    func testReadyDoctorMustContainStartupProbeEvidence() {
        let staticOnly = DoctorSummary(
            ok: true,
            offline: true,
            checks: [
                DoctorCheck(name: "ffmpeg", status: "ok", message: "available"),
            ]
        )
        XCTAssertNotNil(AppModel.startupDoctorIssue(staticOnly))

        let startupChecked = DoctorSummary(
            ok: true,
            offline: true,
            checks: [
                DoctorCheck(
                    name: "startup:asr:mlx-whisper",
                    status: "ok",
                    message: "loaded pinned local model"
                ),
            ],
            startupEvidence: startupEvidence("startup:asr:mlx-whisper")
        )
        XCTAssertNil(AppModel.startupDoctorIssue(startupChecked))

        let contradictory = DoctorSummary(
            ok: true,
            offline: true,
            checks: [
                DoctorCheck(
                    name: "startup:asr:mlx-whisper",
                    status: "error",
                    message: "backend failed to load"
                ),
            ],
            startupEvidence: startupEvidence("startup:asr:mlx-whisper")
        )
        XCTAssertNotNil(AppModel.startupDoctorIssue(contradictory))

        let missingRow = DoctorSummary(
            ok: true,
            offline: true,
            checks: [],
            startupEvidence: startupEvidence("startup:asr:mlx-whisper")
        )
        XCTAssertNotNil(AppModel.startupDoctorIssue(missingRow))

        let duplicateRows = DoctorSummary(
            ok: true,
            offline: true,
            checks: [
                DoctorCheck(
                    name: "startup:asr:mlx-whisper",
                    status: "ok",
                    message: "first"
                ),
                DoctorCheck(
                    name: "startup:asr:mlx-whisper",
                    status: "ok",
                    message: "duplicate"
                ),
            ],
            startupEvidence: startupEvidence("startup:asr:mlx-whisper")
        )
        XCTAssertNotNil(AppModel.startupDoctorIssue(duplicateRows))

        let contradictoryRows = DoctorSummary(
            ok: true,
            offline: true,
            checks: [
                DoctorCheck(
                    name: "startup:asr:mlx-whisper",
                    status: "ok",
                    message: "loaded pinned local model"
                ),
                DoctorCheck(
                    name: "model:asr:large-v3",
                    status: "error",
                    message: "missing"
                ),
            ],
            startupEvidence: startupEvidence("startup:asr:mlx-whisper")
        )
        XCTAssertNotNil(AppModel.startupDoctorIssue(contradictoryRows))
    }

    func testModelStatusAcceptanceRequiresCompleteAuthoritativeEvidence() throws {
        let row = modelStatusRow(role: "asr", key: "large-v3")
        let payload = modelStatusPayload(rows: [row], identifiers: ["asr:large-v3"])

        let statuses = try AppModel.strictModelStatuses(
            from: payload,
            expectedIDs: ["asr:large-v3"]
        )

        XCTAssertEqual(statuses.count, 1)
        XCTAssertEqual(statuses.first?.id, "asr/large-v3")
        XCTAssertTrue(statuses.first?.revisionVerified == true)
        XCTAssertTrue(statuses.first?.selectedForConfig == true)
    }

    func testModelStatusAcceptanceRejectsPartialArraysAndCompletenessDrift() {
        let first = modelStatusRow(role: "asr", key: "large-v3")
        var malformedObject = modelStatusRow(
            role: "alignment",
            key: "wav2vec2"
        ).objectValue ?? [:]
        malformedObject.removeValue(forKey: "revision_verified")
        let identifiers = ["asr:large-v3", "alignment:wav2vec2"]
        let partialPayload = modelStatusPayload(
            rows: [first, .object(malformedObject)],
            identifiers: identifiers
        )

        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: partialPayload,
                expectedIDs: Set(identifiers)
            )
        )
        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [first],
                    identifiers: ["asr:large-v3"],
                    complete: false
                ),
                expectedIDs: ["asr:large-v3"]
            )
        )
        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [first],
                    identifiers: ["asr:large-v3"]
                ),
                expectedIDs: ["asr:unexpected"]
            )
        )
    }

    func testModelStatusAcceptanceRejectsRowOrderAndSelectionMetadataDrift() {
        let asr = modelStatusRow(role: "asr", key: "large-v3")
        let alignment = modelStatusRow(role: "alignment", key: "wav2vec2")
        let identifiers = ["asr:large-v3", "alignment:wav2vec2"]

        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [alignment, asr],
                    identifiers: identifiers
                ),
                expectedIDs: Set(identifiers)
            )
        )

        var missingSelection = asr.objectValue ?? [:]
        missingSelection.removeValue(forKey: "selected_for_config")
        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [.object(missingSelection)],
                    identifiers: ["asr:large-v3"]
                ),
                expectedIDs: ["asr:large-v3"]
            )
        )
    }

    func testModelStatusMetadataMustMatchTheBundledManifestExactly() throws {
        let (manifest, _) = try loadManifestAndCatalog()
        let entry = try XCTUnwrap(manifest.modelManifest.first)
        let identifier = "\(entry.role):\(entry.key)"
        let valid = modelStatusPayload(
            rows: [modelStatusRow(entry: entry)],
            identifiers: [identifier]
        )

        XCTAssertNoThrow(
            try AppModel.strictModelStatuses(
                from: valid,
                expectedIDs: [identifier],
                authoritativeManifestEntries: [entry]
            )
        )

        var mismatch = modelStatusRow(entry: entry).objectValue ?? [:]
        mismatch["revision"] = .string("mismatched-revision")
        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [.object(mismatch)],
                    identifiers: [identifier]
                ),
                expectedIDs: [identifier],
                authoritativeManifestEntries: [entry]
            )
        )

        var fileContractMismatch = modelStatusRow(entry: entry).objectValue ?? [:]
        fileContractMismatch["runtime_files"] = .array([.string("invented.bin")])
        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [.object(fileContractMismatch)],
                    identifiers: [identifier]
                ),
                expectedIDs: [identifier],
                authoritativeManifestEntries: [entry]
            )
        )

        var digestMismatch = modelStatusRow(entry: entry).objectValue ?? [:]
        digestMismatch["expected_sha256"] = .object([:])
        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [.object(digestMismatch)],
                    identifiers: [identifier]
                ),
                expectedIDs: [identifier],
                authoritativeManifestEntries: [entry]
            )
        )

        var productPolicyMismatch = modelStatusRow(entry: entry).objectValue ?? [:]
        productPolicyMismatch["product_selectable"] = .bool(!entry.productSelectable)
        XCTAssertThrowsError(
            try AppModel.strictModelStatuses(
                from: modelStatusPayload(
                    rows: [.object(productPolicyMismatch)],
                    identifiers: [identifier]
                ),
                expectedIDs: [identifier],
                authoritativeManifestEntries: [entry]
            )
        )
    }

    func testConfigStoreFailsClosedWithoutEngineAuthority() throws {
        let (manifest, catalog) = try loadManifestAndCatalog()
        let store = ConfigStore(manifest: manifest, catalog: catalog)

        XCTAssertFalse(store.isEngineSynchronized)
        XCTAssertFalse(store.validationIsCurrent)
        XCTAssertFalse(store.isValid)

        store.detach(reason: "Engine changed.")
        XCTAssertEqual(store.engineSyncError, "Engine changed.")
        XCTAssertFalse(store.isEngineSynchronized)
        XCTAssertFalse(store.isValid)
        XCTAssertTrue(store.environmentOverrides.isEmpty)
    }

    func testImportedModelSelectionMustBeKnownAndRuntimeApproved() throws {
        let (manifest, catalog) = try loadManifestAndCatalog()
        let store = ConfigStore(manifest: manifest, catalog: catalog)
        XCTAssertNil(store.runtimeModelSelectionIssue)
        let reviewed = try XCTUnwrap(
            manifest.resolveModel(role: "asr-mlx", value: "mlx-large-v3")
        )
        XCTAssertTrue(reviewed.isRuntimeSelectable)

        store.setOverride("asr.backend", to: .string("mlx-whisper"))
        store.setOverride("asr.mlx_model", to: .string(reviewed.key))
        XCTAssertNil(store.runtimeModelSelectionIssue)

        store.setOverride("asr.mlx_model", to: .string("not-in-the-authoritative-manifest"))
        XCTAssertNotNil(store.runtimeModelSelectionIssue)
    }
}
