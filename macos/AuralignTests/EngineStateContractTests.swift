// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

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
            "gated": .bool(false),
            "optional": .bool(false),
            "pullable": .bool(true),
            "revision": .string("0123456789abcdef"),
            "license_expression": .string("Apache-2.0"),
            "acquisition_status": .string("connected_pull"),
            "runtime_status": .string("approved_local_runtime"),
            "redistribution_status": .string("upstream_terms"),
            "review_status": .string("verified"),
            "reviewed_at": .string("2026-07-19"),
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
        let blocked = try XCTUnwrap(
            manifest.modelChoices(role: "asr-mlx").first(where: { !$0.isRuntimeSelectable })
        )

        store.setOverride("asr.backend", to: .string("mlx-whisper"))
        store.setOverride("asr.mlx_model", to: .string(blocked.key))
        XCTAssertNotNil(store.runtimeModelSelectionIssue)

        store.setOverride("asr.mlx_model", to: .string("not-in-the-authoritative-manifest"))
        XCTAssertNotNil(store.runtimeModelSelectionIssue)
    }
}
