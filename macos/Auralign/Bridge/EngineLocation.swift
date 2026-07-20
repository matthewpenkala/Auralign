// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

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

    private static let unconfiguredRepositoryRoot = URL(
        fileURLWithPath: "/dev/null/auralign-engine-unconfigured",
        isDirectory: true
    )

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

    var modelCacheRoot: URL {
        configuredModelCacheRoot ?? Self.defaultModelCacheRoot
    }

    enum Diagnosis: Equatable {
        case ready
        case unconfigured
        case missingRepository
        case missingPythonEnvironment
        case missingEmbeddedRuntime
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
            case .invalidConfiguredModelCache:
                return "The selected model directory must be an existing, readable local "
                    + "directory and cannot be a symbolic link."
            }
        }
    }

    var diagnosis: Diagnosis {
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
        if let configuredModelCacheRoot,
           !configuredModelCachePathWasAbsolute
                || !Self.isUsableExternalModelDirectory(configuredModelCacheRoot) {
            return .invalidConfiguredModelCache
        }
        return .ready
    }

    var isReady: Bool { diagnosis == .ready }

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
        let runtimeRoot = packagedRuntimeRoot(bundle: bundle)
        let modelOverride = configuredModelCacheRoot(userDefaults: userDefaults)
        return EngineLocation(
            repositoryRoot: applicationSupportRoot,
            origin: .embeddedRuntime,
            embeddedRuntimeRoot: runtimeRoot,
            configuredModelCacheRoot: modelOverride.root,
            configuredModelCachePathWasAbsolute: modelOverride.pathWasAbsolute
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
