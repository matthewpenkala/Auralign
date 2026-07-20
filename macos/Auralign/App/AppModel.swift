// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import Combine
import Foundation
import SwiftUI

/// Sidebar destinations for the main window.
enum WorkspaceSection: Hashable {
    case newJob
    case configuration(String)
    case allParameters
    case readiness
    case processing
    case results
}

private struct ActiveStageAnnouncement: Equatable {
    let stageID: String
    let ordinal: Int
    let total: Int
    let title: String
}

private struct ValidationAnnouncement: Equatable {
    let count: Int
    let revision: Int
}

struct ResultDrift: Equatable {
    var sourceChanged: Bool
    var configurationChanged: Bool

    var isStale: Bool { sourceChanged || configurationChanged }

    var explanation: String? {
        switch (sourceChanged, configurationChanged) {
        case (true, true):
            return "The selected source and configuration have changed since this run. "
                + "These Results remain associated with the source and settings used at run time."
        case (true, false):
            return "The selected source has changed since this run. These Results remain "
                + "associated with the source used at run time."
        case (false, true):
            return "The configuration has changed since this run. These Results reflect "
                + "the settings used at run time, not the current editor state."
        case (false, false):
            return nil
        }
    }
}

#if DEBUG

extension AppModel {
    /// Construct a deterministic, inert presentation model before any engine
    /// connection can begin. The selector is dual-gated in
    /// `PresentationFixture`; this factory is absent from Release builds.
    static func makePresentationFixture(_ fixture: PresentationFixture) -> AppModel {
        let suite = "com.auralign.studio.synthetic-fixture.\(fixture.rawValue)"
        guard let defaults = UserDefaults(suiteName: suite) else {
            preconditionFailure("Could not create isolated synthetic fixture defaults.")
        }
        defaults.removePersistentDomain(forName: suite)
        let model = AppModel(userDefaults: defaults)
        model.applyPresentationFixture(fixture)
        return model
    }

    /// A malformed gated fixture request is still an inert synthetic launch.
    /// It must never fall through to `.standard` defaults, a real checkout, or
    /// bridge startup.
    static func makeInvalidPresentationFixtureRequest() -> AppModel {
        let suite = "com.auralign.studio.synthetic-fixture.invalid-request"
        guard let defaults = UserDefaults(suiteName: suite) else {
            preconditionFailure("Could not create isolated synthetic fixture defaults.")
        }
        defaults.removePersistentDomain(forName: suite)
        let model = AppModel(userDefaults: defaults)
        model.isPresentationFixture = true
        model.config.setMutationGuard { false }
        model.engineLocation = EngineLocation(
            repositoryRoot: URL(
                fileURLWithPath: "/AURALIGN_SYNTHETIC/InvalidFixtureRequest",
                isDirectory: true
            )
        )
        model.bridge = nil
        model.handshake = nil
        model.isConnectingEngine = false
        model.clearReadiness()
        let resourceSuffix = model.loadFailure.map { " \($0)" } ?? ""
        model.loadFailure =
            "Debug UI fixture request was rejected because its gate or selector "
            + "was missing, unknown, or ambiguous. No live engine was contacted."
            + resourceSuffix
        return model
    }

    private func applyPresentationFixture(_ fixture: PresentationFixture) {
        let syntheticRoot = URL(
            fileURLWithPath: "/AURALIGN_SYNTHETIC/Engine",
            isDirectory: true
        )
        let syntheticInput = URL(
            fileURLWithPath: "/AURALIGN_SYNTHETIC/Media/SYNTHETIC-INTERVIEW-01.wav"
        )
        let syntheticOutput = URL(
            fileURLWithPath: "/AURALIGN_SYNTHETIC/Output",
            isDirectory: true
        )
        let fixedNow = Date(timeIntervalSince1970: 1_768_000_000)

        // Set the execution stopper before assigning any state whose observers
        // could otherwise launch probe/readiness work.
        isPresentationFixture = true
        config.setMutationGuard { false }
        handshakeTask?.cancel()
        inputProbeTask?.cancel()
        readinessTask?.cancel()
        engineLocation = EngineLocation(repositoryRoot: syntheticRoot)
        enginePresentationOverride = EnginePresentationStatus(
            isReady: true,
            message: "Synthetic engine environment available for presentation only.",
            repositoryPath: syntheticRoot.path,
            pythonPath: syntheticRoot.appendingPathComponent(".venv/bin/python").path
        )
        outputRootUsableOverride = true
        presentationDate = fixedNow
        startupError = nil
        isConnectingEngine = false
        doctorEvidenceFailure = nil
        modelEvidenceFailure = nil
        isRunningDoctor = false
        inputURL = nil
        inputProbe = nil
        inputProbeSnapshot = nil
        inputProbeError = nil
        outputRootURL = nil
        writeDebugReport = false
        selectedSection = .newJob
        fieldNavigationRequest = nil
        consumedFieldNavigationRequestID = nil
        keyboardFocusedConfigurationControlIdentifier = nil
        sectionNavigationRequest = nil
        consumedSectionNavigationRequestID = nil

        config.applyPresentationFixture(
            selectedPreset: "balanced",
            validationIsCurrent: true,
            isEngineSynchronized: true
        )
        bridge = EngineBridge(location: engineLocation)
        handshake = syntheticHandshake()
        seedCompleteModelEvidence()
        seedDoctorEvidence()
        doctorFingerprint = config.effectiveConfigFingerprint
        job.applyPresentationFixture(phase: .idle, now: fixedNow)

        switch fixture {
        case .initial:
            bridge = nil
            handshake = nil
            isConnectingEngine = true
            clearReadiness()
            config.applyPresentationFixture(
                validationIsCurrent: false,
                isEngineSynchronized: false,
                engineSyncError: "Synthetic first-launch metadata is still loading."
            )

        case .noInput:
            break

        case .selectedRunPlan:
            seedSelectedInput(syntheticInput, output: syntheticOutput)

        case .invalidInput:
            inputURL = syntheticInput
            inputProbe = nil
            inputProbeError = "Synthetic media inspection rejected this file."
            outputRootURL = syntheticOutput

        case .runtimeUnavailable:
            selectedSection = .readiness
            bridge = nil
            handshake = nil
            startupError = "Synthetic local runtime is unavailable."
            enginePresentationOverride = EnginePresentationStatus(
                isReady: false,
                message: "Synthetic Python environment is missing.",
                repositoryPath: syntheticRoot.path,
                pythonPath: syntheticRoot.appendingPathComponent(".venv/bin/python").path
            )
            clearReadiness()

        case .pythonIncompatible:
            selectedSection = .readiness
            bridge = nil
            handshake = nil
            startupError = "Synthetic engine uses Python 3.12; Auralign requires Python 3.11."
            clearReadiness()

        case .modelUnavailableGated:
            selectedSection = .readiness
            seedCompleteModelEvidence(missingSelectedModels: true)
            seedDoctorEvidence(
                ok: false,
                issueStatus: "error",
                issueMessage: "Synthetic gated model is not present."
            )
            doctorFingerprint = config.effectiveConfigFingerprint

        case .readinessUnknown:
            selectedSection = .readiness
            doctor = nil
            doctorFingerprint = nil
            modelStatuses = []
            doctorEvidenceFailure = nil
            modelEvidenceFailure = nil

        case .readinessChecking:
            selectedSection = .readiness
            doctor = nil
            doctorFingerprint = nil
            modelStatuses = []
            doctorEvidenceFailure = nil
            modelEvidenceFailure = nil
            isRunningDoctor = true

        case .readinessStale:
            selectedSection = .readiness
            doctorFingerprint = "synthetic-stale-fingerprint"
            modelStatuses = []
            doctorEvidenceFailure = nil
            modelEvidenceFailure = nil

        case .readinessReady:
            selectedSection = .readiness

        case .readinessWarnings:
            selectedSection = .readiness
            seedDoctorEvidence(
                ok: true,
                issueStatus: "warning",
                issueMessage: "Synthetic optional accelerator warning."
            )
            doctorFingerprint = config.effectiveConfigFingerprint

        case .readinessBlocked:
            selectedSection = .readiness
            seedDoctorEvidence(
                ok: false,
                issueStatus: "error",
                issueMessage: "Synthetic backend startup failed."
            )
            doctorFingerprint = config.effectiveConfigFingerprint

        case .invalidConfiguration:
            let error = ConfigFieldError(
                location: ["asr", "beam_size"],
                message: "Synthetic beam size is outside the accepted range.",
                type: "less_than_equal"
            )
            config.applyPresentationFixture(
                overrides: ["asr.beam_size": .int(999)],
                fieldErrors: [error]
            )
            doctor = nil
            doctorFingerprint = nil

        case .invalidHiddenAdvanced:
            let error = ConfigFieldError(
                location: ["asr", "temperature"],
                message: "Synthetic incomplete exponent is retained.",
                type: "float_parsing"
            )
            config.applyPresentationFixture(
                overrides: ["asr.temperature": .string("1e")],
                fieldErrors: [error]
            )
            selectedSection = .configuration("asr")
            fieldNavigationSequence = 1
            fieldNavigationRequest = FieldNavigationRequest(
                id: 1,
                sectionID: "asr",
                fieldKey: "asr.temperature",
                message: error.message,
                revealAdvanced: true,
                returnSection: .newJob
            )
            doctor = nil
            doctorFingerprint = nil

        case .partialEvidence:
            selectedSection = .readiness
            doctor = nil
            doctorFingerprint = nil
            doctorEvidenceFailure = ReadinessEvidenceFailure(
                source: .doctor,
                kind: .partial,
                summary: "Synthetic runtime-check evidence is incomplete.",
                technicalDetail: "Synthetic startup coverage was not complete."
            )
            modelStatuses = []
            modelEvidenceFailure = ReadinessEvidenceFailure(
                source: .models,
                kind: .malformed,
                summary: "Synthetic model evidence is malformed.",
                technicalDetail: "Synthetic model rows did not satisfy the strict schema."
            )

        case .running:
            selectedSection = .processing
            seedJob(
                phase: .running,
                ownsActiveProcess: true,
                fixedNow: fixedNow
            )

        case .resultPendingExit:
            selectedSection = .processing
            seedJob(
                phase: .resultPendingExit,
                ownsActiveProcess: true,
                allStagesDone: true,
                fixedNow: fixedNow
            )

        case .stopping:
            selectedSection = .processing
            seedJob(
                phase: .stopping,
                ownsActiveProcess: true,
                fixedNow: fixedNow
            )

        case .cancelled:
            selectedSection = .processing
            job.applyPresentationFixture(
                phase: .cancelled(
                    outputDirectory: syntheticOutput,
                    partialArtifacts: syntheticArtifacts()
                ),
                startedAt: fixedNow.addingTimeInterval(-180),
                finishedAt: fixedNow,
                recentEvents: ["job_accepted", "asr_start", "cancelled"],
                jobDirectory: syntheticOutput,
                now: fixedNow
            )

        case .failure:
            selectedSection = .processing
            job.applyPresentationFixture(
                phase: .failed(
                    JobFailure(
                        errorType: "SyntheticControlledFailure",
                        message: "Synthetic backend startup failed safely.",
                        remediation: "Review the synthetic technical details.",
                        diagnostics: "synthetic redacted diagnostic",
                        partialArtifacts: syntheticArtifacts(),
                        outputDirectory: syntheticOutput
                    )
                ),
                startedAt: fixedNow.addingTimeInterval(-95),
                finishedAt: fixedNow,
                recentEvents: ["preflight_start", "bridge_error"],
                jobDirectory: syntheticOutput,
                diagnosticsTail: "synthetic redacted diagnostic",
                now: fixedNow
            )

        case .completed:
            selectedSection = .processing
            seedCompletedJob(rich: false, stale: false, fixedNow: fixedNow)

        case .resultsRich:
            selectedSection = .results
            seedCompletedJob(rich: true, stale: false, fixedNow: fixedNow)

        case .resultsStale:
            selectedSection = .results
            seedCompletedJob(rich: true, stale: true, fixedNow: fixedNow)

        case .settingsValid:
            selectedSection = .newJob

        case .settingsInvalid:
            selectedSection = .newJob
            enginePresentationOverride = EnginePresentationStatus(
                isReady: false,
                message: "Synthetic repository is missing its Python environment.",
                repositoryPath: syntheticRoot.path,
                pythonPath: syntheticRoot.appendingPathComponent(".venv/bin/python").path
            )

        case .settingsLocked:
            selectedSection = .processing
            seedJob(
                phase: .running,
                ownsActiveProcess: true,
                fixedNow: fixedNow
            )

        case .resourceFailure:
            loadFailure =
                "Synthetic fixture: bundled configuration manifest failed integrity checks."
            bridge = nil
            handshake = nil
            clearReadiness()

        case .longText:
            let longName = String(repeating: "SYNTHETIC-LONG-NAME-", count: 8) + ".wav"
            seedSelectedInput(
                URL(
                    fileURLWithPath: "/AURALIGN_SYNTHETIC/Media/\(longName)"
                ),
                output: syntheticOutput
            )
        }

        // ConfigStore seed methods intentionally bypass the user mutation
        // closure, so close that boundary again after every case.
        config.setMutationGuard { false }
    }

    private func syntheticHandshake() -> JSONValue {
        .object([
            "protocol": .string(Self.supportedBridgeProtocol),
            "package_version": .string(manifest.packageVersion),
            "manifest_schema_version": .string(manifest.schemaVersion),
            "job_acceptance_schema_version": .string(
                Self.supportedJobAcceptanceSchema
            ),
            "job_result_schema_version": .string(Self.supportedJobResultSchema),
            "commands": .array(
                Self.requiredBridgeCommands.sorted().map(JSONValue.string)
            ),
            "python": .string("3.11.13"),
        ])
    }

    private func seedSelectedInput(_ input: URL, output: URL) {
        inputURL = input
        inputProbeSnapshot = SourceFileSnapshot(
            canonicalURL: input.standardizedFileURL,
            identity: ArtifactFileIdentity(device: 0, inode: 0),
            sizeBytes: 0,
            modificationSeconds: 0,
            modificationNanoseconds: 0
        )
        inputProbe = MediaProbe.parse(
            from: .object([
                "duration_seconds": .double(3_842.5),
                "sample_rate": .int(48_000),
                "channels": .int(2),
                "codec": .string("pcm_s24le"),
                "format_name": .string("wav"),
            ])
        )
        inputProbeError = nil
        outputRootURL = output
    }

    private func seedDoctorEvidence(
        ok: Bool = true,
        issueStatus: String? = nil,
        issueMessage: String? = nil
    ) {
        let startupID = "startup:asr:synthetic"
        var checks: [JSONValue] = [
            .object([
                "name": .string(startupID),
                "status": .string("ok"),
                "message": .string("Synthetic local backend started."),
            ]),
            .object([
                "name": .string("startup:evidence-completeness"),
                "status": .string("ok"),
                "message": .string("Synthetic startup evidence is complete."),
            ]),
        ]
        if let issueStatus, let issueMessage {
            checks.append(
                .object([
                    "name": .string("fixture:readiness"),
                    "status": .string(issueStatus),
                    "message": .string(issueMessage),
                ])
            )
        }
        let payload = JSONValue.object([
            "ok": .bool(ok),
            "offline": .bool(
                config.effectiveValue("offline").boolValue ?? false
            ),
            "checks": .array(checks),
            "startup_evidence": .object([
                "schema_version": .string("doctor_startup_evidence.v1"),
                "requested": .bool(true),
                "complete": .bool(true),
                "expected_ids": .array([.string(startupID)]),
                "observed_ids": .array([.string(startupID)]),
                "expected_count": .int(1),
                "observed_count": .int(1),
            ]),
        ])
        doctor = DoctorSummary.parse(from: payload)
        doctorEvidenceFailure = doctor == nil
            ? ReadinessEvidenceFailure(
                source: .doctor,
                kind: .malformed,
                summary: "Synthetic runtime-check evidence is malformed.",
                technicalDetail: "The inert fixture could not construct strict doctor evidence."
            )
            : nil
    }

    private func seedCompleteModelEvidence(missingSelectedModels: Bool = false) {
        let selectedIDs = Set(
            manifest.requiredModelEntries(for: config.effectiveConfig).map {
                "\($0.role):\($0.key)"
            }
        )
        let entries = manifest.modelManifest.sorted {
            "\($0.role):\($0.key)" < "\($1.role):\($1.key)"
        }
        let identifiers = entries.map { "\($0.role):\($0.key)" }
        let rows: [JSONValue] = entries.map { entry in
            let identifier = "\(entry.role):\(entry.key)"
            let selected = selectedIDs.contains(identifier)
            let missing = missingSelectedModels && selected && entry.gated
            return .object([
                "key": .string(entry.key),
                "role": .string(entry.role),
                "model_id": .string(entry.modelId),
                "aliases": .array((entry.aliases ?? []).map(JSONValue.string)),
                "directory_name": .string(entry.directoryName),
                "required_files": .array(entry.requiredFiles.map(JSONValue.string)),
                "any_required_files": .array(
                    entry.anyRequiredFiles.map(JSONValue.string)
                ),
                "runtime_files": .array(entry.runtimeFiles.map(JSONValue.string)),
                "gated": .bool(entry.gated),
                "optional": .bool(entry.optional),
                "pullable": .bool(entry.pullable),
                "revision": .string(entry.revision ?? "synthetic-revision"),
                "license_expression": .string(
                    entry.licenseExpression ?? "LicenseRef-Synthetic-Fixture"
                ),
                "acquisition_status": .string(
                    entry.acquisitionStatus ?? "synthetic_fixture"
                ),
                "runtime_status": .string(
                    entry.runtimeStatus ?? "synthetic_fixture"
                ),
                "redistribution_status": .string(
                    entry.redistributionStatus ?? "synthetic_fixture"
                ),
                "review_status": .string(
                    entry.reviewStatus ?? "synthetic_fixture"
                ),
                "reviewed_at": .string(entry.reviewedAt ?? "2026-07-19"),
                "source_url": .string(
                    entry.sourceURL ?? "https://example.invalid/synthetic"
                ),
                "terms_url": .string(
                    entry.termsURL ?? "https://example.invalid/synthetic-terms"
                ),
                "attribution": .string(entry.attribution ?? "Synthetic fixture"),
                "status_id": .string(identifier),
                "present": .bool(!missing),
                "partial": .bool(false),
                "revision_verified": .bool(!missing),
                "selected_for_config": .bool(selected),
                "problem": missing
                    ? .string("Synthetic gated model is not cached.")
                    : .null,
            ])
        }
        let payload = JSONValue.object([
            "schema_version": .string("gui_model_status.v1"),
            "complete": .bool(true),
            "expected_ids": .array(identifiers.map(JSONValue.string)),
            "observed_ids": .array(identifiers.map(JSONValue.string)),
            "expected_count": .int(identifiers.count),
            "observed_count": .int(identifiers.count),
            "models": .array(rows),
        ])
        do {
            modelStatuses = try Self.strictModelStatuses(
                from: payload,
                expectedIDs: Set(identifiers),
                authoritativeManifestEntries: manifest.modelManifest
            )
            modelEvidenceFailure = nil
        } catch {
            modelStatuses = []
            modelEvidenceFailure = (error as? ReadinessEvidenceFailure)
                ?? ReadinessEvidenceFailure(
                    source: .models,
                    kind: .malformed,
                    summary: "Synthetic model evidence is malformed.",
                    technicalDetail:
                        "The inert fixture could not construct strict model evidence."
                )
        }
    }

    private func seedJob(
        phase: JobController.Phase,
        ownsActiveProcess: Bool,
        allStagesDone: Bool = false,
        fixedNow: Date
    ) {
        // An owned job necessarily has an immutable source and output-root
        // selection captured at launch. Keep the fixture's AppModel
        // projection consistent with the JobController projection so
        // contextual UI never claims that an active process has no source or
        // destination.
        seedSelectedInput(
            URL(
                fileURLWithPath:
                    "/AURALIGN_SYNTHETIC/Media/SYNTHETIC-INTERVIEW-01.wav"
            ),
            output: URL(
                fileURLWithPath: "/AURALIGN_SYNTHETIC/Output",
                isDirectory: true
            )
        )
        var stages = JobExpectations(config: config.effectiveConfig).expectedStages()
        for index in stages.indices {
            if case .skipped = stages[index].status {
                continue
            }
            if allStagesDone || index < 4 {
                stages[index].status = .done(seconds: Double(index + 1) * 1.25)
            } else if index == 4 {
                stages[index].status = .active
                stages[index].detail = "Synthetic event-derived stage"
            }
        }
        let output = URL(
            fileURLWithPath: "/AURALIGN_SYNTHETIC/Output/SYNTHETIC-JOB",
            isDirectory: true
        )
        job.applyPresentationFixture(
            phase: phase,
            ownsActiveProcess: ownsActiveProcess,
            stages: stages,
            warnings: ["Synthetic fixture warning for presentation review."],
            startedAt: fixedNow.addingTimeInterval(-152),
            recentEvents: [
                "job_accepted",
                "inspect_done",
                "canonical_audio_done",
                "asr_start",
            ],
            jobDirectory: output,
            runConfiguration: JobConfigurationSummary(config: config.effectiveConfig),
            now: fixedNow
        )
    }

    private func seedCompletedJob(rich: Bool, stale: Bool, fixedNow: Date) {
        let fingerprint = stale
            ? "synthetic-prior-configuration"
            : config.effectiveConfigFingerprint
        let result = syntheticResult(fingerprint: fingerprint, rich: rich)
        seedSelectedInput(
            result.sourceURL,
            output: result.outputDirectory.deletingLastPathComponent()
        )
        job.applyPresentationFixture(
            phase: .completed(result),
            stages: JobExpectations(config: config.effectiveConfig).expectedStages().map {
                var stage = $0
                if case .skipped = stage.status {
                    return stage
                }
                stage.status = .done(seconds: 1.0)
                return stage
            },
            startedAt: fixedNow.addingTimeInterval(-245),
            finishedAt: fixedNow,
            recentEvents: ["export_done", "result", "process_exited"],
            jobDirectory: result.outputDirectory,
            runConfiguration: JobConfigurationSummary(config: config.effectiveConfig),
            lastRunFingerprint: fingerprint,
            now: fixedNow
        )
    }

    private func syntheticResult(fingerprint: String, rich: Bool) -> JobResult {
        let previewText = rich
            ? """
            SYNTHETIC TRANSCRIPT — PRESENTATION FIXTURE

            Speaker 1: This text exists solely for native interface QA.
            Speaker 2: It is not a transcription of user media.
            Speaker 1: Results remain local and visibly labeled as synthetic.
            """
            : "SYNTHETIC TRANSCRIPT — no user media was processed."
        return JobResult(
            outputDirectory: URL(
                fileURLWithPath: "/AURALIGN_SYNTHETIC/Output/SYNTHETIC-JOB",
                isDirectory: true
            ),
            sourceURL: URL(
                fileURLWithPath: "/AURALIGN_SYNTHETIC/Media/SYNTHETIC-INTERVIEW-01.wav"
            ),
            sourceSnapshot: SourceFileSnapshot(
                canonicalURL: URL(
                    fileURLWithPath:
                        "/AURALIGN_SYNTHETIC/Media/SYNTHETIC-INTERVIEW-01.wav"
                ),
                identity: ArtifactFileIdentity(device: 0, inode: 0),
                sizeBytes: 0,
                modificationSeconds: 0,
                modificationNanoseconds: 0
            ),
            language: "en",
            speakerTurns: rich ? 18 : 4,
            words: rich ? 1_284 : 214,
            segments: rich ? 92 : 17,
            artifacts: syntheticArtifacts(),
            configFingerprint: fingerprint,
            warnings: rich
                ? [
                    "Synthetic fixture notice for layout verification; "
                        + "not runtime evidence."
                ]
                : [],
            qa: ResultQAProjection(
                status: .available,
                overallQualitySummary: rich
                    ? "review_warnings"
                    : "no_deterministic_warnings",
                flagCount: rich ? 1 : 0,
                errorFlagCount: 0,
                warningFlagCount: 0,
                infoFlagCount: rich ? 1 : 0,
                changedSpanCount: 0,
                flags: rich
                    ? [
                        ResultQAFlag(
                            ordinal: 0,
                            code: "synthetic_fixture_notice",
                            severity: "info",
                            message: "Synthetic fixture evidence; not a runtime QA finding."
                        ),
                    ]
                    : [],
                omittedFlagCount: 0
            ),
            transcriptPreview: ResultTranscriptPreview(
                status: .available,
                text: previewText,
                displayedBytes: previewText.utf8.count,
                sourceBytes: Int64(previewText.utf8.count),
                truncated: false
            )
        )
    }

    private func syntheticArtifacts() -> [JobArtifact] {
        let root = URL(
            fileURLWithPath: "/AURALIGN_SYNTHETIC/Output/SYNTHETIC-JOB",
            isDirectory: true
        )
        return [
            JobArtifact(
                name: "transcript.txt",
                sizeBytes: 12_480,
                url: root.appendingPathComponent("transcript.txt"),
                identity: ArtifactFileIdentity(device: 1, inode: 101)
            ),
            JobArtifact(
                name: "transcript.srt",
                sizeBytes: 18_144,
                url: root.appendingPathComponent("transcript.srt"),
                identity: ArtifactFileIdentity(device: 1, inode: 102)
            ),
            JobArtifact(
                name: "qa_report.json",
                sizeBytes: 4_096,
                url: root.appendingPathComponent("qa_report.json"),
                identity: ArtifactFileIdentity(device: 1, inode: 103)
            ),
        ]
    }
}

#endif

/// Composition root: engine location, bridge, configuration store, readiness,
/// and the active job. Owns the startup sequence.
@MainActor
final class AppModel: ObservableObject {
    struct EnginePresentationStatus: Equatable {
        var isReady: Bool
        var message: String
        var repositoryPath: String
        var pythonPath: String
    }

    struct EngineConnectionPresentation: Equatable {
        enum State: Equatable {
            case notConnected
            case connecting
            case connected
            case failed
        }

        var state: State
        var message: String
    }

    static let supportedBridgeProtocol = "auralign_gui_bridge.v1"
    static let supportedJobAcceptanceSchema = JobAcceptanceEnvelope.schemaVersion
    static let supportedJobResultSchema = PendingJobResultEnvelope.schemaVersion
    static let requiredBridgeCommands: Set<String> = [
        "handshake",
        "schema",
        "presets",
        "validate",
        "doctor",
        "models",
        "probe",
        "transcribe",
    ]

    let manifest: ConfigManifest
    let catalog: FieldCatalog
    let config: ConfigStore
    let job = JobController()

    @Published private(set) var engineLocation: EngineLocation
    @Published private(set) var bridge: EngineBridge?
    @Published private(set) var handshake: JSONValue?
    @Published private(set) var startupError: String?
    @Published private(set) var isConnectingEngine = false

    // Readiness (doctor) state.
    @Published private(set) var doctor: DoctorSummary?
    @Published private(set) var doctorEvidenceFailure: ReadinessEvidenceFailure?
    @Published private(set) var isRunningDoctor = false
    @Published private(set) var modelStatuses: [ModelCacheStatus] = []
    @Published private(set) var modelEvidenceFailure: ReadinessEvidenceFailure?
    /// Fingerprint of the configuration the last doctor run checked.
    @Published private(set) var doctorFingerprint: String?

    var doctorError: String? { doctorEvidenceFailure?.summary }
    var modelStatusError: String? { modelEvidenceFailure?.summary }

    private var cancellables: Set<AnyCancellable> = []
    private var connectionGeneration = 0
    private var readinessGeneration = 0
    private var inputProbeGeneration = 0
    private var handshakeTask: Task<Void, Never>?
    private var inputProbeTask: Task<Void, Never>?
    private var readinessTask: Task<Void, Never>?
    private let userDefaults: UserDefaults
    @Published private(set) var hasApplicationOwnership = false
    @Published private(set) var isTerminating = false

    // Job inputs.
    @Published private(set) var inputURL: URL? {
        didSet { probeInput() }
    }
    @Published private(set) var inputProbe: MediaProbe?
    @Published private(set) var inputProbeSnapshot: SourceFileSnapshot?
    @Published private(set) var inputProbeError: String?
    @Published private(set) var outputRootURL: URL?
    @Published private(set) var writeDebugReport = false
    @Published private(set) var jobDraftDate = Date()

    @Published var selectedSection: WorkspaceSection? = .newJob
    @Published private(set) var fieldNavigationRequest: FieldNavigationRequest?
    @Published private(set) var consumedFieldNavigationRequestID: Int?
    @Published private(set)
    var keyboardFocusedConfigurationControlIdentifier: String?
    @Published private(set) var sectionNavigationRequest: SectionNavigationRequest?
    @Published private(set) var consumedSectionNavigationRequestID: Int?

    private(set) var loadFailure: String?
    private var fieldNavigationSequence = 0
    private(set) var isPresentationFixture = false
    private var enginePresentationOverride: EnginePresentationStatus?
    private var outputRootUsableOverride: Bool?
    private var presentationDate: Date?

    init(userDefaults: UserDefaults = .standard) {
        self.userDefaults = userDefaults
        var loadFailureMessage: String?
        var loadedManifest = ConfigManifest(
            schemaVersion: "unavailable",
            packageVersion: "unavailable",
            fields: [],
            modelManifest: []
        )
        var loadedCatalog = FieldCatalog(catalogVersion: "unavailable", sections: [], fields: [])
        do {
            loadedManifest = try ConfigManifest.loadBundled()
            loadedCatalog = try FieldCatalog.loadBundled()
        } catch {
            loadFailureMessage = "Bundled configuration resources failed to load: "
                + error.localizedDescription
        }
        manifest = loadedManifest
        catalog = loadedCatalog
        config = ConfigStore(manifest: loadedManifest, catalog: loadedCatalog)
        loadFailure = loadFailureMessage
        engineLocation = EngineLocation.resolve(userDefaults: userDefaults)
#if DEBUG
        config.setManagedValues([:])
#else
        config.setManagedValues(Self.releaseManagedValues(for: engineLocation))
#endif
        config.setMutationGuard { [weak self] in
            guard let self else { return false }
            return self.hasApplicationOwnership
                && !self.isTerminating
                && !(self.job.ownsActiveProcess || self.job.isRunning)
        }
        // Views observe AppModel; forward nested-store changes so job progress
        // and validation results always re-render.
        config.objectWillChange
            .sink { [weak self] _ in
                guard let self else { return }
                // A configuration mutation (including a validation-state
                // transition) invalidates any in-flight doctor/model request.
                // The fingerprint retains completed evidence only for the
                // explicit stale-state comparison.
                self.readinessGeneration &+= 1
                self.readinessTask?.cancel()
                self.isRunningDoctor = false
                self.modelStatuses = []
                self.doctorEvidenceFailure = nil
                self.modelEvidenceFailure = nil
                self.objectWillChange.send()
            }
            .store(in: &cancellables)
        job.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        job.$phase
            .removeDuplicates(by: { first, second in
                switch (first, second) {
                case (.running, .running),
                     (.resultPendingExit, .resultPendingExit),
                     (.idle, .idle),
                     (.stopping, .stopping):
                    return true
                default:
                    return false
                }
            })
            .sink { [weak self] phase in
                self?.announceJobPhase(phase)
                switch phase {
                case .completed, .failed, .cancelled:
                    self?.requestReadinessRefresh()
                case .idle, .running, .resultPendingExit, .stopping:
                    break
                }
            }
            .store(in: &cancellables)
        job.$stages
            .map { stages -> ActiveStageAnnouncement? in
                guard let index = stages.firstIndex(where: {
                    if case .active = $0.status { return true }
                    return false
                }) else { return nil }
                return ActiveStageAnnouncement(
                    stageID: stages[index].id,
                    ordinal: index + 1,
                    total: stages.count,
                    title: stages[index].title
                )
            }
            .removeDuplicates()
            .compactMap { $0 }
            .sink { announcement in
                AccessibilityAnnouncementCenter.shared.announce(
                    "Stage \(announcement.ordinal) of \(announcement.total): "
                        + "\(announcement.title).",
                    token: "job-stage-\(announcement.stageID)-"
                        + "\(announcement.ordinal)-\(announcement.total)"
                )
            }
            .store(in: &cancellables)
        config.$fieldErrors
            .map { errors in
                ValidationAnnouncement(
                    count: errors.count,
                    revision: errors.map {
                        "\($0.dottedLocation)|\($0.message)"
                    }.joined(separator: "\n").hashValue
                )
            }
            .removeDuplicates()
            .dropFirst()
            .sink { state in
                let message: String
                if state.count == 0 {
                    message = "Configuration validation passed."
                } else if state.count == 1 {
                    message = "One configuration issue requires attention."
                } else {
                    message = "\(state.count) configuration issues require attention."
                }
                let token = "configuration-validation-"
                    + String(state.count)
                    + "-"
                    + String(state.revision)
                AccessibilityAnnouncementCenter.shared.announce(
                    message,
                    token: token,
                    priority: state.count == 0 ? .medium : .high
                )
            }
            .store(in: &cancellables)
    }

    // MARK: - Engine lifecycle

    /// The model is deliberately inert until the process-wide application lock
    /// has been acquired. This prevents a losing duplicate launch from ever
    /// starting a Python bridge before it terminates.
    @discardableResult
    func activateForApplicationOwnership() -> Bool {
        guard !isTerminating else { return false }
        if hasApplicationOwnership {
            return true
        }
        hasApplicationOwnership = true
        if isPresentationFixture {
            return true
        }
        return connectEngine()
    }

    /// Cancels every app-owned asynchronous bridge operation before allowing
    /// AppKit to terminate. One-shot bridge cancellation resumes only after
    /// the exact child has exited and its output pipes have been drained.
    func stopForApplicationTermination() async {
        guard hasApplicationOwnership else { return }
        isTerminating = true
        connectionGeneration &+= 1
        readinessGeneration &+= 1
        inputProbeGeneration &+= 1

        // Signal the long-running job first, then cancel all one-shot work so
        // both process families unwind concurrently.
        job.cancel()
        let handshakeWork = handshakeTask
        let probeWork = inputProbeTask
        let readinessWork = readinessTask
        handshakeWork?.cancel()
        probeWork?.cancel()
        readinessWork?.cancel()

        await config.cancelOutstandingWorkAndWait()
        if let handshakeWork { await handshakeWork.value }
        if let probeWork { await probeWork.value }
        if let readinessWork { await readinessWork.value }
        await job.stopForApplicationTermination()

        handshakeTask = nil
        inputProbeTask = nil
        readinessTask = nil
        isConnectingEngine = false
        isRunningDoctor = false
        config.detach()
        bridge = nil
        handshake = nil
    }

    /// True while the app still owns a transcription subprocess. Every
    /// operation that could replace engine/config/job state uses this single
    /// mutation boundary, including the interval after a result event and
    /// before the child has exited and been reaped.
    var workMutationLocked: Bool {
        !hasApplicationOwnership
            || isTerminating
            || job.ownsActiveProcess
            || job.isRunning
    }

    @discardableResult
    func connectEngine() -> Bool {
        guard hasApplicationOwnership,
              !isTerminating,
              !isPresentationFixture,
              !(job.ownsActiveProcess || job.isRunning)
        else {
            return false
        }
        connectionGeneration &+= 1
        let generation = connectionGeneration
        let previousHandshake = handshakeTask
        let previousReadiness = readinessTask
        let previousProbe = inputProbeTask
        previousHandshake?.cancel()
        readinessGeneration &+= 1
        previousReadiness?.cancel()
        inputProbeGeneration &+= 1
        previousProbe?.cancel()

        let location = EngineLocation.resolve(userDefaults: userDefaults)
        engineLocation = location
#if DEBUG
        config.setManagedValues([:])
#else
        config.setManagedValues(Self.releaseManagedValues(for: location))
#endif
        bridge = nil
        handshake = nil
        startupError = nil
        isConnectingEngine = false
        clearReadiness()
        inputProbe = nil
        inputProbeSnapshot = nil
        inputProbeError = nil
        let previousValidation = config.detach()

        guard loadFailure == nil else {
            startupError = loadFailure
            return true
        }
        guard location.isReady else {
            startupError = location.diagnosis.message
            return true
        }
        let candidate = EngineBridge(location: location)
        isConnectingEngine = true
        handshakeTask = Task { [weak self] in
            if let previousHandshake {
                await previousHandshake.value
            }
            if let previousReadiness {
                await previousReadiness.value
            }
            if let previousProbe {
                await previousProbe.value
            }
            if let previousValidation {
                await previousValidation.value
            }
            guard !Task.isCancelled else { return }
            await self?.performStartupHandshake(
                candidate: candidate,
                generation: generation
            )
        }
        return true
    }

#if DEBUG
    @discardableResult
    func updateEngineRoot(_ url: URL?) -> Bool {
        // Check ownership before touching either persistent preferences or
        // connection generations. A blocked attempt must be a complete no-op.
        guard !isPresentationFixture, !workMutationLocked else { return false }
        if let url {
            userDefaults.set(url.path, forKey: EngineLocation.engineRootDefaultsKey)
        } else {
            userDefaults.removeObject(forKey: EngineLocation.engineRootDefaultsKey)
        }
        _ = connectEngine()
        return true
    }
#endif

    @discardableResult
    func updateModelCache(_ url: URL?) -> Bool {
        guard !isPresentationFixture, !workMutationLocked else { return false }
        if let url,
           url.standardizedFileURL != EngineLocation.defaultModelCacheRoot.standardizedFileURL {
            userDefaults.set(
                url.standardizedFileURL.path,
                forKey: EngineLocation.modelCacheDefaultsKey
            )
        } else {
            userDefaults.removeObject(forKey: EngineLocation.modelCacheDefaultsKey)
        }
        _ = connectEngine()
        return true
    }

    private static func releaseManagedValues(
        for location: EngineLocation
    ) -> [String: JSONValue] {
        [
            "offline": .bool(true),
            "model_cache": .string(location.modelCacheRoot.path),
        ]
    }

    private func performStartupHandshake(
        candidate: EngineBridge,
        generation: Int
    ) async {
        do {
            let payload = try await candidate.runExpecting(
                event: "handshake",
                command: ["handshake"],
                timeout: 60
            )
            guard hasApplicationOwnership,
                  !isTerminating,
                  connectionGeneration == generation,
                  !Task.isCancelled
            else {
                return
            }
            if let issue = Self.handshakeCompatibilityIssue(payload, manifest: manifest) {
                throw EngineBridge.BridgeError(message: issue)
            }

            // The candidate remains private until its protocol is compatible
            // and both engine-owned configuration resources load atomically.
            config.attach(bridge: candidate)
            try await config.refreshFromEngine()
            guard hasApplicationOwnership,
                  !isTerminating,
                  connectionGeneration == generation,
                  !Task.isCancelled
            else {
                return
            }
            _ = await config.validateNow()
            guard hasApplicationOwnership,
                  !isTerminating,
                  connectionGeneration == generation,
                  !Task.isCancelled
            else {
                return
            }

            bridge = candidate
            handshake = payload
            startupError = nil
            isConnectingEngine = false
            probeInput()
            if config.isValid {
                await refreshReadiness()
            }
        } catch {
            guard hasApplicationOwnership,
                  !isTerminating,
                  connectionGeneration == generation,
                  !Task.isCancelled
            else {
                return
            }
            bridge = nil
            handshake = nil
            isConnectingEngine = false
            clearReadiness()
            let message = "Engine startup failed: \(error.localizedDescription)"
            startupError = message
            config.detach(reason: message)
        }
    }

    var engineVersionText: String {
        guard let handshake else { return "Not connected" }
        let version = handshake.value(at: "package_version")?.stringValue ?? "?"
        let python = handshake.value(at: "python")?.stringValue ?? "?"
        return "local-transcript-max \(version) · Python \(python)"
    }

    var enginePresentationStatus: EnginePresentationStatus {
        if let enginePresentationOverride {
            return enginePresentationOverride
        }
        return EnginePresentationStatus(
            isReady: engineLocation.isReady,
            message: engineLocation.diagnosis.message,
            repositoryPath: engineLocation.repositoryDisplayPath,
            pythonPath: engineLocation.pythonDisplayPath
        )
    }

    /// Repository/Python availability and bridge compatibility are separate
    /// facts. Settings must never present a usable checkout as proof that a
    /// handshake, schema sync, or validation connection succeeded.
    var engineConnectionPresentation: EngineConnectionPresentation {
        let environment = enginePresentationStatus
        guard environment.isReady else {
            return EngineConnectionPresentation(
                state: .failed,
                message: "Not connected. \(privacySafePresentationMessage(environment.message))"
            )
        }
        if isConnectingEngine {
            return EngineConnectionPresentation(
                state: .connecting,
                message: "Connecting and checking the local bridge contract…"
            )
        }
        if bridge != nil,
           handshake != nil,
           config.isEngineSynchronized,
           startupError == nil {
            return EngineConnectionPresentation(
                state: .connected,
                message: engineVersionText
            )
        }
        if let failure = startupError ?? config.engineSyncError {
            return EngineConnectionPresentation(
                state: .failed,
                message: privacySafePresentationMessage(failure)
            )
        }
        return EngineConnectionPresentation(
            state: .notConnected,
            message: "Not connected."
        )
    }

    static func handshakeCompatibilityIssue(
        _ payload: JSONValue,
        manifest: ConfigManifest
    ) -> String? {
        guard payload.objectValue != nil else {
            return "Engine handshake payload is malformed."
        }
        guard payload.value(at: "protocol")?.stringValue == supportedBridgeProtocol else {
            return "Unsupported engine bridge protocol. This app requires "
                + "\(supportedBridgeProtocol)."
        }
        guard let packageVersion = payload.value(at: "package_version")?.stringValue,
              packageVersion == manifest.packageVersion
        else {
            return "Engine package version does not match this app's bundled configuration "
                + "manifest (\(manifest.packageVersion))."
        }
        guard payload.value(at: "manifest_schema_version")?.stringValue
            == manifest.schemaVersion
        else {
            return "Engine manifest schema is incompatible with this app "
                + "(\(manifest.schemaVersion) required)."
        }
        guard payload.value(at: "job_acceptance_schema_version")?.stringValue
            == supportedJobAcceptanceSchema
        else {
            return "Engine job-acceptance schema is incompatible with this app "
                + "(\(supportedJobAcceptanceSchema) required)."
        }
        guard payload.value(at: "job_result_schema_version")?.stringValue
            == supportedJobResultSchema
        else {
            return "Engine result schema is incompatible with this app "
                + "(\(supportedJobResultSchema) required)."
        }
        guard let commandValues = payload.value(at: "commands")?.arrayValue,
              commandValues.count == commandValues.compactMap(\.stringValue).count
        else {
            return "Engine handshake did not provide a valid command list."
        }
        let commands = Set(commandValues.compactMap(\.stringValue))
        let missingCommands = requiredBridgeCommands.subtracting(commands).sorted()
        guard missingCommands.isEmpty else {
            return "Engine bridge is missing required commands: "
                + missingCommands.joined(separator: ", ") + "."
        }
        guard let python = payload.value(at: "python")?.stringValue,
              Self.isSupportedPythonVersion(python)
        else {
            return "Engine Python version is unsupported; Auralign requires Python 3.11."
        }
        return nil
    }

    private static func isSupportedPythonVersion(_ version: String) -> Bool {
        let components = version.split(separator: ".")
        guard components.count >= 2,
              components[0] == "3",
              let minor = Int(components[1])
        else {
            return false
        }
        return minor == 11
    }

    // MARK: - Readiness

    /// Starts a single model-owned readiness task. Repeated requests first
    /// cancel and await their predecessor, so application termination always
    /// has one complete chain to cancel and reap.
    func requestReadinessRefresh() {
        guard hasApplicationOwnership,
              !isTerminating,
              !isPresentationFixture,
              !workMutationLocked
        else {
            return
        }
        let previous = readinessTask
        previous?.cancel()
        readinessTask = Task { [weak self] in
            if let previous {
                await previous.value
            }
            guard !Task.isCancelled,
                  let self,
                  self.hasApplicationOwnership,
                  !self.isTerminating
            else {
                return
            }
            await self.refreshReadiness()
        }
    }

    func refreshReadiness() async {
        guard hasApplicationOwnership,
              !isTerminating,
              !isPresentationFixture,
              !workMutationLocked
        else {
            return
        }
        guard let bridge, handshake != nil else {
            clearReadiness()
            doctorEvidenceFailure = ReadinessEvidenceFailure(
                source: .doctor,
                kind: .unavailable,
                summary: "The compatible local runtime is unavailable.",
                technicalDetail: startupError
            )
            return
        }
        guard config.isValid else {
            clearReadiness()
            return
        }

        readinessGeneration &+= 1
        let generation = readinessGeneration
        let engineGeneration = connectionGeneration
        let checkedFingerprint = config.effectiveConfigFingerprint
        defer { finishReadiness(generation: generation) }
        isRunningDoctor = true
        doctor = nil
        doctorEvidenceFailure = nil
        doctorFingerprint = nil
        modelStatuses = []
        modelEvidenceFailure = nil

        do {
            let payload = try await bridge.runExpecting(
                event: "doctor",
                command: ["doctor"],
                input: .object([
                    "config": config.effectiveConfig,
                    "startup": .bool(true),
                ]),
                timeout: 300
            )
            guard readinessRequestIsCurrent(
                generation: generation,
                engineGeneration: engineGeneration,
                bridge: bridge
            ) else {
                return
            }
            guard config.effectiveConfigFingerprint == checkedFingerprint else {
                doctorFingerprint = checkedFingerprint
                return
            }
            guard let summary = DoctorSummary.parse(from: payload) else {
                throw ReadinessEvidenceFailure(
                    source: .doctor,
                    kind: .malformed,
                    summary: "Runtime-check evidence is malformed.",
                    technicalDetail:
                        "The engine response did not match the versioned doctor schema."
                )
            }
            if let failure = Self.startupDoctorIssue(summary) {
                throw failure
            }
            doctor = summary
            doctorFingerprint = checkedFingerprint
        } catch {
            guard readinessRequestIsCurrent(
                generation: generation,
                engineGeneration: engineGeneration,
                bridge: bridge
            ) else {
                return
            }
            doctor = nil
            doctorFingerprint = checkedFingerprint
            doctorEvidenceFailure = (error as? ReadinessEvidenceFailure)
                ?? ReadinessEvidenceFailure(
                    source: .doctor,
                    kind: .unavailable,
                    summary: "Runtime checks could not be completed.",
                    technicalDetail: error.localizedDescription
                )
            return
        }

        do {
            let modelsPayload = try await bridge.runExpecting(
                event: "models",
                command: ["models"],
                input: .object([
                    "model_cache": config.effectiveValue("model_cache"),
                    "config": config.effectiveConfig,
                ]),
                timeout: 60
            )
            guard readinessRequestIsCurrent(
                generation: generation,
                engineGeneration: engineGeneration,
                bridge: bridge
            ), config.effectiveConfigFingerprint == checkedFingerprint else {
                return
            }
            modelStatuses = try Self.strictModelStatuses(
                from: modelsPayload,
                expectedIDs: Set(
                    manifest.modelManifest.map { "\($0.role):\($0.key)" }
                ),
                authoritativeManifestEntries: manifest.modelManifest
            )
        } catch {
            guard readinessRequestIsCurrent(
                generation: generation,
                engineGeneration: engineGeneration,
                bridge: bridge
            ) else {
                return
            }
            modelStatuses = []
            modelEvidenceFailure = (error as? ReadinessEvidenceFailure)
                ?? ReadinessEvidenceFailure(
                    source: .models,
                    kind: .unavailable,
                    summary: "Model evidence could not be collected.",
                    technicalDetail: error.localizedDescription
                )
        }
    }

    static func startupDoctorIssue(
        _ summary: DoctorSummary
    ) -> ReadinessEvidenceFailure? {
        func failure(
            _ kind: ReadinessEvidenceFailure.Kind,
            _ summary: String,
            _ detail: String
        ) -> ReadinessEvidenceFailure {
            return ReadinessEvidenceFailure(
                source: .doctor,
                kind: kind,
                summary: summary,
                technicalDetail: detail
            )
        }
        guard Set(summary.checks.map(\.name)).count == summary.checks.count else {
            return failure(
                .inconsistent,
                "Runtime-check evidence is internally inconsistent.",
                "The doctor returned duplicated readiness-check identifiers."
            )
        }
        guard summary.ok == summary.errors.isEmpty else {
            return failure(
                .inconsistent,
                "Runtime-check evidence is internally inconsistent.",
                "The doctor summary contradicted its blocking check rows."
            )
        }
        guard let evidence = summary.startupEvidence, evidence.requested else {
            return failure(
                .partial,
                "Runtime-check evidence is incomplete.",
                "Backend startup-probe evidence was not included."
            )
        }
        guard evidence.complete else {
            return failure(
                .partial,
                "Runtime-check evidence is incomplete.",
                "Backend startup-probe coverage was explicitly incomplete."
            )
        }
        let startupChecks = summary.checks.filter {
            $0.name.hasPrefix("startup:")
                && $0.name != "startup:evidence-completeness"
        }
        guard startupChecks.count == evidence.observedIDs.count,
              Set(startupChecks.map(\.name)) == Set(evidence.observedIDs)
        else {
            return failure(
                .inconsistent,
                "Runtime-check evidence is internally inconsistent.",
                "Startup-probe rows disagreed with the completeness declaration."
            )
        }
        guard startupChecks.allSatisfy({ $0.status == "ok" || $0.status == "error" }) else {
            return failure(
                .malformed,
                "Runtime-check evidence is malformed.",
                "A startup-probe row used an unsupported status."
            )
        }
        if summary.ok, startupChecks.contains(where: { $0.status != "ok" }) {
            return failure(
                .inconsistent,
                "Runtime-check evidence is internally inconsistent.",
                "The doctor summary contradicted backend startup-probe results."
            )
        }
        return nil
    }

    /// Cross-check the accepted Python evidence against the exact editor state
    /// and bundled compliance authority. Display rows alone never establish
    /// readiness: every selected model must be present, complete, revision
    /// verified, problem-free, and approved for local runtime.
    var readinessEvidenceIssue: String? {
        guard let doctor else { return nil }
        guard let expectedOffline = config.effectiveValue("offline").boolValue else {
            return "The effective configuration has no valid offline-mode value."
        }
        guard doctor.offline == expectedOffline else {
            return "Doctor evidence was produced for a different offline-mode setting."
        }
        let selectedStatuses = modelStatuses.filter(\.selectedForConfig)
        guard !selectedStatuses.isEmpty else {
            return "Model status evidence identifies no models selected for this configuration."
        }
        for status in selectedStatuses {
            guard let entry = manifest.modelManifest.first(
                where: { $0.id == status.id }
            ), entry.isRuntimeSelectable else {
                return "A selected model is not approved by the bundled repository manifest."
            }
            guard status.present, !status.partial else {
                return "A selected model is absent or incomplete in the local cache."
            }
            guard status.revisionVerified else {
                return "A selected model has not passed exact revision verification."
            }
            guard status.problem == nil else {
                return "A selected model reports an unresolved local-cache problem."
            }
        }
        return nil
    }

    /// Fail closed on partial, default-filled, duplicated, or internally
    /// inconsistent model evidence. The public display projection intentionally
    /// remains small, but acceptance verifies the complete bridge contract.
    static func strictModelStatuses(
        from payload: JSONValue,
        expectedIDs authoritativeIDs: Set<String>? = nil,
        authoritativeManifestEntries: [ModelManifestEntry]? = nil
    ) throws -> [ModelCacheStatus] {
        func failure(
            _ kind: ReadinessEvidenceFailure.Kind,
            _ detail: String
        ) -> ReadinessEvidenceFailure {
            let summary: String
            switch kind {
            case .unavailable: summary = "Model evidence is unavailable."
            case .partial: summary = "Model evidence is incomplete."
            case .malformed: summary = "Model evidence is malformed."
            case .inconsistent: summary = "Model evidence is internally inconsistent."
            }
            return ReadinessEvidenceFailure(
                source: .models,
                kind: kind,
                summary: summary,
                technicalDetail: detail
            )
        }
        func malformed(_ detail: String = "A required field was absent or invalid.")
            -> ReadinessEvidenceFailure {
            failure(.malformed, detail)
        }
        func requiredString(
            _ object: [String: JSONValue],
            _ key: String
        ) throws -> String {
            guard let value = object[key]?.stringValue, !value.isEmpty else {
                throw malformed()
            }
            return value
        }
        func stringArray(
            _ object: [String: JSONValue],
            _ key: String
        ) throws -> [String] {
            guard let values = object[key]?.arrayValue,
                  values.count == values.compactMap(\.stringValue).count
            else {
                throw malformed()
            }
            return values.compactMap(\.stringValue)
        }
        func requiredBool(
            _ object: [String: JSONValue],
            _ key: String
        ) throws -> Bool {
            guard let value = object[key]?.boolValue else { throw malformed() }
            return value
        }

        guard let object = payload.objectValue,
              object["schema_version"]?.stringValue == "gui_model_status.v1"
        else {
            throw malformed("The payload did not match gui_model_status.v1.")
        }
        guard let complete = object["complete"]?.boolValue else {
            throw malformed("The completeness declaration was absent or invalid.")
        }
        guard complete else {
            throw failure(
                .partial,
                "The engine explicitly reported incomplete model coverage."
            )
        }
        guard
              let expectedCount = object["expected_count"]?.strictIntValue,
              let observedCount = object["observed_count"]?.strictIntValue,
              expectedCount >= 0,
              observedCount >= 0,
              let modelValues = object["models"]?.arrayValue
        else {
            throw malformed("Model evidence counts or rows were absent or invalid.")
        }
        let expectedIDs = try stringArray(object, "expected_ids")
        let observedIDs = try stringArray(object, "observed_ids")
        if observedCount < expectedCount
            || observedIDs.count < expectedIDs.count
            || modelValues.count < expectedCount {
            throw failure(
                .partial,
                "Model evidence contained fewer rows than the declared contract."
            )
        }
        let authoritativeEntriesByID: [String: ModelManifestEntry]
        if let authoritativeManifestEntries {
            var entries: [String: ModelManifestEntry] = [:]
            for entry in authoritativeManifestEntries {
                let id = "\(entry.role):\(entry.key)"
                guard entries[id] == nil else {
                    throw failure(
                        .inconsistent,
                        "The bundled model manifest contained a duplicate identifier."
                    )
                }
                entries[id] = entry
            }
            authoritativeEntriesByID = entries
        } else {
            authoritativeEntriesByID = [:]
        }
        guard expectedIDs.count == expectedCount,
              observedIDs.count == observedCount,
              modelValues.count == observedCount,
              Set(expectedIDs).count == expectedIDs.count,
              Set(observedIDs).count == observedIDs.count,
              observedIDs == expectedIDs,
              Set(expectedIDs) == Set(observedIDs),
              authoritativeIDs.map({ $0 == Set(expectedIDs) }) ?? true,
              authoritativeManifestEntries == nil
                || Set(authoritativeEntriesByID.keys) == Set(expectedIDs)
        else {
            throw failure(
                .inconsistent,
                "Model identifiers, counts, ordering, or manifest coverage disagreed."
            )
        }

        var statusIDs: [String] = []
        var statuses: [ModelCacheStatus] = []
        for value in modelValues {
            guard let model = value.objectValue else { throw malformed() }
            let role = try requiredString(model, "role")
            let key = try requiredString(model, "key")
            let modelID = try requiredString(model, "model_id")
            let aliases = try stringArray(model, "aliases")
            let directoryName = try requiredString(model, "directory_name")
            let requiredFiles = try stringArray(model, "required_files")
            let anyRequiredFiles = try stringArray(model, "any_required_files")
            let runtimeFiles = try stringArray(model, "runtime_files")
            let revision = try requiredString(model, "revision")
            let licenseExpression = try requiredString(model, "license_expression")
            let acquisitionStatus = try requiredString(model, "acquisition_status")
            let runtimeStatus = try requiredString(model, "runtime_status")
            let redistributionStatus = try requiredString(
                model,
                "redistribution_status"
            )
            let reviewStatus = try requiredString(model, "review_status")
            let reviewedAt = try requiredString(model, "reviewed_at")
            let sourceURL = try requiredString(model, "source_url")
            let termsURL = try requiredString(model, "terms_url")
            let attribution = try requiredString(model, "attribution")
            let statusID = try requiredString(model, "status_id")
            let gated = try requiredBool(model, "gated")
            let optional = try requiredBool(model, "optional")
            let pullable = try requiredBool(model, "pullable")
            guard statusID == "\(role):\(key)",
                  model["problem"] == .null || model["problem"]?.stringValue != nil
            else {
                throw failure(
                    .inconsistent,
                    "A model row's identifier or problem field contradicted its schema."
                )
            }
            guard ModelManifestEntry.isValidFileContract(
                      directoryName: directoryName,
                      requiredFiles: requiredFiles,
                      anyRequiredFiles: anyRequiredFiles,
                      runtimeFiles: runtimeFiles
                  )
            else {
                throw failure(
                    .inconsistent,
                    "A model row contained an unsafe or contradictory file contract."
                )
            }
            if let entry = authoritativeEntriesByID[statusID] {
                guard entry.modelId == modelID,
                      (entry.aliases ?? []) == aliases,
                      entry.directoryName == directoryName,
                      entry.requiredFiles == requiredFiles,
                      entry.anyRequiredFiles == anyRequiredFiles,
                      entry.runtimeFiles == runtimeFiles,
                      entry.revision == revision,
                      entry.licenseExpression == licenseExpression,
                      entry.acquisitionStatus == acquisitionStatus,
                      entry.runtimeStatus == runtimeStatus,
                      entry.redistributionStatus == redistributionStatus,
                      entry.reviewStatus == reviewStatus,
                      entry.reviewedAt == reviewedAt,
                      entry.sourceURL == sourceURL,
                      entry.termsURL == termsURL,
                      entry.attribution == attribution,
                      entry.gated == gated,
                      entry.optional == optional,
                      entry.pullable == pullable
                else {
                    throw failure(
                        .inconsistent,
                        "Model evidence metadata drifted from the bundled authority."
                    )
                }
            }
            let present = try requiredBool(model, "present")
            let partial = try requiredBool(model, "partial")
            let revisionVerified = try requiredBool(model, "revision_verified")
            let selectedForConfig = try requiredBool(model, "selected_for_config")
            let problem = model["problem"]?.stringValue
            guard !(present && partial),
                  !(present && problem != nil),
                  !(partial && revisionVerified)
            else {
                throw failure(
                    .inconsistent,
                    "A model row contained contradictory availability facts."
                )
            }
            statusIDs.append(statusID)
            statuses.append(
                ModelCacheStatus(
                    role: role,
                    key: key,
                    modelId: modelID,
                    gated: gated,
                    optional: optional,
                    present: present,
                    partial: partial,
                    revisionVerified: revisionVerified,
                    selectedForConfig: selectedForConfig,
                    problem: problem
                )
            )
        }
        guard Set(statusIDs).count == statusIDs.count,
              statusIDs == observedIDs
        else {
            throw failure(
                .inconsistent,
                "Model row identifiers were duplicated or out of order."
            )
        }
        return statuses
    }

    enum ReadinessLevel: String, Equatable {
        case unknown
        case checking
        case stale
        case ready
        case warnings
        case blocked
        case unavailable

        var label: String {
            switch self {
            case .unknown: return "Not checked"
            case .checking: return "Checking…"
            case .stale: return "Stale"
            case .ready: return "Ready"
            case .warnings: return "Ready with warnings"
            case .blocked: return "Blocked"
            case .unavailable: return "Unavailable"
            }
        }

        var symbol: String {
            switch self {
            case .unknown: return "questionmark.circle"
            case .checking: return "clock"
            case .stale: return "arrow.clockwise.circle"
            case .ready: return "checkmark.circle.fill"
            case .warnings: return "exclamationmark.triangle.fill"
            case .blocked: return "xmark.octagon.fill"
            case .unavailable: return "bolt.slash"
            }
        }

        var color: Color {
            switch self {
            case .unknown, .checking, .stale: return .secondary
            case .ready: return .green
            case .warnings: return .orange
            case .blocked: return .red
            case .unavailable: return .secondary
            }
        }
    }

    var readinessDecision: ReadinessDecision {
        func make(
            _ level: ReadinessLevel,
            _ headline: String,
            _ explanation: String,
            cause: String? = nil,
            recovery: ReadinessDecision.Recovery = .none
        ) -> ReadinessDecision {
            let checkRevision = doctor?.checks
                .map { "\($0.name)=\($0.status)" }
                .joined(separator: "|")
                .hashValue ?? 0
            let modelRevision = modelStatuses
                .map {
                    "\($0.id)=\($0.present)-\($0.partial)-"
                        + "\($0.revisionVerified)-\($0.selectedForConfig)"
                }
                .joined(separator: "|")
                .hashValue
            let failureRevision = [
                doctorEvidenceFailure.map {
                    "\($0.source.rawValue)-\($0.kind.rawValue)"
                } ?? "doctor-none",
                modelEvidenceFailure.map {
                    "\($0.source.rawValue)-\($0.kind.rawValue)"
                } ?? "models-none",
            ].joined(separator: "|")
            let token = [
                "readiness",
                level.rawValue,
                config.effectiveConfigFingerprint,
                doctorFingerprint ?? "no-doctor-fingerprint",
                String(checkRevision),
                String(modelRevision),
                failureRevision,
                String(config.fieldErrors.count),
            ].joined(separator: "|")
            return ReadinessDecision(
                level: level,
                headline: headline,
                explanation: explanation,
                cause: cause,
                recovery: recovery,
                announcementToken: token
            )
        }

        if isConnectingEngine || isRunningDoctor || config.isValidating {
            return make(
                .checking,
                "Checking local readiness",
                "Auralign is collecting engine, model, and configuration evidence."
            )
        }

        if let loadFailure {
            return make(
                .unavailable,
                "App resources unavailable",
                "The bundled configuration authority could not be loaded.",
                cause: privacySafePresentationMessage(loadFailure),
                recovery: .openSettings
            )
        }
        if bridge == nil || handshake == nil || startupError != nil
            || !config.isEngineSynchronized {
            let cause = startupError
                ?? config.engineSyncError
                ?? enginePresentationStatus.message
            return make(
                .unavailable,
                "Local runtime unavailable",
                "A compatible Python 3.11 Auralign engine is not connected.",
                cause: privacySafePresentationMessage(cause),
                recovery: .openSettings
            )
        }
        if config.manifestDriftDetected {
            return make(
                .blocked,
                "Engine contract changed",
                "The app and connected engine do not share the same configuration schema.",
                cause: "The bundled and live configuration manifests differ.",
                recovery: .openSettings
            )
        }
        if !config.uncataloguedLiveFields.isEmpty {
            return make(
                .blocked,
                "Unknown engine parameters",
                "The connected engine exposes fields this app cannot safely configure.",
                cause:
                    "\(config.uncataloguedLiveFields.count) live field"
                    + (config.uncataloguedLiveFields.count == 1 ? " is" : "s are")
                    + " absent from the reviewed field catalog.",
                recovery: .openSettings
            )
        }
        if !config.isValid {
            let cause = config.runtimeModelSelectionIssue
                ?? config.validationError
                ?? config.fieldErrors.first?.message
                ?? "The effective configuration has not passed authoritative validation."
            return make(
                .blocked,
                "Configuration blocks readiness",
                "Runtime checks cannot be accepted for an invalid configuration.",
                cause: privacySafePresentationMessage(cause),
                recovery: config.fieldErrors.isEmpty
                    ? .none : .showFirstConfigurationIssue
            )
        }
        if let doctorFingerprint, doctorFingerprint != config.effectiveConfigFingerprint {
            return make(
                .stale,
                "Readiness evidence is stale",
                "The effective configuration changed after the last readiness check.",
                cause: "Existing evidence does not describe the current configuration.",
                recovery: .refresh
            )
        }

        let failures = [doctorEvidenceFailure, modelEvidenceFailure].compactMap { $0 }
        if let unavailable = failures.first(where: { $0.kind == .unavailable }) {
            return make(
                .unavailable,
                "\(unavailable.source.label) unavailable",
                "The local evidence command did not return usable results.",
                cause: unavailable.summary,
                recovery: .refresh
            )
        }
        if !failures.isEmpty {
            let cause = failures.map {
                "\($0.source.label): \($0.kind.label)"
            }.joined(separator: " · ")
            return make(
                .blocked,
                "Readiness evidence rejected",
                "Incomplete, malformed, or inconsistent evidence never establishes readiness.",
                cause: cause,
                recovery: .refresh
            )
        }
        guard let doctor else {
            return make(
                .unknown,
                "Readiness has not been checked",
                "Run checks for the current effective configuration before starting.",
                recovery: .refresh
            )
        }
        let evidenceIssue = readinessEvidenceIssue
        let failingModel = modelStatuses.first { status in
            guard status.selectedForConfig else { return false }
            let entry = manifest.modelManifest.first { $0.id == status.id }
            return entry?.isRuntimeSelectable != true
                || !status.present
                || status.partial
                || !status.revisionVerified
                || status.problem != nil
        }
        if let evidenceIssue, let failingModel {
            return make(
                .blocked,
                "Required model evidence blocks this run",
                "A model selected by the effective configuration is unresolved.",
                cause: evidenceIssue,
                recovery: .showModel(failingModel.id)
            )
        }
        if !doctor.ok, let first = doctor.errors.first {
            let count = doctor.errors.count
            return make(
                .blocked,
                "Runtime checks are blocked",
                "\(count) blocking check\(count == 1 ? "" : "s") must be resolved.",
                cause: "A required runtime check reported an error.",
                recovery: .showCheck(first.name)
            )
        }
        if let evidenceIssue {
            return make(
                .blocked,
                "Required evidence blocks this run",
                "The accepted checks do not establish a complete local runtime.",
                cause: evidenceIssue,
                recovery: .refresh
            )
        }
        if let first = doctor.warnings.first {
            let count = doctor.warnings.count
            return make(
                .warnings,
                "Ready with warnings",
                "Required local checks passed with \(count) warning"
                    + (count == 1 ? "." : "s."),
                cause: "A non-blocking runtime check reported a warning.",
                recovery: .showCheck(first.name)
            )
        }
        return make(
            .ready,
            "Ready for local processing",
            "The exact effective configuration passed required runtime and model checks."
        )
    }

    var readinessLevel: ReadinessLevel { readinessDecision.level }

    // MARK: - Input probing

    @discardableResult
    func selectInput(_ url: URL?) -> Bool {
        guard !isPresentationFixture, !workMutationLocked else { return false }
        jobDraftDate = Date()
        inputURL = url
        return true
    }

    @discardableResult
    func selectOutputRoot(_ url: URL?) -> Bool {
        guard !isPresentationFixture, !workMutationLocked else { return false }
        jobDraftDate = Date()
        outputRootURL = url
        return true
    }

    @discardableResult
    func setWriteDebugReport(_ enabled: Bool) -> Bool {
        guard !isPresentationFixture, !workMutationLocked else { return false }
        writeDebugReport = enabled
        return true
    }

    private func probeInput() {
        inputProbeGeneration &+= 1
        let generation = inputProbeGeneration
        let engineGeneration = connectionGeneration
        let previousProbe = inputProbeTask
        previousProbe?.cancel()
        inputProbe = nil
        inputProbeSnapshot = nil
        inputProbeError = nil
        guard hasApplicationOwnership,
              !isTerminating,
              !isPresentationFixture,
              let inputURL
        else {
            return
        }
        guard let inspectedSnapshot = SourceFileSnapshot.capture(at: inputURL) else {
            inputProbeError =
                "The selected media is unavailable or is not a regular local file."
            return
        }
        guard let bridge else { return }
        inputProbeTask = Task { [weak self] in
            if let previousProbe {
                await previousProbe.value
            }
            guard !Task.isCancelled else { return }
            do {
                let payload = try await bridge.runExpecting(
                    event: "probe",
                    command: ["probe"],
                    input: .object([
                        "path": .string(inspectedSnapshot.canonicalURL.path),
                    ]),
                    timeout: 60
                )
                let probe = try Self.validateMediaProbePayload(
                    payload,
                    sourceSnapshot: inspectedSnapshot
                )
                guard let self,
                      self.inputProbeGeneration == generation,
                      self.connectionGeneration == engineGeneration,
                      self.bridge === bridge,
                      SourceFileSnapshot.capture(at: self.inputURL)
                        == inspectedSnapshot,
                      !Task.isCancelled
                else { return }
                self.inputProbeSnapshot = inspectedSnapshot
                self.inputProbe = probe
            } catch {
                guard let self,
                      self.inputProbeGeneration == generation,
                      self.connectionGeneration == engineGeneration,
                      self.bridge === bridge,
                      self.inputURL == inputURL,
                      !Task.isCancelled
                else { return }
                self.inputProbeSnapshot = nil
                self.inputProbeError = error.localizedDescription
            }
        }
    }

    nonisolated static func validateMediaProbePayload(
        _ payload: JSONValue,
        sourceSnapshot: SourceFileSnapshot
    ) throws -> MediaProbe {
        // The bridge deliberately omits the private source path from its
        // response. Bind the response to the request using the captured local
        // file identity plus the request's connection/generation guards.
        guard SourceFileSnapshot.capture(at: sourceSnapshot.canonicalURL)
                == sourceSnapshot else {
            throw EngineBridge.BridgeError(
                message:
                    "The selected media changed while it was being inspected. "
                    + "Inspect it again before starting."
            )
        }
        guard let probe = MediaProbe.parse(from: payload) else {
            throw EngineBridge.BridgeError(
                message: "Engine returned incomplete or invalid media metadata."
            )
        }
        return probe
    }

    // MARK: - Job control

    /// Navigate from a global validation summary to the exact catalog field.
    /// Python remains the error authority; Swift only resolves the published
    /// dotted location into presentation metadata.
    @discardableResult
    func navigateToConfigurationError(_ error: ConfigFieldError) -> Bool {
        let returnSection = selectedSection
        let location = error.dottedLocation
        let entry = catalog.fieldsByKey[location]
            ?? catalog.fields.first {
                location.hasPrefix($0.key + ".")
            }
        guard let entry else {
            // A model-level validator can truthfully identify only a section
            // (for example `diarization`). Navigate to that section-level
            // inline error without inventing a field target.
            let sectionID = catalog.section(location)?.id
                ?? catalog.fields.first {
                    $0.key.hasPrefix(location + ".")
                }?.section
            if let sectionID {
                fieldNavigationSequence &+= 1
                selectedSection = .configuration(sectionID)
                fieldNavigationRequest = nil
                consumedFieldNavigationRequestID = nil
                keyboardFocusedConfigurationControlIdentifier = nil
                sectionNavigationRequest = SectionNavigationRequest(
                    id: fieldNavigationSequence,
                    sectionID: sectionID,
                    message: privacySafePresentationMessage(error.message),
                    returnSection: returnSection
                )
                consumedSectionNavigationRequestID = nil
                return true
            }
            selectedSection = .allParameters
            fieldNavigationRequest = nil
            consumedFieldNavigationRequestID = nil
            keyboardFocusedConfigurationControlIdentifier = nil
            sectionNavigationRequest = nil
            consumedSectionNavigationRequestID = nil
            return false
        }
        fieldNavigationSequence &+= 1
        selectedSection = .configuration(entry.section)
        sectionNavigationRequest = nil
        consumedSectionNavigationRequestID = nil
        let request = FieldNavigationRequest(
            id: fieldNavigationSequence,
            sectionID: entry.section,
            fieldKey: entry.key,
            message: privacySafePresentationMessage(error.message),
            revealAdvanced: entry.disposition == .advanced,
            returnSection: returnSection
        )
        consumedFieldNavigationRequestID = nil
        keyboardFocusedConfigurationControlIdentifier = nil
        fieldNavigationRequest = request
        return true
    }

    func consumeFieldNavigationRequest(_ id: Int) {
        guard let request = fieldNavigationRequest,
              request.id == id
        else { return }
        consumedFieldNavigationRequestID = id
        keyboardFocusedConfigurationControlIdentifier =
            AccessibilityIdentifier.fieldControl(request.fieldKey)
    }

    func consumeSectionNavigationRequest(_ id: Int) {
        guard sectionNavigationRequest?.id == id else { return }
        consumedSectionNavigationRequestID = id
    }

    @discardableResult
    func returnFromConfigurationError() -> Bool {
        let returnSection = fieldNavigationRequest?.returnSection
            ?? sectionNavigationRequest?.returnSection
        guard fieldNavigationRequest != nil || sectionNavigationRequest != nil else {
            return false
        }
        selectedSection = returnSection ?? .newJob
        fieldNavigationRequest = nil
        consumedFieldNavigationRequestID = nil
        keyboardFocusedConfigurationControlIdentifier = nil
        sectionNavigationRequest = nil
        consumedSectionNavigationRequestID = nil
        return true
    }

    private func announceJobPhase(_ phase: JobController.Phase) {
        let message: String
        let token: String
        let priority: NSAccessibilityPriorityLevel
        switch phase {
        case .idle:
            return
        case .running:
            message = "Transcription started."
            token = "job-phase-running"
            priority = .medium
        case .resultPendingExit:
            message = "Result received. Finalizing the engine process before publishing."
            token = "job-phase-result-pending-exit"
            priority = .medium
        case .stopping:
            message = "Cancellation requested. Stopping and reaping the engine process."
            token = "job-phase-stopping"
            priority = .high
        case .completed:
            message = "Transcription completed. Results are available."
            token = "job-phase-completed"
            priority = .high
        case .failed:
            message = "Transcription failed. Review Activity for the cause and recovery."
            token = "job-phase-failed"
            priority = .high
        case .cancelled:
            message = "Transcription cancelled. Verified partial output is shown if available."
            token = "job-phase-cancelled"
            priority = .high
        }
        AccessibilityAnnouncementCenter.shared.announce(
            message,
            token: token,
            priority: priority
        )
    }

    @discardableResult
    func navigateToFirstConfigurationError() -> Bool {
        guard let error = config.fieldErrors.first else { return false }
        return navigateToConfigurationError(error)
    }

    enum StartBlocker: Equatable {
        case resourceFailure(String)
        case engineConnecting
        case noEngine(String)
        case noInput
        case inputInspectionPending
        case invalidInput(String)
        case noOutputRoot
        case invalidOutputRoot
        case plannedJobDirectoryExists
        case configurationUnavailable(String)
        case configurationValidating
        case invalidConfiguration
        case readinessChecking
        case readinessUnknown
        case readinessStale
        case readinessBlocked(String)
        case jobRunning

        var message: String {
            switch self {
            case .resourceFailure(let detail): return detail
            case .engineConnecting:
                return "Connecting to and validating the local engine…"
            case .noEngine(let detail): return detail
            case .noInput: return "Choose an audio or video file first."
            case .inputInspectionPending:
                return "Wait for media inspection to complete."
            case .invalidInput(let detail):
                return "The selected media could not be inspected: \(detail)"
            case .noOutputRoot: return "Choose an output folder first."
            case .invalidOutputRoot:
                return "The output folder is unavailable or not writable."
            case .plannedJobDirectoryExists:
                return "The planned job folder already exists. Start a new job or "
                    + "choose another output folder."
            case .configurationUnavailable(let detail): return detail
            case .configurationValidating:
                return "Wait for authoritative configuration validation to complete."
            case .invalidConfiguration:
                return "The configuration is invalid; fix the flagged fields."
            case .readinessChecking:
                return "Wait for backend startup readiness checks to complete."
            case .readinessUnknown:
                return "Run Runtime & Models readiness checks before starting."
            case .readinessStale:
                return "Configuration changed; re-run Runtime & Models readiness checks."
            case .readinessBlocked(let detail): return detail
            case .jobRunning: return "A job is already running."
            }
        }
    }

    var startBlocker: StartBlocker? {
        if let loadFailure { return .resourceFailure(loadFailure) }
        if workMutationLocked { return .jobRunning }
        if isConnectingEngine { return .engineConnecting }
        guard bridge != nil, handshake != nil else {
            return .noEngine(startupError ?? enginePresentationStatus.message)
        }
        if inputURL == nil { return .noInput }
        if let inputProbeError { return .invalidInput(inputProbeError) }
        if inputProbe == nil { return .inputInspectionPending }
        guard let inputProbeSnapshot else { return .inputInspectionPending }
        if !isPresentationFixture,
           SourceFileSnapshot.capture(at: inputURL) != inputProbeSnapshot {
            return .invalidInput(
                "The selected media changed after inspection. Inspect it again before starting."
            )
        }
        guard let outputRootURL else { return .noOutputRoot }
        if outputRootUsableOverride != true {
            var outputIsDirectory: ObjCBool = false
            guard FileManager.default.fileExists(
                atPath: outputRootURL.path,
                isDirectory: &outputIsDirectory
            ),
                outputIsDirectory.boolValue,
                FileManager.default.isWritableFile(atPath: outputRootURL.path)
            else {
                return .invalidOutputRoot
            }
        }
        if let plannedJobDirectory,
           FileManager.default.fileExists(atPath: plannedJobDirectory.path) {
            return .plannedJobDirectoryExists
        }
        if let syncError = config.engineSyncError {
            return .configurationUnavailable(syncError)
        }
        if !config.isEngineSynchronized {
            return .configurationUnavailable(
                "Engine presets and configuration schema have not loaded."
            )
        }
        if config.manifestDriftDetected {
            return .configurationUnavailable(
                "The engine schema differs from this app build."
            )
        }
        if !config.uncataloguedLiveFields.isEmpty {
            return .configurationUnavailable(
                "The engine exposes parameters this app cannot safely configure."
            )
        }
        if config.isValidating { return .configurationValidating }
        if let validationError = config.validationError {
            return .configurationUnavailable(validationError)
        }
        if !config.validationIsCurrent {
            return .configurationUnavailable(
                "The current configuration has not been authoritatively validated."
            )
        }
        if let modelIssue = config.runtimeModelSelectionIssue {
            return .configurationUnavailable(modelIssue)
        }
        if !config.isValid { return .invalidConfiguration }
        let decision = readinessDecision
        switch decision.level {
        case .checking:
            return .readinessChecking
        case .unknown:
            return .readinessUnknown
        case .stale:
            return .readinessStale
        case .blocked, .unavailable:
            let detail = [decision.explanation, decision.cause]
                .compactMap { $0 }
                .joined(separator: " ")
            return .readinessBlocked(detail)
        case .ready, .warnings:
            return nil
        }
    }

    func startJob() {
        guard !isPresentationFixture,
              !workMutationLocked,
              startBlocker == nil,
              let bridge,
              let inputURL,
              let inputProbeSnapshot,
              let outputRootURL,
              let plannedJobDirectory else { return }
        guard SourceFileSnapshot.capture(at: inputURL) == inputProbeSnapshot else {
            inputProbe = nil
            self.inputProbeSnapshot = nil
            inputProbeError =
                "The selected media changed after inspection. Inspect it again before starting."
            return
        }
        let effective = config.effectiveConfig
        job.start(
            bridge: bridge,
            input: inputURL,
            expectedSourceSnapshot: inputProbeSnapshot,
            outputRoot: outputRootURL,
            jobDirectory: plannedJobDirectory,
            config: effective,
            configFingerprint: config.effectiveConfigFingerprint,
            debugReport: writeDebugReport,
            expectations: JobExpectations(config: effective)
        )
        selectedSection = .processing
    }

    @discardableResult
    func resetJob() -> Bool {
        guard !isPresentationFixture, !workMutationLocked else { return false }
        job.reset()
        return true
    }

    @discardableResult
    func beginNewJob() -> Bool {
        guard resetJob() else { return false }
        jobDraftDate = Date()
        selectedSection = .newJob
        return true
    }

    var plannedJobDirectory: URL? {
        guard let outputRootURL, let inputURL else { return nil }
        return JobController.proposedJobDirectory(
            outputRoot: outputRootURL,
            mediaURL: inputURL,
            now: presentationDate ?? jobDraftDate
        )
    }

    func proposedJobDirectory(outputRoot: URL, mediaURL: URL) -> URL {
        JobController.proposedJobDirectory(
            outputRoot: outputRoot,
            mediaURL: mediaURL,
            now: presentationDate ?? jobDraftDate
        )
    }

    func privacySafePresentationMessage(_ message: String) -> String {
        let engineDescription = engineLocation.usesEmbeddedRuntime
            ? "Auralign application data"
            : "the selected engine checkout"
        return PrivacySafePresentation.message(
            message,
            replacing: [
                (inputURL, "the selected media"),
                (outputRootURL, "the selected output folder"),
                (engineLocation.repositoryRoot, engineDescription),
                (
                    engineLocation.configuredModelCacheRoot,
                    "the selected model directory"
                ),
            ]
        )
    }

    var resultDrift: ResultDrift {
        guard case .completed(let result) = job.phase else {
            return ResultDrift(sourceChanged: false, configurationChanged: false)
        }
        // Presentation fixtures deliberately point at synthetic, nonexistent
        // paths. Re-statting those paths would make every completed fixture
        // look stale even when its seeded source identity is unchanged. Live
        // models continue to use a fresh filesystem identity check.
        let currentSnapshot = isPresentationFixture
            ? inputProbeSnapshot
            : SourceFileSnapshot.capture(at: inputURL)
        let sourceChanged = currentSnapshot?.canonicalURL != result.sourceURL
            || currentSnapshot != result.sourceSnapshot
        return ResultDrift(
            sourceChanged: sourceChanged,
            configurationChanged:
                result.configFingerprint != config.effectiveConfigFingerprint
        )
    }

    var resultsAreStale: Bool { resultDrift.isStale }

    private func clearReadiness() {
        doctor = nil
        doctorEvidenceFailure = nil
        doctorFingerprint = nil
        isRunningDoctor = false
        modelStatuses = []
        modelEvidenceFailure = nil
    }

    private func readinessRequestIsCurrent(
        generation: Int,
        engineGeneration: Int,
        bridge expectedBridge: EngineBridge
    ) -> Bool {
        readinessGeneration == generation
            && connectionGeneration == engineGeneration
            && bridge === expectedBridge
            && !Task.isCancelled
    }

    private func finishReadiness(generation: Int) {
        if readinessGeneration == generation {
            isRunningDoctor = false
        }
    }
}
