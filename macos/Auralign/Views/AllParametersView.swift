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

    var symbol: String {
        switch self {
        case .all: return "list.bullet"
        case .modified: return "pencil.circle.fill"
        case .invalid: return "exclamationmark.circle.fill"
        case .advanced: return "slider.horizontal.3"
        case .experimental: return "flask.fill"
        case .readOnly: return "lock.fill"
        }
    }
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

/// Native expert browser over every production-visible schema field. Blocked
/// implementation-only fields remain in the audited engine/catalog contract
/// for backward compatibility but never become ordinary product controls.
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
            guard entry.isProductVisible,
                  let field = model.manifest.field(entry.key)
            else {
                return nil
            }
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
                if geometry.size.width >= AuralignBreakpoint.parameterLibrary {
                    HStack(spacing: 0) {
                        libraryIndex
                            .frame(width: 210)
                        Divider()
                        parameterList
                            .frame(minWidth: 420, maxWidth: .infinity)
                        Divider()
                        parameterInspector
                            .frame(width: 270)
                    }
                } else {
                    VStack(spacing: 0) {
                        compactIndexPicker
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

    // MARK: - Header

    private var libraryHeader: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
            AuralignWorkspaceHeader(
                symbol: "list.bullet.rectangle",
                accessibilityHeading: "Parameter Library"
            ) {
                Text(librarySelectionSummary)
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: AuralignVisualSystem.spacingM) {
                    searchField
                    Spacer(minLength: 0)
                    resultCount
                }
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                    searchField
                    resultCount
                }
            }
        }
        .padding(.horizontal, AuralignVisualSystem.spacingXL)
        .padding(.vertical, AuralignVisualSystem.spacingM)
        .fixedSize(horizontal: false, vertical: true)
        .layoutPriority(1)
    }

    private var librarySelectionSummary: String {
        var scope: [String] = []
        if filter != .all { scope.append(filter.rawValue) }
        if selectedCategoryID != Self.allCategoryID {
            scope.append(categoryTitle(selectedCategoryID))
        }
        if scope.isEmpty {
            return "All \(allRows.count) production parameters, of "
                + "\(model.manifest.fields.count) audited schema fields."
        }
        return "\(scope.joined(separator: " · ")) — \(rows.count) of \(allRows.count) shown."
    }

    private var searchField: some View {
        TextField("Search label, help, key, or choice", text: $searchText)
            .textFieldStyle(.roundedBorder)
            .frame(minWidth: 220, idealWidth: 330, maxWidth: 430)
            .accessibilityIdentifier(AccessibilityIdentifier.parameterSearch)
    }

    private var resultCount: some View {
        Text("\(rows.count) of \(allRows.count)")
            .font(AuralignType.numericSmall)
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

    // MARK: - Sidebar: saved filters and category taxonomy

    /// Little Snitch's rule-browser pattern: saved queries over the same data
    /// with live count badges, presented alongside — not instead of — the
    /// existing flat category taxonomy.
    private var libraryIndex: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                indexGroup(title: "Filters") {
                    ForEach(ParameterLibraryFilter.allCases) { item in
                        indexRow(
                            title: item.rawValue,
                            symbol: item.symbol,
                            count: filterCount(item),
                            isSelected: filter == item,
                            identifier: "parameters.filter.\(stableIdentifierComponent(item.rawValue))"
                        ) {
                            filter = item
                        }
                    }
                }
                indexGroup(title: "Categories") {
                    indexRow(
                        title: "All Categories",
                        symbol: "square.grid.2x2",
                        count: categoryCount(nil),
                        isSelected: selectedCategoryID == Self.allCategoryID,
                        identifier: "\(AccessibilityIdentifier.parameterCategory).all"
                    ) {
                        selectedCategoryID = Self.allCategoryID
                    }
                    ForEach(model.catalog.sections) { section in
                        indexRow(
                            title: section.title,
                            symbol: section.symbol,
                            count: categoryCount(section.id),
                            isSelected: selectedCategoryID == section.id,
                            identifier:
                                "\(AccessibilityIdentifier.parameterCategory)."
                                    + stableIdentifierComponent(section.id)
                        ) {
                            selectedCategoryID = section.id
                        }
                    }
                }
            }
            .padding(.vertical, AuralignVisualSystem.spacingM)
        }
        .background(AuralignSurface.canvas)
        .accessibilityLabel("Parameter filters and categories")
        .accessibilityIdentifier(AccessibilityIdentifier.parameterCategory)
    }

    private func indexGroup<Content: View>(
        title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            Text(title)
                .font(AuralignType.metadataStrong)
                .foregroundStyle(.secondary)
                .padding(.horizontal, AuralignVisualSystem.spacingM)
                .accessibilityAddTraits(.isHeader)
            content()
        }
    }

    private func indexRow(
        title: String,
        symbol: String,
        count: Int,
        isSelected: Bool,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: AuralignVisualSystem.spacingS) {
                Image(systemName: symbol)
                    .font(.callout)
                    .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)
                    .frame(width: 16)
                    .accessibilityHidden(true)
                Text(title)
                    .font(AuralignType.rowTitle)
                    .foregroundStyle(Color.primary)
                    .lineLimit(1)
                Spacer(minLength: AuralignVisualSystem.spacingS)
                Text("\(count)")
                    .font(AuralignType.numericSmall)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, AuralignVisualSystem.spacingM)
            .padding(.vertical, AuralignVisualSystem.spacingXS)
            .background {
                if isSelected {
                    RoundedRectangle(cornerRadius: AuralignRadius.control)
                        .fill(Color.accentColor.opacity(0.14))
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
        .accessibilityLabel("\(title), \(count) parameter\(count == 1 ? "" : "s")")
        .accessibilityIdentifier(identifier)
    }

    private func filterCount(_ filterCase: ParameterLibraryFilter) -> Int {
        allRows.filter { row in
            categoryMatches(row)
                && searchMatches(row)
                && ParameterLibraryProjection.matches(
                    filter: filterCase,
                    entry: row.entry,
                    isModified: model.config.isModified(row.field.key),
                    hasErrors: !model.config.errors(for: row.field.key).isEmpty
                )
        }.count
    }

    private func categoryCount(_ sectionID: String?) -> Int {
        allRows.filter { row in
            (sectionID == nil || row.entry.section == sectionID)
                && filterMatches(row)
                && searchMatches(row)
        }.count
    }

    private func stableIdentifierComponent(_ value: String) -> String {
        value.unicodeScalars.map { scalar in
            CharacterSet.alphanumerics.contains(scalar) ? String(scalar) : "-"
        }.joined()
    }

    private var compactIndexPicker: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: AuralignVisualSystem.spacingM) {
                compactFilterPicker
                compactCategoryPickerControl
                Spacer(minLength: 0)
            }
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                compactFilterPicker
                compactCategoryPickerControl
            }
        }
        .padding(.horizontal, AuralignVisualSystem.spacingXL)
        .padding(.vertical, AuralignVisualSystem.spacingS)
        .background(.bar)
    }

    private var compactFilterPicker: some View {
        Picker("Filter", selection: $filter) {
            ForEach(ParameterLibraryFilter.allCases) { item in
                Text("\(item.rawValue) (\(filterCount(item)))").tag(item)
            }
        }
        .pickerStyle(.menu)
        .fixedSize()
        .accessibilityIdentifier(AccessibilityIdentifier.parameterFilter)
    }

    private var compactCategoryPickerControl: some View {
        Picker("Category", selection: $selectedCategoryID) {
            Text("All Categories (\(categoryCount(nil)))").tag(Self.allCategoryID)
            ForEach(model.catalog.sections) { section in
                Text("\(section.title) (\(categoryCount(section.id)))").tag(section.id)
            }
        }
        .pickerStyle(.menu)
        .accessibilityIdentifier(AccessibilityIdentifier.parameterCategory)
    }

    // MARK: - Parameter list

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

    /// The schema key is demoted to a hover tooltip here — the scanning list
    /// leads with the human label (`FieldRowView`'s own title row), never a
    /// monospace identifier promoted above it.
    private func parameterRow(_ row: ParameterLibraryRow) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            if !model.config.errors(for: row.field.key).isEmpty {
                AuralignStatusBadge(
                    text: "Invalid",
                    tone: .failure,
                    symbol: "exclamationmark.circle.fill"
                )
            }
            FieldRowView(
                config: model.config,
                field: row.field,
                entry: row.entry,
                compact: true
            )
        }
        .padding(.vertical, AuralignVisualSystem.spacingXS)
        .help(row.field.key)
        .accessibilityIdentifier(AccessibilityIdentifier.parameterRow(row.field.key))
    }

    // MARK: - Inspector

    @ViewBuilder
    private var parameterInspector: some View {
        if let row = selectedRow {
            ScrollView {
                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                    Text(row.entry.title)
                        .font(AuralignType.workspaceTitle)
                        .accessibilityAddTraits(.isHeader)
                    Text(row.field.key)
                        .font(AuralignType.technical)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                    Divider()
                    Text(row.entry.help)
                        .font(AuralignType.body)
                        .fixedSize(horizontal: false, vertical: true)

                    AuralignCausalityBanner(
                        headline: effectiveHeadline(row),
                        detail: effectiveDetail(row),
                        tone: model.config.isModified(row.field.key) ? .active : .neutral,
                        symbol: model.config.isModified(row.field.key)
                            ? "pencil.circle.fill" : "checkmark.circle",
                        linkTitle: resetLinkTitle(row),
                        linkIdentifier: resetLinkTitle(row) == nil
                            ? nil : "parameters.inspector.reset",
                        link: resetLinkTitle(row) == nil
                            ? nil : { model.config.clearOverride(row.field.key) }
                    )

                    inspectorDetails(row)

                    if let choices = row.field.choices, !choices.isEmpty {
                        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                            Text("Choices").font(AuralignType.metadataStrong)
                            Text(choices.joined(separator: ", "))
                                .font(AuralignType.technical)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                    if row.entry.isExperimental {
                        Label("Experimental", systemImage: "flask.fill")
                            .font(AuralignType.status)
                            .foregroundStyle(.orange)
                    }
                    if row.entry.disposition == .readonly {
                        Label("Read-only by engine contract", systemImage: "lock.fill")
                            .font(AuralignType.status)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(AuralignVisualSystem.spacingL)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(AuralignSurface.canvas)
            .accessibilityIdentifier(AccessibilityIdentifier.parameterInspector)
        } else {
            ContentUnavailableView(
                "Select a Parameter",
                systemImage: "sidebar.right",
                description: Text("Details remain secondary to the editable list.")
            )
        }
    }

    /// The one governing fact — Little Snitch's rule-inspector statement —
    /// replaces the flat Effective/Preset/Engine-default triple of lines.
    private func effectiveHeadline(_ row: ParameterLibraryRow) -> String {
        let effective = model.config.effectiveValue(row.field.key).displayText
        if model.config.isModified(row.field.key) {
            let preset = model.config.presetValue(row.field.key).displayText
            return "Effective: \(effective) — overridden from preset value \(preset)."
        }
        if model.config.presetValue(row.field.key) != row.field.default {
            return "Effective: \(effective) — set by the active preset, not overridden."
        }
        return "Effective: \(effective) — matches the engine default, not overridden."
    }

    private func effectiveDetail(_ row: ParameterLibraryRow) -> String? {
        let isModified = model.config.isModified(row.field.key)
        let presetDiffersFromDefault =
            model.config.presetValue(row.field.key) != row.field.default
        guard isModified || presetDiffersFromDefault else { return nil }
        return "Engine default is \(row.field.default.displayText)."
    }

    private func resetLinkTitle(_ row: ParameterLibraryRow) -> String? {
        guard model.config.isModified(row.field.key), !model.workMutationLocked else {
            return nil
        }
        return "Reset to Preset Value"
    }

    private func inspectorDetails(_ row: ParameterLibraryRow) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            Text("Details")
                .font(AuralignType.metadataStrong)
                .foregroundStyle(.secondary)
            VStack(
                alignment: .leading,
                spacing: AuralignVisualSystem.spacingXS
            ) {
                detailRow("Category", categoryTitle(row.entry.section))
                detailRow("Type", row.field.kind.rawValue, technical: true)
                detailRow("Disposition", dispositionTitle(row.entry.disposition))
                if let range = row.field.rangeDescription {
                    detailRow("Constraint", range, technical: true)
                }
            }
        }
    }

    private func detailRow(
        _ label: String,
        _ value: String,
        technical: Bool = false
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: AuralignVisualSystem.spacingM) {
            Text(label)
                .font(AuralignType.metadata)
                .foregroundStyle(.secondary)
            Spacer(minLength: AuralignVisualSystem.spacingS)
            Text(value)
                .font(
                    technical
                        ? AuralignType.technical : AuralignType.metadataStrong
                )
                .foregroundStyle(.primary)
                .multilineTextAlignment(.trailing)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
        .accessibilityElement(children: .combine)
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
        AuralignCausalityBanner(
            headline: "The engine declares \(model.config.uncataloguedLiveFields.count) "
                + "parameter(s) this app build does not present yet.",
            detail: "Regenerate the manifest and catalog; unknown fields fail closed.",
            tone: .warning,
            symbol: "exclamationmark.triangle.fill"
        )
        .padding(.horizontal, AuralignVisualSystem.spacingXL)
        .padding(.vertical, AuralignVisualSystem.spacingS)
    }
}
