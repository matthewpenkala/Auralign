// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// Removes known machine-local locations from user-facing status copy while
/// retaining the actionable cause. This is presentation-only redaction; the
/// engine and its diagnostics remain authoritative and local.
enum PrivacySafePresentation {
    static func message(
        _ rawMessage: String,
        replacing locations: [(url: URL?, label: String)]
    ) -> String {
        var message = rawMessage
        let concreteLocations = locations.compactMap { location -> (URL, String)? in
            guard let url = location.url else { return nil }
            return (url, location.label)
        }
        .sorted { $0.0.path.count > $1.0.path.count }

        for (url, label) in concreteLocations {
            let candidates = Set([
                url.path,
                url.standardizedFileURL.path,
                url.absoluteString,
            ])
            for candidate in candidates where !candidate.isEmpty {
                message = message.replacingOccurrences(of: candidate, with: label)
            }
        }

        let home = FileManager.default.homeDirectoryForCurrentUser.path
        if !home.isEmpty {
            message = message.replacingOccurrences(of: home, with: "~")
        }
        return message
    }
}
