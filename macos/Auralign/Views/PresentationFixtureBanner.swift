// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

#if DEBUG

import SwiftUI

/// Makes synthetic presentation evidence impossible to mistake for a live
/// bridge, readiness, transcription, or Results claim.
struct PresentationFixtureBanner: View {
    let fixture: PresentationFixture

    var body: some View {
        HStack(spacing: AuralignVisualSystem.spacingS) {
            Image(systemName: "testtube.2")
                .accessibilityHidden(true)
            Text(PresentationFixture.evidenceLabel)
                .font(.caption.weight(.semibold))
            Spacer()
            Text(fixture.rawValue)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, AuralignVisualSystem.spacingM)
        .padding(.vertical, AuralignVisualSystem.spacingS)
        .foregroundStyle(.orange)
        .background(Color.orange.opacity(0.12))
        .overlay(alignment: .bottom) {
            Divider()
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(PresentationFixture.evidenceLabel)
        .accessibilityValue(fixture.rawValue)
        .accessibilityIdentifier("fixture.synthetic-evidence")
    }
}

/// Keeps production-view evidence visibly synthetic without changing the
/// content geometry being measured at the required window sizes.
struct PresentationFixtureToolbarItem: ToolbarContent {
    let fixture: PresentationFixture

    var body: some ToolbarContent {
        ToolbarItem(placement: .secondaryAction) {
            HStack(spacing: AuralignVisualSystem.spacingXS) {
                Image(systemName: "testtube.2")
                    .accessibilityHidden(true)
                Text("Synthetic")
            }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.orange)
                .fixedSize()
                .help(
                    "\(PresentationFixture.evidenceLabel): \(fixture.rawValue)"
                )
                .accessibilityLabel(
                    "\(PresentationFixture.evidenceLabel), \(fixture.rawValue)"
                )
                .accessibilityIdentifier("fixture.synthetic-evidence")
        }
    }
}

struct InvalidFixtureRequestBanner: View {
    var body: some View {
        HStack(spacing: AuralignVisualSystem.spacingS) {
            Image(systemName: "testtube.2")
                .accessibilityHidden(true)
            Text(PresentationFixture.evidenceLabel)
                .font(.caption.weight(.semibold))
            Spacer()
            Text("invalid request — live startup blocked")
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, AuralignVisualSystem.spacingM)
        .padding(.vertical, AuralignVisualSystem.spacingS)
        .foregroundStyle(.red)
        .background(Color.red.opacity(0.10))
        .overlay(alignment: .bottom) {
            Divider()
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(PresentationFixture.evidenceLabel)
        .accessibilityValue("Invalid request; live startup blocked")
        .accessibilityIdentifier("fixture.invalid-request")
    }
}

#endif
