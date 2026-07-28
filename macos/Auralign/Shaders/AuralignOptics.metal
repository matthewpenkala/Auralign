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
/// This is representative optical depth, not literal backdrop refraction. It
/// uses a circular normal, a bounded Fresnel-like rim, and a restrained
/// cyan/violet split without sampling neighboring pixels.
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
    const float inside = 1.0 - smoothstep(0.94, 1.0, radiusSquared);
    const float z = sqrt(max(0.0, 1.0 - min(radiusSquared, 1.0)));
    const float fresnelBase = 1.0 - z;
    const float fresnel = fresnelBase * fresnelBase;

    const float2 highlightDelta = p - highlightCenter;
    const float highlight =
        exp(-dot(highlightDelta, highlightDelta) * 6.4) * (1.0 - fresnel);
    const float lowerOcclusion = smoothstep(0.12, 0.92, p.y) * (1.0 - z);

    const half3 cyan = half3(0.20h, 0.80h, 0.98h);
    const half3 violet = half3(0.72h, 0.36h, 0.96h);
    const half directionalSplit = half(
        clamp((p.x - p.y) * 0.5 + 0.5, 0.0, 1.0)
    );
    const half3 chroma = mix(cyan, violet, directionalSplit);
    const half3 base = mix(
        accent.rgb * half(0.34 + lightAppearance * 0.18),
        half3(0.94h),
        half(highlight * (0.28 + lightAppearance * 0.16))
    );
    const half3 color =
        base
        + chroma * half(fresnel * 0.22 * intensity)
        - half3(lowerOcclusion * 0.075 * intensity);
    const half alpha = half(
        inside * intensity * mix(0.82, 0.66, lightAppearance)
    );

    return half4(color * alpha, alpha);
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
