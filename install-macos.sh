#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

set -euo pipefail
umask 077

destination="${LTM_INSTALL_DIR:-.auralign-runtime}"
wheel_path=""
experimental_mlx=0
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --destination) destination="$2"; shift 2 ;;
    --wheel) wheel_path="$2"; shift 2 ;;
    --experimental-mlx) experimental_mlx=1; shift ;;
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

python_path="$(uv python find 3.11 2>/dev/null || true)"
[[ -n "$python_path" ]] || {
  echo "Python 3.11 is required. Install it explicitly with: uv python install 3.11" >&2
  exit 2
}
[[ "$($python_path -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.11" ]] || {
  echo "Resolved Python is not Python 3.11." >&2
  exit 2
}

mkdir -p "$destination"
chmod 700 "$destination"
uv venv --python "$python_path" "$destination/.venv"
runtime_python="$destination/.venv/bin/python"
export VIRTUAL_ENV="$destination/.venv"

extras=(--extra full)
if (( experimental_mlx == 1 )); then
  extras+=(--extra mlx)
fi

if [[ -f pyproject.toml && -f uv.lock ]]; then
  uv sync --active --locked --python "$runtime_python" "${extras[@]}" --group cpu --no-dev
else
  if (( experimental_mlx == 1 )); then
    echo "Experimental MLX is available only from a reviewed source checkout in v0.2.0." >&2
    exit 2
  fi
  if [[ -z "$wheel_path" ]]; then
    wheel_path="$(find "$script_dir" -maxdepth 1 -name 'local_transcript_max-0.2.0-*.whl' -print -quit)"
  fi
  [[ -f "$wheel_path" ]] || { echo "The verified Auralign 0.2.0 wheel was not found." >&2; exit 2; }
  requirements="$script_dir/requirements/macos-arm64-cpu-py311.txt"
  [[ -f "$requirements" ]] || {
    echo "The lock-derived macOS arm64 CPU requirements manifest is missing." >&2
    exit 2
  }
  uv pip install --python "$runtime_python" --require-hashes -r "$requirements"
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
"$runtime_python" -c 'import faster_whisper, pyannote.audio, whisperx, speechbrain, torchcodec'
printf '%s\n' \
  '#!/usr/bin/env bash' \
  "export DYLD_LIBRARY_PATH='$av_dylibs'\${DYLD_LIBRARY_PATH:+:\$DYLD_LIBRARY_PATH}" \
  > "$destination/auralign-env.sh"
chmod 600 "$destination/auralign-env.sh"
echo "Auralign 0.2.0 installed for macOS arm64 CPU."
echo "Model weights remain external; run bootstrap.sh only after reviewing upstream terms."
echo "Source $destination/auralign-env.sh before direct ltm commands."
