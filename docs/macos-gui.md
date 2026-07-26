<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Native macOS GUI

The SwiftUI application under `macos/` is a local control surface for the
authoritative Python engine. It does not reimplement transcription. Debug
builds use the repository runtime; the owner-authorized private standalone PKG
embeds the exact reviewed Python engine and native dependency closure. The
signed app also owns the native, nonmodal model-pack setup surface. This is a
Developer ID distribution outside the Mac App Store.

## Trust Boundary

The app launches one short-lived
`python -m local_transcript_max.gui_bridge` subprocess for configuration,
readiness, and inspection commands, and one streaming subprocess for a
transcription job. The bridge uses versioned JSON Lines on standard output;
backend logging is isolated on standard error. `PipelineConfig`, the model
manifest, doctor/startup probes, and pipeline events remain the source of
truth.

The GUI reports **Ready** only after all of the following succeed for the
current effective configuration:

- a compatible bridge handshake;
- bundled/live schema agreement;
- Python-authoritative configuration validation;
- the strict doctor checks; and
- real selected-backend startup probes.

Changing the engine checkout or effective configuration invalidates that
evidence. The Start action fails closed until the new state has been checked.

## Requirements By Lane

Both lanes require macOS 14 or later on Apple Silicon. Source development uses
an explicitly prepared local cache and additionally requires Xcode, the
repository's Python 3.11 `.venv`, and reviewed FFmpeg/FFprobe. The 0.3.0
standalone product embeds Python, the Auralign engine, CPU/MLX dependency
closure, FFmpeg, and FFprobe, and installs
`com.auralign.studio.models.bootstrap.v1` version `1.0.0` below system
Application Support. That component contains Community-1, English alignment,
ECAPA, and deterministic corpus-free Punkt, but no ASR weights.

The app's **Complete Offline Setup** surface installs one of sixteen exact
public ASR packs below the current user's Application Support. It uses
revision-bound URLs sealed in the signed catalog, `URLSession`, private
staging, exact size/SHA-256/license/notice/provenance/CycloneDX verification,
and atomic publication. The flow is keyboard and VoiceOver accessible, never
runs in Installer, and requires no token. The recipient needs one connected
setup pass unless the exact selected pack is already installed or imported;
processing is strictly offline afterward. They never need Xcode, Command Line
Tools, Python, Homebrew, Git, `uv`, a checkout, virtual environment, compiler,
package manager, or shell-profile setup.

Follow [Models](models.md) for the distinct source-cache, bootstrap-component,
and native-pack verification contracts.

## Development Build

Open `macos/Auralign.xcodeproj`, select the shared `Auralign` scheme, and run
the Debug configuration. Debug embeds the source checkout that built it as a
development convenience. Settings can point the app at another checkout that
contains both `src/local_transcript_max` and an executable `.venv/bin/python`.

From the repository root:

```zsh
derived="$HOME/Library/Developer/Xcode/DerivedData/AuralignLocalGate"

xcodebuild \
  -project macos/Auralign.xcodeproj \
  -scheme Auralign \
  -configuration Debug \
  -destination 'platform=macOS' \
  -derivedDataPath "$derived" \
  -jobs 1 \
  -parallel-testing-enabled NO \
  test
```

Keep Xcode build, test, analysis, launch, and capture operations serialized when
they share that DerivedData directory. UI-test runners must be normally signed
with Xcode's **Sign to Run Locally** identity. Do not put a UI-test runner under
`/tmp`, pass `CODE_SIGNING_ALLOWED=NO` to a UI-test run, use `sudo`, clear
quarantine attributes, or weaken Gatekeeper to work around a launch failure.
Instead stop stale Auralign/test-runner processes, inspect the existing
signature and result bundle, and rebuild in the canonical directory.

Run the complete local macOS gate with:

```bash
bash scripts/check_macos_gui.sh
```

The gate takes an interprocess lock, refuses to overlap another Xcode/app/test
process, preserves its logs and result bundle, runs signed Swift tests and
static analysis serially, validates both icon source paths, and builds an arm64
Release app. It then inspects the Release signature, entitlements, icon
catalog, resources, dynamic dependencies, Debug-only markers, quarantine,
symlinks, and machine-local/private-path leakage.

## Release-Build Boundary

The standalone builder compiles an arm64 Xcode Release app and embeds the
transparent Python 3.11 engine as the nested
`Contents/Frameworks/AuralignEngineRuntime.bundle/Contents/Resources/Runtime`
bundle. The app resolves that runtime relative to its own bundle and launches
Python with `-B -I` plus a sanitized, strictly offline environment. Mutable
state lives only in the user's Application Support, Caches, Logs, and temporary
directories. Release model resolution is locked to the exact root-owned
bootstrap component plus the selected versioned per-user ASR pack. Arbitrary
directories, floating repository IDs, environment overrides, and partial
staging cannot override installed policy.

The app remains unsandboxed because it launches its embedded subprocess and
works with operator-selected media, model caches, and output directories. Its
Release configuration makes `offline` and model locations managed values so
imports or stale overrides cannot bypass the installed policy.

An ordinary Xcode Release build is still only a local artifact. Distribution
requires the checked-in standalone builder, clean exact-commit source, complete
license/SBOM/native closure, inside-out Developer ID signing, a scriptless
Developer ID Installer PKG, Apple notarization, stapling, final package/runtime
smoke tests, and private-release hash parity. See
[the private PKG runbook](release/macos-v0.3.0-private-pkg.md).

## Job Ownership And Privacy

- A running job is owned by the app. Cancel requests graceful termination,
  followed by bounded force termination when necessary.
- Quitting while a job is active invokes the same ownership path; the app must
  not silently orphan Python or native backend processes.
- Auralign is single-instance per signed-in user. LaunchServices blocks ordinary
  duplicate launches, and a per-user process lock also covers direct executable
  and debugger launches that bypass LaunchServices.
- Readiness and diagnostic copy surfaces use privacy-safe summaries. Treat
  paths, stderr, job directories, model caches, and transcript previews as
  sensitive even though the app is local.
- Transcript previews are bounded. Canonical artifacts remain in the private
  job directory and are never uploaded.

## Debug Automation

Debug builds include `SnapshotDriver`, an app-internal visual-verification hook
used when macOS screen-capture automation is unavailable. It remains inert
unless the process is launched with `AURALIGN_ENABLE_DEBUG_AUTOMATION=1` and an
existing output directory in `AURALIGN_SNAPSHOT_DIR`; every written snapshot or
view-tree file is confined to that directory. The hook is compiled out of
Release builds. Use only non-sensitive synthetic media and temporary output
paths when generating review snapshots.

An on-launch capture requires a fresh UUID in `AURALIGN_SNAPSHOT_NONCE` and may
set:

- `AURALIGN_SNAPSHOT_SECTION` to `newJob`, `allParameters`, `readiness`,
  `processing`, `results`, or an exact manifest section identifier;
- `AURALIGN_SNAPSHOT_APPEARANCE` to `light` or `dark`;
- `AURALIGN_SNAPSHOT_WINDOW_SIZE` to a finite positive `width,height`;
- `AURALIGN_SNAPSHOT_WINDOW_ROLE` to `main` or `settings`;
- `AURALIGN_SNAPSHOT_WINDOW_ACTIVITY` to `active` or `inactive`;
- `AURALIGN_SNAPSHOT_SIDEBAR` to `expanded` or `collapsed`; and
- `AURALIGN_SNAPSHOT_EXIT_AFTER_CAPTURE=1` to terminate only after a
  successful capture.

The four `AURALIGN_PRESENTATION_*` accessibility inputs can deterministically
exercise Reduce Transparency, Increase Contrast, Reduce Motion, and
Differentiate Without Color. They are honored only by a dual-gated Debug
fixture launch; Release always follows the real system accessibility settings.
`-NSDoubleLocalizedStrings YES` can be passed through the standard argument
domain for text-expansion review.

Every accepted PNG has an adjacent `.capture.json` receipt using
`auralign.snapshot.v3`. The receipt binds the capture to its nonce, fixture,
requested and selected section, role, activity, sidebar state, appearance,
point and pixel dimensions, effective accessibility state, pseudo-localization
state, executable SHA-256, PNG SHA-256, and—when applicable—the exact
hidden-field focus-recovery request and focused accessibility identifier. Final
synthetic visual evidence must use `ScreenCaptureKit.currentProcess`; flattened
material fallback output is diagnostic only. Destinations, selectors, roles,
values, and duplicate paths fail closed, and existing evidence is never
overwritten.

## Verification Scope

Swift and mocked Python tests establish protocol, state-machine, rendering-data,
and cancellation behavior. They do not prove that selected models exist or
that inference backends can start. Backend readiness requires the GUI startup
check or the equivalent strict CLI command:

```bash
LTM_OFFLINE=1 uv run ltm doctor --startup \
  --config examples/config.strict-offline.yaml
```

Real-media acceptance is a separate, explicitly authorized local operation.
For an installed product, the selected ASR pack must verify before the
equivalent startup check can pass; a successful bridge handshake alone is not
transcription readiness.
