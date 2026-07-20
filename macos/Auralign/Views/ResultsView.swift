// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import SwiftUI

/// Transcript-first presentation of verified engine output.
///
/// Completion is published only after the bridge process exits successfully,
/// is reaped, and every declared artifact is reconciled through retained
/// directory authority. Native file actions return to `JobController` for
/// action-time identity checks; this view never opens a remembered path.
struct ResultsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var lastArtifactActionIndex: Int?
    @FocusState private var artifactActionFocus: Int?
    @AccessibilityFocusState private var artifactActionAccessibilityFocus: Int?

    var body: some View {
        Group {
            switch model.job.phase {
            case .completed(let result):
                completedBody(result)
            case .cancelled(let directory, let artifacts):
                partialBody(
                    state: .cancelled,
                    message: partialAvailabilityMessage(
                        directory: directory,
                        artifacts: artifacts
                    ),
                    remediation: nil,
                    technicalType: nil,
                    technicalDiagnostics: nil,
                    directory: directory,
                    artifacts: artifacts
                )
            case .failed(let failure):
                partialBody(
                    state: .failed,
                    message: model.privacySafePresentationMessage(failure.message),
                    remediation: failure.remediation.map(
                        model.privacySafePresentationMessage
                    ),
                    technicalType: failure.errorType,
                    technicalDiagnostics: failure.diagnostics,
                    directory: failure.outputDirectory,
                    artifacts: failure.partialArtifacts
                )
            case .idle:
                pendingBody(
                    title: "No results yet",
                    message: "Choose a source, check readiness, and start a transcription.",
                    symbol: "doc.text.magnifyingglass",
                    tone: .neutral
                )
            case .running:
                pendingBody(
                    title: "Results pending",
                    message: "The local engine is still processing this job. "
                        + "Activity shows the event-driven pipeline state.",
                    symbol: "gearshape.arrow.trianglehead.2.clockwise.rotate.90",
                    tone: .active
                )
            case .resultPendingExit:
                pendingBody(
                    title: "Finalizing result",
                    message: "A provisional result arrived. Auralign will publish it only "
                        + "after the engine exits successfully and is reaped.",
                    symbol: "hourglass",
                    tone: .active
                )
            case .stopping:
                pendingBody(
                    title: "Stopping job",
                    message: "Auralign is terminating and reaping the engine process. "
                        + "Verified partial output will appear only after exit.",
                    symbol: "stop.circle",
                    tone: .warning
                )
            }
        }
        .navigationTitle("Results")
        .alert(
            "File action not performed",
            isPresented: Binding(
                get: { model.job.artifactActionError != nil },
                set: { presented in
                    if !presented {
                        model.job.clearArtifactActionError()
                        restoreArtifactActionFocus()
                    }
                }
            )
        ) {
            Button("OK") {
                model.job.clearArtifactActionError()
                restoreArtifactActionFocus()
            }
        } message: {
            Text(
                model.job.artifactActionError
                    ?? "The selected output could not be revalidated."
            )
        }
        .accessibilityIdentifier(AccessibilityIdentifier.resultsRoot)
    }

    // MARK: - Incomplete lifecycle states

    private func pendingBody(
        title: String,
        message: String,
        symbol: String,
        tone: AuralignStatusTone
    ) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
            AuralignPageHeader(
                title: "Results",
                subtitle: "Verified local output from the current Auralign job.",
                symbol: "doc.text"
            )
            AuralignStatusBanner(
                title: title,
                message: message,
                tone: tone,
                symbol: symbol,
                actionTitle: model.job.isRunning ? "View Activity" : nil,
                actionIdentifier: model.job.isRunning
                    ? AccessibilityIdentifier.resultsViewActivity
                    : nil,
                action: model.job.isRunning
                    ? { model.selectedSection = .processing }
                    : nil
            )
            Spacer()
        }
        .padding(AuralignVisualSystem.spacingXL)
        .frame(
            maxWidth: AuralignVisualSystem.readableContentWidth,
            maxHeight: .infinity,
            alignment: .topLeading
        )
    }

    // MARK: - Completed

    private func completedBody(_ result: JobResult) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                completedHeader(result)
                if let explanation = model.resultDrift.explanation {
                    AuralignStatusBanner(
                        title: "Results are from an earlier job state",
                        message: explanation,
                        tone: .warning,
                        symbol: "clock.arrow.circlepath"
                    )
                    .accessibilityIdentifier(AccessibilityIdentifier.resultsStale)
                }
                if !result.warnings.isEmpty {
                    runWarnings(result.warnings)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: AuralignVisualSystem.spacingXL) {
                        transcriptSurface(result.transcriptPreview)
                            .frame(minWidth: 440, maxWidth: .infinity)
                        resultInspector(result)
                            .frame(width: AuralignVisualSystem.inspectorWidth)
                    }
                    VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                        transcriptSurface(result.transcriptPreview)
                        DisclosureGroup("Run details and artifacts") {
                            resultInspector(result)
                                .padding(.top, AuralignVisualSystem.spacingM)
                        }
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.resultsCompactDetails
                        )
                    }
                }
            }
            .padding(AuralignVisualSystem.spacingXL)
            .frame(maxWidth: 1_180, alignment: .leading)
        }
    }

    private func completedHeader(_ result: JobResult) -> some View {
        AuralignPageHeader(
            title: "Results",
            subtitle: "Completed after successful engine exit and verified local output.",
            symbol: "checkmark.seal"
        ) {
            Button {
                model.job.revealOutputDirectory()
            } label: {
                Label("Reveal Job Folder", systemImage: "folder")
            }
            .disabled(model.isPresentationFixture)
            .help("Revalidate and reveal the completed job folder in Finder.")
            .accessibilityIdentifier(AccessibilityIdentifier.resultsRevealFolder)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(
            "Completed results for \(result.outputDirectory.lastPathComponent)"
        )
    }

    private func transcriptSurface(
        _ preview: ResultTranscriptPreview
    ) -> some View {
        AuralignSection(
            title: "Transcript Preview",
            subtitle: transcriptSubtitle(preview)
        ) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                switch preview.status {
                case .available:
                    if let text = preview.text {
                        ScrollView {
                            Text(text)
                                .font(.body)
                                .textSelection(.enabled)
                                .frame(
                                    maxWidth: .infinity,
                                    minHeight: 300,
                                    alignment: .topLeading
                                )
                                .padding(AuralignVisualSystem.spacingL)
                        }
                        .frame(minHeight: 320, idealHeight: 430, maxHeight: 620)
                        .background(Color(nsColor: .textBackgroundColor))
                        .overlay {
                            RoundedRectangle(
                                cornerRadius: AuralignVisualSystem.cornerRadius
                            )
                            .strokeBorder(Color(nsColor: .separatorColor))
                        }
                        .clipShape(
                            RoundedRectangle(
                                cornerRadius: AuralignVisualSystem.cornerRadius
                            )
                        )
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.resultsTranscript
                        )

                        HStack(alignment: .firstTextBaseline) {
                            if preview.truncated {
                                Label(
                                    "Preview is truncated; the verified transcript file "
                                        + "contains the complete local output.",
                                    systemImage: "ellipsis.circle"
                                )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                                .accessibilityIdentifier(
                                    AccessibilityIdentifier.resultsTranscriptTruncated
                                )
                            }
                            Spacer(minLength: AuralignVisualSystem.spacingM)
                            Button("Copy Preview") {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(text, forType: .string)
                            }
                            .controlSize(.small)
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.resultsCopyTranscript
                            )
                        }
                    }
                case .empty:
                    transcriptUnavailable(
                        title: "Transcript file is empty",
                        message: "The engine produced a verified zero-byte transcript file."
                    )
                case .missing:
                    transcriptUnavailable(
                        title: "Transcript preview unavailable",
                        message: "This completed result did not declare a transcript.txt artifact."
                    )
                }
            }
        }
    }

    private func transcriptSubtitle(_ preview: ResultTranscriptPreview) -> String {
        switch preview.status {
        case .available:
            let displayed = ByteCountFormatter.string(
                fromByteCount: Int64(preview.displayedBytes),
                countStyle: .file
            )
            if let sourceBytes = preview.sourceBytes {
                let source = ByteCountFormatter.string(
                    fromByteCount: sourceBytes,
                    countStyle: .file
                )
                return preview.truncated
                    ? "Showing \(displayed) of \(source) from transcript.txt."
                    : "Showing the verified \(source) transcript.txt preview."
            }
            return "Bounded preview from the verified transcript.txt artifact."
        case .empty:
            return "Verified transcript artifact with no text."
        case .missing:
            return "No transcript preview was available for this result."
        }
    }

    private func transcriptUnavailable(title: String, message: String) -> some View {
        AuralignStatusBanner(
            title: title,
            message: message,
            tone: .neutral,
            symbol: "doc.text"
        )
        .accessibilityIdentifier(AccessibilityIdentifier.resultsTranscript)
    }

    // MARK: - Details and evidence

    private func resultInspector(_ result: JobResult) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
            runFacts(result)
            qaEvidence(result.qa)
            artifactList(result.artifacts)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.resultsInspector)
    }

    private func runFacts(_ result: JobResult) -> some View {
        AuralignSection(
            title: "Run Facts",
            subtitle: "Counts reported by the completed local engine job."
        ) {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                factRow("Language", result.language ?? "Not reported")
                factRow("Speaker turns", String(result.speakerTurns))
                factRow("Words", String(result.words))
                factRow("Segments", String(result.segments))
                factRow("Artifacts", String(result.artifacts.count))
            }
        }
    }

    private func factRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.callout.weight(.medium))
                .monospacedDigit()
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func qaEvidence(_ qa: ResultQAProjection) -> some View {
        AuralignSection(
            title: "Automated QA",
            subtitle: "Deterministic evidence from qa_report.json; not a quality score."
        ) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                AuralignStatusBadge(
                    text: qaStatusTitle(qa),
                    tone: qaStatusTone(qa),
                    symbol: qaStatusSymbol(qa)
                )
                if qa.status == .available {
                    if let flagCount = qa.flagCount {
                        Text(
                            "\(flagCount) reported flag\(flagCount == 1 ? "" : "s")"
                                + qaChangedSpanSuffix(qa)
                        )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                    }
                    ForEach(qa.flags) { flag in
                        VStack(alignment: .leading, spacing: 2) {
                            Label(
                                flag.message,
                                systemImage: qaFlagSymbol(flag.severity)
                            )
                            .font(.callout)
                            .foregroundStyle(qaFlagTone(flag.severity).color)
                            Text(flag.code)
                                .font(.caption2.monospaced())
                                .foregroundStyle(.tertiary)
                        }
                        .accessibilityElement(children: .combine)
                    }
                    if qa.omittedFlagCount > 0 {
                        Text(
                            "\(qa.omittedFlagCount) additional flag"
                                + "\(qa.omittedFlagCount == 1 ? " was" : "s were") "
                                + "omitted from this bounded projection."
                        )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                } else {
                    Text(qaStatusExplanation(qa.status))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Automated QA")
        .accessibilityIdentifier(AccessibilityIdentifier.resultsQA)
    }

    private func qaStatusTitle(_ qa: ResultQAProjection) -> String {
        switch qa.status {
        case .available:
            return qa.overallQualitySummary == "review_warnings"
                ? "Review warnings reported"
                : "No deterministic warnings reported"
        case .disabled:
            return "QA disabled for this run"
        case .missing:
            return "QA report not produced"
        case .invalid:
            return "QA report could not be parsed"
        case .oversized:
            return "QA report exceeds preview limit"
        }
    }

    private func qaStatusTone(_ qa: ResultQAProjection) -> AuralignStatusTone {
        switch qa.status {
        case .available:
            return (qa.errorFlagCount ?? 0) > 0 || (qa.warningFlagCount ?? 0) > 0
                ? .warning
                : .neutral
        case .invalid, .oversized:
            return .warning
        case .disabled, .missing:
            return .neutral
        }
    }

    private func qaStatusSymbol(_ qa: ResultQAProjection) -> String {
        switch qa.status {
        case .available:
            return qaStatusTone(qa) == .warning
                ? "exclamationmark.triangle"
                : "checklist"
        case .disabled:
            return "minus.circle"
        case .missing:
            return "doc.badge.ellipsis"
        case .invalid:
            return "doc.badge.exclamationmark"
        case .oversized:
            return "doc.badge.clock"
        }
    }

    private func qaStatusExplanation(_ status: ResultQAStatus) -> String {
        switch status {
        case .available:
            return ""
        case .disabled:
            return "The effective configuration did not run automated QA."
        case .missing:
            return "No qa_report.json artifact was declared for this completed job."
        case .invalid:
            return "The verified QA artifact exists, but its bounded projection "
                + "did not satisfy the expected report schema."
        case .oversized:
            return "The verified QA artifact is larger than the 1 MiB parsing limit. "
                + "It remains available in the local job folder."
        }
    }

    private func qaChangedSpanSuffix(_ qa: ResultQAProjection) -> String {
        guard let count = qa.changedSpanCount else { return "" }
        return " · \(count) changed span\(count == 1 ? "" : "s")"
    }

    private func qaFlagTone(_ severity: String) -> AuralignStatusTone {
        switch severity {
        case "error":
            return .failure
        case "warning":
            return .warning
        default:
            return .neutral
        }
    }

    private func qaFlagSymbol(_ severity: String) -> String {
        switch severity {
        case "error":
            return "xmark.octagon"
        case "warning":
            return "exclamationmark.triangle"
        default:
            return "info.circle"
        }
    }

    private func runWarnings(_ warnings: [String]) -> some View {
        AuralignStatusBanner(
            title: warnings.count == 1 ? "Run warning" : "Run warnings",
            message: warnings.joined(separator: "\n"),
            tone: .warning
        )
        .accessibilityIdentifier(AccessibilityIdentifier.resultsWarnings)
    }

    private func artifactList(_ artifacts: [JobArtifact]) -> some View {
        AuralignSection(
            title: "Artifacts",
            subtitle: "Verified immediate regular files in the completed job folder."
        ) {
            if artifacts.isEmpty {
                Text("No regular-file artifacts were declared for this result.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(artifacts.enumerated()), id: \.element.id) {
                        index, artifact in
                        artifactRow(artifact, index: index)
                        if artifact.id != artifacts.last?.id {
                            Divider()
                        }
                    }
                }
            }
        }
    }

    private func artifactRow(_ artifact: JobArtifact, index: Int) -> some View {
        HStack(spacing: AuralignVisualSystem.spacingS) {
            Image(systemName: symbolFor(artifact.name))
                .foregroundStyle(.secondary)
                .frame(width: 18)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(artifact.name)
                    .font(.callout.monospaced())
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(artifact.sizeText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            Spacer(minLength: AuralignVisualSystem.spacingS)
            Menu {
                Button("Open") {
                    lastArtifactActionIndex = index
                    model.job.openArtifact(artifact)
                }
                .disabled(model.isPresentationFixture)
                Button("Reveal in Finder") {
                    lastArtifactActionIndex = index
                    model.job.revealArtifact(artifact)
                }
                .disabled(model.isPresentationFixture)
                Button("Copy Path") {
                    lastArtifactActionIndex = index
                    model.job.copyArtifactPath(artifact)
                }
                .disabled(model.isPresentationFixture)
            } label: {
                Label(
                    "Actions for \(artifact.name)",
                    systemImage: "ellipsis.circle"
                )
                .labelStyle(.iconOnly)
            }
            .menuStyle(.borderlessButton)
            .focused($artifactActionFocus, equals: index)
            .accessibilityFocused(
                $artifactActionAccessibilityFocus,
                equals: index
            )
            .accessibilityLabel("Actions for \(artifact.name)")
            .accessibilityIdentifier(
                AccessibilityIdentifier.artifactActions(index: index)
            )
            .help("Actions revalidate this artifact before using its local path.")
        }
        .padding(.vertical, AuralignVisualSystem.spacingS)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.artifact(index: index))
        .contextMenu {
            Button("Open") {
                lastArtifactActionIndex = index
                model.job.openArtifact(artifact)
            }
            .disabled(model.isPresentationFixture)
            Button("Reveal in Finder") {
                lastArtifactActionIndex = index
                model.job.revealArtifact(artifact)
            }
            .disabled(model.isPresentationFixture)
            Button("Copy Path") {
                lastArtifactActionIndex = index
                model.job.copyArtifactPath(artifact)
            }
            .disabled(model.isPresentationFixture)
        }
    }

    private func symbolFor(_ name: String) -> String {
        if name.hasSuffix(".wav") { return "waveform" }
        if name.hasSuffix(".srt") || name.hasSuffix(".vtt") {
            return "captions.bubble"
        }
        if name.hasSuffix(".json") || name.hasSuffix(".jsonl") {
            return "curlybraces"
        }
        if name.hasSuffix(".csv") { return "tablecells" }
        if name.hasSuffix(".md") || name.hasSuffix(".txt") { return "doc.text" }
        if name.hasSuffix(".rttm") { return "person.2.wave.2" }
        return "doc"
    }

    private func restoreArtifactActionFocus() {
        guard let index = lastArtifactActionIndex else { return }
        Task { @MainActor in
            await Task.yield()
            artifactActionFocus = index
            artifactActionAccessibilityFocus = index
        }
    }

    // MARK: - Failed and cancelled

    private enum PartialState {
        case failed
        case cancelled

        var title: String {
            switch self {
            case .failed: return "Job failed"
            case .cancelled: return "Job cancelled"
            }
        }

        var symbol: String {
            switch self {
            case .failed: return "xmark.octagon"
            case .cancelled: return "stop.circle"
            }
        }

        var tone: AuralignStatusTone {
            switch self {
            case .failed: return .failure
            case .cancelled: return .warning
            }
        }
    }

    private func partialBody(
        state: PartialState,
        message: String,
        remediation: String?,
        technicalType: String?,
        technicalDiagnostics: String?,
        directory: URL?,
        artifacts: [JobArtifact]
    ) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                AuralignPageHeader(
                    title: "Results",
                    subtitle: "Incomplete local output is kept separate from completed Results.",
                    symbol: "doc.text"
                )
                AuralignStatusBanner(
                    title: state.title,
                    message: message,
                    tone: state.tone,
                    symbol: state.symbol,
                    actionTitle: directory == nil ? nil : "Reveal Job Folder",
                    actionIdentifier: directory == nil
                        ? nil
                        : AccessibilityIdentifier.resultsRevealFolder,
                    action: directory == nil
                        ? nil
                        : { model.job.revealOutputDirectory() }
                )
                if let remediation, !remediation.isEmpty {
                    AuralignSection(title: "Recovery") {
                        Text(remediation)
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !artifacts.isEmpty {
                    AuralignSection(
                        title: "Verified Partial Artifacts",
                        subtitle: "These files were inventoried after process exit. "
                            + "They do not indicate successful completion."
                    ) {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(artifacts.enumerated()), id: \.element.id) {
                                index, artifact in
                                artifactRow(artifact, index: index)
                                if artifact.id != artifacts.last?.id {
                                    Divider()
                                }
                            }
                        }
                    }
                }
                if let technicalType {
                    DisclosureGroup("Technical Details") {
                        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                            Text("Failure type")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(technicalType)
                                .font(.callout.monospaced())
                                .textSelection(.enabled)
                            if let technicalDiagnostics, !technicalDiagnostics.isEmpty {
                                Text("Local diagnostic tail")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .padding(.top, AuralignVisualSystem.spacingS)
                                Text(technicalDiagnostics)
                                    .font(.caption.monospaced())
                                    .textSelection(.enabled)
                            }
                        }
                        .padding(.top, AuralignVisualSystem.spacingS)
                    }
                }
            }
            .padding(AuralignVisualSystem.spacingXL)
            .frame(
                maxWidth: AuralignVisualSystem.readableContentWidth,
                alignment: .leading
            )
        }
    }

    private func partialAvailabilityMessage(
        directory: URL?,
        artifacts: [JobArtifact]
    ) -> String {
        if !artifacts.isEmpty {
            return "\(artifacts.count) verified partial artifact"
                + "\(artifacts.count == 1 ? " was" : "s were") "
                + "inventoried after the engine process exited."
        }
        if directory != nil {
            return "The job folder was retained, but it contains no verified "
                + "regular-file artifacts."
        }
        return "The engine process exited without a verified job folder."
    }
}
