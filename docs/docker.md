<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Docker

> **Internal-only artifact.** Auralign container images are not cleared for
> redistribution. For approved internal delivery, use source or Python
> wheel/sdist; no artifact is cleared for public or external distribution
> without the documented written approval. Do not push images to any registry.

Container transcription depends on mounted local models, caches, input media,
and compatible hardware. Validate Compose changes with `docker compose config`
when Docker is available.
Both the default CPU image and the CUDA-based GPU profile install the pinned
Python 3.11 runtime through `uv`, independent of the base image's system Python.
The CPU/server profiles use the explicit PyTorch CPU index and exclude Triton;
the GPU profile uses the mutually exclusive CUDA 12.6 dependency lane. Base
images and the uv tool image are pinned by digest.
CPU/server Debian and GPU Ubuntu `ca-certificates` and FFmpeg package versions
are pinned independently; a package pin from one distribution must never be
reused for the other. The exact observed Debian FFmpeg configuration is recorded
in `compliance/ffmpeg-bookworm-buildconf.txt`. The GPU image's separately built
Ubuntu/native closure requires its own exact-image evidence before release.
These native closures are among the reasons external container distribution
remains blocked.
The supported container platform is `linux/amd64`: the locked pyannote stack's
`torchcodec` build is unavailable for Linux ARM64. Compose declares amd64 for
every profile; Apple Silicon Docker Desktop runs it through emulation. A native
Linux ARM64 image is not claimed as supported.

Direct builds must select the same platform:

```bash
docker build --platform linux/amd64 -t auralign:local .
```

CPU doctor:

```bash
docker compose --profile cpu run --rm ltm-cpu doctor
```

GPU doctor:

```bash
docker compose --profile gpu run --rm ltm-gpu doctor
```

Local server:

```bash
docker compose --profile server up ltm-server
```

The container listens on `0.0.0.0` internally with `--allow-network-bind`, but
Compose publishes it on `127.0.0.1:8765` on the host. The server profile also
sets network-bound mode plus explicit `/input` and `/output` API roots; the
model cache remains separately mounted at `/models`.
Requests outside those mounts fail closed.

Volume mounts:

```text
./models      -> /models
./.hf-cache   -> /hf-cache
./data        -> /input:ro
./outputs     -> /output
./embeddings  -> /speaker-profiles
```

Environment:

- `.env` is optional and ignored by Git.
- Do not bake tokens into images.
- Use `HF_TOKEN` or `LTM_HF_TOKEN` only at runtime after accepting gated terms.
- Offline containers should mount populated `/models` and `/hf-cache`, then run
  `LTM_OFFLINE=1 ltm doctor`.

CUDA image:

The Dockerfile defaults to a CPU Python base. The GPU compose profile passes a
CUDA runtime base image through `BASE_IMAGE`. NVIDIA runtime support must be
configured on the host.

The image carries Auralign's license and notices under
`/usr/share/doc/auralign`, plus an OCI label stating
`internal-only-not-cleared-for-redistribution`. That label is a release block,
not proof that third-party container obligations have been cleared. External
distribution additionally requires exact image SBOM, native-library, FFmpeg,
Debian, NVIDIA, notice, corresponding-source, and codec-patent review.
