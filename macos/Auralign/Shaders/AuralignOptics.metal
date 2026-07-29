// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

#include <metal_stdlib>
#include <SwiftUI/SwiftUI_Metal.h>
using namespace metal;

/// A static, analytic diffusion field for workspace heroes.
///
/// The function performs no texture reads and contains no loop or time input.
/// It is intentionally cyan-led so the app retains violet as an accent rather
/// than washing every large surface purple.
[[ stitchable ]] half4 auralignHeroDiffusion(
    float2 position,
    float4 bounds,
    float intensity,
    float lightAppearance
) {
    const float2 size = bounds.zw;
    const float2 safeSize = max(size, float2(1.0));
    const float2 uv = (position - bounds.xy) / safeSize;
    const float aspect = safeSize.x / safeSize.y;

    float2 sourceDelta = uv - float2(0.09, 0.84);
    sourceDelta.x *= aspect * 0.48;
    sourceDelta.y *= 1.45;
    const float sourceLobe = exp(-dot(sourceDelta, sourceDelta) * 3.8);

    float2 alignmentDelta = uv - float2(0.58, 0.15);
    alignmentDelta.x *= aspect * 0.34;
    alignmentDelta.y *= 1.62;
    const float alignmentLobe =
        exp(-dot(alignmentDelta, alignmentDelta) * 5.2);

    float2 deliveryDelta = uv - float2(0.96, 0.72);
    deliveryDelta.x *= aspect * 0.52;
    deliveryDelta.y *= 1.38;
    const float deliveryLobe =
        exp(-dot(deliveryDelta, deliveryDelta) * 4.6);

    const half3 cyan = half3(0.18h, 0.78h, 0.96h);
    const half3 blue = half3(0.32h, 0.48h, 1.00h);
    const half3 violet = half3(0.70h, 0.34h, 0.95h);
    half3 spectral =
        cyan * half(sourceLobe * 0.54)
        + blue * half(alignmentLobe * 0.34)
        + violet * half(deliveryLobe * 0.22);

    const float energy = min(
        1.0,
        sourceLobe * 0.56 + alignmentLobe * 0.34 + deliveryLobe * 0.22
    );
    const float appearanceOpacity = mix(0.105, 0.055, lightAppearance);
    const half alpha = half(
        clamp(energy * intensity * appearanceOpacity, 0.0, 0.14)
    );

    return half4(spectral * alpha, alpha);
}

/// A bounded chromatic bend for the decorative workspace-hero diffusion.
///
/// Semantic hero text and controls remain separate SwiftUI layers above this
/// material. Only the analytic diffusion field is sampled, so the effect adds
/// real GPU displacement without filtering readable content.
[[ stitchable ]] half4 auralignHeroDiffusionDisplacement(
    float2 position,
    SwiftUI::Layer layer,
    float4 bounds,
    float intensity
) {
    const float2 safeSize = max(bounds.zw, float2(1.0));
    const float2 p = ((position - bounds.xy) / safeSize - 0.5) * 2.0;
    const float radius = min(length(p), 1.0);
    const float edge = smoothstep(0.34, 1.0, radius);
    const float2 normal = radius > 0.0001 ? p / radius : float2(0.0);
    const float displacement = min(0.85, edge * 0.72 * intensity);
    const float2 offset = float2(
        normal.x * 0.78 + normal.y * 0.16,
        normal.y * 0.72 - normal.x * 0.12
    ) * displacement;

    const half4 redSample = layer.sample(position + offset);
    const half4 centerSample = layer.sample(position);
    const half4 blueSample = layer.sample(position - offset);
    const half alpha = max(
        centerSample.a,
        max(redSample.a, blueSample.a)
    );
    const half3 separated = min(
        half3(redSample.r, centerSample.g, blueSample.b),
        half3(alpha)
    );
    return half4(separated, alpha);
}

/// A shallow cylindrical bend for the decorative Alignment Field only.
///
/// The field Canvas contains no text or controls and is accessibility-hidden.
/// Three coherent samples provide restrained refraction and dispersion while
/// the one-point bound keeps the 30 FPS active path predictable.
[[ stitchable ]] half4 auralignAlignmentFieldDisplacement(
    float2 position,
    SwiftUI::Layer layer,
    float4 bounds,
    float intensity
) {
    const float2 safeSize = max(bounds.zw, float2(1.0));
    const float2 uv = (position - bounds.xy) / safeSize;
    const float2 p = (uv - 0.5) * 2.0;
    const float opticalAxis = p.y + 0.12;
    const float band = 1.0 - smoothstep(0.24, 1.0, abs(opticalAxis));
    const float2 direction = normalize(
        float2(p.x * 0.16, opticalAxis + 0.0001)
    );
    const float displacement = min(
        0.75,
        band * (0.30 + abs(opticalAxis) * 0.24) * intensity
    );
    const float2 offset = direction * displacement;

    const half4 redSample = layer.sample(position + offset);
    const half4 centerSample = layer.sample(position);
    const half4 blueSample = layer.sample(position - offset);
    const half alpha = max(
        centerSample.a,
        max(redSample.a, blueSample.a)
    );
    const half3 separated = min(
        half3(redSample.r, centerSample.g, blueSample.b),
        half3(alpha)
    );
    return half4(separated, alpha);
}

/// A small, analytic 2.5D lens material for the Offline Core focal object.
///
/// The refracted connector plane supplies the visible body of the glass. This
/// layer adds only neutral surface-light cues—Fresnel, specular, and a narrow
/// rim—so a readiness/status color never becomes an opaque lens fill.
[[ stitchable ]] half4 auralignCoreLensMaterial(
    float2 position,
    float4 bounds,
    half4 accent,
    float intensity,
    float lightAppearance,
    float2 highlightCenter
) {
    const float2 size = bounds.zw;
    const float2 safeSize = max(size, float2(1.0));
    const float2 uv = (position - bounds.xy) / safeSize;
    const float2 p = (uv - 0.5) * 2.0;
    const float radiusSquared = dot(p, p);
    const float radius = sqrt(max(radiusSquared, 0.0));
    const float inside = 1.0 - smoothstep(0.965, 1.0, radius);
    const float z = sqrt(max(0.0, 1.0 - min(radiusSquared, 1.0)));
    const float fresnelBase = 1.0 - z;
    const float fresnelSquared = fresnelBase * fresnelBase;
    const float fresnel = fresnelSquared * fresnelSquared;

    const float2 highlightDelta =
        (p - highlightCenter) / float2(0.56, 0.38);
    const float specular =
        exp(-dot(highlightDelta, highlightDelta) * 2.7)
        * smoothstep(0.08, 0.52, z);
    const float rim =
        smoothstep(0.78, 0.965, radius)
        * (1.0 - smoothstep(0.965, 1.0, radius));
    const float directionalLight =
        clamp(0.64 - p.y * 0.22 + p.x * 0.08, 0.36, 0.92);

    const float appearanceScale = mix(1.0, 0.74, lightAppearance);
    const float fresnelContribution =
        fresnel * directionalLight * 0.13;
    const float specularContribution = specular * 0.24;
    const float rimContribution = rim * directionalLight * 0.15;
    const float contribution = min(
        0.34,
        (
            fresnelContribution
            + specularContribution
            + rimContribution
        ) * intensity * appearanceScale
    );
    const half3 fresnelColor = half3(0.90h, 0.97h, 1.00h);
    const half3 specularColor = half3(1.00h, 0.99h, 0.98h);
    const half3 rimColor = half3(0.95h, 0.96h, 1.00h);
    const float safeContribution = max(
        fresnelContribution + specularContribution + rimContribution,
        0.0001
    );
    const half3 straightColor = (
        fresnelColor * half(fresnelContribution)
        + specularColor * half(specularContribution)
        + rimColor * half(rimContribution)
    ) / half(safeContribution);
    const half alpha = half(inside * contribution);

    // The accent belongs to the independent foreground glyph and rim stroke.
    // Keeping it out of this surface prevents status red/green from becoming
    // an opaque glass-body tint while retaining the stable shader signature.
    const half preserveSignature = accent.a * 0.0h;
    return half4(
        min(straightColor * alpha, half3(alpha))
            + half3(preserveSignature),
        alpha
    );
}

/// A truthful, bounded three-sample chromatic displacement for the decorative
/// lens material. The maximum possible displacement is one point, matching
/// the SwiftUI `maxSampleOffset` declared at the call site.
[[ stitchable ]] half4 auralignCoreLensDisplacement(
    float2 position,
    SwiftUI::Layer layer,
    float4 bounds,
    float intensity
) {
    const float2 size = bounds.zw;
    const float2 safeSize = max(size, float2(1.0));
    const float2 p =
        ((position - bounds.xy) / safeSize - 0.5) * 2.0;
    const float radius = min(length(p), 1.0);
    const float edge = smoothstep(0.48, 0.98, radius);
    const float2 normal = radius > 0.0001 ? p / radius : float2(0.0);
    const float displacement = min(1.0, edge * 0.82 * intensity);
    const float2 offset = normal * displacement;

    const half4 redSample = layer.sample(position + offset);
    const half4 centerSample = layer.sample(position);
    const half4 blueSample = layer.sample(position - offset);
    const half alpha = max(
        centerSample.a,
        max(redSample.a, blueSample.a)
    );
    const half3 separated = min(
        half3(redSample.r, centerSample.g, blueSample.b),
        half3(alpha)
    );
    return half4(separated, alpha);
}

/// Localized backdrop participation for the Offline Core connector plane.
///
/// The source is one padded decorative connector layer whose undistorted center
/// has been removed from the surrounding Canvas. A plano-convex dome inverse
/// map contracts source coordinates toward the optical axis, reaching roughly
/// 1.43x center magnification. One sharp RGB triplet and one nine-position soft
/// field are weighted independently: the center retains subtle optical
/// softness while geometry becomes crisp at the rim. Eleven deterministic
/// reads fit inside a six-point mathematical envelope (the call site may
/// conservatively declare more).
[[ stitchable ]] half4 auralignCoreConnectorRefraction(
    float2 position,
    SwiftUI::Layer layer,
    float4 bounds,
    float intensity
) {
    const float2 safeSize = max(bounds.zw, float2(1.0));
    const float2 center = bounds.xy + safeSize * 0.5;
    const float lensRadius = 36.0;
    const float2 delta = position - center;
    const float2 p = delta / lensRadius;
    const float radiusSquared = dot(p, p);
    const float radius = min(sqrt(max(radiusSquared, 0.0)), 1.0);
    const float distanceInside = max(1.0 - radius, 0.0);
    const float domeDepth =
        distanceInside
        * distanceInside
        * (3.0 - 2.0 * distanceInside);
    const float refractionStrength = clamp(
        0.30 * intensity,
        0.0,
        0.30
    );
    const float2 sourcePosition =
        center + delta * (1.0 - refractionStrength * domeDepth);
    const float2 normal =
        radius > 0.0001 ? p / radius : float2(0.0);
    const float edge = smoothstep(0.62, 0.98, radius);

    // A compact isotropic kernel is the single-pass equivalent of a separate
    // soft backdrop. Its radius collapses toward the rim so refracted dashes
    // remain crisp where the dome turns steeply toward the viewer.
    const float scatter =
        mix(2.75, 0.45, edge) * intensity;
    const float diagonal = scatter * 0.70710678;
    const float2 axisX = float2(scatter, 0.0);
    const float2 axisY = float2(0.0, scatter);
    const float2 diagonalA = float2(diagonal, diagonal);
    const float2 diagonalB = float2(diagonal, -diagonal);

    const half4 centerSample = layer.sample(sourcePosition);
    const half4 axialSamples =
        layer.sample(sourcePosition + axisX)
        + layer.sample(sourcePosition - axisX)
        + layer.sample(sourcePosition + axisY)
        + layer.sample(sourcePosition - axisY);
    const half4 diagonalSamples =
        layer.sample(sourcePosition + diagonalA)
        + layer.sample(sourcePosition - diagonalA)
        + layer.sample(sourcePosition + diagonalB)
        + layer.sample(sourcePosition - diagonalB);
    const half4 softSample =
        centerSample * 0.20h
        + axialSamples * 0.12h
        + diagonalSamples * 0.08h;

    const float dispersionDistance =
        mix(0.08, 0.70, edge) * intensity;
    const float2 dispersion = normal * dispersionDistance;
    const half4 redSample =
        layer.sample(sourcePosition + dispersion);
    const half4 blueSample =
        layer.sample(sourcePosition - dispersion);
    const half sharpAlpha = max(
        centerSample.a,
        max(redSample.a, blueSample.a)
    );
    const half3 sharpRGB = min(
        half3(redSample.r, centerSample.g, blueSample.b),
        half3(sharpAlpha)
    );
    const half4 sharpSample = half4(sharpRGB, sharpAlpha);

    // Most center detail comes from the soft field; the transition reverses
    // toward the boundary so the lens reads as clear glass rather than glow.
    const half softMix = half(
        mix(0.76, 0.10, edge) * intensity
    );
    const half4 refracted = mix(
        sharpSample,
        softSample,
        softMix
    );
    return half4(
        min(refracted.rgb, half3(refracted.a)),
        refracted.a
    );
}
