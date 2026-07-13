#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
destination="${LTM_INSTALL_DIR:-$script_dir/.auralign-runtime}"
wheel_path=""
experimental_mlx=0
source_checkout=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --destination) destination="$2"; shift 2 ;;
    --wheel) wheel_path="$2"; shift 2 ;;
    --experimental-mlx) experimental_mlx=1; shift ;;
    --source-checkout) source_checkout=1; shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ "$(uname -s)" == "Darwin" ]] || { echo "Auralign macOS installer requires macOS." >&2; exit 2; }
[[ "$(uname -m)" == "arm64" ]] || { echo "Auralign 0.2.0 supports Apple Silicon only." >&2; exit 2; }
macos_major="$(sw_vers -productVersion | cut -d. -f1)"
(( macos_major >= 14 )) || { echo "Auralign 0.2.0 requires macOS 14 or later." >&2; exit 2; }

command -v uv >/dev/null || { echo "uv is required on PATH." >&2; exit 2; }
command -v ffmpeg >/dev/null || { echo "FFmpeg is required on PATH." >&2; exit 2; }
command -v ffprobe >/dev/null || { echo "FFprobe is required on PATH." >&2; exit 2; }
ffmpeg -version >/dev/null
ffprobe -version >/dev/null

python_path="$(uv python find 3.11 2>/dev/null || true)"
[[ -n "$python_path" ]] || {
  echo "Python 3.11 is required. Install it explicitly with: uv python install 3.11" >&2
  exit 2
}
[[ "$($python_path -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.11" ]] || {
  echo "Resolved Python is not Python 3.11." >&2
  exit 2
}

destination="$(mkdir -p "$destination" && cd -- "$destination" && pwd -P)"
chmod 700 "$destination"
uv venv --python "$python_path" "$destination/.venv"
runtime_python="$destination/.venv/bin/python"
export VIRTUAL_ENV="$destination/.venv"

extras=(--extra full)
if (( experimental_mlx == 1 )); then
  extras+=(--extra mlx)
fi

if (( source_checkout == 1 )); then
  [[ -f "$script_dir/pyproject.toml" && -f "$script_dir/uv.lock" ]] || {
    echo "--source-checkout requires pyproject.toml and uv.lock beside this installer." >&2
    exit 2
  }
  (
    cd -- "$script_dir"
    uv sync --active --locked --python "$runtime_python" "${extras[@]}" --group cpu --no-dev
  )
else
  if (( experimental_mlx == 1 )); then
    echo "Experimental MLX is available only with --source-checkout in v0.2.0." >&2
    exit 2
  fi
  if [[ -z "$wheel_path" ]]; then
    shopt -s nullglob
    wheels=("$script_dir"/local_transcript_max-0.2.0-py3-none-any.whl)
    shopt -u nullglob
    [[ "${#wheels[@]}" -eq 1 ]] || {
      echo "The bundle must contain exactly one verified Auralign 0.2.0 wheel." >&2
      exit 2
    }
    wheel_path="${wheels[0]}"
  fi
  [[ -f "$wheel_path" ]] || { echo "The verified Auralign 0.2.0 wheel was not found." >&2; exit 2; }
  requirements="$script_dir/requirements/macos-arm64-cpu-py311.txt"
  [[ -f "$requirements" ]] || {
    echo "The lock-derived macOS arm64 CPU requirements manifest is missing." >&2
    exit 2
  }
  # Every candidate remains hash-pinned. Considering all declared indexes is
  # required because uv's first-index policy can otherwise stop at a PyTorch
  # index that contains a package name but not the reviewed PyPI version.
  uv pip install --python "$runtime_python" --index-strategy unsafe-best-match \
    --require-hashes -r "$requirements"
  uv pip install --python "$runtime_python" --no-deps "$wheel_path"
fi

av_dylibs="$($runtime_python -c 'import pathlib, av; print(pathlib.Path(av.__file__).parent / ".dylibs")')"
for contract in \
  'libavutil.59:libavutil.59.*.dylib' \
  'libavcodec.61:libavcodec.61.*.dylib' \
  'libavformat.61:libavformat.61.*.dylib' \
  'libavdevice.61:libavdevice.61.*.dylib' \
  'libavfilter.10:libavfilter.10.*.dylib' \
  'libswscale.8:libswscale.8.*.dylib' \
  'libswresample.5:libswresample.5.*.dylib'; do
  alias_name="${contract%%:*}.dylib"
  pattern="${contract#*:}"
  matches=("$av_dylibs"/$pattern)
  [[ "${#matches[@]}" -eq 1 && -f "${matches[0]}" ]] || {
    echo "The reviewed PyAV 15.1.0 FFmpeg 7 runtime contract is incomplete." >&2
    exit 2
  }
  ln -sfn "$(basename "${matches[0]}")" "$av_dylibs/$alias_name"
done
export DYLD_LIBRARY_PATH="$av_dylibs${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
"$runtime_python" -c 'import ctranslate2, faster_whisper, pyannote.audio, whisperx, speechbrain, torch, torchcodec; assert (torch.ones(1) + 1).item() == 2; assert ctranslate2.get_supported_compute_types("cpu")'

# Resolve the dependency path when sourced so this local file contains no
# embedded home directory and remains valid when the runtime directory moves.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [ -n "${BASH_VERSION:-}" ]; then' \
  '  _auralign_env_source="${BASH_SOURCE[0]}"' \
  'elif [ -n "${ZSH_VERSION:-}" ]; then' \
  '  eval '\''_auralign_env_source="${(%):-%N}"'\''' \
  'else' \
  '  printf "Auralign environment setup requires bash or zsh.\n" >&2' \
  '  return 2' \
  'fi' \
  '_auralign_runtime_dir="$(cd -- "$(dirname -- "$_auralign_env_source")" && pwd -P)"' \
  '_auralign_python="$_auralign_runtime_dir/.venv/bin/python"' \
  '_auralign_av_dylibs="$("$_auralign_python" -c '\''import pathlib, av; print(pathlib.Path(av.__file__).parent / ".dylibs")'\'')"' \
  'export DYLD_LIBRARY_PATH="$_auralign_av_dylibs${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"' \
  'unset _auralign_env_source _auralign_runtime_dir _auralign_python _auralign_av_dylibs' \
  > "$destination/auralign-env.sh"
chmod 600 "$destination/auralign-env.sh"
echo "Auralign 0.2.0 installed for macOS arm64 CPU."
echo "Runtime: $destination"
echo "Model weights remain external; run bootstrap.sh only after reviewing upstream terms."
echo "Source $destination/auralign-env.sh before direct ltm commands."
