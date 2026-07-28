// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

// MARK: - Deliberate boundary exception
//
// Every other drawn surface in Auralign renders only what the engine
// reports. This one is different, by explicit user authorization
// (2026-07-28): it is decorative atmosphere for the selected source, not a
// measurement. No per-sample audio data exists anywhere in the bridge
// contract, so this shape is never presented as a waveform — it carries no
// "waveform" label anywhere in visible or accessible text, its silhouette is
// smooth and abstract rather than jagged per-sample bars, and it is
// `.accessibilityHidden` throughout. What keeps it honest rather than
// arbitrary is that it is a deterministic function of real probed facts:
// the same file, at the same duration/rate/channels/codec, always renders
// the same identity. The product design record documents this representative
// visual boundary.

/// A stable seed derived only from real, already-displayed probe facts —
/// never from the filename or path, so the shape carries no incidental
/// information beyond what the visible metadata line already states.
struct SourceIdentitySeed: Equatable {
    var value: UInt64

    init(durationSeconds: Double?, sampleRate: Int?, channels: Int?, codec: String?) {
        var hasher = FNV1aAccumulator()
        hasher.combine(durationSeconds ?? -1)
        hasher.combine(Double(sampleRate ?? -1))
        hasher.combine(Double(channels ?? -1))
        hasher.combine(codec ?? "")
        value = hasher.value
    }
}

/// A minimal, dependency-free deterministic hash. `Hasher` is explicitly
/// randomized per process by Swift's runtime, which would make the same file
/// render a different identity on every launch — the opposite of the point.
private struct FNV1aAccumulator {
    private(set) var value: UInt64 = 0xcbf2_9ce4_8422_2325

    mutating func combine(_ double: Double) {
        combine(double.bitPattern.description)
    }

    mutating func combine(_ string: String) {
        for byte in string.utf8 {
            value ^= UInt64(byte)
            value = value &* 0x0000_0100_0000_01B3
        }
    }
}

/// A tiny deterministic generator (SplitMix64) — reproducible across
/// launches and platforms, unlike `SystemRandomNumberGenerator`.
private struct SplitMix64: RandomNumberGenerator {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state = state &+ 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

/// A smooth, abstract silhouette in the register of designed plugin art —
/// several seeded sine components summed together, never a jagged per-sample
/// shape, so it reads as atmosphere rather than a measurement.
struct SourceIdentityField: View {
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor

    let seed: SourceIdentitySeed
    var tint: AuralignSpectrumStop = .source

    static let height: CGFloat = 40

    private struct Component {
        var frequency: Double
        var phase: Double
        var amplitude: Double
    }

    private var components: [Component] {
        var generator = SplitMix64(seed: seed.value)
        return (0..<4).map { index in
            Component(
                frequency: Double(1 + index) * Double.random(in: 0.6...1.4, using: &generator),
                phase: Double.random(in: 0...(2 * .pi), using: &generator),
                amplitude: Double.random(in: 0.35...1.0, using: &generator)
                    / Double(index + 1)
            )
        }
    }

    var body: some View {
        Canvas(opaque: false) { context, size in
            draw(&context, size: size)
        }
        .frame(height: Self.height)
        .accessibilityHidden(true)
    }

    private func envelope(
        atUnit unit: Double,
        components: [Component],
        normalizer: Double
    ) -> Double {
        let total = components.reduce(into: 0.0) { sum, component in
            sum += component.amplitude
                * sin(unit * component.frequency * 2 * .pi + component.phase)
        }
        guard normalizer > 0 else { return 0 }
        return total / normalizer
    }

    private func draw(_ context: inout GraphicsContext, size: CGSize) {
        guard size.width > 1, size.height > 1 else { return }
        let centreY = size.height * 0.5
        let maxHalf = size.height * 0.42
        let steps = max(Int(size.width / 3), 24)
        let resolvedComponents = components
        let normalizer = resolvedComponents.reduce(0.0) {
            $0 + $1.amplitude
        }

        var top: [CGPoint] = []
        var bottom: [CGPoint] = []
        for step in 0...steps {
            let unit = Double(step) / Double(steps)
            let raw = envelope(
                atUnit: unit,
                components: resolvedComponents,
                normalizer: normalizer
            )
            // Rectified and floored so the silhouette stays a continuous
            // organic band rather than crossing through zero — a deliberate
            // difference from a real waveform's bipolar shape.
            let magnitude = 0.14 + 0.86 * abs(raw)
            let half = maxHalf * magnitude
            let x = CGFloat(unit) * size.width
            top.append(CGPoint(x: x, y: centreY - half))
            bottom.append(CGPoint(x: x, y: centreY + half))
        }

        var silhouette = Path()
        silhouette.move(to: top[0])
        for point in top.dropFirst() { silhouette.addLine(to: point) }
        for point in bottom.reversed() { silhouette.addLine(to: point) }
        silhouette.closeSubpath()

        let monochrome = increaseContrast || differentiateWithoutColor
        if monochrome {
            context.fill(silhouette, with: .color(.secondary.opacity(0.22)))
            context.stroke(silhouette, with: .color(.primary.opacity(0.5)), lineWidth: 1)
            return
        }

        let base = tint.color
        context.fill(
            silhouette,
            with: .linearGradient(
                Gradient(colors: [
                    base.opacity(reduceTransparency ? 0.55 : 0.34),
                    base.opacity(reduceTransparency ? 0.30 : 0.14),
                ]),
                startPoint: CGPoint(x: 0, y: centreY - maxHalf),
                endPoint: CGPoint(x: 0, y: centreY + maxHalf)
            )
        )
    }
}
