// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import CryptoKit
import Darwin
import Foundation

/// Locates either the Debug checkout or the self-contained Release runtime.
///
/// Debug preserves the repository's locked `uv` workflow. Release resolves a
/// purpose-built, relocatable Python 3.11 engine from the signed application
/// bundle and never consults a checkout, shell profile, Homebrew, or `PATH`.
struct EngineLocation: Equatable {
#if DEBUG
    static let engineRootDefaultsKey = "AuralignEngineRootOverride"
#endif
    static let runtimeDirectoryName = "AuralignEngineRuntime"
    static let runtimeBundleName = "\(runtimeDirectoryName).bundle"
    static let modelCacheDefaultsKey = "AuralignModelCacheOverride"
    static let applicationSupportDirectoryName = "com.auralign.studio"
    static let modelBundleIdentifier = "com.auralign.studio.models.bootstrap.v2"
    static let modelBundleVersion = "2.0.0"

    private static let unconfiguredRepositoryRoot = URL(
        fileURLWithPath: "/dev/null/auralign-engine-unconfigured",
        isDirectory: true
    )
    private static let modelBundleSchemaVersion = "1.0"
    private static let strictModelKeys = [
        "community-1",
        "en",
        "ecapa",
    ]
    private static let punktRuntimeAssetKey =
        "nltk-punkt-tab-english-corpus-free"
    private static let punktRuntimeRoot = "models/runtime-assets/nltk_data"
    private static let punktLanguageRoot =
        "\(punktRuntimeRoot)/tokenizers/punkt_tab/english"
    private static let punktMarkerPath =
        "\(punktRuntimeRoot)/.ltm-generated-punkt.json"
    private static let punktFiles = [
        "abbrev_types.txt",
        "collocations.tab",
        "ortho_context.tab",
        "sent_starters.txt",
    ]
    private static let emptyFileSHA256 =
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    private static let trustedMetadataMaximumBytes: off_t = 4 * 1024 * 1024
    private static let verificationChecks = [
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
    ]

    enum Origin: Equatable {
        case explicit
        case persistedOverride
        case embeddedBuildDefault
        case embeddedRuntime
        case unconfigured
    }

    var repositoryRoot: URL
    var origin: Origin = .explicit
    var embeddedRuntimeRoot: URL? = nil
    var configuredModelCacheRoot: URL? = nil
    var configuredModelCachePathWasAbsolute = true
    var installedModelComponentRoot: URL? = nil

    var isConfigured: Bool { origin != .unconfigured }
    var usesEmbeddedRuntime: Bool { origin == .embeddedRuntime }

    var repositoryDisplayPath: String {
        if usesEmbeddedRuntime {
            return "Included with Auralign"
        }
        return isConfigured ? repositoryRoot.path : "Not selected"
    }

    var pythonDisplayPath: String {
        if usesEmbeddedRuntime {
            return "Included Python 3.11 runtime"
        }
        return isConfigured
            ? pythonExecutable.path
            : "Not available until a repository is selected"
    }

    var pythonExecutable: URL {
        if let embeddedRuntimeRoot {
            return embeddedRuntimeRoot.appendingPathComponent("bin/python3")
        }
        return repositoryRoot.appendingPathComponent(".venv/bin/python")
    }

    var packageDirectory: URL {
        if let embeddedRuntimeRoot {
            return embeddedRuntimeRoot.appendingPathComponent(
                "lib/python3.11/site-packages/local_transcript_max",
                isDirectory: true
            )
        }
        return repositoryRoot.appendingPathComponent("src/local_transcript_max")
    }

    var workingDirectory: URL {
        usesEmbeddedRuntime ? Self.applicationSupportRoot : repositoryRoot
    }

    var bundledToolsDirectory: URL? {
        embeddedRuntimeRoot?.appendingPathComponent("bin", isDirectory: true)
    }

    var modelCacheDisplayPath: String {
        modelCacheRoot.path
    }

    var modelComponentRoot: URL {
        installedModelComponentRoot ?? Self.trustedModelComponentRoot
    }

    var modelCacheRoot: URL {
        if usesEmbeddedRuntime {
            return Self.defaultModelCacheRoot
        }
        return configuredModelCacheRoot ?? Self.defaultModelCacheRoot
    }

    enum Diagnosis: Equatable {
        case ready
        case unconfigured
        case missingRepository
        case missingPythonEnvironment
        case missingEmbeddedRuntime
        case missingTrustedModelBundle
        case invalidTrustedModelBundle
        case invalidConfiguredModelCache

        var message: String {
            switch self {
            case .ready:
                return "Local engine environment found."
            case .unconfigured:
                return "Choose a local Auralign repository in Settings."
            case .missingRepository:
                return "The Auralign repository was not found at the configured location."
            case .missingPythonEnvironment:
                return "The engine's Python environment (.venv) is missing. Run `uv sync` "
                    + "with the documented extras inside the repository first."
            case .missingEmbeddedRuntime:
                return "The included Auralign engine is missing or incomplete. Reinstall "
                    + "Auralign from the signed installer."
            case .missingTrustedModelBundle:
                return "The separately installed Auralign model component is missing. "
                    + "Reinstall Auralign from the signed installer."
            case .invalidTrustedModelBundle:
                return "The installed Auralign model component is incomplete or does not "
                    + "match its immutable verification records. Reinstall Auralign from "
                    + "the signed installer."
            case .invalidConfiguredModelCache:
                return "The selected model directory must be an existing, readable local "
                    + "directory and cannot be a symbolic link."
            }
        }
    }

    /// Executable/runtime readiness is intentionally independent from model
    /// delivery. The native setup experience and bridge handshake must remain
    /// available while a large model pack is absent or downloading.
    var runtimeDiagnosis: Diagnosis {
        guard isConfigured else { return .unconfigured }
        let fileManager = FileManager.default
        if usesEmbeddedRuntime {
            guard fileManager.isExecutableFile(atPath: pythonExecutable.path),
                  fileManager.fileExists(atPath: packageDirectory.path),
                  let bundledToolsDirectory,
                  fileManager.isExecutableFile(
                    atPath: bundledToolsDirectory.appendingPathComponent("ffmpeg").path
                  ),
                  fileManager.isExecutableFile(
                    atPath: bundledToolsDirectory.appendingPathComponent("ffprobe").path
                  )
            else {
                return .missingEmbeddedRuntime
            }
        }
        if !usesEmbeddedRuntime {
            guard fileManager.fileExists(atPath: packageDirectory.path) else {
                return .missingRepository
            }
            guard fileManager.isExecutableFile(atPath: pythonExecutable.path) else {
                return .missingPythonEnvironment
            }
        }
        return .ready
    }

    var diagnosis: Diagnosis { runtimeDiagnosis }

    var isRuntimeReady: Bool { runtimeDiagnosis == .ready }

    var isReady: Bool { isRuntimeReady }

    /// Release accepts only the immutable, root-owned component installed by
    /// Auralign's signed package. Debug checkout builds retain their explicit
    /// local-directory workflow.
    var modelBundleDiagnosis: Diagnosis {
        if usesEmbeddedRuntime {
            return Self.trustedModelBundleDiagnosis(at: modelComponentRoot)
        }
        if let configuredModelCacheRoot,
           !configuredModelCachePathWasAbsolute
                || !Self.isUsableExternalModelDirectory(configuredModelCacheRoot) {
            return .invalidConfiguredModelCache
        }
        return .ready
    }

    var modelBundlePresentationMessage: String {
        switch modelBundleDiagnosis {
        case .ready:
            return usesEmbeddedRuntime
                ? "Installed model component records verified."
                : "Local development model directory available."
        default:
            return modelBundleDiagnosis.message
        }
    }

#if DEBUG
    static func embeddedRepositoryRoot(bundle: Bundle = .main) -> URL? {
        guard let embedded = bundle.object(forInfoDictionaryKey: "AuralignEngineRoot")
            as? String,
              !embedded.isEmpty else { return nil }
        return URL(fileURLWithPath: embedded, isDirectory: true).standardizedFileURL
    }
#endif

    static var applicationSupportRoot: URL {
        let root = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support", isDirectory: true)
        return root.appendingPathComponent(
            applicationSupportDirectoryName,
            isDirectory: true
        )
    }

    static var defaultModelCacheRoot: URL {
        applicationSupportRoot.appendingPathComponent("Models", isDirectory: true)
    }

    static var userModelPacksRoot: URL {
        defaultModelCacheRoot.appendingPathComponent("Packs", isDirectory: true)
    }

    static var trustedModelComponentRoot: URL {
        URL(
            fileURLWithPath: "/Library/Application Support/com.auralign.studio/Models",
            isDirectory: true
        )
        .appendingPathComponent(modelBundleIdentifier, isDirectory: true)
    }

    static var applicationCacheRoot: URL {
        let root = FileManager.default.urls(
            for: .cachesDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Caches", isDirectory: true)
        return root.appendingPathComponent(
            applicationSupportDirectoryName,
            isDirectory: true
        )
    }

    static var applicationLogsRoot: URL {
        let root = FileManager.default.urls(
            for: .libraryDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
        return root
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent(
                applicationSupportDirectoryName,
                isDirectory: true
            )
    }

    static var applicationTemporaryRoot: URL {
        applicationCacheRoot.appendingPathComponent("Temporary", isDirectory: true)
    }

    static func configuredModelCacheRoot(
        userDefaults: UserDefaults = .standard
    ) -> (root: URL?, pathWasAbsolute: Bool) {
        guard let override = userDefaults.string(forKey: modelCacheDefaultsKey),
              !override.isEmpty
        else {
            return (nil, true)
        }
        return (
            URL(fileURLWithPath: override, isDirectory: true).standardizedFileURL,
            (override as NSString).isAbsolutePath
        )
    }

    static func modelCacheRoot(userDefaults: UserDefaults = .standard) -> URL {
        configuredModelCacheRoot(userDefaults: userDefaults).root
            ?? defaultModelCacheRoot
    }

    static func isUsableExternalModelDirectory(_ url: URL) -> Bool {
        let path = url.standardizedFileURL.path
        var isDirectory: ObjCBool = false
        guard (path as NSString).isAbsolutePath,
              FileManager.default.fileExists(
                atPath: path,
                isDirectory: &isDirectory
              ),
              isDirectory.boolValue,
              FileManager.default.isReadableFile(atPath: path),
              let values = try? url.resourceValues(
                forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
              ),
              values.isDirectory == true,
              values.isSymbolicLink != true
        else {
            return false
        }
        return true
    }

    /// Validate the installed component's immutable root and its hash-linked
    /// completion records without mutating it or hashing multi-gigabyte model
    /// payloads on the main thread. The Python readiness authority performs the
    /// exact per-model payload digest verification before work can begin.
    static func trustedModelBundleDiagnosis(
        at componentRoot: URL,
        expectedOwnerUID: uid_t = 0
    ) -> Diagnosis {
        let root = componentRoot.standardizedFileURL
        var rootInformation = stat()
        guard lstat(root.path, &rootInformation) == 0 else {
            return errno == ENOENT ? .missingTrustedModelBundle : .invalidTrustedModelBundle
        }
        guard root.lastPathComponent == modelBundleIdentifier,
              isTrustedDirectory(rootInformation, expectedOwnerUID: expectedOwnerUID)
        else {
            return .invalidTrustedModelBundle
        }

        let models = root.appendingPathComponent("models", isDirectory: true)
        let licenses = root.appendingPathComponent("licenses", isDirectory: true)
        guard isTrustedDirectory(at: models, expectedOwnerUID: expectedOwnerUID),
              isTrustedDirectory(at: licenses, expectedOwnerUID: expectedOwnerUID)
        else {
            return .invalidTrustedModelBundle
        }

        let requiredPublicFiles = [
            "THIRD-PARTY-NOTICES.txt",
            "model-bundle-provenance.json",
            "licenses/MIT.txt",
            "licenses/Apache-2.0.txt",
            "licenses/CC-BY-4.0.txt",
        ]
        guard requiredPublicFiles.allSatisfy({
            isTrustedRegularFile(
                at: root.appendingPathComponent($0),
                expectedOwnerUID: expectedOwnerUID
            )
        }) else {
            return .invalidTrustedModelBundle
        }

        guard let manifestData = trustedMetadataData(
            at: root.appendingPathComponent("model-bundle-manifest.json"),
            expectedOwnerUID: expectedOwnerUID
        ),
        let verificationData = trustedMetadataData(
            at: root.appendingPathComponent("model-bundle-verification.json"),
            expectedOwnerUID: expectedOwnerUID
        ),
        let completionData = trustedMetadataData(
            at: root.appendingPathComponent("model-bundle-complete.json"),
            expectedOwnerUID: expectedOwnerUID
        ),
        let manifest = try? JSONDecoder().decode(
            TrustedModelManifestHeader.self,
            from: manifestData
        ),
        let verification = try? JSONDecoder().decode(
            TrustedModelVerification.self,
            from: verificationData
        ),
        let completion = try? JSONDecoder().decode(
            TrustedModelCompletion.self,
            from: completionData
        )
        else {
            return .invalidTrustedModelBundle
        }

        let manifestSHA256 = sha256(manifestData)
        let verificationSHA256 = sha256(verificationData)
        guard manifest.schemaVersion == modelBundleSchemaVersion,
              manifest.layoutVersion == "1",
              manifest.bundleId == modelBundleIdentifier,
              manifest.bundleVersion == modelBundleVersion,
              manifest.modelKeys == strictModelKeys,
              trustedPayloadStructureIsValid(
                  manifest.models,
                  componentRoot: root,
                  expectedOwnerUID: expectedOwnerUID
              ),
              trustedRuntimeAssetsAreValid(
                  manifest.runtimeAssets,
                  componentRoot: root,
                  expectedOwnerUID: expectedOwnerUID
              ),
              verification.schemaVersion == modelBundleSchemaVersion,
              verification.recordType == "auralign-model-bundle-verification",
              verification.bundleId == modelBundleIdentifier,
              verification.bundleVersion == modelBundleVersion,
              verification.state == "verified",
              verification.modelKeys == strictModelKeys,
              verification.runtimeAssetKeys == [punktRuntimeAssetKey],
              verification.manifestSHA256 == manifestSHA256,
              verification.checks == verificationChecks,
              completion.schemaVersion == modelBundleSchemaVersion,
              completion.recordType == "auralign-model-bundle-completion",
              completion.bundleId == modelBundleIdentifier,
              completion.bundleVersion == modelBundleVersion,
              completion.state == "complete",
              completion.manifestSHA256 == manifestSHA256,
              completion.verificationRecordSHA256 == verificationSHA256
        else {
            return .invalidTrustedModelBundle
        }
        return .ready
    }

    private static func isTrustedDirectory(
        at url: URL,
        expectedOwnerUID: uid_t
    ) -> Bool {
        var information = stat()
        guard lstat(url.standardizedFileURL.path, &information) == 0 else {
            return false
        }
        return isTrustedDirectory(information, expectedOwnerUID: expectedOwnerUID)
    }

    private static func isTrustedDirectory(
        _ information: stat,
        expectedOwnerUID: uid_t
    ) -> Bool {
        (information.st_mode & S_IFMT) == S_IFDIR
            && information.st_uid == expectedOwnerUID
            && (information.st_mode & 0o777) == 0o555
    }

    private static func isTrustedRegularFile(
        at url: URL,
        expectedOwnerUID: uid_t
    ) -> Bool {
        var information = stat()
        guard lstat(url.standardizedFileURL.path, &information) == 0 else {
            return false
        }
        return (information.st_mode & S_IFMT) == S_IFREG
            && information.st_uid == expectedOwnerUID
            && information.st_nlink == 1
            && (information.st_mode & 0o777) == 0o444
    }

    private static func trustedPayloadStructureIsValid(
        _ models: [TrustedModelRecord],
        componentRoot: URL,
        expectedOwnerUID: uid_t
    ) -> Bool {
        guard models.map(\.key) == strictModelKeys else { return false }
        for model in models {
            guard isSafeDirectoryComponent(model.directoryName),
                  model.productClassification == "production",
                  model.blocker == nil,
                  !model.backendCompatibility.isEmpty,
                  !model.modificationStatus.isEmpty,
                  model.modificationStatus != "unreviewed",
                  model.packageBundleId == modelBundleIdentifier,
                  model.packageRelativePath == "models/\(model.directoryName)",
                  !model.files.isEmpty,
                  Set(model.files.map(\.path)).count == model.files.count
            else {
                return false
            }

            let modelRoot = componentRoot
                .appendingPathComponent("models", isDirectory: true)
                .appendingPathComponent(model.directoryName, isDirectory: true)
            guard isTrustedDirectory(
                at: modelRoot,
                expectedOwnerUID: expectedOwnerUID
            ) else {
                return false
            }

            let prefix = "models/\(model.directoryName)/"
            for file in model.files {
                guard file.path.hasPrefix(prefix),
                      isSafeRelativePath(file.path),
                      file.size >= 0,
                      isValidSHA256(file.sha256)
                else {
                    return false
                }
                let components = file.path.split(separator: "/").map(String.init)
                var current = componentRoot
                for component in components.dropLast() {
                    current.appendPathComponent(component, isDirectory: true)
                    guard isTrustedDirectory(
                        at: current,
                        expectedOwnerUID: expectedOwnerUID
                    ) else {
                        return false
                    }
                }
                let payloadFile = componentRoot.appendingPathComponent(file.path)
                var information = stat()
                guard lstat(payloadFile.standardizedFileURL.path, &information) == 0,
                      (information.st_mode & S_IFMT) == S_IFREG,
                      information.st_uid == expectedOwnerUID,
                      information.st_nlink == 1,
                      (information.st_mode & 0o777) == 0o444,
                      Int64(information.st_size) == file.size
                else {
                    return false
                }
            }
        }
        return true
    }

    private static func trustedRuntimeAssetsAreValid(
        _ assets: [TrustedRuntimeAssetRecord],
        componentRoot: URL,
        expectedOwnerUID: uid_t
    ) -> Bool {
        guard assets.count == 1, let asset = assets.first,
              asset.key == punktRuntimeAssetKey,
              asset.runtimeCacheRelativeRoot == "runtime-assets/nltk_data",
              asset.packageRelativeRoot == punktRuntimeRoot,
              asset.schemaVersion == "ltm-generated-punkt.v1",
              asset.generator == "nltk.tokenize.punkt.save_punkt_params",
              asset.nltkVersion == "3.9.4",
              asset.trainingData == "none",
              asset.marker.path == punktMarkerPath,
              asset.marker.size > 0,
              isValidSHA256(asset.marker.sha256),
              trustedPayloadFileIsValid(
                  asset.marker,
                  componentRoot: componentRoot,
                  expectedOwnerUID: expectedOwnerUID
              )
        else {
            return false
        }

        let expectedPaths = punktFiles.map { "\(punktLanguageRoot)/\($0)" }
        guard asset.files.map(\.path) == expectedPaths else { return false }
        return asset.files.allSatisfy { file in
            file.size == 0
                && file.sha256 == emptyFileSHA256
                && trustedPayloadFileIsValid(
                    file,
                    componentRoot: componentRoot,
                    expectedOwnerUID: expectedOwnerUID
                )
        }
    }

    private static func trustedPayloadFileIsValid(
        _ file: TrustedModelFileRecord,
        componentRoot: URL,
        expectedOwnerUID: uid_t
    ) -> Bool {
        guard isSafeRelativePath(file.path),
              file.size >= 0,
              isValidSHA256(file.sha256)
        else {
            return false
        }
        let components = file.path.split(separator: "/").map(String.init)
        var current = componentRoot
        for component in components.dropLast() {
            current.appendPathComponent(component, isDirectory: true)
            guard isTrustedDirectory(
                at: current,
                expectedOwnerUID: expectedOwnerUID
            ) else {
                return false
            }
        }
        let payloadFile = componentRoot.appendingPathComponent(file.path)
        var information = stat()
        return lstat(payloadFile.standardizedFileURL.path, &information) == 0
            && (information.st_mode & S_IFMT) == S_IFREG
            && information.st_uid == expectedOwnerUID
            && information.st_nlink == 1
            && (information.st_mode & 0o777) == 0o444
            && Int64(information.st_size) == file.size
    }

    private static func isSafeDirectoryComponent(_ value: String) -> Bool {
        !value.isEmpty
            && value != "."
            && value != ".."
            && !value.contains("/")
            && !value.contains("\\")
            && !value.contains("\0")
    }

    private static func isSafeRelativePath(_ value: String) -> Bool {
        guard !value.isEmpty,
              !value.hasPrefix("/"),
              !value.contains("\\"),
              !value.contains("\0")
        else {
            return false
        }
        return value.split(separator: "/", omittingEmptySubsequences: false)
            .allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
    }

    private static func isValidSHA256(_ value: String) -> Bool {
        value.utf8.count == 64
            && value.utf8.allSatisfy {
                (48...57).contains($0) || (97...102).contains($0)
            }
    }

    private static func trustedMetadataData(
        at url: URL,
        expectedOwnerUID: uid_t
    ) -> Data? {
        var information = stat()
        guard lstat(url.standardizedFileURL.path, &information) == 0,
              (information.st_mode & S_IFMT) == S_IFREG,
              information.st_uid == expectedOwnerUID,
              information.st_nlink == 1,
              (information.st_mode & 0o777) == 0o444,
              information.st_size > 0,
              information.st_size <= trustedMetadataMaximumBytes
        else {
            return nil
        }
        return try? Data(contentsOf: url, options: [.mappedIfSafe])
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    static func packagedRuntimeRoot(bundle: Bundle = .main) -> URL {
        bundle.bundleURL
            .appendingPathComponent("Contents/Frameworks", isDirectory: true)
            .appendingPathComponent(runtimeBundleName, isDirectory: true)
            .appendingPathComponent("Contents/Resources/Runtime", isDirectory: true)
    }

    static func resolve(
        userDefaults: UserDefaults = .standard,
        bundle: Bundle = .main
    ) -> EngineLocation {
#if DEBUG
        let modelOverride = configuredModelCacheRoot(userDefaults: userDefaults)
        return resolve(
            override: userDefaults.string(forKey: engineRootDefaultsKey),
            embeddedRepositoryRoot: embeddedRepositoryRoot(bundle: bundle),
            configuredModelCacheRoot: modelOverride.root,
            configuredModelCachePathWasAbsolute: modelOverride.pathWasAbsolute
        )
#else
        _ = userDefaults
        let runtimeRoot = packagedRuntimeRoot(bundle: bundle)
        return EngineLocation(
            repositoryRoot: applicationSupportRoot,
            origin: .embeddedRuntime,
            embeddedRuntimeRoot: runtimeRoot,
            installedModelComponentRoot: trustedModelComponentRoot
        )
#endif
    }

#if DEBUG
    static func resolve(
        override: String?,
        embeddedRepositoryRoot: URL?,
        configuredModelCacheRoot: URL? = nil,
        configuredModelCachePathWasAbsolute: Bool = true
    ) -> EngineLocation {
        if let override, !override.isEmpty {
            return EngineLocation(
                repositoryRoot: URL(fileURLWithPath: override, isDirectory: true),
                origin: .persistedOverride,
                configuredModelCacheRoot: configuredModelCacheRoot,
                configuredModelCachePathWasAbsolute:
                    configuredModelCachePathWasAbsolute
            )
        }
        if let embeddedRepositoryRoot {
            return EngineLocation(
                repositoryRoot: embeddedRepositoryRoot,
                origin: .embeddedBuildDefault,
                configuredModelCacheRoot: configuredModelCacheRoot,
                configuredModelCachePathWasAbsolute:
                    configuredModelCachePathWasAbsolute
            )
        }
        return EngineLocation(
            repositoryRoot: unconfiguredRepositoryRoot,
            origin: .unconfigured,
            configuredModelCacheRoot: configuredModelCacheRoot,
            configuredModelCachePathWasAbsolute:
                configuredModelCachePathWasAbsolute
        )
    }
#endif
}

private struct TrustedModelManifestHeader: Decodable {
    var schemaVersion: String
    var layoutVersion: String
    var bundleId: String
    var bundleVersion: String
    var modelKeys: [String]
    var models: [TrustedModelRecord]
    var runtimeAssets: [TrustedRuntimeAssetRecord]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case layoutVersion = "layout_version"
        case bundleId = "bundle_id"
        case bundleVersion = "bundle_version"
        case modelKeys = "model_keys"
        case models
        case runtimeAssets = "runtime_assets"
    }
}

private struct TrustedModelRecord: Decodable {
    var key: String
    var directoryName: String
    var productClassification: String
    var blocker: String?
    var backendCompatibility: String
    var modificationStatus: String
    var packageBundleId: String
    var packageRelativePath: String
    var files: [TrustedModelFileRecord]

    enum CodingKeys: String, CodingKey {
        case key, blocker, files
        case directoryName = "directory_name"
        case productClassification = "product_classification"
        case backendCompatibility = "backend_compatibility"
        case modificationStatus = "modification_status"
        case packageBundleId = "package_bundle_id"
        case packageRelativePath = "package_relative_path"
    }
}

private struct TrustedModelFileRecord: Decodable {
    var path: String
    var size: Int64
    var sha256: String
}

private struct TrustedRuntimeAssetRecord: Decodable {
    var key: String
    var runtimeCacheRelativeRoot: String
    var packageRelativeRoot: String
    var schemaVersion: String
    var generator: String
    var nltkVersion: String
    var trainingData: String
    var marker: TrustedModelFileRecord
    var files: [TrustedModelFileRecord]

    enum CodingKeys: String, CodingKey {
        case key, generator, marker, files
        case runtimeCacheRelativeRoot = "runtime_cache_relative_root"
        case packageRelativeRoot = "package_relative_root"
        case schemaVersion = "schema_version"
        case nltkVersion = "nltk_version"
        case trainingData = "training_data"
    }
}

private struct TrustedModelVerification: Decodable {
    var schemaVersion: String
    var recordType: String
    var bundleId: String
    var bundleVersion: String
    var state: String
    var modelKeys: [String]
    var runtimeAssetKeys: [String]
    var manifestSHA256: String
    var checks: [String]

    enum CodingKeys: String, CodingKey {
        case state, checks
        case schemaVersion = "schema_version"
        case recordType = "record_type"
        case bundleId = "bundle_id"
        case bundleVersion = "bundle_version"
        case modelKeys = "model_keys"
        case runtimeAssetKeys = "runtime_asset_keys"
        case manifestSHA256 = "manifest_sha256"
    }
}

private struct TrustedModelCompletion: Decodable {
    var schemaVersion: String
    var recordType: String
    var bundleId: String
    var bundleVersion: String
    var state: String
    var manifestSHA256: String
    var verificationRecordSHA256: String

    enum CodingKeys: String, CodingKey {
        case state
        case schemaVersion = "schema_version"
        case recordType = "record_type"
        case bundleId = "bundle_id"
        case bundleVersion = "bundle_version"
        case manifestSHA256 = "manifest_sha256"
        case verificationRecordSHA256 = "verification_record_sha256"
    }
}
