#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config="$script_dir/examples/config.strict-macos-arm64-cpu.yaml"
cache_dir="$script_dir/models"
runtime_dir="${LTM_RUNTIME_DIR:-$script_dir/.auralign-runtime}"
repair=0
source_checkout=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config|--cache-dir|--runtime-dir)
      if [[ $# -lt 2 || "$2" == --* ]]; then
        printf 'Option %s requires a value.\n' "$1" >&2
        exit 2
      fi
      ;;
  esac
  case "$1" in
    --config) config="$2"; shift 2 ;;
    --cache-dir) cache_dir="$2"; shift 2 ;;
    --runtime-dir) runtime_dir="$2"; shift 2 ;;
    --repair) repair=1; shift ;;
    --source-checkout) source_checkout=1; shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [[ "$config" != /* ]]; then config="$script_dir/$config"; fi
if [[ "$cache_dir" != /* ]]; then cache_dir="$script_dir/$cache_dir"; fi
if [[ "$runtime_dir" != /* ]]; then runtime_dir="$script_dir/$runtime_dir"; fi

if (( source_checkout == 1 )); then
  [[ -f "$script_dir/pyproject.toml" ]] || {
    echo "--source-checkout requires pyproject.toml beside bootstrap.sh." >&2
    exit 2
  }
  args=(uv run --project "$script_dir" ltm prepare-runtime --config "$config" --cache-dir "$cache_dir")
else
  if [[ -f "$runtime_dir/auralign-env.sh" ]]; then
    source "$runtime_dir/auralign-env.sh"
  fi
  [[ -x "$runtime_dir/.venv/bin/ltm" ]] || {
    echo "Install Auralign first or pass --source-checkout explicitly." >&2
    exit 2
  }
  args=("$runtime_dir/.venv/bin/ltm" prepare-runtime --config "$config" --cache-dir "$cache_dir")
fi
if (( repair == 1 )); then args+=(--repair); fi

# The Python command owns acquisition, redaction, token removal, immutable-file
# verification, generated Punkt preparation, and strict offline backend startup.
exec "${args[@]}"
