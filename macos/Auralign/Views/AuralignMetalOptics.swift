// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import SwiftUI

enum AuralignRendererEffectTier: String {
    case staticOnly = "static"
    case swiftuiMetal
}

/// One deliberately small gain adjustment shared by every declared optical
/// material. This strengthens the existing finish without changing its
/// footprint, adding a render pass, or making low-intensity surfaces compete
/// with their content.
enum AuralignOpticalIntensity {
    static let subtleLift: Double = 1.06

    static func lifted(_ intensity: Double) -> Double {
        min(max(intensity * subtleLift, 0), 1)
    }
}

/// One shared contract for the app's explicitly GPU-backed decorative layer.
///
/// These shaders are static and analytic. They are removed when an
/// accessibility appearance asks for opaque, high-contrast, or
/// color-independent structure. The surrounding semantic SwiftUI hierarchy is
/// never filtered.
enum AuralignOpticalRenderingPolicy {
    static let fixedPhase = "static"

    static func effectTier(
        reduceTransparency: Bool,
        increaseContrast: Bool,
        differentiateWithoutColor: Bool
    ) -> AuralignRendererEffectTier {
        if reduceTransparency
            || increaseContrast
            || differentiateWithoutColor
        {
            return .staticOnly
        }
        return .swiftuiMetal
    }

    static func resolvedIntensity(
        _ intensity: Double,
        windowIsActive: Bool
    ) -> Double {
        let lifted = AuralignOpticalIntensity.lifted(intensity)
        return windowIsActive ? lifted : lifted * 0.42
    }
}

enum AuralignOpticalMotionPolicy {
    static let maximumRefreshRate: Double = 15
    static let refreshInterval: TimeInterval = 1 / maximumRefreshRate
    static let loopPeriod: TimeInterval = 8

    static let captureAllowsMotion: Bool = {
        #if DEBUG
        return ProcessInfo.processInfo
            .environment["AURALIGN_SNAPSHOT_ON_LAUNCH"] == nil
        #else
        return true
        #endif
    }()

    static func allowsCoreLensAnimation(
        isActiveOperation: Bool,
        reduceMotion: Bool,
        windowIsActive: Bool,
        metalIsEnabled: Bool,
        captureAllowsMotion: Bool
    ) -> Bool {
        isActiveOperation
            && !reduceMotion
            && windowIsActive
            && metalIsEnabled
            && captureAllowsMotion
    }
}

/// A low-opacity GPU diffusion field isolated behind workspace-hero content.
struct AuralignHeroMetalOptics: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.controlActiveState) private var controlActiveState

    var intensity: Double = 1

    @ViewBuilder
    var body: some View {
        if metalIsEnabled {
            RoundedRectangle(
                cornerRadius: AuralignRadius.hero,
                style: .continuous
            )
            .fill(heroShader)
            .layerEffect(
                ShaderLibrary.auralignHeroDiffusionDisplacement(
                    .boundingRect,
                    .float(resolvedIntensity)
                ),
                maxSampleOffset: CGSize(width: 1, height: 1),
                isEnabled: true
            )
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
    }

    private var heroShader: Shader {
        var shader = ShaderLibrary.auralignHeroDiffusion(
            .boundingRect,
            .float(resolvedIntensity),
            .float(colorScheme == .light ? 1 : 0)
        )
        shader.dithersColor = true
        return shader
    }

    private var metalIsEnabled: Bool {
        AuralignOpticalRenderingPolicy.effectTier(
            reduceTransparency: reduceTransparency,
            increaseContrast: increaseContrast,
            differentiateWithoutColor: differentiateWithoutColor
        ) == .swiftuiMetal
    }

    private var resolvedIntensity: Double {
        AuralignOpticalRenderingPolicy.resolvedIntensity(
            intensity,
            windowIsActive: controlActiveState != .inactive
        )
    }
}

/// The Offline Core's unique GPU material. The semantic symbol remains a
/// normal SwiftUI image above this decorative layer so it stays crisp.
struct AuralignCoreLensMetalMaterial: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.auralignDifferentiateWithoutColor)
    private var differentiateWithoutColor
    @Environment(\.auralignIncreaseContrast) private var increaseContrast
    @Environment(\.auralignReduceMotion) private var reduceMotion
    @Environment(\.auralignReduceTransparency) private var reduceTransparency
    @Environment(\.controlActiveState) private var controlActiveState

    let accent: Color
    let isActiveOperation: Bool

    @ViewBuilder
    var body: some View {
        if metalIsEnabled {
            TimelineView(
                .animation(
                    minimumInterval:
                        AuralignOpticalMotionPolicy.refreshInterval,
                    paused: !animationIsAllowed
                )
            ) { timeline in
                lensMaterial(
                    opticalPhase: animationIsAllowed
                        ? opticalPhase(at: timeline.date)
                        : 0
                )
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
    }

    private var animationIsAllowed: Bool {
        AuralignOpticalMotionPolicy.allowsCoreLensAnimation(
            isActiveOperation: isActiveOperation,
            reduceMotion: reduceMotion,
            windowIsActive: controlActiveState != .inactive,
            metalIsEnabled: metalIsEnabled,
            captureAllowsMotion:
                AuralignOpticalMotionPolicy.captureAllowsMotion
        )
    }

    private func opticalPhase(at date: Date) -> Double {
        let period = AuralignOpticalMotionPolicy.loopPeriod
        return date.timeIntervalSinceReferenceDate
            .truncatingRemainder(dividingBy: period) / period
    }

    private func lensMaterial(opticalPhase: Double) -> some View {
        let highlightCenter = highlightCenter(at: opticalPhase)
        return Circle()
            .fill(
                ShaderLibrary.auralignCoreLensMaterial(
                    .boundingRect,
                    .color(accent),
                    .float(resolvedIntensity),
                    .float(colorScheme == .light ? 1 : 0),
                    .float2(highlightCenter)
                )
            )
            .layerEffect(
                ShaderLibrary.auralignCoreLensDisplacement(
                    .boundingRect,
                    .float(resolvedIntensity)
                ),
                maxSampleOffset: CGSize(width: 1, height: 1),
                isEnabled: true
            )
    }

    private func highlightCenter(at opticalPhase: Double) -> CGPoint {
        let angle = opticalPhase * 2 * Double.pi
        return CGPoint(
            x: -0.34 + sin(angle) * 0.045,
            y: -0.42 + cos(angle) * 0.035
        )
    }

    private var metalIsEnabled: Bool {
        AuralignOpticalRenderingPolicy.effectTier(
            reduceTransparency: reduceTransparency,
            increaseContrast: increaseContrast,
            differentiateWithoutColor: differentiateWithoutColor
        ) == .swiftuiMetal
    }

    private var resolvedIntensity: Double {
        AuralignOpticalRenderingPolicy.resolvedIntensity(
            0.90,
            windowIsActive: controlActiveState != .inactive
        )
    }
}
