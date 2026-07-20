// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// One deterministic request to reveal and focus an exact configuration
/// control. AppModel resolves Python error locations against the complete
/// catalog; the destination view performs disclosure, scroll, keyboard focus,
/// accessibility focus, and announcement.
struct FieldNavigationRequest: Equatable, Identifiable {
    let id: Int
    let sectionID: String
    let fieldKey: String
    let message: String
    let revealAdvanced: Bool
    let returnSection: WorkspaceSection?
}

/// A deterministic fallback for an authoritative validation error that names
/// a configuration section but not an exact schema field. The destination
/// focuses the visible section error before announcing it; it never invents a
/// field target.
struct SectionNavigationRequest: Equatable, Identifiable {
    let id: Int
    let sectionID: String
    let message: String
    let returnSection: WorkspaceSection?
}
