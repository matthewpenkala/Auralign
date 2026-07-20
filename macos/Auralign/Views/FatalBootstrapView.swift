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
            VStack(spacing: AuralignVisualSystem.spacingL) {
                Image(systemName: "exclamationmark.shield.fill")
                    .font(.system(size: 42))
                    .foregroundStyle(.red)
                    .accessibilityHidden(true)

                VStack(spacing: AuralignVisualSystem.spacingS) {
                    Text("Auralign could not load its configuration resources")
                        .font(.title2.weight(.semibold))
                        .multilineTextAlignment(.center)
                        .accessibilityAddTraits(.isHeader)
                    Text(
                        "The application is blocked so an incomplete schema can never "
                            + "be edited or sent to the engine."
                    )
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                }

                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                    Text("Safe recovery")
                        .font(.headline)
                        .accessibilityAddTraits(.isHeader)
                    Text(
                        "Quit Auralign, verify the repository checkout, regenerate the "
                            + "bundled manifest and field catalog, then rebuild the app."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    Text(detail)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(AuralignVisualSystem.spacingL)
                .frame(maxWidth: 520, alignment: .leading)
                .background(Color(nsColor: .controlBackgroundColor))
                .overlay {
                    RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius)
                        .stroke(Color(nsColor: .separatorColor))
                }
                .clipShape(RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius))

                Button("Quit Auralign") {
                    NSApp.terminate(nil)
                }
                .keyboardShortcut(.defaultAction)
            }
            .padding(AuralignVisualSystem.spacingXL)
            .frame(maxWidth: .infinity, minHeight: 552)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .accessibilityIdentifier("bootstrap.resource-failure")
    }
}
