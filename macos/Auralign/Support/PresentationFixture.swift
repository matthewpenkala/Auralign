// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

#if DEBUG

import Foundation

/// Immutable selectors for deterministic presentation evidence.
///
/// A fixture is only a seed for the production AppModel/ConfigStore/
/// JobController contracts. It is not a second runtime state machine and it
/// never launches the bridge.
enum PresentationFixture: String, CaseIterable, Identifiable {
    case initial = "initial"
    case noInput = "no-input"
    case selectedRunPlan = "selected-run-plan"
    case invalidInput = "invalid-input"
    case runtimeUnavailable = "runtime-unavailable"
    case pythonIncompatible = "python-incompatible"
    case modelUnavailableGated = "model-unavailable-gated"
    case readinessUnknown = "readiness-unknown"
    case readinessChecking = "readiness-checking"
    case readinessStale = "readiness-stale"
    case readinessReady = "readiness-ready"
    case readinessWarnings = "readiness-warnings"
    case readinessBlocked = "readiness-blocked"
    case invalidConfiguration = "invalid-configuration"
    case invalidHiddenAdvanced = "invalid-hidden-advanced"
    case partialEvidence = "partial-evidence"
    case running = "running"
    case resultPendingExit = "result-pending-exit"
    case stopping = "stopping"
    case cancelled = "cancelled"
    case failure = "failure"
    case completed = "completed"
    case resultsRich = "results-rich"
    case resultsStale = "results-stale"
    case settingsValid = "settings-valid"
    case settingsInvalid = "settings-invalid"
    case settingsLocked = "settings-locked"
    case resourceFailure = "resource-failure"
    case longText = "long-text"

    static let enableEnvironmentKey = "AURALIGN_ENABLE_UI_TEST_FIXTURES"
    static let launchArgument = "--ui-fixture"
    static let evidenceLabel = "Synthetic UI Fixture — Not live engine evidence"

    var id: String { rawValue }

    enum Request: Equatable {
        case none
        case fixture(PresentationFixture)
        case invalid
    }

    /// Distinguishes an ordinary launch from an explicitly requested but
    /// malformed fixture launch. Collapsing both cases to `nil` would let a
    /// typo fall through to the live engine and machine-local preferences.
    static func request(
        environment: [String: String],
        arguments: [String]
    ) -> Request {
        let gateValue = environment[enableEnvironmentKey]
        let indexes = arguments.indices.filter { arguments[$0] == launchArgument }
        let fixtureWasRequested = gateValue != nil || !indexes.isEmpty
        guard fixtureWasRequested else { return .none }
        guard gateValue == "1" else { return .invalid }
        guard indexes.count == 1,
              let index = indexes.first,
              arguments.indices.contains(arguments.index(after: index))
        else {
            return .invalid
        }
        let name = arguments[arguments.index(after: index)]
        guard !name.hasPrefix("-"),
              let fixture = PresentationFixture(rawValue: name)
        else {
            return .invalid
        }
        return .fixture(fixture)
    }

    /// Compatibility projection for unit callers that only need the selected
    /// fixture. The application entry point uses `request` so invalid launches
    /// cannot be confused with ordinary live launches.
    static func select(
        environment: [String: String],
        arguments: [String]
    ) -> PresentationFixture? {
        guard case .fixture(let fixture) = request(
            environment: environment,
            arguments: arguments
        ) else {
            return nil
        }
        return fixture
    }

    static func requested(processInfo: ProcessInfo = .processInfo) -> Request {
        request(
            environment: processInfo.environment,
            arguments: processInfo.arguments
        )
    }
}

/// Explicit, debug-only SwiftUI environment overrides for deterministic
/// accessibility layout evidence. They are accepted only through the same
/// fixture + automation gates as SnapshotDriver and are always disclosed in
/// the capture receipt; they never alter the user's system preferences.
struct PresentationAccessibilityOverrides: Equatable {
    static let reduceTransparencyKey =
        "AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY"
    static let increaseContrastKey =
        "AURALIGN_PRESENTATION_INCREASE_CONTRAST"
    static let reduceMotionKey =
        "AURALIGN_PRESENTATION_REDUCE_MOTION"
    static let differentiateWithoutColorKey =
        "AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR"

    let reduceTransparency: Bool?
    let increaseContrast: Bool?
    let reduceMotion: Bool?
    let differentiateWithoutColor: Bool?

    static func authorized(
        environment: [String: String]
    ) -> PresentationAccessibilityOverrides? {
        let keys = [
            reduceTransparencyKey,
            increaseContrastKey,
            reduceMotionKey,
            differentiateWithoutColorKey,
        ]
        guard keys.contains(where: { environment[$0] != nil }),
              environment[PresentationFixture.enableEnvironmentKey] == "1",
              environment["AURALIGN_ENABLE_DEBUG_AUTOMATION"] == "1"
        else { return nil }
        func parse(_ key: String) -> Bool?? {
            guard let raw = environment[key] else { return .some(nil) }
            switch raw {
            case "0": return .some(false)
            case "1": return .some(true)
            default: return nil
            }
        }
        guard let reduceTransparency = parse(reduceTransparencyKey),
              let increaseContrast = parse(increaseContrastKey),
              let reduceMotion = parse(reduceMotionKey),
              let differentiateWithoutColor = parse(
                  differentiateWithoutColorKey
              )
        else { return nil }
        return PresentationAccessibilityOverrides(
            reduceTransparency: reduceTransparency,
            increaseContrast: increaseContrast,
            reduceMotion: reduceMotion,
            differentiateWithoutColor: differentiateWithoutColor
        )
    }
}

#endif
