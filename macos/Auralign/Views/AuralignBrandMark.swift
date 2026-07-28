// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

/// A small-size adaptation of the application icon for persistent UI chrome.
///
/// The Finder/Dock icon keeps its full material, rails, nodes, and depth. At
/// sidebar scale those details collapse into noise, so this mark retains only
/// the recognizable dark field and mirrored signal.
struct AuralignSidebarBrandMark: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor

    var body: some View {
        Canvas(opaque: false, rendersAsynchronously: false) { context, size in
            guard size.width > 1, size.height > 1 else { return }

            let bounds = CGRect(origin: .zero, size: size)
            let tile = Path(
                roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5),
                cornerRadius: 11
            )
            context.fill(tile, with: tileFill(size: size))
            context.stroke(
                tile,
                with: .color(
                    increaseContrast
                        ? .primary
                        : Color(red: 0.30, green: 0.39, blue: 0.58)
                            .opacity(0.72)
                ),
                lineWidth: increaseContrast ? 1.5 : 0.75
            )

            drawSignal(context: &context, size: size)
        }
        .frame(width: 46, height: 46)
        .accessibilityHidden(true)
    }

    private func tileFill(size: CGSize) -> GraphicsContext.Shading {
        if increaseContrast {
            return .color(
                colorScheme == .dark
                    ? Color.black
                    : Color.white
            )
        }
        return .linearGradient(
            Gradient(colors: [
                Color(red: 0.025, green: 0.050, blue: 0.125),
                Color(red: 0.055, green: 0.040, blue: 0.145),
            ]),
            startPoint: .zero,
            endPoint: CGPoint(x: size.width, y: size.height)
        )
    }

    private func drawSignal(
        context: inout GraphicsContext,
        size: CGSize
    ) {
        let amplitudes: [CGFloat] = [
            0.08, 0.17, 0.34, 0.72, 0.38, 0.88, 0.47, 0.66, 0.30, 0.14, 0.07,
        ]
        let leading = size.width * 0.10
        let trailing = size.width * 0.90
        let centerY = size.height * 0.50
        let maximumHalf = size.height * 0.31
        let step = (trailing - leading) / CGFloat(amplitudes.count - 1)

        var signal = Path()
        signal.move(
            to: CGPoint(
                x: leading,
                y: centerY - amplitudes[0] * maximumHalf
            )
        )
        for (index, amplitude) in amplitudes.enumerated().dropFirst() {
            signal.addLine(
                to: CGPoint(
                    x: leading + CGFloat(index) * step,
                    y: centerY - amplitude * maximumHalf
                )
            )
        }
        for (index, amplitude) in amplitudes.enumerated().reversed() {
            signal.addLine(
                to: CGPoint(
                    x: leading + CGFloat(index) * step,
                    y: centerY + amplitude * maximumHalf
                )
            )
        }
        signal.closeSubpath()

        if increaseContrast || differentiateWithoutColor {
            context.fill(signal, with: .color(monochromeInk))
        } else {
            context.fill(
                signal,
                with: .linearGradient(
                    Gradient(colors: [
                        AuralignSpectrumStop.source.color,
                        AuralignSpectrumStop.recognition.color,
                        AuralignSpectrumStop.alignment.color,
                        AuralignSpectrumStop.delivery.color,
                    ]),
                    startPoint: CGPoint(x: leading, y: centerY),
                    endPoint: CGPoint(x: trailing, y: centerY)
                )
            )
        }

        var centerLine = Path()
        centerLine.move(to: CGPoint(x: leading, y: centerY))
        centerLine.addLine(to: CGPoint(x: trailing, y: centerY))
        context.stroke(
            centerLine,
            with: .color(
                colorScheme == .dark
                    ? Color.black.opacity(0.48)
                    : Color.white.opacity(0.52)
            ),
            lineWidth: 0.75
        )
    }

    private var monochromeInk: Color {
        if increaseContrast {
            return .primary
        }
        // Differentiate Without Color retains the ordinary dark brand tile in
        // both appearances, so use explicit light ink rather than semantic
        // primary (which would become black in the light appearance).
        return .white
    }
}
