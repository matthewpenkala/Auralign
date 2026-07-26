// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// Stable UI automation and accessibility identifiers. Values are structural
/// only; they never contain user paths, filenames, transcript text, model
/// messages, or diagnostic content.
enum AccessibilityIdentifier {
    static let primaryAction = "toolbar.primary-action"
    static let primaryActionStatus = "toolbar.primary-action.status"
    static let validationSummary = "validation.summary"
    static let firstValidationError = "validation.first-error"
    static let readinessSummary = "readiness.summary"
    static let readinessHeadline = "readiness.headline"
    static let readinessRecovery = "readiness.recovery"
    static let readinessCounts = "readiness.counts"
    static let readinessRefresh = "readiness.refresh"
    static let readinessReturn = "readiness.return-to-summary"
    static let readinessInspector = "readiness.inspector"
    static let readinessPassedDisclosure = "readiness.passed-disclosure"
    static let modelSetupRoot = "model-setup.root"
    static let modelSetupOpen = "model-setup.open"
    static let modelSetupSummary = "model-setup.summary"
    static let modelSetupInstallRequired = "model-setup.install-required"
    static let modelSetupComplete = "model-setup.complete"
    static let modelSetupPrivacy = "model-setup.privacy"
    static let mediaDropZone = "source.media.drop-zone"
    static let mediaChoose = "source.media.choose"
    static let mediaSummary = "source.media.summary"
    static let mediaProbe = "source.media.probe"
    static let mediaProbeError = "source.media.probe-error"
    static let mediaDropError = "source.media.drop-error"
    static let outputChoose = "output.choose"
    static let outputSummary = "output.summary"
    static let outputRoot = "output.root"
    static let outputJobFolder = "output.job-folder"
    static let debugReport = "job.debug-report"
    static let runPlan = "job.run-plan"
    static let runPlanStages = "job.run-plan.stages"
    static let nextSafeAction = "job.next-safe-action"
    static let resultsTranscript = "results.transcript"
    static let resultsTranscriptTruncated = "results.transcript.truncated"
    static let resultsCopyTranscript = "results.transcript.copy"
    static let resultsInspector = "results.inspector"
    static let resultsCompactDetails = "results.compact-details"
    static let resultsQA = "results.qa"
    static let resultsWarnings = "results.warnings"
    static let resultsStale = "results.stale"
    static let resultsRevealFolder = "results.reveal-folder"
    static let resultsViewActivity = "results.view-activity"
    static let resultsRoot = "results.root"
    static let activitySummary = "activity.summary"
    static let activityStages = "activity.stages"
    static let activityConfiguration = "activity.configuration"
    static let activityWarnings = "activity.warnings"
    static let activityFailure = "activity.failure"
    static let activityTechnicalDetails = "activity.technical-details"
    static let activityPartialReveal = "activity.partial-reveal"
    static let activityViewResults = "activity.view-results"
    static let configurationReturnToOrigin = "configuration.return-to-origin"
    static let settingsRoot = "settings.root"
    static let settingsRepository = "settings.engine.repository"
    static let settingsRepositoryEnvironment = "settings.engine.repository-environment"
    static let settingsConnection = "settings.engine.connection"
    static let settingsChoose = "settings.engine.choose"
    static let settingsBuildDefault = "settings.engine.build-default"
    static let settingsLockReason = "settings.engine.lock-reason"
    static let settingsModelCache = "settings.models.directory"
    static let settingsChooseModelCache = "settings.models.choose"
    static let settingsDefaultModelCache = "settings.models.default"
    static let parameterSearch = "parameters.search"
    static let parameterFilter = "parameters.filter"
    static let parameterCategory = "parameters.category"
    static let parameterCount = "parameters.count"
    static let parameterNoResults = "parameters.no-results"
    static let parameterShowAll = "parameters.show-all"
    static let parameterInspector = "parameters.inspector"

    static func navigation(_ route: String) -> String {
        "nav.\(route)"
    }

    static func advancedGroup(sectionID: String) -> String {
        "group.\(sectionID).advanced"
    }

    static func field(_ key: String) -> String {
        "field.\(key)"
    }

    static func fieldControl(_ key: String) -> String {
        "\(field(key)).control"
    }

    static func fieldError(_ key: String) -> String {
        "\(field(key)).error"
    }

    static func fieldReset(_ key: String) -> String {
        "\(field(key)).reset"
    }

    static func readinessModel(role: String, key: String) -> String {
        "readiness.model.\(role).\(key)"
    }

    static func readinessFacet(_ facet: String) -> String {
        "readiness.facet.\(facet)"
    }

    static func readinessCheck(index: Int) -> String {
        "readiness.check.\(index)"
    }

    static func modelSetupPack(_ identifier: String) -> String {
        "model-setup.pack.\(stableComponent(identifier))"
    }

    static func modelSetupProgress(_ identifier: String) -> String {
        "\(modelSetupPack(identifier)).progress"
    }

    static func modelSetupInstall(_ identifier: String) -> String {
        "\(modelSetupPack(identifier)).install"
    }

    static func modelSetupPause(_ identifier: String) -> String {
        "\(modelSetupPack(identifier)).pause"
    }

    static func modelSetupResume(_ identifier: String) -> String {
        "\(modelSetupPack(identifier)).resume"
    }

    static func modelSetupCancel(_ identifier: String) -> String {
        "\(modelSetupPack(identifier)).cancel"
    }

    static func modelSetupRetry(_ identifier: String) -> String {
        "\(modelSetupPack(identifier)).retry"
    }

    static func modelSetupSource(_ identifier: String) -> String {
        "\(modelSetupPack(identifier)).source"
    }

    static func artifact(index: Int) -> String {
        "results.artifact.\(index)"
    }

    static func artifactActions(index: Int) -> String {
        "\(artifact(index: index)).actions"
    }

    static func activityStage(_ stageID: String) -> String {
        "activity.stage.\(stageID)"
    }

    static func parameterRow(_ key: String) -> String {
        "parameters.row.\(key)"
    }

    static func runPlanStage(_ stageID: String) -> String {
        "job.run-plan.stage.\(stageID)"
    }

    private static func stableComponent(_ value: String) -> String {
        value.unicodeScalars.map { scalar in
            CharacterSet.alphanumerics.contains(scalar)
                ? String(scalar)
                : "-"
        }.joined()
    }
}
