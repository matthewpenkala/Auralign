// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import SwiftUI

/// Live job progress: real pipeline stages from bridge events, elapsed time,
/// warnings, and copyable technical diagnostics. Progress is stage-based and
/// honest — no synthesized percentages inside stages.
struct ProcessingView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.auralignReduceMotion) private var reduceMotion
    @State private var showTechnicalDetails = false
    @FocusState private var partialRevealFocused: Bool
    @AccessibilityFocusState private var partialRevealAccessibilityFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                statusHeader
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier(AccessibilityIdentifier.activitySummary)
                if !model.job.stages.isEmpty {
                    // The same canvas the user met on New Transcription, now
                    // carrying live engine stage state. Keeping it structurally
                    // identical is what makes the run legible as one journey.
                    AlignmentCanvasView(
                        projection: AlignmentCanvasProjection.live(model: model),
                        sourceTitle: model.inputURL?.lastPathComponent
                    )
                    stageList
                }
                if model.job.runConfiguration != nil {
                    configSummary
                }
                if !model.job.warnings.isEmpty {
                    warningsBox
                }
                failureBox
                technicalDetails
            }
            .padding(AuralignVisualSystem.spacingXL)
            .frame(maxWidth: AuralignBreakpoint.workbench, alignment: .leading)
        }
        .navigationTitle("Processing")
        .alert(
            "File action not performed",
            isPresented: Binding(
                get: { model.job.artifactActionError != nil },
                set: { presented in
                    if !presented {
                        model.job.clearArtifactActionError()
                        restorePartialRevealFocus()
                    }
                }
            )
        ) {
            Button("OK") {
                model.job.clearArtifactActionError()
                restorePartialRevealFocus()
            }
        } message: {
            Text(
                model.job.artifactActionError
                    ?? "The partial output could not be revalidated."
            )
        }
    }

    // MARK: - Header

    /// The active stage's real title and real elapsed time, promoted into the
    /// workspace header slot instead of a generic restated "Processing" title
    /// — the toolbar already carries that text via `.navigationTitle`.
    private var statusHeader: some View {
        AuralignWorkspaceHeader(
            symbol: headerSymbol,
            accessibilityHeading: headerAccessibilityHeading
        ) {
            headerContext
        } accessory: {
            headerAccessory
        }
    }

    private var headerSymbol: String {
        switch model.job.phase {
        case .idle: return "moon.zzz"
        case .running: return "waveform"
        case .resultPendingExit: return "hourglass"
        case .stopping: return "stop.circle"
        case .completed: return "checkmark.circle.fill"
        case .failed: return "xmark.octagon.fill"
        case .cancelled: return "stop.circle.fill"
        }
    }

    private var headerAccessibilityHeading: String {
        switch model.job.phase {
        case .idle: return "No job running"
        case .running: return "Processing \(activeStageTitle)"
        case .resultPendingExit: return "Finalizing engine process"
        case .stopping: return "Stopping job"
        case .completed: return "Job completed"
        case .failed: return "Job failed"
        case .cancelled: return "Job cancelled"
        }
    }

    @ViewBuilder
    private var headerContext: some View {
        switch model.job.phase {
        case .idle:
            VStack(alignment: .leading, spacing: 2) {
                Text("No job running").font(AuralignType.pageTitle)
                Text("Start a transcription from New Transcription.")
                    .font(AuralignType.evidence)
                    .foregroundStyle(.secondary)
            }
        case .running:
            HStack(spacing: AuralignVisualSystem.spacingM) {
                activityIndicator()
                VStack(alignment: .leading, spacing: 2) {
                    Text(activeStageTitle).font(AuralignType.pageTitle)
                    elapsedText
                }
            }
        case .resultPendingExit:
            HStack(spacing: AuralignVisualSystem.spacingM) {
                activityIndicator(symbol: "hourglass")
                VStack(alignment: .leading, spacing: 2) {
                    Text("Finalizing engine process…")
                        .font(AuralignType.pageTitle)
                    Text(
                        "The engine reported a provisional result. Auralign is "
                            + "waiting for a successful process exit and reaping "
                            + "before publishing Results."
                    )
                    .font(AuralignType.evidence)
                    .foregroundStyle(.secondary)
                }
            }
        case .stopping:
            HStack(spacing: AuralignVisualSystem.spacingM) {
                activityIndicator(symbol: "stop.circle")
                VStack(alignment: .leading, spacing: 2) {
                    Text("Stopping…").font(AuralignType.pageTitle)
                    Text(
                        "Asked the engine to stop. If it does not exit within 20 seconds "
                            + "it and its child processes will be force-terminated; "
                            + "partial artifacts may remain."
                    )
                    .font(AuralignType.evidence)
                    .foregroundStyle(.secondary)
                }
            }
        case .completed(let result):
            HStack(spacing: AuralignVisualSystem.spacingM) {
                statusIcon("checkmark.circle.fill", tone: .success)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Completed").font(AuralignType.pageTitle)
                    Text(
                        "\(result.speakerTurns) speaker turns · \(result.words) words"
                            + (elapsedString.map { " · \($0)" } ?? "")
                    )
                    .font(AuralignType.numeric)
                    .foregroundStyle(.secondary)
                }
            }
        case .failed:
            HStack(spacing: AuralignVisualSystem.spacingM) {
                statusIcon("xmark.octagon.fill", tone: .failure)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Job failed").font(AuralignType.pageTitle)
                    elapsedText
                }
            }
        case .cancelled(let directory, let artifacts):
            HStack(spacing: AuralignVisualSystem.spacingM) {
                statusIcon("stop.circle.fill", tone: .warning)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Cancelled").font(AuralignType.pageTitle)
                    Text(cancelledAvailabilityText(directory: directory, artifacts: artifacts))
                        .font(AuralignType.evidence)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func statusIcon(_ symbol: String, tone: AuralignStatusTone) -> some View {
        Image(systemName: symbol)
            .font(.title2)
            .foregroundStyle(tone.color)
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var headerAccessory: some View {
        switch model.job.phase {
        case .completed:
            Button("View Results") { model.selectedSection = .results }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier(
                    AccessibilityIdentifier.activityViewResults
                )
        case .cancelled(let directory, _):
            if directory != nil {
                Button("Reveal Partial Output in Finder") {
                    model.job.revealOutputDirectory()
                }
                .buttonStyle(.bordered)
                .focused($partialRevealFocused)
                .accessibilityFocused($partialRevealAccessibilityFocused)
                .accessibilityIdentifier(
                    AccessibilityIdentifier.activityPartialReveal
                )
                .disabled(model.isPresentationFixture)
            }
        default:
            EmptyView()
        }
    }

    @ViewBuilder
    private func activityIndicator(symbol: String = "clock") -> some View {
        if reduceMotion {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
        } else {
            ProgressView()
                .controlSize(.regular)
                .accessibilityHidden(true)
        }
    }

    private func cancelledAvailabilityText(
        directory: URL?,
        artifacts: [JobArtifact]
    ) -> String {
        if !artifacts.isEmpty {
            return "The job stopped before completion; "
                + "\(artifacts.count) verified partial artifact"
                + "\(artifacts.count == 1 ? " remains" : "s remain")."
        }
        if directory != nil {
            return "The job stopped before completion; its verified folder contains "
                + "no regular-file artifacts."
        }
        return "The job stopped before completion without a verified output folder."
    }

    private var activeStageTitle: String {
        model.job.stages.first { $0.status == .active }?.title ?? "Working…"
    }

    private var elapsedString: String? {
        guard let elapsed = model.job.elapsed else { return nil }
        guard elapsed.isFinite, elapsed >= 0, elapsed <= 315_576_000 else {
            return "Elapsed time unavailable"
        }
        let minutes = floor(elapsed / 60)
        let seconds = floor(elapsed.truncatingRemainder(dividingBy: 60))
        return String(format: "%.0f:%02.0f elapsed", minutes, seconds)
    }

    @ViewBuilder
    private var elapsedText: some View {
        if model.job.isRunning {
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                elapsedLabel
            }
        } else {
            elapsedLabel
        }
    }

    @ViewBuilder
    private var elapsedLabel: some View {
        if let text = elapsedString {
            Text(text)
                .font(AuralignType.numeric)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Stages

    /// Compressor's job-list pattern: a colored status word in its own
    /// column (folding any engine sub-phase detail into that word rather than
    /// a separate widget), real elapsed seconds right-aligned only when the
    /// engine actually reported `done(seconds:)`. No numeric progress or ETA
    /// is ever synthesized for the indeterminate `.active` state.
    private var stageList: some View {
        AuralignSection(
            title: "Pipeline Stages",
            subtitle:
                "Stages are listed in engine order. Elapsed values appear only "
                + "when the engine reports them.",
            headerIdentifier: AccessibilityIdentifier.activityStages
        ) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                ForEach(model.job.stages) { stage in
                    responsiveStageRow(stage)
                        .padding(.vertical, AuralignVisualSystem.spacingXS)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(
                            "\(stage.title). \(stageAccessibilityValue(stage))"
                        )
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.activityStage(stage.id)
                        )
                    if stage.id != model.job.stages.last?.id {
                        Divider().opacity(0.35)
                    }
                }
            }
        }
    }

    private func responsiveStageRow(_ stage: PipelineStage) -> some View {
        let tone = stageTone(stage.status)
        return ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: AuralignVisualSystem.spacingM) {
                stageIcon(stage.status)
                    .frame(width: 18)
                Text(stage.title)
                    .font(AuralignType.rowTitle)
                    .fontWeight(stageIsActive(stage.status) ? .semibold : .regular)
                    .frame(minWidth: 150, idealWidth: 190, alignment: .leading)
                Spacer(minLength: AuralignVisualSystem.spacingS)
                Text(stageStatusWord(stage))
                    .font(AuralignType.metadataStrong)
                    .foregroundStyle(tone.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 260, alignment: .trailing)
                if let elapsed = stageElapsedText(stage.status) {
                    Text(elapsed)
                        .font(AuralignType.numericSmall)
                        .foregroundStyle(.secondary)
                        .frame(minWidth: 48, alignment: .trailing)
                }
            }

            HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
                stageIcon(stage.status)
                    .frame(width: 18)
                VStack(alignment: .leading, spacing: 2) {
                    Text(stage.title)
                        .font(AuralignType.rowTitle)
                        .fontWeight(stageIsActive(stage.status) ? .semibold : .regular)
                    HStack(spacing: AuralignVisualSystem.spacingS) {
                        Text(stageStatusWord(stage))
                            .font(AuralignType.metadataStrong)
                            .foregroundStyle(tone.color)
                            .fixedSize(horizontal: false, vertical: true)
                        if let elapsed = stageElapsedText(stage.status) {
                            Text(elapsed)
                                .font(AuralignType.numericSmall)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Spacer(minLength: 0)
            }
        }
    }

    @ViewBuilder
    private func stageIcon(_ status: PipelineStage.Status) -> some View {
        switch status {
        case .pending:
            Image(systemName: "circle")
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
        case .active:
            if reduceMotion {
                Image(systemName: "clock")
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            } else {
                ProgressView()
                    .controlSize(.small)
                    .accessibilityHidden(true)
            }
        case .done:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(AuralignStatusTone.success.color)
                .accessibilityHidden(true)
        case .skipped:
            Image(systemName: "minus.circle")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
        }
    }

    private func stageTone(_ status: PipelineStage.Status) -> AuralignStatusTone {
        switch status {
        case .pending: return .neutral
        case .active: return .active
        case .done: return .success
        case .skipped: return .neutral
        }
    }

    /// Folds any real engine sub-phase detail into the status word itself
    /// ("Processing: transcoding…") rather than a separate line, per the
    /// Compressor reference pattern — the word is always literal text, never
    /// a synthesized fraction.
    private func stageStatusWord(_ stage: PipelineStage) -> String {
        let detail = model
            .privacySafePresentationMessage(stage.detail)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        switch stage.status {
        case .pending:
            return "Waiting"
        case .active:
            return detail.isEmpty ? "Processing" : "Processing: \(detail)"
        case .done:
            return detail.isEmpty ? "Successful" : "Successful · \(detail)"
        case .skipped(let reason):
            let safeReason = model.privacySafePresentationMessage(reason)
            return safeReason.isEmpty ? "Skipped" : "Skipped · \(safeReason)"
        }
    }

    private func stageElapsedText(_ status: PipelineStage.Status) -> String? {
        guard case .done(let seconds) = status, let seconds else { return nil }
        return JobController.timeText(seconds)
    }

    private func stageIsActive(_ status: PipelineStage.Status) -> Bool {
        if case .active = status { return true }
        return false
    }

    private func stageAccessibilityValue(_ stage: PipelineStage) -> String {
        var value = stageStatusWord(stage)
        if let elapsed = stageElapsedText(stage.status) {
            value += ", elapsed \(elapsed)"
        }
        return value
    }

    // MARK: - Config summary and warnings

    private var configSummary: some View {
        AuralignSection(title: "Run Configuration") {
            if let config = model.job.runConfiguration {
                LazyVGrid(
                    columns: [
                        GridItem(
                            .adaptive(minimum: 112, maximum: 180),
                            alignment: .leading
                        )
                    ],
                    alignment: .leading,
                    spacing: AuralignVisualSystem.spacingM
                ) {
                    chip("Preset", config.preset)
                    chip("ASR", config.asrModel)
                    chip("Backend", config.asrBackend)
                    chip("Diarization", config.diarization)
                    chip("Alignment", config.alignment)
                    if config.offline {
                        chip("Offline", "yes")
                    }
                }
            }
        }
        .accessibilityIdentifier(AccessibilityIdentifier.activityConfiguration)
    }

    private func chip(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(AuralignType.metadata).foregroundStyle(.tertiary)
            Text(value).font(AuralignType.rowTitle)
        }
    }

    private var warningsBox: some View {
        AuralignSection(title: "Warnings") {
            AuralignCausalityBanner(
                headline:
                    "The engine reported \(model.job.warnings.count) "
                    + (model.job.warnings.count == 1 ? "warning" : "warnings")
                    + " during this run.",
                detail: model.job.warnings
                    .map(model.privacySafePresentationMessage)
                    .joined(separator: "\n"),
                tone: .warning
            )
        }
        .accessibilityIdentifier(AccessibilityIdentifier.activityWarnings)
    }

    @ViewBuilder
    private var failureBox: some View {
        if case .failed(let failure) = model.job.phase {
            AuralignSection(title: "Failure") {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                    AuralignCausalityBanner(
                        headline: model.privacySafePresentationMessage(failure.message),
                        detail: failure.remediation.map {
                            model.privacySafePresentationMessage($0)
                        },
                        tone: .failure
                    )
                    .textSelection(.enabled)
                    if failure.outputDirectory != nil {
                        Button("Reveal Partial Output in Finder") {
                            model.job.revealOutputDirectory()
                        }
                        .disabled(model.isPresentationFixture)
                        .focused($partialRevealFocused)
                        .accessibilityFocused($partialRevealAccessibilityFocused)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.activityPartialReveal
                        )
                    }
                }
            }
            .accessibilityIdentifier(AccessibilityIdentifier.activityFailure)
        }
    }

    // MARK: - Technical details

    @ViewBuilder
    private var technicalDetails: some View {
        if model.job.phase != .idle {
            // QLab's "Logs" tab pattern: raw technical detail belongs in one
            // dedicated, clearly labeled area rather than scattered through
            // the main reading path above. Monospace here is expected.
            DisclosureGroup("Technical Details", isExpanded: $showTechnicalDetails) {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                    if case .failed(let failure) = model.job.phase {
                        Text("Failure type")
                            .font(AuralignType.metadataStrong)
                            .foregroundStyle(.secondary)
                        Text(failure.errorType)
                            .font(AuralignType.technical)
                            .textSelection(.enabled)
                    }
                    if !model.job.recentEvents.isEmpty {
                        Text("Bridge events (redacted)")
                            .font(AuralignType.metadataStrong)
                            .foregroundStyle(.secondary)
                        ScrollView {
                            Text(model.job.recentEvents.suffix(60).joined(separator: "\n"))
                                .font(AuralignType.technical)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(height: 140)
                        .background(AuralignSurface.inset)
                        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.control))
                    }
                    let diagnostics = model.job.diagnosticsTail
                    if !diagnostics.isEmpty {
                        Text("Engine stderr (local view only)")
                            .font(AuralignType.metadataStrong)
                            .foregroundStyle(.secondary)
                        ScrollView {
                            Text(diagnostics)
                                .font(AuralignType.technical)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(height: 120)
                        .background(AuralignSurface.inset)
                        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.control))
                    }
                    Button("Copy Diagnostics") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(
                            model.job.copyableDiagnostics,
                            forType: .string
                        )
                    }
                    .controlSize(.small)
                    Text("Copied diagnostics are redacted; raw stderr is omitted.")
                        .font(AuralignType.metadata)
                        .foregroundStyle(.secondary)
                }
                .padding(.top, AuralignVisualSystem.spacingS)
            }
            .font(AuralignType.evidence)
            .accessibilityIdentifier(
                AccessibilityIdentifier.activityTechnicalDetails
            )
        }
    }

    private func restorePartialRevealFocus() {
        Task { @MainActor in
            await Task.yield()
            partialRevealFocused = true
            partialRevealAccessibilityFocused = true
        }
    }
}
