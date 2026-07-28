// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// Explicit, native acquisition of the exact signed-catalog model packs.
/// Closing this window never cancels an active transfer.
struct ModelSetupView: View {
    @ObservedObject var controller: ModelSetupController
    @Environment(\.openURL) private var openURL
    @Environment(\.auralignReduceMotion) private var reduceMotion
    @State private var cancelPackID: String?
    @FocusState private var primaryActionFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
                header
                if let catalogProblem = controller.catalogProblem {
                    AuralignStatusBanner(
                        title: "Model setup unavailable",
                        message: catalogProblem,
                        tone: .failure,
                        symbol: "exclamationmark.triangle.fill"
                    )
                } else {
                    summary
                    if !controller.requiredPacks.isEmpty {
                        packSection(
                            title: "Required for normal presets",
                            explanation:
                                "Install these exact packs once to use Max, Balanced, "
                                + "Fast, and CPU processing offline.",
                            packs: controller.requiredPacks
                        )
                    }
                    if !controller.optionalPacks.isEmpty {
                        packSection(
                            title: "Optional model packs",
                            explanation:
                                "Install only the advanced models you intend to use. "
                                + "Their availability remains visible and truthful.",
                            packs: controller.optionalPacks
                        )
                    }
                    privacyPosture
                }
            }
            .padding(AuralignVisualSystem.spacingXL)
            .frame(maxWidth: 960, alignment: .topLeading)
            .frame(maxWidth: .infinity, alignment: .top)
        }
        .frame(minWidth: 720, minHeight: 560)
        .background(AuralignStudioBackdrop())
        .navigationTitle("Complete Offline Setup")
        .accessibilityIdentifier(AccessibilityIdentifier.modelSetupRoot)
        .confirmationDialog(
            "Discard this incomplete download?",
            isPresented: Binding(
                get: { cancelPackID != nil },
                set: { if !$0 { cancelPackID = nil } }
            )
        ) {
            Button("Discard Incomplete Download", role: .destructive) {
                if let cancelPackID {
                    controller.cancel(packID: cancelPackID)
                }
                cancelPackID = nil
            }
            Button("Keep Download", role: .cancel) {
                cancelPackID = nil
            }
        } message: {
            Text(
                "Auralign will remove only this model pack’s task-owned partial "
                    + "files and resume state. Installed packs and user data remain."
            )
        }
        .task {
            controller.restore()
            await Task.yield()
            primaryActionFocused = true
        }
    }

    private var header: some View {
        AuralignPageHeader(
            title: "Complete Offline Setup",
            subtitle:
                "Download only exact, pinned model data. Auralign verifies every "
                + "file before installation; transcription remains local and offline.",
            symbol: "arrow.down.circle"
        ) {
            if let activePack = controller.packs.first(where: { $0.phase.isActive }) {
                let statusText =
                    "\(activePack.phase.label) \(activePack.displayName) — "
                    + activePack.progressDescription
                if reduceMotion {
                    Label(statusText, systemImage: activePack.phase.symbol)
                        .font(AuralignType.numericSmall)
                        .foregroundStyle(.secondary)
                } else {
                    HStack(spacing: AuralignVisualSystem.spacingS) {
                        ProgressView(value: activePack.fractionComplete)
                            .frame(width: 120)
                        Text(statusText)
                            .font(AuralignType.numericSmall)
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(statusText)
                }
            }
        }
    }

    private var summary: some View {
        AuralignSection(
            title: controller.needsOfflineSetup
                ? "Setup is incomplete" : "Offline setup complete"
        ) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: AuralignVisualSystem.spacingL) {
                    summaryFacts
                    Spacer(minLength: AuralignVisualSystem.spacingL)
                    primaryAction
                }
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                    summaryFacts
                    primaryAction
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(AccessibilityIdentifier.modelSetupSummary)
    }

    private var summaryFacts: some View {
        Text(
            "\(controller.requiredPacks.filter { $0.phase == .installed }.count) "
                + "of \(controller.requiredPacks.count) required packs installed · "
                + ByteCountFormatter.string(
                    fromByteCount: controller.totalRequiredBytes,
                    countStyle: .file
                )
                + " total"
        )
        .font(AuralignType.numeric)
        .foregroundStyle(.secondary)
    }

    @ViewBuilder
    private var primaryAction: some View {
        if controller.needsOfflineSetup {
            Button {
                controller.installRequired()
            } label: {
                Label(
                    controller.requiredPacks.contains(where: { $0.phase == .paused })
                        ? "Resume Setup" : "Install Required Models",
                    systemImage: "arrow.down.circle.fill"
                )
            }
            .buttonStyle(.borderedProminent)
            .disabled(
                controller.hasActiveDownloads
                    || controller.requiredPacks.allSatisfy {
                        $0.phase == .installed
                    }
            )
            .focused($primaryActionFocused)
            .accessibilityHint(
                "Downloads exact signed-catalog model files and verifies them "
                    + "before making them available."
            )
            .accessibilityIdentifier(AccessibilityIdentifier.modelSetupInstallRequired)
        } else {
            Label("All required packs verified", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .accessibilityIdentifier(AccessibilityIdentifier.modelSetupComplete)
        }
    }

    private func packSection(
        title: String,
        explanation: String,
        packs: [ModelPackSnapshot]
    ) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(title)
                    .font(AuralignType.workspaceTitle)
                    .accessibilityAddTraits(.isHeader)
                Text(explanation)
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
            }
            ForEach(packs) { pack in
                packRow(pack)
            }
        }
    }

    private func packRow(_ pack: ModelPackSnapshot) -> some View {
        AuralignSection(title: pack.displayName, subtitle: pack.description) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
                    Text(
                        "Quality: \(pack.quality) · Speed: \(pack.speed) · "
                            + "Memory: \(pack.memory)"
                    )
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
                    Spacer(minLength: AuralignVisualSystem.spacingM)
                    VStack(alignment: .trailing, spacing: AuralignVisualSystem.spacingXS) {
                        Label(pack.phase.label, systemImage: pack.phase.symbol)
                            .font(AuralignType.bodyEmphasis)
                            .foregroundStyle(tone(for: pack.phase).color)
                        Text(
                            ByteCountFormatter.string(
                                fromByteCount: pack.totalBytes,
                                countStyle: .file
                            )
                        )
                        .font(AuralignType.numericSmall)
                        .foregroundStyle(.secondary)
                    }
                }

                if pack.phase.isActive || pack.phase == .paused {
                    ProgressView(value: pack.fractionComplete)
                        .progressViewStyle(.linear)
                        .accessibilityLabel("\(pack.displayName) download progress")
                        .accessibilityValue(
                            "\(Int((pack.fractionComplete * 100).rounded())) percent, "
                                + pack.progressDescription
                        )
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.modelSetupProgress(pack.id)
                        )
                    Text(pack.progressDescription)
                        .font(AuralignType.numericSmall)
                        .foregroundStyle(.secondary)
                }

                if case .repairRequired(let reason) = pack.phase {
                    problemText(reason)
                } else if case .failed(let reason) = pack.phase {
                    problemText(reason)
                }

                HStack(spacing: AuralignVisualSystem.spacingS) {
                    actionControls(pack)
                    Spacer()
                    if let source = pack.sourceURLs.first,
                       let sourceURL = URL(string: source) {
                        Button("Source") { openURL(sourceURL) }
                            .buttonStyle(.link)
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.modelSetupSource(pack.id)
                            )
                    }
                    Text(pack.licenseExpressions.joined(separator: ", "))
                        .font(AuralignType.technical)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(
                            "License \(pack.licenseExpressions.joined(separator: ", "))"
                        )
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(pack.accessibilityLabel)
        .accessibilityValue(
            "\(pack.phase.label). \(pack.progressDescription)."
        )
        .accessibilityIdentifier(AccessibilityIdentifier.modelSetupPack(pack.id))
    }

    @ViewBuilder
    private func actionControls(_ pack: ModelPackSnapshot) -> some View {
        switch pack.phase {
        case .notInstalled:
            Button("Install") { controller.install(packID: pack.id) }
                .accessibilityIdentifier(
                    AccessibilityIdentifier.modelSetupInstall(pack.id)
                )
        case .queued, .preparing, .downloading:
            Button("Pause") { controller.pause(packID: pack.id) }
                .accessibilityIdentifier(
                    AccessibilityIdentifier.modelSetupPause(pack.id)
                )
            Button("Cancel", role: .destructive) {
                cancelPackID = pack.id
            }
            .accessibilityIdentifier(
                AccessibilityIdentifier.modelSetupCancel(pack.id)
            )
        case .paused:
            Button("Resume") { controller.resume(packID: pack.id) }
                .accessibilityIdentifier(
                    AccessibilityIdentifier.modelSetupResume(pack.id)
                )
            Button("Cancel", role: .destructive) {
                cancelPackID = pack.id
            }
            .accessibilityIdentifier(
                AccessibilityIdentifier.modelSetupCancel(pack.id)
            )
        case .verifying, .publishing:
            Text("Verification cannot be paused.")
                .font(AuralignType.metadata)
                .foregroundStyle(.secondary)
        case .installed:
            Label("Verified", systemImage: "checkmark.shield.fill")
                .foregroundStyle(.green)
        case .repairRequired, .failed:
            Button("Retry") { controller.retry(packID: pack.id) }
                .accessibilityIdentifier(
                    AccessibilityIdentifier.modelSetupRetry(pack.id)
                )
        }
    }

    private func problemText(_ message: String) -> some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(AuralignType.body)
            .foregroundStyle(.red)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var privacyPosture: some View {
        Label {
            Text(
                "Downloads occur only after an explicit setup action. Auralign "
                    + "does not upload media, send telemetry, or use the network "
                    + "during normal transcription."
            )
        } icon: {
            Image(systemName: "lock.shield")
        }
        .font(AuralignType.body)
        .foregroundStyle(.secondary)
        .accessibilityIdentifier(AccessibilityIdentifier.modelSetupPrivacy)
    }

    private func tone(for phase: ModelPackPhase) -> AuralignStatusTone {
        switch phase {
        case .installed:
            return .success
        case .failed, .repairRequired:
            return .failure
        case .paused:
            return .warning
        case .queued, .preparing, .downloading, .verifying, .publishing:
            return .active
        case .notInstalled:
            return .neutral
        }
    }
}
