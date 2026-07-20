// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// Task-level configuration destination over the unchanged engine/catalog
/// section IDs. The group is navigation-only; ConfigSectionView remains the
/// exact field, validation, dependency, and reset authority.
struct PipelineConfigurationView: View {
    @EnvironmentObject private var model: AppModel
    let group: PipelineNavigationGroup
    let sectionID: String

    private var sections: [FieldCatalog.Section] {
        group.sectionIDs.compactMap(model.catalog.section(_:))
    }

    var body: some View {
        VStack(spacing: 0) {
            if sections.count > 1 {
                HStack {
                    Picker(group.title, selection: sectionBinding) {
                        ForEach(sections) { section in
                            Text(section.title).tag(section.id)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.segmented)
                    .accessibilityLabel("\(group.title) section")
                    .accessibilityIdentifier(
                        AccessibilityIdentifier.navigation(
                            "pipeline.\(group.rawValue).section"
                        )
                    )
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, AuralignVisualSystem.spacingXL)
                .padding(.vertical, AuralignVisualSystem.spacingS)
                .background(.bar)
                Divider()
            }
            ConfigSectionView(sectionID: sectionID)
        }
        .navigationTitle(group.title)
    }

    private var sectionBinding: Binding<String> {
        Binding(
            get: { sectionID },
            set: { selectedID in
                guard group.sectionIDs.contains(selectedID) else { return }
                model.selectedSection = .configuration(selectedID)
            }
        )
    }
}
