// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// One pipeline-configuration field as declared by the Python engine.
/// Generated from the Pydantic models via `gui_bridge schema`; never
/// hand-edited, so constraints cannot silently drift from the source of truth.
struct ConfigField: Codable, Identifiable, Equatable {
    enum Kind: String, Codable {
        case bool
        case int
        case float
        case string
        case enumeration = "enum"
        case path
        case pathList = "path_list"
        case floatList = "float_list"
        case floatOrFloatList = "float_or_float_list"
    }

    var key: String
    var kind: Kind
    var optional: Bool
    var choices: [String]?
    var `default`: JSONValue
    var ge: Double?
    var le: Double?
    var gt: Double?
    var lt: Double?
    var maxLength: Int?
    var modelRole: String?

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, kind, optional, choices, ge, le, gt, lt
        case `default` = "default"
        case maxLength = "max_length"
        case modelRole = "model_role"
    }

    /// Human-readable constraint summary, e.g. "1 – 128" or "> 0".
    var rangeDescription: String? {
        var parts: [String] = []
        if let ge { parts.append("≥ \(Self.trim(ge))") }
        if let gt { parts.append("> \(Self.trim(gt))") }
        if let le { parts.append("≤ \(Self.trim(le))") }
        if let lt { parts.append("< \(Self.trim(lt))") }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }

    /// Whether a numeric value satisfies the engine-declared bounds.
    func isWithinBounds(_ value: Double) -> Bool {
        if let ge, value < ge { return false }
        if let gt, value <= gt { return false }
        if let le, value > le { return false }
        if let lt, value >= lt { return false }
        return true
    }

    private static func trim(_ value: Double) -> String {
        if let integer = JSONValue.double(value).intValue {
            return String(integer)
        }
        return String(value)
    }
}

struct ModelManifestEntry: Codable, Identifiable, Equatable {
    var key: String
    var role: String
    var modelId: String
    var aliases: [String]?
    var directoryName: String
    var requiredFiles: [String]
    var anyRequiredFiles: [String]
    var runtimeFiles: [String]
    var expectedSHA256: [String: String]
    var expectedSize: [String: Int64]
    var gated: Bool
    var optional: Bool
    var pullable: Bool
    var revision: String?
    var licenseExpression: String?
    var acquisitionStatus: String?
    var runtimeStatus: String?
    var redistributionStatus: String?
    var reviewStatus: String?
    var reviewedAt: String?
    var productClassification: String
    var productSelectable: Bool
    var blocker: String?
    var backendCompatibility: String
    var modificationStatus: String
    var packageBundleId: String?
    var packageBundleVersion: String?
    var packageRelativePath: String?
    var delivery: String
    var sourceURL: String?
    var termsURL: String?
    var attribution: String?

    var id: String { role + "/" + key }

    enum CodingKeys: String, CodingKey {
        case key, role, aliases, gated, optional, pullable, revision
        case modelId = "model_id"
        case directoryName = "directory_name"
        case requiredFiles = "required_files"
        case anyRequiredFiles = "any_required_files"
        case runtimeFiles = "runtime_files"
        case expectedSHA256 = "expected_sha256"
        case expectedSize = "expected_size"
        case licenseExpression = "license_expression"
        case acquisitionStatus = "acquisition_status"
        case runtimeStatus = "runtime_status"
        case redistributionStatus = "redistribution_status"
        case reviewStatus = "review_status"
        case reviewedAt = "reviewed_at"
        case productClassification = "product_classification"
        case productSelectable = "product_selectable"
        case blocker
        case backendCompatibility = "backend_compatibility"
        case modificationStatus = "modification_status"
        case packageBundleId = "package_bundle_id"
        case packageBundleVersion = "package_bundle_version"
        case packageRelativePath = "package_relative_path"
        case delivery
        case sourceURL = "source_url"
        case termsURL = "terms_url"
        case attribution
    }

    /// Production selection fails closed unless the engine manifest explicitly
    /// records a pinned, digest-complete, licensed, reviewed model in the exact
    /// separately installed product component used by the packaged runtime.
    var isRuntimeSelectable: Bool {
        guard hasValidFileContract,
              productSelectable,
              ["production", "optional"].contains(productClassification),
              blocker == nil,
              let revision, Self.isValidRevision(revision),
              Set(expectedSHA256.keys) == Set(runtimeFiles),
              expectedSHA256.values.allSatisfy(Self.isValidSHA256),
              Set(expectedSize.keys) == Set(runtimeFiles),
              expectedSize.values.allSatisfy({ $0 > 0 }),
              let licenseExpression,
              !licenseExpression.isEmpty,
              licenseExpression != "NOASSERTION",
              let acquisitionStatus, !acquisitionStatus.isEmpty,
              redistributionStatus?.hasPrefix("permitted_") == true,
              runtimeStatus?.hasPrefix("approved_local") == true,
              reviewStatus?.hasPrefix("verified") == true,
              !backendCompatibility.isEmpty,
              !modificationStatus.isEmpty,
              modificationStatus != "unreviewed",
              let packageBundleId,
              packageBundleId.hasPrefix("com.auralign.studio."),
              let packageBundleVersion,
              Self.isValidBundleVersion(packageBundleVersion),
              packageRelativePath == "models/\(directoryName)",
              ["system-component", "native-download"].contains(delivery),
              let reviewedAt, !reviewedAt.isEmpty,
              let sourceURL, !sourceURL.isEmpty,
              let termsURL, !termsURL.isEmpty,
              let attribution, !attribution.isEmpty
        else { return false }
        return true
    }

    /// The GUI accepts the Python engine's reviewed file contract only when it
    /// is a safe, internally consistent relative-path allowlist. `runtimeFiles`
    /// is the stable union of files that acquisition may place in the canonical
    /// model directory; it does not imply that every alternative is required.
    var hasValidFileContract: Bool {
        Self.isValidFileContract(
            directoryName: directoryName,
            requiredFiles: requiredFiles,
            anyRequiredFiles: anyRequiredFiles,
            runtimeFiles: runtimeFiles
        )
    }

    static func isValidFileContract(
        directoryName: String,
        requiredFiles: [String],
        anyRequiredFiles: [String],
        runtimeFiles: [String]
    ) -> Bool {
        guard isSafeDirectoryComponent(directoryName),
              !runtimeFiles.isEmpty,
              requiredFiles.allSatisfy(isSafeRelativeFilePath),
              anyRequiredFiles.allSatisfy(isSafeRelativeFilePath),
              runtimeFiles.allSatisfy(isSafeRelativeFilePath),
              Set(requiredFiles).count == requiredFiles.count,
              Set(anyRequiredFiles).count == anyRequiredFiles.count,
              Set(runtimeFiles).count == runtimeFiles.count,
              Set(requiredFiles).isDisjoint(with: Set(anyRequiredFiles)),
              runtimeFiles == requiredFiles + anyRequiredFiles
        else {
            return false
        }
        return true
    }

    private static func isValidRevision(_ value: String) -> Bool {
        value.count == 40
            && value.unicodeScalars.allSatisfy { scalar in
                (48...57).contains(scalar.value)
                    || (97...102).contains(scalar.value)
            }
    }

    private static func isValidSHA256(_ value: String) -> Bool {
        value.count == 64
            && value.unicodeScalars.allSatisfy { scalar in
                (48...57).contains(scalar.value)
                    || (97...102).contains(scalar.value)
            }
    }

    private static func isValidBundleVersion(_ value: String) -> Bool {
        let components = value.split(separator: ".", omittingEmptySubsequences: false)
        return components.count == 3
            && components.allSatisfy { component in
                !component.isEmpty && component.allSatisfy(\.isNumber)
            }
    }

    private static func isSafeDirectoryComponent(_ value: String) -> Bool {
        !value.isEmpty
            && value != "."
            && value != ".."
            && !value.contains("/")
            && !value.contains("\\")
            && !value.contains("\0")
    }

    private static func isSafeRelativeFilePath(_ value: String) -> Bool {
        guard !value.isEmpty,
              !value.hasPrefix("/"),
              !value.contains("\\"),
              !value.contains("\0")
        else {
            return false
        }
        let components = value.split(separator: "/", omittingEmptySubsequences: false)
        return components.allSatisfy { component in
            !component.isEmpty && component != "." && component != ".."
        }
    }

    var policyLabel: String {
        guard isRuntimeSelectable else { return "Unavailable" }
        if delivery == "native-download" {
            return productClassification == "optional"
                ? "Optional · local pack"
                : "Production · local pack"
        }
        if productClassification == "optional" {
            return "Optional · bundled"
        }
        return "Production · bundled"
    }

    var policyExplanation: String {
        guard isRuntimeSelectable else {
            if let blocker, !blocker.isEmpty {
                return "Unavailable in this product: \(blocker)"
            }
            let review = reviewStatus ?? "missing release evidence"
            return "Unavailable in this product: \(review)."
        }
        if gated {
            return "Bundled and verified for offline use. Upstream gating applies to "
                + "controlled release-machine acquisition, not recipient startup."
        }
        if delivery == "native-download" {
            return "Installed through Complete Offline Setup, verified byte-for-byte, "
                + "and used locally without runtime network access."
        }
        if reviewStatus?.contains("caveat") == true {
            return "Bundled and verified for offline use with the recorded provenance caveat."
        }
        return "Bundled and verified for offline use by the signed model component."
    }

    var productDisplayName: String {
        switch (role, key) {
        case ("asr", "large-v3"):
            return "Whisper Large v3"
        case ("asr", "turbo"):
            return "Whisper Large v3 Turbo"
        case ("asr", "distil-large-v3"):
            return "Distil Whisper Large v3"
        case ("asr", "distil-large-v3.5"):
            return "Distil Whisper Large v3.5"
        case ("asr-mlx", "mlx-tiny"):
            return "MLX Whisper Tiny"
        case ("asr-mlx", "mlx-tiny.en"):
            return "MLX Whisper Tiny English"
        case ("asr-mlx", "mlx-base"):
            return "MLX Whisper Base"
        case ("asr-mlx", "mlx-base.en"):
            return "MLX Whisper Base English"
        case ("asr-mlx", "mlx-small"):
            return "MLX Whisper Small"
        case ("asr-mlx", "mlx-small.en"):
            return "MLX Whisper Small English"
        case ("asr-mlx", "mlx-medium"):
            return "MLX Whisper Medium"
        case ("asr-mlx", "mlx-medium.en"):
            return "MLX Whisper Medium English"
        case ("asr-mlx", "mlx-large"):
            return "MLX Whisper Large"
        case ("asr-mlx", "mlx-large-v2"):
            return "MLX Whisper Large v2"
        case ("asr-mlx", "mlx-large-v3"):
            return "MLX Whisper Large v3"
        case ("asr-mlx", "mlx-large-v3-turbo"):
            return "MLX Whisper Large v3 Turbo"
        case ("diarization", "community-1"):
            return "Community-1 Speaker Diarization"
        case ("alignment", "en"):
            return "English Forced Alignment"
        case ("speaker-embedding", "ecapa"):
            return "ECAPA Speaker Embeddings"
        default:
            return key
        }
    }

    var productSummary: String {
        "\(productDisplayName). \(backendCompatibility). \(policyExplanation)"
    }

    var shortRevision: String {
        guard let revision, !revision.isEmpty else { return "unpinned" }
        return String(revision.prefix(12))
    }
}

struct ConfigManifest: Codable, Equatable {
    var schemaVersion: String
    var packageVersion: String
    var fields: [ConfigField]
    var modelManifest: [ModelManifestEntry]

    enum CodingKeys: String, CodingKey {
        case fields
        case schemaVersion = "schema_version"
        case packageVersion = "package_version"
        case modelManifest = "model_manifest"
    }

    func field(_ key: String) -> ConfigField? {
        fieldsByKey[key]
    }

    var fieldsByKey: [String: ConfigField] {
        Dictionary(uniqueKeysWithValues: fields.map { ($0.key, $0) })
    }

    func modelChoices(role: String) -> [ModelManifestEntry] {
        modelRecords(role: role).filter(\.isRuntimeSelectable)
    }

    /// Full audit records remain resolvable for stale imports and truthful
    /// blockers, but only `modelChoices` may populate an ordinary picker.
    func modelRecords(role: String) -> [ModelManifestEntry] {
        modelManifest.filter { $0.role == role }
    }

    func resolveModel(role: String, value: String) -> ModelManifestEntry? {
        modelRecords(role: role).first {
            $0.key == value
                || $0.modelId == value
                || ($0.aliases ?? []).contains(value)
        }
    }

    /// Manifest entries required by the effective configuration. The rules
    /// mirror the Python doctor's selected-backend model checks.
    func requiredModelEntries(for configuration: JSONValue) -> [ModelManifestEntry] {
        requiredModelRequests(for: configuration).compactMap { request in
            resolveModel(role: request.role, value: request.value)
        }
    }

    /// User-facing, privacy-safe blockers for unknown or policy-blocked model
    /// selections. Callers use this as a second fail-closed boundary in
    /// addition to disabling unavailable picker rows.
    func modelPolicyBlockers(for configuration: JSONValue) -> [String] {
        requiredModelRequests(for: configuration).compactMap { request in
            guard let entry = resolveModel(role: request.role, value: request.value) else {
                return "The selected \(request.role) model is absent from the reviewed manifest."
            }
            guard entry.isRuntimeSelectable else {
                return "\(entry.role) model “\(entry.key)” is unavailable: "
                    + entry.policyExplanation
            }
            return nil
        }
    }

    private func requiredModelRequests(
        for configuration: JSONValue
    ) -> [(role: String, value: String)] {
        var requests: [(role: String, value: String)] = []
        if configuration.value(at: "asr.backend")?.stringValue == "mlx-whisper" {
            requests.append(
                ("asr-mlx", configuration.value(at: "asr.mlx_model")?.stringValue ?? "")
            )
        } else {
            requests.append(
                ("asr", configuration.value(at: "asr.model")?.stringValue ?? "")
            )
        }
        if configuration.value(at: "diarization.enabled")?.boolValue == true,
           configuration.value(at: "diarization.backend")?.stringValue != "off" {
            requests.append(
                (
                    "diarization",
                    configuration.value(at: "diarization.model")?.stringValue ?? ""
                )
            )
        }
        if configuration.value(at: "alignment.enabled")?.boolValue == true,
           let alignmentBackend = configuration.value(
               at: "alignment.backend"
           )?.stringValue,
           ["auto", "forced"].contains(alignmentBackend) {
            requests.append(
                (
                    "alignment",
                    configuration.value(at: "alignment.model")?.stringValue ?? "en"
                )
            )
        }
        if configuration.value(at: "speaker_id.mode")?.stringValue == "auto" {
            requests.append(
                (
                    "speaker-embedding",
                    configuration.value(at: "speaker_id.model")?.stringValue ?? ""
                )
            )
        }
        return requests
    }

    /// Engine defaults folded into a nested configuration object.
    func defaultConfiguration() -> JSONValue {
        var config = JSONValue.object([:])
        for field in fields {
            config = config.setting(field.key, to: field.default)
        }
        return config
    }

    static func loadBundled() throws -> ConfigManifest {
        guard let url = Bundle.main.url(forResource: "config_manifest", withExtension: "json")
        else {
            throw CocoaError(.fileNoSuchFile)
        }
        return try JSONDecoder().decode(ConfigManifest.self, from: Data(contentsOf: url))
    }
}
