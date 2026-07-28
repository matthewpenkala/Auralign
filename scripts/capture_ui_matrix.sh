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
# Each accepted PNG is published with an adjacent auralign.snapshot.v3 receipt.
#
# Usage:
#   bash scripts/capture_ui_matrix.sh [output-directory]

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
  )
  local pair
  for pair in "$@"; do
    environment+=("$pair")
  done

  # A capture that fails SnapshotDriver's visual-validity gate never sets
  # EXIT_AFTER_CAPTURE, so the process would wait forever. Bound every launch.
  env "${environment[@]}" "$binary" --ui-fixture "$fixture" >/dev/null 2>&1 &
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
capture "19-parameters-invalid-dark"    invalid-hidden-advanced allParameters dark 1180 780

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
capture "70-settings-valid-dark" settings-valid newJob dark 620 460 \
  "AURALIGN_SNAPSHOT_WINDOW_ROLE=settings"
capture "71-settings-locked-dark" settings-locked newJob dark 620 460 \
  "AURALIGN_SNAPSHOT_WINDOW_ROLE=settings"
capture "72-fatal-bootstrap-dark" resource-failure newJob dark 900 600

printf '\nCaptured %d, skipped %d, failed %d, into %s\n' \
  "$captured" "$skipped" "$failed" "$output"
[[ "$failed" -eq 0 ]] || exit 1
