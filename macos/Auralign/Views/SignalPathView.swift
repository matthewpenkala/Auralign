// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// One truthful step in Auralign's domain-specific signal path. Callers supply
/// only configuration-enabled or engine-reported steps.
struct SignalPathStep: Identifiable, Equatable {
    enum State: Equatable {
        case pending
        case active
        case completed
        case skipped
        case failed

        var tone: AuralignStatusTone {
            switch self {
            case .pending, .skipped:
                return .neutral
            case .active:
                return .active
            case .completed:
                return .success
            case .failed:
                return .failure
            }
        }

        var symbol: String {
            switch self {
            case .pending:
                return "circle"
            case .active:
                return "circle.inset.filled"
            case .completed:
                return "checkmark.circle.fill"
            case .skipped:
                return "minus.circle"
            case .failed:
                return "xmark.circle.fill"
            }
        }

        var accessibilityLabel: String {
            switch self {
            case .pending:
                return "Pending"
            case .active:
                return "Active"
            case .completed:
                return "Completed"
            case .skipped:
                return "Skipped"
            case .failed:
                return "Failed"
            }
        }
    }

    let id: String
    let title: String
    let shortTitle: String
    let state: State

    init(id: String, title: String, shortTitle: String? = nil, state: State) {
        self.id = id
        self.title = title
        self.shortTitle = shortTitle ?? title
        self.state = state
    }
}

/// Adaptive overview of the real input-to-delivery workflow. It becomes a
/// compact vertical ledger instead of clipping or horizontally scrolling at
/// the minimum window size.
struct SignalPathView: View {
    let steps: [SignalPathStep]

    var body: some View {
        ViewThatFits(in: .horizontal) {
            horizontalPath
            compactPath
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Auralign signal path")
    }

    private var horizontalPath: some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingS) {
            ForEach(Array(steps.enumerated()), id: \.element.id) { index, step in
                horizontalStep(step)
                if index < steps.count - 1 {
                    Rectangle()
                        .fill(connectorColor(after: step))
                        .frame(minWidth: 18, maxWidth: 42, minHeight: 2, maxHeight: 2)
                        .padding(.top, 9)
                        .accessibilityHidden(true)
                }
            }
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private func horizontalStep(_ step: SignalPathStep) -> some View {
        VStack(spacing: AuralignVisualSystem.spacingXS) {
            Image(systemName: step.state.symbol)
                .foregroundStyle(step.state.tone.color)
                .font(.body.weight(.medium))
            Text(step.shortTitle)
                .font(.caption)
                .foregroundStyle(step.state == .skipped ? .secondary : .primary)
                .lineLimit(1)
        }
        .frame(minWidth: 58)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(step.title)
        .accessibilityValue(step.state.accessibilityLabel)
        .accessibilityIdentifier("activity.stage.\(step.id)")
    }

    private var compactPath: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.element.id) { index, step in
                HStack(spacing: AuralignVisualSystem.spacingS) {
                    VStack(spacing: 0) {
                        Image(systemName: step.state.symbol)
                            .foregroundStyle(step.state.tone.color)
                            .frame(width: 18, height: 18)
                            .accessibilityHidden(true)
                        if index < steps.count - 1 {
                            Rectangle()
                                .fill(connectorColor(after: step))
                                .frame(width: 2, height: 16)
                                .accessibilityHidden(true)
                        }
                    }
                    Text(step.title)
                        .font(.callout)
                    Spacer()
                    Text(step.state.accessibilityLabel)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("activity.stage.\(step.id)")
            }
        }
    }

    private func connectorColor(after step: SignalPathStep) -> Color {
        switch step.state {
        case .completed:
            return .green.opacity(0.55)
        case .active:
            return Color.accentColor.opacity(0.55)
        case .pending, .skipped, .failed:
            return Color(nsColor: .separatorColor)
        }
    }
}
