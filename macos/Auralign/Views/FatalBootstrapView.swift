// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import SwiftUI

/// Dedicated fail-closed presentation for missing or malformed bundled
/// configuration resources. No partial manifest or field catalog is rendered.
struct FatalBootstrapView: View {
    let detail: String

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                AuralignStatusBanner(
                    title: "Auralign could not load its configuration resources",
                    message:
                        "The application is blocked so an incomplete schema can "
                        + "never be edited or sent to the engine.",
                    tone: .failure,
                    symbol: "exclamationmark.shield.fill"
                )
                .accessibilityAddTraits(.isHeader)

                AuralignCausalityBanner(
                    headline: "Safe recovery",
                    detail:
                        "Quit Auralign, verify the repository checkout, regenerate "
                        + "the bundled manifest and field catalog, then rebuild the app.",
                    tone: .neutral,
                    symbol: "arrow.clockwise.circle"
                )

                Text(detail)
                    .font(AuralignType.technical)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(AuralignVisualSystem.spacingM)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(AuralignSurface.inset)
                    .overlay {
                        RoundedRectangle(cornerRadius: AuralignRadius.group)
                            .stroke(AuralignSurface.hairline)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.group))

                Button("Quit Auralign") {
                    NSApp.terminate(nil)
                }
                .keyboardShortcut(.defaultAction)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .padding(AuralignVisualSystem.spacingXL)
            .frame(maxWidth: 520)
            .frame(maxWidth: .infinity, minHeight: 552, alignment: .center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(AuralignStudioBackdrop())
        .accessibilityIdentifier("bootstrap.resource-failure")
    }
}
