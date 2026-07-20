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
    @State private var now = Date()
    @FocusState private var partialRevealFocused: Bool
    @AccessibilityFocusState private var partialRevealAccessibilityFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                statusHeader
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier(AccessibilityIdentifier.activitySummary)
                if !model.job.stages.isEmpty {
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
            .padding(20)
            .frame(maxWidth: 720, alignment: .leading)
        }
        .navigationTitle("Processing")
        .task(id: model.job.isRunning) {
            guard model.job.isRunning else { return }
            now = Date()
            while !Task.isCancelled, model.job.isRunning {
                do {
                    try await Task.sleep(nanoseconds: 1_000_000_000)
                } catch {
                    return
                }
                guard !Task.isCancelled else { return }
                now = Date()
            }
        }
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

    @ViewBuilder
    private var statusHeader: some View {
        HStack(spacing: 12) {
            switch model.job.phase {
            case .idle:
                Image(systemName: "moon.zzz")
                    .font(.title2)
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("No job running").font(.title3.weight(.semibold))
                    Text("Start a transcription from New Transcription.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            case .running:
                activityIndicator()
                VStack(alignment: .leading, spacing: 2) {
                    Text(activeStageTitle).font(.title3.weight(.semibold))
                    elapsedText
                }
            case .resultPendingExit:
                activityIndicator(symbol: "hourglass")
                VStack(alignment: .leading, spacing: 2) {
                    Text("Finalizing engine process…")
                        .font(.title3.weight(.semibold))
                    Text(
                        "The engine reported a provisional result. Auralign is "
                            + "waiting for a successful process exit and reaping "
                            + "before publishing Results."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                }
            case .stopping:
                activityIndicator(symbol: "stop.circle")
                VStack(alignment: .leading, spacing: 2) {
                    Text("Stopping…").font(.title3.weight(.semibold))
                    Text(
                        "Asked the engine to stop. If it does not exit within 20 seconds "
                            + "it and its child processes will be force-terminated; "
                            + "partial artifacts may remain."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                }
            case .completed(let result):
                Image(systemName: "checkmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.green)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Completed").font(.title3.weight(.semibold))
                    Text(
                        "\(result.speakerTurns) speaker turns · \(result.words) words"
                            + (elapsedString.map { " · \($0)" } ?? "")
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                }
                Spacer()
                Button("View Results") { model.selectedSection = .results }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier(
                        AccessibilityIdentifier.activityViewResults
                    )
            case .failed:
                Image(systemName: "xmark.octagon.fill")
                    .font(.title2)
                    .foregroundStyle(.red)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Job failed").font(.title3.weight(.semibold))
                    elapsedText
                }
            case .cancelled(let directory, let artifacts):
                Image(systemName: "stop.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Cancelled").font(.title3.weight(.semibold))
                    Text(cancelledAvailabilityText(directory: directory, artifacts: artifacts))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                if directory != nil {
                    Spacer()
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
            }
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
        _ = now
        guard elapsed.isFinite, elapsed >= 0, elapsed <= 315_576_000 else {
            return "Elapsed time unavailable"
        }
        let minutes = floor(elapsed / 60)
        let seconds = floor(elapsed.truncatingRemainder(dividingBy: 60))
        return String(format: "%.0f:%02.0f elapsed", minutes, seconds)
    }

    @ViewBuilder
    private var elapsedText: some View {
        if let text = elapsedString {
            Text(text)
                .font(.callout)
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
    }

    // MARK: - Stages

    private var stageList: some View {
        GroupBox("Pipeline stages") {
            VStack(alignment: .leading, spacing: 0) {
                Text(
                    "Stages are listed in engine order. Row spacing does not represent "
                        + "duration; elapsed values appear only when the engine reports them."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 6)
                ForEach(model.job.stages) { stage in
                    responsiveStageRow(stage)
                    .padding(.vertical, 4)
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
            .padding(4)
        }
        .accessibilityIdentifier(AccessibilityIdentifier.activityStages)
    }

    private func responsiveStageRow(_ stage: PipelineStage) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                stageIcon(stage.status)
                    .frame(width: 18)
                Text(stage.title)
                    .fontWeight(stageIsActive(stage.status) ? .semibold : .regular)
                    .frame(minWidth: 150, idealWidth: 190, alignment: .leading)
                Text(stage.detail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                stageStatus(stage.status)
            }

            HStack(alignment: .top, spacing: 10) {
                stageIcon(stage.status)
                    .frame(width: 18)
                VStack(alignment: .leading, spacing: 2) {
                    Text(stage.title)
                        .fontWeight(stageIsActive(stage.status) ? .semibold : .regular)
                    Text(stage.detail)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    stageStatus(stage.status)
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
                .foregroundStyle(.green)
                .accessibilityHidden(true)
        case .skipped:
            Image(systemName: "minus.circle")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
        }
    }

    @ViewBuilder
    private func stageStatus(_ status: PipelineStage.Status) -> some View {
        switch status {
        case .pending:
            Text("Pending")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .active:
            Text("Active")
                .font(.caption.weight(.semibold))
        case .done(let seconds):
            Text(
                seconds.map {
                    "Completed · \(JobController.timeText($0))"
                } ?? "Completed"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .monospacedDigit()
        case .skipped(let reason):
            Text("Skipped · \(reason)")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func stageIsActive(_ status: PipelineStage.Status) -> Bool {
        if case .active = status { return true }
        return false
    }

    private func stageAccessibilityValue(_ stage: PipelineStage) -> String {
        let status: String
        switch stage.status {
        case .pending:
            status = "Pending"
        case .active:
            status = "Active"
        case .done(let seconds):
            status = seconds.map {
                "Completed in \(JobController.timeText($0))"
            } ?? "Completed"
        case .skipped(let reason):
            status = "Skipped. \(reason)"
        }
        return stage.detail.isEmpty ? status : "\(status). \(stage.detail)"
    }

    // MARK: - Config summary and warnings

    private var configSummary: some View {
        GroupBox("Run configuration") {
            if let config = model.job.runConfiguration {
                LazyVGrid(
                    columns: [
                        GridItem(
                            .adaptive(minimum: 112, maximum: 180),
                            alignment: .leading
                        )
                    ],
                    alignment: .leading,
                    spacing: 10
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
                .padding(4)
            }
        }
        .accessibilityIdentifier(AccessibilityIdentifier.activityConfiguration)
    }

    private func chip(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.caption2).foregroundStyle(.tertiary)
            Text(value).font(.callout.weight(.medium))
        }
    }

    private var warningsBox: some View {
        GroupBox("Warnings") {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(
                    Array(model.job.warnings.enumerated()),
                    id: \.offset
                ) { _, warning in
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
            }
            .padding(4)
        }
        .accessibilityIdentifier(AccessibilityIdentifier.activityWarnings)
    }

    @ViewBuilder
    private var failureBox: some View {
        if case .failed(let failure) = model.job.phase {
            GroupBox("Failure") {
                VStack(alignment: .leading, spacing: 6) {
                    Text(model.privacySafePresentationMessage(failure.message))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    if let remediation = failure.remediation {
                        Label(
                            model.privacySafePresentationMessage(remediation),
                            systemImage: "lightbulb"
                        )
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
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
                .padding(4)
            }
            .accessibilityIdentifier(AccessibilityIdentifier.activityFailure)
        }
    }

    // MARK: - Technical details

    @ViewBuilder
    private var technicalDetails: some View {
        if model.job.phase != .idle {
            DisclosureGroup("Technical details", isExpanded: $showTechnicalDetails) {
                VStack(alignment: .leading, spacing: 8) {
                    if case .failed(let failure) = model.job.phase {
                        Text("Failure type")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(failure.errorType)
                            .font(.caption.monospaced())
                            .textSelection(.enabled)
                    }
                    if !model.job.recentEvents.isEmpty {
                        Text("Bridge events (redacted)")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ScrollView {
                            Text(model.job.recentEvents.suffix(60).joined(separator: "\n"))
                                .font(.caption.monospaced())
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(height: 140)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                    let diagnostics = model.job.diagnosticsTail
                    if !diagnostics.isEmpty {
                        Text("Engine stderr (local view only)")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ScrollView {
                            Text(diagnostics)
                                .font(.caption.monospaced())
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(height: 120)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 4))
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
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.top, 6)
            }
            .font(.callout)
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
