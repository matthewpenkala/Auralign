// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// A rejected readiness response, classified without retaining private paths,
/// commands, environment values, or backend diagnostic bodies.
struct ReadinessEvidenceFailure: Error, Equatable, LocalizedError {
    enum Source: String, Equatable {
        case doctor
        case models

        var label: String {
            switch self {
            case .doctor: return "Runtime checks"
            case .models: return "Model evidence"
            }
        }
    }

    enum Kind: String, Equatable {
        case unavailable
        case partial
        case malformed
        case inconsistent

        var label: String {
            switch self {
            case .unavailable: return "unavailable"
            case .partial: return "incomplete"
            case .malformed: return "malformed"
            case .inconsistent: return "inconsistent"
            }
        }
    }

    let source: Source
    let kind: Kind
    let summary: String
    let technicalDetail: String?

    var errorDescription: String? { summary }
}

/// One immutable, user-facing projection over the accepted engine,
/// configuration, doctor, and model evidence. It is presentation state only;
/// Python and the strict parsers remain the readiness authorities.
struct ReadinessDecision: Equatable {
    enum Recovery: Equatable {
        case none
        case refresh
        case openSettings
        case showFirstConfigurationIssue
        case completeOfflineSetup(String?)
        case showModel(String)
        case showCheck(String)
    }

    let level: AppModel.ReadinessLevel
    let headline: String
    let explanation: String
    let cause: String?
    let recovery: Recovery
    /// Structural and privacy-safe. Used only to deduplicate accessibility
    /// announcements as accepted evidence revisions change.
    let announcementToken: String
}
