// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// Local engine and model-storage settings.
///
/// Debug exposes checkout selection for development. Release always uses the
/// signed, included engine and exposes only the separately acquired model
/// directory; the installer never downloads models or writes into the app.
struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Engine") {
#if DEBUG
                LabeledContent("Repository") {
                    VStack(alignment: .trailing, spacing: 8) {
                        Text(abbreviatedRepositoryPath)
                            .lineLimit(2)
                            .truncationMode(.middle)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                            .help(model.enginePresentationStatus.repositoryPath)
                            .accessibilityLabel(
                                "Engine repository, \(abbreviatedRepositoryPath)"
                            )
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.settingsRepository
                        )
                        HStack(spacing: 8) {
                            Button("Choose…") {
                                if let url = LocalFilePanel.chooseDirectory(
                                    message: "Choose the Auralign repository checkout"
                                ) {
                                    model.updateEngineRoot(url)
                                }
                            }
                            .accessibilityLabel("Choose engine repository")
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.settingsChoose
                            )
                            .help("Choose a local Auralign repository checkout.")
                            .disabled(
                                model.workMutationLocked || model.isPresentationFixture
                            )
                        if EngineLocation.embeddedRepositoryRoot() != nil {
                            Button("Use Build Default") {
                                model.updateEngineRoot(nil)
                            }
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.settingsBuildDefault
                            )
                            .help("Use the repository recorded when this Debug app was built.")
                            .disabled(
                                model.workMutationLocked || model.isPresentationFixture
                            )
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
#endif
                LabeledContent(engineEnvironmentLabel) {
                    AuralignStatusBadge(
                        text: model.enginePresentationStatus.message,
                        tone: model.enginePresentationStatus.isReady
                            ? .success : .failure,
                        symbol: model.enginePresentationStatus.isReady
                            ? "checkmark.circle.fill"
                            : "xmark.octagon.fill"
                    )
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier(
                        AccessibilityIdentifier.settingsRepositoryEnvironment
                    )
                }
                LabeledContent("Connection") {
                    AuralignStatusBadge(
                        text: model.engineConnectionPresentation.message,
                        tone: connectionTone,
                        symbol: connectionSymbol
                    )
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier(
                        AccessibilityIdentifier.settingsConnection
                    )
                }
#if DEBUG
                Text(
                    "The app runs the engine from this repository's uv-managed Python "
                        + "environment (.venv). This checkout selector is excluded from "
                        + "Release builds."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
#else
                Text(
                    "The signed application includes its local Python engine and media "
                        + "tools. It does not require a source checkout, Homebrew, or a "
                        + "system Python."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
#endif
                if model.workMutationLocked {
                    Label(
                        "Engine settings are locked until the owned engine "
                            + "process exits and is reaped.",
                        systemImage: "lock.fill"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier(
                        AccessibilityIdentifier.settingsLockReason
                    )
                }
            }

            Section("Models") {
                LabeledContent("Local Model Directory") {
                    VStack(alignment: .trailing, spacing: 8) {
                        Text(abbreviatedModelCachePath)
                            .lineLimit(2)
                            .truncationMode(.middle)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                            .help(model.engineLocation.modelCacheDisplayPath)
                            .accessibilityLabel(
                                "Local model directory, \(abbreviatedModelCachePath)"
                            )
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.settingsModelCache
                            )
                        HStack(spacing: 8) {
                            Button("Choose…") {
                                if let url = LocalFilePanel.chooseDirectory(
                                    message: "Choose the verified Auralign model directory"
                                ) {
                                    model.updateModelCache(url)
                                }
                            }
                            .accessibilityLabel("Choose local model directory")
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.settingsChooseModelCache
                            )
                            .help(
                                "Use a model directory prepared by the documented "
                                    + "connected-acquisition workflow."
                            )
                            .disabled(
                                model.workMutationLocked || model.isPresentationFixture
                            )

                            Button("Use Default") {
                                model.updateModelCache(nil)
                            }
                            .accessibilityIdentifier(
                                AccessibilityIdentifier.settingsDefaultModelCache
                            )
                            .help("Use Auralign's Application Support model directory.")
                            .disabled(
                                model.workMutationLocked || model.isPresentationFixture
                            )
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
                Text(
                    "Models remain external, user-owned, and separately verified. "
                        + "Auralign runs offline and never acquires gated assets during "
                        + "installation or application startup."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(minWidth: 420, idealWidth: 620, maxWidth: 760)
        .padding(.vertical, 8)
        .accessibilityIdentifier(AccessibilityIdentifier.settingsRoot)
    }

    private var abbreviatedRepositoryPath: String {
        (model.enginePresentationStatus.repositoryPath as NSString)
            .abbreviatingWithTildeInPath
    }

    private var engineEnvironmentLabel: String {
#if DEBUG
        return "Repository Environment"
#else
        return "Runtime"
#endif
    }

    private var abbreviatedModelCachePath: String {
        (model.engineLocation.modelCacheDisplayPath as NSString)
            .abbreviatingWithTildeInPath
    }

    private var connectionTone: AuralignStatusTone {
        switch model.engineConnectionPresentation.state {
        case .notConnected:
            return .neutral
        case .connecting:
            return .active
        case .connected:
            return .success
        case .failed:
            return .failure
        }
    }

    private var connectionSymbol: String {
        switch model.engineConnectionPresentation.state {
        case .notConnected:
            return "bolt.slash"
        case .connecting:
            return "clock"
        case .connected:
            return "bolt.fill"
        case .failed:
            return "xmark.octagon.fill"
        }
    }
}
