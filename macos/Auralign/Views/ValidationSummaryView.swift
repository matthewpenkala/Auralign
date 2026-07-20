// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// One global, recovery-first projection of Python-authoritative
/// configuration validation. It never repairs values; its only action routes
/// to the exact retained editor draft and inline error.
struct ValidationSummaryView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            if model.config.isValidating {
                AuralignStatusBanner(
                    title: "Checking configuration",
                    message: "Waiting for the local Python configuration authority.",
                    tone: .neutral,
                    symbol: "ellipsis.circle"
                )
            } else if let validationError = model.config.validationError {
                AuralignStatusBanner(
                    title: "Configuration evidence unavailable",
                    message: validationError,
                    tone: .failure
                )
            } else if !model.config.fieldErrors.isEmpty {
                let count = model.config.fieldErrors.count
                AuralignStatusBanner(
                    title: count == 1
                        ? "1 configuration issue"
                        : "\(count) configuration issues",
                    message: "Review the first exact field. Invalid text is preserved.",
                    tone: .failure,
                    actionTitle: "Show First Issue",
                    actionIdentifier: AccessibilityIdentifier.firstValidationError,
                    action: {
                        _ = model.navigateToFirstConfigurationError()
                    }
                )
            }
        }
        .accessibilityIdentifier(AccessibilityIdentifier.validationSummary)
    }
}
