// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI
import UniformTypeIdentifiers

/// Compact, testable projection of the effective Python-authoritative
/// configuration. This is display data only; it never becomes a second
/// configuration model.
struct NewJobRunPlanProjection: Equatable {
    let preset: String
    let language: String
    let speakerCount: String
    let offline: Bool
    let overrideCount: Int
    let stages: [PipelineStage]

    init(effectiveConfig: JSONValue, preset: String, overrideCount: Int) {
        self.preset = preset
        let configuredLanguage = effectiveConfig
            .value(at: "asr.language")?
            .stringValue?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        language = configuredLanguage?.isEmpty == false
            ? configuredLanguage ?? "Automatic"
            : "Automatic"
        if effectiveConfig.value(at: "diarization.enabled")?.boolValue == false {
            speakerCount = "Disabled"
        } else if let speakers = effectiveConfig
            .value(at: "diarization.num_speakers")?
            .strictIntValue,
            speakers > 0 {
            speakerCount = String(speakers)
        } else {
            speakerCount = "Automatic"
        }
        offline = effectiveConfig.value(at: "offline")?.boolValue ?? false
        self.overrideCount = overrideCount
        stages = JobExpectations(config: effectiveConfig).expectedStages()
    }
}

protocol MediaDropURLProvider {
    var canLoadAuralignFileURL: Bool { get }
    func loadAuralignFileURL() async -> URL?
}

extension NSItemProvider: MediaDropURLProvider {
    var canLoadAuralignFileURL: Bool {
        canLoadObject(ofClass: URL.self)
    }

    func loadAuralignFileURL() async -> URL? {
        await withCheckedContinuation { continuation in
            _ = loadObject(ofClass: URL.self) { url, error in
                continuation.resume(returning: error == nil ? url : nil)
            }
        }
    }
}

enum MediaDropResolution: Equatable {
    case selected
    case unsupported
    case unreadable
    case rejected
}

@MainActor
enum MediaDropResolver {
    static func hasSupportedProvider<Provider: MediaDropURLProvider>(
        _ providers: [Provider]
    ) -> Bool {
        providers.contains(where: \.canLoadAuralignFileURL)
    }

    static func resolve<Provider: MediaDropURLProvider>(
        _ providers: [Provider],
        accept: (URL) -> Bool
    ) async -> MediaDropResolution {
        let candidates = providers.filter(\.canLoadAuralignFileURL)
        guard !candidates.isEmpty else { return .unsupported }
        for provider in candidates {
            guard let url = await provider.loadAuralignFileURL() else {
                continue
            }
            return accept(url) ? .selected : .rejected
        }
        return .unreadable
    }
}

/// Task-first entry point for one local run. The body deliberately has no
/// Start button: the window toolbar is the single primary Start/Cancel
/// authority and this view explains exactly why that authority is enabled or
/// disabled.
struct NewJobView: View {
    @EnvironmentObject private var model: AppModel
    @State private var isDropTargeted = false
    @State private var dropError: String?

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
                    AuralignPageHeader(
                        title: "New Transcription",
                        subtitle: "Choose local media and a destination, then review the "
                            + "effective run before starting it from the window toolbar.",
                        symbol: "plus.square.on.square"
                    )

                    // The alignment canvas is the visual centre of the
                    // workflow: it shows the journey the *effective
                    // configuration* will actually take, including honestly
                    // disabled phases, before anything runs.
                    AlignmentCanvasView(
                        projection: AlignmentCanvasProjection.live(model: model),
                        sourceTitle: model.inputURL?.lastPathComponent
                    )

                    if geometry.size.width >= AuralignBreakpoint.workbench {
                        wideWorkbench
                    } else {
                        compactWorkbench
                    }
                }
                .padding(AuralignVisualSystem.spacingXL)
                .frame(
                    maxWidth: AuralignBreakpoint.workbenchMaxWidth,
                    alignment: .leading
                )
            }
        }
        // Keep the native toolbar title concise at the 900-point minimum
        // width and under localization expansion. The content heading below
        // retains the more descriptive “New Transcription” label.
        .navigationTitle("New Job")
    }

    private var wideWorkbench: some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingXL) {
            sourceAndDestination
                .frame(minWidth: 390, maxWidth: .infinity, alignment: .top)
            runPlan
                .frame(width: 360, alignment: .top)
        }
    }

    private var compactWorkbench: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
            sourceAndDestination
            runPlan
        }
    }

    private var sourceAndDestination: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
            AuralignSection(
                title: "Source",
                subtitle: "The local engine probes the selected file; its extension is "
                    + "never treated as proof that the media is valid."
            ) {
                sourceWell
            }

            AuralignSection(
                title: "Destination",
                subtitle: "Every run receives a new empty job directory."
            ) {
                destinationSummary
            }

            AuralignSection(
                title: "Run Options",
                subtitle: "Debug reports are local, bounded to this job, and redacted."
            ) {
                Toggle(
                    "Write redacted debug report",
                    isOn: Binding(
                        get: { model.writeDebugReport },
                        set: { _ = model.setWriteDebugReport($0) }
                    )
                )
                .toggleStyle(.checkbox)
                .help(
                    "Writes a redacted debug_report.json into the job folder, "
                        + "including a trace if the job fails."
                )
                .disabled(model.workMutationLocked || model.isPresentationFixture)
                .accessibilityIdentifier(AccessibilityIdentifier.debugReport)
            }

            if model.config.isValidating
                || model.config.validationError != nil
                || !model.config.fieldErrors.isEmpty {
                ValidationSummaryView()
            }
        }
    }

    // MARK: - Source

    private var sourceWell: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
            Group {
                if let url = model.inputURL {
                    selectedSource(url)
                } else {
                    emptySource
                }
            }
            if let dropError {
                AuralignStatusBanner(
                    title: "Media was not selected",
                    message: dropError,
                    tone: .warning,
                    symbol: "exclamationmark.triangle.fill",
                    actionTitle: "Choose Media…",
                    actionIdentifier: AccessibilityIdentifier.mediaChoose,
                    action: chooseMedia
                )
                .accessibilityIdentifier(AccessibilityIdentifier.mediaDropError)
            }
        }
        .frame(maxWidth: .infinity)
        .onDrop(of: [.fileURL], isTargeted: $isDropTargeted) { providers in
            loadDroppedURL(providers)
        }
        .disabled(model.workMutationLocked || model.isPresentationFixture)
        .accessibilityIdentifier(AccessibilityIdentifier.mediaDropZone)
    }

    private var emptySource: some View {
        VStack(spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: "doc.badge.plus")
                .font(.system(size: 30))
                .foregroundStyle(isDropTargeted ? Color.accentColor : .secondary)
                .accessibilityHidden(true)
            VStack(spacing: AuralignVisualSystem.spacingXS) {
                Text("Drop local audio or video here")
                    .font(.headline)
                Text("Auralign will inspect the real media before enabling Start.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            Button("Choose Media…") {
                chooseMedia()
            }
            .accessibilityIdentifier(AccessibilityIdentifier.mediaChoose)
        }
        .padding(AuralignVisualSystem.spacingXL)
        .frame(maxWidth: .infinity, minHeight: 150)
        .background(
            isDropTargeted
                ? Color.accentColor.opacity(0.08)
                : Color(nsColor: .controlBackgroundColor)
        )
        .overlay {
            RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius)
                .strokeBorder(
                    isDropTargeted
                        ? Color.accentColor
                        : Color(nsColor: .separatorColor),
                    style: StrokeStyle(lineWidth: isDropTargeted ? 2 : 1, dash: [6, 4])
                )
        }
        .clipShape(
            RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius)
        )
    }

    private func selectedSource(_ url: URL) -> some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: "doc.fill")
                .font(.title2)
                .foregroundStyle(.tint)
                .frame(width: 28)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                Text(url.lastPathComponent)
                    .font(.headline)
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .help(url.lastPathComponent)

                sourceEvidence
            }

            Spacer(minLength: AuralignVisualSystem.spacingM)

            Button("Change…") {
                chooseMedia()
            }
            .accessibilityIdentifier(AccessibilityIdentifier.mediaChoose)
        }
        .padding(AuralignVisualSystem.spacingL)
        .background(Color(nsColor: .controlBackgroundColor))
        .overlay {
            RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius)
                .strokeBorder(Color(nsColor: .separatorColor))
        }
        .clipShape(
            RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius)
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.mediaSummary)
    }

    @ViewBuilder
    private var sourceEvidence: some View {
        if let probe = model.inputProbe {
            HStack(spacing: AuralignVisualSystem.spacingS) {
                AuralignStatusBadge(
                    text: "Inspected",
                    tone: .success,
                    symbol: "checkmark.circle.fill"
                )
                Text(probeSummary(probe))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            .accessibilityIdentifier(AccessibilityIdentifier.mediaProbe)
        } else if let error = model.inputProbeError {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                AuralignStatusBadge(
                    text: "Inspection failed",
                    tone: .failure,
                    symbol: "xmark.octagon.fill"
                )
                Text(privacySafeMessage(error))
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityIdentifier(AccessibilityIdentifier.mediaProbeError)
        } else {
            AuralignStatusBadge(
                text: "Inspecting local media",
                tone: .active,
                symbol: "ellipsis.circle"
            )
        }
    }

    private func probeSummary(_ probe: MediaProbe) -> String {
        var parts: [String] = [probe.durationText]
        if let format = probe.formatName, !format.isEmpty {
            parts.append(format)
        }
        if let codec = probe.codec, !codec.isEmpty {
            parts.append(codec)
        }
        if let rate = probe.sampleRate {
            parts.append("\(rate.formatted()) Hz")
        }
        if let channels = probe.channels {
            parts.append(channels == 1 ? "mono" : "\(channels) channels")
        }
        return parts.joined(separator: " · ")
    }

    private func chooseMedia() {
        guard !model.isPresentationFixture else { return }
        guard let url = LocalFilePanel.chooseMedia() else { return }
        if model.selectInput(url) {
            dropError = nil
        }
    }

    private func loadDroppedURL(_ providers: [NSItemProvider]) -> Bool {
        guard !model.isPresentationFixture, !model.workMutationLocked else {
            return false
        }
        guard MediaDropResolver.hasSupportedProvider(providers) else {
            dropError =
                "The dropped item did not provide a local file URL. "
                + "Choose an audio or video file instead."
            return false
        }
        dropError = nil
        Task { @MainActor in
            switch await MediaDropResolver.resolve(
                providers,
                accept: { model.selectInput($0) }
            ) {
            case .selected:
                dropError = nil
            case .rejected:
                dropError =
                    "Auralign could not accept the dropped file in the current state. "
                    + "Wait for the active job to finish or choose media again."
            case .unsupported:
                dropError =
                    "The dropped item did not provide a local file URL. "
                    + "Choose an audio or video file instead."
            case .unreadable:
                dropError =
                    "macOS could not read a local file URL from the dropped items. "
                    + "Use Choose Media to select the file directly."
            }
        }
        return true
    }

    // MARK: - Destination

    private var destinationSummary: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
            HStack(alignment: .firstTextBaseline, spacing: AuralignVisualSystem.spacingM) {
                Image(systemName: "folder")
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                    if let root = model.outputRootURL {
                        Text((root.path as NSString).abbreviatingWithTildeInPath)
                            .lineLimit(2)
                            .truncationMode(.middle)
                            .help("Selected output folder")
                            .textSelection(.enabled)
                            .accessibilityIdentifier(AccessibilityIdentifier.outputRoot)
                    } else {
                        Text("No output folder selected")
                            .foregroundStyle(.secondary)
                    }
                    Text(proposedJobFolderText)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.outputJobFolder
                        )
                }
                Spacer(minLength: AuralignVisualSystem.spacingM)
                Button(model.outputRootURL == nil ? "Choose…" : "Change…") {
                    chooseOutputRoot()
                }
                .disabled(model.workMutationLocked || model.isPresentationFixture)
                .accessibilityIdentifier(AccessibilityIdentifier.outputChoose)
            }

            Label(
                "Planned, not created. The local engine rechecks that the folder "
                    + "is new and empty at launch.",
                systemImage: "folder.badge.plus"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.outputSummary)
    }

    private var proposedJobFolderText: String {
        guard model.outputRootURL != nil else {
            return "Fresh job folder: available after a destination is selected"
        }
        guard model.inputURL != nil else {
            return "Fresh job folder: name available after media is selected"
        }
        guard let proposed = model.plannedJobDirectory else {
            return "Fresh job folder: unavailable"
        }
        let path = (proposed.path as NSString).abbreviatingWithTildeInPath
        return "Planned fresh job folder: \(path)"
    }

    private func chooseOutputRoot() {
        guard !model.isPresentationFixture else { return }
        guard let url = LocalFilePanel.chooseOutputRoot() else { return }
        _ = model.selectOutputRoot(url)
    }

    // MARK: - Run plan

    private var runPlan: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
            AuralignSection(
                title: "Run Plan",
                subtitle: "A read-only projection of the effective configuration that "
                    + "will be captured when the job starts."
            ) {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                    summaryRow("Preset", plan.preset)
                    summaryRow("Language", plan.language)
                    summaryRow("Speaker count", plan.speakerCount)
                    summaryRow("Offline mode", plan.offline ? "Required" : "Not required")
                    summaryRow(
                        "Overrides",
                        plan.overrideCount == 0
                            ? "None"
                            : "\(plan.overrideCount) modified"
                    )

                    HStack {
                        Button("Edit Configuration") {
                            model.selectedSection = .configuration("general")
                        }
                        .controlSize(.small)
                        Spacer()
                        Button("Parameter Library") {
                            model.selectedSection = .allParameters
                        }
                        .controlSize(.small)
                    }
                }
            }

            AuralignSection(
                title: "Ordered Sequence",
                subtitle: "\(plan.stages.count) ordinal stages. Order and spacing do "
                    + "not represent duration or progress."
            ) {
                stageLedger
            }

            AuralignSection(title: "Readiness") {
                readinessSummary
            }

            nextSafeAction
        }
        .accessibilityIdentifier(AccessibilityIdentifier.runPlan)
    }

    private var plan: NewJobRunPlanProjection {
        NewJobRunPlanProjection(
            effectiveConfig: model.config.effectiveConfig,
            preset: model.config.selectedPreset,
            overrideCount: model.config.modifiedKeys.count
        )
    }

    private func summaryRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: AuralignVisualSystem.spacingM) {
            Text(label)
                .foregroundStyle(.secondary)
            Spacer(minLength: AuralignVisualSystem.spacingM)
            Text(value)
                .fontWeight(.medium)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .font(.callout)
    }

    private var stageLedger: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
            ForEach(Array(plan.stages.enumerated()), id: \.element.id) { index, stage in
                HStack(alignment: .firstTextBaseline, spacing: AuralignVisualSystem.spacingS) {
                    Text("\(index + 1)")
                        .font(.caption.monospacedDigit().weight(.semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 22, alignment: .trailing)
                    Text(stage.title)
                        .font(.callout)
                    Spacer(minLength: AuralignVisualSystem.spacingS)
                    if case .skipped = stage.status {
                        Text("Disabled")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(
                    "Stage \(index + 1) of \(plan.stages.count), \(stage.title)"
                        + (isSkipped(stage) ? ", disabled" : "")
                )
                .accessibilityIdentifier(
                    AccessibilityIdentifier.runPlanStage(stage.id)
                )
            }
        }
        .accessibilityIdentifier(AccessibilityIdentifier.runPlanStages)
    }

    private func isSkipped(_ stage: PipelineStage) -> Bool {
        if case .skipped = stage.status { return true }
        return false
    }

    private var readinessSummary: some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            AuralignStatusBadge(
                text: model.readinessLevel.label,
                tone: readinessTone,
                symbol: model.readinessLevel.symbol
            )
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(readinessDetail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Runtime & Model Details") {
                    model.selectedSection = .readiness
                }
                .controlSize(.small)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.readinessSummary)
    }

    private var readinessTone: AuralignStatusTone {
        switch model.readinessLevel {
        case .unknown, .unavailable:
            return .neutral
        case .checking:
            return .active
        case .stale, .warnings:
            return .warning
        case .ready:
            return .success
        case .blocked:
            return .failure
        }
    }

    private var readinessDetail: String {
        model.readinessDecision.explanation
    }

    @ViewBuilder
    private var nextSafeAction: some View {
        let blocker = model.startBlocker
        #if DEBUG
        if model.isPresentationFixture, blocker == nil {
            AuralignStatusBanner(
                title: "Checked Synthetic State",
                message:
                    "This inert fixture represents a checked configuration. Runtime "
                    + "Start is disabled; this is not live engine evidence.",
                tone: .success,
                symbol: "checkmark.circle.fill"
            )
            .accessibilityIdentifier(AccessibilityIdentifier.nextSafeAction)
        } else {
            operationalNextSafeAction(blocker)
        }
        #else
        operationalNextSafeAction(blocker)
        #endif
    }

    private func operationalNextSafeAction(
        _ blocker: AppModel.StartBlocker?
    ) -> some View {
        AuralignStatusBanner(
            title: blocker == nil ? "Ready for Start" : "Next Safe Action",
            message: privacySafeMessage(
                blocker?.message
                    ?? "Use Start Transcription in the window toolbar. "
                        + "No network upload or telemetry is involved."
            ),
            tone: blocker == nil ? .success : .warning,
            symbol: blocker == nil
                ? "checkmark.circle.fill"
                : "arrow.forward.circle.fill",
            actionTitle: recovery?.title,
            actionIdentifier: AccessibilityIdentifier.nextSafeAction,
            action: recovery?.action
        )
        .accessibilityIdentifier(AccessibilityIdentifier.nextSafeAction)
    }

    private var recovery: (title: String, action: () -> Void)? {
        guard !model.isPresentationFixture, let blocker = model.startBlocker else {
            return nil
        }
        switch blocker {
        case .noInput, .invalidInput:
            return ("Choose Media", { chooseMedia() })
        case .noOutputRoot, .invalidOutputRoot, .plannedJobDirectoryExists:
            return ("Choose Output", { chooseOutputRoot() })
        case .invalidConfiguration:
            return ("Show First Issue", {
                _ = model.navigateToFirstConfigurationError()
            })
        case .jobRunning:
            return ("Show Activity", {
                model.selectedSection = .processing
            })
        case .engineConnecting, .noEngine, .configurationUnavailable,
             .readinessChecking, .readinessUnknown, .readinessStale,
             .readinessBlocked:
            return ("Runtime Details", {
                model.selectedSection = .readiness
            })
        case .resourceFailure, .inputInspectionPending, .configurationValidating:
            return nil
        }
    }

    private func privacySafeMessage(_ message: String) -> String {
        model.privacySafePresentationMessage(message)
    }
}
