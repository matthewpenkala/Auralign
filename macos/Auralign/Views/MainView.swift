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
                .navigationSplitViewColumnWidth(min: 200, ideal: 225, max: 300)
        } detail: {
            WorkspaceDetailView()
                .frame(minWidth: 560)
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
        switch model.selectedSection {
        case .newJob, .none:
            NewJobView()
        case .configuration(let sectionID):
            if let group = PipelineNavigationGroup.containing(sectionID: sectionID) {
                PipelineConfigurationView(group: group, sectionID: sectionID)
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

/// One primary job authority shared by the shipping shell and Debug design
/// directions. Prototype captures therefore exercise production Start/Cancel
/// ownership, disabled states, and lifecycle language.
struct PrimaryJobToolbar: ToolbarContent {
    @ObservedObject var model: AppModel

    var body: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            if model.job.showsCancellationAuthority {
                Button {
                    model.job.cancel()
                } label: {
                    Label("Cancel Job", systemImage: "stop.fill")
                }
                .help("Stop the running transcription.")
                .disabled(!model.job.canRequestCancellation)
                .accessibilityIdentifier(AccessibilityIdentifier.primaryAction)
            } else {
                Button {
                    model.startJob()
                } label: {
                    Label("Start Transcription", systemImage: "play.fill")
                }
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
            HStack(spacing: 8) {
                Image(systemName: presentation.statusSymbol)
                    .font(.caption.weight(.semibold))
                    .frame(width: 16)
                    .accessibilityHidden(true)
                Text(presentation.compactStatusLabel)
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
                Spacer(minLength: 4)
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
            }
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
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

    var body: some View {
        VStack(spacing: 0) {
            List(selection: destinationBinding) {
                Section("Workflow") {
                    Label("New Transcription", systemImage: "plus.square.on.square")
                        .tag(SidebarDestination.newJob)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.navigation("new-job")
                        )
                    Label("Activity", systemImage: processingSymbol)
                        .tag(SidebarDestination.processing)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.navigation("activity")
                        )
                    Label("Results", systemImage: resultsSymbol)
                        .tag(SidebarDestination.results)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.navigation("results")
                        )
                }
                Section("Configuration") {
                    ForEach(PipelineNavigationGroup.allCases) { group in
                        HStack {
                            Label(group.title, systemImage: group.symbol)
                            Spacer()
                            if modifiedCount(group) > 0 {
                                Text("\(modifiedCount(group))")
                                    .font(.caption2.weight(.semibold))
                                    .foregroundStyle(Color.accentColor)
                                    .monospacedDigit()
                            }
                        }
                        .tag(SidebarDestination.pipeline(group))
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(group.title)
                        .accessibilityValue(
                            modifiedCount(group) == 0
                                ? "No modified parameters"
                                : "\(modifiedCount(group)) modified parameters"
                        )
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.navigation(
                                "pipeline.\(group.rawValue)"
                            )
                        )
                    }
                }
                Section("Expert") {
                    Label("Parameter Library", systemImage: "list.bullet.rectangle")
                        .tag(SidebarDestination.allParameters)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.navigation(
                                "parameter-library"
                            )
                        )
                }
                Section("System") {
                    HStack {
                        Label("Runtime & Models", systemImage: "cpu")
                        Spacer()
                        Image(systemName: model.readinessLevel.symbol)
                            .foregroundStyle(model.readinessLevel.color)
                            .font(.caption)
                    }
                    .tag(SidebarDestination.readiness)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Runtime & Models")
                    .accessibilityValue(model.readinessLevel.label)
                    .accessibilityIdentifier(
                        AccessibilityIdentifier.navigation("runtime-models")
                    )
                }
            }
            .listStyle(.sidebar)
            Divider()
            GlobalStatusFooter(model: model)
        }
    }

    private enum SidebarDestination: Hashable {
        case newJob
        case processing
        case results
        case pipeline(PipelineNavigationGroup)
        case allParameters
        case readiness
    }

    private var destinationBinding: Binding<SidebarDestination?> {
        Binding(
            get: { selectedDestination },
            set: { destination in
                guard let destination else { return }
                Task { @MainActor in
                    // AppKit can invoke List's selection setter while SwiftUI
                    // is still reconciling the sidebar. Route on the next task
                    // turn so the ObservableObject is never published from
                    // inside that view update.
                    await Task.yield()
                    navigate(to: destination)
                }
            }
        )
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
            return PipelineNavigationGroup.containing(sectionID: sectionID)
                .map(SidebarDestination.pipeline)
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

    private func modifiedCount(_ group: PipelineNavigationGroup) -> Int {
        group.sectionIDs
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
