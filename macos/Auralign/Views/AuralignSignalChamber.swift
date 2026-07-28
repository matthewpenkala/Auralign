// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// A recessed optical housing for Auralign's source-to-delivery field.
///
/// The chamber is intentionally constructed from native gradients and vector
/// strokes rather than imported imagery. Its static outer planes make the
/// drawn field feel embedded in the workspace while leaving every semantic
/// label and control in ordinary SwiftUI outside this decorative layer.
struct AuralignSignalChamber<Content: View>: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.controlActiveState) private var controlActiveState

    var emphasis: AuralignSpectrumStop?
    let content: Content

    init(
        emphasis: AuralignSpectrumStop? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.emphasis = emphasis
        self.content = content()
    }

    var body: some View {
        content
            .padding(.horizontal, AuralignVisualSystem.spacingM)
            .padding(.vertical, AuralignVisualSystem.spacingS)
            .background {
                chamberMaterial
                    .accessibilityHidden(true)
                    .allowsHitTesting(false)
            }
            .overlay {
                AuralignSignalChamberFrame(
                    emphasis: resolvedEmphasis,
                    increaseContrast: increaseContrast,
                    reduceTransparency: reduceTransparency,
                    monochrome: differentiateWithoutColor
                )
                .accessibilityHidden(true)
                .allowsHitTesting(false)
            }
            .clipShape(
                RoundedRectangle(
                    cornerRadius: AuralignRadius.hero,
                    style: .continuous
                )
            )
            .saturation(controlActiveState == .inactive ? 0.48 : 1)
            .shadow(
                color: Color.black.opacity(shadowOpacity),
                radius: reduceTransparency ? 0 : 8,
                y: reduceTransparency ? 0 : 3
            )
    }

    @ViewBuilder
    private var chamberMaterial: some View {
        ZStack {
            RoundedRectangle(
                cornerRadius: AuralignRadius.hero,
                style: .continuous
            )
            .fill(outerMaterial)

            RoundedRectangle(
                cornerRadius: AuralignRadius.inner(
                    of: AuralignRadius.hero,
                    inset: 4
                ),
                style: .continuous
            )
            .fill(innerMaterial)
            .padding(4)

            if !reduceTransparency && !increaseContrast {
                LinearGradient(
                    stops: [
                        .init(color: Color.white.opacity(0.14), location: 0),
                        .init(color: Color.white.opacity(0.025), location: 0.16),
                        .init(color: Color.clear, location: 0.42),
                        .init(color: Color.black.opacity(0.19), location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                if let resolvedEmphasis {
                    RadialGradient(
                        colors: [
                            resolvedEmphasis.color.opacity(
                                colorScheme == .dark ? 0.12 : 0.08
                            ),
                            Color.clear,
                        ],
                        center: .center,
                        startRadius: 0,
                        endRadius: 240
                    )
                }
            }
        }
    }

    private var outerMaterial: AnyShapeStyle {
        if reduceTransparency {
            return AnyShapeStyle(AuralignSurface.raised)
        }
        if colorScheme == .dark {
            return AnyShapeStyle(
                LinearGradient(
                    colors: [
                        Color(red: 0.145, green: 0.140, blue: 0.175),
                        Color(red: 0.060, green: 0.055, blue: 0.085),
                        Color(red: 0.115, green: 0.100, blue: 0.145),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
        }
        return AnyShapeStyle(
            LinearGradient(
                colors: [
                    Color(red: 0.94, green: 0.95, blue: 0.98),
                    Color(red: 0.76, green: 0.78, blue: 0.84),
                    Color(red: 0.89, green: 0.90, blue: 0.94),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }

    private var innerMaterial: AnyShapeStyle {
        if reduceTransparency {
            return AnyShapeStyle(AuralignSurface.canvasField(
                reduceTransparency: true
            ))
        }
        if colorScheme == .dark {
            return AnyShapeStyle(
                LinearGradient(
                    colors: [
                        Color(red: 0.038, green: 0.039, blue: 0.060),
                        Color(red: 0.064, green: 0.052, blue: 0.086),
                        Color(red: 0.027, green: 0.029, blue: 0.045),
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
        }
        return AnyShapeStyle(
            LinearGradient(
                colors: [
                    Color(red: 0.88, green: 0.90, blue: 0.95),
                    Color(red: 0.96, green: 0.95, blue: 0.98),
                    Color(red: 0.84, green: 0.86, blue: 0.91),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
    }

    private var resolvedEmphasis: AuralignSpectrumStop? {
        guard controlActiveState != .inactive else { return nil }
        guard !differentiateWithoutColor else { return nil }
        return emphasis
    }

    private var shadowOpacity: Double {
        guard controlActiveState != .inactive else { return 0.08 }
        return colorScheme == .dark ? 0.34 : 0.14
    }
}

/// Crisp construction lines and phase registration marks. Keeping these in one
/// Canvas avoids a stack of tiny shape views and guarantees that the artwork
/// remains a single accessibility-hidden decoration.
private struct AuralignSignalChamberFrame: View {
    var emphasis: AuralignSpectrumStop?
    var increaseContrast: Bool
    var reduceTransparency: Bool
    var monochrome: Bool

    var body: some View {
        Canvas(opaque: false, rendersAsynchronously: false) { context, size in
            guard size.width > 1, size.height > 1 else { return }

            let bounds = CGRect(origin: .zero, size: size)
            let outer = Path(
                roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5),
                cornerRadius: AuralignRadius.hero
            )
            let innerInset: CGFloat = 4.5
            let inner = Path(
                roundedRect: bounds.insetBy(dx: innerInset, dy: innerInset),
                cornerRadius: AuralignRadius.inner(
                    of: AuralignRadius.hero,
                    inset: innerInset
                )
            )

            context.stroke(
                outer,
                with: .color(
                    increaseContrast
                        ? .primary
                        : .white.opacity(reduceTransparency ? 0.10 : 0.20)
                ),
                lineWidth: increaseContrast ? 1.5 : 1
            )
            context.stroke(
                inner,
                with: .color(
                    increaseContrast
                        ? .primary
                        : .black.opacity(reduceTransparency ? 0.20 : 0.62)
                ),
                lineWidth: increaseContrast ? 1.25 : 1
            )

            if !reduceTransparency && !increaseContrast {
                drawEdgeLight(context: &context, size: size)
            }
            drawPhaseRegisters(context: &context, size: size)
        }
    }

    private func drawEdgeLight(
        context: inout GraphicsContext,
        size: CGSize
    ) {
        let inset: CGFloat = 22
        var top = Path()
        top.move(to: CGPoint(x: inset, y: 5.5))
        top.addLine(to: CGPoint(x: size.width - inset, y: 5.5))
        context.stroke(
            top,
            with: .linearGradient(
                Gradient(colors: [
                    Color.clear,
                    Color.white.opacity(0.32),
                    Color.clear,
                ]),
                startPoint: CGPoint(x: inset, y: 5.5),
                endPoint: CGPoint(x: size.width - inset, y: 5.5)
            ),
            lineWidth: 0.75
        )

        var bottom = Path()
        bottom.move(to: CGPoint(x: inset, y: size.height - 5.5))
        bottom.addLine(to: CGPoint(x: size.width - inset, y: size.height - 5.5))
        context.stroke(
            bottom,
            with: .color(.black.opacity(0.42)),
            lineWidth: 1
        )
    }

    private func drawPhaseRegisters(
        context: inout GraphicsContext,
        size: CGSize
    ) {
        let phases = AlignmentCanvasPhase.allCases
        guard !phases.isEmpty else { return }

        for (index, phase) in phases.enumerated() {
            let unit = (CGFloat(index) + 0.5) / CGFloat(phases.count)
            let x = unit * size.width
            let color: Color
            if increaseContrast || monochrome {
                color = .primary.opacity(0.76)
            } else if emphasis == phase.spectrumStop {
                color = phase.spectrumStop.color.opacity(0.92)
            } else {
                color = phase.spectrumStop.color.opacity(
                    reduceTransparency ? 0.38 : 0.52
                )
            }

            var register = Path()
            register.move(to: CGPoint(x: x, y: 6))
            register.addLine(to: CGPoint(x: x, y: 10))
            register.move(to: CGPoint(x: x, y: size.height - 10))
            register.addLine(to: CGPoint(x: x, y: size.height - 6))
            context.stroke(
                register,
                with: .color(color),
                style: StrokeStyle(lineWidth: 1, lineCap: .round)
            )
        }
    }
}
