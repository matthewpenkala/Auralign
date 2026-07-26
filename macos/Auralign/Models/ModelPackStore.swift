// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import CryptoKit
import Darwin
import Foundation

struct ModelPackVerificationResult: Equatable, Sendable {
    var isReady: Bool
    var problem: String?

    static let ready = ModelPackVerificationResult(isReady: true, problem: nil)

    static func rejected(_ problem: String) -> ModelPackVerificationResult {
        ModelPackVerificationResult(isReady: false, problem: problem)
    }
}

/// Owns the fixed per-user model-pack namespace. Every destructive operation is
/// constrained to a path derived from a validated signed pack identifier.
actor ModelPackStore {
    static let schemaVersion = "1.0"
    static let layoutVersion = "1"
    static let manifestName = "model-pack-manifest.json"
    static let verificationName = "model-pack-verification.json"
    static let provenanceName = "model-pack-provenance.json"
    static let sbomName = "model-pack.cdx.json"
    static let completionName = "model-pack-complete.json"
    static let noticesName = "THIRD-PARTY-NOTICES.txt"
    static let licensesDirectoryName = "licenses"
    static let verificationChecks = [
        "canonical-json",
        "exact-model-authority",
        "exact-payload-allowlist",
        "exact-sha256-and-size",
        "cyclonedx-1.6-model-sbom",
        "current-user-ownership",
        "private-read-only-modes",
        "single-filesystem-tree",
        "no-symbolic-links",
        "no-hardlinks",
        "no-private-acquisition-metadata",
        "same-filesystem-atomic-publication",
        "published-root-read-only-seal",
    ]

    let modelsRoot: URL
    let expectedOwnerUID: uid_t
    let expectedOwnerGID: gid_t
    private let fileManager: FileManager
    private let licenseBundle: Bundle

    init(
        modelsRoot: URL = EngineLocation.defaultModelCacheRoot,
        expectedOwnerUID: uid_t = geteuid(),
        expectedOwnerGID: gid_t = getegid(),
        fileManager: FileManager = .default,
        licenseBundle: Bundle = .main
    ) {
        self.modelsRoot = Self.canonicalSystemAliasURL(modelsRoot)
        self.expectedOwnerUID = expectedOwnerUID
        self.expectedOwnerGID = expectedOwnerGID
        self.fileManager = fileManager
        self.licenseBundle = licenseBundle
    }

    /// macOS exposes a few root-owned compatibility aliases (notably `/var`
    /// and `/tmp`) as symbolic links. Canonicalize only those fixed system
    /// aliases before descriptor traversal; never resolve an arbitrary
    /// user-controlled ancestor in the model namespace.
    private static func canonicalSystemAliasURL(_ url: URL) -> URL {
        let lexical = lexicallyNormalizedFileURL(url)
        let path = lexical.path
        for (alias, canonical) in [
            ("/var", "/private/var"),
            ("/tmp", "/private/tmp"),
            ("/etc", "/private/etc"),
        ] {
            if path == alias {
                return URL(fileURLWithPath: canonical, isDirectory: true)
            }
            if path.hasPrefix(alias + "/") {
                return URL(
                    fileURLWithPath: canonical + path.dropFirst(alias.count),
                    isDirectory: lexical.hasDirectoryPath
                )
            }
        }
        return lexical
    }

    /// Foundation's `standardizedFileURL` may rewrite an existing
    /// `/private/var` prefix back through `/var`, while leaving a not-yet-
    /// existing descendant untouched. That makes security decisions depend on
    /// filesystem timing. Normalize dot components lexically instead.
    private static func lexicallyNormalizedFileURL(_ url: URL) -> URL {
        guard url.isFileURL, url.path.hasPrefix("/") else { return url }
        var components: [String] = []
        for component in url.path.split(separator: "/", omittingEmptySubsequences: true) {
            switch component {
            case ".":
                continue
            case "..":
                if !components.isEmpty {
                    components.removeLast()
                }
            default:
                components.append(String(component))
            }
        }
        return URL(
            fileURLWithPath: "/" + components.joined(separator: "/"),
            isDirectory: url.hasDirectoryPath
        )
    }

    var packsRoot: URL {
        modelsRoot.appendingPathComponent("Packs", isDirectory: true)
    }

    var stagingRoot: URL {
        modelsRoot.appendingPathComponent(".staging", isDirectory: true)
    }

    var resumeRoot: URL {
        modelsRoot.appendingPathComponent(".resume", isDirectory: true)
    }

    var journalURL: URL {
        modelsRoot.appendingPathComponent("download-journal.json")
    }

    func prepareRoots() throws {
        for directory in [modelsRoot, packsRoot, stagingRoot, resumeRoot] {
            try createPrivateDirectory(directory)
        }
    }

    func readJournalData() -> Data? {
        do {
            guard try pathEntryExists(journalURL) else { return nil }
            guard let data = stableFileData(
                at: journalURL,
                expectedMode: nil,
                maximumSize: 1024 * 1024,
                requireNonEmpty: true,
                requirePrivateMode: true
            ) else {
                try? removeOwnedItem(journalURL)
                return nil
            }
            return data
        } catch {
            try? removeOwnedItem(journalURL)
            return nil
        }
    }

    func writeJournalData(_ data: Data?) throws {
        guard let data, !data.isEmpty, data.count <= 1024 * 1024 else {
            try removeOwnedItem(journalURL)
            return
        }
        try prepareRoots()
        try data.write(to: journalURL, options: [.atomic])
        guard chmod(journalURL.path, 0o600) == 0 else {
            throw StoreError.couldNotSecureState
        }
    }

    func installedPackURL(_ pack: ModelDeliveryPack) throws -> URL {
        try validateIdentity(pack)
        return packsRoot
            .appendingPathComponent(pack.id, isDirectory: true)
            .appendingPathComponent(pack.version, isDirectory: true)
            .absoluteURL
    }

    func stagingPackURL(
        _ pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws -> URL {
        try validateIdentity(pack)
        guard ModelDeliveryCatalog.isValidSHA256(catalogDigest) else {
            throw StoreError.invalidCatalogDigest
        }
        return stagingRoot.appendingPathComponent(
            "\(pack.id).\(pack.version).\(catalogDigest.prefix(16))",
            isDirectory: true
        ).absoluteURL
    }

    func prepareStaging(
        for pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws -> URL {
        try prepareRoots()
        let root = try stagingPackURL(pack, catalogDigest: catalogDigest)
        try createPrivateDirectory(root)
        for file in pack.files {
            let destination = try stagedFileURL(
                for: file,
                pack: pack,
                catalogDigest: catalogDigest
            )
            try createPrivateDirectory(destination.deletingLastPathComponent())
        }
        return root
    }

    func stagedFileURL(
        for file: ModelDeliveryFile,
        pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws -> URL {
        guard pack.files.contains(file),
              ModelDeliveryCatalog.isSafeRelativePath(file.relativePath)
        else {
            throw StoreError.unknownFile
        }
        let root = try stagingPackURL(pack, catalogDigest: catalogDigest)
        let result = Self.lexicallyNormalizedFileURL(
            root.appendingPathComponent(file.relativePath)
        )
        guard result.path.hasPrefix(root.path + "/") else {
            throw StoreError.unsafePath
        }
        return result
    }

    func temporaryDownloadURL(
        for file: ModelDeliveryFile,
        pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws -> URL {
        let final = try stagedFileURL(
            for: file,
            pack: pack,
            catalogDigest: catalogDigest
        )
        return final.deletingLastPathComponent().appendingPathComponent(
            ".\(final.lastPathComponent).download",
            isDirectory: false
        )
    }

    func resumeData(
        for file: ModelDeliveryFile,
        pack: ModelDeliveryPack
    ) throws -> Data? {
        let url = try resumeDataURL(for: file, pack: pack)
        guard try pathEntryExists(url) else { return nil }
        guard let data = stableFileData(
            at: url,
            expectedMode: nil,
            maximumSize: 16 * 1024 * 1024,
            requireNonEmpty: true,
            requirePrivateMode: true
        ) else {
            try? removeOwnedItem(url)
            return nil
        }
        return data
    }

    func storeResumeData(
        _ data: Data?,
        for file: ModelDeliveryFile,
        pack: ModelDeliveryPack
    ) throws {
        let url = try resumeDataURL(for: file, pack: pack)
        guard let data, !data.isEmpty, data.count <= 16 * 1024 * 1024 else {
            try? removeOwnedItem(url)
            return
        }
        try createPrivateDirectory(url.deletingLastPathComponent())
        try data.write(to: url, options: [.atomic])
        guard chmod(url.path, 0o600) == 0 else {
            throw StoreError.couldNotSecureState
        }
    }

    func removeResumeData(
        for file: ModelDeliveryFile,
        pack: ModelDeliveryPack
    ) throws {
        try removeOwnedItem(try resumeDataURL(for: file, pack: pack))
    }

    func stagedFileIsValid(
        _ file: ModelDeliveryFile,
        pack: ModelDeliveryPack,
        catalogDigest: String
    ) -> Bool {
        guard let url = try? stagedFileURL(
            for: file,
            pack: pack,
            catalogDigest: catalogDigest
        ) else {
            return false
        }
        return verifyPayloadFile(
            at: url,
            expectedSize: file.expectedSize,
            expectedSHA256: file.sha256,
            requiresReadOnlyMode: false
        )
    }

    func acceptDownloadedFile(
        at temporaryURL: URL,
        file: ModelDeliveryFile,
        pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws {
        let expectedTemporaryURL = try temporaryDownloadURL(
            for: file,
            pack: pack,
            catalogDigest: catalogDigest
        )
        guard Self.canonicalSystemAliasURL(temporaryURL).pathComponents
            == expectedTemporaryURL.pathComponents
        else {
            throw StoreError.unsafePath
        }
        let destination = try stagedFileURL(
            for: file,
            pack: pack,
            catalogDigest: catalogDigest
        )
        try? removeOwnedItem(destination)
        do {
            try promoteVerifiedDownloadedFile(
                from: temporaryURL,
                to: destination,
                expectedSize: file.expectedSize,
                expectedSHA256: file.sha256,
                fileID: file.id
            )
        } catch {
            try? removeOwnedItem(temporaryURL)
            throw error
        }
    }

    func sealAndPublish(
        pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws -> URL {
        let staging = try stagingPackURL(pack, catalogDigest: catalogDigest)
        for file in pack.files {
            guard stagedFileIsValid(
                file,
                pack: pack,
                catalogDigest: catalogDigest
            ) else {
                throw StoreError.digestMismatch(file.id)
            }
        }

        let metadata = try deterministicMetadata(for: pack)
        try writeDeterministicMetadata(metadata, under: staging)
        // Darwin refuses to rename a directory whose own mode is 0500. Seal
        // every descendant first, but keep this uniquely named staging root
        // private and writable until the exclusive rename commits. Readiness
        // remains closed until the published root is changed to 0500 and a
        // second strict verification succeeds.
        try makeTreeReadOnly(staging, sealRoot: false)

        let stagedVerification = verifyPack(
            pack,
            catalogDigest: catalogDigest,
            at: staging,
            acceptedRootMode: 0o700
        )
        guard stagedVerification.isReady else {
            throw StoreError.packVerificationFailed(
                stagedVerification.problem
                    ?? "The staged pack failed deterministic verification."
            )
        }

        try createPrivateDirectory(packsRoot)
        let identifierRoot = packsRoot.appendingPathComponent(
            pack.id,
            isDirectory: true
        )
        try createPrivateDirectory(identifierRoot)
        let destination = try installedPackURL(pack)
        if try pathEntryExists(destination) {
            let existing = verifyPack(
                pack,
                catalogDigest: catalogDigest,
                at: destination
            )
            guard existing.isReady else {
                throw StoreError.packAlreadyExists(
                    existing.problem
                        ?? "An invalid same-version pack already exists."
                )
            }
            try makeTreeWritable(staging)
            try removeOwnedItem(staging)
            try removeResumeDirectory(for: pack)
            return destination
        }
        try atomicRenameExclusive(from: staging, to: destination)
        try sealPublishedRoot(destination)
        try fsyncDirectory(identifierRoot)
        let installedVerification = verifyPack(
            pack,
            catalogDigest: catalogDigest,
            at: destination
        )
        guard installedVerification.isReady else {
            throw StoreError.packVerificationFailed(
                installedVerification.problem
                    ?? "The published pack failed deterministic verification."
            )
        }
        try removeResumeDirectory(for: pack)
        return destination
    }

    /// Remove only an invalid same-version pack so the explicit native Retry
    /// action can reacquire it. A valid accepted pack is never removed.
    func removeInvalidInstalledPack(
        _ pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws {
        let root = try installedPackURL(pack)
        guard try pathEntryExists(root) else { return }
        let existing = verifyPack(
            pack,
            catalogDigest: catalogDigest,
            at: root
        )
        guard !existing.isReady else {
            throw StoreError.packAlreadyExists(
                "The same-version pack is already installed and verified."
            )
        }
        try makeInstalledTreeWritableForRemoval(root)
        try removeOwnedItem(root)

        let identifierRoot = root.deletingLastPathComponent()
        if (try? directoryIsEmpty(identifierRoot)) == true {
            try removeOwnedItem(identifierRoot)
        }
        try fsyncDirectory(packsRoot)
    }

    func verifyInstalledPack(
        _ pack: ModelDeliveryPack,
        catalogDigest: String
    ) -> ModelPackVerificationResult {
        guard let root = try? installedPackURL(pack) else {
            return .rejected("The installed pack identity is invalid.")
        }
        do {
            guard try pathEntryExists(root) else {
                return .rejected("not-installed")
            }
        } catch {
            return .rejected("The installed model namespace is unsafe.")
        }
        return verifyPack(pack, catalogDigest: catalogDigest, at: root)
    }

    func discardStaging(
        for pack: ModelDeliveryPack,
        catalogDigest: String
    ) throws {
        let root = try stagingPackURL(pack, catalogDigest: catalogDigest)
        try makeTreeWritable(root)
        try removeOwnedItem(root)
        try removeResumeDirectory(for: pack)
    }

    private func deterministicMetadata(
        for pack: ModelDeliveryPack
    ) throws -> DeterministicPackMetadata {
        let authority = pack.authority
        guard pack.contract.isExactSupportedContract,
              authority.delivery == "native-download",
              authority.packID == pack.id,
              authority.packVersion == pack.version,
              let packageRelativePath = authority.packRelativePath,
              ModelDeliveryCatalog.isSafeRelativePath(packageRelativePath),
              authority.files.count == pack.files.count,
              pack.modelKeys == [authority.key],
              pack.licenseExpressions == [authority.licenseExpression],
              let license = pack.contract.licenses[
                  authority.licenseExpression
              ],
              let embeddedSBOM = authority.packSBOM
        else {
            throw StoreError.invalidPackAuthority
        }
        for file in authority.files {
            let expectedPath = "\(packageRelativePath)/\(file.relativePath)"
            guard let delivered = pack.files.first(where: {
                $0.relativePath == expectedPath
            }),
                  delivered.expectedSize == file.expectedSize,
                  delivered.sha256 == file.sha256,
                  delivered.sourceURL == file.sourceURL
            else {
                throw StoreError.invalidPackAuthority
            }
        }

        guard let expectedSBOM = authority.expectedPackSBOM else {
            throw StoreError.invalidPackAuthority
        }
        guard embeddedSBOM == expectedSBOM else {
            throw StoreError.invalidPackAuthority
        }

        let licenseData = try bundledLicenseData(
            filename: license.filename,
            expectedSHA256: license.sha256
        )
        let licensePath =
            "\(pack.contract.filenames.licensesDirectory)/\(license.filename)"
        let licenseRecord = PackFileRecord(
            path: licensePath,
            size: Int64(licenseData.count),
            sha256: Self.sha256(licenseData)
        )

        let noticesData = Data(notices(for: authority).utf8)
        let noticesRecord = PackFileRecord(
            path: pack.contract.filenames.notices,
            size: Int64(noticesData.count),
            sha256: Self.sha256(noticesData)
        )

        let sbomData = try Self.canonicalData(expectedSBOM)
        let sbomRecord = PackFileRecord(
            path: pack.contract.filenames.sbom,
            size: Int64(sbomData.count),
            sha256: Self.sha256(sbomData)
        )

        let provenanceValue = provenancePayload(
            for: pack,
            sbomRecord: sbomRecord
        )
        let provenanceData = try Self.canonicalData(provenanceValue)
        let provenanceRecord = PackFileRecord(
            path: pack.contract.filenames.provenance,
            size: Int64(provenanceData.count),
            sha256: Self.sha256(provenanceData)
        )

        let modelRecords = pack.files.map {
            PackFileRecord(
                path: $0.relativePath,
                size: $0.expectedSize,
                sha256: $0.sha256
            )
        }.sorted(by: { $0.path < $1.path })
        var payloadRecords = modelRecords + [
            licenseRecord,
            noticesRecord,
            sbomRecord,
            provenanceRecord,
        ]
        payloadRecords.sort(by: { $0.path < $1.path })
        let payloadSize = try exactSize(payloadRecords)
        let payloadSHA256 = Self.sha256(
            try Self.canonicalData(
                .object([
                    "files": .array(payloadRecords.map(\.jsonValue)),
                ])
            )
        )

        let manifestValue = manifestPayload(
            for: pack,
            modelRecords: modelRecords,
            licenseRecord: licenseRecord,
            noticesRecord: noticesRecord,
            sbomRecord: sbomRecord,
            provenanceRecord: provenanceRecord,
            payloadFileCount: payloadRecords.count,
            payloadSize: payloadSize,
            payloadSHA256: payloadSHA256
        )
        let manifestData = try Self.canonicalData(manifestValue)
        let manifestSHA256 = Self.sha256(manifestData)

        let verificationValue = verificationPayload(
            for: pack,
            manifestSHA256: manifestSHA256,
            sbomSHA256: sbomRecord.sha256,
            payloadSHA256: payloadSHA256,
            payloadFileCount: payloadRecords.count,
            payloadSize: payloadSize
        )
        let verificationData = try Self.canonicalData(verificationValue)
        let verificationSHA256 = Self.sha256(verificationData)
        let completionData = try Self.canonicalData(
            completionPayload(
                for: pack,
                manifestSHA256: manifestSHA256,
                verificationSHA256: verificationSHA256
            )
        )
        return DeterministicPackMetadata(
            files: [
                licensePath: licenseData,
                pack.contract.filenames.notices: noticesData,
                pack.contract.filenames.sbom: sbomData,
                pack.contract.filenames.provenance: provenanceData,
                pack.contract.filenames.manifest: manifestData,
                pack.contract.filenames.verification: verificationData,
                pack.contract.filenames.completion: completionData,
            ]
        )
    }

    private func writeDeterministicMetadata(
        _ metadata: DeterministicPackMetadata,
        under root: URL
    ) throws {
        for relativePath in metadata.files.keys.sorted() {
            guard ModelDeliveryCatalog.isSafeRelativePath(relativePath),
                  let data = metadata.files[relativePath]
            else {
                throw StoreError.unsafePath
            }
            let destination = root.appendingPathComponent(relativePath)
            let normalizedDestination = Self.lexicallyNormalizedFileURL(
                destination
            )
            guard normalizedDestination.path.hasPrefix(root.path + "/") else {
                throw StoreError.unsafePath
            }
            try createPrivateDirectory(
                normalizedDestination.deletingLastPathComponent()
            )
            try writeMetadata(
                data,
                named: normalizedDestination.lastPathComponent,
                under: normalizedDestination.deletingLastPathComponent()
            )
        }
    }

    private func bundledLicenseData(
        filename: String,
        expectedSHA256: String
    ) throws -> Data {
        let path = filename as NSString
        guard let url = licenseBundle.url(
            forResource: path.deletingPathExtension,
            withExtension: path.pathExtension
        ),
              let data = try? Data(contentsOf: url, options: [.mappedIfSafe]),
              !data.isEmpty,
              data.count <= 4 * 1024 * 1024,
              Self.sha256(data) == expectedSHA256
        else {
            throw StoreError.invalidLicenseResource
        }
        return data
    }

    private func notices(for authority: ModelDeliveryModel) -> String {
        [
            "Auralign Native-Download Model Pack",
            "",
            "Model: \(authority.modelID)",
            "Model key: \(authority.key)",
            "Immutable revision: \(authority.revision)",
            "License: \(authority.licenseExpression)",
            "Source: \(authority.sourceURL.absoluteString)",
            "Terms: \(authority.termsURL.absoluteString)",
            "Attribution: \(authority.attribution)",
            "Modification status: \(authority.modificationStatus)",
            "",
            "This pack was assembled locally from exact verified files.",
            "It contains no authentication token or private acquisition metadata.",
            "",
        ].joined(separator: "\n")
    }

    private func provenancePayload(
        for pack: ModelDeliveryPack,
        sbomRecord: PackFileRecord
    ) -> CanonicalJSONValue {
        let authority = pack.authority
        return .object([
            "schema_version": .string(Self.schemaVersion),
            "record_type": .string("auralign-model-pack-provenance"),
            "authority": .string(
                "supplied-local_transcript_max.config.ModelEntry"
            ),
            "bundle_id": .string(pack.id),
            "bundle_version": .string(pack.version),
            "package_relative_path": .string(authority.packRelativePath!),
            "sbom": .object([
                "path": .string(sbomRecord.path),
                "size": .integer(sbomRecord.size),
                "sha256": .string(sbomRecord.sha256),
                "bom_format": .string(pack.contract.sbom.bomFormat),
                "spec_version": .string(pack.contract.sbom.specVersion),
            ]),
            "model": .object([
                "key": .string(authority.key),
                "role": .string(authority.role),
                "model_id": .string(authority.modelID),
                "aliases": .array(
                    authority.aliases.map(CanonicalJSONValue.string)
                ),
                "directory_aliases": .array(
                    authority.directoryAliases.map(CanonicalJSONValue.string)
                ),
                "revision": .string(authority.revision),
                "gated": .boolean(authority.gated),
                "license_expression": .string(
                    authority.licenseExpression
                ),
                "source_url": .string(authority.sourceURL.absoluteString),
                "terms_url": .string(authority.termsURL.absoluteString),
                "attribution": .string(authority.attribution),
                "acquisition_status": .string(
                    authority.acquisitionStatus
                ),
                "runtime_status": .string(authority.runtimeStatus),
                "redistribution_status": .string(
                    authority.redistributionStatus
                ),
                "review_status": .string(authority.reviewStatus),
                "reviewed_at": authority.reviewedAt.map(
                    CanonicalJSONValue.string
                ) ?? .null,
                "product_classification": .string(
                    authority.productClassification
                ),
                "backend_compatibility": .string(
                    authority.backendCompatibility
                ),
                "modification_status": .string(
                    authority.modificationStatus
                ),
                "delivery": .string(authority.delivery),
                "required_files": .array(
                    authority.files.map {
                        .object([
                            "path": .string($0.relativePath),
                            "size": .integer($0.expectedSize),
                            "sha256": .string($0.sha256),
                        ])
                    }
                ),
            ]),
        ])
    }

    private func manifestPayload(
        for pack: ModelDeliveryPack,
        modelRecords: [PackFileRecord],
        licenseRecord: PackFileRecord,
        noticesRecord: PackFileRecord,
        sbomRecord: PackFileRecord,
        provenanceRecord: PackFileRecord,
        payloadFileCount: Int,
        payloadSize: Int64,
        payloadSHA256: String
    ) -> CanonicalJSONValue {
        let authority = pack.authority
        return .object([
            "schema_version": .string(Self.schemaVersion),
            "layout_version": .string(Self.layoutVersion),
            "record_type": .string("auralign-model-pack-manifest"),
            "bundle_id": .string(pack.id),
            "bundle_version": .string(pack.version),
            "package_relative_path": .string(authority.packRelativePath!),
            "filesystem_contract": .object([
                "directory_mode": .string(
                    pack.contract.filesystem.directoryMode
                ),
                "file_mode": .string(pack.contract.filesystem.fileMode),
                "staging_root_mode": .string(
                    pack.contract.filesystem.stagingRootMode
                ),
                "published_root_mode": .string(
                    pack.contract.filesystem.publishedRootMode
                ),
                "ownership": .string("current-effective-user"),
                "regular_file_link_count": .integer(1),
                "single_filesystem": .boolean(true),
                "symbolic_links": .string("forbidden"),
                "publication": .string(
                    pack.contract.filesystem.publication
                ),
            ]),
            "model": .object([
                "key": .string(authority.key),
                "role": .string(authority.role),
                "model_id": .string(authority.modelID),
                "aliases": .array(
                    authority.aliases.map(CanonicalJSONValue.string)
                ),
                "revision": .string(authority.revision),
                "license_expression": .string(
                    authority.licenseExpression
                ),
                "product_classification": .string(
                    authority.productClassification
                ),
                "backend_compatibility": .string(
                    authority.backendCompatibility
                ),
                "runtime_status": .string(authority.runtimeStatus),
                "review_status": .string(authority.reviewStatus),
                "modification_status": .string(
                    authority.modificationStatus
                ),
                "delivery": .string(authority.delivery),
                "files": .array(modelRecords.map(\.jsonValue)),
            ]),
            "compliance": .object([
                "license": .object([
                    "license_expression": .string(
                        authority.licenseExpression
                    ),
                    "file": licenseRecord.jsonValue,
                ]),
                "notices": noticesRecord.jsonValue,
                "sbom": sbomRecord.jsonValue,
                "provenance": provenanceRecord.jsonValue,
            ]),
            "payload": .object([
                "file_count": .integer(Int64(payloadFileCount)),
                "size": .integer(payloadSize),
                "sha256": .string(payloadSHA256),
            ]),
        ])
    }

    private func verificationPayload(
        for pack: ModelDeliveryPack,
        manifestSHA256: String,
        sbomSHA256: String,
        payloadSHA256: String,
        payloadFileCount: Int,
        payloadSize: Int64
    ) -> CanonicalJSONValue {
        let authority = pack.authority
        return .object([
            "schema_version": .string(Self.schemaVersion),
            "record_type": .string("auralign-model-pack-verification"),
            "bundle_id": .string(pack.id),
            "bundle_version": .string(pack.version),
            "package_relative_path": .string(authority.packRelativePath!),
            "state": .string("verified"),
            "model_key": .string(authority.key),
            "model_id": .string(authority.modelID),
            "revision": .string(authority.revision),
            "manifest_sha256": .string(manifestSHA256),
            "sbom_sha256": .string(sbomSHA256),
            "payload_sha256": .string(payloadSHA256),
            "payload_file_count": .integer(Int64(payloadFileCount)),
            "payload_size": .integer(payloadSize),
            "checks": .array(
                Self.verificationChecks.map(CanonicalJSONValue.string)
            ),
        ])
    }

    private func completionPayload(
        for pack: ModelDeliveryPack,
        manifestSHA256: String,
        verificationSHA256: String
    ) -> CanonicalJSONValue {
        .object([
            "schema_version": .string(Self.schemaVersion),
            "record_type": .string("auralign-model-pack-completion"),
            "bundle_id": .string(pack.id),
            "bundle_version": .string(pack.version),
            "package_relative_path": .string(
                pack.authority.packRelativePath!
            ),
            "model_key": .string(pack.authority.key),
            "revision": .string(pack.authority.revision),
            "state": .string("complete"),
            "manifest_sha256": .string(manifestSHA256),
            "verification_record_sha256": .string(
                verificationSHA256
            ),
        ])
    }

    private func exactSize(_ records: [PackFileRecord]) throws -> Int64 {
        var result: Int64 = 0
        for record in records {
            let addition = result.addingReportingOverflow(record.size)
            guard !addition.overflow else {
                throw StoreError.invalidPackAuthority
            }
            result = addition.partialValue
        }
        return result
    }

    private func verifyPack(
        _ pack: ModelDeliveryPack,
        catalogDigest: String,
        at root: URL,
        acceptedRootMode: mode_t = 0o500
    ) -> ModelPackVerificationResult {
        guard isDirectory(
            root,
            owner: expectedOwnerUID,
            mode: acceptedRootMode
        ) else {
            return .rejected("The pack root is missing or has unsafe permissions.")
        }
        _ = catalogDigest
        guard pack.contract.isExactSupportedContract,
              let metadata = try? deterministicMetadata(for: pack)
        else {
            return .rejected(
                "The signed model-pack contract is unsupported or malformed."
            )
        }
        var expectedFiles = Set(pack.files.map(\.relativePath))
        expectedFiles.formUnion(metadata.files.keys)
        let expectedDirectories = expectedDirectoryPaths(
            for: expectedFiles,
            root: root
        )
        if let treeProblem = exactTreeProblem(
            root: root,
            expectedFiles: expectedFiles,
            expectedDirectories: expectedDirectories
        ) {
            return .rejected(
                "The pack contains missing, linked, or unexpected content: "
                    + treeProblem
            )
        }
        for (relativePath, expectedData) in metadata.files {
            let url = root.appendingPathComponent(relativePath)
            guard let actualData = trustedMetadataData(url),
                  actualData == expectedData
            else {
                return .rejected(
                    "Pack metadata is non-canonical or inconsistent with "
                        + "the signed model authority: \(relativePath)"
                )
            }
        }
        for file in pack.files {
            let url = root.appendingPathComponent(file.relativePath)
            guard verifyPayloadFile(
                at: url,
                expectedSize: file.expectedSize,
                expectedSHA256: file.sha256,
                requiresReadOnlyMode: true
            ) else {
                return .rejected("A model file failed exact size or SHA-256 verification.")
            }
        }
        if let treeProblem = exactTreeProblem(
            root: root,
            expectedFiles: expectedFiles,
            expectedDirectories: expectedDirectories
        ) {
            return .rejected(
                "The pack changed during verification: " + treeProblem
            )
        }
        return .ready
    }

    private func exactTreeProblem(
        root: URL,
        expectedFiles: Set<String>,
        expectedDirectories: Set<String>
    ) -> String? {
        do {
            let chain = try openDirectoryChain(root, createMissing: false)
            defer { closeDirectoryChain(chain) }
            guard chainIsStable(chain) else {
                return "the pack namespace changed while it was opened"
            }
            var rootInformation = stat()
            guard fstat(chain.leafDescriptor, &rootInformation) == 0,
                  (rootInformation.st_mode & S_IFMT) == S_IFDIR,
                  rootInformation.st_uid == expectedOwnerUID,
                  rootInformation.st_gid == expectedOwnerGID
            else {
                return "the pack root could not be inspected safely"
            }
            var observedFiles: Set<String> = []
            var observedDirectories: Set<String> = []
            try scanPackTree(
                descriptor: chain.leafDescriptor,
                relativePrefix: "",
                rootDevice: rootInformation.st_dev,
                observedFiles: &observedFiles,
                observedDirectories: &observedDirectories
            )
            guard chainIsStable(chain) else {
                return "the pack namespace changed during enumeration"
            }
            guard observedFiles == expectedFiles else {
                return "file allowlist mismatch; missing="
                    + expectedFiles.subtracting(observedFiles).sorted()
                        .joined(separator: ",")
                    + "; unexpected="
                    + observedFiles.subtracting(expectedFiles).sorted()
                        .joined(separator: ",")
            }
            guard observedDirectories == expectedDirectories else {
                return "directory allowlist mismatch; missing="
                    + expectedDirectories.subtracting(observedDirectories)
                        .sorted().joined(separator: ",")
                    + "; unexpected="
                    + observedDirectories.subtracting(expectedDirectories)
                        .sorted().joined(separator: ",")
            }
            return nil
        } catch {
            return "the pack tree could not be enumerated without following links"
        }
    }

    private func expectedDirectoryPaths(
        for files: Set<String>,
        root: URL
    ) -> Set<String> {
        _ = root
        var result: Set<String> = []
        for file in files {
            var components = file.split(separator: "/").map(String.init)
            _ = components.popLast()
            while !components.isEmpty {
                result.insert(components.joined(separator: "/"))
                _ = components.popLast()
            }
        }
        return result
    }

    private func verifyPayloadFile(
        at url: URL,
        expectedSize: Int64,
        expectedSHA256: String,
        requiresReadOnlyMode: Bool
    ) -> Bool {
        stableFileDigest(
            at: url,
            expectedSize: expectedSize,
            expectedMode: requiresReadOnlyMode ? 0o400 : nil
        ) == expectedSHA256
    }

    private func trustedMetadataData(_ url: URL) -> Data? {
        stableFileData(
            at: url,
            expectedMode: 0o400,
            maximumSize: 4 * 1024 * 1024,
            requireNonEmpty: true,
            requirePrivateMode: false
        )
    }

    private func resumeDataURL(
        for file: ModelDeliveryFile,
        pack: ModelDeliveryPack
    ) throws -> URL {
        try validateIdentity(pack)
        guard pack.files.contains(file) else { throw StoreError.unknownFile }
        let fileName = Self.sha256(Data(file.id.utf8)) + ".resume"
        return resumeRoot
            .appendingPathComponent(pack.id, isDirectory: true)
            .appendingPathComponent(pack.version, isDirectory: true)
            .appendingPathComponent(fileName)
    }

    private func removeResumeDirectory(for pack: ModelDeliveryPack) throws {
        try validateIdentity(pack)
        let identifier = resumeRoot.appendingPathComponent(
            pack.id,
            isDirectory: true
        )
        let version = identifier.appendingPathComponent(
            pack.version,
            isDirectory: true
        )
        try removeOwnedItem(version)
        if (try? directoryIsEmpty(identifier)) == true {
            try removeOwnedItem(identifier)
        }
    }

    private func validateIdentity(_ pack: ModelDeliveryPack) throws {
        guard ModelDeliveryCatalog.isSafeIdentifier(pack.id),
              ModelDeliveryCatalog.isSafeIdentifier(pack.version)
        else {
            throw StoreError.unsafePath
        }
    }

    private struct DirectoryChain {
        let descriptors: [Int32]
        let names: [String]

        var leafDescriptor: Int32 {
            descriptors[descriptors.count - 1]
        }
    }

    private func createPrivateDirectory(_ url: URL) throws {
        let chain = try openDirectoryChain(url, createMissing: true)
        defer { closeDirectoryChain(chain) }
        guard fchmod(chain.leafDescriptor, 0o700) == 0 else {
            throw StoreError.couldNotSecureState
        }
        var information = stat()
        guard fstat(chain.leafDescriptor, &information) == 0,
              (information.st_mode & S_IFMT) == S_IFDIR,
              information.st_uid == expectedOwnerUID,
              information.st_gid == expectedOwnerGID,
              (information.st_mode & 0o777) == 0o700,
              chainIsStable(chain)
        else {
            throw StoreError.couldNotSecureState
        }
    }

    private func writeMetadata(_ data: Data, named name: String, under root: URL) throws {
        let destination = root.appendingPathComponent(name)
        try data.write(to: destination, options: [.atomic])
        guard chmod(destination.path, 0o600) == 0 else {
            throw StoreError.couldNotSecureState
        }
    }

    private func makeTreeReadOnly(
        _ root: URL,
        sealRoot: Bool = true
    ) throws {
        try setTreeModes(
            root,
            rootMode: sealRoot ? 0o500 : 0o700,
            directoryMode: 0o500,
            fileMode: 0o400,
            rejectSymbolicLinks: true
        )
    }

    private func makeTreeWritable(_ root: URL) throws {
        guard isDescendant(root, of: stagingRoot) else {
            throw StoreError.unsafePath
        }
        guard try pathEntryExists(root) else { return }
        try setTreeModes(
            root,
            rootMode: 0o700,
            directoryMode: 0o700,
            fileMode: 0o600,
            rejectSymbolicLinks: false
        )
    }

    private func makeInstalledTreeWritableForRemoval(_ root: URL) throws {
        guard isDescendant(root, of: packsRoot) else {
            throw StoreError.unsafePath
        }
        try setTreeModes(
            root,
            rootMode: 0o700,
            directoryMode: 0o700,
            fileMode: 0o600,
            rejectSymbolicLinks: false
        )
    }

    private func removeOwnedItem(_ url: URL) throws {
        guard isDescendant(url, of: modelsRoot), url != modelsRoot else {
            throw StoreError.unsafePath
        }
        let parent = url.deletingLastPathComponent()
        let name = url.lastPathComponent
        guard validLeafName(name) else {
            throw StoreError.unsafePath
        }
        let chain: DirectoryChain
        do {
            chain = try openDirectoryChain(parent, createMissing: false)
        } catch let error as POSIXError where error.code == .ENOENT {
            return
        }
        defer { closeDirectoryChain(chain) }
        guard chainIsStable(chain) else {
            throw StoreError.couldNotSecureState
        }
        var information = stat()
        let lookup = fstatatLeaf(
            chain.leafDescriptor,
            name: name,
            into: &information,
            flags: AT_SYMLINK_NOFOLLOW
        )
        if lookup != 0 {
            if errno == ENOENT { return }
            throw StoreError.couldNotSecureState
        }
        try removeEntry(
            parentDescriptor: chain.leafDescriptor,
            name: name,
            expectedDevice: information.st_dev
        )
        guard chainIsStable(chain) else {
            throw StoreError.couldNotSecureState
        }
    }

    private func pathEntryExists(_ url: URL) throws -> Bool {
        guard isDescendant(url, of: modelsRoot), url != modelsRoot else {
            throw StoreError.unsafePath
        }
        let parent = url.deletingLastPathComponent()
        let name = url.lastPathComponent
        guard validLeafName(name) else {
            throw StoreError.unsafePath
        }
        let chain: DirectoryChain
        do {
            chain = try openDirectoryChain(parent, createMissing: false)
        } catch let error as POSIXError where error.code == .ENOENT {
            return false
        }
        defer { closeDirectoryChain(chain) }
        var information = stat()
        let result = fstatatLeaf(
            chain.leafDescriptor,
            name: name,
            into: &information,
            flags: AT_SYMLINK_NOFOLLOW
        )
        if result != 0 {
            if errno == ENOENT { return false }
            throw StoreError.couldNotSecureState
        }
        guard chainIsStable(chain) else {
            throw StoreError.couldNotSecureState
        }
        return true
    }

    private func isDescendant(_ candidate: URL, of ancestor: URL) -> Bool {
        let candidateComponents = Self.canonicalSystemAliasURL(candidate)
            .pathComponents
        let ancestorComponents = Self.canonicalSystemAliasURL(ancestor)
            .pathComponents
        return candidateComponents.count > ancestorComponents.count
            && Array(candidateComponents.prefix(ancestorComponents.count))
                == ancestorComponents
    }

    private func isDirectory(_ url: URL, owner: uid_t, mode: mode_t) -> Bool {
        do {
            let chain = try openDirectoryChain(url, createMissing: false)
            defer { closeDirectoryChain(chain) }
            var information = stat()
            return fstat(chain.leafDescriptor, &information) == 0
                && (information.st_mode & S_IFMT) == S_IFDIR
                && information.st_uid == owner
                && information.st_gid == expectedOwnerGID
                && (information.st_mode & 0o777) == mode
                && chainIsStable(chain)
        } catch {
            return false
        }
    }

    private func openDirectoryChain(
        _ url: URL,
        createMissing: Bool
    ) throws -> DirectoryChain {
        let normalized = Self.canonicalSystemAliasURL(url)
        guard normalized.isFileURL,
              normalized.pathComponents.first == "/",
              normalized == modelsRoot || isDescendant(normalized, of: modelsRoot)
        else {
            throw StoreError.unsafePath
        }
        let rootComponents = modelsRoot.pathComponents
        let targetComponents = normalized.pathComponents
        guard targetComponents.count >= rootComponents.count,
              Array(targetComponents.prefix(rootComponents.count)) == rootComponents
        else {
            throw StoreError.unsafePath
        }

        let rootDescriptor = Darwin.open(
            "/",
            O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
        )
        guard rootDescriptor >= 0 else {
            throw StoreError.couldNotSecureState
        }
        var descriptors = [rootDescriptor]
        var names: [String] = []
        do {
            for (offset, name) in targetComponents.dropFirst().enumerated() {
                guard validLeafName(name) else {
                    throw StoreError.unsafePath
                }
                let parentDescriptor = descriptors[descriptors.count - 1]
                var descriptor = openatLeaf(
                    parentDescriptor,
                    name: name,
                    flags: O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
                )
                if descriptor < 0, errno == ENOENT, createMissing {
                    var parentStatus = stat()
                    guard fstat(parentDescriptor, &parentStatus) == 0,
                          parentStatus.st_uid == expectedOwnerUID,
                          (parentStatus.st_mode & 0o022) == 0,
                          mkdiratLeaf(parentDescriptor, name: name, mode: 0o700) == 0
                    else {
                        throw StoreError.couldNotSecureState
                    }
                    descriptor = openatLeaf(
                        parentDescriptor,
                        name: name,
                        flags: O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
                    )
                }
                guard descriptor >= 0 else {
                    let code = errno
                    throw POSIXError(
                        POSIXErrorCode(rawValue: code) ?? .EIO
                    )
                }
                descriptors.append(descriptor)
                names.append(name)

                var lookupStatus = stat()
                var descriptorStatus = stat()
                guard fstatatLeaf(
                    parentDescriptor,
                    name: name,
                    into: &lookupStatus,
                    flags: AT_SYMLINK_NOFOLLOW
                ) == 0,
                      fstat(descriptor, &descriptorStatus) == 0,
                      sameEntryIdentity(lookupStatus, descriptorStatus),
                      (descriptorStatus.st_mode & S_IFMT) == S_IFDIR,
                      descriptorStatus.st_uid == 0
                          || descriptorStatus.st_uid == expectedOwnerUID
                else {
                    throw StoreError.couldNotSecureState
                }

                let componentCount = offset + 2
                let isInsideModelNamespace = componentCount >= rootComponents.count
                if descriptorStatus.st_uid == expectedOwnerUID,
                   (descriptorStatus.st_mode & 0o022) != 0 {
                    throw StoreError.couldNotSecureState
                }
                if isInsideModelNamespace {
                    guard descriptorStatus.st_uid == expectedOwnerUID,
                          descriptorStatus.st_gid == expectedOwnerGID,
                          (descriptorStatus.st_mode & 0o022) == 0
                    else {
                        throw StoreError.couldNotSecureState
                    }
                }
            }
            let chain = DirectoryChain(descriptors: descriptors, names: names)
            guard chainIsStable(chain) else {
                throw StoreError.couldNotSecureState
            }
            return chain
        } catch {
            for descriptor in descriptors.reversed() {
                Darwin.close(descriptor)
            }
            throw error
        }
    }

    private func closeDirectoryChain(_ chain: DirectoryChain) {
        for descriptor in chain.descriptors.reversed() {
            Darwin.close(descriptor)
        }
    }

    private func chainIsStable(_ chain: DirectoryChain) -> Bool {
        guard chain.descriptors.count == chain.names.count + 1 else {
            return false
        }
        for index in chain.names.indices {
            var lookupStatus = stat()
            var descriptorStatus = stat()
            guard fstatatLeaf(
                chain.descriptors[index],
                name: chain.names[index],
                into: &lookupStatus,
                flags: AT_SYMLINK_NOFOLLOW
            ) == 0,
                  fstat(chain.descriptors[index + 1], &descriptorStatus) == 0,
                  sameEntryIdentity(lookupStatus, descriptorStatus),
                  (descriptorStatus.st_mode & S_IFMT) == S_IFDIR
            else {
                return false
            }
        }
        return true
    }

    private func directoryIsEmpty(_ url: URL) throws -> Bool {
        let chain = try openDirectoryChain(url, createMissing: false)
        defer { closeDirectoryChain(chain) }
        let names = try directoryEntryNames(chain.leafDescriptor)
        guard chainIsStable(chain) else {
            throw StoreError.couldNotSecureState
        }
        return names.isEmpty
    }

    private func scanPackTree(
        descriptor: Int32,
        relativePrefix: String,
        rootDevice: dev_t,
        observedFiles: inout Set<String>,
        observedDirectories: inout Set<String>
    ) throws {
        var before = stat()
        guard fstat(descriptor, &before) == 0,
              (before.st_mode & S_IFMT) == S_IFDIR,
              before.st_uid == expectedOwnerUID,
              before.st_gid == expectedOwnerGID,
              before.st_dev == rootDevice
        else {
            throw StoreError.couldNotSecureState
        }
        for name in try directoryEntryNames(descriptor) {
            var lookupStatus = stat()
            guard fstatatLeaf(
                descriptor,
                name: name,
                into: &lookupStatus,
                flags: AT_SYMLINK_NOFOLLOW
            ) == 0,
                  lookupStatus.st_uid == expectedOwnerUID,
                  lookupStatus.st_gid == expectedOwnerGID,
                  lookupStatus.st_dev == rootDevice
            else {
                throw StoreError.couldNotSecureState
            }
            let relative = relativePrefix.isEmpty
                ? name : "\(relativePrefix)/\(name)"
            switch lookupStatus.st_mode & S_IFMT {
            case S_IFDIR:
                guard (lookupStatus.st_mode & 0o777) == 0o500 else {
                    throw StoreError.couldNotSecureState
                }
                let child = openatLeaf(
                    descriptor,
                    name: name,
                    flags: O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
                )
                guard child >= 0 else {
                    throw StoreError.couldNotSecureState
                }
                defer { Darwin.close(child) }
                var childStatus = stat()
                guard fstat(child, &childStatus) == 0,
                      sameEntryIdentity(lookupStatus, childStatus)
                else {
                    throw StoreError.couldNotSecureState
                }
                observedDirectories.insert(relative)
                try scanPackTree(
                    descriptor: child,
                    relativePrefix: relative,
                    rootDevice: rootDevice,
                    observedFiles: &observedFiles,
                    observedDirectories: &observedDirectories
                )
            case S_IFREG:
                guard lookupStatus.st_nlink == 1,
                      (lookupStatus.st_mode & 0o777) == 0o400
                else {
                    throw StoreError.couldNotSecureState
                }
                observedFiles.insert(relative)
            default:
                throw StoreError.unexpectedContent
            }
        }
        var after = stat()
        guard fstat(descriptor, &after) == 0,
              sameStableStatus(before, after)
        else {
            throw StoreError.couldNotSecureState
        }
    }

    private func setTreeModes(
        _ root: URL,
        rootMode: mode_t,
        directoryMode: mode_t,
        fileMode: mode_t,
        rejectSymbolicLinks: Bool
    ) throws {
        let chain = try openDirectoryChain(root, createMissing: false)
        defer { closeDirectoryChain(chain) }
        var rootStatus = stat()
        guard fstat(chain.leafDescriptor, &rootStatus) == 0,
              (rootStatus.st_mode & S_IFMT) == S_IFDIR,
              rootStatus.st_uid == expectedOwnerUID,
              rootStatus.st_gid == expectedOwnerGID
        else {
            throw StoreError.couldNotSecureState
        }
        if (rootMode & 0o200) != 0 {
            guard fchmod(chain.leafDescriptor, rootMode) == 0 else {
                throw StoreError.couldNotSecureState
            }
        }
        try setTreeModes(
            descriptor: chain.leafDescriptor,
            rootDevice: rootStatus.st_dev,
            directoryMode: directoryMode,
            fileMode: fileMode,
            rejectSymbolicLinks: rejectSymbolicLinks
        )
        guard fchmod(chain.leafDescriptor, rootMode) == 0,
              fsync(chain.leafDescriptor) == 0,
              chainIsStable(chain)
        else {
            throw StoreError.couldNotSecureState
        }
    }

    private func setTreeModes(
        descriptor: Int32,
        rootDevice: dev_t,
        directoryMode: mode_t,
        fileMode: mode_t,
        rejectSymbolicLinks: Bool
    ) throws {
        for name in try directoryEntryNames(descriptor) {
            var lookupStatus = stat()
            guard fstatatLeaf(
                descriptor,
                name: name,
                into: &lookupStatus,
                flags: AT_SYMLINK_NOFOLLOW
            ) == 0,
                  lookupStatus.st_uid == expectedOwnerUID,
                  lookupStatus.st_gid == expectedOwnerGID,
                  lookupStatus.st_dev == rootDevice
            else {
                throw StoreError.couldNotSecureState
            }
            switch lookupStatus.st_mode & S_IFMT {
            case S_IFDIR:
                let child = openatLeaf(
                    descriptor,
                    name: name,
                    flags: O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
                )
                guard child >= 0 else {
                    throw StoreError.couldNotSecureState
                }
                defer { Darwin.close(child) }
                var childStatus = stat()
                guard fstat(child, &childStatus) == 0,
                      sameEntryIdentity(lookupStatus, childStatus)
                else {
                    throw StoreError.couldNotSecureState
                }
                if (directoryMode & 0o200) != 0 {
                    guard fchmod(child, directoryMode) == 0 else {
                        throw StoreError.couldNotSecureState
                    }
                }
                try setTreeModes(
                    descriptor: child,
                    rootDevice: rootDevice,
                    directoryMode: directoryMode,
                    fileMode: fileMode,
                    rejectSymbolicLinks: rejectSymbolicLinks
                )
                guard fchmod(child, directoryMode) == 0 else {
                    throw StoreError.couldNotSecureState
                }
                var pathStatus = stat()
                var sealedStatus = stat()
                guard fstatatLeaf(
                    descriptor,
                    name: name,
                    into: &pathStatus,
                    flags: AT_SYMLINK_NOFOLLOW
                ) == 0,
                      fstat(child, &sealedStatus) == 0,
                      sameEntryIdentity(pathStatus, sealedStatus),
                      (sealedStatus.st_mode & 0o777) == directoryMode
                else {
                    throw StoreError.couldNotSecureState
                }
            case S_IFREG:
                guard lookupStatus.st_nlink == 1 else {
                    throw StoreError.couldNotSecureState
                }
                let file = openatLeaf(
                    descriptor,
                    name: name,
                    flags: O_RDONLY | O_NOFOLLOW | O_CLOEXEC
                )
                guard file >= 0 else {
                    throw StoreError.couldNotSecureState
                }
                defer { Darwin.close(file) }
                var openedStatus = stat()
                guard fstat(file, &openedStatus) == 0,
                      sameEntryIdentity(lookupStatus, openedStatus),
                      openedStatus.st_nlink == 1,
                      fchmod(file, fileMode) == 0
                else {
                    throw StoreError.couldNotSecureState
                }
                var pathStatus = stat()
                var sealedStatus = stat()
                guard fstatatLeaf(
                    descriptor,
                    name: name,
                    into: &pathStatus,
                    flags: AT_SYMLINK_NOFOLLOW
                ) == 0,
                      fstat(file, &sealedStatus) == 0,
                      sameEntryIdentity(pathStatus, sealedStatus),
                      (sealedStatus.st_mode & 0o777) == fileMode
                else {
                    throw StoreError.couldNotSecureState
                }
            case S_IFLNK where !rejectSymbolicLinks:
                continue
            default:
                throw StoreError.unexpectedContent
            }
        }
    }

    private func removeEntry(
        parentDescriptor: Int32,
        name: String,
        expectedDevice: dev_t
    ) throws {
        var lookupStatus = stat()
        guard fstatatLeaf(
            parentDescriptor,
            name: name,
            into: &lookupStatus,
            flags: AT_SYMLINK_NOFOLLOW
        ) == 0,
              lookupStatus.st_uid == expectedOwnerUID,
              lookupStatus.st_gid == expectedOwnerGID,
              lookupStatus.st_dev == expectedDevice
        else {
            throw StoreError.couldNotSecureState
        }
        switch lookupStatus.st_mode & S_IFMT {
        case S_IFDIR:
            let child = openatLeaf(
                parentDescriptor,
                name: name,
                flags: O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
            )
            guard child >= 0 else {
                throw StoreError.couldNotSecureState
            }
            defer { Darwin.close(child) }
            var openedStatus = stat()
            guard fstat(child, &openedStatus) == 0,
                  sameEntryIdentity(lookupStatus, openedStatus),
                  fchmod(child, 0o700) == 0
            else {
                throw StoreError.couldNotSecureState
            }
            for childName in try directoryEntryNames(child) {
                try removeEntry(
                    parentDescriptor: child,
                    name: childName,
                    expectedDevice: expectedDevice
                )
            }
            var finalPathStatus = stat()
            var finalDescriptorStatus = stat()
            guard fstatatLeaf(
                parentDescriptor,
                name: name,
                into: &finalPathStatus,
                flags: AT_SYMLINK_NOFOLLOW
            ) == 0,
                  fstat(child, &finalDescriptorStatus) == 0,
                  sameEntryIdentity(finalPathStatus, finalDescriptorStatus),
                  unlinkatLeaf(parentDescriptor, name: name, flags: AT_REMOVEDIR) == 0
            else {
                throw StoreError.couldNotSecureState
            }
        case S_IFREG:
            guard lookupStatus.st_nlink == 1 else {
                throw StoreError.couldNotSecureState
            }
            let file = openatLeaf(
                parentDescriptor,
                name: name,
                flags: O_RDONLY | O_NOFOLLOW | O_CLOEXEC
            )
            guard file >= 0 else {
                throw StoreError.couldNotSecureState
            }
            defer { Darwin.close(file) }
            var openedStatus = stat()
            guard fstat(file, &openedStatus) == 0,
                  sameEntryIdentity(lookupStatus, openedStatus),
                  unlinkatLeaf(parentDescriptor, name: name, flags: 0) == 0
            else {
                throw StoreError.couldNotSecureState
            }
            var removedStatus = stat()
            guard fstat(file, &removedStatus) == 0,
                  removedStatus.st_nlink == 0
            else {
                throw StoreError.couldNotSecureState
            }
        case S_IFLNK:
            guard unlinkatLeaf(parentDescriptor, name: name, flags: 0) == 0 else {
                throw StoreError.couldNotSecureState
            }
        default:
            throw StoreError.unexpectedContent
        }
    }

    private func stableFileDigest(
        at url: URL,
        expectedSize: Int64,
        expectedMode: mode_t?
    ) -> String? {
        withStableRegularFile(
            at: url,
            expectedSize: expectedSize,
            expectedMode: expectedMode,
            maximumSize: nil,
            requireNonEmpty: false,
            requirePrivateMode: false
        ) { descriptor in
            try Self.descriptorSHA256(descriptor)
        }
    }

    private func stableFileData(
        at url: URL,
        expectedMode: mode_t?,
        maximumSize: Int64,
        requireNonEmpty: Bool,
        requirePrivateMode: Bool
    ) -> Data? {
        withStableRegularFile(
            at: url,
            expectedSize: nil,
            expectedMode: expectedMode,
            maximumSize: maximumSize,
            requireNonEmpty: requireNonEmpty,
            requirePrivateMode: requirePrivateMode
        ) { descriptor in
            try Self.descriptorData(descriptor, maximumSize: maximumSize)
        }
    }

    private func withStableRegularFile<Result>(
        at url: URL,
        expectedSize: Int64?,
        expectedMode: mode_t?,
        maximumSize: Int64?,
        requireNonEmpty: Bool,
        requirePrivateMode: Bool,
        body: (Int32) throws -> Result
    ) -> Result? {
        guard isDescendant(url, of: modelsRoot), url != modelsRoot else {
            return nil
        }
        let parent = url.deletingLastPathComponent()
        let name = url.lastPathComponent
        guard validLeafName(name) else { return nil }
        do {
            let chain = try openDirectoryChain(parent, createMissing: false)
            defer { closeDirectoryChain(chain) }
            guard chainIsStable(chain) else { return nil }
            var lookupStatus = stat()
            guard fstatatLeaf(
                chain.leafDescriptor,
                name: name,
                into: &lookupStatus,
                flags: AT_SYMLINK_NOFOLLOW
            ) == 0,
                  (lookupStatus.st_mode & S_IFMT) == S_IFREG
            else {
                return nil
            }
            let descriptor = openatLeaf(
                chain.leafDescriptor,
                name: name,
                flags: O_RDONLY | O_NOFOLLOW | O_CLOEXEC
            )
            guard descriptor >= 0 else { return nil }
            defer { Darwin.close(descriptor) }
            var before = stat()
            guard fstat(descriptor, &before) == 0,
                  sameStableStatus(lookupStatus, before),
                  before.st_uid == expectedOwnerUID,
                  before.st_gid == expectedOwnerGID,
                  before.st_nlink == 1,
                  before.st_size >= 0,
                  expectedSize == nil || Int64(before.st_size) == expectedSize,
                  maximumSize == nil || Int64(before.st_size) <= maximumSize!,
                  !requireNonEmpty || before.st_size > 0,
                  expectedMode == nil
                      || (before.st_mode & 0o777) == expectedMode!,
                  !requirePrivateMode || (before.st_mode & 0o077) == 0
            else {
                return nil
            }
            let result = try body(descriptor)
            var after = stat()
            var pathAfter = stat()
            guard fstat(descriptor, &after) == 0,
                  fstatatLeaf(
                    chain.leafDescriptor,
                    name: name,
                    into: &pathAfter,
                    flags: AT_SYMLINK_NOFOLLOW
                  ) == 0,
                  sameStableStatus(before, after),
                  sameStableStatus(after, pathAfter),
                  chainIsStable(chain)
            else {
                return nil
            }
            return result
        } catch {
            return nil
        }
    }

    private func directoryEntryNames(_ descriptor: Int32) throws -> [String] {
        let duplicate = Darwin.dup(descriptor)
        guard duplicate >= 0 else {
            throw StoreError.couldNotSecureState
        }
        guard Darwin.fcntl(duplicate, F_SETFD, FD_CLOEXEC) == 0,
              Darwin.lseek(duplicate, 0, SEEK_SET) >= 0
        else {
            Darwin.close(duplicate)
            throw StoreError.couldNotSecureState
        }
        guard let directory = Darwin.fdopendir(duplicate) else {
            Darwin.close(duplicate)
            throw StoreError.couldNotSecureState
        }
        defer { Darwin.closedir(directory) }
        var result: [String] = []
        while true {
            errno = 0
            guard let entry = Darwin.readdir(directory) else {
                if errno != 0 {
                    throw StoreError.couldNotSecureState
                }
                break
            }
            let length = Int(entry.pointee.d_namlen)
            var tuple = entry.pointee.d_name
            let data = withUnsafeBytes(of: &tuple) {
                Data($0.prefix(length))
            }
            guard length > 0,
                  length <= 255,
                  data.count == length,
                  let name = String(data: data, encoding: .utf8)
            else {
                throw StoreError.unexpectedContent
            }
            if name == "." || name == ".." {
                continue
            }
            guard validLeafName(name) else {
                throw StoreError.unexpectedContent
            }
            result.append(name)
            guard result.count <= 100_000 else {
                throw StoreError.unexpectedContent
            }
        }
        return result.sorted()
    }

    private func validLeafName(_ name: String) -> Bool {
        !name.isEmpty
            && name != "."
            && name != ".."
            && name.utf8.count <= 255
            && !name.contains("/")
            && !name.contains("\0")
    }

    private func sameEntryIdentity(_ first: stat, _ second: stat) -> Bool {
        first.st_dev == second.st_dev
            && first.st_ino == second.st_ino
            && (first.st_mode & S_IFMT) == (second.st_mode & S_IFMT)
            && first.st_uid == second.st_uid
            && first.st_gid == second.st_gid
    }

    private func sameStableStatus(_ first: stat, _ second: stat) -> Bool {
        sameContentStatus(first, second)
            && first.st_ctimespec.tv_sec == second.st_ctimespec.tv_sec
            && first.st_ctimespec.tv_nsec == second.st_ctimespec.tv_nsec
    }

    private func sameContentStatus(_ first: stat, _ second: stat) -> Bool {
        sameEntryIdentity(first, second)
            && first.st_mode == second.st_mode
            && first.st_nlink == second.st_nlink
            && first.st_size == second.st_size
            && first.st_mtimespec.tv_sec == second.st_mtimespec.tv_sec
            && first.st_mtimespec.tv_nsec == second.st_mtimespec.tv_nsec
    }

    private func openatLeaf(
        _ parentDescriptor: Int32,
        name: String,
        flags: Int32
    ) -> Int32 {
        name.withCString {
            Darwin.openat(parentDescriptor, $0, flags)
        }
    }

    private func fstatatLeaf(
        _ parentDescriptor: Int32,
        name: String,
        into status: UnsafeMutablePointer<stat>,
        flags: Int32
    ) -> Int32 {
        name.withCString {
            Darwin.fstatat(parentDescriptor, $0, status, flags)
        }
    }

    private func mkdiratLeaf(
        _ parentDescriptor: Int32,
        name: String,
        mode: mode_t
    ) -> Int32 {
        name.withCString {
            Darwin.mkdirat(parentDescriptor, $0, mode)
        }
    }

    private func unlinkatLeaf(
        _ parentDescriptor: Int32,
        name: String,
        flags: Int32
    ) -> Int32 {
        name.withCString {
            Darwin.unlinkat(parentDescriptor, $0, flags)
        }
    }

    private func promoteVerifiedDownloadedFile(
        from source: URL,
        to destination: URL,
        expectedSize: Int64,
        expectedSHA256: String,
        fileID: String
    ) throws {
        let sourceParent = Self.canonicalSystemAliasURL(
            source.deletingLastPathComponent()
        )
        let destinationParent = Self.canonicalSystemAliasURL(
            destination.deletingLastPathComponent()
        )
        let sourceName = source.lastPathComponent
        let destinationName = destination.lastPathComponent
        guard sourceParent.pathComponents == destinationParent.pathComponents,
              validLeafName(sourceName),
              validLeafName(destinationName),
              isDescendant(source, of: stagingRoot),
              isDescendant(destination, of: stagingRoot)
        else {
            throw StoreError.unsafePath
        }

        let chain = try openDirectoryChain(
            sourceParent,
            createMissing: false
        )
        defer { closeDirectoryChain(chain) }
        guard chainIsStable(chain) else {
            throw StoreError.couldNotSecureState
        }

        var lookup = stat()
        guard fstatatLeaf(
            chain.leafDescriptor,
            name: sourceName,
            into: &lookup,
            flags: AT_SYMLINK_NOFOLLOW
        ) == 0,
              (lookup.st_mode & S_IFMT) == S_IFREG,
              lookup.st_uid == expectedOwnerUID,
              lookup.st_gid == expectedOwnerGID,
              lookup.st_nlink == 1,
              lookup.st_size == expectedSize
        else {
            throw StoreError.digestMismatch(fileID)
        }
        let descriptor = openatLeaf(
            chain.leafDescriptor,
            name: sourceName,
            flags: O_RDONLY | O_NOFOLLOW | O_CLOEXEC
        )
        guard descriptor >= 0 else {
            throw StoreError.digestMismatch(fileID)
        }
        defer { Darwin.close(descriptor) }

        var opened = stat()
        guard fstat(descriptor, &opened) == 0,
              sameStableStatus(lookup, opened),
              fchmod(descriptor, 0o600) == 0
        else {
            throw StoreError.couldNotSecureState
        }
        var beforeHash = stat()
        var pathBeforeHash = stat()
        guard fstat(descriptor, &beforeHash) == 0,
              fstatatLeaf(
                chain.leafDescriptor,
                name: sourceName,
                into: &pathBeforeHash,
                flags: AT_SYMLINK_NOFOLLOW
              ) == 0,
              sameStableStatus(beforeHash, pathBeforeHash),
              (beforeHash.st_mode & 0o777) == 0o600,
              beforeHash.st_nlink == 1,
              beforeHash.st_size == expectedSize
        else {
            throw StoreError.couldNotSecureState
        }

        let digest = try Self.descriptorSHA256(descriptor)
        var afterHash = stat()
        var pathAfterHash = stat()
        guard fstat(descriptor, &afterHash) == 0,
              fstatatLeaf(
                chain.leafDescriptor,
                name: sourceName,
                into: &pathAfterHash,
                flags: AT_SYMLINK_NOFOLLOW
              ) == 0,
              sameStableStatus(beforeHash, afterHash),
              sameStableStatus(afterHash, pathAfterHash),
              chainIsStable(chain)
        else {
            throw StoreError.couldNotSecureState
        }
        guard digest == expectedSHA256 else {
            throw StoreError.digestMismatch(fileID)
        }

        var existingDestination = stat()
        guard fstatatLeaf(
            chain.leafDescriptor,
            name: destinationName,
            into: &existingDestination,
            flags: AT_SYMLINK_NOFOLLOW
        ) != 0,
              errno == ENOENT,
              fsync(descriptor) == 0
        else {
            throw StoreError.couldNotSecureState
        }
        let result = sourceName.withCString { sourcePath in
            destinationName.withCString { destinationPath in
                Darwin.renameatx_np(
                    chain.leafDescriptor,
                    sourcePath,
                    chain.leafDescriptor,
                    destinationPath,
                    UInt32(RENAME_EXCL)
                )
            }
        }
        guard result == 0 else {
            throw POSIXError(
                POSIXErrorCode(rawValue: errno) ?? .EIO
            )
        }

        var promoted = stat()
        var held = stat()
        guard fstatatLeaf(
            chain.leafDescriptor,
            name: destinationName,
            into: &promoted,
            flags: AT_SYMLINK_NOFOLLOW
              ) == 0,
              fstat(descriptor, &held) == 0,
              sameContentStatus(afterHash, held),
              sameStableStatus(held, promoted),
              fsync(chain.leafDescriptor) == 0,
              chainIsStable(chain)
        else {
            throw StoreError.couldNotSecureState
        }
    }

    private func atomicRenameExclusive(from source: URL, to destination: URL) throws {
        let destinationParent = destination.deletingLastPathComponent()
        let sourceParent = source.deletingLastPathComponent()
        let sourceName = source.lastPathComponent
        let destinationName = destination.lastPathComponent
        guard validLeafName(sourceName), validLeafName(destinationName),
              isDescendant(source, of: stagingRoot),
              isDescendant(destination, of: packsRoot)
        else {
            throw StoreError.unsafePath
        }
        let sourceChain = try openDirectoryChain(sourceParent, createMissing: false)
        defer { closeDirectoryChain(sourceChain) }
        let destinationChain = try openDirectoryChain(
            destinationParent,
            createMissing: false
        )
        defer { closeDirectoryChain(destinationChain) }
        var sourceMetadata = stat()
        var destinationParentMetadata = stat()
        guard chainIsStable(sourceChain),
              chainIsStable(destinationChain),
              fstatatLeaf(
                sourceChain.leafDescriptor,
                name: sourceName,
                into: &sourceMetadata,
                flags: AT_SYMLINK_NOFOLLOW
              ) == 0,
              fstat(destinationChain.leafDescriptor, &destinationParentMetadata) == 0,
              (sourceMetadata.st_mode & S_IFMT) == S_IFDIR,
              (sourceMetadata.st_mode & 0o777) == 0o700,
              sourceMetadata.st_uid == expectedOwnerUID,
              sourceMetadata.st_gid == expectedOwnerGID,
              destinationParentMetadata.st_uid == expectedOwnerUID,
              destinationParentMetadata.st_gid == expectedOwnerGID,
              (destinationParentMetadata.st_mode & 0o077) == 0,
              sourceMetadata.st_dev == destinationParentMetadata.st_dev
        else {
            throw StoreError.crossFilesystemPublication
        }
        var existingDestination = stat()
        guard fstatatLeaf(
            destinationChain.leafDescriptor,
            name: destinationName,
            into: &existingDestination,
            flags: AT_SYMLINK_NOFOLLOW
        ) != 0,
              errno == ENOENT
        else {
            throw StoreError.packAlreadyExists(
                "The same-version destination appeared during publication."
            )
        }
        let result = sourceName.withCString { sourcePath in
            destinationName.withCString { destinationPath in
                Darwin.renameatx_np(
                    sourceChain.leafDescriptor,
                    sourcePath,
                    destinationChain.leafDescriptor,
                    destinationPath,
                    UInt32(RENAME_EXCL)
                )
            }
        }
        guard result == 0 else {
            if errno == EEXIST {
                throw StoreError.packAlreadyExists(
                    "The same-version destination appeared during publication."
                )
            }
            throw POSIXError(
                POSIXErrorCode(rawValue: errno) ?? .EIO
            )
        }
        var publishedMetadata = stat()
        guard fstatatLeaf(
            destinationChain.leafDescriptor,
            name: destinationName,
            into: &publishedMetadata,
            flags: AT_SYMLINK_NOFOLLOW
        ) == 0,
              sameEntryIdentity(sourceMetadata, publishedMetadata),
              chainIsStable(sourceChain),
              chainIsStable(destinationChain)
        else {
            throw StoreError.couldNotSecureState
        }
    }

    private func sealPublishedRoot(_ root: URL) throws {
        let chain = try openDirectoryChain(root, createMissing: false)
        defer { closeDirectoryChain(chain) }
        let descriptor = chain.leafDescriptor
        var descriptorMetadata = stat()
        guard fstat(descriptor, &descriptorMetadata) == 0,
              (descriptorMetadata.st_mode & S_IFMT) == S_IFDIR,
              descriptorMetadata.st_uid == expectedOwnerUID,
              descriptorMetadata.st_gid == expectedOwnerGID,
              (descriptorMetadata.st_mode & 0o777) == 0o700,
              fchmod(descriptor, 0o500) == 0,
              fsync(descriptor) == 0,
              chainIsStable(chain)
        else {
            throw StoreError.couldNotSecureState
        }
        var sealed = stat()
        guard fstat(descriptor, &sealed) == 0,
              sealed.st_uid == expectedOwnerUID,
              sealed.st_gid == expectedOwnerGID,
              (sealed.st_mode & 0o777) == 0o500
        else {
            throw StoreError.couldNotSecureState
        }
    }

    private func fsyncDirectory(_ directory: URL) throws {
        let chain = try openDirectoryChain(directory, createMissing: false)
        defer { closeDirectoryChain(chain) }
        guard fsync(chain.leafDescriptor) == 0,
              chainIsStable(chain)
        else {
            throw StoreError.couldNotSecureState
        }
    }

    private static func descriptorSHA256(_ descriptor: Int32) throws -> String {
        guard Darwin.lseek(descriptor, 0, SEEK_SET) >= 0 else {
            throw StoreError.couldNotSecureState
        }
        var hash = SHA256()
        var buffer = [UInt8](repeating: 0, count: 4 * 1024 * 1024)
        while true {
            let count = buffer.withUnsafeMutableBytes {
                Darwin.read(descriptor, $0.baseAddress, $0.count)
            }
            if count < 0 {
                if errno == EINTR { continue }
                throw StoreError.couldNotSecureState
            }
            if count == 0 { break }
            let data = Data(buffer[0..<count])
            hash.update(data: data)
        }
        return hash.finalize()
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private static func descriptorData(
        _ descriptor: Int32,
        maximumSize: Int64
    ) throws -> Data {
        guard Darwin.lseek(descriptor, 0, SEEK_SET) >= 0 else {
            throw StoreError.couldNotSecureState
        }
        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            let count = buffer.withUnsafeMutableBytes {
                Darwin.read(descriptor, $0.baseAddress, $0.count)
            }
            if count < 0 {
                if errno == EINTR { continue }
                throw StoreError.couldNotSecureState
            }
            if count == 0 { break }
            guard Int64(result.count) + Int64(count) <= maximumSize else {
                throw StoreError.couldNotSecureState
            }
            result.append(contentsOf: buffer[0..<count])
        }
        return result
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private static func canonicalData(
        _ value: CanonicalJSONValue
    ) throws -> Data {
        var data = try canonicalEncoder.encode(value)
        data.append(0x0A)
        return data
    }

    private static var canonicalEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }

    enum StoreError: LocalizedError {
        case unsafePath
        case invalidCatalogDigest
        case unknownFile
        case invalidPackAuthority
        case invalidLicenseResource
        case couldNotSecureState
        case digestMismatch(String)
        case packVerificationFailed(String)
        case unexpectedContent
        case packAlreadyExists(String)
        case crossFilesystemPublication

        var errorDescription: String? {
            switch self {
            case .unsafePath:
                return "A model pack path is outside Auralign's fixed private namespace."
            case .invalidCatalogDigest:
                return "The signed model catalog digest is invalid."
            case .unknownFile:
                return "The requested model file is absent from the signed catalog."
            case .invalidPackAuthority:
                return "The model pack authority is incomplete or inconsistent."
            case .invalidLicenseResource:
                return "The signed application is missing the exact reviewed "
                    + "license text required by this model pack."
            case .couldNotSecureState:
                return "Auralign could not establish private model setup storage."
            case .digestMismatch:
                return "A downloaded model file failed exact size or SHA-256 verification."
            case .packVerificationFailed(let problem):
                return "The completed model pack did not pass final verification: "
                    + problem
            case .unexpectedContent:
                return "The model pack contains an unsupported file or link type."
            case .packAlreadyExists(let problem):
                return "The model pack destination is already occupied: " + problem
            case .crossFilesystemPublication:
                return "The verified model pack could not be published by one "
                    + "same-filesystem atomic rename."
            }
        }
    }
}

private struct PackFileRecord: Equatable {
    var path: String
    var size: Int64
    var sha256: String

    var jsonValue: CanonicalJSONValue {
        .object([
            "path": .string(path),
            "size": .integer(size),
            "sha256": .string(sha256),
            "mode": .string("0400"),
        ])
    }
}

private struct DeterministicPackMetadata {
    var files: [String: Data]
}
