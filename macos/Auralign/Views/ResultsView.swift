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
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
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
                emptyResultsBody()
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

    // MARK: - Empty state

    /// The measured baseline was ~92% void: one bordered row, then a black
    /// rectangle. A confident, content-proportioned panel with the action
    /// that fills it replaces the bordered sentence in a void.
    private func emptyResultsBody() -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
            AuralignWorkspaceHeader(
                symbol: "doc.text",
                accessibilityHeading: "Results"
            ) {
                Text("No run has completed in this workspace yet.")
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            HStack {
                Spacer(minLength: 0)
                emptyResultsCard
                Spacer(minLength: 0)
            }

            Spacer(minLength: 0)
        }
        .padding(AuralignVisualSystem.spacingXL)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var emptyResultsCard: some View {
        VStack(spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 30))
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
            Text("No results yet")
                .font(AuralignType.pageTitle)
                .accessibilityAddTraits(.isHeader)
            Text(
                "Results appear here only after the local engine completes a "
                    + "job and its output is verified. Choose a source and "
                    + "check readiness to start one."
            )
            .font(AuralignType.body)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 360)
            Button {
                model.selectedSection = .newJob
            } label: {
                Label("Start a New Transcription", systemImage: "waveform.badge.plus")
            }
            .controlSize(.large)
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("results.empty.start-new-job")
        }
        .padding(AuralignVisualSystem.spacingXL)
        .frame(maxWidth: 420)
        .background(AuralignSurface.raised)
        .overlay {
            RoundedRectangle(cornerRadius: AuralignRadius.group)
                .strokeBorder(
                    increaseContrast ? Color.primary : AuralignSurface.hairline,
                    lineWidth: increaseContrast ? 1 : 0.5
                )
        }
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.group))
    }

    // MARK: - Incomplete lifecycle states

    private func pendingBody(
        title: String,
        message: String,
        symbol: String,
        tone: AuralignStatusTone
    ) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
            AuralignWorkspaceHeader(
                symbol: "doc.text",
                accessibilityHeading: "Results"
            ) {
                Text("Verified local output from the current Auralign job.")
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
                if model.resultDrift.isStale, let explanation = model.resultDrift.explanation {
                    AuralignCausalityBanner(
                        headline: staleHeadline(model.resultDrift),
                        detail: explanation,
                        tone: .warning,
                        symbol: "clock.arrow.circlepath",
                        linkTitle: "Start a New Transcription",
                        linkIdentifier: "results.stale.start-new-job",
                        link: { model.selectedSection = .newJob }
                    )
                    .accessibilityIdentifier(AccessibilityIdentifier.resultsStale)
                }
                if !result.warnings.isEmpty {
                    runWarnings(result.warnings)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: AuralignVisualSystem.spacingXL) {
                        transcriptSurface(result)
                            .frame(minWidth: 440, maxWidth: .infinity)
                        resultInspector(result)
                            .frame(width: AuralignVisualSystem.inspectorWidth)
                    }
                    VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                        transcriptSurface(result)
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
            .frame(maxWidth: 1_320, alignment: .leading)
        }
    }

    private func completedHeader(_ result: JobResult) -> some View {
        AuralignWorkspaceHeader(
            symbol: "checkmark.seal",
            accessibilityHeading:
                "Completed results for \(result.outputDirectory.lastPathComponent)"
        ) {
            VStack(alignment: .leading, spacing: 2) {
                Text(result.sourceURL.lastPathComponent)
                    .font(AuralignType.rowTitle)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(completedSummaryLine(result))
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        } accessory: {
            Button {
                model.job.revealOutputDirectory()
            } label: {
                Label("Reveal Job Folder", systemImage: "folder")
            }
            .disabled(model.isPresentationFixture)
            .help("Revalidate and reveal the completed job folder in Finder.")
            .accessibilityIdentifier(AccessibilityIdentifier.resultsRevealFolder)
        }
    }

    private func completedSummaryLine(_ result: JobResult) -> String {
        var parts = ["Completed"]
        parts.append("\(result.words) word\(result.words == 1 ? "" : "s")")
        parts.append("\(result.segments) segment\(result.segments == 1 ? "" : "s")")
        if result.speakerTurns > 0 {
            parts.append(
                "\(result.speakerTurns) speaker turn\(result.speakerTurns == 1 ? "" : "s")"
            )
        }
        return parts.joined(separator: " · ")
    }

    private func staleHeadline(_ drift: ResultDrift) -> String {
        switch (drift.sourceChanged, drift.configurationChanged) {
        case (true, true):
            return "Stale — the source and configuration changed after this run."
        case (true, false):
            return "Stale — the selected source changed after this run."
        case (false, true):
            return "Stale — the configuration changed after this run."
        case (false, false):
            return "Results are current."
        }
    }

    // MARK: - Transcript

    /// Generous, document-shaped reading content in a single reading
    /// measure — never a monospace-adjacent box. No structure beyond the flat
    /// preview text and the scalar counts the contract actually supplies.
    private func transcriptSurface(_ result: JobResult) -> some View {
        let preview = result.transcriptPreview
        return AuralignSection(
            title: "Transcript",
            subtitle: transcriptSubtitle(preview)
        ) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                if preview.status == .available {
                    transcriptStatLine(result)
                }
                switch preview.status {
                case .available:
                    if let text = preview.text {
                        transcriptReadingSurface(text)
                        transcriptFooter(preview: preview, text: text)
                    }
                case .empty:
                    transcriptUnavailable(
                        title: "Transcript file is empty",
                        message: "The engine produced a verified zero-byte transcript file."
                    )
                case .missing:
                    transcriptUnavailable(
                        title: "Transcript preview unavailable",
                        message: "This completed result did not declare a transcript.txt "
                            + "artifact."
                    )
                }
            }
        }
    }

    private func transcriptStatLine(_ result: JobResult) -> some View {
        HStack(spacing: AuralignVisualSystem.spacingL) {
            transcriptStat(value: result.words, label: "words")
            transcriptStat(value: result.segments, label: "segments")
            if result.speakerTurns > 0 {
                transcriptStat(value: result.speakerTurns, label: "speaker turns")
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }

    private func transcriptStat(value: Int, label: String) -> some View {
        HStack(spacing: 4) {
            Text("\(value)")
                .font(AuralignType.numeric)
            Text(label)
                .font(AuralignType.metadata)
                .foregroundStyle(.secondary)
        }
    }

    private func transcriptReadingSurface(_ text: String) -> some View {
        ScrollView {
            Text(text)
                .font(AuralignType.transcript)
                .lineSpacing(7)
                .textSelection(.enabled)
                .frame(maxWidth: 640, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(AuralignVisualSystem.spacingXL)
        }
        .frame(minHeight: 320, idealHeight: 440, maxHeight: 640)
        .background(AuralignSurface.inset)
        .overlay {
            RoundedRectangle(cornerRadius: AuralignRadius.group)
                .strokeBorder(
                    increaseContrast ? Color.primary : AuralignSurface.hairline,
                    lineWidth: increaseContrast ? 1 : 0.5
                )
        }
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.group))
        .accessibilityIdentifier(AccessibilityIdentifier.resultsTranscript)
    }

    private func transcriptFooter(
        preview: ResultTranscriptPreview,
        text: String
    ) -> some View {
        HStack(alignment: .firstTextBaseline) {
            if preview.truncated, let sourceBytes = preview.sourceBytes {
                let shown = ByteCountFormatter.string(
                    fromByteCount: Int64(preview.displayedBytes),
                    countStyle: .file
                )
                let total = ByteCountFormatter.string(
                    fromByteCount: sourceBytes,
                    countStyle: .file
                )
                Label(
                    "Showing the first \(shown) of \(total) — open the full "
                        + "transcript to read the rest.",
                    systemImage: "ellipsis.circle"
            )
                .font(AuralignType.metadata)
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
            .accessibilityIdentifier(AccessibilityIdentifier.resultsCopyTranscript)
        }
    }

    private func transcriptSubtitle(_ preview: ResultTranscriptPreview) -> String {
        switch preview.status {
        case .available:
            return preview.truncated
                ? "Bounded preview from the verified transcript.txt artifact."
                : "The complete verified transcript.txt artifact."
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
            qaEvidence(result.qa)
            artifactList(result.artifacts)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.resultsInspector)
    }

    @ViewBuilder
    private func qaEvidence(_ qa: ResultQAProjection) -> some View {
        AuralignSection(
            title: "Automated QA",
            subtitle: "Deterministic evidence from qa_report.json; not a quality score."
        ) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                AuralignCausalityBanner(
                    headline: qaStatusTitle(qa),
                    detail: qaStatusDetail(qa),
                    tone: qaStatusTone(qa),
                    symbol: qaStatusSymbol(qa)
                )
                if qa.status == .available, !qa.flags.isEmpty {
                    VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                        ForEach(qa.flags) { flag in
                            qaFlagRow(flag)
                        }
                        if qa.omittedFlagCount > 0 {
                            Text(
                                "\(qa.omittedFlagCount) additional flag"
                                    + "\(qa.omittedFlagCount == 1 ? " was" : "s were") "
                                    + "omitted from this bounded projection."
                            )
                            .font(AuralignType.metadata)
                            .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Automated QA")
        .accessibilityIdentifier(AccessibilityIdentifier.resultsQA)
    }

    private func qaFlagRow(_ flag: ResultQAFlag) -> some View {
        AuralignMediaRow(
            title: flag.message,
            metadata: flag.code,
            swatch: auralignMediaRowSwatch(
                symbol: qaFlagSymbol(flag.severity),
                tint: presentationTint(qaFlagTone(flag.severity).color)
            ),
            allowsMultiline: true
        )
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

    private func qaStatusDetail(_ qa: ResultQAProjection) -> String? {
        if qa.status == .available {
            guard let flagCount = qa.flagCount else { return nil }
            return "\(flagCount) reported flag\(flagCount == 1 ? "" : "s")"
                + qaChangedSpanSuffix(qa) + "."
        }
        let explanation = qaStatusExplanation(qa.status)
        return explanation.isEmpty ? nil : explanation
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
        AuralignCausalityBanner(
            headline: warnings.count == 1 ? "Run warning" : "\(warnings.count) run warnings",
            detail: warnings
                .map(model.privacySafePresentationMessage)
                .joined(separator: "\n"),
            tone: .warning,
            symbol: "exclamationmark.triangle.fill"
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
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                    ForEach(Array(artifacts.enumerated()), id: \.element.id) {
                        index, artifact in
                        artifactRow(artifact, index: index)
                    }
                }
            }
        }
    }

    private func artifactRow(_ artifact: JobArtifact, index: Int) -> some View {
        AuralignMediaRow(
            title: artifact.name,
            metadata: artifact.sizeText,
            swatch: auralignMediaRowSwatch(
                symbol: symbolFor(artifact.name),
                tint: presentationTint(tintFor(artifact.name))
            ),
            containsInteractiveTrailing: true,
            contentIdentifier: AccessibilityIdentifier.artifact(index: index)
        ) {
            artifactActionsMenu(artifact, index: index)
        }
        .contextMenu {
            artifactMenuItems(artifact, index: index)
        }
    }

    private func artifactActionsMenu(_ artifact: JobArtifact, index: Int) -> some View {
        Menu {
            artifactMenuItems(artifact, index: index)
        } label: {
            Label("Actions for \(artifact.name)", systemImage: "ellipsis.circle")
                .labelStyle(.iconOnly)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .focused($artifactActionFocus, equals: index)
        .accessibilityFocused($artifactActionAccessibilityFocus, equals: index)
        .accessibilityLabel("Actions for \(artifact.name)")
        .accessibilityIdentifier(AccessibilityIdentifier.artifactActions(index: index))
        .help("Actions revalidate this artifact before using its local path.")
    }

    @ViewBuilder
    private func artifactMenuItems(_ artifact: JobArtifact, index: Int) -> some View {
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

    /// A categorical tint for the artifact's format glyph — never a
    /// fabricated per-file waveform. Collapses to `.secondary` whenever
    /// decorative color must be suppressed.
    private func tintFor(_ name: String) -> Color {
        if name.hasSuffix(".wav") { return .teal }
        if name.hasSuffix(".srt") || name.hasSuffix(".vtt") { return .blue }
        if name.hasSuffix(".json") || name.hasSuffix(".jsonl") { return .purple }
        if name.hasSuffix(".csv") { return .green }
        if name.hasSuffix(".rttm") { return .pink }
        return .secondary
    }

    private func presentationTint(_ color: Color) -> Color {
        increaseContrast || differentiateWithoutColor ? .secondary : color
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
                AuralignWorkspaceHeader(
                    symbol: "doc.text",
                    accessibilityHeading: "Results"
                ) {
                    Text(
                        "Incomplete local output is kept separate from completed Results."
                    )
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                }
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
                            .font(AuralignType.body)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !artifacts.isEmpty {
                    AuralignSection(
                        title: "Verified Partial Artifacts",
                        subtitle: "These files were inventoried after process exit. "
                            + "They do not indicate successful completion."
                    ) {
                        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                            ForEach(Array(artifacts.enumerated()), id: \.element.id) {
                                index, artifact in
                                artifactRow(artifact, index: index)
                            }
                        }
                    }
                }
                if let technicalType {
                    DisclosureGroup("Technical Details") {
                        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                            Text("Failure type")
                                .font(AuralignType.metadata)
                                .foregroundStyle(.secondary)
                            Text(technicalType)
                                .font(AuralignType.technicalBody)
                                .textSelection(.enabled)
                            if let technicalDiagnostics, !technicalDiagnostics.isEmpty {
                                Text("Local diagnostic tail")
                                    .font(AuralignType.metadata)
                                    .foregroundStyle(.secondary)
                                    .padding(.top, AuralignVisualSystem.spacingS)
                                Text(technicalDiagnostics)
                                    .font(AuralignType.technical)
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
