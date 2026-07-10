#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

set -euo pipefail
umask 077

config="examples/config.strict-macos-arm64-cpu.yaml"
cache_dir="models"
repair=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config="$2"; shift 2 ;;
    --cache-dir) cache_dir="$2"; shift 2 ;;
    --repair) repair=1; shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

runtime_dir="${LTM_RUNTIME_DIR:-.auralign-runtime}"
if [[ -f "$runtime_dir/auralign-env.sh" ]]; then
  # Generated locally by install-macos.sh; it contains no token or private path.
  source "$runtime_dir/auralign-env.sh"
fi
if [[ -x "$runtime_dir/.venv/bin/ltm" ]]; then
  args=("$runtime_dir/.venv/bin/ltm" prepare-runtime --config "$config" --cache-dir "$cache_dir")
else
  args=(uv run ltm prepare-runtime --config "$config" --cache-dir "$cache_dir")
fi
if (( repair == 1 )); then
  args+=(--repair)
fi

# The Python command owns acquisition, redaction, token removal, immutable-file
# verification, generated Punkt preparation, and strict offline backend startup.
exec "${args[@]}"
