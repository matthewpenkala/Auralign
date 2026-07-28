# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Repository-level contracts for the native macOS control surface."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "macos"
APP = MACOS / "Auralign"
PROJECT = MACOS / "Auralign.xcodeproj" / "project.pbxproj"
APP_ICON = APP / "Assets.xcassets" / "AppIcon.appiconset"
ICON_COMPOSER_DOCUMENT = APP / "audio-waveform-icon.icon"
ASSET_PROVENANCE = MACOS / "ASSET_PROVENANCE.md"
CAPTURE_MATRIX = ROOT / "scripts" / "capture_ui_matrix.sh"

ICON_PACKAGE_HASHES = {
    "Assets/01-structural-frame.svg": (
        "474b1a6dc3be5d547826267f7dca8d134552e67f71a1631c174083950eb374dc"
    ),
    "Assets/02-inner-panel-and-axis.svg": (
        "676e7349fa826f3142b03d9190e43850d951833672668ab4264dc34ab550b95d"
    ),
    "Assets/03-waveform-base.svg": (
        "c322281b1e303cf68109d4128920b97acdac506e4831dadc6b13cfc72dfa168b"
    ),
    "Assets/04-waveform-detail.svg": (
        "49f176abb8663e62cdbcbdec85b434f62771d2ad7e0f4c0db224adc0952524f7"
    ),
    "Assets/05-inactive-rails.svg": (
        "dd16d4d3f46953b00dffe454b3eb4584a047e69f099f15e2d6db957949aa9549"
    ),
    "Assets/06-selected-rail.svg": (
        "2210f306afa73f7ff8feafd9c8e9d1b3d8f6419b6cef4ec4bb29e20d5654a25d"
    ),
    "Assets/07-inactive-controls.svg": (
        "4f593bbbed3db64ee1bcf0b32bb34600c2ae41391329e7e7adb41f6359892673"
    ),
    "Assets/08-selected-controls.svg": (
        "416a5b107f056c5de202775afe6470fecc60990a636f20d06d96082319a141d4"
    ),
    "icon.json": "e3e1589694b7f168fd04074b09b4d9a06620e4ccc34bd68e17ece5f64ddea4a3",
}
ICON_PACKAGE_SNAPSHOT = (
    "69b2088ccbe96a0c88fae867c775c6957f2ffe897b513667b44d216a0f292862"
)
FALLBACK_HASHES = {
    "Contents.json": (
        "13b1c7461192863c9f3ab3ceab313acd6f6531898360983495bf13d48dfc967f"
    ),
    "icon_16x16.png": (
        "e48b4d8566ba5cf441667051fa8ceb902ab7eac76db6f07fa90daa38b7ce95be"
    ),
    "icon_16x16@2x.png": (
        "2e641facf28743785c7adb5d901c5956b37a52e62e936bd77bec797e25031df1"
    ),
    "icon_32x32.png": (
        "2e641facf28743785c7adb5d901c5956b37a52e62e936bd77bec797e25031df1"
    ),
    "icon_32x32@2x.png": (
        "f2cd0f5f25fc246e6aeb118368eee7570117cb8e70b543e69e84b25a15fef372"
    ),
    "icon_128x128.png": (
        "547c49c67aa4c98afc1f45a62f473a20e2f5d3460f7d5b6f182d3e30fad153ff"
    ),
    "icon_128x128@2x.png": (
        "3f5ac5475098bd134754520db487656b8a35bf49bf83d009cecfdb71ff39fc9d"
    ),
    "icon_256x256.png": (
        "3f5ac5475098bd134754520db487656b8a35bf49bf83d009cecfdb71ff39fc9d"
    ),
    "icon_256x256@2x.png": (
        "1f4162a7e08b835b82a6ccc9f684aa634f8b8182c36548619559d3747dcf8473"
    ),
    "icon_512x512.png": (
        "1f4162a7e08b835b82a6ccc9f684aa634f8b8182c36548619559d3747dcf8473"
    ),
    "icon_512x512@2x.png": (
        "2c1b5ed43a0ae83cb43582bbe34cef1071e44cf257ef5b41edb0480a21dc1e4a"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_visual_capture_matrix_freezes_presentation_and_receipt_state() -> None:
    script = CAPTURE_MATRIX.read_text(encoding="utf-8")
    app = (APP / "App" / "AuralignApp.swift").read_text(encoding="utf-8")

    assert script.count('\ncapture "') == 44
    assert "AURALIGN_SKIP_CAPTURE_BUILD" in script
    assert '"AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY=${reduce_transparency}"' in script
    assert '"AURALIGN_PRESENTATION_INCREASE_CONTRAST=${increase_contrast}"' in script
    assert '"AURALIGN_PRESENTATION_REDUCE_MOTION=${reduce_motion}"' in script
    assert (
        '"AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR='
        '${differentiate_without_color}"'
    ) in script
    assert "application_arguments=(" in script
    assert "-NSDoubleLocalizedStrings YES" in script
    assert "if (( extra_environment_count > 0 )); then" in script
    assert (
        'capture "70-settings-valid-dark" settings-valid newJob dark 900 600'
        in script
    )
    assert (
        'capture "73-model-setup-required-dark" model-unavailable-gated '
        "newJob dark 900 680"
        in script
    )
    assert (
        'capture "71-settings-locked-dark" settings-locked newJob dark 900 600'
        in script
    )
    assert '"$binary" "${application_arguments[@]}"' in script
    assert "expected 44 matrix PNGs" in script
    assert "stale executable receipt" in script
    assert "stale code-identity components" in script
    assert "stale code-identity receipt" in script
    assert 'binary.with_name(f"{binary.name}.debug.dylib")' in script
    assert "captured a non-active window state" in script
    assert "double-strings capture did not pass NSDoubleLocalizedStrings" in script
    assert "DebugAuxiliaryWindowAutoOpener" in app
    assert "private var modelSetupContent: some View" in app
    assert app.count("PresentationFixtureBanner(fixture: fixture)") >= 2

    subprocess.run(
        ["bash", "-n", str(CAPTURE_MATRIX)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _has_quarantine(path: Path) -> bool:
    if sys.platform != "darwin":
        return False
    return (
        subprocess.run(
            ["/usr/bin/xattr", "-p", "com.apple.quarantine", path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _png_metadata(path: Path) -> tuple[int, int, int, int]:
    """Return width, height, bit depth, and color type from a valid PNG IHDR."""

    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a complete PNG")
    chunk_length = struct.unpack(">I", data[8:12])[0]
    chunk_type = data[12:16]
    if chunk_length != 13 or chunk_type != b"IHDR":
        raise AssertionError(f"{path} does not begin with a canonical PNG IHDR")
    chunk_data = data[16:29]
    expected_crc = struct.unpack(">I", data[29:33])[0]
    actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise AssertionError(f"{path} has an invalid PNG IHDR checksum")
    width, height, bit_depth, color_type, compression, filtering, interlace = (
        struct.unpack(">IIBBBBB", chunk_data)
    )
    if (compression, filtering, interlace) != (0, 0, 0):
        raise AssertionError(f"{path} uses an unexpected PNG encoding")
    return width, height, bit_depth, color_type


def test_release_build_is_arm64_hardened_path_free_and_fixture_free() -> None:
    project = PROJECT.read_text(encoding="utf-8")
    info = (APP / "Info.plist").read_text(encoding="utf-8")

    assert project.count("ARCHS = arm64;") == 2
    assert 'AURALIGN_ENGINE_ROOT = "";' in project
    assert "ENABLE_HARDENED_RUNTIME = YES;" in project
    assert (
        project.count(
            'ASSETCATALOG_COMPILER_APPICON_NAME = "audio-waveform-icon";'
        )
        == 2
    )
    assert project.count("ASSETCATALOG_COMPILER_STANDALONE_ICON_BEHAVIOR = all;") == 2
    assert project.count("ENABLE_PREVIEWS = YES;") == 1
    assert project.count("ENABLE_PREVIEWS = NO;") == 1
    assert project.count("CODE_SIGN_INJECT_BASE_ENTITLEMENTS = NO;") == 1
    assert "<key>LSMultipleInstancesProhibited</key>" in info
    assert "/Users/" not in project
    assert "/Users/" not in info
    assert "ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;" not in project
    assert not list(APP_ICON.glob("AppIcon-*.png"))


def test_passive_global_status_is_not_rendered_as_a_toolbar_control() -> None:
    main_view = (APP / "Views" / "MainView.swift").read_text(encoding="utf-8")

    assert "ToolbarItem(placement: .status)" not in main_view
    assert "GlobalStatusFooter(model: model)" in main_view
    assert "ToolbarItem(placement: .primaryAction)" in main_view
    assert "AccessibilityIdentifier.primaryActionStatus" in main_view


def test_sidebar_brand_uses_a_simplified_auralign_app_icon_adaptation() -> None:
    main_view = (APP / "Views" / "MainView.swift").read_text(encoding="utf-8")
    brand_mark = (
        APP / "Views" / "AuralignBrandMark.swift"
    ).read_text(encoding="utf-8")
    brand = main_view.split("private var brand: some View {", maxsplit=1)[1].split(
        "private var sidebarBackground:", maxsplit=1
    )[0]

    assert "AuralignSidebarBrandMark()" in brand
    assert 'systemName: "point.3.connected.trianglepath.dotted"' not in brand
    assert 'Text(verbatim: "AURALIGN")' in brand
    assert 'Text(verbatim: "NEURAL AUDIO\\nINTELLIGENCE")' in brand
    assert 'accessibilityLabel("Auralign, neural audio intelligence")' in brand
    assert "AuralignType.brandWordmark" in brand
    assert ".tracking(1.1)" in brand
    assert "VStack(alignment: .leading, spacing: 2)" in brand
    assert "LOCAL SIGNAL STUDIO" not in brand
    assert "OFFLINE TRANSCRIPTION" not in brand
    assert "struct AuralignSidebarBrandMark: View" in brand_mark
    assert "let amplitudes: [CGFloat]" in brand_mark
    assert "drawRails" not in brand_mark
    assert "[0.28, 0.50, 0.72]" not in brand_mark
    assert "AuralignSpectrumStop.source.color" in brand_mark
    assert ".accessibilityHidden(true)" in brand_mark
    assert "NSApplication.shared.applicationIconImage" not in brand_mark


def test_workspace_heroes_are_content_proportional() -> None:
    components = (APP / "Views" / "AuralignComponents.swift").read_text(
        encoding="utf-8"
    )
    hero = components.split(
        "struct AuralignWorkspaceHeader<Context: View, Accessory: View>: View {",
        maxsplit=1,
    )[1].split("// MARK: - Causality banner", maxsplit=1)[0]

    assert ".frame(minHeight:" not in hero
    assert ".padding(.vertical, AuralignVisualSystem.spacingL)" in hero
    assert ".frame(maxWidth: .infinity, alignment: .leading)" in hero
    assert "AuralignHeroMetalOptics(intensity: 0.86)" in hero
    assert "HStack(alignment: .center" in hero
    assert "VStack(alignment: .leading, spacing: AuralignVisualSystem.spacingM)" in hero


def test_large_structural_surfaces_share_a_restrained_optical_finish() -> None:
    chrome = (APP / "Views" / "AuralignStudioChrome.swift").read_text(
        encoding="utf-8"
    )
    components = (APP / "Views" / "AuralignComponents.swift").read_text(
        encoding="utf-8"
    )
    visual_system = (
        APP / "Views" / "AuralignVisualSystem.swift"
    ).read_text(encoding="utf-8")
    optical = chrome.split(
        "struct AuralignOpticalSurface: View {", maxsplit=1
    )[1].split(
        "struct AuralignStudioBackdrop: View {", maxsplit=1
    )[0]
    panel = chrome.split(
        "struct AuralignStudioPanel<Content: View>: View {", maxsplit=1
    )[1].split(
        "struct AuralignPrimaryActionButtonStyle:", maxsplit=1
    )[0]
    hero = components.split(
        "struct AuralignWorkspaceHeader<Context: View, Accessory: View>: View {",
        maxsplit=1,
    )[1].split("// MARK: - Causality banner", maxsplit=1)[0]

    assert "specularWash" in optical
    assert "spectralRim" in optical
    assert "topEdgeMask" in optical
    assert "bottomEdgeMask" in optical
    assert "auralignReduceTransparency" in optical
    assert "auralignIncreaseContrast" in optical
    assert "auralignDifferentiateWithoutColor" in optical
    assert "controlActiveState == .inactive" in optical
    assert ".allowsHitTesting(false)" in optical
    assert ".accessibilityHidden(true)" in optical
    assert "glassEffect" not in optical
    assert "Metal" not in optical
    assert "AuralignOpticalSurface(" in panel
    assert "AuralignOpticalSurface(" in hero
    assert components.count("AuralignOpticalSurface(") >= 2
    assert "struct AuralignStatusBanner: View" in visual_system
    assert "AuralignOpticalSurface(" in visual_system


def test_offline_core_lens_has_bounded_accessible_material_fallbacks() -> None:
    readiness = (APP / "Views" / "ReadinessView.swift").read_text(
        encoding="utf-8"
    )
    core = readiness.split(
        "private struct AuralignCoreLens: View {", maxsplit=1
    )[1].split(
        "private struct ReadinessFacet:", maxsplit=1
    )[0]

    assert "AuralignCoreLens(" in readiness
    assert "RadialGradient(" in core
    assert "AuralignSpectrumStop.source.color" in core
    assert "AuralignSpectrumStop.delivery.color" in core
    assert "auralignReduceTransparency" in core
    assert "auralignIncreaseContrast" in core
    assert "auralignDifferentiateWithoutColor" in core
    assert "controlActiveState == .inactive" in core
    assert "AuralignCoreLensMetalMaterial(" in core
    assert "AuralignCoreConnectorRefraction(colors: connectorColors)" in core
    assert "AuralignCoreRefractedRay" in readiness
    assert "path.addQuadCurve(" in readiness
    assert ".clipShape(Circle())" in core
    assert "isActiveOperation: isActiveOperation" in core
    assert ".allowsHitTesting(false)" in core
    assert ".accessibilityHidden(true)" in core
    assert "TimelineView" not in core


def test_gpu_optics_are_original_bounded_decorative_and_receipted() -> None:
    views = APP / "Views"
    shader = (APP / "Shaders" / "AuralignOptics.metal").read_text(
        encoding="utf-8"
    )
    optics = (views / "AuralignMetalOptics.swift").read_text(encoding="utf-8")
    chrome = (views / "AuralignStudioChrome.swift").read_text(encoding="utf-8")
    components = (views / "AuralignComponents.swift").read_text(
        encoding="utf-8"
    )
    readiness = (views / "ReadinessView.swift").read_text(encoding="utf-8")
    alignment_field = (views / "AlignmentField.swift").read_text(
        encoding="utf-8"
    )
    results = (views / "ResultsView.swift").read_text(encoding="utf-8")
    processing = (views / "ProcessingView.swift").read_text(encoding="utf-8")
    snapshot = (APP / "Support" / "SnapshotDriver.swift").read_text(
        encoding="utf-8"
    )
    matrix = (ROOT / "scripts" / "capture_ui_matrix.sh").read_text(
        encoding="utf-8"
    )

    assert shader.count("[[ stitchable ]]") == 5
    assert "auralignHeroDiffusion" in shader
    assert "auralignHeroDiffusionDisplacement" in shader
    assert "auralignAlignmentFieldDisplacement" in shader
    assert "auralignCoreLensMaterial" in shader
    assert "auralignCoreLensDisplacement" in shader
    assert shader.count("layer.sample(") == 9
    assert "min(1.0, edge * 0.82 * intensity)" in shader
    assert "for (" not in shader
    assert "while (" not in shader
    assert "float time" not in shader
    assert "elapsed" not in shader.lower()
    assert "texture2d" not in shader
    assert shader.count("float4 bounds") == 5
    assert shader.count("half4 source") == 0
    assert "fast::sin" not in shader
    assert "fast::cos" not in shader
    assert "pow(" not in shader
    assert "return half4(spectral * alpha, alpha)" in shader
    assert "return half4(color * alpha, alpha)" in shader

    assert "enum AuralignOpticalRenderingPolicy" in optics
    assert "enum AuralignOpticalIntensity" in optics
    assert "static let subtleLift: Double = 1.06" in optics
    assert "min(max(intensity * subtleLift, 0), 1)" in optics
    assert "enum AuralignOpticalMotionPolicy" in optics
    assert "maximumRefreshRate: Double = 15" in optics
    assert "loopPeriod: TimeInterval = 8" in optics
    assert "ShaderLibrary.auralignHeroDiffusion" in optics
    assert "ShaderLibrary.auralignHeroDiffusionDisplacement" in optics
    assert "ShaderLibrary.auralignCoreLensMaterial" in optics
    assert "ShaderLibrary.auralignCoreLensDisplacement" in optics
    assert "maxSampleOffset: CGSize(width: 1, height: 1)" in optics
    assert "auralignReduceTransparency" in optics
    assert "auralignIncreaseContrast" in optics
    assert "auralignReduceMotion" in optics
    assert "auralignDifferentiateWithoutColor" in optics
    assert "controlActiveState != .inactive" in optics
    assert "isActiveOperation" in optics
    assert 'environment["AURALIGN_SNAPSHOT_ON_LAUNCH"] == nil' in optics
    assert "TimelineView(" in optics
    assert ".animation(" in optics
    assert "paused: !animationIsAllowed" in optics
    assert "if metalIsEnabled {" in optics
    assert optics.count(".boundingRect") == 4
    assert ".fill(heroShader)" in optics
    assert "shader.dithersColor = true" in optics
    assert ".colorEffect(" not in optics
    assert "GeometryReader" not in optics
    assert "highlightCenter(at: opticalPhase)" in optics
    assert ".float2(highlightCenter)" in optics
    assert optics.count(".allowsHitTesting(false)") == 2
    assert optics.count(".accessibilityHidden(true)") == 2
    assert "AuralignOpticalIntensity.lifted(intensity)" in chrome

    assert "AuralignHeroMetalOptics(intensity: 0.86)" in components
    assert "AuralignCoreLensMetalMaterial(" in readiness
    assert "ShaderLibrary.auralignAlignmentFieldDisplacement" in alignment_field
    assert "maxSampleOffset: CGSize(width: 1, height: 1)" in alignment_field
    assert "model.isConnectingEngine || model.isRunningDoctor" in readiness
    assert "AuralignHeroMetalOptics" not in results
    assert "AuralignCoreLensMetalMaterial" not in results
    assert "AuralignHeroMetalOptics" not in processing
    assert "AuralignCoreLensMetalMaterial" not in processing

    assert 'schemaVersion: "auralign.snapshot.v5"' in snapshot
    assert '"Contents/Resources/default.metallib"' in snapshot
    assert "rendererEffectTier: rendererEffectTier.rawValue" in snapshot
    assert "opticalPhase: AuralignOpticalRenderingPolicy.fixedPhase" in snapshot
    assert "auralign.snapshot.v5" in matrix
    assert '"default.metallib"' in matrix
    assert '"rendererEffectTier"' in matrix


def test_signal_setup_selected_phase_uses_low_intensity_optical_depth() -> None:
    pipeline = (
        APP / "Views" / "PipelineConfigurationView.swift"
    ).read_text(encoding="utf-8")
    navigator = pipeline.split(
        "private var phaseNavigator: some View {", maxsplit=1
    )[1].split(
        "private var sectionBinding:", maxsplit=1
    )[0]

    assert "if isSelected {" in navigator
    assert "AuralignOpticalSurface(" in navigator
    assert "accent: .alignment" in navigator
    assert "intensity: 0.46" in navigator
    assert ".buttonStyle(.plain)" in navigator
    assert ".accessibilityAddTraits(isSelected ? [.isSelected] : [])" in navigator


def test_restrained_optical_depth_covers_every_major_workspace_posture() -> None:
    views = APP / "Views"
    workspace_hero_files = (
        "NewJobView.swift",
        "ProcessingView.swift",
        "ResultsView.swift",
        "AllParametersView.swift",
        "ReadinessView.swift",
    )
    section_files = (
        "NewJobView.swift",
        "ProcessingView.swift",
        "ResultsView.swift",
        "ReadinessView.swift",
        "ModelSetupView.swift",
    )

    for name in workspace_hero_files:
        source = (views / name).read_text(encoding="utf-8")
        assert "AuralignWorkspaceHeader(" in source

    for name in section_files:
        source = (views / name).read_text(encoding="utf-8")
        assert "AuralignSection(" in source

    settings = (views / "SettingsView.swift").read_text(encoding="utf-8")
    setup = (views / "ModelSetupView.swift").read_text(encoding="utf-8")
    fatal = (views / "FatalBootstrapView.swift").read_text(encoding="utf-8")
    assert "AuralignPageHeader(" in settings
    assert "AuralignPageHeader(" in setup
    assert "AuralignStatusBanner(" in fatal
    assert "AuralignCausalityBanner(" in fatal


def test_optical_depth_is_static_deterministic_and_absent_from_reading_wells() -> None:
    views = APP / "Views"
    chrome = (views / "AuralignStudioChrome.swift").read_text(encoding="utf-8")
    readiness = (views / "ReadinessView.swift").read_text(encoding="utf-8")
    results = (views / "ResultsView.swift").read_text(encoding="utf-8")
    processing = (views / "ProcessingView.swift").read_text(encoding="utf-8")
    parameters = (views / "AllParametersView.swift").read_text(encoding="utf-8")
    config = (views / "ConfigSectionView.swift").read_text(encoding="utf-8")

    optical = chrome.split(
        "struct AuralignOpticalSurface: View {", maxsplit=1
    )[1].split(
        "struct AuralignStudioBackdrop: View {", maxsplit=1
    )[0]
    panel = chrome.split(
        "struct AuralignStudioPanel<Content: View>: View {", maxsplit=1
    )[1].split(
        "struct AuralignPrimaryActionButtonStyle:", maxsplit=1
    )[0]
    core = readiness.split(
        "private struct AuralignCoreLens: View {", maxsplit=1
    )[1].split(
        "private struct ReadinessFacet:", maxsplit=1
    )[0]
    transcript = results.split(
        "private func transcriptReadingSurface(_ text: String) -> some View {",
        maxsplit=1,
    )[1].split(
        "private func transcriptFooter(", maxsplit=1
    )[0]
    logs = processing.split(
        "private var technicalDetails: some View {", maxsplit=1
    )[1].split(
        "private func restorePartialRevealFocus()", maxsplit=1
    )[0]
    inspector = parameters.split(
        "private var parameterInspector: some View {", maxsplit=1
    )[1].split(
        "private func effectiveHeadline(", maxsplit=1
    )[0]

    forbidden_dynamic_effects = (
        "TimelineView",
        "Canvas",
        ".blur(",
        "CIFilter",
        "Shader",
        "distortionEffect",
        "layerEffect",
        "colorEffect",
        ".animation(",
        "withAnimation",
        "Date(",
        "UUID(",
        ".random(",
    )
    for source in (optical, panel, core):
        for forbidden in forbidden_dynamic_effects:
            assert forbidden not in source

    assert "AuralignWrappedPanelHighlightPath" in chrome
    assert "addQuadCurve(" in chrome
    assert "startPoint: .bottomLeading" in chrome
    assert "endPoint: .topTrailing" in chrome

    assert ".background(AuralignSurface.inset)" in transcript
    assert ".background(AuralignSurface.inset)" in logs
    assert ".background(AuralignSurface.canvas)" in inspector
    for source in (transcript, logs, inspector, config):
        assert "AuralignOpticalSurface(" not in source
        assert "AuralignCoreLens(" not in source


def test_passive_status_component_never_looks_like_an_input_capsule() -> None:
    visual_system = (
        APP / "Views" / "AuralignVisualSystem.swift"
    ).read_text(encoding="utf-8")
    status_component = visual_system.split(
        "struct AuralignStatusBadge: View {", maxsplit=1
    )[1].split(
        "struct AuralignStatusBanner: View {", maxsplit=1
    )[0]

    assert "Label(text, systemImage:" in status_component
    assert "Capsule()" not in status_component
    assert ".background(" not in status_component
    assert ".overlay" not in status_component


def test_typography_system_uses_reviewed_system_font_designs_only() -> None:
    foundations = (
        APP / "Views" / "AuralignDesignFoundations.swift"
    ).read_text(encoding="utf-8")
    info = (APP / "Info.plist").read_text(encoding="utf-8")
    swift_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(APP.rglob("*.swift"))
    )

    # Display, product, interface, instrument, and transcript voices remain
    # semantic, system-provided designs. Transcript prose intentionally uses
    # the default system face so its reading surface belongs to the app.
    # No unreviewed font file or runtime registration can silently become part
    # of the signed application.
    assert "design: .rounded" not in foundations
    assert foundations.count("design: .default") >= 10
    assert "design: .default" in foundations
    assert "design: .monospaced" in foundations
    assert "design: .serif" not in foundations
    assert (
        "static let transcript = Font.system(.body, design: .default)"
        in foundations
    )
    assert "static let technicalBody" in foundations
    assert "static func navigation(selected: Bool)" in foundations
    assert "Font.custom" not in swift_source
    assert "CTFontManager" not in swift_source
    assert "UIAppFonts" not in info
    assert not [
        path
        for path in APP.rglob("*")
        if path.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}
    ]


def test_semantic_typography_roles_reach_each_major_ui_posture() -> None:
    views = APP / "Views"
    main = (views / "MainView.swift").read_text(encoding="utf-8")
    results = (views / "ResultsView.swift").read_text(encoding="utf-8")
    readiness = (views / "ReadinessView.swift").read_text(encoding="utf-8")
    parameters = (views / "AllParametersView.swift").read_text(encoding="utf-8")
    fields = (views / "FieldRowView.swift").read_text(encoding="utf-8")
    setup = (views / "ModelSetupView.swift").read_text(encoding="utf-8")
    fatal = (views / "FatalBootstrapView.swift").read_text(encoding="utf-8")
    transcript_surface = results.split(
        "private func transcriptReadingSurface(_ text: String) -> some View {",
        maxsplit=1,
    )[1].split(
        "private func transcriptFooter(",
        maxsplit=1,
    )[0]

    assert "AuralignType.brandWordmark" in main
    assert "AuralignType.brandDescriptor" in main
    assert "AuralignType.navigation(selected:" in main
    assert "AuralignType.transcript" in transcript_surface
    assert ".lineSpacing(7)" in transcript_surface
    assert ".frame(maxWidth: 640, alignment: .leading)" in transcript_surface
    assert ".textSelection(.enabled)" in transcript_surface
    assert "AuralignType.numeric" in results
    assert "AuralignType.technical" in readiness
    assert "AuralignType.workspaceTitle" in parameters
    assert "AuralignType.technicalBody" in fields
    assert "AuralignType.numericSmall" in setup
    assert "AuralignType.technical" in fatal


def test_genuine_icon_composer_document_matches_authoritative_snapshot() -> None:
    root_metadata = ICON_COMPOSER_DOCUMENT.lstat()
    assert stat.S_ISDIR(root_metadata.st_mode)
    assert not stat.S_ISLNK(root_metadata.st_mode)

    directories: list[str] = []
    entries: list[dict[str, int | str]] = []
    for path in sorted(ICON_COMPOSER_DOCUMENT.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(ICON_COMPOSER_DOCUMENT).as_posix()
        assert not stat.S_ISLNK(metadata.st_mode), relative
        assert not _has_quarantine(path), relative
        if stat.S_ISDIR(metadata.st_mode):
            directories.append(relative)
            continue
        assert stat.S_ISREG(metadata.st_mode), relative
        if os.name != "nt":
            assert stat.S_IMODE(metadata.st_mode) == 0o644, relative
        entries.append(
            {
                "bytes": metadata.st_size,
                # Git records only executable/non-executable. Normalize the
                # reviewed non-executable source mode so the immutable package
                # digest is stable on Windows checkouts as well as POSIX hosts.
                "mode": 0o644,
                "path": relative,
                "sha256": _sha256(path),
            }
        )

    assert directories == ["Assets"]
    assert len(entries) == 9
    assert sum(int(entry["bytes"]) for entry in entries) == 29_869
    assert {str(entry["path"]): str(entry["sha256"]) for entry in entries} == (
        ICON_PACKAGE_HASHES
    )
    canonical = json.dumps(
        {"directories": directories, "files": entries},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert hashlib.sha256(canonical).hexdigest() == ICON_PACKAGE_SNAPSHOT


def test_app_icon_fallback_has_exact_hashes_slots_and_pixel_sizes() -> None:
    assert {
        path.name for path in APP_ICON.iterdir() if path.is_file()
    } == set(FALLBACK_HASHES)
    for filename, expected_hash in FALLBACK_HASHES.items():
        path = APP_ICON / filename
        metadata = path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert not stat.S_ISLNK(metadata.st_mode)
        assert not _has_quarantine(path)
        assert _sha256(path) == expected_hash

    contents = json.loads((APP_ICON / "Contents.json").read_text(encoding="utf-8"))
    declared: dict[str, int] = {}
    for item in contents["images"]:
        logical_size = int(item["size"].split("x", maxsplit=1)[0])
        scale = int(item["scale"].removesuffix("x"))
        declared[item["filename"]] = logical_size * scale

    assert declared == {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, pixels in declared.items():
        width, height, bit_depth, color_type = _png_metadata(APP_ICON / filename)
        assert (width, height) == (pixels, pixels)
        assert bit_depth == 8
        assert color_type == 2  # Truecolor RGB, with no alpha channel.


def test_icon_provenance_is_immutable_and_repository_relative() -> None:
    provenance = ASSET_PROVENANCE.read_text(encoding="utf-8")
    for value in (
        "ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48",
        "1f8db9502713133f8d2963be8a6009e9ab6b593bb81ddc3e33b9e10210a0e5d6",
        ICON_PACKAGE_SNAPSHOT,
        FALLBACK_HASHES["Contents.json"],
        "24a627e0bec4201d19f24c37422fddafda48d7cd450807ff523ffe3f9dded3af",
    ):
        assert value in provenance
    assert "/Users/" not in provenance
    assert "exports/icon-composer/audio-waveform-icon.icon" in provenance
    assert "exports/xcode/AppIcon.xcassets/AppIcon.appiconset" in provenance


def test_macos_gui_runbook_and_local_gate_are_indexed_and_safe() -> None:
    docs_index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "macos-gui.md").read_text(encoding="utf-8")
    gate = (ROOT / "scripts" / "check_macos_gui.sh").read_text(encoding="utf-8")

    assert "macos-gui.md" in docs_index
    assert "docs/macos-gui.md" in readme
    assert "doctor --startup" in runbook
    assert "pass `CODE_SIGNING_ALLOWED=NO` to a UI-test run" in runbook
    assert "/tmp/auralign-macos-derived" not in runbook
    assert "Sign to Run Locally" in runbook
    assert "Gatekeeper" in runbook

    assert "CODE_SIGNING_ALLOWED=NO" not in gate
    assert "export PYTHONDONTWRITEBYTECODE=1" in gate
    assert gate.count("uv run --python 3.11 python ") == 6
    assert gate.count("uv run --python 3.11 python -B -") == 6
    assert "Library/Developer/Xcode/DerivedData/AuralignLocalGate" in gate
    assert "GateEvidence" in gate
    assert ".AuralignLocalGate.lock" in gate
    assert "mktemp" not in gate
    assert 'rm -rf "$derived_data"' not in gate
    assert "-jobs 1" in gate
    assert "-parallel-testing-enabled NO" in gate
    assert "-resultBundlePath" in gate
    assert "signed-tests-summary.json" in gate
    assert "signed-tests-tree.json" in gate
    assert "minimumTestCount" in gate
    assert "runtimeWarnings" in gate
    assert "unexpectedRuntimeWarnings" in gate
    assert "User-interactive quality-of-service" in gate
    assert "build-warnings.json" in gate
    assert "ps -axo pid=,args=" in gate
    assert "source-state-before.json" in gate
    assert "source-state-after.json" in gate
    assert "repository source state changed" in gate
    assert "LAST_ATTEMPT" in gate
    assert "LATEST_PASS" in gate
    assert "after-tests" in gate
    assert "after-analyze" in gate
    assert "after-release-build" in gate
    assert "postflight" in gate
    assert "AuralignEngineRoot" in gate
    assert "codesign --verify --deep --strict" in gate
    assert "com.apple.security.get-task-allow" in gate
    assert "com.apple.security.cs.disable-library-validation" in gate
    assert "com.apple.security.cs.allow-unsigned-executable-memory" in gate
    assert "com.apple.security.cs.dynamic-codesigning" in gate
    assert "com.apple.security.application-groups" in gate
    assert "exact empty entitlement dictionary" in gate
    assert "com.auralign.studio" in gate
    assert "LSMinimumSystemVersion" in gate
    assert "CFBundlePackageType" in gate
    assert "CFBundleShortVersionString" in gate
    assert "CFBundleVersion" in gate
    assert "CFBundleIconName" in gate
    assert "CFBundleIconFile" in gate
    assert "audio-waveform-icon.icns" in gate
    assert "assetutil" in gate
    assert "iconutil" in gate
    assert "config_manifest.json" in gate
    assert "FieldCatalog.json" in gate
    assert "release-file-allowlist.diff" in gate
    assert "find -P" in gate
    assert "com.apple.quarantine" in gate
    assert "otool -L" in gate
    assert "XCRemoteSwiftPackageReference" in gate
    assert "single-instance-contender" in gate
    assert "AURALIGN_ENABLE_UI_TEST_FIXTURES" in gate
    assert "AURALIGN_PROTOTYPE_DIRECTION" in gate
    assert "AURALIGN_ENABLE_DEBUG_AUTOMATION" in gate
    assert "AURALIGN_SNAPSHOT_DIR" in gate
    assert "AURALIGN_SNAPSHOT_NONCE" in gate
    assert "AURALIGN_SNAPSHOT_WINDOW_ROLE" in gate
    assert "AURALIGN_SNAPSHOT_WINDOW_ACTIVITY" in gate
    assert "AURALIGN_SNAPSHOT_SIDEBAR" in gate
    assert "AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY" in gate
    assert "AURALIGN_PRESENTATION_INCREASE_CONTRAST" in gate
    assert "AURALIGN_PRESENTATION_REDUCE_MOTION" in gate
    assert "AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR" in gate
    assert "com.auralign.studio.debug.command" in gate
    assert "com.auralign.studio.synthetic-fixture." in gate
    assert "selected-run-plan" in gate
    assert "invalid-hidden-advanced" in gate
    assert "settings-locked" in gate
    assert "/AURALIGN_SYNTHETIC/" in gate
    assert "Auralign.debug.dylib" in gate
    assert "__preview.dylib" in gate

    project = PROJECT.read_text(encoding="utf-8")
    for marker in (
        "XCRemoteSwiftPackageReference",
        "XCLocalSwiftPackageReference",
        "packageReferences",
    ):
        assert marker not in project
    assert "DEPLOYMENT_POSTPROCESSING = YES;" in project
    assert "STRIP_INSTALLED_PRODUCT = YES;" in project
    assert "STRIP_STYLE = all;" in project
