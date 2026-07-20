// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// One configuration domain: primary fields, an Advanced disclosure, and
/// section-level validation errors from the Python authority.
struct ConfigSectionView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.auralignReduceMotion) private var reduceMotion
    let sectionID: String
    @State private var advancedExpanded = false
    @State private var focusReadyRequestID: Int?
    @AccessibilityFocusState private var sectionErrorFocused: Bool

    private var section: FieldCatalog.Section? {
        model.catalog.section(sectionID)
    }

    private var entries: [FieldCatalog.Entry] {
        model.catalog.entries(inSection: sectionID)
    }

    private func fieldsFor(_ dispositions: Set<FieldCatalog.Disposition>) -> [(
        ConfigField, FieldCatalog.Entry
    )] {
        entries.compactMap { entry in
            guard dispositions.contains(entry.disposition),
                  let field = model.manifest.field(entry.key) else { return nil }
            return (field, entry)
        }
    }

    var body: some View {
        let primary = fieldsFor([.primary, .readonly])
        let advanced = fieldsFor([.advanced])
        let sectionErrors = model.config.sectionErrors(for: sectionID)
        let modifiedCount = entries.filter { model.config.isModified($0.key) }.count

        ScrollViewReader { scrollProxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    header(modifiedCount: modifiedCount)
                    if !sectionErrors.isEmpty {
                        sectionErrorSummary(sectionErrors)
                    }
                    if model.workMutationLocked {
                        Label(
                            "A job is running; configuration is read-only until it finishes.",
                            systemImage: "lock.fill"
                        )
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 20)
                        .padding(.bottom, 6)
                    }
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(primary, id: \.0.key) { field, entry in
                            FieldRowView(
                                config: model.config,
                                field: field,
                                entry: entry,
                                focusRequest: readyFocusRequest
                            )
                            .id(AccessibilityIdentifier.field(field.key))
                            if field.key != primary.last?.0.key {
                                Divider().opacity(0.4)
                            }
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 6)

                    if !advanced.isEmpty {
                        DisclosureGroup(isExpanded: $advancedExpanded) {
                            VStack(alignment: .leading, spacing: 0) {
                                ForEach(advanced, id: \.0.key) { field, entry in
                                    FieldRowView(
                                        config: model.config,
                                        field: field,
                                        entry: entry,
                                        focusRequest: readyFocusRequest
                                    )
                                    .id(AccessibilityIdentifier.field(field.key))
                                    if field.key != advanced.last?.0.key {
                                        Divider().opacity(0.4)
                                    }
                                }
                            }
                            .padding(.top, 4)
                        } label: {
                            Text("Advanced")
                                .font(.headline)
                        }
                        .padding(.horizontal, 20)
                        .padding(.vertical, 10)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.advancedGroup(sectionID: sectionID)
                        )
                    }
                }
                .padding(.bottom, 24)
            }
            .task(id: model.fieldNavigationRequest?.id) {
                guard let request = model.fieldNavigationRequest,
                      request.sectionID == sectionID,
                      model.consumedFieldNavigationRequestID != request.id
                else { return }
                focusReadyRequestID = nil
                if request.revealAdvanced {
                    advancedExpanded = true
                    await Task.yield()
                }
                await Task.yield()
                if reduceMotion {
                    scrollProxy.scrollTo(
                        AccessibilityIdentifier.field(request.fieldKey),
                        anchor: .center
                    )
                } else {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        scrollProxy.scrollTo(
                            AccessibilityIdentifier.field(request.fieldKey),
                            anchor: .center
                        )
                    }
                    try? await Task.sleep(nanoseconds: 220_000_000)
                }
                await Task.yield()
                focusReadyRequestID = request.id
            }
            .task(id: model.sectionNavigationRequest?.id) {
                guard let request = model.sectionNavigationRequest,
                      request.sectionID == sectionID,
                      model.consumedSectionNavigationRequestID != request.id
                else { return }
                await Task.yield()
                sectionErrorFocused = true
                await Task.yield()
                AccessibilityAnnouncementCenter.shared.announce(
                    "Configuration section error. \(request.message)",
                    token: "configuration-section-error-\(request.id)",
                    priority: .high
                )
                model.consumeSectionNavigationRequest(request.id)
            }
        }
        .navigationTitle(section?.title ?? sectionID)
        .toolbar {
            ToolbarItem(placement: .automatic) {
                HStack(spacing: 8) {
                    if let returnSection = activeReturnSection,
                       returnSection != .configuration(sectionID) {
                        Button(returnButtonTitle(for: returnSection)) {
                            model.returnFromConfigurationError()
                        }
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.configurationReturnToOrigin
                        )
                        .help(
                            "Return to the validation summary that opened this field."
                        )
                    }
                    if modifiedCount > 0 {
                        Button("Reset Section") {
                            model.config.clearSection(sectionID)
                        }
                        .help("Remove every override in this section.")
                        .disabled(model.workMutationLocked)
                    }
                }
            }
        }
    }

    private var readyFocusRequest: FieldNavigationRequest? {
        guard let request = model.fieldNavigationRequest,
              request.id == focusReadyRequestID
        else { return nil }
        return request
    }

    private var activeReturnSection: WorkspaceSection? {
        if let request = model.fieldNavigationRequest,
           request.sectionID == sectionID {
            return request.returnSection
        }
        if let request = model.sectionNavigationRequest,
           request.sectionID == sectionID {
            return request.returnSection
        }
        return nil
    }

    private func sectionErrorSummary(
        _ errors: [ConfigFieldError]
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(errors) { error in
                Label(
                    model.privacySafePresentationMessage(error.message),
                    systemImage: "exclamationmark.circle.fill"
                )
                .font(.callout)
                .foregroundStyle(.red)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 6)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(
            "\(errors.count) configuration section "
                + (errors.count == 1 ? "error" : "errors")
        )
        .accessibilityFocused($sectionErrorFocused)
        .accessibilityIdentifier("configuration.section-error.\(sectionID)")
    }

    private func header(modifiedCount: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if let section {
                    Image(systemName: section.symbol)
                        .foregroundStyle(Color.accentColor)
                    Text(section.title)
                        .font(.title3.weight(.semibold))
                        .accessibilityAddTraits(.isHeader)
                }
                if modifiedCount > 0 {
                    Text("\(modifiedCount) modified")
                        .font(.caption)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.accentColor.opacity(0.15), in: Capsule())
                }
                Spacer()
            }
            if let section {
                Text(section.summary)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .padding(.bottom, 8)
    }

    private func returnButtonTitle(for section: WorkspaceSection?) -> String {
        switch section {
        case .newJob, .none:
            return "Return to New Job"
        case .readiness:
            return "Return to Runtime & Models"
        case .allParameters:
            return "Return to Parameter Library"
        case .processing:
            return "Return to Activity"
        case .results:
            return "Return to Results"
        case .configuration:
            return "Return to Configuration"
        }
    }
}
