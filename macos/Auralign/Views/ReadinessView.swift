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
    @Environment(\.openWindow) private var openWindow

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
                        offlineCoreMap
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
            if model.modelSetup.needsOfflineSetup {
                ToolbarItem {
                    Button {
                        openWindow(id: "offline-setup")
                    } label: {
                        Label(
                            "Complete Offline Setup",
                            systemImage: "arrow.down.circle"
                        )
                    }
                    .help("Install and verify the model packs required by normal presets.")
                    .accessibilityIdentifier(AccessibilityIdentifier.modelSetupOpen)
                }
            }
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
        AuralignWorkspaceHeader(
            symbol: "cpu",
            accessibilityHeading: "Runtime & Models"
        ) {
            VStack(alignment: .leading, spacing: 2) {
                Text(engineConnectionText)
                    .font(AuralignType.rowTitle)
                Text("Preset: \(model.config.selectedPreset.capitalized)")
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
            }
        } accessory: {
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

    private var engineConnectionText: String {
        if model.isConnectingEngine { return "Connecting to local engine…" }
        if model.bridge != nil, model.handshake != nil {
            return "Engine connected · \(model.engineVersionText)"
        }
        return "Engine not connected"
    }

    /// A representative system topology, not a diagnostic graph. Its four
    /// nodes are driven by the accepted evidence summaries below; the exact
    /// ledger remains the textual authority.
    private var offlineCoreMap: some View {
        AuralignStudioPanel(accent: .source) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("OFFLINE CORE")
                            .font(AuralignType.eyebrow)
                            .tracking(1.45)
                            .foregroundStyle(.secondary)
                        Text("One sealed local system")
                            .font(AuralignType.workspaceTitle)
                    }
                    Spacer()
                    Text(decision.level.label.uppercased())
                        .font(AuralignType.badge.weight(.bold))
                        .tracking(1.1)
                        .foregroundStyle(decisionTone.color)
                }

                GeometryReader { geometry in
                    Canvas { context, size in
                        let center = CGPoint(x: size.width / 2, y: size.height / 2)
                        for index in 0..<min(facetValues.count, 4) {
                            let node = coreNodePoint(index, in: size)
                            var path = Path()
                            path.move(
                                to: coreConnectorStartPoint(
                                    from: center,
                                    toward: node
                                )
                            )
                            path.addLine(to: node)
                            context.stroke(
                                path,
                                with: .color(
                                    facetValues[index].tone.color.opacity(0.38)
                                ),
                                style: StrokeStyle(
                                    lineWidth: 1,
                                    dash: [4, 6]
                                )
                            )
                        }
                    }
                    .accessibilityHidden(true)

                    ZStack {
                        coreSeal(in: geometry.size)
                            .position(
                                x: geometry.size.width / 2,
                                y: geometry.size.height / 2
                            )
                        ForEach(Array(facetValues.prefix(4).enumerated()), id: \.element.id) {
                            index, facet in
                            coreNode(facet)
                                .position(coreNodePoint(index, in: geometry.size))
                        }
                    }
                    .accessibilityHidden(true)
                }
                .frame(height: 238)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Offline core. "
                + facetValues.prefix(4)
                .map { "\($0.title): \($0.value)" }
                .joined(separator: ". ")
        )
    }

    private func coreSeal(in size: CGSize) -> some View {
        AuralignCoreLens(
            tone: decisionTone,
            symbol: decision.level.symbol,
            connectorColors: Array(
                facetValues.prefix(4).map { $0.tone.color }
            ),
            connectorDirections: coreConnectorDirections(in: size),
            isActiveOperation:
                model.isConnectingEngine || model.isRunningDoctor
        )
    }

    private func coreNode(_ facet: ReadinessFacet) -> some View {
        HStack(spacing: 9) {
            ZStack {
                Circle()
                    .fill(facet.tone.color.opacity(0.14))
                    .frame(width: 34, height: 34)
                Image(systemName: facet.symbol)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(facet.tone.color)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(facet.title)
                    .font(AuralignType.status)
                    .lineLimit(1)
                Text(facet.value)
                    .font(AuralignType.microcopy)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 11)
        .frame(width: 168, height: 52, alignment: .leading)
        .background(
            AuralignSurface.raised,
            in: RoundedRectangle(cornerRadius: AuralignRadius.group)
        )
    }

    private func coreNodePoint(_ index: Int, in size: CGSize) -> CGPoint {
        switch index {
        case 0:
            return CGPoint(x: 92, y: 50)
        case 1:
            return CGPoint(x: size.width - 92, y: 50)
        case 2:
            return CGPoint(x: 92, y: size.height - 50)
        default:
            return CGPoint(x: size.width - 92, y: size.height - 50)
        }
    }

    private func coreConnectorDirections(in size: CGSize) -> [CGVector] {
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        return (0..<min(facetValues.count, 4)).map { index in
            let node = coreNodePoint(index, in: size)
            let delta = CGVector(
                dx: node.x - center.x,
                dy: node.y - center.y
            )
            let distance = max(hypot(delta.dx, delta.dy), 1)
            return CGVector(
                dx: delta.dx / distance,
                dy: delta.dy / distance
            )
        }
    }

    private func coreConnectorStartPoint(
        from center: CGPoint,
        toward node: CGPoint
    ) -> CGPoint {
        let delta = CGVector(
            dx: node.x - center.x,
            dy: node.y - center.y
        )
        let distance = max(hypot(delta.dx, delta.dy), 1)
        let lensRadius = AuralignCoreLiquidLensTuning.radius
        return CGPoint(
            x: center.x + delta.dx / distance * lensRadius,
            y: center.y + delta.dy / distance * lensRadius
        )
    }

    private func decisionPanel(proxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
            AuralignCausalityBanner(
                headline: decision.headline,
                detail: decisionDetail,
                tone: decisionTone,
                symbol: decision.level.symbol
            )
            .accessibilityIdentifier(AccessibilityIdentifier.readinessHeadline)

            // Only the states whose accepted evidence was itself rejected
            // (malformed/partial/inconsistent) get this row — a plain blocking
            // check or missing model never populates `doctorEvidenceFailure`/
            // `modelEvidenceFailure`, so this keeps that class of state
            // structurally distinct above the fold rather than sharing the
            // blocked banner's exact shape.
            if let promotedFailure {
                evidenceFailureRow(promotedFailure)
                    .padding(AuralignVisualSystem.spacingM)
                    .background(Color.primary.opacity(0.035))
                    .clipShape(
                        RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius)
                    )
            }

            if recoveryControlIsVisible {
                recoveryControl(proxy: proxy)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityFocused($summaryAccessibilityFocused)
        .accessibilityIdentifier(AccessibilityIdentifier.readinessSummary)
        .id(AccessibilityIdentifier.readinessSummary)
    }

    private var decisionDetail: String {
        var lines = [decision.explanation]
        if let cause = decision.cause {
            lines.append(cause)
        }
        if decision.recovery == .refresh {
            lines.append("Use Refresh Readiness in the toolbar to collect current evidence.")
        }
        return lines.joined(separator: "\n")
    }

    /// The one rejected-evidence fact, when the current decision is actually
    /// caused by unusable doctor/model evidence rather than an ordinary
    /// blocking check or missing model.
    private var promotedFailure: ReadinessEvidenceFailure? {
        model.doctorEvidenceFailure ?? model.modelEvidenceFailure
    }

    private var recoveryControlIsVisible: Bool {
        switch decision.recovery {
        case .none, .refresh: return false
        default: return true
        }
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
        case .completeOfflineSetup:
            Button {
                openWindow(id: "offline-setup")
            } label: {
                Label("Complete Offline Setup", systemImage: "arrow.down.circle")
            }
            .buttonStyle(.borderedProminent)
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
                primaryEvidence(proxy: proxy)
                    .frame(maxWidth: .infinity, alignment: .topLeading)

                if let selectedEvidence {
                    evidenceInspector(selectedEvidence, proxy: proxy)
                        .frame(width: AuralignVisualSystem.inspectorWidth)
                        .transition(.opacity)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
                primaryEvidence(proxy: proxy)
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

    private func primaryEvidence(proxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXL) {
            facets()
            requiredModels(proxy: proxy)
            doctorEvidence(proxy: proxy)
        }
    }

    /// Four accepted-evidence facts as a real differentiated list rather than
    /// a grid of equal-weight tiles distinguished only by a small glyph: each
    /// row's swatch, trailing value, and metadata line all vary with the
    /// real `AuralignStatusTone`, and every row stays legible in greyscale
    /// because status is also carried as explicit text, not fill alone.
    private func facets() -> some View {
        AuralignSection(
            title: "Readiness Evidence",
            subtitle: "Summary first; exact causes and provenance remain inspectable below."
        ) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
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
        AuralignMediaRow(
            title: facet.title,
            metadata: facet.detail,
            swatch: auralignMediaRowSwatch(symbol: facet.symbol, tint: facet.tone.color),
            trailing: {
                Text(facet.value)
                    .font(AuralignType.rowTitle)
                    .foregroundStyle(facet.tone.color)
                    .multilineTextAlignment(.trailing)
            },
            action: nil
        )
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
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                if let failure = model.modelEvidenceFailure {
                    evidenceFailureRow(failure)
                } else if selectedStatuses.isEmpty {
                    Text("Required-model evidence has not been accepted for this configuration.")
                        .font(AuralignType.body)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    ForEach(selectedStatuses) { status in
                        modelRow(status, required: true, proxy: proxy)
                    }
                }

                if !otherStatuses.isEmpty {
                    DisclosureGroup(
                        "Other reviewed models (\(otherStatuses.count))",
                        isExpanded: $otherModelsExpanded
                    ) {
                        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                            ForEach(otherStatuses) { status in
                                modelRow(status, required: false, proxy: proxy)
                            }
                        }
                        .padding(.top, AuralignVisualSystem.spacingS)
                    }
                    .font(AuralignType.body)
                }
            }
        }
    }

    /// A list-of-facts row (installed models) as `AuralignMediaRow`: swatch
    /// tint and trailing text both carry the real `AuralignStatusTone`, so a
    /// row's state is never distinguished only by its 20pt glyph.
    private func modelRow(
        _ status: ModelCacheStatus,
        required: Bool,
        proxy: ScrollViewProxy
    ) -> some View {
        let state = modelPresentation(status, required: required)
        let selection = ReadinessEvidenceSelection.model(status.id)
        let entry = manifestEntry(for: status)
        let metadata = [status.role, status.modelId, status.gated ? "Gated terms" : nil]
            .compactMap { $0 }
            .joined(separator: " · ")

        return Button {
            selectEvidence(selection, proxy: proxy)
        } label: {
            AuralignMediaRow(
                title: entry?.key ?? status.key,
                metadata: metadata,
                swatch: auralignMediaRowSwatch(symbol: state.symbol, tint: state.tone.color),
                trailing: {
                    Text(state.label)
                        .font(AuralignType.metadataStrong)
                        .foregroundStyle(state.tone.color)
                        .multilineTextAlignment(.trailing)
                },
                action: nil
            )
            .overlay {
                if selectedEvidence == selection {
                    RoundedRectangle(cornerRadius: AuralignRadius.group)
                        .stroke(Color.accentColor, lineWidth: 2)
                }
            }
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
                        .font(AuralignType.body)
                        .foregroundStyle(.orange)
                    } else if issueChecks.isEmpty {
                        Label(
                            "No blocking or warning checks.",
                            systemImage: "checkmark.circle.fill"
                        )
                        .font(AuralignType.body)
                        .foregroundStyle(.green)
                    } else {
                        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                            ForEach(issueChecks) { check in
                                checkRow(check, proxy: proxy)
                            }
                        }
                    }

                    if !passedChecks.isEmpty {
                        DisclosureGroup(
                            "Passed and informational checks (\(passedChecks.count))",
                            isExpanded: $passedExpanded
                        ) {
                            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                                ForEach(passedChecks) { check in
                                    checkRow(check, proxy: proxy)
                                }
                            }
                            .padding(.top, AuralignVisualSystem.spacingS)
                        }
                        .font(AuralignType.body)
                        .accessibilityIdentifier(
                            AccessibilityIdentifier.readinessPassedDisclosure
                        )
                    }
                } else {
                    Text("Runtime checks have not produced accepted evidence.")
                        .font(AuralignType.body)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func doctorCounts(_ doctor: DoctorSummary) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
            if decision.level == .stale {
                Text("Previous report")
                    .font(AuralignType.metadataStrong)
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
        .font(AuralignType.numericSmall)
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(AccessibilityIdentifier.readinessCounts)
    }

    /// A list-of-facts row (runtime dependency checks) as `AuralignMediaRow`.
    /// The full check message remains available, with text selection, in the
    /// evidence inspector once a row is selected — this scanning row only
    /// needs to say enough to tell rows apart.
    private func checkRow(
        _ check: DoctorCheck,
        proxy: ScrollViewProxy
    ) -> some View {
        let state = checkPresentation(check)
        let selection = ReadinessEvidenceSelection.check(check.name)
        return Button {
            selectEvidence(selection, proxy: proxy)
        } label: {
            AuralignMediaRow(
                title: userFacingCheckTitle(check),
                metadata: safeMessage(check.message),
                swatch: auralignMediaRowSwatch(symbol: state.symbol, tint: state.tone.color),
                trailing: {
                    Text(state.label)
                        .font(AuralignType.metadataStrong)
                        .foregroundStyle(state.tone.color)
                },
                action: nil
            )
            .overlay {
                if selectedEvidence == selection {
                    RoundedRectangle(cornerRadius: AuralignRadius.group)
                        .stroke(Color.accentColor, lineWidth: 2)
                }
            }
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
        let tone: AuralignStatusTone = failure.kind == .unavailable ? .neutral : .failure
        return HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(
                systemName: failure.kind == .unavailable
                    ? "bolt.slash" : "xmark.octagon.fill"
            )
            .foregroundStyle(tone.color)
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(failure.summary)
                    .font(AuralignType.rowTitle)
                Text("Evidence classification: \(failure.kind.label).")
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
                if let detail = failure.technicalDetail {
                    DisclosureGroup("Technical detail") {
                        Text(safeMessage(detail))
                            .font(AuralignType.technical)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .font(AuralignType.metadata)
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
                .font(AuralignType.sectionTitle)
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
                        .font(AuralignType.workspaceTitle)
                    Text(entry.role)
                        .font(AuralignType.metadata)
                        .foregroundStyle(.secondary)
                    AuralignStatusBadge(
                        text: state.label,
                        tone: state.tone,
                        symbol: state.symbol
                    )
                }

                technicalLabeledContent("Model ID", value: entry.modelId)
                technicalLabeledContent(
                    "Revision",
                    value: entry.revision ?? "Unpinned"
                )
                technicalLabeledContent(
                    "License",
                    value: entry.licenseExpression ?? "Unrecorded"
                )
                technicalLabeledContent(
                    "Review",
                    value: entry.reviewStatus ?? "Unrecorded"
                )
                technicalLabeledContent(
                    "Runtime",
                    value: entry.runtimeStatus ?? "Unrecorded"
                )
                technicalLabeledContent(
                    "Local directory",
                    value: entry.directoryName
                )

                fileContract(entry)

                if entry.gated {
                    Label(
                        "The operator must separately accept upstream gated terms.",
                        systemImage: "lock"
                )
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
                }
                if let problem = status.problem {
                    Text(safeMessage(problem))
                        .font(AuralignType.metadata)
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
            .font(AuralignType.body)
            .textSelection(.enabled)
        } else {
            Text("The selected model row is no longer part of current accepted evidence.")
                .foregroundStyle(.secondary)
        }
    }

    private func fileContract(_ entry: ModelManifestEntry) -> some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
            Text("Expected Files")
                .font(AuralignType.bodyEmphasis)
                .accessibilityAddTraits(.isHeader)

            if !entry.requiredFiles.isEmpty {
                Text("Required")
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
                ForEach(entry.requiredFiles, id: \.self) { filename in
                    Label(filename, systemImage: "doc")
                        .font(AuralignType.technical)
                }
            }

            if !entry.anyRequiredFiles.isEmpty {
                Text("At least one alternative")
                    .font(AuralignType.metadata)
                    .foregroundStyle(.secondary)
                ForEach(entry.anyRequiredFiles, id: \.self) { filename in
                    Label(filename, systemImage: "doc")
                        .font(AuralignType.technical)
                }
            }

            Text("\(entry.runtimeFiles.count) reviewed runtime file"
                    + (entry.runtimeFiles.count == 1 ? "" : "s")
                    + " in the local allowlist.")
                .font(AuralignType.technicalSmall)
                .foregroundStyle(.secondary)
        }
    }

    private func technicalLabeledContent(
        _ label: String,
        value: String
    ) -> some View {
        LabeledContent(label) {
            Text(value)
                .font(AuralignType.technicalBody)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }

    @ViewBuilder
    private func checkInspector(_ identifier: String) -> some View {
        if let check = model.doctor?.checks.first(where: { $0.name == identifier }) {
            let state = checkPresentation(check)
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                Text(userFacingCheckTitle(check))
                    .font(AuralignType.workspaceTitle)
                AuralignStatusBadge(
                    text: state.label,
                    tone: state.tone,
                    symbol: state.symbol
                )
                Text(safeMessage(check.message))
                    .font(AuralignType.body)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Technical check identifier")
                        .font(AuralignType.metadata)
                        .foregroundStyle(.secondary)
                    Text(check.name)
                        .font(AuralignType.technical)
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
                    .font(AuralignType.bodyEmphasis)
                Text(
                    "Readiness checks and normal processing use verified local files only. "
                        + "Auralign downloads public model data only after an explicit "
                        + "Complete Offline Setup action; it does not upload media, send "
                        + "telemetry, or acquire gated access."
                )
                .font(AuralignType.metadata)
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

private enum AuralignCoreLiquidLensTuning {
    static let visibleDiameter: CGFloat = 72
    static let radius: CGFloat = visibleDiameter / 2
    static let sampleEnvelope: CGFloat = 6
    static let sourceDiameter: CGFloat =
        visibleDiameter + sampleEnvelope * 2
}

/// The Offline Core's single purpose-built optical object.
///
/// Large structural planes share `AuralignOpticalSurface`; this smaller lens
/// earns a different construction because it represents the accepted system
/// decision at the center of the topology. Its refraction is static and
/// decorative, while the real readiness level remains semantic text in the
/// surrounding workspace and accessibility label.
private struct AuralignCoreLens: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.controlActiveState) private var controlActiveState

    let tone: AuralignStatusTone
    let symbol: String
    let connectorColors: [Color]
    let connectorDirections: [CGVector]
    let isActiveOperation: Bool

    var body: some View {
        ZStack {
            Circle()
                .fill(outerFill)
                .frame(width: 92, height: 92)

            if opticalDetailIsVisible {
                AuralignCoreConnectorRefraction(
                    colors: connectorColors,
                    directions: connectorDirections
                )
                    .frame(
                        width: AuralignCoreLiquidLensTuning.visibleDiameter,
                        height: AuralignCoreLiquidLensTuning.visibleDiameter
                    )
                    .clipShape(Circle())
            }

            if !opticalDetailIsVisible {
                Circle()
                    .fill(lensFill)
                    .frame(
                        width: AuralignCoreLiquidLensTuning.visibleDiameter,
                        height: AuralignCoreLiquidLensTuning.visibleDiameter
                    )
            }

            AuralignCoreLensMetalMaterial(
                accent: presentationColor,
                isActiveOperation: isActiveOperation
            )
                .frame(
                    width: AuralignCoreLiquidLensTuning.visibleDiameter,
                    height: AuralignCoreLiquidLensTuning.visibleDiameter
                )

            Circle()
                .strokeBorder(
                    presentationColor.opacity(increaseContrast ? 1 : 0.72),
                    lineWidth: increaseContrast ? 2 : 1.35
                )
                .frame(
                    width: AuralignCoreLiquidLensTuning.visibleDiameter,
                    height: AuralignCoreLiquidLensTuning.visibleDiameter
                )

            if opticalDetailIsVisible {
                Circle()
                    .trim(from: 0.54, to: 0.84)
                    .stroke(
                        Color.white.opacity(0.48),
                        style: StrokeStyle(
                            lineWidth: 1.05,
                            lineCap: .round
                        )
                    )
                    .rotationEffect(.degrees(18))
                    .frame(width: 66, height: 66)

                Circle()
                    .trim(from: 0.06, to: 0.27)
                    .stroke(
                        AuralignSpectrumStop.source.color.opacity(0.34),
                        style: StrokeStyle(
                            lineWidth: 0.75,
                            lineCap: .round
                        )
                    )
                    .frame(width: 80, height: 80)
                    .offset(x: -0.45, y: -0.25)

                Circle()
                    .trim(from: 0.55, to: 0.76)
                    .stroke(
                        AuralignSpectrumStop.delivery.color.opacity(0.30),
                        style: StrokeStyle(
                            lineWidth: 0.75,
                            lineCap: .round
                        )
                    )
                    .frame(width: 80, height: 80)
                    .offset(x: 0.45, y: 0.25)
            }

            Image(systemName: symbol)
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(presentationColor)
        }
        .saturation(controlActiveState == .inactive ? 0.42 : 1)
        .shadow(
            color: presentationColor.opacity(
                opticalDetailIsVisible
                    ? (controlActiveState == .inactive ? 0.05 : 0.11)
                    : 0
            ),
            radius: 9,
            y: 2
        )
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private var presentationColor: Color {
        if increaseContrast || differentiateWithoutColor {
            return .primary
        }
        if controlActiveState == .inactive {
            return .secondary
        }
        return tone.color
    }

    private var opticalDetailIsVisible: Bool {
        !reduceTransparency
            && !increaseContrast
            && !differentiateWithoutColor
    }

    private var outerFill: Color {
        if !opticalDetailIsVisible {
            return presentationColor.opacity(
                reduceTransparency ? 0.12 : 0.08
            )
        }
        return colorScheme == .dark
            ? Color.white.opacity(0.026)
            : Color.black.opacity(0.022)
    }

    private var lensFill: AnyShapeStyle {
        if !opticalDetailIsVisible {
            return AnyShapeStyle(presentationColor.opacity(0.10))
        }
        return AnyShapeStyle(
            RadialGradient(
                stops: [
                    .init(
                        color: Color.white.opacity(
                            colorScheme == .dark ? 0.14 : 0.30
                        ),
                        location: 0
                    ),
                    .init(
                        color: presentationColor.opacity(
                            colorScheme == .dark ? 0.12 : 0.08
                        ),
                        location: 0.42
                    ),
                    .init(
                        color: Color.black.opacity(
                            colorScheme == .dark ? 0.10 : 0.02
                        ),
                        location: 1
                    ),
                ],
                center: UnitPoint(x: 0.32, y: 0.24),
                startRadius: 0,
                endRadius: 58
            )
        )
    }
}

/// A static, bounded refraction of the Core's localized decorative backdrop.
///
/// The production bridge exposes categorical readiness facets, not an optical
/// scene, so this reconstructs the small panel region beneath the lens from a
/// matching appearance-aware base, restrained field contours, energy marks,
/// and exactly one connector per facet. The original straight connectors stop
/// at the lens boundary; a shape-aware GPU warp bends and disperses this single
/// local layer inside the circle without sampling or filtering semantic
/// content.
private struct AuralignCoreConnectorRefraction: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.controlActiveState) private var controlActiveState

    let colors: [Color]
    let directions: [CGVector]

    var body: some View {
        sourcePlane
            .layerEffect(
                ShaderLibrary.auralignCoreConnectorRefraction(
                    .boundingRect,
                    .float(
                        AuralignOpticalRenderingPolicy.resolvedIntensity(
                            0.92,
                            windowIsActive: controlActiveState != .inactive
                        )
                    )
                ),
                maxSampleOffset: CGSize(
                    width: AuralignCoreLiquidLensTuning.sampleEnvelope,
                    height: AuralignCoreLiquidLensTuning.sampleEnvelope
                ),
                isEnabled: true
            )
            .frame(
                width: AuralignCoreLiquidLensTuning.visibleDiameter,
                height: AuralignCoreLiquidLensTuning.visibleDiameter
            )
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    private var sourcePlane: some View {
        ZStack {
            Rectangle()
                .fill(localizedPanelTone)

            Circle()
                .fill(
                    RadialGradient(
                        stops: [
                            .init(
                                color: Color.white.opacity(
                                    colorScheme == .dark ? 0.055 : 0.18
                                ),
                                location: 0
                            ),
                            .init(
                                color: AuralignSpectrumStop.source.color.opacity(
                                    colorScheme == .dark ? 0.08 : 0.045
                                ),
                                location: 0.50
                            ),
                            .init(color: Color.clear, location: 1),
                        ],
                        center: UnitPoint(x: 0.34, y: 0.27),
                        startRadius: 0,
                        endRadius: 58
                    )
                )

            Canvas { context, size in
                drawFieldContours(context: &context, in: size)
                drawEnergyMarks(context: &context, in: size)
                drawConnectors(context: &context, in: size)
            }
        }
        .frame(
            width: AuralignCoreLiquidLensTuning.sourceDiameter,
            height: AuralignCoreLiquidLensTuning.sourceDiameter
        )
    }

    private var localizedPanelTone: Color {
        if colorScheme == .dark {
            return Color(red: 0.080, green: 0.066, blue: 0.112)
        }
        return Color(red: 0.995, green: 0.980, blue: 1)
    }

    private func drawFieldContours(
        context: inout GraphicsContext,
        in size: CGSize
    ) {
        var upperContour = Path()
        upperContour.move(to: CGPoint(x: -4, y: size.height * 0.32))
        upperContour.addCurve(
            to: CGPoint(x: size.width + 4, y: size.height * 0.54),
            control1: CGPoint(x: size.width * 0.27, y: size.height * 0.16),
            control2: CGPoint(x: size.width * 0.67, y: size.height * 0.70)
        )
        context.stroke(
            upperContour,
            with: .color(
                AuralignSpectrumStop.source.color.opacity(
                    colorScheme == .dark ? 0.20 : 0.13
                )
            ),
            style: StrokeStyle(lineWidth: 0.70, lineCap: .round)
        )

        var lowerContour = Path()
        lowerContour.move(to: CGPoint(x: -4, y: size.height * 0.72))
        lowerContour.addCurve(
            to: CGPoint(x: size.width + 4, y: size.height * 0.40),
            control1: CGPoint(x: size.width * 0.30, y: size.height * 0.84),
            control2: CGPoint(x: size.width * 0.73, y: size.height * 0.20)
        )
        context.stroke(
            lowerContour,
            with: .color(
                AuralignSpectrumStop.delivery.color.opacity(
                    colorScheme == .dark ? 0.18 : 0.11
                )
            ),
            style: StrokeStyle(lineWidth: 0.65, lineCap: .round)
        )
    }

    private func drawEnergyMarks(
        context: inout GraphicsContext,
        in size: CGSize
    ) {
        let marks: [(CGPoint, CGFloat, Color)] = [
            (
                CGPoint(x: size.width * 0.22, y: size.height * 0.24),
                5,
                AuralignSpectrumStop.source.color
            ),
            (
                CGPoint(x: size.width * 0.72, y: size.height * 0.20),
                7,
                AuralignSpectrumStop.recognition.color
            ),
            (
                CGPoint(x: size.width * 0.30, y: size.height * 0.78),
                6,
                AuralignSpectrumStop.alignment.color
            ),
            (
                CGPoint(x: size.width * 0.78, y: size.height * 0.70),
                4,
                AuralignSpectrumStop.delivery.color
            ),
        ]
        for (point, length, color) in marks {
            var mark = Path()
            mark.move(to: CGPoint(x: point.x, y: point.y - length / 2))
            mark.addLine(to: CGPoint(x: point.x, y: point.y + length / 2))
            context.stroke(
                mark,
                with: .color(
                    color.opacity(colorScheme == .dark ? 0.42 : 0.27)
                ),
                style: StrokeStyle(
                    lineWidth: 0.85,
                    lineCap: .round
                )
            )
        }
    }

    private func drawConnectors(
        context: inout GraphicsContext,
        in size: CGSize
    ) {
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        let count = min(colors.count, directions.count, 4)
        let extent = max(size.width, size.height)

        for index in 0..<count {
            let direction = directions[index]
            var path = Path()
            path.move(to: center)
            path.addLine(
                to: CGPoint(
                    x: center.x + direction.dx * extent,
                    y: center.y + direction.dy * extent
                )
            )
            context.stroke(
                path,
                with: .color(colors[index].opacity(0.82)),
                style: StrokeStyle(
                    lineWidth: 1.10,
                    lineCap: .round,
                    dash: [3, 3.5]
                )
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
