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
                    AuralignWorkspaceHeader(
                        symbol: "plus.square.on.square",
                        accessibilityHeading: "New transcription workspace"
                    ) {
                        workspaceContext
                    }

                    AuralignSection(
                        title: "Local Source",
                        subtitle:
                            "Drop audio or video into the studio. Auralign inspects "
                            + "the real file before it can run."
                    ) {
                        sourceWell
                    }

                    // The phase map is a direct route into the effective run
                    // configuration. Its shapes remain representative rather
                    // than claiming unavailable waveform or timing data.
                    AlignmentCanvasView(
                        projection: AlignmentCanvasProjection.live(model: model),
                        sourceTitle: model.inputURL?.lastPathComponent,
                        onSelectPhase: openConfiguration
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
        .navigationTitle("New Job")
    }

    @ViewBuilder
    private var workspaceContext: some View {
        if let url = model.inputURL {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(url.lastPathComponent)
                    .font(AuralignType.pageTitle)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(
                    "Local source selected. Review the run below, then start it "
                        + "from the toolbar."
                )
                .font(AuralignType.metadata)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text("No source selected yet")
                    .font(AuralignType.pageTitle)
                Text(
                    "Drop local audio or video below, then review the effective "
                        + "run before starting it from the toolbar."
                )
                .font(AuralignType.metadata)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
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

    private func openConfiguration(_ phase: AlignmentCanvasPhase) {
        let group: PipelineNavigationGroup
        switch phase {
        case .source:
            group = .sourceAudio
        case .recognition:
            group = .recognition
        case .speakers:
            group = .speakers
        case .refinement:
            group = .refinement
        case .delivery:
            group = .delivery
        }
        guard let sectionID = group.firstAvailableSection(in: model.catalog) else {
            return
        }
        model.selectedSection = .configuration(sectionID)
    }

    // A confident, filled drop target: the same card shape and surface as
    // the selected-source summary below, so the two states read as one
    // component rather than two unrelated ones — only the fill accent,
    // icon, and copy change.
    private var emptySource: some View {
        HStack(alignment: .center, spacing: AuralignVisualSystem.spacingL) {
            ZStack {
                RoundedRectangle(cornerRadius: AuralignRadius.control)
                    .fill(
                        (isDropTargeted
                            ? Color.accentColor
                            : AuralignSpectrumStop.source.color)
                            .opacity(isDropTargeted ? 0.24 : 0.14)
                    )
                    .frame(width: 52, height: 52)
                Image(systemName: "waveform.badge.plus")
                    .font(.title2)
                    .foregroundStyle(
                        isDropTargeted
                            ? Color.accentColor
                            : AuralignSpectrumStop.source.color
                    )
            }
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(
                    isDropTargeted
                        ? "Release to add this file"
                        : "No local source selected"
                )
                .font(AuralignType.rowTitle)
                Text(
                    "Drop local audio or video here, or choose a file. Auralign "
                        + "inspects the real media before enabling Start."
                )
                .font(AuralignType.metadata)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: AuralignVisualSystem.spacingM)

            Button("Choose Media…") {
                chooseMedia()
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier(AccessibilityIdentifier.mediaChoose)
        }
        .padding(AuralignVisualSystem.spacingL)
        .frame(maxWidth: .infinity, minHeight: 158)
        .background(AuralignSurface.raised)
        .overlay {
            RoundedRectangle(cornerRadius: AuralignRadius.group)
                .strokeBorder(
                    isDropTargeted ? Color.accentColor : AuralignSurface.hairline,
                    lineWidth: isDropTargeted ? 2 : 1
                )
        }
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.group))
    }

    private func selectedSource(_ url: URL) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            AuralignMediaRow(
                title: url.lastPathComponent,
                metadata: sourceMetadataLine,
                swatch: auralignMediaRowSwatch(
                    symbol: "waveform",
                    tint: AuralignSpectrumStop.source.color
                ),
                containsInteractiveTrailing: true,
                trailing: {
                    HStack(spacing: AuralignVisualSystem.spacingS) {
                        sourceStatusBadge
                            .accessibilityIdentifier(AccessibilityIdentifier.mediaProbe)
                        Button("Change…") {
                            chooseMedia()
                        }
                        .controlSize(.small)
                        .accessibilityIdentifier(AccessibilityIdentifier.mediaChoose)
                    }
                }
            )
            .help(url.lastPathComponent)

            if let error = model.inputProbeError {
                Text(privacySafeMessage(error))
                    .font(AuralignType.metadata)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, AuralignVisualSystem.spacingM)
                    .accessibilityIdentifier(AccessibilityIdentifier.mediaProbeError)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.mediaSummary)
    }

    private var sourceMetadataLine: String {
        if let probe = model.inputProbe {
            return probeSummary(probe)
        }
        if model.inputProbeError != nil {
            return "Inspection failed — see details below."
        }
        return "Inspecting local media…"
    }

    @ViewBuilder
    private var sourceStatusBadge: some View {
        if model.inputProbe != nil {
            AuralignStatusBadge(
                text: "Inspected",
                tone: .success,
                symbol: "checkmark.circle.fill"
            )
        } else if model.inputProbeError != nil {
            AuralignStatusBadge(
                text: "Failed",
                tone: .failure,
                symbol: "xmark.octagon.fill"
            )
        } else {
            AuralignStatusBadge(
                text: "Inspecting",
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
                            .font(AuralignType.technicalBody)
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
                        .font(AuralignType.technical)
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
            .font(AuralignType.metadata)
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
                subtitle: "The effective configuration that will be captured when "
                    + "the job starts. The field above summarizes configured phase "
                    + "categories; Activity lists real execution order once reported."
            ) {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                    runPlanTable
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

    // A compact table replaces five right-flushed label/value rows: each row
    // carries a real category glyph, and `AuralignModifiedBadge` marks any
    // value that differs from the engine's schema default rather than
    // leaving the whole panel visually inert.
    private var runPlanTable: some View {
        Grid(
            alignment: .leading,
            horizontalSpacing: AuralignVisualSystem.spacingM,
            verticalSpacing: AuralignVisualSystem.spacingS
        ) {
            runPlanRow(
                symbol: "slider.horizontal.3",
                label: "Preset",
                value: plan.preset.capitalized,
                modified: fieldDiffersFromDefault("preset")
            )
            GridRow { Divider().gridCellColumns(2) }
            runPlanRow(
                symbol: "globe",
                label: "Language",
                value: plan.language,
                modified: fieldDiffersFromDefault("asr.language")
            )
            GridRow { Divider().gridCellColumns(2) }
            runPlanRow(
                symbol: "person.2",
                label: "Speakers",
                value: plan.speakerCount,
                modified: speakerCountDiffersFromDefault
            )
            GridRow { Divider().gridCellColumns(2) }
            runPlanRow(
                symbol: plan.offline ? "wifi.slash" : "wifi",
                label: "Offline mode",
                value: plan.offline ? "Required" : "Not required",
                modified: fieldDiffersFromDefault("offline")
            )
            GridRow { Divider().gridCellColumns(2) }
            runPlanRow(
                symbol: "pencil.and.list.clipboard",
                label: "Overrides",
                value: plan.overrideCount == 0
                    ? "None"
                    : "\(plan.overrideCount) modified",
                modified: false
            )
        }
        .padding(AuralignVisualSystem.spacingM)
        .background(AuralignSurface.raised)
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.group))
    }

    private func runPlanRow(
        symbol: String,
        label: String,
        value: String,
        modified: Bool
    ) -> some View {
        GridRow {
            HStack(spacing: AuralignVisualSystem.spacingS) {
                Image(systemName: symbol)
                    .foregroundStyle(.secondary)
                    .frame(width: 16)
                    .accessibilityHidden(true)
                if modified {
                    AuralignModifiedBadge()
                }
                Text(label)
                    .font(AuralignType.rowTitle)
                    .foregroundStyle(.secondary)
            }
            Text(value)
                .font(AuralignType.evidenceEmphasis)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
                .gridColumnAlignment(.trailing)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(label): \(value)" + (modified ? ", modified from default" : "")
        )
    }

    private func fieldDiffersFromDefault(_ key: String) -> Bool {
        guard let field = model.manifest.field(key) else { return false }
        return model.config.effectiveValue(key) != field.default
    }

    private var speakerCountDiffersFromDefault: Bool {
        if model.config.effectiveValue("diarization.enabled").boolValue == false {
            return fieldDiffersFromDefault("diarization.enabled")
        }
        return fieldDiffersFromDefault("diarization.num_speakers")
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
                    .font(AuralignType.metadata)
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
                    ?? "The source, destination, configuration, and local runtime "
                        + "are checked. No network upload or telemetry is involved."
            ),
            tone: blocker == nil ? .success : .warning,
            symbol: blocker == nil
                ? "checkmark.circle.fill"
                : "arrow.forward.circle.fill",
            actionTitle: blocker == nil ? "Start Transcription" : recovery?.title,
            actionIdentifier: AccessibilityIdentifier.nextSafeAction,
            action: blocker == nil ? { model.startJob() } : recovery?.action
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
