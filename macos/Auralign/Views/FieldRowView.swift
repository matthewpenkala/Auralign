// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI
import UniformTypeIdentifiers

/// Renders one configuration field with the appropriate native control,
/// driven by the engine-generated schema (kind, choices, bounds, defaults)
/// and the curated catalog (title, help, dependencies, prominence).
struct FieldRowView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var config: ConfigStore
    let field: ConfigField
    let entry: FieldCatalog.Entry
    var compact = false
    var focusRequest: FieldNavigationRequest?
    @State private var numericDraftError: String?
    @State private var floatOrListShape: FloatOrListShape?
    @FocusState private var editorFocused: Bool
    @AccessibilityFocusState private var accessibilityFocused: Bool

    private var effective: JSONValue { config.effectiveValue(field.key) }
    private var isModified: Bool { config.isModified(field.key) }
    private var selectedModelEntry: ModelManifestEntry? {
        guard let role = field.modelRole, let key = effective.stringValue else { return nil }
        return config.manifest.resolveModel(role: role, value: key)
    }
    private var selectedChoicePresentation: FieldCatalog.ChoicePresentation? {
        guard let value = effective.stringValue else { return nil }
        return entry.choicePresentations?[value]
    }
    private var unmetRequirement: FieldCatalog.Requirement? {
        entry.unmetRequirement(in: config.effectiveConfig)
    }
    private var isEditable: Bool {
        entry.disposition != .readonly
            && !config.isManaged(field.key)
            && unmetRequirement == nil
            && !model.workMutationLocked
    }
    private var fieldErrors: [ConfigFieldError] { config.errors(for: field.key) }
    private var focusRequestID: Int? {
        focusRequest?.fieldKey == field.key ? focusRequest?.id : nil
    }
    private var localBoundsError: String? {
        guard field.kind == .int || field.kind == .float,
              let number = effective.doubleValue,
              !field.isWithinBounds(number),
              let range = field.rangeDescription else { return nil }
        return "Out of range (\(range))."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    titleView
                    Spacer(minLength: 12)
                    controlView
                        .disabled(!isEditable)
                        .frame(maxWidth: 320, alignment: .trailing)
                }
                VStack(alignment: .leading, spacing: 6) {
                    titleView
                    controlView
                        .disabled(!isEditable)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            captionView
        }
        .padding(.vertical, compact ? 2 : 4)
        .contextMenu {
            if isModified {
                Button("Reset to Preset Value") { config.clearOverride(field.key) }
                    .accessibilityIdentifier(
                        AccessibilityIdentifier.fieldReset(field.key)
                    )
                    .disabled(model.workMutationLocked)
            }
            Button("Copy Key") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(field.key, forType: .string)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(entry.title))
        .accessibilityValue(Text(effective.displayText))
        .accessibilityIdentifier(AccessibilityIdentifier.field(field.key))
        .task(id: focusRequestID) {
            guard let focusRequest,
                  focusRequest.id == focusRequestID,
                  !childControlOwnsFocusCompletion
            else { return }
            await Task.yield()
            editorFocused = true
            accessibilityFocused = true
            try? await Task.sleep(nanoseconds: 100_000_000)
            guard editorFocused else { return }
            completeFocusRequest(focusRequest.id)
        }
        .onChange(of: effective) { _, newValue in
            if newValue.isNull || newValue.doubleValue?.isFinite == true {
                numericDraftError = nil
            } else if let values = newValue.arrayValue,
                      values.allSatisfy({ $0.doubleValue?.isFinite == true }) {
                numericDraftError = nil
            }
            if let shape = FloatOrListShape(value: newValue) {
                floatOrListShape = shape
            }
        }
    }

    // MARK: - Title and captions

    private var titleView: some View {
        HStack(spacing: 6) {
            if isModified {
                Text("Modified")
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .overlay {
                        Capsule().stroke(Color.accentColor, lineWidth: 1)
                    }
            }
            Text(entry.title)
                .fontWeight(isModified ? .medium : .regular)
            if entry.isExperimental {
                Text("EXPERIMENTAL")
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 4)
                    .padding(.vertical, 1)
                    .background(Color.orange.opacity(0.18), in: Capsule())
                    .foregroundStyle(.orange)
            }
            if entry.disposition == .readonly || config.isManaged(field.key) {
                Image(systemName: "lock")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel(
                        config.isManaged(field.key)
                            ? "Managed by Auralign"
                            : "Read-only"
                    )
            }
        }
    }

    @ViewBuilder
    private var captionView: some View {
        VStack(alignment: .leading, spacing: 2) {
            if !compact {
                Text(entry.help)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                if let range = field.rangeDescription {
                    Text(range).font(.caption2).foregroundStyle(.tertiary)
                }
                if isModified {
                    Text("Preset: \(config.presetValue(field.key).displayText)")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                } else if config.isManaged(field.key) {
                    Text("Managed by the installed application")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                } else if config.presetValue(field.key) != field.default {
                    Text("Preset override · engine default: \(field.default.displayText)")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            if let requirement = unmetRequirement {
                Text(requirementExplanation(requirement))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .italic()
            }
            if let localBoundsError {
                errorText(localBoundsError, suffix: "bounds")
            }
            if let numericDraftError {
                errorText(numericDraftError, suffix: "draft")
            }
            if field.modelRole != nil {
                modelPolicyCaption
            }
            ForEach(Array(fieldErrors.enumerated()), id: \.element.id) {
                index, error in
                errorText(error.message, suffix: "engine.\(index)")
            }
        }
    }

    private func errorText(_ message: String, suffix: String) -> some View {
        Label(message, systemImage: "exclamationmark.circle.fill")
            .font(.caption)
            .foregroundStyle(.red)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier(
                AccessibilityIdentifier.fieldError(field.key) + ".\(suffix)"
            )
    }

    @ViewBuilder
    private var modelPolicyCaption: some View {
        if let selectedModelEntry {
            HStack(spacing: 6) {
                Image(
                    systemName: selectedModelEntry.isRuntimeSelectable
                        ? "checkmark.shield.fill" : "exclamationmark.shield.fill"
                )
                Text(selectedModelEntry.policyExplanation)
                Text(
                    "\(selectedModelEntry.licenseExpression ?? "unknown license")"
                        + " · revision \(selectedModelEntry.shortRevision)"
                )
                .foregroundStyle(.tertiary)
            }
            .font(.caption)
            .foregroundStyle(selectedModelEntry.isRuntimeSelectable ? Color.secondary : Color.red)
            .fixedSize(horizontal: false, vertical: true)
        } else if effective.stringValue != nil {
            errorText(
                "This model is absent from the reviewed engine manifest.",
                suffix: "model-policy"
            )
        }
    }

    private func requirementExplanation(_ requirement: FieldCatalog.Requirement) -> String {
        let dependencyTitle = config.catalog.fieldsByKey[requirement.key]?.title
            ?? requirement.key
        if let equals = requirement.equals {
            return "Requires \(dependencyTitle) = \(equals.displayText)."
        }
        if let notEquals = requirement.notEquals {
            return "Requires \(dependencyTitle) ≠ \(notEquals.displayText)."
        }
        return "Unavailable with the current configuration."
    }

    private var controlAccessibilityHint: String {
        var parts = [entry.help]
        if let requirement = unmetRequirement {
            parts.append(requirementExplanation(requirement))
        } else if model.workMutationLocked {
            parts.append("Editing is locked while Auralign owns an engine process.")
        } else if config.isManaged(field.key) {
            parts.append("This value is managed by the installed application.")
        } else if entry.disposition == .readonly {
            parts.append("This field is read-only.")
        }
        if let selectedChoicePresentation {
            parts.append(selectedChoicePresentation.help)
        }
        if let selectedModelEntry {
            parts.append(
                model.modelSetup.productSummary(for: selectedModelEntry)
            )
        } else if field.modelRole != nil, let value = effective.stringValue {
            parts.append("\(value) is absent from the reviewed model manifest.")
        }
        if let localBoundsError {
            parts.append(localBoundsError)
        }
        if let numericDraftError {
            parts.append(numericDraftError)
        }
        parts.append(contentsOf: fieldErrors.map(\.message))
        return parts.joined(separator: " ")
    }

    // MARK: - Controls

    @ViewBuilder
    private var controlView: some View {
        switch field.kind {
        case .bool:
            Toggle(entry.title, isOn: boolBinding)
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.small)
                .focused($editorFocused)
                .accessibilityFocused($accessibilityFocused)
                .accessibilityHint(Text(controlAccessibilityHint))
                .accessibilityIdentifier(
                    AccessibilityIdentifier.fieldControl(field.key)
                )
        case .enumeration:
            enumControl
                .focused($editorFocused)
        case .int:
            numberControl(isInteger: true)
        case .float:
            numberControl(isInteger: false)
        case .string:
            if field.modelRole != nil {
                modelPickerControl
                    .focused($editorFocused)
            } else {
                stringControl
                    .focused($editorFocused)
            }
        case .path:
            PathControl(
                label: entry.title,
                accessibilityHint: controlAccessibilityHint,
                accessibilityIdentifier: AccessibilityIdentifier.fieldControl(field.key),
                value: pathBinding,
                optional: field.optional,
                directory: entry.isDirectoryPath,
                focusRequestID: focusRequestID,
                onFocusRequestCompleted: completeFocusRequest
            )
        case .floatList, .floatOrFloatList:
            NumericDraftTextField(
                label: entry.title,
                prompt: "e.g. 0.0, 0.2, 0.4",
                text: floatListBinding,
                width: 180,
                focusRequestID: focusRequestID,
                accessibilityHint: controlAccessibilityHint,
                accessibilityIdentifier: AccessibilityIdentifier.fieldControl(field.key),
                onFocusRequestCompleted: completeFocusRequest
            )
        case .pathList:
            PathListControl(
                label: entry.title,
                accessibilityHint: controlAccessibilityHint,
                accessibilityIdentifier: AccessibilityIdentifier.fieldControl(field.key),
                values: pathListBinding,
                focusRequestID: focusRequestID,
                onFocusRequestCompleted: completeFocusRequest
            )
        }
    }

    private var boolBinding: Binding<Bool> {
        Binding(
            get: { effective.boolValue ?? (field.default.boolValue ?? false) },
            set: { config.setOverride(field.key, to: .bool($0)) }
        )
    }

    private static let unsetTag = "\u{0}automatic"

    private var enumControl: some View {
        Picker(entry.title, selection: enumBinding) {
            if field.optional {
                Text("Automatic").tag(Self.unsetTag)
                Divider()
            }
            ForEach(field.choices ?? [], id: \.self) { choice in
                enumChoiceLabel(choice)
                    .tag(choice)
            }
        }
        .labelsHidden()
        .pickerStyle(.menu)
        .fixedSize()
        .accessibilityFocused($accessibilityFocused)
        .accessibilityValue(
            Text(
                selectedChoicePresentation?.accessibilityLabel
                    ?? effective.displayText
            )
        )
        .accessibilityHint(Text(controlAccessibilityHint))
        .accessibilityIdentifier(AccessibilityIdentifier.fieldControl(field.key))
    }

    @ViewBuilder
    private func enumChoiceLabel(_ choice: String) -> some View {
        if let presentation = entry.choicePresentations?[choice] {
            Text(presentation.label)
                .accessibilityLabel(
                    Text(presentation.accessibilityLabel)
                )
        } else {
            Text(choice)
        }
    }

    private var enumBinding: Binding<String> {
        Binding(
            get: { effective.stringValue ?? Self.unsetTag },
            set: { newValue in
                config.setOverride(
                    field.key,
                    to: newValue == Self.unsetTag ? .null : .string(newValue)
                )
            }
        )
    }

    private var modelPickerControl: some View {
        let choices = config.manifest.modelChoices(role: field.modelRole ?? "")
        let current = effective.stringValue ?? ""
        return Picker(entry.title, selection: enumBindingForModels) {
            if field.optional {
                Text("Automatic").tag(Self.unsetTag)
                Divider()
            }
            ForEach(choices) { choice in
                Text(
                    "\(model.modelSetup.displayName(for: choice)) — "
                        + model.modelSetup.installationLabel(for: choice)
                )
                    .accessibilityLabel(
                        Text(
                            model.modelSetup.accessibilityLabel(for: choice)
                                + ". "
                                + model.modelSetup.installationLabel(for: choice)
                        )
                    )
                    .tag(choice.key)
            }
            if !current.isEmpty, !choices.contains(where: { $0.key == current }) {
                Divider()
                if let selectedModelEntry {
                    if selectedModelEntry.isRuntimeSelectable {
                        Text(
                            "\(model.modelSetup.displayName(for: selectedModelEntry))"
                                + " — current alias \(current)"
                        )
                        .tag(current)
                    } else {
                        Text(
                            "Unavailable current value — "
                                + model.modelSetup.displayName(
                                    for: selectedModelEntry
                                )
                        )
                        .tag(current)
                        .disabled(true)
                    }
                } else {
                    Text("Unavailable current value — \(current)")
                        .tag(current)
                        .disabled(true)
                }
            }
        }
        .labelsHidden()
        .pickerStyle(.menu)
        .fixedSize()
        .help(
            selectedModelEntry.map {
                model.modelSetup.productSummary(for: $0)
            } ?? "Choose an exact verified local model."
        )
        .accessibilityFocused($accessibilityFocused)
        .accessibilityValue(
            Text(
                selectedModelEntry.map {
                    model.modelSetup.displayName(for: $0)
                } ?? current
            )
        )
        .accessibilityHint(Text(controlAccessibilityHint))
        .accessibilityIdentifier(AccessibilityIdentifier.fieldControl(field.key))
    }

    private var enumBindingForModels: Binding<String> {
        Binding(
            get: { effective.stringValue ?? Self.unsetTag },
            set: { newValue in
                config.setOverride(
                    field.key,
                    to: newValue == Self.unsetTag ? .null : .string(newValue)
                )
            }
        )
    }

    @ViewBuilder
    private func numberControl(isInteger: Bool) -> some View {
        HStack(spacing: 6) {
            if field.optional {
                if effective.isNull {
                    Toggle(
                        "Automatic",
                        isOn: optionalNumberEnabledBinding.not
                    )
                        .toggleStyle(.checkbox)
                        .controlSize(.small)
                        .help("Leave unset so the engine chooses automatically.")
                        .focused($editorFocused)
                        .accessibilityFocused($accessibilityFocused)
                        .accessibilityLabel(
                            Text("Use automatic value for \(entry.title)")
                        )
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.fieldControl(field.key)
                                + ".automatic"
                        )
                } else {
                    Toggle(
                        "Automatic",
                        isOn: optionalNumberEnabledBinding.not
                    )
                        .toggleStyle(.checkbox)
                        .controlSize(.small)
                        .help("Leave unset so the engine chooses automatically.")
                        .accessibilityLabel(
                            Text("Use automatic value for \(entry.title)")
                        )
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.fieldControl(field.key)
                                + ".automatic"
                        )
                }
            }
            if !field.optional || !effective.isNull {
                if isInteger {
                    NumericDraftTextField(
                        label: entry.title,
                        text: integerBinding,
                        width: 120,
                        focusRequestID: focusRequestID,
                        accessibilityHint: controlAccessibilityHint,
                        accessibilityIdentifier:
                            AccessibilityIdentifier.fieldControl(field.key),
                        onFocusRequestCompleted: completeFocusRequest
                    )
                    Stepper(
                        "Adjust \(entry.title)",
                        onIncrement: { adjustInteger(by: 1) },
                        onDecrement: { adjustInteger(by: -1) }
                    )
                        .labelsHidden()
                        .controlSize(.small)
                        .disabled(effective.strictIntValue == nil)
                        .accessibilityHint(Text(controlAccessibilityHint))
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.fieldControl(field.key)
                                + ".stepper"
                        )
                } else {
                    NumericDraftTextField(
                        label: entry.title,
                        text: scalarFloatBinding,
                        width: 180,
                        focusRequestID: focusRequestID,
                        accessibilityHint: controlAccessibilityHint,
                        accessibilityIdentifier:
                            AccessibilityIdentifier.fieldControl(field.key),
                        onFocusRequestCompleted: completeFocusRequest
                    )
                }
                if let unit = entry.unit, unit != "directory" {
                    Text(unit).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var optionalNumberEnabledBinding: Binding<Bool> {
        Binding(
            get: { !effective.isNull },
            set: { enabled in
                if enabled {
                    let integerFallback = JSONValue.double(
                        field.ge ?? field.gt.map { $0 + 1 } ?? 1
                    ).intValue ?? 1
                    let fallback: JSONValue = field.kind == .int
                        ? .int(integerFallback)
                        : .double(field.ge ?? 1.0)
                    let restored = field.default.isNull ? fallback : field.default
                    config.setOverride(field.key, to: restored)
                } else {
                    config.setOverride(field.key, to: .null)
                }
            }
        )
    }

    private var integerBinding: Binding<String> {
        Binding(
            get: { LosslessIntegerDraft.text(for: effective) },
            set: { text in
                let update = LosslessIntegerDraft.update(for: text)
                numericDraftError = update.validationError
                config.setOverride(field.key, to: update.storedValue)
            }
        )
    }

    private func adjustInteger(by delta: Int) {
        guard let value = effective.strictIntValue else { return }
        let (updated, overflow) = value.addingReportingOverflow(delta)
        guard !overflow else { return }
        numericDraftError = nil
        config.setOverride(field.key, to: .int(updated))
    }

    private var scalarFloatBinding: Binding<String> {
        Binding(
            get: { LosslessNumericDraft.scalarText(for: effective) },
            set: { text in
                let update = LosslessNumericDraft.scalarUpdate(for: text)
                numericDraftError = update.validationError
                config.setOverride(field.key, to: update.storedValue)
            }
        )
    }

    private var stringControl: some View {
        TextField(
            entry.title,
            text: stringBinding,
            prompt: field.optional ? Text("Automatic") : nil
        )
        .labelsHidden()
        .textFieldStyle(.roundedBorder)
        .frame(minWidth: 140, maxWidth: 320)
        .accessibilityFocused($accessibilityFocused)
        .accessibilityHint(Text(controlAccessibilityHint))
        .accessibilityIdentifier(AccessibilityIdentifier.fieldControl(field.key))
    }

    private var stringBinding: Binding<String> {
        Binding(
            get: { effective.stringValue ?? "" },
            set: { newValue in
                if newValue.isEmpty && field.optional {
                    config.setOverride(field.key, to: .null)
                } else {
                    config.setOverride(field.key, to: .string(newValue))
                }
            }
        )
    }

    private var pathBinding: Binding<String?> {
        Binding(
            get: { effective.stringValue },
            set: { newValue in
                config.setOverride(
                    field.key,
                    to: newValue.map { JSONValue.string($0) } ?? .null
                )
            }
        )
    }

    private var floatListBinding: Binding<String> {
        Binding(
            get: { LosslessNumericDraft.sequenceText(for: effective) },
            set: { text in
                let fallbackShape = FloatOrListShape(value: effective)
                    ?? FloatOrListShape(value: config.presetValue(field.key))
                    ?? FloatOrListShape(value: field.default)
                    ?? .list
                let preferredShape = field.kind == .floatList
                    ? FloatOrListShape.list
                    : (floatOrListShape ?? fallbackShape)
                let update = LosslessNumericDraft.sequenceUpdate(
                    for: text,
                    preserving: preferredShape
                )
                numericDraftError = update.validationError
                floatOrListShape = field.kind == .floatList ? .list : update.shape
                config.setOverride(field.key, to: update.storedValue)
            }
        )
    }

    private var pathListBinding: Binding<[String]> {
        Binding(
            get: { effective.arrayValue?.compactMap(\.stringValue) ?? [] },
            set: { paths in
                config.setOverride(
                    field.key,
                    to: .array(paths.map { JSONValue.string($0) })
                )
            }
        )
    }

    private var childControlOwnsFocusCompletion: Bool {
        switch field.kind {
        case .path, .pathList, .floatList, .floatOrFloatList:
            return true
        case .int, .float:
            return !field.optional || !effective.isNull
        case .bool, .enumeration, .string:
            return false
        }
    }

    private func completeFocusRequest(_ requestID: Int) {
        guard let focusRequest,
              focusRequest.id == requestID,
              model.consumedFieldNavigationRequestID != requestID
        else {
            return
        }
        AccessibilityAnnouncementCenter.shared.announce(
            "Configuration error. \(entry.title). \(focusRequest.message)",
            token: "configuration-field-error-\(requestID)",
            priority: .high
        )
        // Completion is published only by the control that owns actual
        // keyboard and accessibility focus.
        model.consumeFieldNavigationRequest(requestID)
    }
}

private extension Binding where Value == Bool {
    var not: Binding<Bool> {
        Binding(get: { !wrappedValue }, set: { wrappedValue = !$0 })
    }
}

// MARK: - Path controls

struct PathControl: View {
    @EnvironmentObject private var model: AppModel
    var label: String
    var accessibilityHint: String
    var accessibilityIdentifier: String
    @Binding var value: String?
    var optional: Bool
    var directory: Bool
    var focusRequestID: Int?
    var onFocusRequestCompleted: (Int) -> Void
    @FocusState private var chooseFocused: Bool
    @AccessibilityFocusState private var chooseAccessibilityFocused: Bool

    var body: some View {
        HStack(spacing: 6) {
            if let value {
                Text(abbreviate(value))
                    .font(.callout)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .frame(maxWidth: 200, alignment: .trailing)
                    .help(value)
            } else {
                Text(optional ? "Not set" : "Required")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Button("Choose…") { choose() }
                .controlSize(.small)
                .focused($chooseFocused)
                .accessibilityFocused($chooseAccessibilityFocused)
                .accessibilityLabel(Text("Choose \(label)"))
                .accessibilityHint(Text(accessibilityHint))
                .accessibilityIdentifier(accessibilityIdentifier)
                .disabled(model.isPresentationFixture)
            if optional && value != nil {
                Button {
                    value = nil
                } label: {
                    Image(systemName: "xmark.circle.fill")
                }
                .buttonStyle(.borderless)
                .accessibilityLabel(Text("Clear \(label)"))
                .accessibilityHint(Text(accessibilityHint))
                .accessibilityIdentifier(accessibilityIdentifier + ".clear")
            }
        }
        .task(id: focusRequestID) {
            guard let focusRequestID else { return }
            await Task.yield()
            chooseFocused = true
            chooseAccessibilityFocused = true
            try? await Task.sleep(nanoseconds: 100_000_000)
            guard chooseFocused else { return }
            onFocusRequestCompleted(focusRequestID)
        }
    }

    private func choose() {
        guard !model.isPresentationFixture else { return }
        let url = directory
            ? LocalFilePanel.chooseDirectory(message: "Choose \(label)")
            : LocalFilePanel.chooseFile(message: "Choose \(label)")
        if let url {
            value = url.path
        }
    }

    private func abbreviate(_ path: String) -> String {
        (path as NSString).abbreviatingWithTildeInPath
    }
}

struct PathListControl: View {
    @EnvironmentObject private var model: AppModel
    var label: String
    var accessibilityHint: String
    var accessibilityIdentifier: String
    @Binding var values: [String]
    var focusRequestID: Int?
    var onFocusRequestCompleted: (Int) -> Void
    @FocusState private var addFocused: Bool
    @AccessibilityFocusState private var addAccessibilityFocused: Bool
    @State private var rowIDs: [UUID] = []

    var body: some View {
        VStack(alignment: .trailing, spacing: 4) {
            ForEach(Array(rowIDs.enumerated()), id: \.element) {
                index, rowID in
                if values.indices.contains(index) {
                    let path = values[index]
                HStack(spacing: 6) {
                    Text((path as NSString).abbreviatingWithTildeInPath)
                        .font(.callout)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(path)
                    Button {
                        rowIDs.remove(at: index)
                        values.remove(at: index)
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(
                        Text("Remove \(label) path \(index + 1)")
                    )
                    .accessibilityHint(Text(accessibilityHint))
                    .accessibilityIdentifier(
                        accessibilityIdentifier + ".remove.\(index)"
                    )
                    .disabled(model.isPresentationFixture)
                }
                .id(rowID)
                }
            }
            Button("Add Folder…") {
                guard !model.isPresentationFixture else { return }
                let additions = LocalFilePanel.chooseDirectories(
                    message: "Add folders to \(label)"
                ).map(\.path)
                rowIDs.append(contentsOf: additions.map { _ in UUID() })
                values.append(contentsOf: additions)
            }
            .controlSize(.small)
            .focused($addFocused)
            .accessibilityFocused($addAccessibilityFocused)
            .accessibilityLabel(Text("Add folders to \(label)"))
            .accessibilityHint(Text(accessibilityHint))
            .accessibilityIdentifier(accessibilityIdentifier)
            .disabled(model.isPresentationFixture)
        }
        .onAppear { synchronizeRowIDs() }
        .onChange(of: values) { _, _ in synchronizeRowIDs() }
        .task(id: focusRequestID) {
            guard let focusRequestID else { return }
            await Task.yield()
            addFocused = true
            addAccessibilityFocused = true
            try? await Task.sleep(nanoseconds: 100_000_000)
            guard addFocused else { return }
            onFocusRequestCompleted(focusRequestID)
        }
    }

    private func synchronizeRowIDs() {
        if rowIDs.count > values.count {
            rowIDs.removeLast(rowIDs.count - values.count)
        } else if rowIDs.count < values.count {
            rowIDs.append(
                contentsOf: (rowIDs.count..<values.count).map { _ in UUID() }
            )
        }
    }
}

struct NumericDraftTextField: View {
    var label: String
    var prompt: String?
    @Binding var text: String
    var width: CGFloat
    var focusRequestID: Int?
    var accessibilityHint: String
    var accessibilityIdentifier: String
    var onFocusRequestCompleted: (Int) -> Void
    @State private var draft: String = ""
    @FocusState private var focused: Bool
    @AccessibilityFocusState private var accessibilityFocused: Bool

    init(
        label: String,
        prompt: String? = nil,
        text: Binding<String>,
        width: CGFloat,
        focusRequestID: Int? = nil,
        accessibilityHint: String = "",
        accessibilityIdentifier: String = "",
        onFocusRequestCompleted: @escaping (Int) -> Void = { _ in }
    ) {
        self.label = label
        self.prompt = prompt
        _text = text
        self.width = width
        self.focusRequestID = focusRequestID
        self.accessibilityHint = accessibilityHint
        self.accessibilityIdentifier = accessibilityIdentifier
        self.onFocusRequestCompleted = onFocusRequestCompleted
    }

    var body: some View {
        TextField(
            label,
            text: $draft,
            prompt: prompt.map(Text.init)
        )
            .labelsHidden()
            .textFieldStyle(.roundedBorder)
            .frame(width: width)
            .multilineTextAlignment(.trailing)
            .monospacedDigit()
            .focused($focused)
            .accessibilityFocused($accessibilityFocused)
            .accessibilityHint(Text(accessibilityHint))
            .accessibilityIdentifier(accessibilityIdentifier)
            .onAppear { draft = text }
            .onChange(of: text) { _, newValue in
                if !focused, draft != newValue {
                    draft = newValue
                }
            }
            .onChange(of: draft) { _, newValue in
                if focused, text != newValue {
                    text = newValue
                }
            }
            .onSubmit { commitDraft(normalize: false) }
            .onChange(of: focused) { _, isFocused in
                if isFocused {
                    if draft != text {
                        draft = text
                    }
                } else {
                    commitDraft(normalize: true)
                }
            }
            .task(id: focusRequestID) {
                guard let focusRequestID else { return }
                await Task.yield()
                focused = true
                accessibilityFocused = true
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard focused else { return }
                onFocusRequestCompleted(focusRequestID)
            }
            .accessibilityLabel(Text(label))
    }

    private func commitDraft(normalize: Bool) {
        if text != draft {
            text = draft
        }
        if normalize {
            let normalized = text
            if normalized != draft {
                draft = normalized
            }
        }
    }
}
