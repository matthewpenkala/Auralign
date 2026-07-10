#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

set -euo pipefail
umask 077

BOOTSTRAP_LOCK="${TMPDIR:-/tmp}/auralign-bootstrap-$(pwd | cksum | awk '{print $1}').lock"
if ! mkdir "$BOOTSTRAP_LOCK" 2>/dev/null; then
  echo "Another Auralign bootstrap is active for this repository." >&2
  exit 3
fi
trap 'rmdir "$BOOTSTRAP_LOCK" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

LOG_FILE="${PWD}/auralign_bootstrap.log"
REQUIRED_FAILURES=()
OPTIONAL_FAILURES=()
BOOTSTRAP_CAVEATS=()

log() {
  printf '%s\n' "$*" | tee -a "$LOG_FILE"
}

retry() {
  local label="$1"
  shift
  local attempt=1
  local delay=2
  while true; do
    log "START $label attempt $attempt"
    if "$@" >>"$LOG_FILE" 2>&1; then
      log "OK $label"
      return 0
    fi
    if [[ "$attempt" -ge 3 ]]; then
      log "SKIP $label failed after 3 attempts"
      return 1
    fi
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

retry_required() {
  local label="$1"
  shift
  if ! retry "$label" "$@"; then
    REQUIRED_FAILURES+=("$label")
  fi
}

retry_optional() {
  local label="$1"
  shift
  if ! retry "$label" "$@"; then
    OPTIONAL_FAILURES+=("$label")
  fi
}

run_required() {
  local label="$1"
  shift
  log "REQUIRED $label"
  if "$@" >>"$LOG_FILE" 2>&1; then
    log "OK $label"
    return 0
  fi
  log "ABORT $label failed; see $LOG_FILE"
  exit 1
}

if [[ ! -f "pyproject.toml" ]]; then
  echo "Auralign bootstrap must be run from the repository root containing pyproject.toml." >&2
  exit 2
fi

: > "$LOG_FILE"

export LTM_MODEL_CACHE="${LTM_MODEL_CACHE:-$PWD/models}"
export HF_HOME="${HF_HOME:-$PWD/.hf-cache}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"

LTM_BOOTSTRAP_PROFILE="${LTM_BOOTSTRAP_PROFILE:-runtime-samples}"
LTM_BOOTSTRAP_MAX_LOCAL_GB="${LTM_BOOTSTRAP_MAX_LOCAL_GB:-65}"
LTM_BOOTSTRAP_MIN_FREE_GB="${LTM_BOOTSTRAP_MIN_FREE_GB:-100}"
LTM_SAMPLE_MAX_GB="${LTM_SAMPLE_MAX_GB:-1}"
LTM_ALLOW_OVER_BUDGET="${LTM_ALLOW_OVER_BUDGET:-0}"
DATASET_CACHE="${LTM_DATASET_CACHE:-$PWD/datasets}"

mkdir -p "$LTM_MODEL_CACHE" "$HF_HOME" "$HF_HUB_CACHE" "$DATASET_CACHE/samples"

log "Auralign local bootstrap starting"
log "LTM_MODEL_CACHE=$LTM_MODEL_CACHE"
log "HF_HOME=$HF_HOME"
log "HF_HUB_CACHE=$HF_HUB_CACHE"
log "LTM_DATASET_CACHE=$DATASET_CACHE"
log "LTM_BOOTSTRAP_PROFILE=$LTM_BOOTSTRAP_PROFILE"
log "LTM_BOOTSTRAP_MAX_LOCAL_GB=$LTM_BOOTSTRAP_MAX_LOCAL_GB"
log "LTM_BOOTSTRAP_MIN_FREE_GB=$LTM_BOOTSTRAP_MIN_FREE_GB"
log "LTM_SAMPLE_MAX_GB=$LTM_SAMPLE_MAX_GB"
log "LTM_ALLOW_OVER_BUDGET=$LTM_ALLOW_OVER_BUDGET"
test -n "${LTM_HF_TOKEN:-}" && log "LTM_HF_TOKEN=present" || log "LTM_HF_TOKEN=missing"
test -n "${HF_TOKEN:-}" && log "HF_TOKEN=present" || log "HF_TOKEN=missing"
test -n "${LTM_DATASET_MIRROR_REMOTE:-}" && log "LTM_DATASET_MIRROR_REMOTE=present" || log "LTM_DATASET_MIRROR_REMOTE=missing"

log "Dependency provisioning commands to run before this script if needed:"
log "uv sync --extra dev --extra asr --extra diarization --extra alignment --extra speaker-id --extra api --group cpu --no-python-downloads"
log "use --group cu126 instead of --group cpu only for the CUDA 12.6 profile"

budget_args=(
  uv run ltm datasets plan
  --profile "$LTM_BOOTSTRAP_PROFILE"
  --cache-dir "$DATASET_CACHE"
  --model-cache-dir "$LTM_MODEL_CACHE"
  --max-local-gb "$LTM_BOOTSTRAP_MAX_LOCAL_GB"
  --min-free-gb "$LTM_BOOTSTRAP_MIN_FREE_GB"
)
if [[ "$LTM_ALLOW_OVER_BUDGET" == "1" ]]; then
  budget_args+=(--allow-over-budget)
fi
run_required "budget preflight" "${budget_args[@]}"

retry_optional "model audit preflight" uv run ltm models audit

retry_required "asr large-v3 Systran/faster-whisper-large-v3" \
  uv run ltm models pull --asr large-v3 --cache-dir "$LTM_MODEL_CACHE"

retry_required "asr large-v3-turbo mobiuslabsgmbh/faster-whisper-large-v3-turbo" \
  uv run ltm models pull --asr large-v3-turbo --cache-dir "$LTM_MODEL_CACHE"

retry_required "asr distil-large-v3 Systran/faster-distil-whisper-large-v3" \
  uv run ltm models pull --asr distil-large-v3 --cache-dir "$LTM_MODEL_CACHE"

retry_required "asr distil-large-v3.5 distil-whisper/distil-large-v3.5-ct2" \
  uv run ltm models pull --asr distil-large-v3.5 --cache-dir "$LTM_MODEL_CACHE"

retry_optional "alignment facebook/wav2vec2-base-960h" \
  uv run ltm models pull --alignment auto --cache-dir "$LTM_MODEL_CACHE"

retry_optional "speaker-id speechbrain/spkrec-ecapa-voxceleb" \
  uv run ltm models pull --speaker-id ecapa --cache-dir "$LTM_MODEL_CACHE"

if [[ -n "${LTM_HF_TOKEN:-}${HF_TOKEN:-}" ]]; then
  retry_required "diarization pyannote/speaker-diarization-community-1" \
    uv run ltm models pull --diarization community-1 --cache-dir "$LTM_MODEL_CACHE"
else
  log "SKIP diarization pyannote/speaker-diarization-community-1 requires HF_TOKEN or LTM_HF_TOKEN"
  BOOTSTRAP_CAVEATS+=("pyannote diarization was skipped because HF_TOKEN or LTM_HF_TOKEN is missing")
fi

log "SKIP dataset sample acquisition; all configured samples are blocked pending verified provenance and license"

retry_optional "dataset manifest" \
  uv run ltm datasets list --profile "$LTM_BOOTSTRAP_PROFILE" --cache-dir "$DATASET_CACHE"

if [[ -n "${LTM_DATASET_MIRROR_REMOTE:-}" ]]; then
  retry_optional "cloud mirror plan" \
    uv run ltm datasets cloud-plan \
      --profile "$LTM_BOOTSTRAP_PROFILE" \
      --cache-dir "$DATASET_CACHE" \
      --remote "$LTM_DATASET_MIRROR_REMOTE"
else
  log "SKIP cloud mirror plan; LTM_DATASET_MIRROR_REMOTE is not configured"
fi

retry_optional "model audit final" uv run ltm models audit
uv run ltm models verify --cache-dir "$LTM_MODEL_CACHE" >>"$LOG_FILE" 2>&1 || {
  log "SKIP verify not fully ready; see log for missing gated or optional resources"
  BOOTSTRAP_CAVEATS+=("model verification is not fully ready")
}
LTM_OFFLINE=1 uv run ltm doctor >>"$LOG_FILE" 2>&1 || {
  log "SKIP offline doctor not fully ready; see log for remediation commands"
  BOOTSTRAP_CAVEATS+=("offline doctor is not fully ready")
}

if [[ "${#OPTIONAL_FAILURES[@]}" -gt 0 ]]; then
  for failure in "${OPTIONAL_FAILURES[@]}"; do
    BOOTSTRAP_CAVEATS+=("optional step failed: $failure")
  done
fi

if [[ "${#BOOTSTRAP_CAVEATS[@]}" -gt 0 ]]; then
  log "Bootstrap caveats:"
  for caveat in "${BOOTSTRAP_CAVEATS[@]}"; do
    log "  - $caveat"
  done
fi

if [[ "${#REQUIRED_FAILURES[@]}" -gt 0 ]]; then
  log "Required bootstrap steps failed:"
  for failure in "${REQUIRED_FAILURES[@]}"; do
    log "  - $failure"
  done
  log "Auralign local bootstrap finished with required failures. Review $LOG_FILE."
  exit 1
fi

log "Auralign local bootstrap finished. Review $LOG_FILE, models/model_audit.json, and datasets/dataset_manifest.json."
