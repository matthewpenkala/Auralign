// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit

/// Posts sparse, authoritative state changes to assistive technologies.
///
/// Callers provide a stable, nonsensitive token so repeated view invalidations
/// cannot replay the same announcement. Visible labels and focus remain the
/// primary presentation; this service is reserved for state changes that would
/// otherwise be missed.
@MainActor
final class AccessibilityAnnouncementCenter {
    static let shared = AccessibilityAnnouncementCenter()

    private var lastToken: String?

    private init() {}

    func announce(
        _ message: String,
        token: String,
        priority: NSAccessibilityPriorityLevel = .medium
    ) {
        guard token != lastToken else { return }
        guard let window = NSApp.keyWindow
            ?? NSApp.mainWindow
            ?? NSApp.windows.first(where: \.isVisible)
        else {
            return
        }
        lastToken = token
        NSAccessibility.post(
            element: window,
            notification: .announcementRequested,
            userInfo: [
                .announcement: message,
                .priority: priority.rawValue,
            ]
        )
    }

    func reset() {
        lastToken = nil
    }
}
