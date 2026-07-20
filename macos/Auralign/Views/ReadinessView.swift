// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

private enum ReadinessEvidenceSelection: Hashable {
    case model(String)
    case check(String)
}

/// Runtime and model evidence for the exact effective configuration.
///
/// This view does not infer readiness. It presents `AppModel.readinessDecision`
/// and the strictly accepted doctor/model payloads, with direct recovery and
/// selection-driven provenance detail.
struct ReadinessView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.auralignReduceMotion) private var reduceMotion

    @State private var selectedEvidence: ReadinessEvidenceSelection?
    @State private var passedExpanded = false
    @State private var otherModelsExpanded = false
    @FocusState private var focusedEvidence: ReadinessEvidenceSelection?
    @AccessibilityFocusState
    private var accessibilityFocusedEvidence: ReadinessEvidenceSelection?
    @AccessibilityFocusState private var summaryAccessibilityFocused: Bool

    private var decision: ReadinessDecision { model.readinessDecision }

    var body: some View {
        GeometryReader { geometry in
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
                        pageHeader
                        decisionPanel(proxy: proxy)
                        evidenceLayout(
                            showsSideInspector: geometry.size.width >= 900,
                            proxy: proxy
                        )
                        localOnlyPosture
                    }
                    .padding(AuralignVisualSystem.spacingXL)
                    .frame(maxWidth: 1_180, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                }
            }
        }
        .navigationTitle("Runtime & Models")
        .toolbar {
            ToolbarItem {
                Button {
                    model.requestReadinessRefresh()
                } label: {
                    Label("Refresh Readiness", systemImage: "arrow.clockwise")
                }
                .disabled(model.isRunningDoctor || model.workMutationLocked)
                .help("Re-run local readiness checks for the current configuration.")
                .accessibilityIdentifier(AccessibilityIdentifier.readinessRefresh)
            }
        }
    }

    private var pageHeader: some View {
        AuralignPageHeader(
            title: "Runtime & Models",
            subtitle:
                "Accepted local evidence for the exact effective configuration. "
                + "No model acquisition occurs here.",
            symbol: "cpu"
        ) {
            if model.isRunningDoctor {
                if reduceMotion {
                    Image(systemName: "clock")
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Readiness check in progress")
                } else {
                    ProgressView()
                        .controlSize(.small)
                        .accessibilityLabel("Readiness check in progress")
                }
            } else {
                AuralignStatusBadge(
                    text: decision.level.label,
                    tone: decisionTone,
                    symbol: decision.level.symbol
                )
            }
        }
    }

    private func decisionPanel(proxy: ScrollViewProxy) -> some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: decision.level.symbol)
                .font(.title3)
                .foregroundStyle(decisionTone.color)
                .frame(width: 24)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(decision.headline)
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier(AccessibilityIdentifier.readinessHeadline)

                Text(decision.explanation)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                if let cause = decision.cause {
                    Text(cause)
                        .font(.callout.weight(.medium))
                        .fixedSize(horizontal: false, vertical: true)
                }

                if decision.recovery == .refresh {
                    Text("Use Refresh Readiness in the toolbar to collect current evidence.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: AuralignVisualSystem.spacingM)
            recoveryControl(proxy: proxy)
        }
        .padding(AuralignVisualSystem.spacingM)
        .background(decisionTone.color.opacity(0.08))
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(decisionTone.color)
                .frame(width: 3)
        }
        .clipShape(RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius))
        .accessibilityElement(children: .contain)
        .accessibilityFocused($summaryAccessibilityFocused)
        .accessibilityIdentifier(AccessibilityIdentifier.readinessSummary)
        .id(AccessibilityIdentifier.readinessSummary)
    }

    @ViewBuilder
    private func recoveryControl(proxy: ScrollViewProxy) -> some View {
        switch decision.recovery {
        case .none, .refresh:
            EmptyView()
        case .openSettings:
            SettingsLink {
                Label("Open Settings", systemImage: "gear")
            }
            .controlSize(.small)
            .accessibilityIdentifier(AccessibilityIdentifier.readinessRecovery)
        case .showFirstConfigurationIssue:
            Button("Show Invalid Parameter") {
                _ = model.navigateToFirstConfigurationError()
            }
            .controlSize(.small)
            .accessibilityIdentifier(AccessibilityIdentifier.readinessRecovery)
        case .showModel(let identifier):
            Button("Show Affected Model") {
                selectEvidence(.model(identifier), proxy: proxy)
            }
            .controlSize(.small)
            .accessibilityIdentifier(AccessibilityIdentifier.readinessRecovery)
        case .showCheck(let identifier):
            Button(
                decision.level == .warnings
                    ? "Show Warning Check" : "Show Blocking Check"
            ) {
                selectEvidence(.check(identifier), proxy: proxy)
            }
            .controlSize(.small)
            .accessibilityIdentifier(AccessibilityIdentifier.readinessRecovery)
        }
    }

    @ViewBuilder
    private func evidenceLayout(
        showsSideInspector: Bool,
        proxy: ScrollViewProxy
    ) -> some View {
        if showsSideInspector {
            HStack(alignment: .top, spacing: AuralignVisualSystem.spacingXL) {
                primaryEvidence(
                    facetColumnCount: selectedEvidence == nil ? 4 : 2,
                    proxy: proxy
                )
                    .frame(maxWidth: .infinity, alignment: .topLeading)

                if let selectedEvidence {
                    evidenceInspector(selectedEvidence, proxy: proxy)
                        .frame(width: AuralignVisualSystem.inspectorWidth)
                        .transition(.opacity)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
                primaryEvidence(facetColumnCount: 2, proxy: proxy)
                if let selectedEvidence {
                    AuralignSection(
                        title: "Selected Evidence",
                        subtitle: "Context for the selected accepted row."
                    ) {
                        evidenceInspectorContent(selectedEvidence, proxy: proxy)
                    }
                }
            }
        }
    }

    private func primaryEvidence(
        facetColumnCount: Int,
        proxy: ScrollViewProxy
    ) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
            facets(columnCount: facetColumnCount)
            requiredModels(proxy: proxy)
            doctorEvidence(proxy: proxy)
        }
    }

    private func facets(columnCount: Int) -> some View {
        AuralignSection(
            title: "Readiness Evidence",
            subtitle: "Summary first; exact causes and provenance remain inspectable below."
        ) {
            LazyVGrid(
                columns: Array(
                    repeating: GridItem(.flexible(minimum: 0), spacing: 12),
                    count: columnCount
                ),
                alignment: .leading,
                spacing: 12
            ) {
                ForEach(facetValues) { facet in
                    facetView(facet)
                }
            }
        }
    }

    private var facetValues: [ReadinessFacet] {
        let engine: ReadinessFacet
        if model.isConnectingEngine {
            engine = ReadinessFacet(
                id: "engine",
                title: "Engine",
                value: "Connecting",
                detail: "Validating the local bridge",
                symbol: "clock",
                tone: .active
            )
        } else if model.bridge != nil, model.handshake != nil {
            engine = ReadinessFacet(
                id: "engine",
                title: "Engine",
                value: "Connected",
                detail: model.engineVersionText,
                symbol: "bolt.fill",
                tone: .success
            )
        } else {
            engine = ReadinessFacet(
                id: "engine",
                title: "Engine",
                value: "Unavailable",
                detail: "No compatible local bridge",
                symbol: "bolt.slash",
                tone: .neutral
            )
        }

        let required = selectedStatuses
        let accepted = required.filter { modelPresentation($0, required: true).accepted }
        let models: ReadinessFacet
        if required.isEmpty {
            models = ReadinessFacet(
                id: "models",
                title: "Required Models",
                value: "Not checked",
                detail: "No accepted current rows",
                symbol: "externaldrive",
                tone: .neutral
            )
        } else if accepted.count == required.count {
            models = ReadinessFacet(
                id: "models",
                title: "Required Models",
                value: "\(accepted.count) verified",
                detail: "Present, complete, and pinned",
                symbol: "checkmark.seal.fill",
                tone: .success
            )
        } else {
            let unresolved = required.count - accepted.count
            models = ReadinessFacet(
                id: "models",
                title: "Required Models",
                value: "\(unresolved) unresolved",
                detail: "\(required.count) selected by configuration",
                symbol: "externaldrive.badge.exclamationmark",
                tone: .failure
            )
        }

        let configuration = ReadinessFacet(
            id: "configuration",
            title: "Configuration",
            value: model.config.isValid ? "Validated" : "Blocked",
            detail: model.config.isValid
                ? "Exact effective values accepted"
                : "\(model.config.fieldErrors.count) invalid field"
                    + (model.config.fieldErrors.count == 1 ? "" : "s"),
            symbol: model.config.isValid
                ? "checkmark.circle.fill" : "exclamationmark.circle.fill",
            tone: model.config.isValid ? .success : .failure
        )

        let readiness = ReadinessFacet(
            id: "readiness",
            title: "Readiness",
            value: decision.level.label,
            detail: doctorCountDetail,
            symbol: decision.level.symbol,
            tone: decisionTone
        )
        return [engine, models, configuration, readiness]
    }

    private func facetView(_ facet: ReadinessFacet) -> some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingS) {
            Image(systemName: facet.symbol)
                .foregroundStyle(facet.tone.color)
                .frame(width: 20)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(facet.title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(facet.value)
                    .font(.callout.weight(.semibold))
                Text(facet.detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AuralignVisualSystem.spacingM)
        .frame(maxWidth: .infinity, minHeight: 86, alignment: .topLeading)
        .background(Color.primary.opacity(0.035))
        .clipShape(RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(facet.title)
        .accessibilityValue("\(facet.value). \(facet.detail)")
        .accessibilityIdentifier(AccessibilityIdentifier.readinessFacet(facet.id))
    }

    private func requiredModels(proxy: ScrollViewProxy) -> some View {
        AuralignSection(
            title: "Required Models",
            subtitle:
                "Only models selected by the effective configuration can establish readiness."
        ) {
            VStack(alignment: .leading, spacing: 0) {
                if let failure = model.modelEvidenceFailure {
                    evidenceFailureRow(failure)
                } else if selectedStatuses.isEmpty {
                    Text("Required-model evidence has not been accepted for this configuration.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    ForEach(selectedStatuses) { status in
                        modelRow(status, required: true, proxy: proxy)
                        if status.id != selectedStatuses.last?.id {
                            Divider().opacity(0.5)
                        }
                    }
                }

                if !otherStatuses.isEmpty {
                    Divider().padding(.top, AuralignVisualSystem.spacingS)
                    DisclosureGroup(
                        "Other reviewed models (\(otherStatuses.count))",
                        isExpanded: $otherModelsExpanded
                    ) {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(otherStatuses) { status in
                                modelRow(status, required: false, proxy: proxy)
                            }
                        }
                        .padding(.top, AuralignVisualSystem.spacingS)
                    }
                    .font(.callout)
                }
            }
        }
    }

    private func modelRow(
        _ status: ModelCacheStatus,
        required: Bool,
        proxy: ScrollViewProxy
    ) -> some View {
        let state = modelPresentation(status, required: required)
        let selection = ReadinessEvidenceSelection.model(status.id)
        let entry = manifestEntry(for: status)

        return Button {
            selectEvidence(selection, proxy: proxy)
        } label: {
            HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
                Image(systemName: state.symbol)
                    .foregroundStyle(state.tone.color)
                    .frame(width: 20)
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: AuralignVisualSystem.spacingS) {
                        Text(entry?.key ?? status.key)
                            .font(.callout.weight(.semibold))
                        Text(status.role)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if status.gated {
                            Text("Gated terms")
                                .font(.caption2.weight(.medium))
                                .foregroundStyle(.orange)
                        }
                    }
                    Text(status.modelId)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }

                Spacer(minLength: AuralignVisualSystem.spacingM)

                Text(state.label)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(state.tone.color)
                    .multilineTextAlignment(.trailing)
            }
            .contentShape(Rectangle())
            .padding(.vertical, 9)
            .padding(.horizontal, AuralignVisualSystem.spacingS)
            .background(
                selectedEvidence == selection
                    ? Color.accentColor.opacity(0.09) : Color.clear
            )
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
        .focused($focusedEvidence, equals: selection)
        .accessibilityFocused(
            $accessibilityFocusedEvidence,
            equals: selection
        )
        .accessibilityLabel("\(entry?.key ?? status.key), \(status.role)")
        .accessibilityValue(state.label)
        .accessibilityHint("Show model provenance and file-contract details.")
        .accessibilityIdentifier(
            AccessibilityIdentifier.readinessModel(role: status.role, key: status.key)
        )
        .id(modelScrollID(status.id))
    }

    private func doctorEvidence(proxy: ScrollViewProxy) -> some View {
        AuralignSection(
            title: "Runtime Checks",
            subtitle:
                "Blocking and warning causes stay visible; passed detail remains collapsed."
        ) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                if let failure = model.doctorEvidenceFailure {
                    evidenceFailureRow(failure)
                } else if let doctor = model.doctor {
                    doctorCounts(doctor)

                    if decision.level == .stale {
                        Label(
                            "Previous report shown for context; it does not describe the current configuration.",
                            systemImage: "arrow.clockwise.circle"
                        )
                        .font(.callout)
                        .foregroundStyle(.orange)
                    } else if issueChecks.isEmpty {
                        Label(
                            "No blocking or warning checks.",
                            systemImage: "checkmark.circle.fill"
                        )
                        .font(.callout)
                        .foregroundStyle(.green)
                    } else {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(issueChecks) { check in
                                checkRow(check, proxy: proxy)
                                if check.id != issueChecks.last?.id {
                                    Divider().opacity(0.5)
                                }
                            }
                        }
                    }

                    if !passedChecks.isEmpty {
                        DisclosureGroup(
                            "Passed and informational checks (\(passedChecks.count))",
                            isExpanded: $passedExpanded
                        ) {
                            VStack(alignment: .leading, spacing: 0) {
                                ForEach(passedChecks) { check in
                                    checkRow(check, proxy: proxy)
                                }
                            }
                            .padding(.top, AuralignVisualSystem.spacingS)
                        }
                        .font(.callout)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.readinessPassedDisclosure
                        )
                    }
                } else {
                    Text("Runtime checks have not produced accepted evidence.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func doctorCounts(_ doctor: DoctorSummary) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            if decision.level == .stale {
                Text("Previous report")
                    .font(.caption.weight(.semibold))
            }
            HStack(spacing: AuralignVisualSystem.spacingL) {
                Label(
                    quantified(doctor.errors.count, singular: "blocker"),
                    systemImage: doctor.errors.isEmpty
                        ? "checkmark.circle" : "xmark.octagon.fill"
                )
                Label(
                    quantified(doctor.warnings.count, singular: "warning"),
                    systemImage: "exclamationmark.triangle"
                )
                Label(
                    "\(passedChecks.count) passed or informational",
                    systemImage: "checkmark"
                )
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(AccessibilityIdentifier.readinessCounts)
    }

    private func checkRow(
        _ check: DoctorCheck,
        proxy: ScrollViewProxy
    ) -> some View {
        let state = checkPresentation(check)
        let selection = ReadinessEvidenceSelection.check(check.name)
        return Button {
            selectEvidence(selection, proxy: proxy)
        } label: {
            HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
                Image(systemName: state.symbol)
                    .foregroundStyle(state.tone.color)
                    .frame(width: 20)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(userFacingCheckTitle(check))
                        .font(.callout.weight(.semibold))
                    Text(safeMessage(check.message))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                Spacer(minLength: AuralignVisualSystem.spacingM)
                Text(state.label)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(state.tone.color)
            }
            .contentShape(Rectangle())
            .padding(.vertical, 9)
            .padding(.horizontal, AuralignVisualSystem.spacingS)
            .background(
                selectedEvidence == selection
                    ? Color.accentColor.opacity(0.09) : Color.clear
            )
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
        .focused($focusedEvidence, equals: selection)
        .accessibilityFocused(
            $accessibilityFocusedEvidence,
            equals: selection
        )
        .accessibilityLabel(userFacingCheckTitle(check))
        .accessibilityValue("\(state.label). \(safeMessage(check.message))")
        .accessibilityHint("Show the exact technical check identifier.")
        .accessibilityIdentifier(
            AccessibilityIdentifier.readinessCheck(index: checkIndex(check))
        )
        .id(checkScrollID(check.name))
    }

    private func evidenceFailureRow(_ failure: ReadinessEvidenceFailure) -> some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(
                systemName: failure.kind == .unavailable
                    ? "bolt.slash" : "xmark.octagon.fill"
            )
            .foregroundStyle(failure.kind == .unavailable ? Color.gray : Color.red)
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(failure.summary)
                    .font(.callout.weight(.semibold))
                Text("Evidence classification: \(failure.kind.label).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let detail = failure.technicalDetail {
                    DisclosureGroup("Technical detail") {
                        Text(safeMessage(detail))
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .font(.caption)
                }
            }
        }
    }

    private func evidenceInspector(
        _ selection: ReadinessEvidenceSelection,
        proxy: ScrollViewProxy
    ) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
            Text("Evidence Detail")
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
            Divider()
            evidenceInspectorContent(selection, proxy: proxy)
        }
        .padding(AuralignVisualSystem.spacingM)
        .background(Color.primary.opacity(0.035))
        .clipShape(RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius))
        .accessibilityIdentifier(AccessibilityIdentifier.readinessInspector)
    }

    @ViewBuilder
    private func evidenceInspectorContent(
        _ selection: ReadinessEvidenceSelection,
        proxy: ScrollViewProxy
    ) -> some View {
        switch selection {
        case .model(let identifier):
            modelInspector(identifier)
        case .check(let identifier):
            checkInspector(identifier)
        }

        Divider()
        Button("Return to Readiness Summary") {
            returnToSummary(proxy: proxy)
        }
        .controlSize(.small)
        .accessibilityIdentifier(AccessibilityIdentifier.readinessReturn)
    }

    @ViewBuilder
    private func modelInspector(_ identifier: String) -> some View {
        if let status = model.modelStatuses.first(where: { $0.id == identifier }),
           let entry = manifestEntry(for: status) {
            let state = modelPresentation(status, required: status.selectedForConfig)
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(entry.key)
                        .font(.title3.weight(.semibold))
                    Text(entry.role)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    AuralignStatusBadge(
                        text: state.label,
                        tone: state.tone,
                        symbol: state.symbol
                    )
                }

                LabeledContent("Model ID", value: entry.modelId)
                LabeledContent("Revision", value: entry.revision ?? "Unpinned")
                LabeledContent(
                    "License",
                    value: entry.licenseExpression ?? "Unrecorded"
                )
                LabeledContent("Review", value: entry.reviewStatus ?? "Unrecorded")
                LabeledContent("Runtime", value: entry.runtimeStatus ?? "Unrecorded")
                LabeledContent("Local directory", value: entry.directoryName)

                fileContract(entry)

                if entry.gated {
                    Label(
                        "The operator must separately accept upstream gated terms.",
                        systemImage: "lock"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                if let problem = status.problem {
                    Text(safeMessage(problem))
                        .font(.caption)
                        .foregroundStyle(.red)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let source = entry.sourceURL, let url = URL(string: source) {
                    Link("Reviewed source", destination: url)
                }
                if let terms = entry.termsURL, let url = URL(string: terms) {
                    Link("Upstream terms", destination: url)
                }
            }
            .font(.callout)
            .textSelection(.enabled)
        } else {
            Text("The selected model row is no longer part of current accepted evidence.")
                .foregroundStyle(.secondary)
        }
    }

    private func fileContract(_ entry: ModelManifestEntry) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
            Text("Expected Files")
                .font(.callout.weight(.semibold))
                .accessibilityAddTraits(.isHeader)

            if !entry.requiredFiles.isEmpty {
                Text("Required")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                ForEach(entry.requiredFiles, id: \.self) { filename in
                    Label(filename, systemImage: "doc")
                        .font(.caption.monospaced())
                }
            }

            if !entry.anyRequiredFiles.isEmpty {
                Text("At least one alternative")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                ForEach(entry.anyRequiredFiles, id: \.self) { filename in
                    Label(filename, systemImage: "doc")
                        .font(.caption.monospaced())
                }
            }

            Text("\(entry.runtimeFiles.count) reviewed runtime file"
                    + (entry.runtimeFiles.count == 1 ? "" : "s")
                    + " in the local allowlist.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func checkInspector(_ identifier: String) -> some View {
        if let check = model.doctor?.checks.first(where: { $0.name == identifier }) {
            let state = checkPresentation(check)
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                Text(userFacingCheckTitle(check))
                    .font(.title3.weight(.semibold))
                AuralignStatusBadge(
                    text: state.label,
                    tone: state.tone,
                    symbol: state.symbol
                )
                Text(safeMessage(check.message))
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Technical check identifier")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(check.name)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                }
            }
        } else {
            Text("The selected check is no longer part of current accepted evidence.")
                .foregroundStyle(.secondary)
        }
    }

    private var localOnlyPosture: some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: "lock.shield")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text("Local-first runtime")
                    .font(.callout.weight(.semibold))
                Text(
                    "Readiness checks run against the selected local checkout and cache. "
                        + "Auralign does not upload media, send telemetry, download models "
                        + "automatically, or acquire gated access."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var selectedStatuses: [ModelCacheStatus] {
        model.modelStatuses.filter(\.selectedForConfig)
    }

    private var otherStatuses: [ModelCacheStatus] {
        model.modelStatuses.filter { !$0.selectedForConfig }
    }

    private var issueChecks: [DoctorCheck] {
        model.doctor?.checks.filter {
            $0.status == "error" || $0.status == "warning"
        } ?? []
    }

    private var passedChecks: [DoctorCheck] {
        model.doctor?.checks.filter {
            $0.status == "ok" || $0.status == "info"
        } ?? []
    }

    private var doctorCountDetail: String {
        guard let doctor = model.doctor else { return "No accepted current report" }
        if decision.level == .stale {
            return "Previous report · refresh required"
        }
        return quantified(doctor.errors.count, singular: "blocker")
            + " · "
            + quantified(doctor.warnings.count, singular: "warning")
    }

    private func quantified(_ count: Int, singular: String) -> String {
        "\(count) \(singular)\(count == 1 ? "" : "s")"
    }

    private var decisionTone: AuralignStatusTone {
        switch decision.level {
        case .unknown, .unavailable:
            return .neutral
        case .checking:
            return .active
        case .stale, .warnings:
            return .warning
        case .ready:
            return .success
        case .blocked:
            return .failure
        }
    }

    private func manifestEntry(
        for status: ModelCacheStatus
    ) -> ModelManifestEntry? {
        model.manifest.modelManifest.first { $0.id == status.id }
    }

    private func modelPresentation(
        _ status: ModelCacheStatus,
        required: Bool
    ) -> ReadinessRowPresentation {
        guard let entry = manifestEntry(for: status), entry.isRuntimeSelectable else {
            return ReadinessRowPresentation(
                accepted: false,
                label: "Policy blocked",
                symbol: "exclamationmark.shield.fill",
                tone: .failure
            )
        }
        if status.present, !status.partial, status.revisionVerified, status.problem == nil {
            return ReadinessRowPresentation(
                accepted: true,
                label: "Revision verified",
                symbol: "checkmark.circle.fill",
                tone: .success
            )
        }
        if !required, !status.present, !status.partial {
            return ReadinessRowPresentation(
                accepted: false,
                label: "Not cached",
                symbol: "circle.dashed",
                tone: .neutral
            )
        }
        if status.partial {
            return ReadinessRowPresentation(
                accepted: false,
                label: "Incomplete",
                symbol: "exclamationmark.triangle.fill",
                tone: .failure
            )
        }
        if status.present, !status.revisionVerified {
            return ReadinessRowPresentation(
                accepted: false,
                label: "Revision unverified",
                symbol: "exclamationmark.triangle.fill",
                tone: .failure
            )
        }
        if status.problem != nil {
            return ReadinessRowPresentation(
                accepted: false,
                label: status.present
                    ? "Local cache problem" : "Not available locally",
                symbol: "xmark.octagon.fill",
                tone: .failure
            )
        }
        return ReadinessRowPresentation(
            accepted: false,
            label: required ? "Required, not cached" : "Not cached",
            symbol: required ? "xmark.octagon.fill" : "circle.dashed",
            tone: required ? .failure : .neutral
        )
    }

    private func checkPresentation(
        _ check: DoctorCheck
    ) -> ReadinessRowPresentation {
        switch check.status {
        case "ok":
            return ReadinessRowPresentation(
                accepted: true,
                label: "Passed",
                symbol: "checkmark.circle.fill",
                tone: .success
            )
        case "warning":
            return ReadinessRowPresentation(
                accepted: true,
                label: "Warning",
                symbol: "exclamationmark.triangle.fill",
                tone: .warning
            )
        case "error":
            return ReadinessRowPresentation(
                accepted: false,
                label: "Blocking",
                symbol: "xmark.octagon.fill",
                tone: .failure
            )
        default:
            return ReadinessRowPresentation(
                accepted: true,
                label: "Information",
                symbol: "info.circle",
                tone: .neutral
            )
        }
    }

    private func userFacingCheckTitle(_ check: DoctorCheck) -> String {
        let components = check.name.split(separator: ":").map(String.init)
        switch components.first {
        case "startup":
            return "Backend startup"
        case "model":
            return "Required model"
        case "ffmpeg", "ffprobe":
            return "Media tools"
        case "offline":
            return "Offline mode"
        default:
            let source = components.last ?? check.name
            return source
                .replacingOccurrences(of: "-", with: " ")
                .replacingOccurrences(of: "_", with: " ")
                .capitalized
        }
    }

    private func safeMessage(_ message: String) -> String {
        model.privacySafePresentationMessage(message)
            .replacingOccurrences(
                of: FileManager.default.homeDirectoryForCurrentUser.path,
                with: "~"
            )
    }

    private func checkIndex(_ check: DoctorCheck) -> Int {
        model.doctor?.checks.firstIndex(where: { $0.name == check.name }) ?? 0
    }

    private func modelScrollID(_ identifier: String) -> String {
        "readiness.model.scroll.\(identifier)"
    }

    private func checkScrollID(_ identifier: String) -> String {
        "readiness.check.scroll.\(identifier)"
    }

    private func selectEvidence(
        _ selection: ReadinessEvidenceSelection,
        proxy: ScrollViewProxy
    ) {
        selectedEvidence = selection
        let scrollID: String
        let announcement: String
        switch selection {
        case .model(let identifier):
            scrollID = modelScrollID(identifier)
            let key = model.manifest.modelManifest.first {
                $0.id == identifier
            }?.key ?? "model"
            announcement = "Affected model focused. \(key)."
        case .check(let identifier):
            scrollID = checkScrollID(identifier)
            let check = model.doctor?.checks.first { $0.name == identifier }
            announcement = check.map {
                "Readiness check focused. \(userFacingCheckTitle($0))."
            } ?? "Readiness check focused."
        }

        Task { @MainActor in
            // Reveal/selection precedes scrolling; focus follows only after the
            // selected row and optional inspector have completed a layout pass.
            await Task.yield()
            if reduceMotion {
                proxy.scrollTo(scrollID, anchor: .center)
            } else {
                withAnimation(.easeInOut(duration: 0.18)) {
                    proxy.scrollTo(scrollID, anchor: .center)
                }
            }
            await Task.yield()
            focusedEvidence = selection
            accessibilityFocusedEvidence = selection
            await Task.yield()
            AccessibilityAnnouncementCenter.shared.announce(
                announcement,
                token:
                    "readiness-focus-\(decision.announcementToken.hashValue)-"
                    + "\(scrollID.hashValue)"
            )
        }
    }

    private func returnToSummary(proxy: ScrollViewProxy) {
        Task { @MainActor in
            if reduceMotion {
                proxy.scrollTo(AccessibilityIdentifier.readinessSummary, anchor: .top)
            } else {
                withAnimation(.easeInOut(duration: 0.18)) {
                    proxy.scrollTo(
                        AccessibilityIdentifier.readinessSummary,
                        anchor: .top
                    )
                }
            }
            await Task.yield()
            focusedEvidence = nil
            accessibilityFocusedEvidence = nil
            summaryAccessibilityFocused = true
            await Task.yield()
            AccessibilityAnnouncementCenter.shared.announce(
                "Readiness summary focused.",
                token:
                    "readiness-summary-focus-"
                    + "\(decision.announcementToken.hashValue)"
            )
        }
    }
}

private struct ReadinessFacet: Identifiable {
    let id: String
    let title: String
    let value: String
    let detail: String
    let symbol: String
    let tone: AuralignStatusTone
}

private struct ReadinessRowPresentation {
    let accepted: Bool
    let label: String
    let symbol: String
    let tone: AuralignStatusTone
}

#if DEBUG

private struct ReadinessPreviewRoot: View {
    let fixture: PresentationFixture
    private let model: AppModel

    init(_ fixture: PresentationFixture) {
        self.fixture = fixture
        model = AppModel.makePresentationFixture(fixture)
    }

    var body: some View {
        ReadinessView()
            .environmentObject(model)
            .frame(width: 1_180, height: 780)
    }
}

#Preview("Runtime Ready") {
    ReadinessPreviewRoot(.readinessReady)
}

#Preview("Runtime Blocked") {
    ReadinessPreviewRoot(.readinessBlocked)
}

#Preview("Partial Evidence") {
    ReadinessPreviewRoot(.partialEvidence)
}

#endif
