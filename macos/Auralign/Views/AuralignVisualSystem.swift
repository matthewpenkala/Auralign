// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import SwiftUI

private struct AuralignReduceTransparencyKey: EnvironmentKey {
    static let defaultValue = false
}

private struct AuralignIncreaseContrastKey: EnvironmentKey {
    static let defaultValue = false
}

private struct AuralignReduceMotionKey: EnvironmentKey {
    static let defaultValue = false
}

private struct AuralignDifferentiateWithoutColorKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var auralignReduceTransparency: Bool {
        get { self[AuralignReduceTransparencyKey.self] }
        set { self[AuralignReduceTransparencyKey.self] = newValue }
    }

    var auralignIncreaseContrast: Bool {
        get { self[AuralignIncreaseContrastKey.self] }
        set { self[AuralignIncreaseContrastKey.self] = newValue }
    }

    var auralignReduceMotion: Bool {
        get { self[AuralignReduceMotionKey.self] }
        set { self[AuralignReduceMotionKey.self] = newValue }
    }

    var auralignDifferentiateWithoutColor: Bool {
        get { self[AuralignDifferentiateWithoutColorKey.self] }
        set { self[AuralignDifferentiateWithoutColorKey.self] = newValue }
    }
}

/// Shared visual primitives for the Auralign workbench.
///
/// The system deliberately builds on semantic macOS colors, materials, and
/// control metrics. It gives Auralign a consistent hierarchy without creating
/// a custom window chrome or imitating another application's trade dress.
enum AuralignVisualSystem {
    static let spacingXS: CGFloat = 4
    static let spacingS: CGFloat = 8
    static let spacingM: CGFloat = 12
    static let spacingL: CGFloat = 16
    static let spacingXL: CGFloat = 24

    static let standardRowHeight: CGFloat = 40
    static let compactRowHeight: CGFloat = 32
    static let readableContentWidth: CGFloat = 780
    static let inspectorWidth: CGFloat = 300
    static let cornerRadius: CGFloat = 8
}

enum AuralignStatusTone: Equatable {
    case neutral
    case active
    case success
    case warning
    case failure

    var color: Color {
        switch self {
        case .neutral:
            return .secondary
        case .active:
            return .accentColor
        case .success:
            return .green
        case .warning:
            return .orange
        case .failure:
            return .red
        }
    }

    var defaultSymbol: String {
        switch self {
        case .neutral:
            return "circle"
        case .active:
            return "circle.inset.filled"
        case .success:
            return "checkmark.circle.fill"
        case .warning:
            return "exclamationmark.triangle.fill"
        case .failure:
            return "xmark.octagon.fill"
        }
    }
}

/// A compact, borderless status treatment. Status is communicated by symbol
/// and text; color is supplemental. Passive status must never resemble an
/// input control or search field.
struct AuralignStatusBadge: View {
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.controlActiveState) private var controlActiveState

    let text: String
    let tone: AuralignStatusTone
    var symbol: String?

    var body: some View {
        Label(text, systemImage: symbol ?? tone.defaultSymbol)
            .font(AuralignType.status)
            .foregroundStyle(
                increaseContrast ? Color.primary : presentationColor
            )
            .lineLimit(nil)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .combine)
            .accessibilityLabel(text)
    }

    private var presentationColor: Color {
        controlActiveState == .inactive ? .secondary : tone.color
    }
}

/// A recovery-first banner. The action, when supplied, is intentionally
/// trailing and secondary to the explanatory text.
struct AuralignStatusBanner: View {
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.controlActiveState) private var controlActiveState

    let title: String
    let message: String
    let tone: AuralignStatusTone
    var symbol: String?
    var actionTitle: String?
    var actionIdentifier: String?
    var action: (() -> Void)?

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
                bannerCopy
                Spacer(minLength: AuralignVisualSystem.spacingM)
                actionButton
            }

            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                bannerCopy
                if actionTitle != nil, action != nil {
                    actionButton
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
        }
        .padding(AuralignVisualSystem.spacingM)
        .background {
            ZStack {
                RoundedRectangle(
                    cornerRadius: AuralignVisualSystem.cornerRadius,
                    style: .continuous
                )
                .fill(statusBackground)

                AuralignOpticalSurface(
                    cornerRadius: AuralignVisualSystem.cornerRadius,
                    intensity: 0.38,
                    accentColor: presentationColor
                )
            }
        }
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(presentationColor)
                .frame(width: 3)
        }
        .overlay {
            if increaseContrast || differentiateWithoutColor {
                RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius)
                    .stroke(
                        increaseContrast
                            ? Color.primary : presentationColor,
                        lineWidth: 1
                    )
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: AuralignVisualSystem.cornerRadius))
        .accessibilityElement(children: .contain)
    }

    private var presentationColor: Color {
        controlActiveState == .inactive ? .secondary : tone.color
    }

    private var statusBackground: Color {
        reduceTransparency
            ? Color(nsColor: .controlBackgroundColor)
            : presentationColor.opacity(increaseContrast ? 0.14 : 0.08)
    }

    private var bannerCopy: some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: symbol ?? tone.defaultSymbol)
                .foregroundStyle(presentationColor)
                .font(.title3)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(title)
                    .font(AuralignType.bodyEmphasis)
                Text(message)
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var actionButton: some View {
        if let actionTitle, let action {
            if let actionIdentifier {
                Button(actionTitle, action: action)
                    .controlSize(.small)
                    .accessibilityIdentifier(actionIdentifier)
            } else {
                Button(actionTitle, action: action)
                    .controlSize(.small)
            }
        }
    }
}

/// Consistent title area for document-like workbench destinations.
struct AuralignPageHeader<Accessory: View>: View {
    let title: String
    let subtitle: String
    let symbol: String
    let accessory: Accessory

    init(
        title: String,
        subtitle: String,
        symbol: String,
        @ViewBuilder accessory: @escaping () -> Accessory
    ) {
        self.title = title
        self.subtitle = subtitle
        self.symbol = symbol
        self.accessory = accessory()
    }

    var body: some View {
        AuralignStudioPanel(accent: .delivery) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
                    headerCopy
                    Spacer(minLength: AuralignVisualSystem.spacingL)
                    accessory
                }

                VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                    headerCopy
                    accessory
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private var headerCopy: some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(.tint)
                .frame(width: 28)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(title)
                    .font(AuralignType.display)
                    .accessibilityAddTraits(.isHeader)
                Text(subtitle)
                    .font(AuralignType.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

extension AuralignPageHeader where Accessory == EmptyView {
    init(title: String, subtitle: String, symbol: String) {
        self.init(title: title, subtitle: subtitle, symbol: symbol) {
            EmptyView()
        }
    }
}

/// A studio plane groups one coherent unit of work. The offset index and
/// spectral rule create rhythm without changing the semantic reading order.
struct AuralignSection<Content: View>: View {
    let title: String
    var subtitle: String?
    var headerIdentifier: String? = nil
    let content: Content

    init(
        title: String,
        subtitle: String? = nil,
        headerIdentifier: String? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.subtitle = subtitle
        self.headerIdentifier = headerIdentifier
        self.content = content()
    }

    var body: some View {
        AuralignStudioPanel(accent: accent) {
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingL) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(sectionIndex)
                        .font(AuralignType.sectionIndex)
                        .foregroundStyle(accent.color)
                        .accessibilityHidden(true)
                    VStack(
                        alignment: .leading,
                        spacing: AuralignVisualSystem.spacingXS
                    ) {
                        Text(title)
                            .font(AuralignType.workspaceTitle)
                            .accessibilityAddTraits(.isHeader)
                            .modifier(
                                AuralignSectionIdentifier(
                                    identifier: headerIdentifier
                                )
                            )
                        if let subtitle {
                            Text(subtitle)
                                .font(AuralignType.metadata)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                content
            }
        }
    }

    private var sectionIndex: String {
        String(format: "%02d", titleSeed % 99 + 1)
    }

    private var accent: AuralignSpectrumStop {
        AuralignSpectrumStop.allCases[
            titleSeed % AuralignSpectrumStop.allCases.count
        ]
    }

    private var titleSeed: Int {
        abs(title.unicodeScalars.reduce(0) { $0 + Int($1.value) })
    }
}

private struct AuralignSectionIdentifier: ViewModifier {
    let identifier: String?

    func body(content: Content) -> some View {
        if let identifier {
            content.accessibilityIdentifier(identifier)
        } else {
            content
        }
    }
}
