#!/usr/bin/env bash
#
# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
#
# Renders the Auralign visual-state evidence matrix from the Debug app using
# the existing DEBUG-only SnapshotDriver and PresentationFixture surfaces.
#
# Every capture is synthetic: fixtures are inert, never contact Python, never
# read real user media, and are marked "Synthetic UI Fixture" in the window.
# Each accepted PNG is published with an adjacent auralign.snapshot.v5 receipt.
#
# Usage:
#   bash scripts/capture_ui_matrix.sh [output-directory]
#
# The script builds the exact Debug app it launches. Set
# AURALIGN_SKIP_CAPTURE_BUILD=1 only when an enclosing gate has already built
# this same DerivedData product from the current source snapshot.

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
derived="${HOME}/Library/Developer/Xcode/DerivedData/AuralignLocalGate"
app="${derived}/Build/Products/Debug/Auralign.app"
binary="${app}/Contents/MacOS/Auralign"
output="${1:-${root}/build/ui-redesign-evidence-$(date -u '+%Y%m%d')/after}"

fail() {
  printf 'capture_ui_matrix: %s\n' "$*" >&2
  exit 1
}

if [[ "${AURALIGN_SKIP_CAPTURE_BUILD:-0}" != "1" ]]; then
  printf 'Building the current Auralign source for capture\n'
  xcodebuild \
    -project "${root}/macos/Auralign.xcodeproj" \
    -scheme Auralign \
    -configuration Debug \
    -destination 'platform=macOS' \
    -derivedDataPath "$derived" \
    -jobs 1 \
    CODE_SIGN_IDENTITY=- \
    build
fi

[[ -x "$binary" ]] || fail "Debug app not built at ${app}"
mkdir -p "$output"

captured=0
skipped=0
failed=0
capture_timeout_deciseconds="${AURALIGN_CAPTURE_TIMEOUT_DECISECONDS:-450}"

# capture <name> <fixture> <section> <appearance> <width> <height> [extra-env...]
capture() {
  local name="$1" fixture="$2" section="$3" appearance="$4"
  local width="$5" height="$6"
  shift 6

  local destination="${output}/${name}.png"
  if [[ -e "$destination" ]]; then
    printf '  skip (exists) %s\n' "$name"
    skipped=$((skipped + 1))
    return 0
  fi

  # Explicit false defaults make ordinary captures deterministic even when the
  # release machine has an accessibility appearance enabled system-wide. Each
  # named accessibility case replaces exactly one of these values below.
  local reduce_transparency=0
  local increase_contrast=0
  local reduce_motion=0
  local differentiate_without_color=0
  local -a extra_environment=()
  local extra_environment_count=0
  local -a application_arguments=(--ui-fixture "$fixture")
  local pair
  for pair in "$@"; do
    case "$pair" in
      AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY=*)
        reduce_transparency="${pair#*=}"
        ;;
      AURALIGN_PRESENTATION_INCREASE_CONTRAST=*)
        increase_contrast="${pair#*=}"
        ;;
      AURALIGN_PRESENTATION_REDUCE_MOTION=*)
        reduce_motion="${pair#*=}"
        ;;
      AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR=*)
        differentiate_without_color="${pair#*=}"
        ;;
      "AURALIGN_SNAPSHOT_EXTRA_ARGS=-NSDoubleLocalizedStrings YES")
        application_arguments=(
          -NSDoubleLocalizedStrings YES
          "${application_arguments[@]}"
        )
        ;;
      AURALIGN_SNAPSHOT_EXTRA_ARGS=*)
        fail "unsupported snapshot application arguments for ${name}"
        ;;
      *)
        extra_environment+=("$pair")
        extra_environment_count=$((extra_environment_count + 1))
        ;;
    esac
  done

  local -a environment=(
    "AURALIGN_ENABLE_UI_TEST_FIXTURES=1"
    "AURALIGN_ENABLE_DEBUG_AUTOMATION=1"
    "AURALIGN_SNAPSHOT_DIR=${output}"
    "AURALIGN_SNAPSHOT_ON_LAUNCH=${destination}"
    "AURALIGN_SNAPSHOT_NONCE=$(uuidgen)"
    "AURALIGN_SNAPSHOT_SECTION=${section}"
    "AURALIGN_SNAPSHOT_APPEARANCE=${appearance}"
    "AURALIGN_SNAPSHOT_WINDOW_SIZE=${width},${height}"
    "AURALIGN_SNAPSHOT_EXIT_AFTER_CAPTURE=1"
    "AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY=${reduce_transparency}"
    "AURALIGN_PRESENTATION_INCREASE_CONTRAST=${increase_contrast}"
    "AURALIGN_PRESENTATION_REDUCE_MOTION=${reduce_motion}"
    "AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR=${differentiate_without_color}"
  )
  # macOS still ships Bash 3.2, whose `set -u` handling treats expansion of an
  # explicitly empty local array as an unbound variable. Do not expand it until
  # at least one extra environment value was parsed.
  if (( extra_environment_count > 0 )); then
    environment+=("${extra_environment[@]}")
  fi

  # A capture that fails SnapshotDriver's visual-validity gate never sets
  # EXIT_AFTER_CAPTURE, so the process would wait forever. Bound every launch.
  env "${environment[@]}" "$binary" "${application_arguments[@]}" \
    >/dev/null 2>&1 &
  local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null && (( waited < capture_timeout_deciseconds )); do
    sleep 0.1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    sleep 1
    kill -KILL "$pid" 2>/dev/null || true
    printf '  TIMEOUT %s (no valid frame in %ds)\n' \
      "$name" "$((capture_timeout_deciseconds / 10))" >&2
  fi
  wait "$pid" 2>/dev/null || true

  if [[ -f "$destination" ]]; then
    printf '  ok   %s\n' "$name"
    captured=$((captured + 1))
  else
    printf '  FAIL %s\n' "$name" >&2
    failed=$((failed + 1))
  fi
}

printf 'Rendering Auralign visual-state matrix into %s\n' "$output"

# --- Lifecycle states, default window, dark ---------------------------------
capture "01-newjob-empty-dark"          no-input            newJob        dark  1180 780
capture "02-newjob-selected-dark"       selected-run-plan   newJob        dark  1180 780
capture "03-newjob-invalid-input-dark"  invalid-input       newJob        dark  1180 780
capture "04-activity-running-dark"      running             processing    dark  1180 780
capture "05-activity-stopping-dark"     stopping            processing    dark  1180 780
capture "06-activity-pending-exit-dark" result-pending-exit processing    dark  1180 780
capture "07-activity-failed-dark"       failure             processing    dark  1180 780
capture "08-activity-cancelled-dark"    cancelled           processing    dark  1180 780
capture "09-results-rich-dark"          results-rich        results       dark  1180 780
capture "10-results-stale-dark"         results-stale       results       dark  1180 780
capture "11-results-empty-dark"         no-input            results       dark  1180 780
capture "12-readiness-ready-dark"       readiness-ready     readiness     dark  1180 780
capture "13-readiness-blocked-dark"     readiness-blocked   readiness     dark  1180 780
capture "14-readiness-warnings-dark"    readiness-warnings  readiness     dark  1180 780
capture "15-readiness-checking-dark"    readiness-checking  readiness     dark  1180 780
capture "16-readiness-stale-dark"       readiness-stale     readiness     dark  1180 780
capture "17-runtime-unavailable-dark"   runtime-unavailable readiness     dark  1180 780
capture "18-parameters-dark"            selected-run-plan   allParameters dark  1180 780
capture "19-signal-setup-invalid-advanced-dark" invalid-hidden-advanced asr dark 1180 780

# --- Light appearance -------------------------------------------------------
capture "20-newjob-selected-light"      selected-run-plan   newJob        light 1180 780
capture "21-activity-running-light"     running             processing    light 1180 780
capture "22-results-rich-light"         results-rich        results       light 1180 780
capture "23-readiness-blocked-light"    readiness-blocked   readiness     light 1180 780
capture "24-parameters-light"           selected-run-plan   allParameters light 1180 780

# --- Window sizes -----------------------------------------------------------
capture "30-newjob-minimum-dark"        selected-run-plan   newJob        dark   900 600
capture "31-parameters-minimum-dark"    selected-run-plan   allParameters dark   900 600
capture "32-activity-minimum-dark"      running             processing    dark   900 600
capture "33-results-minimum-dark"       results-rich        results       dark   900 600
capture "34-newjob-wide-dark"           selected-run-plan   newJob        dark  1440 900
capture "35-parameters-wide-dark"       selected-run-plan   allParameters dark  1440 900
capture "36-results-wide-dark"          results-rich        results       dark  1440 900

# --- Accessibility appearances ---------------------------------------------
capture "40-newjob-increase-contrast-dark" selected-run-plan newJob dark 1180 780 \
  "AURALIGN_PRESENTATION_INCREASE_CONTRAST=1"
capture "41-newjob-reduce-transparency-dark" selected-run-plan newJob dark 1180 780 \
  "AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY=1"
capture "42-newjob-differentiate-dark" selected-run-plan newJob dark 1180 780 \
  "AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR=1"
capture "43-activity-reduce-motion-dark" running processing dark 1180 780 \
  "AURALIGN_PRESENTATION_REDUCE_MOTION=1"
capture "44-newjob-increase-contrast-light" selected-run-plan newJob light 1180 780 \
  "AURALIGN_PRESENTATION_INCREASE_CONTRAST=1"
capture "45-activity-differentiate-dark" running processing dark 1180 780 \
  "AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR=1"

# --- Enlarged text (pseudo-localization doubles string length) --------------
capture "50-newjob-double-strings-dark" selected-run-plan newJob dark 1180 780 \
  "AURALIGN_SNAPSHOT_EXTRA_ARGS=-NSDoubleLocalizedStrings YES"

# --- Inactive window and collapsed sidebar ---------------------------------
capture "60-newjob-inactive-dark" selected-run-plan newJob dark 1180 780 \
  "AURALIGN_SNAPSHOT_WINDOW_ACTIVITY=inactive"
capture "61-newjob-sidebar-collapsed-dark" selected-run-plan newJob dark 1180 780 \
  "AURALIGN_SNAPSHOT_SIDEBAR=collapsed"

# --- Settings and recovery windows -----------------------------------------
# The Settings scene's production content establishes a 900-point minimum
# width. Request that real supported geometry rather than waiting for an
# impossible 620-point frame.
capture "70-settings-valid-dark" settings-valid newJob dark 900 600 \
  "AURALIGN_SNAPSHOT_WINDOW_ROLE=settings"
capture "71-settings-locked-dark" settings-locked newJob dark 900 600 \
  "AURALIGN_SNAPSHOT_WINDOW_ROLE=settings"
capture "72-fatal-bootstrap-dark" resource-failure newJob dark 900 600
# The synthetic provenance banner sits above ModelSetupView's production
# 560-point minimum content height. Request a frame that can truthfully contain
# both rather than waiting for AppKit to accept an impossible 620-point frame.
capture "73-model-setup-required-dark" model-unavailable-gated newJob dark 900 680 \
  "AURALIGN_SNAPSHOT_WINDOW_ROLE=model-setup"

printf '\nCaptured %d, skipped %d, failed %d, into %s\n' \
  "$captured" "$skipped" "$failed" "$output"
[[ "$failed" -eq 0 ]] || exit 1

uv run --python 3.11 python -B - "$output" "$binary" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
binary = Path(sys.argv[2])
pngs = sorted(output.glob("*.png"))
if len(pngs) != 44:
    raise SystemExit(f"expected 44 matrix PNGs, found {len(pngs)}")

binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
app = binary.parents[2]
code_identity_components = {
    binary.relative_to(app).as_posix(): binary_sha256,
}
debug_implementation = binary.with_name(f"{binary.name}.debug.dylib")
if debug_implementation.is_file():
    code_identity_components[
        debug_implementation.relative_to(app).as_posix()
    ] = hashlib.sha256(debug_implementation.read_bytes()).hexdigest()
metallib = app / "Contents" / "Resources" / "default.metallib"
if not metallib.is_file():
    raise SystemExit("compiled default.metallib is missing from the captured app")
code_identity_components[
    metallib.relative_to(app).as_posix()
] = hashlib.sha256(metallib.read_bytes()).hexdigest()
code_identity_manifest = b"".join(
    path.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n"
    for path, digest in sorted(code_identity_components.items())
)
code_identity_sha256 = hashlib.sha256(code_identity_manifest).hexdigest()
receipts: dict[str, dict[str, object]] = {}
for png in pngs:
    receipt_path = png.with_suffix(".capture.json")
    if not receipt_path.is_file():
        raise SystemExit(f"missing capture receipt for {png.name}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipts[png.stem] = receipt
    if receipt.get("schemaVersion") != "auralign.snapshot.v5":
        raise SystemExit(f"wrong receipt schema for {png.name}")
    if receipt.get("executableSHA256") != binary_sha256:
        raise SystemExit(f"stale executable receipt for {png.name}")
    if receipt.get("codeIdentityComponents") != code_identity_components:
        raise SystemExit(f"stale code-identity components for {png.name}")
    if receipt.get("codeIdentitySHA256") != code_identity_sha256:
        raise SystemExit(f"stale code-identity receipt for {png.name}")
    if receipt.get("presentationAccessibilityOverrideApplied") is not True:
        raise SystemExit(f"missing deterministic accessibility override for {png.name}")
    expected_effect_tier = (
        "static"
        if receipt.get("effectiveReduceTransparency")
        or receipt.get("effectiveIncreaseContrast")
        or receipt.get("effectiveDifferentiateWithoutColor")
        else "swiftuiMetal"
    )
    if receipt.get("rendererEffectTier") != expected_effect_tier:
        raise SystemExit(f"wrong renderer effect tier for {png.name}")
    if receipt.get("opticalPhase") != "static":
        raise SystemExit(f"non-deterministic optical phase for {png.name}")
    if not receipt.get("operatingSystemVersion"):
        raise SystemExit(f"missing operating-system identity for {png.name}")

    requested_activity = receipt.get("requestedWindowActivity")
    if png.stem == "60-newjob-inactive-dark":
        if requested_activity != "inactive":
            raise SystemExit("inactive fixture did not request inactive presentation")
        if receipt.get("isApplicationActive") is not False:
            raise SystemExit("inactive fixture captured while application was active")
        if receipt.get("isKeyWindow") is not False:
            raise SystemExit("inactive fixture captured a key window")
    else:
        if requested_activity != "active":
            raise SystemExit(f"{png.name} did not request active presentation")
        active_state = (
            receipt.get("isApplicationActive"),
            receipt.get("isKeyWindow"),
            receipt.get("isMainWindow"),
        )
        if active_state != (True, True, True):
            raise SystemExit(
                f"{png.name} captured a non-active window state: {active_state}"
            )

expected_effects = {
    "40-newjob-increase-contrast-dark": (False, False, True, False),
    "41-newjob-reduce-transparency-dark": (False, True, False, False),
    "42-newjob-differentiate-dark": (False, False, False, True),
    "43-activity-reduce-motion-dark": (True, False, False, False),
    "44-newjob-increase-contrast-light": (False, False, True, False),
    "45-activity-differentiate-dark": (False, False, False, True),
}
for stem, receipt in receipts.items():
    expected = expected_effects.get(stem, (False, False, False, False))
    observed = (
        receipt.get("effectiveReduceMotion"),
        receipt.get("effectiveReduceTransparency"),
        receipt.get("effectiveIncreaseContrast"),
        receipt.get("effectiveDifferentiateWithoutColor"),
    )
    if observed != expected:
        raise SystemExit(
            f"{stem} accessibility state {observed} does not match {expected}"
        )

if receipts["50-newjob-double-strings-dark"].get("doubleLocalizedStrings") is not True:
    raise SystemExit("double-strings capture did not pass NSDoubleLocalizedStrings")

print(
    "Capture receipt validation passed: 44 current-code frames, deterministic "
    "accessibility states, active/inactive window states, and doubled strings; "
    "Debug launcher and implementation identities are bound."
)
PY
