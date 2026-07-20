// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

enum ParameterLibraryFilter: String, CaseIterable, Identifiable {
    case all = "All"
    case modified = "Modified"
    case invalid = "Invalid"
    case advanced = "Advanced"
    case experimental = "Experimental"
    case readOnly = "Read-only"

    var id: String { rawValue }
}

struct ParameterLibraryRow: Identifiable {
    let field: ConfigField
    let entry: FieldCatalog.Entry

    var id: String { field.key }
}

enum ParameterLibraryProjection {
    static func matches(
        filter: ParameterLibraryFilter,
        entry: FieldCatalog.Entry,
        isModified: Bool,
        hasErrors: Bool
    ) -> Bool {
        switch filter {
        case .all:
            return true
        case .modified:
            return isModified
        case .invalid:
            return hasErrors
        case .advanced:
            return entry.disposition == .advanced
        case .experimental:
            return entry.isExperimental
        case .readOnly:
            return entry.disposition == .readonly
        }
    }

    static func searchCorpus(
        field: ConfigField,
        entry: FieldCatalog.Entry,
        manifest: ConfigManifest
    ) -> String {
        var values = [
            field.key,
            entry.title,
            entry.help,
        ]
        values.append(contentsOf: field.choices ?? [])
        if let role = field.modelRole {
            for modelEntry in manifest.modelChoices(role: role) {
                values.append(modelEntry.key)
                values.append(modelEntry.modelId)
                values.append(contentsOf: modelEntry.aliases ?? [])
            }
        }
        return values.joined(separator: "\n")
    }

    static func matchesSearch(
        query: String,
        field: ConfigField,
        entry: FieldCatalog.Entry,
        manifest: ConfigManifest
    ) -> Bool {
        let normalized = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return true }
        if manifest.field(normalized) != nil {
            return field.key == normalized
        }
        return searchCorpus(field: field, entry: entry, manifest: manifest)
            .localizedCaseInsensitiveContains(normalized)
    }
}

/// Native expert browser over every schema-derived field. Search and filters
/// only project the authoritative 112-row catalog; they never create a second
/// configuration model or drop fields from the editable surface.
struct AllParametersView: View {
    @EnvironmentObject private var model: AppModel
    @State private var searchText = ""
    @State private var filter: ParameterLibraryFilter = .all
    @State private var selectedCategoryID = Self.allCategoryID
    @State private var selectedKey: String?
    @State private var resultAnnouncementTask: Task<Void, Never>?

    private static let allCategoryID = "__all__"

    private var allRows: [ParameterLibraryRow] {
        model.catalog.fields.compactMap { entry in
            guard let field = model.manifest.field(entry.key) else { return nil }
            return ParameterLibraryRow(field: field, entry: entry)
        }
    }

    private var rows: [ParameterLibraryRow] {
        allRows.filter { row in
            categoryMatches(row)
                && filterMatches(row)
                && searchMatches(row)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            libraryHeader
            Divider()
            if !model.config.uncataloguedLiveFields.isEmpty {
                uncataloguedBanner
            }
            GeometryReader { geometry in
                if geometry.size.width >= 840 {
                    HStack(spacing: 0) {
                        categoryIndex
                            .frame(minWidth: 148, idealWidth: 160, maxWidth: 176)
                        Divider()
                        parameterList
                            .frame(minWidth: 440)
                        Divider()
                        parameterInspector
                            .frame(minWidth: 250, idealWidth: 270, maxWidth: 290)
                    }
                } else {
                    VStack(spacing: 0) {
                        compactCategoryPicker
                        Divider()
                        parameterList
                    }
                }
            }
        }
        .navigationTitle("Parameter Library")
        .task(id: rows.map(\.id)) {
            // A row projection can change while SwiftUI is reconciling either
            // List. Defer the dependent selection update to the task turn so
            // no @State is published from inside that view update.
            await Task.yield()
            normalizeSelection()
            scheduleResultCountAnnouncement()
        }
        .onDisappear {
            resultAnnouncementTask?.cancel()
            resultAnnouncementTask = nil
        }
    }

    private var libraryHeader: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
            AuralignPageHeader(
                title: "Parameter Library",
                subtitle: "Search and inspect all \(model.catalog.fields.count) "
                    + "schema-derived parameters without leaving the effective configuration.",
                symbol: "list.bullet.rectangle"
            )
            ViewThatFits(in: .horizontal) {
                HStack(spacing: AuralignVisualSystem.spacingM) {
                    searchField
                    filterPicker
                    Spacer(minLength: 0)
                    resultCount
                }
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                    searchField
                    HStack {
                        filterPicker
                        Spacer()
                        resultCount
                    }
                }
            }
        }
        .padding(.horizontal, AuralignVisualSystem.spacingXL)
        .padding(.vertical, AuralignVisualSystem.spacingM)
        .fixedSize(horizontal: false, vertical: true)
        .layoutPriority(1)
    }

    private var searchField: some View {
        TextField("Search label, help, key, or choice", text: $searchText)
            .textFieldStyle(.roundedBorder)
            .frame(minWidth: 220, idealWidth: 330, maxWidth: 430)
            .accessibilityIdentifier(AccessibilityIdentifier.parameterSearch)
    }

    private var filterPicker: some View {
        Picker("Filter", selection: $filter) {
            ForEach(ParameterLibraryFilter.allCases) { item in
                Text(item.rawValue).tag(item)
            }
        }
        .pickerStyle(.menu)
        .fixedSize()
        .accessibilityIdentifier(AccessibilityIdentifier.parameterFilter)
    }

    private var resultCount: some View {
        Text("\(rows.count) of \(allRows.count)")
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
            .accessibilityLabel("\(rows.count) of \(allRows.count) parameters")
            .accessibilityIdentifier(AccessibilityIdentifier.parameterCount)
    }

    private func scheduleResultCountAnnouncement() {
        resultAnnouncementTask?.cancel()
        let count = rows.count
        let total = allRows.count
        let token = [
            "parameter-results",
            selectedCategoryID,
            filter.rawValue,
            searchText,
            String(count),
            String(total),
        ].joined(separator: "|")
        resultAnnouncementTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            let message = count == 0
                ? "No parameters match the current search and filters."
                : "\(count) of \(total) parameters shown."
            AccessibilityAnnouncementCenter.shared.announce(
                message,
                token: token
            )
        }
    }

    private var categoryIndex: some View {
        List(selection: categorySelection) {
            Label("All Categories", systemImage: "square.grid.2x2")
                .tag(Self.allCategoryID)
            ForEach(model.catalog.sections) { section in
                Label(section.title, systemImage: section.symbol)
                    .tag(section.id)
            }
        }
        .listStyle(.sidebar)
        .accessibilityLabel("Parameter categories")
        .accessibilityIdentifier(AccessibilityIdentifier.parameterCategory)
    }

    private var compactCategoryPicker: some View {
        HStack {
            Picker("Category", selection: $selectedCategoryID) {
                Text("All Categories").tag(Self.allCategoryID)
                ForEach(model.catalog.sections) { section in
                    Text(section.title).tag(section.id)
                }
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier(AccessibilityIdentifier.parameterCategory)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, AuralignVisualSystem.spacingXL)
        .padding(.vertical, AuralignVisualSystem.spacingS)
        .background(.bar)
    }

    @ViewBuilder
    private var parameterList: some View {
        if rows.isEmpty {
            ContentUnavailableView {
                Label("No Matching Parameters", systemImage: "line.3.horizontal.decrease.circle")
            } description: {
                Text("Change the search, filter, or category to show schema fields.")
            } actions: {
                Button("Show All Parameters") {
                    searchText = ""
                    filter = .all
                    selectedCategoryID = Self.allCategoryID
                }
                .accessibilityIdentifier(
                    AccessibilityIdentifier.parameterShowAll
                )
            }
            .accessibilityIdentifier(AccessibilityIdentifier.parameterNoResults)
        } else {
            List(selection: $selectedKey) {
                ForEach(groupedSections, id: \.0) { sectionID, sectionRows in
                    Section {
                        ForEach(sectionRows) { row in
                            parameterRow(row)
                                .tag(row.id)
                        }
                    } header: {
                        Text(model.catalog.section(sectionID)?.title ?? sectionID)
                    }
                }
            }
            .listStyle(.inset)
            .accessibilityLabel("Parameters")
        }
    }

    private func parameterRow(_ row: ParameterLibraryRow) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            HStack(spacing: AuralignVisualSystem.spacingS) {
                Text(row.field.key)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Spacer(minLength: 0)
                if model.config.isModified(row.field.key) {
                    AuralignStatusBadge(
                        text: "Modified",
                        tone: .active,
                        symbol: "pencil.circle.fill"
                    )
                }
                if !model.config.errors(for: row.field.key).isEmpty {
                    AuralignStatusBadge(
                        text: "Invalid",
                        tone: .failure,
                        symbol: "exclamationmark.circle.fill"
                    )
                }
            }
            FieldRowView(
                config: model.config,
                field: row.field,
                entry: row.entry,
                compact: true
            )
        }
        .padding(.vertical, AuralignVisualSystem.spacingXS)
        .accessibilityIdentifier(AccessibilityIdentifier.parameterRow(row.field.key))
    }

    @ViewBuilder
    private var parameterInspector: some View {
        if let row = selectedRow {
            ScrollView {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                    Text(row.entry.title)
                        .font(.title3.weight(.semibold))
                        .accessibilityAddTraits(.isHeader)
                    Text(row.field.key)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                    Divider()
                    Text(row.entry.help)
                        .font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                    inspectorValue("Category", categoryTitle(row.entry.section))
                    inspectorValue("Type", row.field.kind.rawValue)
                    inspectorValue("Disposition", dispositionTitle(row.entry.disposition))
                    inspectorValue("Effective", model.config.effectiveValue(row.field.key).displayText)
                    inspectorValue("Preset", model.config.presetValue(row.field.key).displayText)
                    inspectorValue("Engine default", row.field.default.displayText)
                    if let range = row.field.rangeDescription {
                        inspectorValue("Constraint", range)
                    }
                    if let choices = row.field.choices, !choices.isEmpty {
                        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                            Text("Choices").font(.caption.weight(.semibold))
                            Text(choices.joined(separator: ", "))
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                    if row.entry.isExperimental {
                        Label("Experimental", systemImage: "flask.fill")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                    if row.entry.disposition == .readonly {
                        Label("Read-only by engine contract", systemImage: "lock.fill")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if model.config.isModified(row.field.key) {
                        Button("Reset to Preset Value") {
                            model.config.clearOverride(row.field.key)
                        }
                        .disabled(model.workMutationLocked)
                    }
                    Spacer(minLength: 0)
                }
                .padding(AuralignVisualSystem.spacingL)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Color(nsColor: .windowBackgroundColor))
            .accessibilityIdentifier(AccessibilityIdentifier.parameterInspector)
        } else {
            ContentUnavailableView(
                "Select a Parameter",
                systemImage: "sidebar.right",
                description: Text("Details remain secondary to the editable list.")
            )
        }
    }

    private var selectedRow: ParameterLibraryRow? {
        guard let selectedKey else { return rows.first }
        return rows.first { $0.id == selectedKey } ?? rows.first
    }

    private var groupedSections: [(String, [ParameterLibraryRow])] {
        let grouped = Dictionary(grouping: rows, by: \.entry.section)
        return model.catalog.sections.compactMap { section in
            guard let values = grouped[section.id], !values.isEmpty else { return nil }
            return (section.id, values)
        }
    }

    private var categorySelection: Binding<String?> {
        Binding(
            get: { selectedCategoryID },
            set: { selectedCategoryID = $0 ?? Self.allCategoryID }
        )
    }

    private func normalizeSelection() {
        guard let first = rows.first else {
            selectedKey = nil
            return
        }
        if selectedKey == nil || !rows.contains(where: { $0.id == selectedKey }) {
            selectedKey = first.id
        }
    }

    private func categoryMatches(_ row: ParameterLibraryRow) -> Bool {
        selectedCategoryID == Self.allCategoryID
            || row.entry.section == selectedCategoryID
    }

    private func filterMatches(_ row: ParameterLibraryRow) -> Bool {
        ParameterLibraryProjection.matches(
            filter: filter,
            entry: row.entry,
            isModified: model.config.isModified(row.field.key),
            hasErrors: !model.config.errors(for: row.field.key).isEmpty
        )
    }

    private func searchMatches(_ row: ParameterLibraryRow) -> Bool {
        ParameterLibraryProjection.matchesSearch(
            query: searchText,
            field: row.field,
            entry: row.entry,
            manifest: model.manifest
        )
    }

    private func inspectorValue(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption.weight(.semibold))
            Text(value)
                .font(.caption)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func categoryTitle(_ sectionID: String) -> String {
        model.catalog.section(sectionID)?.title ?? sectionID
    }

    private func dispositionTitle(_ disposition: FieldCatalog.Disposition) -> String {
        switch disposition {
        case .primary: return "Primary"
        case .advanced: return "Advanced"
        case .readonly: return "Read-only"
        }
    }

    private var uncataloguedBanner: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            Label(
                "The engine declares \(model.config.uncataloguedLiveFields.count) "
                    + "parameter(s) this app build does not present yet.",
                systemImage: "exclamationmark.triangle.fill"
            )
            .font(.callout)
            .foregroundStyle(.orange)
            Text("Regenerate the manifest and catalog; unknown fields fail closed.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, AuralignVisualSystem.spacingXL)
        .padding(.vertical, AuralignVisualSystem.spacingS)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.08))
    }
}
