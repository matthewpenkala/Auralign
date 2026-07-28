// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import SwiftUI

// MARK: - Dynamic color construction

/// Builds an appearance-reactive color without hard-coding a single literal
/// for both Light and Dark. Every Auralign identity color is defined this way
/// so Dark Mode can carry reduced chroma, as the platform grammar requires.
func auralignDynamicColor(light: NSColor, dark: NSColor) -> Color {
    Color(
        nsColor: NSColor(name: nil) { appearance in
            let match = appearance.bestMatch(from: [.aqua, .darkAqua])
            return match == .darkAqua ? dark : light
        }
    )
}

private func srgb(
    _ red: CGFloat,
    _ green: CGFloat,
    _ blue: CGFloat
) -> NSColor {
    NSColor(srgbRed: red, green: green, blue: blue, alpha: 1)
}

// MARK: - Identity spectrum

/// The Auralign identity spectrum.
///
/// The four stops are ordered as the product's own story — a source becomes
/// recognized speech, is aligned, and is delivered as artifacts. Exact
/// evidence views use that position literally; ambient studio surfaces may
/// abstract the spectrum into a restrained atmosphere without implying
/// measurements the engine did not report.
enum AuralignSpectrumStop: Int, CaseIterable, Equatable {
    case source
    case recognition
    case alignment
    case delivery

    /// Dark values are deliberately lower-chroma than Light values so
    /// highlights do not bloom on a graphite canvas.
    var color: Color {
        switch self {
        case .source:
            return auralignDynamicColor(
                light: srgb(0.02, 0.60, 0.75),
                dark: srgb(0.29, 0.80, 0.92)
            )
        case .recognition:
            return auralignDynamicColor(
                light: srgb(0.11, 0.40, 0.86),
                dark: srgb(0.39, 0.62, 0.98)
            )
        case .alignment:
            return auralignDynamicColor(
                light: srgb(0.42, 0.25, 0.85),
                dark: srgb(0.63, 0.48, 0.97)
            )
        case .delivery:
            return auralignDynamicColor(
                light: srgb(0.70, 0.24, 0.78),
                dark: srgb(0.88, 0.55, 0.95)
            )
        }
    }

    /// Position of this stop along the identity ramp, 0...1.
    var unitPosition: Double {
        let count = Double(AuralignSpectrumStop.allCases.count - 1)
        guard count > 0 else { return 0 }
        return Double(rawValue) / count
    }
}

/// Gradient construction for the identity spectrum.
///
/// Every entry point takes the resolved accessibility state so a gradient can
/// collapse to a single legible tint under Increase Contrast or
/// Differentiate Without Color rather than silently losing meaning.
enum AuralignGradient {
    /// The full source-to-delivery ramp. Use only where the whole journey is
    /// genuinely being represented.
    static func identity(
        increaseContrast: Bool = false,
        differentiateWithoutColor: Bool = false
    ) -> LinearGradient {
        if increaseContrast || differentiateWithoutColor {
            return LinearGradient(
                colors: [Color.accentColor, Color.accentColor],
                startPoint: .leading,
                endPoint: .trailing
            )
        }
        return LinearGradient(
            stops: AuralignSpectrumStop.allCases.map { stop in
                Gradient.Stop(
                    color: stop.color,
                    location: stop.unitPosition
                )
            },
            startPoint: .leading,
            endPoint: .trailing
        )
    }

    /// A ramp truncated at `fraction` of the journey. Callers must derive
    /// `fraction` from real completed-stage counts — never from a timer.
    static func progressed(
        through fraction: Double,
        increaseContrast: Bool = false,
        differentiateWithoutColor: Bool = false
    ) -> LinearGradient {
        let clamped = min(max(fraction.isFinite ? fraction : 0, 0), 1)
        if increaseContrast || differentiateWithoutColor {
            return LinearGradient(
                colors: [Color.accentColor, Color.accentColor],
                startPoint: .leading,
                endPoint: .trailing
            )
        }
        let stops = AuralignSpectrumStop.allCases
            .filter { $0.unitPosition <= max(clamped, 0.0001) }
        let colors = stops.isEmpty
            ? [AuralignSpectrumStop.source.color]
            : stops.map(\.color)
        return LinearGradient(
            colors: colors,
            startPoint: .leading,
            endPoint: .trailing
        )
    }

    /// A localized emphasis wash for the one selected or active piece of
    /// evidence. Deliberately low-alpha: bloom marks a location, it does not
    /// illuminate the window.
    static func bloom(
        _ stop: AuralignSpectrumStop,
        increaseContrast: Bool = false,
        reduceTransparency: Bool = false
    ) -> RadialGradient {
        let base = increaseContrast ? Color.accentColor : stop.color
        let peak = reduceTransparency ? 0.0 : (increaseContrast ? 0.30 : 0.22)
        return RadialGradient(
            colors: [base.opacity(peak), base.opacity(0)],
            center: .center,
            startRadius: 0,
            endRadius: 120
        )
    }
}

// MARK: - Surfaces and elevation

/// Layered surfaces for the workbench.
///
/// Three elevation steps only, as recommended: the canvas the work sits on,
/// a raised evidence plane, and an inset well for content that is read rather
/// than acted upon (transcripts, logs, diagnostics).
enum AuralignSurface {
    /// The window's own content plane.
    static var canvas: Color { Color(nsColor: .windowBackgroundColor) }

    /// Evidence grouped above the canvas.
    static var raised: Color { Color(nsColor: .controlBackgroundColor) }

    /// Long-form readable content. Always opaque — sensitive transcript, path,
    /// and log text must never sit under a translucent material.
    static var inset: Color { Color(nsColor: .textBackgroundColor) }

    /// Hairline separators and evidence-plane borders.
    static var hairline: Color { Color(nsColor: .separatorColor) }

    /// The graphite tint layered beneath the alignment canvas. Returns a plain
    /// opaque surface under Reduce Transparency.
    static func canvasField(reduceTransparency: Bool) -> Color {
        reduceTransparency
            ? Color(nsColor: .controlBackgroundColor)
            : auralignDynamicColor(
                light: srgb(0.95, 0.96, 0.98),
                dark: srgb(0.09, 0.10, 0.12)
            )
    }
}

/// Shallow, purposeful depth. Auralign separates planes with one soft shadow
/// step; it does not simulate physical cards.
enum AuralignElevation {
    case flat
    case raised
    case floating

    var shadowRadius: CGFloat {
        switch self {
        case .flat: return 0
        case .raised: return 6
        case .floating: return 14
        }
    }

    var shadowOpacity: Double {
        switch self {
        case .flat: return 0
        case .raised: return 0.10
        case .floating: return 0.18
        }
    }

    var shadowOffsetY: CGFloat {
        switch self {
        case .flat: return 0
        case .raised: return 2
        case .floating: return 6
        }
    }
}

// MARK: - Geometry

/// One concentric radius family, as macOS 26 geometry requires. Inner curves
/// are derived from their parent by subtracting the inset, so nested corners
/// stay concentric instead of pinching.
enum AuralignRadius {
    static let control: CGFloat = 6
    static let group: CGFloat = 10
    static let canvas: CGFloat = 14
    static let hero: CGFloat = 20

    /// Concentric inner radius for a child inset by `inset` points.
    static func inner(of parent: CGFloat, inset: CGFloat) -> CGFloat {
        max(parent - inset, 2)
    }
}

/// Shared layout breakpoints. Previously each surface invented its own
/// threshold (840 / 860 / 900), which made reflow behavior inconsistent.
enum AuralignBreakpoint {
    /// Below this width the contextual inspector collapses first.
    static let inspector: CGFloat = 880

    /// Below this width two-column workbenches stack.
    static let workbench: CGFloat = 860

    /// Maximum measure for a full workbench composition.
    static let workbenchMaxWidth: CGFloat = 1_180

    /// Maximum measure for single-column reading content.
    static let readingMaxWidth: CGFloat = 780

    /// The expert browser's three columns total 902 points
    /// including dividers. Leave breathing room before adopting that layout.
    static let parameterLibrary: CGFloat = 940
}

// MARK: - Typography

/// Auralign's type system, built from two system font designs.
///
/// The families are system-provided font designs, not bundled font files:
///
/// - Default SF gives display titles, navigation, compact headings, and short state language
///   a precise, non-rounded macOS voice without changing native metrics.
/// - Default remains the calm interface and transcript face; measure and
///   leading distinguish those roles.
/// - Monospaced distinguishes exact evidence and keeps changing values stable.
///
/// Every role starts from a semantic text style so enlarged-text settings,
/// script-specific fallback, and localization continue to work. Keep exact
/// point sizes limited to SF Symbols and other non-text geometry.
enum AuralignType {
    // MARK: Product voice

    /// The single dominant state or object name on a workspace.
    ///
    /// Large utility/setup titles use non-rounded system SF, consistent with
    /// every human-readable product title while retaining semantic sizing.
    static let display =
        Font.system(.largeTitle, design: .default).weight(.semibold)

    /// Major state within a workspace, empty state, setup, or recovery screen.
    static let pageTitle = Font.system(.title2, design: .default).weight(.semibold)

    /// Coherent studio-plane or inspector title.
    static let workspaceTitle = Font.system(.title3, design: .default).weight(.semibold)

    /// Compact section or disclosure heading.
    static let sectionTitle = Font.system(.headline, design: .default).weight(.semibold)

    /// Short all-caps studio label. Tracking is applied only at the call site
    /// when the source string is intentionally authored for that treatment.
    static let eyebrow = Font.system(.caption, design: .default).weight(.bold)

    /// Auralign wordmark rendered as live, accessible text. The title-three
    /// scale makes its optical width meet the two-line instrument descriptor
    /// at sidebar size without turning the lockup into a second page heading.
    static let brandWordmark =
        Font.system(.title3, design: .default).weight(.bold)

    /// Short active-state and badge language.
    static let status = Font.system(.caption, design: .default).weight(.medium)
    static let badge = Font.system(.caption2, design: .default).weight(.semibold)

    /// Native-looking primary and secondary action labels.
    static let button = Font.system(.callout, design: .default).weight(.semibold)

    static func navigation(selected: Bool) -> Font {
        Font.system(.callout, design: .default)
            .weight(selected ? .semibold : .regular)
    }

    static func phaseLabel(selected: Bool) -> Font {
        Font.system(.caption, design: .default)
            .weight(selected ? .semibold : .regular)
    }

    // MARK: Interface voice — system default

    static let body = Font.system(.callout, design: .default)
    static let bodyEmphasis = Font.callout.weight(.semibold)
    static let control = Font.callout
    static let controlEmphasis = Font.callout.weight(.semibold)
    static let rowTitle = Font.callout.weight(.medium)
    static let evidence = Font.callout
    static let evidenceEmphasis = Font.callout.weight(.medium)
    static let metadata = Font.caption
    static let metadataStrong = Font.caption.weight(.medium)
    static let microcopy = Font.caption2

    // MARK: Instrument voice — system monospaced

    /// Secondary brand descriptor: it signals a local technical instrument
    /// without turning ordinary navigation or prose into code.
    static let brandDescriptor =
        Font.system(.caption2, design: .monospaced).weight(.medium)

    /// Decorative section indices and compact technical counters.
    static let sectionIndex =
        Font.system(.caption2, design: .monospaced).weight(.bold)
    static let navigationCount =
        Font.system(.caption2, design: .monospaced).weight(.bold)

    /// Canonical identifiers and diagnostic values at body, caption, and
    /// microcopy scales.
    static let technicalBody = Font.system(.callout, design: .monospaced)
    static let technical = Font.system(.caption, design: .monospaced)
    static let technicalSmall = Font.system(.caption2, design: .monospaced)

    /// Backward-compatible semantic name for exact keys, revisions, hashes,
    /// and paths. New call sites should prefer the scale-specific names above.
    static let mono = technical

    /// Durations, timestamps, counts, and other instrument readouts.
    static let numeric = Font.system(.callout, design: .monospaced)
    static let numericSmall = Font.system(.caption, design: .monospaced)

    // MARK: Reading voice — system default

    /// Human-language transcript content only. Its semantic body scale,
    /// constrained measure, and generous leading create the reading voice;
    /// technical logs and diagnostics remain monospaced.
    static let transcript = Font.system(.body, design: .default)
}

// MARK: - Motion

/// Motion durations, in seconds.
///
/// Motion exists to explain a change of state or scope. Nothing here loops,
/// and no helper produces an animation that can run while the engine is idle.
enum AuralignMotion {
    static let selection: Double = 0.16
    static let disclosure: Double = 0.18
    static let stageTransition: Double = 0.24
    static let settle: Double = 0.30

    /// Returns a spring-free, short animation, or `nil` under Reduce Motion so
    /// callers apply the state change instantly while preserving meaning.
    static func animation(
        _ duration: Double,
        reduceMotion: Bool
    ) -> Animation? {
        reduceMotion ? nil : .easeInOut(duration: duration)
    }
}

// MARK: - Semantic status

/// The stable semantic roles. Selection intentionally uses the system accent
/// rather than reusing success green or warning amber.
extension AuralignStatusTone {
    /// A low-alpha fill for the tone, or an opaque control surface when
    /// transparency is reduced.
    func fill(
        increaseContrast: Bool,
        reduceTransparency: Bool
    ) -> Color {
        if reduceTransparency {
            return AuralignSurface.raised
        }
        return color.opacity(increaseContrast ? 0.16 : 0.09)
    }
}
