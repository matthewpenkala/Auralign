// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

// MARK: - Motion policy

enum AlignmentFieldMotion {
    /// Ambient motion is suspended while a deterministic capture is pending.
    ///
    /// `SnapshotDriver.waitForStableLayout` only accepts a frame after three
    /// identical consecutive renders, so a continuously animating `Canvas`
    /// would never let a capture settle. Interactive runtime behaviour is
    /// unaffected — this is false only when the process was launched with a
    /// snapshot destination, which is a DEBUG-only automation path.
    static let ambientAllowed: Bool = {
        #if DEBUG
        return ProcessInfo.processInfo
            .environment["AURALIGN_SNAPSHOT_ON_LAUNCH"] == nil
        #else
        return true
        #endif
    }()

    /// The field never asks SwiftUI for more than thirty updates per second.
    ///
    /// The source animation evidence uses a 30 FPS loop, but this is not a
    /// sprite reconstruction: Auralign derives its phase from absolute time so
    /// a busy UI can skip a presentation update without slowing the loop.
    static let maximumRefreshRate: Double = 30
    static let refreshInterval: TimeInterval = 1 / maximumRefreshRate

    /// Seconds for one seamless, non-directional ambient cycle.
    static let loopPeriod: TimeInterval = 2.8

    /// Pure gate used by the view and unit tests. Motion is decorative, so any
    /// preference or runtime condition that argues against it wins.
    static func allowsAnimation(
        hasActiveEnergy: Bool,
        reduceMotion: Bool,
        windowIsActive: Bool,
        captureAllowsMotion: Bool
    ) -> Bool {
        hasActiveEnergy
            && !reduceMotion
            && windowIsActive
            && captureAllowsMotion
    }
}

// MARK: - Energy policy and deterministic detail

/// Visual intensity for one phase.
///
/// These levels are not measurements. They are a categorical presentation of
/// phase status already shown in text beneath the field:
///
/// - `quiet`: no particulate detail;
/// - `settled`: a few static marks for work known to be complete;
/// - `active`: a bounded ambient loop for work the engine reports as active.
enum AlignmentFieldEnergyLevel: Equatable {
    case quiet
    case settled
    case active
}

/// Converts the real projection into a deliberately narrow visual policy.
///
/// Pre-run postures never energize, even if a malformed caller supplies a
/// completed stage. Active animation is accepted only while the job is in an
/// operational posture. Completed work may retain quiet, static "settled"
/// marks after a failure or cancellation because that completion is real.
struct AlignmentFieldEnergyPolicy: Equatable {
    struct Entry: Equatable {
        var phase: AlignmentCanvasPhase
        var level: AlignmentFieldEnergyLevel
    }

    var entries: [Entry]

    init(projection: AlignmentCanvasProjection) {
        entries = projection.segments.map { segment in
            Entry(
                phase: segment.phase,
                level: Self.level(
                    for: segment.status,
                    posture: projection.posture
                )
            )
        }
    }

    var hasActiveEnergy: Bool {
        entries.contains { $0.level == .active }
    }

    func level(for phase: AlignmentCanvasPhase) -> AlignmentFieldEnergyLevel {
        entries.first(where: { $0.phase == phase })?.level ?? .quiet
    }

    private static func level(
        for status: AlignmentCanvasPhaseStatus,
        posture: AlignmentCanvasProjection.Posture
    ) -> AlignmentFieldEnergyLevel {
        switch posture {
        case .empty, .sourceSelected, .ready:
            return .quiet
        case .running, .stopping, .finalizing:
            switch status {
            case .active:
                return .active
            case .complete:
                return .settled
            case .pending, .halted, .skipped:
                return .quiet
            }
        case .completed, .failed, .cancelled:
            return status == .complete ? .settled : .quiet
        }
    }
}

/// A compact, deterministic set of points and short filaments.
///
/// Placement comes from the already-authorized `SourceIdentitySeed` when a
/// source has been probed. A stable product seed is the fallback for diagnostic
/// projections that have stage state but no probe. No filename, path, sample,
/// confidence, timing, waveform, or progress value participates.
struct AlignmentFieldEnergyPattern: Equatable {
    struct Particle: Equatable {
        var phase: AlignmentCanvasPhase
        var level: AlignmentFieldEnergyLevel
        /// Base location in field coordinates, both normalized.
        var unitX: CGFloat
        var unitY: CGFloat
        /// Small closed orbit used only by an active particle.
        var orbitUnitX: CGFloat
        var orbitUnitY: CGFloat
        var phaseOffset: Double
        /// Integral harmonics keep the ambient loop seamless.
        var harmonic: Int
        var radius: CGFloat
        var aspect: CGFloat
    }

    var particles: [Particle]

    init(
        geometry: AlignmentFieldGeometry,
        projection: AlignmentCanvasProjection,
        policy: AlignmentFieldEnergyPolicy
    ) {
        let sourceSeed = projection.sourceIdentitySeed?.value
            ?? 0xA11A_6E5E_51A1_2026
        var built: [Particle] = []

        for cell in geometry.cells {
            let level = policy.level(for: cell.phase)
            let count: Int
            switch level {
            case .quiet:
                count = 0
            case .settled:
                count = 3
            case .active:
                count = 9
            }
            guard count > 0 else { continue }

            let phaseSalt = UInt64(cell.phase.rawValue + 1)
                &* 0x9E37_79B9_7F4A_7C15
            var generator = AlignmentFieldGenerator(seed: sourceSeed ^ phaseSalt)
            let span = max(cell.end - cell.start, 0)

            for _ in 0..<count {
                let localX = generator.value(in: 0.14...0.86)
                let active = level == .active
                built.append(
                    Particle(
                        phase: cell.phase,
                        level: level,
                        unitX: cell.start + span * CGFloat(localX),
                        unitY: CGFloat(generator.value(in: -0.58...0.58)),
                        orbitUnitX: active
                            ? span * CGFloat(generator.value(in: 0.018...0.052))
                            : 0,
                        orbitUnitY: active
                            ? CGFloat(generator.value(in: 0.05...0.16))
                            : 0,
                        phaseOffset: generator.value(in: 0...1),
                        harmonic: active ? generator.integer(in: 1...2) : 0,
                        radius: CGFloat(generator.value(in: 0.75...1.4)),
                        aspect: CGFloat(generator.value(in: 1.0...3.2))
                    )
                )
            }
        }

        particles = built
    }
}

/// SplitMix64 supplies reproducible, dependency-free layout variation.
private struct AlignmentFieldGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        state = seed
    }

    mutating func next() -> UInt64 {
        state = state &+ 0x9E37_79B9_7F4A_7C15
        var value = state
        value = (value ^ (value >> 30)) &* 0xBF58_476D_1CE4_E5B9
        value = (value ^ (value >> 27)) &* 0x94D0_49BB_1331_11EB
        return value ^ (value >> 31)
    }

    mutating func value(in range: ClosedRange<Double>) -> Double {
        let unit = Double(next() >> 11) / Double(UInt64(1) << 53)
        return range.lowerBound + (range.upperBound - range.lowerBound) * unit
    }

    mutating func integer(in range: ClosedRange<Int>) -> Int {
        let count = UInt64(range.upperBound - range.lowerBound + 1)
        return range.lowerBound + Int(next() % count)
    }
}

// MARK: - Geometry

/// Pure geometry for the alignment field.
///
/// This is a value type with no view dependencies so the truthfulness rules can
/// be unit-tested without rendering. Envelope thickness is derived from the
/// number of stages the effective configuration will actually run. Phase
/// widths stay categorical because these five presentation groups are not a
/// claim about literal engine execution order.
struct AlignmentFieldGeometry: Equatable {
    struct Cell: Equatable {
        var phase: AlignmentCanvasPhase
        var status: AlignmentCanvasPhaseStatus
        /// Unit span across the field, 0...1.
        var start: CGFloat
        var end: CGFloat
        /// Envelope thickness 0...1, from the real runnable stage count.
        var weight: CGFloat

        var center: CGFloat { (start + end) / 2 }
    }

    var cells: [Cell]

    /// Explicit truthfulness signal for consumers. The current phase map never
    /// maps elapsed time onto width.
    var widthsAreMeasured: Bool

    init(projection: AlignmentCanvasProjection) {
        let segments = projection.segments
        guard !segments.isEmpty else {
            cells = []
            widthsAreMeasured = false
            return
        }

        widthsAreMeasured = false

        // --- Widths -------------------------------------------------------
        let shares = Array(
            repeating: CGFloat(1) / CGFloat(segments.count),
            count: segments.count
        )

        // --- Envelope thickness -------------------------------------------
        let maximumStages = max(
            segments.map(\.activeStageCount).max() ?? 1,
            1
        )

        var built: [Cell] = []
        var cursor: CGFloat = 0
        for (index, segment) in segments.enumerated() {
            let width = shares[index]
            let weight: CGFloat
            if segment.status == .skipped || segment.activeStageCount == 0 {
                weight = 0.06
            } else {
                let ratio = CGFloat(segment.activeStageCount)
                    / CGFloat(maximumStages)
                weight = 0.34 + 0.66 * ratio
            }
            built.append(
                Cell(
                    phase: segment.phase,
                    status: segment.status,
                    start: cursor,
                    end: cursor + width,
                    weight: weight
                )
            )
            cursor += width
        }
        cells = built
    }

    /// Smoothly interpolated envelope thickness at a unit position.
    func weight(atUnit unit: CGFloat) -> CGFloat {
        guard let first = cells.first, let last = cells.last else { return 0 }
        if unit <= first.center { return first.weight }
        if unit >= last.center { return last.weight }
        for index in 0..<(cells.count - 1) {
            let leading = cells[index]
            let trailing = cells[index + 1]
            guard unit >= leading.center, unit <= trailing.center else {
                continue
            }
            let span = max(trailing.center - leading.center, 0.0001)
            let t = (unit - leading.center) / span
            // Smoothstep keeps the silhouette organic rather than faceted.
            let eased = t * t * (3 - 2 * t)
            return leading.weight + (trailing.weight - leading.weight) * eased
        }
        return last.weight
    }
}

// MARK: - View

/// Auralign's signature drawn object.
///
/// The field is a *visual layer only*. It carries no semantics: it is hidden
/// from accessibility, and every fact it depicts is also published as real
/// SwiftUI text by `AlignmentCanvasView`. That split follows the platform rule
/// that individually drawn `Canvas` elements do not become accessible.
struct AlignmentFieldView: View {
    @Environment(\.auralignReduceMotion) private var reduceMotion
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.controlActiveState) private var controlActiveState
    @Environment(\.colorScheme) private var colorScheme

    let projection: AlignmentCanvasProjection

    /// Field height. Tall enough to read as the workspace's focal object rather
    /// than a progress accessory.
    static let height: CGFloat = 104

    private var monochrome: Bool {
        increaseContrast || differentiateWithoutColor
    }

    var body: some View {
        let geometry = AlignmentFieldGeometry(projection: projection)
        let energyPolicy = AlignmentFieldEnergyPolicy(projection: projection)
        let energyPattern = AlignmentFieldEnergyPattern(
            geometry: geometry,
            projection: projection,
            policy: energyPolicy
        )
        let shouldAnimate = AlignmentFieldMotion.allowsAnimation(
            hasActiveEnergy: energyPolicy.hasActiveEnergy,
            reduceMotion: reduceMotion,
            windowIsActive: controlActiveState != .inactive,
            captureAllowsMotion: AlignmentFieldMotion.ambientAllowed
        )
        let field = TimelineView(
            .animation(
                minimumInterval: AlignmentFieldMotion.refreshInterval,
                paused: !shouldAnimate
            )
        ) { timeline in
            Canvas(opaque: false) { context, size in
                let loopPhase: Double? = if shouldAnimate {
                    timeline.date.timeIntervalSinceReferenceDate
                        .truncatingRemainder(
                            dividingBy: AlignmentFieldMotion.loopPeriod
                        )
                        / AlignmentFieldMotion.loopPeriod
                } else {
                    nil
                }
                draw(
                    &context,
                    size: size,
                    geometry: geometry,
                    energyPattern: energyPattern,
                    loopPhase: loopPhase
                )
            }
        }
        Group {
            if metalIsEnabled {
                field.layerEffect(
                    ShaderLibrary.auralignAlignmentFieldDisplacement(
                        .boundingRect,
                        .float(resolvedOpticalIntensity)
                    ),
                    maxSampleOffset: CGSize(width: 1, height: 1),
                    isEnabled: true
                )
            } else {
                field
            }
        }
        .frame(height: Self.height)
        .accessibilityHidden(true)
    }

    private var metalIsEnabled: Bool {
        AuralignOpticalRenderingPolicy.effectTier(
            reduceTransparency: reduceTransparency,
            increaseContrast: increaseContrast,
            differentiateWithoutColor: differentiateWithoutColor
        ) == .swiftuiMetal
    }

    private var resolvedOpticalIntensity: Double {
        AuralignOpticalRenderingPolicy.resolvedIntensity(
            0.70,
            windowIsActive: controlActiveState != .inactive
        )
    }

    // MARK: Drawing

    private func draw(
        _ context: inout GraphicsContext,
        size: CGSize,
        geometry: AlignmentFieldGeometry,
        energyPattern: AlignmentFieldEnergyPattern,
        loopPhase: Double?
    ) {
        guard !geometry.cells.isEmpty, size.width > 1, size.height > 1 else {
            return
        }

        let centreY = size.height * 0.44
        let maximumHalf: CGFloat = 30
        let baseHalf: CGFloat = 3

        // --- Envelope ------------------------------------------------------
        let steps = max(Int(size.width / 2), 32)
        var topPoints: [CGPoint] = []
        var bottomPoints: [CGPoint] = []
        topPoints.reserveCapacity(steps + 1)
        bottomPoints.reserveCapacity(steps + 1)
        for step in 0...steps {
            let unit = CGFloat(step) / CGFloat(steps)
            let x = unit * size.width
            let half = baseHalf + geometry.weight(atUnit: unit) * maximumHalf
            topPoints.append(CGPoint(x: x, y: centreY - half))
            bottomPoints.append(CGPoint(x: x, y: centreY + half))
        }

        // Built with explicit `addLine(to:)` rather than `addLines(_:)`:
        // `addLines` opens a new subpath, which would produce two disconnected
        // ribbons instead of one closed band.
        var envelope = Path()
        envelope.move(to: topPoints[0])
        for point in topPoints.dropFirst() {
            envelope.addLine(to: point)
        }
        for point in bottomPoints.reversed() {
            envelope.addLine(to: point)
        }
        envelope.closeSubpath()

        var crest = Path()
        crest.move(to: topPoints[0])
        crest.addLines(topPoints)

        // --- Spectral fill --------------------------------------------------
        var stops: [Gradient.Stop] = geometry.cells.map { cell in
            Gradient.Stop(
                color: fillColor(
                    for: cell,
                    posture: projection.posture
                ),
                location: min(max(cell.center, 0), 1)
            )
        }
        if let first = stops.first, first.location > 0 {
            stops.insert(
                Gradient.Stop(color: first.color, location: 0),
                at: 0
            )
        }
        if let last = stops.last, last.location < 1 {
            stops.append(Gradient.Stop(color: last.color, location: 1))
        }

        context.fill(
            envelope,
            with: .linearGradient(
                Gradient(stops: stops),
                startPoint: CGPoint(x: 0, y: centreY),
                endPoint: CGPoint(x: size.width, y: centreY)
            )
        )

        // --- Depth ----------------------------------------------------------
        // Occlusion and a specular crest give the band physical form. Both are
        // suppressed in the accessibility appearances, where flat, maximally
        // separated fills matter more than modelling.
        if !monochrome && !reduceTransparency {
            context.drawLayer { layer in
                layer.clip(to: envelope)
                layer.fill(
                    Path(CGRect(origin: .zero, size: size)),
                    with: .linearGradient(
                        Gradient(stops: [
                            .init(color: .white.opacity(0.18), location: 0),
                            .init(color: .clear, location: 0.48),
                            .init(color: .black.opacity(0.22), location: 1),
                        ]),
                        startPoint: CGPoint(x: 0, y: centreY - maximumHalf),
                        endPoint: CGPoint(x: 0, y: centreY + maximumHalf)
                    )
                )
            }
            context.stroke(
                crest,
                with: .color(.white.opacity(0.24)),
                lineWidth: 1
            )
        }

        if monochrome {
            context.stroke(
                envelope,
                with: .color(.primary.opacity(0.75)),
                lineWidth: 1
            )
        }

        drawEnergy(
            &context,
            size: size,
            geometry: geometry,
            envelope: envelope,
            centreY: centreY,
            baseHalf: baseHalf,
            maximumHalf: maximumHalf,
            pattern: energyPattern,
            loopPhase: loopPhase
        )

        // --- Phase boundaries -------------------------------------------------
        for cell in geometry.cells.dropLast() {
            let x = cell.end * size.width
            var divider = Path()
            divider.move(to: CGPoint(x: x, y: centreY - maximumHalf - 4))
            divider.addLine(to: CGPoint(x: x, y: centreY + maximumHalf + 4))
            context.stroke(
                divider,
                with: .color(
                    monochrome
                        ? .primary.opacity(0.55)
                        : .black.opacity(0.35)
                ),
                lineWidth: 1
            )
        }
    }

    private func drawEnergy(
        _ context: inout GraphicsContext,
        size: CGSize,
        geometry: AlignmentFieldGeometry,
        envelope: Path,
        centreY: CGFloat,
        baseHalf: CGFloat,
        maximumHalf: CGFloat,
        pattern: AlignmentFieldEnergyPattern,
        loopPhase: Double?
    ) {
        guard !pattern.particles.isEmpty else { return }

        // A fixed phase is the static accessibility/inactive/snapshot fallback.
        // Live motion is an oscillating closed loop, never a left-to-right
        // traversal, so it cannot be mistaken for unreported progress.
        let resolvedPhase = loopPhase ?? 0.17
        let twoPi = Double.pi * 2

        context.drawLayer { layer in
            layer.clip(to: envelope)

            for particle in pattern.particles {
                guard let cell = geometry.cells.first(where: {
                    $0.phase == particle.phase
                }) else {
                    continue
                }

                let isActive = particle.level == .active
                let angle = twoPi * (
                    resolvedPhase * Double(max(particle.harmonic, 1))
                        + particle.phaseOffset
                )
                let xUnit = particle.unitX
                    + (isActive ? particle.orbitUnitX * CGFloat(sin(angle)) : 0)
                let resolvedUnitX = min(max(xUnit, cell.start), cell.end)
                let half = baseHalf
                    + geometry.weight(atUnit: resolvedUnitX) * maximumHalf
                let yUnit = particle.unitY
                    + (isActive ? particle.orbitUnitY * CGFloat(cos(angle)) : 0)
                let x = resolvedUnitX * size.width
                let y = centreY + min(max(yUnit, -0.78), 0.78) * half
                let pulse = isActive
                    ? 0.68 + 0.32 * (0.5 + 0.5 * sin(angle + .pi / 3))
                    : 0.42
                let coreColor = energyColor(for: particle.phase)

                let coreRect = CGRect(
                    x: x - particle.radius * particle.aspect,
                    y: y - particle.radius,
                    width: particle.radius * particle.aspect * 2,
                    height: particle.radius * 2
                )

                if isActive && !monochrome && !reduceTransparency {
                    let glowScale: CGFloat = 2.8
                    let glowRect = coreRect.insetBy(
                        dx: -particle.radius * glowScale,
                        dy: -particle.radius * glowScale
                    )
                    layer.fill(
                        Path(ellipseIn: glowRect),
                        with: .color(coreColor.opacity(0.10 * pulse))
                    )
                }

                let coreOpacity: Double
                if monochrome || reduceTransparency {
                    coreOpacity = isActive ? 0.90 : 0.55
                } else {
                    coreOpacity = isActive ? 0.72 * pulse : 0.34
                }
                layer.fill(
                    Path(ellipseIn: coreRect),
                    with: .color(coreColor.opacity(coreOpacity))
                )
            }
        }

        guard let active = geometry.cells.first(where: { cell in
            pattern.particles.contains { particle in
                particle.phase == cell.phase
                    && particle.level == .active
            }
        }) else {
            return
        }

        // A centred breathing focus reinforces the active category. Its centre
        // never moves through the phase, so it conveys activity without
        // pretending to know sub-stage position.
        let pulse = loopPhase.map {
            0.5 + 0.5 * sin(twoPi * $0)
        } ?? 0.5
        let x = active.center * size.width
        let radius = 30 + 5 * CGFloat(pulse)
        let focusRect = CGRect(
            x: x - radius,
            y: centreY - radius,
            width: radius * 2,
            height: radius * 2
        )

        context.drawLayer { layer in
            layer.clip(to: envelope)
            if monochrome || reduceTransparency {
                layer.stroke(
                    Path(ellipseIn: focusRect),
                    with: .color(energyColor(for: active.phase).opacity(0.72)),
                    lineWidth: increaseContrast ? 1.5 : 1
                )
            } else {
                layer.fill(
                    Path(ellipseIn: focusRect),
                    with: .radialGradient(
                        Gradient(colors: [
                            .white.opacity(0.13 + 0.05 * pulse),
                            active.phase.spectrumStop.color.opacity(0.06),
                            .clear,
                        ]),
                        center: CGPoint(x: x, y: centreY),
                        startRadius: 0,
                        endRadius: radius
                    )
                )
            }
        }
    }

    // MARK: Colour

    private func fillColor(
        for cell: AlignmentFieldGeometry.Cell,
        posture: AlignmentCanvasProjection.Posture
    ) -> Color {
        if monochrome {
            switch cell.status {
            case .complete:
                return .primary.opacity(0.88)
            case .active:
                return .primary
            case .pending:
                return .secondary.opacity(0.30)
            case .halted:
                return .secondary.opacity(0.22)
            case .skipped:
                return .secondary.opacity(0.10)
            }
        }

        let stop = cell.phase.spectrumStop.color
        switch cell.status {
        case .complete, .active:
            return stop
        case .pending:
            // Identity without a claim: the hue says *which* phase this is, the
            // low intensity says it has not run. Status itself is always
            // carried by the symbol and text beneath the field.
            if posture == .empty {
                // An empty studio shows only a spectral scaffold. Colour
                // resolves once a real source has been inspected, mirroring
                // the field's transition from possibility to configured work.
                return stop.opacity(reduceTransparency ? 0.20 : 0.12)
            }
            return stop.opacity(reduceTransparency ? 0.38 : 0.26)
        case .halted:
            return .secondary.opacity(0.28)
        case .skipped:
            return .secondary.opacity(0.12)
        }
    }

    private func energyColor(for phase: AlignmentCanvasPhase) -> Color {
        if monochrome {
            // Inverse ink keeps the non-colour marks visible inside the
            // primary-colour monochrome band.
            return colorScheme == .dark ? .black : .white
        }
        return phase.spectrumStop.color
    }
}
