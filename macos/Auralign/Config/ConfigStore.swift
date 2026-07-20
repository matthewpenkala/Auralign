// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation
import SwiftUI

/// Central configuration state: preset baseline, user overrides, effective
/// values, and Pydantic-authoritative validation results.
///
/// Value semantics per field:
/// - engine default: from the generated manifest (`PipelineConfig()` defaults)
/// - preset value: the effective baseline reported by the engine for the
///   selected preset, including engine-applied environment overrides
/// - override: an explicit user edit stored as a dotted-key patch
/// - managed value: an installed-application policy that user edits and imports
///   cannot replace
/// - effective: preset baseline, then user overrides, then managed values —
///   exactly what crosses the bridge when a job starts
@MainActor
final class ConfigStore: ObservableObject {
    enum EngineSyncError: LocalizedError, Equatable {
        case noBridge
        case attachmentChanged
        case malformedPresets
        case missingPresets([String])
        case malformedEnvironmentOverrides
        case malformedSchema
        case manifestMismatch
        case uncataloguedFields([String])

        var errorDescription: String? {
            switch self {
            case .noBridge:
                return "No compatible engine bridge is attached."
            case .attachmentChanged:
                return "The engine location changed while configuration metadata was loading."
            case .malformedPresets:
                return "The engine returned a malformed preset response."
            case .missingPresets(let names):
                return "The engine did not return required presets: \(names.joined(separator: ", "))."
            case .malformedEnvironmentOverrides:
                return "The engine returned malformed environment-override metadata."
            case .malformedSchema:
                return "The engine returned a configuration schema that this app could not decode."
            case .manifestMismatch:
                return "The live engine configuration schema differs from this app build."
            case .uncataloguedFields(let keys):
                return "The live engine exposes uncatalogued fields: \(keys.joined(separator: ", "))."
            }
        }
    }

    let manifest: ConfigManifest
    let catalog: FieldCatalog

    @Published private(set) var presets: [String: JSONValue]
    @Published private(set) var environmentOverrides: [String: String] = [:]
    @Published private(set) var selectedPreset: String = "max"
    @Published private(set) var overrides: [String: JSONValue] = [:] {
        didSet { scheduleValidation() }
    }
    @Published private(set) var managedValues: [String: JSONValue] = [:]
    @Published private(set) var fieldErrors: [ConfigFieldError] = []
    @Published private(set) var isValidating = false
    /// A transport/protocol failure distinct from authoritative field errors.
    @Published private(set) var validationError: String?
    /// Fingerprint for the most recent validation response, successful or not.
    @Published private(set) var validationFingerprint: String?
    /// True only after presets and the live schema were loaded atomically.
    @Published private(set) var isEngineSynchronized = false
    @Published private(set) var engineSyncError: String?
    /// Set when the live engine schema disagrees with the bundled manifest.
    @Published private(set) var manifestDriftDetected = false
    /// Fields in the live schema that the bundled catalog does not know yet.
    @Published private(set) var uncataloguedLiveFields: [ConfigField] = []

    private let fallbackPresets: [String: JSONValue]
    private var bridge: EngineBridge?
    private var validationTask: Task<Void, Never>?
    private var attachmentGeneration = 0
    private var validationGeneration = 0
    /// User-edit boundary supplied by the composition root. Engine attachment
    /// and authoritative validation remain internal lifecycle operations, but
    /// every public configuration edit fails closed while the active job owns
    /// its subprocess.
    private var mutationAllowed: () -> Bool = { true }

    init(manifest: ConfigManifest, catalog: FieldCatalog) {
        self.manifest = manifest
        self.catalog = catalog
        var fallbackPresets: [String: JSONValue] = [:]
        if let presetField = manifest.field("preset"), let choices = presetField.choices {
            for preset in choices {
                fallbackPresets[preset] = manifest.defaultConfiguration()
                    .setting("preset", to: .string(preset))
            }
        }
        self.fallbackPresets = fallbackPresets
        self.presets = fallbackPresets
    }

    func setMutationGuard(_ mutationAllowed: @escaping () -> Bool) {
        self.mutationAllowed = mutationAllowed
    }

    /// Replace the application-owned configuration policy. Managed values are
    /// applied after all imported or directly edited values and cannot remain
    /// in the user override patch.
    func setManagedValues(_ values: [String: JSONValue]) {
        let knownKeys = Set(manifest.fields.map(\.key))
        let accepted = values.filter { knownKeys.contains($0.key) }
        guard accepted != managedValues
                || !overrides.keys.allSatisfy({ accepted[$0] == nil })
        else {
            return
        }
        managedValues = accepted
        overrides = overrides.filter { accepted[$0.key] == nil }
        scheduleValidation()
    }

    // MARK: - Engine attachment

    func attach(bridge: EngineBridge) {
        attachmentGeneration &+= 1
        validationGeneration &+= 1
        validationTask?.cancel()
        self.bridge = bridge
        presets = fallbackPresets
        environmentOverrides = [:]
        isEngineSynchronized = false
        engineSyncError = nil
        manifestDriftDetected = false
        uncataloguedLiveFields = []
        clearValidationState()
    }

    /// Remove all state derived from an engine checkout. User overrides remain
    /// intact so choosing a new checkout cannot silently discard edits.
    @discardableResult
    func detach(reason: String? = nil) -> Task<Void, Never>? {
        let pendingValidation = validationTask
        attachmentGeneration &+= 1
        validationGeneration &+= 1
        pendingValidation?.cancel()
        bridge = nil
        presets = fallbackPresets
        environmentOverrides = [:]
        isEngineSynchronized = false
        engineSyncError = reason
        manifestDriftDetected = false
        uncataloguedLiveFields = []
        clearValidationState()
        return pendingValidation
    }

    /// Cancels and awaits the complete serialized validation chain. AppModel
    /// calls this before application termination so no one-shot Python child
    /// or delayed validation mutation can outlive the owning app.
    func cancelOutstandingWorkAndWait() async {
        validationGeneration &+= 1
        let cancellationGeneration = validationGeneration
        let pending = validationTask
        pending?.cancel()
        if let pending {
            await pending.value
        }
        if validationGeneration == cancellationGeneration {
            validationTask = nil
        }
        isValidating = false
    }

    /// Replace fallback baselines with engine-reported effective presets and
    /// verify the live schema. The update is atomic: a partial or stale
    /// response never becomes the effective baseline.
    func refreshFromEngine() async throws {
        guard let attachedBridge = bridge else {
            throw EngineSyncError.noBridge
        }
        let generation = attachmentGeneration

        do {
            let presetPayload = try await attachedBridge.runExpecting(
                event: "presets",
                command: ["presets"]
            )
            try requireCurrentAttachment(attachedBridge, generation: generation)
            guard let presetObject = presetPayload.value(at: "presets")?.objectValue,
                  !presetObject.isEmpty,
                  presetObject.values.allSatisfy({ $0.objectValue != nil })
            else {
                throw EngineSyncError.malformedPresets
            }
            let missingPresets = Set(presetNames).subtracting(presetObject.keys).sorted()
            guard missingPresets.isEmpty else {
                throw EngineSyncError.missingPresets(missingPresets)
            }
            guard let environmentObject = presetPayload
                .value(at: "environment_overrides")?
                .objectValue
            else {
                throw EngineSyncError.malformedEnvironmentOverrides
            }
            let liveEnvironment = environmentObject.compactMapValues(\.stringValue)
            guard liveEnvironment.count == environmentObject.count else {
                throw EngineSyncError.malformedEnvironmentOverrides
            }

            let schemaPayload = try await attachedBridge.runExpecting(
                event: "schema",
                command: ["schema"]
            )
            try requireCurrentAttachment(attachedBridge, generation: generation)
            let liveManifest: ConfigManifest
            do {
                liveManifest = try JSONDecoder().decode(
                    ConfigManifest.self,
                    from: schemaPayload.encodedData()
                )
            } catch {
                throw EngineSyncError.malformedSchema
            }

            guard liveManifest == manifest else {
                throw EngineSyncError.manifestMismatch
            }
            let knownKeys = Set(catalog.fields.map(\.key))
            let uncatalogued = liveManifest.fields.filter { !knownKeys.contains($0.key) }
            guard uncatalogued.isEmpty else {
                throw EngineSyncError.uncataloguedFields(uncatalogued.map(\.key).sorted())
            }

            try requireCurrentAttachment(attachedBridge, generation: generation)
            presets = presetObject
            environmentOverrides = liveEnvironment
            manifestDriftDetected = false
            uncataloguedLiveFields = []
            engineSyncError = nil
            isEngineSynchronized = true
            scheduleValidation()
        } catch {
            if bridge === attachedBridge, attachmentGeneration == generation {
                presets = fallbackPresets
                environmentOverrides = [:]
                isEngineSynchronized = false
                engineSyncError = error.localizedDescription
                manifestDriftDetected = false
                if let syncError = error as? EngineSyncError,
                   syncError == .manifestMismatch {
                    manifestDriftDetected = true
                }
                if let syncError = error as? EngineSyncError,
                   case .uncataloguedFields(let keys) = syncError {
                    let keySet = Set(keys)
                    uncataloguedLiveFields = manifest.fields.filter {
                        keySet.contains($0.key)
                    }
                } else {
                    uncataloguedLiveFields = []
                }
                invalidateValidation()
            }
            throw error
        }
    }

    // MARK: - Values

    var presetNames: [String] {
        manifest.field("preset")?.choices ?? ["max", "balanced", "fast", "cpu"]
    }

    var baseline: JSONValue {
        presets[selectedPreset] ?? manifest.defaultConfiguration()
    }

    var effectiveConfig: JSONValue {
        var config = baseline.setting("preset", to: .string(selectedPreset))
        for (key, value) in overrides {
            config = config.setting(key, to: value)
        }
        for (key, value) in managedValues {
            config = config.setting(key, to: value)
        }
        return config
    }

    func engineDefault(_ key: String) -> JSONValue {
        manifest.field(key)?.default ?? .null
    }

    func presetValue(_ key: String) -> JSONValue {
        baseline.value(at: key) ?? .null
    }

    func effectiveValue(_ key: String) -> JSONValue {
        if let managed = managedValues[key] { return managed }
        if let override = overrides[key] { return override }
        return presetValue(key)
    }

    func isModified(_ key: String) -> Bool {
        guard managedValues[key] == nil else { return false }
        return overrides[key] != nil && overrides[key] != presetValue(key)
    }

    func isManaged(_ key: String) -> Bool {
        managedValues[key] != nil
    }

    var modifiedKeys: [String] {
        overrides.keys.filter { isModified($0) }.sorted()
    }

    @discardableResult
    func selectPreset(_ preset: String) -> Bool {
        guard mutationAllowed(), presetNames.contains(preset) else { return false }
        guard preset != selectedPreset else { return true }
        selectedPreset = preset
        scheduleValidation()
        return true
    }

    @discardableResult
    func setOverride(_ key: String, to value: JSONValue) -> Bool {
        guard mutationAllowed(), !isManaged(key) else { return false }
        if value == presetValue(key) {
            overrides.removeValue(forKey: key)
        } else {
            overrides[key] = value
        }
        return true
    }

    @discardableResult
    func clearOverride(_ key: String) -> Bool {
        guard mutationAllowed(), !isManaged(key) else { return false }
        overrides.removeValue(forKey: key)
        return true
    }

    @discardableResult
    func clearSection(_ sectionID: String) -> Bool {
        guard mutationAllowed() else { return false }
        let keys = catalog.entries(inSection: sectionID).map(\.key)
        for key in keys where !isManaged(key) {
            overrides.removeValue(forKey: key)
        }
        return true
    }

    @discardableResult
    func clearAllOverrides() -> Bool {
        guard mutationAllowed() else { return false }
        overrides = [:]
        return true
    }

    /// Import overrides from a nested configuration object by diffing it
    /// against the current preset baseline.
    @discardableResult
    func importConfiguration(_ config: JSONValue) -> Bool {
        guard mutationAllowed() else { return false }
        var imported: [String: JSONValue] = [:]
        for field in manifest.fields where !isManaged(field.key) {
            guard let value = config.value(at: field.key) else { continue }
            if value != presetValue(field.key) {
                imported[field.key] = value
            }
        }
        overrides = imported
        return true
    }

    /// Stable fingerprint of the effective configuration, used to mark
    /// completed results as stale after edits.
    var effectiveConfigFingerprint: String {
        effectiveConfig.canonicalJSONString()
    }

    // MARK: - Errors

    func errors(for key: String) -> [ConfigFieldError] {
        fieldErrors.filter { error in
            let location = error.dottedLocation
            return location == key || location.hasPrefix(key + ".")
        }
    }

    /// Errors attached to a section but not to a single exposed field, such as
    /// model-level validators reporting on `diarization` as a whole.
    func sectionErrors(for sectionID: String) -> [ConfigFieldError] {
        let keys = Set(catalog.entries(inSection: sectionID).map(\.key))
        return fieldErrors.filter { error in
            let location = error.dottedLocation
            guard !keys.contains(location) else { return false }
            guard !keys.contains(where: { $0 == location || location.hasPrefix($0 + ".") })
            else { return false }
            return keys.contains { $0.hasPrefix(location + ".") }
        }
    }

    var validationIsCurrent: Bool {
        validationFingerprint == effectiveConfigFingerprint && !isValidating
    }

    /// A second, Swift-side fail-closed boundary for imported/debug-injected
    /// model keys. Python remains the configuration authority, but a value is
    /// not runnable through the GUI unless the bundled authoritative manifest
    /// explicitly marks the exact role/key pair for local runtime use.
    var runtimeModelSelectionIssue: String? {
        manifest.modelPolicyBlockers(for: effectiveConfig).first
    }

    var isValid: Bool {
        isEngineSynchronized
            && validationIsCurrent
            && validationError == nil
            && fieldErrors.isEmpty
            && runtimeModelSelectionIssue == nil
    }

    // MARK: - Validation through the Python authority

    private func scheduleValidation() {
        validationGeneration &+= 1
        let previous = validationTask
        previous?.cancel()
        validationFingerprint = nil
        validationError = nil
        fieldErrors = []
        guard bridge != nil, isEngineSynchronized else {
            isValidating = false
            return
        }
        isValidating = true
        let generation = validationGeneration
        let attachment = attachmentGeneration
        let fingerprint = effectiveConfigFingerprint
        validationTask = Task { [weak self] in
            if let previous {
                await previous.value
            }
            guard !Task.isCancelled else { return }
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            _ = await self?.performValidation(
                generation: generation,
                attachment: attachment,
                fingerprint: fingerprint
            )
        }
    }

    @discardableResult
    func validateNow() async -> Bool {
        let previous = validationTask
        previous?.cancel()
        validationTask = nil
        if let previous {
            await previous.value
        }
        guard !Task.isCancelled else {
            isValidating = false
            return false
        }
        validationGeneration &+= 1
        guard bridge != nil, isEngineSynchronized else {
            invalidateValidation()
            validationError = "Configuration validation is unavailable until engine metadata loads."
            return false
        }
        let generation = validationGeneration
        let attachment = attachmentGeneration
        let fingerprint = effectiveConfigFingerprint
        isValidating = true
        validationFingerprint = nil
        validationError = nil
        fieldErrors = []
        return await performValidation(
            generation: generation,
            attachment: attachment,
            fingerprint: fingerprint
        )
    }

    private func performValidation(
        generation: Int,
        attachment: Int,
        fingerprint: String
    ) async -> Bool {
        guard let validatingBridge = bridge else { return false }
        let payload = JSONValue.object(["config": effectiveConfig])
        do {
            let result = try await validatingBridge.runExpecting(
                event: "validate",
                command: ["validate"],
                input: payload
            )
            guard validationGeneration == generation,
                  attachmentGeneration == attachment,
                  bridge === validatingBridge,
                  effectiveConfigFingerprint == fingerprint
            else {
                return false
            }
            validationFingerprint = fingerprint
            isValidating = false
            guard let accepted = result.value(at: "ok")?.boolValue else {
                validationError = "The engine returned a malformed validation response."
                fieldErrors = []
                return false
            }
            if accepted {
                guard result.value(at: "errors") == nil else {
                    validationError =
                        "The engine returned contradictory validation evidence."
                    fieldErrors = []
                    return false
                }
                validationError = nil
                fieldErrors = []
                return true
            }
            guard let parsedErrors = ConfigFieldError.parse(from: result) else {
                fieldErrors = []
                validationError = "The engine returned malformed validation-error evidence."
                return false
            }
            fieldErrors = parsedErrors
            validationError = parsedErrors.isEmpty
                ? "The engine rejected the configuration without structured field errors."
                : nil
            return false
        } catch {
            guard validationGeneration == generation,
                  attachmentGeneration == attachment,
                  bridge === validatingBridge,
                  effectiveConfigFingerprint == fingerprint
            else {
                return false
            }
            validationFingerprint = nil
            validationError = "Configuration validation failed: \(error.localizedDescription)"
            fieldErrors = []
            isValidating = false
            return false
        }
    }

    private func requireCurrentAttachment(
        _ expectedBridge: EngineBridge,
        generation: Int
    ) throws {
        guard bridge === expectedBridge, attachmentGeneration == generation else {
            throw EngineSyncError.attachmentChanged
        }
    }

    private func clearValidationState() {
        isValidating = false
        validationError = nil
        validationFingerprint = nil
        fieldErrors = []
    }

    private func invalidateValidation() {
        validationGeneration &+= 1
        validationTask?.cancel()
        clearValidationState()
    }

    #if DEBUG
    /// Seeds deterministic presentation evidence through the same state the
    /// production views observe. The fixture path never attaches a bridge and
    /// is compiled out of Release.
    func applyPresentationFixture(
        selectedPreset: String = "balanced",
        overrides: [String: JSONValue] = [:],
        fieldErrors: [ConfigFieldError] = [],
        isValidating: Bool = false,
        validationError: String? = nil,
        validationIsCurrent: Bool = true,
        isEngineSynchronized: Bool = true,
        engineSyncError: String? = nil,
        manifestDriftDetected: Bool = false,
        uncataloguedLiveFields: [ConfigField] = []
    ) {
        validationGeneration &+= 1
        validationTask?.cancel()
        bridge = nil
        self.selectedPreset = selectedPreset
        self.overrides = overrides
        environmentOverrides = [:]
        self.isEngineSynchronized = isEngineSynchronized
        self.engineSyncError = engineSyncError
        self.manifestDriftDetected = manifestDriftDetected
        self.uncataloguedLiveFields = uncataloguedLiveFields
        self.isValidating = isValidating
        self.validationError = validationError
        self.fieldErrors = fieldErrors
        validationFingerprint = validationIsCurrent
            ? effectiveConfigFingerprint
            : nil
    }
    #endif
}
