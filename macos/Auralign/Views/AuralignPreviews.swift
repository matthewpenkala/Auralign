// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

#if DEBUG

import SwiftUI

/// Named previews exercise the same inert production models used by UI tests.
/// They never contact Python, inspect real files, or read standard user
/// defaults. App ownership is granted explicitly because previews do not run
/// through `AuralignApplicationDelegate`.
@MainActor
private func previewMain(
    _ fixture: PresentationFixture,
    section: WorkspaceSection? = nil,
    width: CGFloat = 1_180,
    height: CGFloat = 780
) -> some View {
    let model = AppModel.makePresentationFixture(fixture)
    _ = model.activateForApplicationOwnership()
    if let section {
        model.selectedSection = section
    }
    return VStack(spacing: 0) {
        PresentationFixtureBanner(fixture: fixture)
        MainView()
    }
    .environmentObject(model)
    .modifier(AuralignAccessibilityEnvironmentModifier(environment: [:]))
    .frame(width: width, height: height)
}

@MainActor
private func previewSettings(
    _ fixture: PresentationFixture,
    width: CGFloat = 620,
    height: CGFloat = 420
) -> some View {
    let model = AppModel.makePresentationFixture(fixture)
    _ = model.activateForApplicationOwnership()
    return VStack(spacing: 0) {
        PresentationFixtureBanner(fixture: fixture)
        SettingsView()
    }
    .environmentObject(model)
    .modifier(AuralignAccessibilityEnvironmentModifier(environment: [:]))
    .frame(width: width, height: height)
}

#Preview("New Job — Empty — 900×600") {
    previewMain(.noInput, width: 900, height: 600)
}

#Preview("New Job — Selected — 1440×900") {
    previewMain(.selectedRunPlan, width: 1_440, height: 900)
}

#Preview("New Job — Long Text — 900×600") {
    previewMain(.longText, width: 900, height: 600)
}

#Preview("Parameter Library — 113 Fields") {
    previewMain(.selectedRunPlan, section: .allParameters)
}

#Preview("Hidden Advanced Error Recovery — 900×600") {
    previewMain(.invalidHiddenAdvanced, width: 900, height: 600)
}

#Preview("Activity — Running") {
    previewMain(.running)
}

#Preview("Activity — Stopping") {
    previewMain(.stopping)
}

#Preview("Activity — Result Pending Exit") {
    previewMain(.resultPendingExit)
}

#Preview("Activity — Failed — 900×600") {
    previewMain(.failure, width: 900, height: 600)
}

#Preview("Results — Transcript and Artifacts") {
    previewMain(.resultsRich)
}

#Preview("Results — Stale") {
    previewMain(.resultsStale)
}

#Preview("Settings — Valid") {
    previewSettings(.settingsValid)
}

#Preview("Settings — Invalid") {
    previewSettings(.settingsInvalid)
}

#Preview("Settings — Locked") {
    previewSettings(.settingsLocked)
}

#Preview("Fatal Bootstrap Recovery — 900×600") {
    previewMain(.resourceFailure, width: 900, height: 600)
}

#endif
