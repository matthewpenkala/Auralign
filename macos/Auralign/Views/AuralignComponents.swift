// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

// MARK: - Workspace hero

/// A destination's visible opening statement. The toolbar title remains a
/// compact window breadcrumb; this hero owns the workspace's hierarchy and
/// can carry the selected source, active phase, or result beside it.
struct AuralignWorkspaceHeader<Context: View, Accessory: View>: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency

    let symbol: String
    let accessibilityHeading: String
    let context: Context
    let accessory: Accessory

    init(
        symbol: String,
        accessibilityHeading: String,
        @ViewBuilder context: @escaping () -> Context,
        @ViewBuilder accessory: @escaping () -> Accessory
    ) {
        self.symbol = symbol
        self.accessibilityHeading = accessibilityHeading
        self.context = context()
        self.accessory = accessory()
    }

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: AuralignVisualSystem.spacingXL) {
                headerContext
                Spacer(minLength: AuralignVisualSystem.spacingL)
                accessory
            }

            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM) {
                headerContext
                accessory
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, AuralignVisualSystem.spacingXL)
        .padding(.vertical, AuralignVisualSystem.spacingL)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            ZStack {
                RoundedRectangle(
                    cornerRadius: AuralignRadius.hero,
                    style: .continuous
                )
                .fill(
                    LinearGradient(
                        colors: heroColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )

                AuralignHeroMetalOptics(intensity: 0.86)

                AuralignOpticalSurface(
                    cornerRadius: AuralignRadius.hero,
                    accent: .alignment,
                    intensity: 0.90
                )
            }
        }
        .overlay {
            RoundedRectangle(cornerRadius: AuralignRadius.hero)
                .strokeBorder(
                    increaseContrast
                        ? Color.primary
                        : AuralignSurface.hairline.opacity(0.6),
                    lineWidth: increaseContrast ? 1.25 : 0.5
                )
        }
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.hero))
        .shadow(
            color: Color.black.opacity(
                reduceTransparency
                    ? 0
                    : (colorScheme == .dark ? 0.14 : 0.08)
            ),
            radius: reduceTransparency ? 0 : 9,
            y: reduceTransparency ? 0 : 3
        )
        .accessibilityElement(children: .contain)
    }

    private var heroColors: [Color] {
        if colorScheme == .dark {
            return [
                Color(red: 0.112, green: 0.074, blue: 0.165)
                    .opacity(reduceTransparency ? 1 : 0.96),
                Color(red: 0.074, green: 0.068, blue: 0.112)
                    .opacity(reduceTransparency ? 1 : 0.91)
            ]
        }
        return [
            Color(red: 0.985, green: 0.960, blue: 1)
                .opacity(reduceTransparency ? 1 : 0.96),
            Color(red: 0.955, green: 0.970, blue: 1)
                .opacity(reduceTransparency ? 1 : 0.92)
        ]
    }

    private var headerContext: some View {
        VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(AuralignSpectrumStop.source.color)
                    .accessibilityHidden(true)
                Text(accessibilityHeading.uppercased())
                    .font(AuralignType.eyebrow)
                    .tracking(1.45)
                    .foregroundStyle(.secondary)
                    .accessibilityAddTraits(.isHeader)
            }
            context
        }
    }
}

extension AuralignWorkspaceHeader where Accessory == EmptyView {
    init(
        symbol: String,
        accessibilityHeading: String,
        @ViewBuilder context: @escaping () -> Context
    ) {
        self.init(
            symbol: symbol,
            accessibilityHeading: accessibilityHeading,
            context: context
        ) { EmptyView() }
    }
}

// MARK: - Causality banner

/// States the *effective* governing fact in one sentence, the way Little
/// Snitch's rule inspector states which rule actually wins before listing the
/// properties that produce it. This is a stronger, full-width fill than
/// `AuralignStatusBanner` — reserved for the one governing fact of a surface
/// (why a job is blocked, why a parameter's effective value is what it is),
/// not for every incidental notice.
struct AuralignCausalityBanner: View {
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.controlActiveState) private var controlActiveState

    let headline: String
    var detail: String?
    let tone: AuralignStatusTone
    var symbol: String?
    var linkTitle: String?
    var linkIdentifier: String?
    var link: (() -> Void)?

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
                copy
                Spacer(minLength: AuralignVisualSystem.spacingM)
                linkButton
            }
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingS) {
                copy
                if linkTitle != nil, link != nil {
                    linkButton.frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
        }
        .padding(AuralignVisualSystem.spacingM)
        .background {
            ZStack {
                RoundedRectangle(
                    cornerRadius: AuralignRadius.group,
                    style: .continuous
                )
                .fill(fill)

                AuralignOpticalSurface(
                    cornerRadius: AuralignRadius.group,
                    intensity: 0.34,
                    accentColor: presentationColor
                )
            }
        }
        .overlay {
            if increaseContrast || differentiateWithoutColor {
                RoundedRectangle(cornerRadius: AuralignRadius.group)
                    .stroke(Color.primary.opacity(0.6), lineWidth: 1)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.group))
        .accessibilityElement(children: .contain)
    }

    private var presentationColor: Color {
        controlActiveState == .inactive ? .secondary : tone.color
    }

    private var fill: Color {
        reduceTransparency
            ? Color(nsColor: .controlBackgroundColor)
            : presentationColor.opacity(increaseContrast ? 0.24 : 0.16)
    }

    private var copy: some View {
        HStack(alignment: .top, spacing: AuralignVisualSystem.spacingM) {
            Image(systemName: symbol ?? tone.defaultSymbol)
                .font(.title3)
                .foregroundStyle(presentationColor)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingXS) {
                Text(headline)
                    .font(AuralignType.bodyEmphasis)
                    .fixedSize(horizontal: false, vertical: true)
                if let detail {
                    Text(detail)
                        .font(AuralignType.metadata)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private var linkButton: some View {
        if let linkTitle, let link {
            Button(linkTitle, action: link)
                .controlSize(.small)
                .modifier(OptionalIdentifier(identifier: linkIdentifier))
        }
    }
}

private struct OptionalIdentifier: ViewModifier {
    let identifier: String?
    func body(content: Content) -> some View {
        if let identifier {
            content.accessibilityIdentifier(identifier)
        } else {
            content
        }
    }
}

// MARK: - Media row

/// A row backed by a small derived visual rather than bare text — the
/// mechanism Permute and Fission use for job/artifact rows.
///
/// `swatch` must be built only from real categorical facts already known
/// about the row (a format glyph, a phase's spectrum color, a file
/// extension) — never a fabricated waveform or thumbnail, since no
/// per-file sample data exists in the Swift-visible contract. Use
/// `auralignMediaRowSwatch(symbol:tint:)` for the common case.
struct AuralignMediaRow<Trailing: View>: View {
    @Environment(\.auralignReduceTransparency) private var reduceTransparency

    let title: String
    var metadata: String?
    let swatch: AnyView
    var containsInteractiveTrailing = false
    var allowsMultiline = false
    var contentIdentifier: String?
    var trailing: () -> Trailing
    var action: (() -> Void)?

    // Written explicitly rather than relying on memberwise synthesis: a
    // synthesized init does not reliably propagate `@ViewBuilder` to a
    // generic closure parameter, which produced "generic parameter
    // 'Trailing' could not be inferred" at every multi-statement call site.
    init(
        title: String,
        metadata: String? = nil,
        swatch: AnyView,
        containsInteractiveTrailing: Bool = false,
        allowsMultiline: Bool = false,
        contentIdentifier: String? = nil,
        @ViewBuilder trailing: @escaping () -> Trailing,
        action: (() -> Void)? = nil
    ) {
        self.title = title
        self.metadata = metadata
        self.swatch = swatch
        self.containsInteractiveTrailing = containsInteractiveTrailing
        self.allowsMultiline = allowsMultiline
        self.contentIdentifier = contentIdentifier
        self.trailing = trailing
        self.action = action
    }

    var body: some View {
        let row = HStack(spacing: AuralignVisualSystem.spacingM) {
            swatch
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(AuralignType.rowTitle)
                    .lineLimit(allowsMultiline ? nil : 1)
                    .truncationMode(.middle)
                if let metadata {
                    Text(metadata)
                        .font(AuralignType.metadata)
                        .foregroundStyle(.secondary)
                        .lineLimit(allowsMultiline ? nil : 1)
                        .fixedSize(
                            horizontal: false,
                            vertical: allowsMultiline
                        )
                }
            }
            .accessibilityElement(children: .combine)
            .modifier(OptionalIdentifier(identifier: contentIdentifier))
            Spacer(minLength: AuralignVisualSystem.spacingS)
            trailing()
        }
        .padding(.vertical, AuralignVisualSystem.spacingS)
        .padding(.horizontal, AuralignVisualSystem.spacingM)
        .background(AuralignSurface.raised)
        .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.group))

        if let action {
            Button(action: action) { row }
                .buttonStyle(.plain)
                .accessibilityElement(children: .combine)
        } else if containsInteractiveTrailing {
            row.accessibilityElement(children: .contain)
        } else {
            row.accessibilityElement(children: .combine)
        }
    }
}

extension AuralignMediaRow where Trailing == EmptyView {
    init(
        title: String,
        metadata: String? = nil,
        swatch: AnyView,
        allowsMultiline: Bool = false,
        contentIdentifier: String? = nil,
        action: (() -> Void)? = nil
    ) {
        self.init(
            title: title,
            metadata: metadata,
            swatch: swatch,
            allowsMultiline: allowsMultiline,
            contentIdentifier: contentIdentifier,
            trailing: { EmptyView() },
            action: action
        )
    }
}

// A free function, not a static member of `AuralignMediaRow<Trailing>`: a
// static method on a generic type still requires Swift to resolve the
// enclosing type's generic parameter to reference it, even when the method
// itself never touches that parameter. That produced "generic parameter
// 'Trailing' could not be inferred" at every call site that built the
// argument inline (`AuralignMediaRow(swatch: AuralignMediaRow.swatch(...),
// trailing: { ... })`), since Swift tried to solve one shared `Trailing`
// across both the static-method reference and the enclosing initializer.
// A free function has no generic parameter to resolve, so it is
// unconditionally callable from any context.
func auralignMediaRowSwatch(symbol: String, tint: Color) -> AnyView {
    AnyView(
        RoundedRectangle(cornerRadius: AuralignRadius.control)
            .fill(tint.opacity(0.16))
            .frame(width: 36, height: 36)
            .overlay {
                Image(systemName: symbol)
                    .font(.callout)
                    .foregroundStyle(tint)
            }
            .accessibilityHidden(true)
    )
}

// MARK: - Modified-from-default indicator

/// Marks a field whose effective value differs from its schema default. This
/// is the one fact operators actually want when scanning a dense parameter
/// list, and the baseline surfaces it nowhere.
struct AuralignModifiedBadge: View {
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor

    var body: some View {
        Label("Modified", systemImage: "circle.fill")
            .labelStyle(.iconOnly)
            .font(.system(size: 6))
            .foregroundStyle(
                increaseContrast || differentiateWithoutColor
                    ? Color.primary
                    : Color.accentColor
            )
            .accessibilityLabel("Modified from default")
            .accessibilityHidden(false)
    }
}
