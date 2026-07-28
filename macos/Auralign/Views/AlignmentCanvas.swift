// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

// MARK: - Truthfulness boundary
//
// The engine's Swift-visible contract supplies:
//
//   * source media facts (duration, channels, sample rate, codec/format);
//   * an ordered stage list derived from the *effective configuration*, with
//     per-stage pending/active/done(seconds)/skipped(reason) status and
//     engine-authored detail strings;
//   * measured elapsed time and measured per-stage durations;
//   * scalar result counts (segments, words, speaker turns) and artifacts.
//
// It supplies NO audio samples, NO per-segment timings, NO speaker segments,
// NO word alignment positions, and NO confidence values. `PendingJobResultEnvelope`
// rejects unknown keys outright, so those layers cannot be added without a
// coordinated bridge schema-version bump on both sides.
//
// The canvas therefore renders the *journey* and its real evidence, and
// deliberately omits waveform, speaker-lane, and confidence layers rather than
// simulating them. Every mark below traces to a value listed above.

/// The five phases the user is taught to recognise. These are a navigable
/// configuration map over real stage identifiers, not a claim about literal
/// execution order; engine stages can interleave across these groups.
enum AlignmentCanvasPhase: Int, CaseIterable, Identifiable {
    case source
    case recognition
    case speakers
    case refinement
    case delivery

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .source: return "Source"
        case .recognition: return "Recognition"
        case .speakers: return "Speakers"
        case .refinement: return "Refinement"
        case .delivery: return "Delivery"
        }
    }

    var symbol: String {
        switch self {
        case .source: return "waveform"
        case .recognition: return "text.quote"
        case .speakers: return "person.2.wave.2"
        case .refinement: return "slider.horizontal.3"
        case .delivery: return "square.and.arrow.up"
        }
    }

    /// Identity spectrum stop. `refinement` shares the alignment stop with
    /// `speakers` because the spectrum has four stops and the journey has five
    /// phases; the ramp is positional, not one-hue-per-stage.
    var spectrumStop: AuralignSpectrumStop {
        switch self {
        case .source: return .source
        case .recognition: return .recognition
        case .speakers, .refinement: return .alignment
        case .delivery: return .delivery
        }
    }

    /// Engine stage identifiers owned by this phase. Mirrors
    /// `JobExpectations.expectedStages()` and `PipelineNavigationGroup`.
    var stageIdentifiers: Set<String> {
        switch self {
        case .source:
            return ["preflight", "inspect", "canonical_audio", "vad"]
        case .recognition:
            return ["asr"]
        case .speakers:
            return ["diarization", "fusion", "speaker_id", "speaker_identity"]
        case .refinement:
            return ["alignment", "redecode", "cleanup"]
        case .delivery:
            return ["export", "model_release", "complete"]
        }
    }
}

/// Resolved status of one canvas phase, derived only from real stage status.
enum AlignmentCanvasPhaseStatus: Equatable {
    /// No stage in this phase will run under the effective configuration.
    case skipped
    /// Stages exist but have not started.
    case pending
    /// At least one member stage is currently active.
    case active
    /// Every member stage that runs has finished.
    case complete
    /// The run stopped before this phase resolved (failed or cancelled).
    case halted

    var label: String {
        switch self {
        case .skipped: return "Disabled"
        case .pending: return "Pending"
        case .active: return "Active"
        case .complete: return "Complete"
        case .halted: return "Not reached"
        }
    }
}

/// One rendered phase.
struct AlignmentCanvasSegment: Identifiable, Equatable {
    var phase: AlignmentCanvasPhase
    var status: AlignmentCanvasPhaseStatus
    /// Engine-authored evidence for this phase, already redacted upstream.
    var evidence: [String]
    /// Number of member stages that will actually run.
    var activeStageCount: Int

    var id: Int { phase.rawValue }
}

/// A pure, testable projection of the alignment canvas.
///
/// It is intentionally a value type with no view or engine dependencies so the
/// truthfulness rules above can be unit-tested without rendering.
struct AlignmentCanvasProjection: Equatable {
    /// Overall posture of the canvas, mirroring the job lifecycle.
    enum Posture: Equatable {
        case empty
        case sourceSelected
        case ready
        case running
        case stopping
        case finalizing
        case completed
        case failed
        case cancelled
    }

    var posture: Posture
    var segments: [AlignmentCanvasSegment]
    /// Real probed duration in seconds, when the media has been inspected.
    var sourceDurationSeconds: Double?
    var sourceDurationText: String?
    /// Real probed channel/rate/format facts, already formatted.
    var sourceFacts: [String]
    /// Fraction of phases that have genuinely completed, 0...1. Derived from
    /// counted phase status only — never from a timer.
    var completedFraction: Double
    /// Deterministic seed for the decorative `SourceIdentityField`, derived
    /// only from real probed facts. `nil` until the source has been probed —
    /// see DESIGN.md's "Authorized exception" for why this decorative visual
    /// is permitted at all.
    var sourceIdentitySeed: SourceIdentitySeed?

    /// Builds the projection from the live contract.
    ///
    /// - Parameters:
    ///   - stages: `JobController.stages`, the engine-derived stage list.
    ///   - posture: resolved lifecycle posture.
    ///   - probe: probed source facts, when available.
    init(
        stages: [PipelineStage],
        posture: Posture,
        probe: MediaProbe?,
        privacySafeTransform: (String) -> String = { $0 }
    ) {
        self.posture = posture

        var built: [AlignmentCanvasSegment] = []
        for phase in AlignmentCanvasPhase.allCases {
            let members = stages.filter { phase.stageIdentifiers.contains($0.id) }
            let runnable = members.filter {
                if case .skipped = $0.status { return false }
                return true
            }
            let status: AlignmentCanvasPhaseStatus
            if members.isEmpty {
                // The configuration produces no stage for this phase at all.
                status = .skipped
            } else if runnable.isEmpty {
                status = .skipped
            } else if runnable.contains(where: { $0.status == .active }) {
                status = .active
            } else if runnable.allSatisfy({
                if case .done = $0.status { return true }
                return false
            }) {
                status = .complete
            } else if posture == .failed || posture == .cancelled {
                // Preserve the causal story: unreached phases are not "pending".
                status = .halted
            } else {
                status = .pending
            }

            let evidence = members.compactMap { stage -> String? in
                switch stage.status {
                case .skipped(let reason):
                    let safeReason = privacySafeTransform(reason)
                    return safeReason.isEmpty
                        ? nil
                        : "\(stage.title) — \(safeReason)"
                default:
                    let trimmed = privacySafeTransform(stage.detail)
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    return trimmed.isEmpty ? nil : "\(stage.title) — \(trimmed)"
                }
            }

            built.append(
                AlignmentCanvasSegment(
                    phase: phase,
                    status: status,
                    evidence: evidence,
                    activeStageCount: runnable.count
                )
            )
        }
        segments = built

        let counted = built.filter { $0.status != .skipped }
        let done = counted.filter { $0.status == .complete }.count
        completedFraction = counted.isEmpty
            ? 0
            : Double(done) / Double(counted.count)

        if let probe {
            sourceDurationSeconds = probe.durationSeconds
            sourceDurationText = probe.durationText
            var facts: [String] = []
            if let channels = probe.channels {
                facts.append(channels == 1 ? "mono" : "\(channels) ch")
            }
            if let rate = probe.sampleRate {
                facts.append("\(rate / 1_000) kHz")
            }
            if let codec = probe.codec, !codec.isEmpty {
                facts.append(codec)
            }
            sourceFacts = facts
            sourceIdentitySeed = SourceIdentitySeed(
                durationSeconds: probe.durationSeconds,
                sampleRate: probe.sampleRate,
                channels: probe.channels,
                codec: probe.codec
            )
        } else {
            sourceDurationSeconds = nil
            sourceDurationText = nil
            sourceFacts = []
            sourceIdentitySeed = nil
        }
    }

    /// A complete spoken summary. The canvas must never be the only carrier of
    /// this information.
    var accessibilitySummary: String {
        var parts: [String] = []
        switch posture {
        case .empty: parts.append("No source selected.")
        case .sourceSelected: parts.append("Source selected.")
        case .ready: parts.append("Ready to start.")
        case .running: parts.append("Running.")
        case .stopping: parts.append("Stopping.")
        case .finalizing: parts.append("Finalizing.")
        case .completed: parts.append("Completed.")
        case .failed: parts.append("Failed.")
        case .cancelled: parts.append("Cancelled.")
        }
        if let sourceDurationText {
            parts.append("Source duration \(sourceDurationText).")
        }
        for segment in segments {
            parts.append("\(segment.phase.title): \(segment.status.label).")
        }
        return parts.joined(separator: " ")
    }
}

extension AlignmentCanvasProjection {
    /// Builds the canvas from live application state.
    ///
    /// Before a run exists the stage list comes from `JobExpectations`, so the
    /// user sees the journey their *effective configuration* will actually
    /// take — including honestly disabled phases — rather than a generic
    /// preview. Once a job owns the process the engine's own stage list wins.
    @MainActor
    static func live(model: AppModel) -> AlignmentCanvasProjection {
        let posture: Posture
        switch model.job.phase {
        case .running:
            posture = .running
        case .stopping:
            posture = .stopping
        case .resultPendingExit:
            posture = .finalizing
        case .completed:
            posture = .completed
        case .failed:
            posture = .failed
        case .cancelled:
            posture = .cancelled
        case .idle:
            // Deliberately does NOT consult `startBlocker`: that accessor
            // re-stats the source file and probes the output root on every
            // read, so calling it from a view body causes filesystem I/O on
            // each render pass. The canvas renders identically for
            // "selected" and "ready" anyway — readiness is carried by the
            // toolbar action, the readiness summary, and the status footer.
            posture = model.inputURL == nil ? .empty : .sourceSelected
        }

        let stages: [PipelineStage]
        if model.job.stages.isEmpty {
            stages = JobExpectations(config: model.config.effectiveConfig)
                .expectedStages()
        } else {
            stages = model.job.stages
        }

        return AlignmentCanvasProjection(
            stages: stages,
            posture: posture,
            probe: model.inputProbe,
            privacySafeTransform: model.privacySafePresentationMessage
        )
    }
}

// MARK: - View

/// Auralign's signature workspace object: the source-to-delivery journey,
/// rendered from real engine stage state.
///
/// The composition is structurally identical in every posture — the same five
/// phases in the same positions — so the canvas never collapses into an
/// unrelated layout between states. Only fill, evidence, and emphasis change.
struct AlignmentCanvasView: View {
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.controlActiveState) private var controlActiveState

    let projection: AlignmentCanvasProjection
    /// Source title shown above the ribbon. Callers pass a display name only —
    /// never a full path.
    var sourceTitle: String?
    var selectedPhase: AlignmentCanvasPhase?
    var onSelectPhase: ((AlignmentCanvasPhase) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
            sourceBand
            if let seed = projection.sourceIdentitySeed {
                SourceIdentityField(seed: seed)
            }
            AuralignSignalChamber(emphasis: chamberEmphasis) {
                AlignmentFieldView(projection: projection)
            }
            labelRow
            caption
        }
        .padding(AuralignVisualSystem.spacingL)
        .background(canvasBackground)
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.canvas))
        .overlay {
            RoundedRectangle(cornerRadius: AuralignRadius.canvas)
                .strokeBorder(
                    increaseContrast ? Color.primary : AuralignSurface.hairline,
                    lineWidth: increaseContrast ? 1 : 0.5
                )
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Alignment canvas")
        .accessibilityValue(projection.accessibilitySummary)
        .accessibilityIdentifier(AccessibilityIdentifier.alignmentCanvas)
    }

    // MARK: Background

    @ViewBuilder
    private var canvasBackground: some View {
        ZStack {
            AuralignSurface.canvasField(reduceTransparency: reduceTransparency)
            // Localized bloom marks the one active phase. It is absent when no
            // phase is active, so an idle window never glows.
            if let bloomStop, !reduceTransparency, !differentiateWithoutColor {
                GeometryReader { proxy in
                    let width = proxy.size.width
                    RoundedRectangle(cornerRadius: AuralignRadius.canvas)
                        .fill(
                            AuralignGradient.bloom(
                                bloomStop,
                                increaseContrast: increaseContrast,
                                reduceTransparency: reduceTransparency
                            )
                        )
                        .frame(width: max(width * 0.55, 160))
                        .offset(x: bloomOffset(in: width))
                        .allowsHitTesting(false)
                }
            }
        }
    }

    private var bloomStop: AuralignSpectrumStop? {
        guard controlActiveState != .inactive else { return nil }
        guard let active = projection.segments.first(where: {
            $0.status == .active
        }) else { return nil }
        return active.phase.spectrumStop
    }

    private var chamberEmphasis: AuralignSpectrumStop? {
        guard controlActiveState != .inactive else { return nil }
        if let active = projection.segments.first(where: {
            $0.status == .active
        }) {
            return active.phase.spectrumStop
        }
        return selectedPhase?.spectrumStop
    }

    private func bloomOffset(in width: CGFloat) -> CGFloat {
        guard let index = projection.segments.firstIndex(where: {
            $0.status == .active
        }) else { return 0 }
        let count = max(projection.segments.count, 1)
        let centre = (CGFloat(index) + 0.5) / CGFloat(count) * width
        return centre - max(width * 0.55, 160) / 2
    }

    // MARK: Source band

    @ViewBuilder
    private var sourceBand: some View {
        HStack(alignment: .firstTextBaseline, spacing: AuralignVisualSystem.spacingS) {
            if let sourceTitle {
                Text(sourceTitle)
                    .font(AuralignType.rowTitle)
                    .lineLimit(1)
                    .truncationMode(.middle)
            } else {
                Text("No source selected")
                    .font(AuralignType.rowTitle)
                    .foregroundStyle(.secondary)
            }

            if !projection.sourceFacts.isEmpty {
                Text(projection.sourceFacts.joined(separator: " · "))
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: AuralignVisualSystem.spacingS)

            if let duration = projection.sourceDurationText {
                Text(duration)
                    .font(AuralignType.numericSmall)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Ribbon

    private var labelRow: some View {
        HStack(spacing: AuralignVisualSystem.spacingXS) {
            ForEach(projection.segments) { segment in
                segmentView(segment)
            }
        }
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private func segmentView(_ segment: AlignmentCanvasSegment) -> some View {
        let isSelected = selectedPhase == segment.phase
        if let onSelectPhase {
            Button {
                onSelectPhase(segment.phase)
            } label: {
                segmentLabel(segment, isSelected: isSelected)
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(segment.phase.title)
            .accessibilityValue(accessibilityValue(for: segment))
            .accessibilityIdentifier(
                AccessibilityIdentifier.alignmentCanvasPhase(segment.phase.title)
            )
        } else {
            segmentLabel(segment, isSelected: isSelected)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(segment.phase.title)
                .accessibilityValue(accessibilityValue(for: segment))
                .accessibilityIdentifier(
                    AccessibilityIdentifier.alignmentCanvasPhase(segment.phase.title)
                )
        }
    }

    private func segmentLabel(
        _ segment: AlignmentCanvasSegment,
        isSelected: Bool
    ) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            // The phase's visual weight now lives in the drawn field above.
            // This row carries the semantics the field cannot: an accessible
            // name, a non-colour status symbol, and explicit status text.
            HStack(spacing: AuralignVisualSystem.spacingXS) {
                Image(systemName: statusSymbol(for: segment))
                    .font(.caption2)
                    .foregroundStyle(symbolColor(for: segment))
                    .accessibilityHidden(true)
                Text(segment.phase.title)
                    .font(AuralignType.metadataStrong)
                    .foregroundStyle(
                        segment.status == .skipped ? .secondary : .primary
                    )
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Status is always carried as text, never by color alone.
            Text(segment.status.label)
                .font(AuralignType.metadata)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, AuralignVisualSystem.spacingXS)
        .padding(.horizontal, AuralignVisualSystem.spacingXS)
        .background {
            if isSelected {
                RoundedRectangle(
                    cornerRadius: AuralignRadius.inner(
                        of: AuralignRadius.canvas,
                        inset: AuralignVisualSystem.spacingS
                    )
                )
                .fill(Color.accentColor.opacity(increaseContrast ? 0.22 : 0.12))
            }
        }
        .contentShape(Rectangle())
    }

    private func accessibilityValue(for segment: AlignmentCanvasSegment) -> String {
        var value = segment.status.label
        if !segment.evidence.isEmpty {
            value += ". " + segment.evidence.joined(separator: ". ")
        }
        return value
    }

    private func statusSymbol(for segment: AlignmentCanvasSegment) -> String {
        switch segment.status {
        case .complete: return "checkmark.circle.fill"
        case .active: return "circle.inset.filled"
        case .pending: return "circle"
        case .halted: return "minus.circle"
        case .skipped: return "slash.circle"
        }
    }

    private func symbolColor(for segment: AlignmentCanvasSegment) -> Color {
        if increaseContrast || differentiateWithoutColor { return .primary }
        if controlActiveState == .inactive { return .secondary }
        switch segment.status {
        case .complete, .active: return segment.phase.spectrumStop.color
        case .pending: return segment.phase.spectrumStop.color.opacity(0.75)
        case .halted, .skipped: return .secondary
        }
    }

    // MARK: Caption

    /// States the visual's categorical boundary so a reader never mistakes
    /// the phase map for a metric timeline or literal execution order.
    @ViewBuilder
    private var caption: some View {
        HStack(spacing: AuralignVisualSystem.spacingXS) {
            Text("Phase map — position and width do not show execution order or duration.")
            .font(AuralignType.metadata)
            .foregroundStyle(.tertiary)

            Spacer(minLength: AuralignVisualSystem.spacingS)

            if let text = projection.sourceDurationText {
                Text(text)
                    .font(AuralignType.numericSmall)
                    .foregroundStyle(.tertiary)
                    .accessibilityLabel("Source duration \(text)")
            }
        }
    }
}
