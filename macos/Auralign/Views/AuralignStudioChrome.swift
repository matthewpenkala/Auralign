// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// A restrained optical finish for Auralign's large structural surfaces.
///
/// The treatment is intentionally decorative and non-interactive: a clipped
/// specular wash, a sub-point spectral rim, and a lower occlusion edge suggest
/// a thin refractive layer without turning every plane into generic frosted
/// glass. Accessibility appearances remove the chromatic/refraction cues
/// rather than asking color or translucency to carry structure.
struct AuralignOpticalSurface: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.controlActiveState) private var controlActiveState

    let cornerRadius: CGFloat
    var accent: AuralignSpectrumStop = .alignment
    var intensity: Double = 1
    var accentColor: Color? = nil

    var body: some View {
        ZStack {
            if opticalDetailIsVisible {
                RoundedRectangle(
                    cornerRadius: cornerRadius,
                    style: .continuous
                )
                .fill(specularWash)

                RoundedRectangle(
                    cornerRadius: cornerRadius,
                    style: .continuous
                )
                .strokeBorder(spectralRim, lineWidth: 0.72)

                RoundedRectangle(
                    cornerRadius: AuralignRadius.inner(
                        of: cornerRadius,
                        inset: 0.9
                    ),
                    style: .continuous
                )
                .strokeBorder(
                    Color.white.opacity(
                        (colorScheme == .dark ? 0.10 : 0.42)
                            * resolvedIntensity
                    ),
                    lineWidth: 0.45
                )
                .padding(0.9)
                .mask(topEdgeMask)

                RoundedRectangle(
                    cornerRadius: AuralignRadius.inner(
                        of: cornerRadius,
                        inset: 0.7
                    ),
                    style: .continuous
                )
                .strokeBorder(
                    Color.black.opacity(
                        (colorScheme == .dark ? 0.24 : 0.10)
                            * resolvedIntensity
                    ),
                    lineWidth: 0.7
                )
                .padding(0.7)
                .mask(bottomEdgeMask)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private var opticalDetailIsVisible: Bool {
        !reduceTransparency
            && !increaseContrast
            && !differentiateWithoutColor
    }

    private var resolvedIntensity: Double {
        let lifted = AuralignOpticalIntensity.lifted(intensity)
        return controlActiveState == .inactive ? lifted * 0.42 : lifted
    }

    private var specularWash: LinearGradient {
        LinearGradient(
            stops: [
                .init(
                    color: Color.white.opacity(
                        (colorScheme == .dark ? 0.055 : 0.20)
                            * resolvedIntensity
                    ),
                    location: 0
                ),
                .init(color: Color.clear, location: 0.34),
                .init(color: Color.clear, location: 0.70),
                .init(
                    color: resolvedAccent.opacity(0.026 * resolvedIntensity),
                    location: 1
                ),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private var spectralRim: LinearGradient {
        LinearGradient(
            stops: [
                .init(
                    color: AuralignSpectrumStop.source.color.opacity(
                        0.20 * resolvedIntensity
                    ),
                    location: 0
                ),
                .init(
                    color: Color.white.opacity(0.13 * resolvedIntensity),
                    location: 0.18
                ),
                .init(color: Color.clear, location: 0.52),
                .init(
                    color: resolvedAccent.opacity(0.10 * resolvedIntensity),
                    location: 0.78
                ),
                .init(
                    color: AuralignSpectrumStop.delivery.color.opacity(
                        0.15 * resolvedIntensity
                    ),
                    location: 1
                ),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private var topEdgeMask: LinearGradient {
        LinearGradient(
            stops: [
                .init(color: .black, location: 0),
                .init(color: .black.opacity(0.72), location: 0.18),
                .init(color: .clear, location: 0.48),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    private var bottomEdgeMask: LinearGradient {
        LinearGradient(
            stops: [
                .init(color: .clear, location: 0.52),
                .init(color: .black.opacity(0.66), location: 0.82),
                .init(color: .black, location: 1),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    private var resolvedAccent: Color {
        accentColor ?? accent.color
    }
}

/// A procedural, data-agnostic atmosphere shared by every production
/// workspace. The paths suggest timing, overlap, and convergence without
/// pretending to visualize samples the Swift bridge does not expose.
struct AuralignStudioBackdrop: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency

    var body: some View {
        ZStack {
            base

            if !reduceTransparency {
                RadialGradient(
                    colors: [
                        AuralignSpectrumStop.alignment.color.opacity(
                            colorScheme == .dark ? 0.19 : 0.13
                        ),
                        Color.clear
                    ],
                    center: .topTrailing,
                    startRadius: 0,
                    endRadius: 560
                )
                RadialGradient(
                    colors: [
                        AuralignSpectrumStop.delivery.color.opacity(
                            colorScheme == .dark ? 0.11 : 0.08
                        ),
                        Color.clear
                    ],
                    center: .bottomLeading,
                    startRadius: 0,
                    endRadius: 480
                )
                Canvas { context, size in
                    drawSignalPaths(context: &context, size: size)
                    drawRegistrationMarks(context: &context, size: size)
                }
                .opacity(increaseContrast ? 0.16 : 0.34)
                .accessibilityHidden(true)
            }
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private var base: some View {
        LinearGradient(
            stops: [
                .init(
                    color: colorScheme == .dark
                        ? Color(red: 0.030, green: 0.026, blue: 0.058)
                        : Color(red: 0.958, green: 0.969, blue: 0.988),
                    location: 0
                ),
                .init(
                    color: colorScheme == .dark
                        ? Color(red: 0.079, green: 0.053, blue: 0.126)
                        : Color(red: 0.940, green: 0.938, blue: 0.982),
                    location: 0.58
                ),
                .init(
                    color: colorScheme == .dark
                        ? Color(red: 0.066, green: 0.031, blue: 0.094)
                        : Color(red: 0.982, green: 0.948, blue: 0.982),
                    location: 1
                )
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private func drawSignalPaths(
        context: inout GraphicsContext,
        size: CGSize
    ) {
        let muted = differentiateWithoutColor || increaseContrast
        let colors: [Color] = muted
            ? [.primary.opacity(0.22), .secondary.opacity(0.15)]
            : [
                AuralignSpectrumStop.source.color.opacity(0.24),
                AuralignSpectrumStop.alignment.color.opacity(0.19),
                AuralignSpectrumStop.delivery.color.opacity(0.15)
            ]

        for index in 0..<7 {
            var path = Path()
            let row = CGFloat(index)
            let originY = size.height * (0.08 + row * 0.125)
            let amplitude = size.height * (0.018 + row.truncatingRemainder(
                dividingBy: 3
            ) * 0.009)
            path.move(to: CGPoint(x: -40, y: originY))

            let segmentCount = 8
            for segment in 0..<segmentCount {
                let startX = size.width * CGFloat(segment) / CGFloat(segmentCount)
                let endX = size.width * CGFloat(segment + 1) / CGFloat(segmentCount)
                let direction: CGFloat = (segment + index).isMultiple(of: 2) ? 1 : -1
                let endY = originY + direction * amplitude
                path.addCurve(
                    to: CGPoint(x: endX, y: endY),
                    control1: CGPoint(
                        x: startX + (endX - startX) * 0.36,
                        y: originY - direction * amplitude * 1.7
                    ),
                    control2: CGPoint(
                        x: startX + (endX - startX) * 0.70,
                        y: endY + direction * amplitude * 1.4
                    )
                )
            }

            context.stroke(
                path,
                with: .color(colors[index % colors.count]),
                style: StrokeStyle(
                    lineWidth: index.isMultiple(of: 3) ? 1.1 : 0.65,
                    lineCap: .round
                )
            )
        }
    }

    private func drawRegistrationMarks(
        context: inout GraphicsContext,
        size: CGSize
    ) {
        let color = Color.primary.opacity(increaseContrast ? 0.18 : 0.07)
        for x in stride(from: CGFloat(72), through: size.width, by: 144) {
            var mark = Path()
            mark.move(to: CGPoint(x: x, y: size.height - 42))
            mark.addLine(to: CGPoint(x: x, y: size.height - 34))
            context.stroke(mark, with: .color(color), lineWidth: 0.75)
        }
    }
}

/// A raised studio plane with a quiet spectral edge. It replaces the prior
/// divider-only sections so hierarchy is visible in both sparse and dense
/// workspaces while keeping the content itself fully native and selectable.
private struct AuralignWrappedPanelHighlightPath: Shape {
    func path(in rect: CGRect) -> Path {
        let radius = min(AuralignRadius.canvas, rect.height * 0.44)
        var path = Path()
        path.move(to: CGPoint(x: 0.5, y: rect.height - 0.5))
        path.addLine(to: CGPoint(x: 0.5, y: radius))
        path.addQuadCurve(
            to: CGPoint(x: radius, y: 0.5),
            control: CGPoint(x: 0.5, y: 0.5)
        )
        path.addLine(to: CGPoint(x: rect.width - 0.5, y: 0.5))
        return path
    }
}

private struct AuralignWrappedPanelHighlight: View {
    let accent: Color
    let lineWidth: CGFloat

    var body: some View {
        GeometryReader { geometry in
            AuralignWrappedPanelHighlightPath()
                .stroke(
                    LinearGradient(
                        stops: [
                            .init(color: Color.clear, location: 0),
                            .init(color: accent.opacity(0.42), location: 0.18),
                            .init(color: accent.opacity(0.74), location: 0.38),
                            .init(color: accent.opacity(0.28), location: 0.70),
                            .init(color: Color.clear, location: 1),
                        ],
                        startPoint: .bottomLeading,
                        endPoint: .topTrailing
                    ),
                    style: StrokeStyle(
                        lineWidth: lineWidth,
                        lineCap: .round,
                        lineJoin: .round
                    )
                )
                .frame(
                    width: min(max(geometry.size.width * 0.34, 150), 320),
                    height: AuralignRadius.canvas + 16,
                    alignment: .topLeading
                )
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct AuralignStudioPanel<Content: View>: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency

    var accent: AuralignSpectrumStop = .recognition
    var inset: CGFloat = AuralignVisualSystem.spacingL
    let content: Content

    init(
        accent: AuralignSpectrumStop = .recognition,
        inset: CGFloat = AuralignVisualSystem.spacingL,
        @ViewBuilder content: () -> Content
    ) {
        self.accent = accent
        self.inset = inset
        self.content = content()
    }

    var body: some View {
        content
            .padding(inset)
            .background {
                ZStack {
                    RoundedRectangle(
                        cornerRadius: AuralignRadius.canvas,
                        style: .continuous
                    )
                    .fill(panelFill)

                    AuralignOpticalSurface(
                        cornerRadius: AuralignRadius.canvas,
                        accent: accent,
                        intensity: 0.72
                    )
                }
            }
            .overlay(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: AuralignRadius.canvas)
                    .strokeBorder(
                        increaseContrast
                            ? Color.primary
                            : AuralignSurface.hairline.opacity(0.62),
                        lineWidth: increaseContrast ? 1.25 : 0.5
                    )
            }
            .overlay(alignment: .topLeading) {
                if !reduceTransparency
                    && !increaseContrast
                    && !differentiateWithoutColor
                {
                    AuralignWrappedPanelHighlight(
                        accent: accent.color,
                        lineWidth: 1
                    )
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: AuralignRadius.canvas))
            .shadow(
                color: Color.black.opacity(
                    reduceTransparency ? 0 : AuralignElevation.raised.shadowOpacity
                ),
                radius: AuralignElevation.raised.shadowRadius,
                y: AuralignElevation.raised.shadowOffsetY
            )
    }

    private var panelFill: Color {
        if colorScheme == .dark {
            return Color(
                red: reduceTransparency ? 0.088 : 0.080,
                green: reduceTransparency ? 0.075 : 0.066,
                blue: reduceTransparency ? 0.123 : 0.112
            )
            .opacity(reduceTransparency ? 1 : 0.91)
        }
        return Color(
            red: 0.995,
            green: reduceTransparency ? 0.985 : 0.980,
            blue: 1
        )
        .opacity(reduceTransparency ? 1 : 0.90)
    }
}

/// The prominent action language used by the global Start/Cancel authority.
/// It remains a real SwiftUI Button and retains native focus, disabled,
/// keyboard, and accessibility behavior.
struct AuralignPrimaryActionButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceMotion) private var reduceMotion

    var destructive = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AuralignType.button)
            .foregroundStyle(isEnabled ? Color.white : Color.secondary)
            .padding(.horizontal, 15)
            .frame(minHeight: 30)
            .background {
                Capsule()
                    .fill(actionFill(configuration: configuration))
            }
            .overlay {
                Capsule()
                    .strokeBorder(
                        actionEdge,
                        lineWidth: increaseContrast ? 1.25 : 0.75
                    )
            }
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
            .opacity(isEnabled ? 1 : 0.66)
            .animation(
                AuralignMotion.animation(
                    AuralignMotion.selection,
                    reduceMotion: reduceMotion
                ),
                value: configuration.isPressed
            )
    }

    private func actionFill(configuration: Configuration) -> AnyShapeStyle {
        if !isEnabled {
            return AnyShapeStyle(AuralignSurface.raised)
        }
        if destructive {
            return AnyShapeStyle(
                Color(red: 0.48, green: 0.04, blue: 0.07)
                    .opacity(configuration.isPressed ? 0.86 : 1)
            )
        }
        let opacity = configuration.isPressed ? 0.86 : 1.0
        return AnyShapeStyle(
            LinearGradient(
                colors: [
                    Color(red: 0.125, green: 0.090, blue: 0.235)
                        .opacity(opacity),
                    Color(red: 0.205, green: 0.090, blue: 0.265)
                        .opacity(opacity)
                ],
                startPoint: .leading,
                endPoint: .trailing
            )
        )
    }

    private var actionEdge: AnyShapeStyle {
        if increaseContrast {
            return AnyShapeStyle(Color.primary)
        }
        if !isEnabled {
            return AnyShapeStyle(AuralignSurface.hairline.opacity(0.35))
        }
        return AnyShapeStyle(
            LinearGradient(
                colors: [
                    AuralignSpectrumStop.source.color.opacity(0.86),
                    AuralignSpectrumStop.alignment.color.opacity(0.92),
                    AuralignSpectrumStop.delivery.color.opacity(0.76)
                ],
                startPoint: .leading,
                endPoint: .trailing
            )
        )
    }
}
