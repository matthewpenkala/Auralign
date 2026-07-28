// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// Main window: sidebar of workflow destinations, detail content, and a
/// persistent status bar showing engine and job state.
struct MainView: View {
    @EnvironmentObject private var model: AppModel
    @State private var columnVisibility: NavigationSplitViewVisibility

    init() {
        #if DEBUG
        _columnVisibility = State(
            initialValue: SnapshotDriver.authorizedInitialColumnVisibility(
                environment: ProcessInfo.processInfo.environment
            )
        )
        #else
        _columnVisibility = State(initialValue: .all)
        #endif
    }

    @ViewBuilder
    var body: some View {
        if let loadFailure = model.loadFailure {
            FatalBootstrapView(detail: loadFailure)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .onAppear {
                    AccessibilityAnnouncementCenter.shared.announce(
                        "Auralign could not load its configuration resources. "
                            + "The workspace is unavailable.",
                        token: "fatal-bootstrap-resource-failure",
                        priority: .high
                    )
                }
        } else {
            workspace
        }
    }

    private var workspace: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 220, ideal: 248, max: 320)
        } detail: {
            WorkspaceDetailView()
                .frame(minWidth: 600)
        }
        .toolbar {
            PrimaryJobToolbar(model: model)
        }
        #if DEBUG
        .onAppear {
            SnapshotDriver.recordEffectiveSidebarVisibility(columnVisibility)
        }
        .onChange(of: columnVisibility) { _, visibility in
            SnapshotDriver.recordEffectiveSidebarVisibility(visibility)
        }
        #endif
        .onChange(of: model.readinessDecision.announcementToken) { _, _ in
            let decision = model.readinessDecision
            let cause = decision.cause.map {
                " " + model.privacySafePresentationMessage($0)
            } ?? ""
            AccessibilityAnnouncementCenter.shared.announce(
                "\(decision.headline). \(decision.explanation)\(cause)",
                token: decision.announcementToken
            )
        }
    }

}

/// The single production workspace router. Debug design directions reuse this
/// exact content so a tournament shell cannot substitute mock readiness,
/// lifecycle, result, configuration, or parameter behavior.
struct WorkspaceDetailView: View {
    @EnvironmentObject private var model: AppModel

    @ViewBuilder
    var body: some View {
        ZStack {
            AuralignStudioBackdrop()
            Group {
                switch model.selectedSection {
                case .newJob, .none:
                    NewJobView()
                case .configuration(let sectionID):
                    if let group = PipelineNavigationGroup.containing(
                        sectionID: sectionID
                    ) {
                        PipelineConfigurationView(
                            group: group,
                            sectionID: sectionID
                        )
                    } else {
                        ConfigSectionView(sectionID: sectionID)
                    }
                case .allParameters:
                    AllParametersView()
                case .readiness:
                    ReadinessView()
                case .processing:
                    ProcessingView()
                case .results:
                    ResultsView()
                }
            }
        }
    }
}

/// One primary job authority shared by the shipping shell and Debug design
/// directions. Prototype captures therefore exercise production Start/Cancel
/// ownership, disabled states, and lifecycle language.
struct PrimaryJobToolbar: ToolbarContent {
    @ObservedObject var model: AppModel

    var body: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            if model.job.showsCancellationAuthority {
                Button(role: .destructive) {
                    model.job.cancel()
                } label: {
                    Label("Cancel Job", systemImage: "stop.fill")
                }
                .buttonStyle(AuralignPrimaryActionButtonStyle(destructive: true))
                .help("Stop the running transcription.")
                .disabled(!model.job.canRequestCancellation)
                .accessibilityIdentifier(AccessibilityIdentifier.primaryAction)
            } else {
                Button {
                    model.startJob()
                } label: {
                    Label("Start Transcription", systemImage: "play.fill")
                }
                .buttonStyle(AuralignPrimaryActionButtonStyle())
                .help(
                    model.startBlocker == nil
                        ? "Start transcription with the checked effective configuration."
                        : model.privacySafePresentationMessage(
                            model.startBlocker?.message
                                ?? "Resolve the requirement shown on New Job before starting."
                        )
                )
                .disabled(model.startBlocker != nil || model.isPresentationFixture)
                .accessibilityIdentifier(AccessibilityIdentifier.primaryAction)
            }
        }
    }

    var compactStatusLabel: String {
        switch model.job.phase {
        case .running: return "Running"
        case .resultPendingExit: return "Finalizing"
        case .stopping: return "Stopping"
        case .completed: return "Completed"
        case .failed: return "Failed"
        case .cancelled: return "Cancelled"
        case .idle:
            if model.selectedSection == .readiness {
                return model.readinessLevel.label
            }
            switch model.startBlocker {
            case .engineConnecting: return "Connecting"
            case .resourceFailure, .noEngine, .configurationUnavailable:
                return "Unavailable"
            case .inputInspectionPending, .configurationValidating,
                    .readinessChecking:
                return "Checking"
            case .invalidInput, .invalidOutputRoot,
                    .plannedJobDirectoryExists, .invalidConfiguration,
                    .readinessBlocked:
                return "Needs attention"
            case .noInput, .noOutputRoot:
                return "Setup required"
            case .readinessUnknown:
                return "Not checked"
            case .readinessStale:
                return "Checks stale"
            case .jobRunning:
                return "Running"
            case nil:
                return "Ready"
            }
        }
    }

    var statusSymbol: String {
        switch model.job.phase {
        case .running: return "waveform"
        case .resultPendingExit: return "hourglass"
        case .stopping: return "stop.circle"
        case .completed: return "checkmark.circle.fill"
        case .failed: return "xmark.octagon.fill"
        case .cancelled: return "slash.circle"
        case .idle:
            if model.selectedSection == .readiness {
                return model.readinessLevel.symbol
            }
            switch model.startBlocker {
            case .engineConnecting, .inputInspectionPending,
                    .configurationValidating, .readinessChecking:
                return "clock"
            case .resourceFailure, .noEngine, .configurationUnavailable:
                return "bolt.slash"
            case .invalidInput, .invalidOutputRoot,
                    .plannedJobDirectoryExists, .invalidConfiguration,
                    .readinessBlocked:
                return "exclamationmark.circle.fill"
            case .noInput:
                return "doc.badge.plus"
            case .noOutputRoot:
                return "folder.badge.plus"
            case .readinessUnknown:
                return "questionmark.circle"
            case .readinessStale:
                return "arrow.clockwise.circle"
            case .jobRunning:
                return "waveform"
            case nil:
                return "checkmark.circle.fill"
            }
        }
    }

    var statusDestination: WorkspaceSection {
        switch model.job.phase {
        case .running, .resultPendingExit, .stopping, .failed, .cancelled:
            return .processing
        case .completed:
            return .results
        case .idle:
            if model.selectedSection == .readiness {
                return .readiness
            }
            switch model.startBlocker {
            case .engineConnecting, .resourceFailure, .noEngine,
                    .configurationUnavailable, .readinessChecking,
                    .readinessUnknown, .readinessStale, .readinessBlocked:
                return .readiness
            default:
                return .newJob
            }
        }
    }

    /// Mirrors the tone mapping `ReadinessView.decisionTone` applies to
    /// `ReadinessLevel`, so the sidebar footer's status color agrees with the
    /// Runtime & Models workspace rather than inventing a second scale.
    var statusTone: AuralignStatusTone {
        switch model.job.phase {
        case .running, .resultPendingExit: return .active
        case .stopping: return .warning
        case .completed: return .success
        case .failed: return .failure
        case .cancelled: return .neutral
        case .idle:
            if model.selectedSection == .readiness {
                switch model.readinessLevel {
                case .unknown, .unavailable: return .neutral
                case .checking: return .active
                case .stale, .warnings: return .warning
                case .ready: return .success
                case .blocked: return .failure
                }
            }
            switch model.startBlocker {
            case .engineConnecting, .inputInspectionPending,
                    .configurationValidating, .readinessChecking, .jobRunning:
                return .active
            case .resourceFailure, .noEngine, .configurationUnavailable:
                return .failure
            case .invalidInput, .invalidOutputRoot,
                    .plannedJobDirectoryExists, .invalidConfiguration,
                    .readinessBlocked, .noInput, .noOutputRoot, .readinessStale:
                return .warning
            case .readinessUnknown:
                return .neutral
            case nil:
                return .success
            }
        }
    }

    var primaryActionStatus: String {
        switch model.job.phase {
        case .running:
            return "Transcription owns the local engine process."
        case .resultPendingExit:
            return "Finalizing engine process before publishing Results."
        case .stopping:
            return "Stopping and reaping the engine process."
        case .completed:
            return "Completed successfully."
        case .failed:
            return "The last job failed."
        case .cancelled:
            return "The last job was cancelled."
        case .idle:
            if model.selectedSection == .readiness {
                return model.readinessDecision.headline
            }
            #if DEBUG
            if model.isPresentationFixture, model.startBlocker == nil {
                return "Synthetic checked state; runtime Start is disabled."
            }
            #endif
            return model.startBlocker.map {
                model.privacySafePresentationMessage($0.message)
            }
                ?? "Ready to start with the checked effective configuration."
        }
    }
}

/// A compact global status affordance anchored to the navigation edge.
///
/// Keeping it out of the toolbar avoids presenting passive text with the
/// search-field-like Liquid Glass treatment used for toolbar controls. The
/// short visible label is backed by the complete, truthful explanation in
/// accessibility and Help, and selecting it opens the relevant workspace.
struct GlobalStatusFooter: View {
    @ObservedObject var model: AppModel

    private var presentation: PrimaryJobToolbar {
        PrimaryJobToolbar(model: model)
    }

    var body: some View {
        Button {
            model.selectedSection = presentation.statusDestination
        } label: {
            HStack(spacing: AuralignVisualSystem.spacingS) {
                AuralignStatusBadge(
                    text: presentation.compactStatusLabel,
                    tone: presentation.statusTone,
                    symbol: presentation.statusSymbol
                )
                Spacer(minLength: AuralignVisualSystem.spacingXS)
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, AuralignVisualSystem.spacingM)
            .frame(height: 38)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(presentation.primaryActionStatus)
        .accessibilityLabel(
            "\(presentation.compactStatusLabel). "
                + presentation.primaryActionStatus
        )
        .accessibilityHint("Open the relevant Auralign workspace.")
        .accessibilityIdentifier(
            AccessibilityIdentifier.primaryActionStatus
        )
    }
}

struct SidebarView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            brand

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    navigationGroup("FLOW", index: "01") {
                        navigationButton(
                            "New Transcription",
                            symbol: "waveform.badge.plus",
                            destination: .newJob,
                            identifier: AccessibilityIdentifier.navigation("new-job")
                        )
                        navigationButton(
                            "Activity",
                            symbol: processingSymbol,
                            destination: .processing,
                            identifier: AccessibilityIdentifier.navigation("activity")
                        )
                        navigationButton(
                            "Results",
                            symbol: resultsSymbol,
                            destination: .results,
                            identifier: AccessibilityIdentifier.navigation("results")
                        )
                    }

                    navigationGroup("DESIGN THE RUN", index: "02") {
                        navigationButton(
                            "Signal Setup",
                            symbol: "point.3.connected.trianglepath.dotted",
                            destination: .pipeline(.sourceAudio),
                            count: modifiedCountAll,
                            identifier: AccessibilityIdentifier.navigation(
                                "pipeline.sourceAudio"
                            )
                        )
                    }

                    navigationGroup("SYSTEM", index: "03") {
                        navigationButton(
                            "Parameter Library",
                            symbol: "slider.horizontal.3",
                            destination: .allParameters,
                            identifier: AccessibilityIdentifier.navigation(
                                "parameter-library"
                            )
                        )
                        navigationButton(
                            "Runtime & Models",
                            symbol: model.readinessLevel.symbol,
                            destination: .readiness,
                            status: model.readinessLevel.label,
                            identifier: AccessibilityIdentifier.navigation(
                                "runtime-models"
                            )
                        )
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 14)
            }

            GlobalStatusFooter(model: model)
                .padding(10)
                .background(.ultraThinMaterial)
        }
        .background(sidebarBackground)
    }

    private enum SidebarDestination: Hashable {
        case newJob
        case processing
        case results
        case pipeline(PipelineNavigationGroup)
        case allParameters
        case readiness
    }

    private var brand: some View {
        HStack(spacing: 11) {
            AuralignSidebarBrandMark()

            VStack(alignment: .leading, spacing: 2) {
                // The product lockup is fixed brand artwork expressed as live
                // text, not localized interface copy. Keep it stable under
                // NSDoubleLocalizedStrings layout-stress captures.
                Text(verbatim: "AURALIGN")
                    .font(AuralignType.brandWordmark)
                    .tracking(1.1)
                Text(verbatim: "NEURAL AUDIO\nINTELLIGENCE")
                    .font(AuralignType.brandDescriptor)
                    .tracking(1.2)
                    .lineSpacing(0)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.top, 18)
        .padding(.bottom, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Auralign, neural audio intelligence")
    }

    private var sidebarBackground: some View {
        ZStack {
            if colorScheme == .dark {
                Color(red: 0.048, green: 0.044, blue: 0.075)
            } else {
                Color(red: 0.965, green: 0.975, blue: 0.988)
            }
            LinearGradient(
                colors: [
                    AuralignSpectrumStop.source.color.opacity(
                        colorScheme == .dark ? 0.11 : 0.08
                    ),
                    Color.clear,
                    AuralignSpectrumStop.alignment.color.opacity(
                        colorScheme == .dark ? 0.13 : 0.09
                    )
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
    }

    private func navigationGroup<Content: View>(
        _ title: String,
        index: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 7) {
                Text(index)
                    .font(AuralignType.sectionIndex)
                    .foregroundStyle(AuralignSpectrumStop.alignment.color)
                Text(title)
                    .font(AuralignType.badge.weight(.bold))
                    .tracking(1.25)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 9)
            .accessibilityHidden(true)

            content()
        }
    }

    private func navigationButton(
        _ title: String,
        symbol: String,
        destination: SidebarDestination,
        count: Int = 0,
        status: String? = nil,
        identifier: String
    ) -> some View {
        let isSelected = selectedDestination == destination
        return Button {
            navigate(to: destination)
        } label: {
            HStack(spacing: 10) {
                Image(systemName: symbol)
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: 18)
                    .foregroundStyle(
                        isSelected
                            ? AuralignSpectrumStop.alignment.color
                            : Color.secondary
                    )
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .font(AuralignType.navigation(selected: isSelected))
                        .foregroundStyle(Color.primary)
                        .lineLimit(1)
                        .layoutPriority(1)
                    if let status {
                        Text(status)
                            .font(AuralignType.badge)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 6)
                if count > 0 {
                    Text("\(count)")
                        .font(AuralignType.navigationCount)
                        .foregroundStyle(AuralignSpectrumStop.delivery.color)
                }
                if isSelected {
                    Circle()
                        .fill(AuralignSpectrumStop.source.color)
                        .frame(width: 5, height: 5)
                        .accessibilityHidden(true)
                }
            }
            .padding(.horizontal, 10)
            .frame(minHeight: status == nil ? 34 : 42)
            .contentShape(RoundedRectangle(cornerRadius: 9))
            .background {
                RoundedRectangle(cornerRadius: 9)
                    .fill(
                        isSelected
                            ? AuralignSpectrumStop.alignment.color.opacity(0.17)
                            : Color.clear
                    )
            }
            .overlay(alignment: .leading) {
                if isSelected {
                    Capsule()
                        .fill(AuralignSpectrumStop.alignment.color)
                        .frame(width: 2.5, height: 18)
                        .padding(.leading, 1)
                        .accessibilityHidden(true)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityValue(
            status
                ?? (count == 0
                    ? (isSelected ? "Selected" : "")
                    : "\(count) modified parameters")
        )
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
        .accessibilityIdentifier(identifier)
    }

    private var selectedDestination: SidebarDestination? {
        switch model.selectedSection {
        case .newJob, .none:
            return .newJob
        case .processing:
            return .processing
        case .results:
            return .results
        case .allParameters:
            return .allParameters
        case .readiness:
            return .readiness
        case .configuration(let sectionID):
            return PipelineNavigationGroup.containing(sectionID: sectionID) == nil
                ? nil
                : .pipeline(.sourceAudio)
        }
    }

    @MainActor
    private func navigate(to destination: SidebarDestination) {
        guard selectedDestination != destination else { return }
        switch destination {
        case .newJob:
            model.selectedSection = .newJob
        case .processing:
            model.selectedSection = .processing
        case .results:
            model.selectedSection = .results
        case .allParameters:
            model.selectedSection = .allParameters
        case .readiness:
            model.selectedSection = .readiness
        case .pipeline(let group):
            if let sectionID = group.firstAvailableSection(in: model.catalog) {
                model.selectedSection = .configuration(sectionID)
            }
        }
    }

    private var modifiedCountAll: Int {
        PipelineNavigationGroup.allCases
            .flatMap(\.sectionIDs)
            .flatMap(model.catalog.entries(inSection:))
            .filter { model.config.isModified($0.key) }
            .count
    }

    private var processingSymbol: String {
        model.job.isRunning ? "gearshape.arrow.trianglehead.2.clockwise.rotate.90" : "gearshape"
    }

    private var resultsSymbol: String {
        if case .completed = model.job.phase { return "doc.text.fill" }
        return "doc.text"
    }
}
