// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import CryptoKit
import Foundation

/// Code-signature-sealed authority for the exact model bytes that native setup
/// may acquire. It is generated from the Python manifest and external-asset
/// registry, then embedded as `model_delivery_catalog.json`.
struct ModelDeliveryCatalog: Codable, Equatable, Sendable {
    static let schemaVersion = "auralign-model-delivery-catalog.v1"

    var schema: String
    var catalogVersion: String
    var productVersion: String
    var allowedInitialHosts: [String]
    var redirectHostSuffixes: [String]
    var bootstrap: Bootstrap
    var packContract: ModelPackContract
    var normalPresets: [String: String]
    var models: [ModelDeliveryModel]
    private(set) var sourceSHA256: String? = nil

    enum CodingKeys: String, CodingKey {
        case models, bootstrap
        case schema = "schema_version"
        case catalogVersion = "catalog_version"
        case productVersion = "product_version"
        case allowedInitialHosts = "allowed_initial_hosts"
        case redirectHostSuffixes = "allowed_redirect_host_suffixes"
        case packContract = "pack_contract"
        case normalPresets = "normal_presets"
    }

    struct Bootstrap: Codable, Equatable, Sendable {
        var bundleID: String
        var bundleVersion: String
        var modelKeys: [String]

        enum CodingKeys: String, CodingKey {
            case bundleID = "bundle_id"
            case bundleVersion = "bundle_version"
            case modelKeys = "model_keys"
        }
    }

    var digest: String {
        if let sourceSHA256 { return sourceSHA256 }
        guard let data = try? Self.canonicalEncoder.encode(self) else {
            return ""
        }
        return Self.sha256(data)
    }

    /// One immutable directory per downloadable model. Independent packs keep
    /// repair and update scope bounded even for multi-gigabyte weights.
    var packs: [ModelDeliveryPack] {
        models.compactMap { model in
            guard model.delivery == "native-download",
                  let packID = model.packID,
                  let packVersion = model.packVersion,
                  let packRelativePath = model.packRelativePath
            else {
                return nil
            }
            return ModelDeliveryPack(
                authority: model,
                contract: packContract,
                id: packID,
                version: packVersion,
                displayName: model.displayName,
                description: model.description,
                accessibilityLabel: model.accessibilityLabel,
                modelKeys: [model.key],
                requiredForOfflineSetup: !model.requiredForPresets.isEmpty,
                requiredForPresets: model.requiredForPresets,
                quality: model.quality,
                speed: model.speed,
                memory: model.memory,
                licenseExpressions: [model.licenseExpression],
                attributions: [model.attribution],
                sourceURLs: [model.sourceURL.absoluteString],
                files: model.files.compactMap { file in
                    guard let sourceURL = file.sourceURL else {
                        return nil
                    }
                    return ModelDeliveryFile(
                        id: "\(model.role)/\(model.key)/\(file.relativePath)",
                        modelKey: model.key,
                        relativePath: "\(packRelativePath)/\(file.relativePath)",
                        sourceURL: sourceURL,
                        expectedSize: file.expectedSize,
                        sha256: file.sha256
                    )
                }
            )
        }.sorted(by: { $0.id < $1.id })
    }

    var requiredPacks: [ModelDeliveryPack] {
        packs.filter(\.requiredForOfflineSetup)
    }

    func pack(for entry: ModelManifestEntry) -> ModelDeliveryPack? {
        guard entry.delivery == "native-download",
              let identifier = entry.packageBundleId,
              let version = entry.packageBundleVersion
        else {
            return nil
        }
        return packs.first {
            $0.id == identifier
                && $0.version == version
                && $0.modelKeys.contains(entry.key)
        }
    }

    static func loadBundled(bundle: Bundle = .main) throws
        -> ModelDeliveryCatalog
    {
        guard let url = bundle.url(
            forResource: "model_delivery_catalog",
            withExtension: "json"
        ) else {
            throw CocoaError(.fileNoSuchFile)
        }
        let data = try Data(contentsOf: url, options: [.mappedIfSafe])
        var catalog = try JSONDecoder().decode(
            ModelDeliveryCatalog.self,
            from: data
        )
        catalog.sourceSHA256 = sha256(data)
        try catalog.validate()
        return catalog
    }

    func validate() throws {
        guard schema == Self.schemaVersion,
              Self.isSafeVersion(catalogVersion),
              Self.isSafeVersion(productVersion),
              !allowedInitialHosts.isEmpty,
              Set(allowedInitialHosts).count == allowedInitialHosts.count,
              allowedInitialHosts.allSatisfy(Self.isSafeHost),
              !redirectHostSuffixes.isEmpty,
              Set(redirectHostSuffixes).count == redirectHostSuffixes.count,
              // The persisted key retains its v1 name for schema stability,
              // but every value is an exact reviewed redirect endpoint.
              redirectHostSuffixes.allSatisfy(Self.isSafeHost),
              !normalPresets.isEmpty,
              normalPresets.keys.allSatisfy(Self.isSafeIdentifier),
              normalPresets.values.allSatisfy(Self.isSafeIdentifier),
              models.count == 19,
              Set(models.map(\.id)).count == models.count,
              Set(packs.map(\.id)).count == packs.count,
              packContract.isExactSupportedContract,
              Self.isSafeIdentifier(bootstrap.bundleID),
              Self.isSafeVersion(bootstrap.bundleVersion),
              bootstrap.modelKeys.count == 3,
              Set(bootstrap.modelKeys).count == bootstrap.modelKeys.count
        else {
            throw CatalogError.invalidCatalog
        }

        let keys = Set(models.map(\.key))
        let systemComponentKeys = Set(
            models
                .filter { $0.delivery == "system-component" }
                .map(\.key)
        )
        guard keys.count == models.count,
              Set(bootstrap.modelKeys) == systemComponentKeys,
              Set(normalPresets.values).isSubset(of: keys)
        else {
            throw CatalogError.invalidCatalog
        }
        for (preset, key) in normalPresets {
            guard let model = models.first(where: { $0.key == key }),
                  model.delivery == "native-download",
                  model.requiredForPresets.contains(preset)
            else {
                throw CatalogError.invalidCatalog
            }
        }
        for model in models {
            guard model.requiredForPresets.allSatisfy({
                normalPresets[$0] == model.key
            }) else {
                throw CatalogError.invalidModel(model.id)
            }
            try model.validate(
                initialHosts: allowedInitialHosts,
                redirectHostSuffixes: redirectHostSuffixes,
                bootstrap: bootstrap
            )
        }
        for pack in packs {
            try pack.validate(
                initialHosts: allowedInitialHosts,
                redirectHostSuffixes: redirectHostSuffixes
            )
        }
    }

    static func isAllowedInitialHost(
        _ host: String?,
        hosts: [String]
    ) -> Bool {
        guard let host = host?.lowercased() else { return false }
        return hosts.contains(host)
    }

    static func isAllowedRedirectHost(
        _ host: String?,
        suffixes: [String]
    ) -> Bool {
        guard let host = host?.lowercased(), !host.isEmpty else {
            return false
        }
        // Exact membership is intentional. A listed apex such as
        // `huggingface.co` must not authorize every one of its subdomains,
        // and a listed CDN endpoint must not authorize nested hostnames.
        return suffixes.contains(host)
    }

    fileprivate static func isSafeVersion(_ value: String) -> Bool {
        !value.isEmpty
            && value.count <= 128
            && value.unicodeScalars.allSatisfy {
                CharacterSet.alphanumerics.union(
                    CharacterSet(charactersIn: ".-_")
                ).contains($0)
            }
    }

    private static func isSafeHost(_ value: String) -> Bool {
        !value.isEmpty
            && value == value.lowercased()
            && !value.hasPrefix(".")
            && !value.hasSuffix(".")
            && value.unicodeScalars.allSatisfy {
                CharacterSet.alphanumerics.union(
                    CharacterSet(charactersIn: ".-")
                ).contains($0)
            }
    }

    static func isSafeIdentifier(_ value: String) -> Bool {
        !value.isEmpty
            && value.count <= 200
            && !value.contains("/")
            && !value.contains("\\")
            && !value.contains("\0")
            && value != "."
            && value != ".."
    }

    static func isSafeRelativePath(_ value: String) -> Bool {
        guard !value.isEmpty,
              value.utf8.count <= 1_024,
              !value.hasPrefix("/"),
              !value.contains("\\"),
              !value.contains("\0")
        else {
            return false
        }
        return value.split(separator: "/", omittingEmptySubsequences: false)
            .allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
    }

    static func isValidSHA256(_ value: String) -> Bool {
        value.utf8.count == 64
            && value.utf8.allSatisfy {
                (48...57).contains($0) || (97...102).contains($0)
            }
    }

    fileprivate static var canonicalEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    enum CatalogError: LocalizedError {
        case invalidCatalog
        case invalidModel(String)
        case invalidPack(String)
        case invalidFile(String)

        var errorDescription: String? {
            switch self {
            case .invalidCatalog:
                return "The signed model delivery catalog is malformed."
            case .invalidModel(let identifier):
                return "Model \(identifier) has an invalid signed delivery contract."
            case .invalidPack(let identifier):
                return "Model pack \(identifier) has an invalid signed delivery contract."
            case .invalidFile(let identifier):
                return "Model file \(identifier) has an invalid signed delivery contract."
            }
        }
    }
}

enum CanonicalJSONValue: Codable, Equatable, Sendable {
    case object([String: CanonicalJSONValue])
    case array([CanonicalJSONValue])
    case string(String)
    case integer(Int64)
    case boolean(Bool)
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .boolean(value)
        } else if let value = try? container.decode(Int64.self) {
            self = .integer(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode(
            [CanonicalJSONValue].self
        ) {
            self = .array(value)
        } else if let value = try? container.decode(
            [String: CanonicalJSONValue].self
        ) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription:
                    "Only canonical JSON objects, arrays, strings, integers, "
                    + "booleans, and null are supported."
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        case .integer(let value):
            try container.encode(value)
        case .boolean(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }
}

struct ModelPackContract: Codable, Equatable, Sendable {
    static let schemaVersion = "1.0"
    static let layoutVersion = "1"

    var schemaVersion: String
    var layoutVersion: String
    var filenames: Filenames
    var filesystem: Filesystem
    var licenses: [String: License]
    var sbom: SBOM

    enum CodingKeys: String, CodingKey {
        case filenames, filesystem, licenses, sbom
        case schemaVersion = "schema_version"
        case layoutVersion = "layout_version"
    }

    struct Filenames: Codable, Equatable, Sendable {
        var completion: String
        var licensesDirectory: String
        var manifest: String
        var notices: String
        var provenance: String
        var sbom: String
        var verification: String

        enum CodingKeys: String, CodingKey {
            case completion, manifest, notices, provenance, sbom, verification
            case licensesDirectory = "licenses_directory"
        }
    }

    struct License: Codable, Equatable, Sendable {
        var filename: String
        var sha256: String
    }

    struct Filesystem: Codable, Equatable, Sendable {
        var directoryMode: String
        var fileMode: String
        var stagingRootMode: String
        var publishedRootMode: String
        var publication: String

        enum CodingKeys: String, CodingKey {
            case publication
            case directoryMode = "directory_mode"
            case fileMode = "file_mode"
            case stagingRootMode = "staging_root_mode"
            case publishedRootMode = "published_root_mode"
        }
    }

    struct SBOM: Codable, Equatable, Sendable {
        var bomFormat: String
        var specVersion: String

        enum CodingKeys: String, CodingKey {
            case bomFormat = "bom_format"
            case specVersion = "spec_version"
        }
    }

    var isExactSupportedContract: Bool {
        schemaVersion == Self.schemaVersion
            && layoutVersion == Self.layoutVersion
            && filenames.completion == "model-pack-complete.json"
            && filenames.licensesDirectory == "licenses"
            && filenames.manifest == "model-pack-manifest.json"
            && filenames.notices == "THIRD-PARTY-NOTICES.txt"
            && filenames.provenance == "model-pack-provenance.json"
            && filenames.sbom == "model-pack.cdx.json"
            && filenames.verification == "model-pack-verification.json"
            && filesystem == Filesystem(
                directoryMode: "0500",
                fileMode: "0400",
                stagingRootMode: "0700",
                publishedRootMode: "0500",
                publication: "exclusive-rename-then-read-only-root-seal"
            )
            && licenses == [
                "Apache-2.0": License(
                    filename: "Apache-2.0.txt",
                    sha256:
                        "074e6e32c86a4c0ef8b3ed25b721ca23aca83df277cd88106ef7177c354615ff"
                ),
                "CC-BY-4.0": License(
                    filename: "CC-BY-4.0.txt",
                    sha256:
                        "d557539df68e771cc1eedcc91d13f70fca930e508d11eedcafa4b15db49e3744"
                ),
                "MIT": License(
                    filename: "MIT.txt",
                    sha256:
                        "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5"
                ),
            ]
            && sbom == SBOM(bomFormat: "CycloneDX", specVersion: "1.6")
    }
}

struct ModelDeliveryModel: Codable, Equatable, Identifiable, Sendable {
    var key: String
    var role: String
    var modelID: String
    var aliases: [String]
    var directoryAliases: [String]
    var displayName: String
    var description: String
    var quality: String
    var speed: String
    var memory: String
    var accessibilityLabel: String
    var revision: String
    var delivery: String
    var packID: String?
    var packVersion: String?
    var packRelativePath: String?
    var requiredForPresets: [String]
    var totalSize: Int64
    var files: [ModelDeliveryFile]
    var licenseExpression: String
    var attribution: String
    var gated: Bool
    var redistributionStatus: String
    var reviewStatus: String
    var reviewedAt: String?
    var acquisitionStatus: String
    var runtimeStatus: String
    var productClassification: String
    var backendCompatibility: String
    var modificationStatus: String
    var sourceURL: URL
    var termsURL: URL
    var packSBOM: CanonicalJSONValue? = nil

    var id: String { "\(role)/\(key)" }

    var expectedPackSBOM: CanonicalJSONValue? {
        guard delivery == "native-download",
              let packID,
              let packVersion,
              let packRelativePath
        else {
            return nil
        }
        let modelReference = "model:\(modelID)@\(revision)"
        let orderedFiles = files.sorted(by: {
            $0.relativePath < $1.relativePath
        })
        let fileReferences = orderedFiles.map {
            "file:\(key)/\($0.relativePath)"
        }
        let components: [CanonicalJSONValue] = orderedFiles.map { file in
            .object([
                "type": .string("file"),
                "bom-ref": .string("file:\(key)/\(file.relativePath)"),
                "name": .string(file.relativePath),
                "hashes": .array([
                    .object([
                        "alg": .string("SHA-256"),
                        "content": .string(file.sha256),
                    ]),
                ]),
                "properties": .array([
                    .object([
                        "name": .string("auralign:model-file-size"),
                        "value": .string(String(file.expectedSize)),
                    ]),
                    .object([
                        "name": .string("auralign:model-key"),
                        "value": .string(key),
                    ]),
                    .object([
                        "name": .string("auralign:model-package-path"),
                        "value": .string(
                            "\(packRelativePath)/\(file.relativePath)"
                        ),
                    ]),
                ]),
            ])
        }
        return .object([
            "bomFormat": .string("CycloneDX"),
            "specVersion": .string("1.6"),
            "version": .integer(1),
            "metadata": .object([
                "component": .object([
                    "type": .string("machine-learning-model"),
                    "bom-ref": .string(modelReference),
                    "name": .string(modelID),
                    "version": .string(revision),
                    "licenses": .array([
                        .object([
                            "license": .object([
                                "id": .string(licenseExpression),
                            ]),
                        ]),
                    ]),
                    "externalReferences": .array([
                        .object([
                            "type": .string("distribution"),
                            "url": .string(sourceURL.absoluteString),
                        ]),
                        .object([
                            "type": .string("website"),
                            "url": .string(termsURL.absoluteString),
                        ]),
                    ]),
                    "properties": .array([
                        .object([
                            "name": .string("auralign:model-key"),
                            "value": .string(key),
                        ]),
                        .object([
                            "name": .string("auralign:model-role"),
                            "value": .string(role),
                        ]),
                        .object([
                            "name": .string("auralign:model-pack-id"),
                            "value": .string(packID),
                        ]),
                        .object([
                            "name": .string("auralign:model-pack-version"),
                            "value": .string(packVersion),
                        ]),
                        .object([
                            "name": .string(
                                "auralign:model-package-relative-path"
                            ),
                            "value": .string(packRelativePath),
                        ]),
                    ]),
                ]),
            ]),
            "components": .array(components),
            "dependencies": .array([
                .object([
                    "ref": .string(modelReference),
                    "dependsOn": .array(
                        fileReferences.map(CanonicalJSONValue.string)
                    ),
                ]),
            ]),
        ])
    }

    enum CodingKeys: String, CodingKey {
        case key, role, aliases, description, quality, speed, memory
        case revision, delivery, files, attribution, gated
        case modelID = "model_id"
        case displayName = "display_name"
        case accessibilityLabel = "accessibility_label"
        case packID = "pack_id"
        case packVersion = "pack_version"
        case packRelativePath = "pack_relative_path"
        case requiredForPresets = "required_for_presets"
        case totalSize = "total_size"
        case licenseExpression = "license_expression"
        case redistributionStatus = "redistribution_status"
        case reviewStatus = "review_status"
        case reviewedAt = "reviewed_at"
        case directoryAliases = "directory_aliases"
        case acquisitionStatus = "acquisition_status"
        case runtimeStatus = "runtime_status"
        case productClassification = "product_classification"
        case backendCompatibility = "backend_compatibility"
        case modificationStatus = "modification_status"
        case sourceURL = "source_url"
        case termsURL = "terms_url"
        case packSBOM = "pack_sbom"
    }

    fileprivate func validate(
        initialHosts: [String],
        redirectHostSuffixes: [String],
        bootstrap: ModelDeliveryCatalog.Bootstrap
    ) throws {
        var exactFileTotal: Int64 = 0
        for file in files {
            let addition = exactFileTotal.addingReportingOverflow(
                file.expectedSize
            )
            guard !addition.overflow else {
                throw ModelDeliveryCatalog.CatalogError.invalidModel(id)
            }
            exactFileTotal = addition.partialValue
        }
        guard ModelDeliveryCatalog.isSafeIdentifier(key),
              ModelDeliveryCatalog.isSafeIdentifier(role),
              !modelID.isEmpty,
              aliases.allSatisfy({ !$0.isEmpty && !$0.contains("\0") }),
              directoryAliases.allSatisfy({
                  !$0.isEmpty && !$0.contains("\0")
              }),
              !displayName.isEmpty,
              !description.isEmpty,
              !quality.isEmpty,
              !speed.isEmpty,
              !memory.isEmpty,
              !accessibilityLabel.isEmpty,
              revision.count == 40,
              revision.utf8.allSatisfy({
                  (48...57).contains($0) || (97...102).contains($0)
              }),
              ["system-component", "native-download"].contains(delivery),
              totalSize > 0,
              !files.isEmpty,
              Set(files.map(\.relativePath)).count == files.count,
              exactFileTotal == totalSize,
              !licenseExpression.isEmpty,
              licenseExpression != "NOASSERTION",
              !attribution.isEmpty,
              redistributionStatus.hasPrefix("permitted_"),
              reviewStatus.hasPrefix("verified"),
              reviewedAt == nil
                  || (reviewedAt?.isEmpty == false
                      && reviewedAt?.contains("\0") == false),
              !acquisitionStatus.isEmpty,
              !runtimeStatus.isEmpty,
              ["production", "optional"].contains(productClassification),
              !backendCompatibility.isEmpty,
              !modificationStatus.isEmpty,
              sourceURL.scheme?.lowercased() == "https",
              termsURL.scheme?.lowercased() == "https",
              sourceURL.user == nil,
              sourceURL.password == nil,
              termsURL.user == nil,
              termsURL.password == nil,
              ModelDeliveryCatalog.isAllowedInitialHost(
                  sourceURL.host,
                  hosts: initialHosts
              ),
              ModelDeliveryCatalog.isAllowedInitialHost(
                  termsURL.host,
                  hosts: initialHosts
              )
        else {
            throw ModelDeliveryCatalog.CatalogError.invalidModel(id)
        }

        if delivery == "system-component" {
            guard packID == bootstrap.bundleID,
                  packVersion == bootstrap.bundleVersion,
                  bootstrap.modelKeys.contains(key),
                  requiredForPresets.isEmpty,
                  packSBOM == nil
            else {
                throw ModelDeliveryCatalog.CatalogError.invalidModel(id)
            }
        } else {
            guard let packID,
                  let packVersion,
                  let packRelativePath,
                  ModelDeliveryCatalog.isSafeIdentifier(packID),
                  ModelDeliveryCatalog.isSafeVersion(packVersion),
                  ModelDeliveryCatalog.isSafeRelativePath(packRelativePath),
                  packSBOM != nil
            else {
                throw ModelDeliveryCatalog.CatalogError.invalidModel(id)
            }
            _ = packID
            _ = packVersion
            _ = packRelativePath
            guard packSBOM == expectedPackSBOM else {
                throw ModelDeliveryCatalog.CatalogError.invalidModel(id)
            }
        }
        for file in files {
            try file.validate(
                initialHosts: initialHosts,
                redirectHostSuffixes: redirectHostSuffixes,
                requiresSourceURL: delivery == "native-download"
            )
        }
    }
}

struct ModelDeliveryPack: Equatable, Identifiable, Sendable {
    var authority: ModelDeliveryModel
    var contract: ModelPackContract
    var id: String
    var version: String
    var displayName: String
    var description: String
    var accessibilityLabel: String
    var modelKeys: [String]
    var requiredForOfflineSetup: Bool
    var requiredForPresets: [String]
    var quality: String
    var speed: String
    var memory: String
    var licenseExpressions: [String]
    var attributions: [String]
    var sourceURLs: [String]
    var files: [ModelDeliveryFile]

    var totalBytes: Int64 {
        files.reduce(0) { partial, file in
            let addition = partial.addingReportingOverflow(file.expectedSize)
            return addition.overflow ? Int64.max : addition.partialValue
        }
    }

    fileprivate func validate(
        initialHosts: [String],
        redirectHostSuffixes: [String]
    ) throws {
        guard ModelDeliveryCatalog.isSafeIdentifier(id),
              ModelDeliveryCatalog.isSafeVersion(version),
              !displayName.isEmpty,
              !description.isEmpty,
              !accessibilityLabel.isEmpty,
              !modelKeys.isEmpty,
              Set(modelKeys).count == modelKeys.count,
              modelKeys.allSatisfy(ModelDeliveryCatalog.isSafeIdentifier),
              !files.isEmpty,
              authority.files.count == files.count,
              Set(files.map(\.id)).count == files.count,
              Set(files.map(\.relativePath)).count == files.count,
              totalBytes > 0,
              !licenseExpressions.isEmpty,
              licenseExpressions.allSatisfy({ !$0.isEmpty }),
              !attributions.isEmpty,
              !sourceURLs.isEmpty,
              authority.key == modelKeys.first,
              authority.packID == id,
              authority.packVersion == version,
              authority.delivery == "native-download"
        else {
            throw ModelDeliveryCatalog.CatalogError.invalidPack(id)
        }
        for file in files {
            try file.validate(
                initialHosts: initialHosts,
                redirectHostSuffixes: redirectHostSuffixes,
                requiresSourceURL: true
            )
        }
    }
}

struct ModelDeliveryFile: Codable, Equatable, Identifiable, Sendable {
    var id: String
    var modelKey: String
    var relativePath: String
    var sourceURL: URL?
    var expectedSize: Int64
    var sha256: String

    enum CodingKeys: String, CodingKey {
        case sha256
        case relativePath = "path"
        case sourceURL = "url"
        case expectedSize = "size"
    }

    init(
        id: String,
        modelKey: String,
        relativePath: String,
        sourceURL: URL?,
        expectedSize: Int64,
        sha256: String
    ) {
        self.id = id
        self.modelKey = modelKey
        self.relativePath = relativePath
        self.sourceURL = sourceURL
        self.expectedSize = expectedSize
        self.sha256 = sha256
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        relativePath = try container.decode(String.self, forKey: .relativePath)
        sourceURL = try container.decodeIfPresent(
            URL.self,
            forKey: .sourceURL
        )
        expectedSize = try container.decode(Int64.self, forKey: .expectedSize)
        sha256 = try container.decode(String.self, forKey: .sha256)
        id = relativePath
        modelKey = ""
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(relativePath, forKey: .relativePath)
        try container.encodeIfPresent(sourceURL, forKey: .sourceURL)
        try container.encode(expectedSize, forKey: .expectedSize)
        try container.encode(sha256, forKey: .sha256)
    }

    fileprivate func validate(
        initialHosts: [String],
        redirectHostSuffixes: [String],
        requiresSourceURL: Bool
    ) throws {
        guard ModelDeliveryCatalog.isSafeRelativePath(relativePath),
              expectedSize > 0,
              ModelDeliveryCatalog.isValidSHA256(sha256)
        else {
            throw ModelDeliveryCatalog.CatalogError.invalidFile(id)
        }
        guard let sourceURL else {
            if requiresSourceURL {
                throw ModelDeliveryCatalog.CatalogError.invalidFile(id)
            }
            return
        }
        guard sourceURL.scheme?.lowercased() == "https",
              sourceURL.user == nil,
              sourceURL.password == nil,
              ModelDeliveryCatalog.isAllowedInitialHost(
                  sourceURL.host,
                  hosts: initialHosts
              ),
              ModelDeliveryCatalog.isAllowedRedirectHost(
                  sourceURL.host,
                  suffixes: redirectHostSuffixes
              )
        else {
            throw ModelDeliveryCatalog.CatalogError.invalidFile(id)
        }
    }
}
