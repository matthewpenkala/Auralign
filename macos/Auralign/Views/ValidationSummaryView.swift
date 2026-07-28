// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// One global, recovery-first projection of Python-authoritative
/// configuration validation. It never repairs values; every row's only
/// action routes to the exact retained editor draft and inline error —
/// auto-disclosing a collapsed Advanced section when the governing field
/// lives there.
struct ValidationSummaryView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
            summaryBanner
            if !model.config.fieldErrors.isEmpty {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                    ForEach(
                        Array(model.config.fieldErrors.enumerated()),
                        id: \.element.id
                    ) { index, error in
                        issueRow(error, index: index)
                    }
                }
            }
        }
        .accessibilityIdentifier(AccessibilityIdentifier.validationSummary)
    }

    @ViewBuilder
    private var summaryBanner: some View {
        if model.config.isValidating {
            AuralignCausalityBanner(
                headline: "Checking configuration",
                detail: "Waiting for the local Python configuration authority.",
                tone: .neutral,
                symbol: "ellipsis.circle"
            )
        } else if let validationError = model.config.validationError {
            AuralignCausalityBanner(
                headline: "Configuration evidence unavailable",
                detail: validationError,
                tone: .failure
            )
        } else if !model.config.fieldErrors.isEmpty {
            let count = model.config.fieldErrors.count
            AuralignCausalityBanner(
                headline: count == 1
                    ? "1 configuration issue is blocking Start"
                    : "\(count) configuration issues are blocking Start",
                detail: "Each issue below is caused by an exact field. Open one to "
                    + "fix it in place; invalid text is preserved.",
                tone: .failure,
                linkTitle: "Show First Issue",
                linkIdentifier: AccessibilityIdentifier.firstValidationError,
                link: { _ = model.navigateToFirstConfigurationError() }
            )
        }
    }

    private func issueRow(_ error: ConfigFieldError, index: Int) -> some View {
        AuralignMediaRow(
            title: fieldTitle(for: error),
            metadata: model.privacySafePresentationMessage(error.message),
            swatch: auralignMediaRowSwatch(
                symbol: "exclamationmark.triangle.fill",
                tint: AuralignStatusTone.failure.color
            ),
            allowsMultiline: true,
            trailing: {
                Image(systemName: "chevron.forward")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            },
            action: { _ = model.navigateToConfigurationError(error) }
        )
        // Structural identifier only: the dotted schema location, never the
        // engine's message text, which may echo diagnostic content.
        .accessibilityIdentifier(
            "validation.summary.issue.\(index)."
                + stableIdentifierComponent(error.dottedLocation)
        )
    }

    private func fieldTitle(for error: ConfigFieldError) -> String {
        let location = error.dottedLocation
        if let entry = model.catalog.fieldsByKey[location] {
            return entry.title
        }
        if let entry = model.catalog.fields.first(
            where: { location.hasPrefix($0.key + ".") }
        ) {
            return entry.title
        }
        if let section = model.catalog.section(location) {
            return section.title
        }
        return location
    }

    /// Follows the same non-user-content sanitisation pattern as
    /// `AccessibilityIdentifier.stableComponent` — reimplemented locally
    /// since that helper is private to its own file.
    private func stableIdentifierComponent(_ value: String) -> String {
        value.unicodeScalars.map { scalar in
            CharacterSet.alphanumerics.contains(scalar) ? String(scalar) : "-"
        }.joined()
    }
}
