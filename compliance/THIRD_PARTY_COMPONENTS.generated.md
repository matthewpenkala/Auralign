<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Effective Third-Party Component Inventory

> Generated from the machine-readable compliance registries. Do not edit by hand.
> This inventory records evidence and release disposition; it grants no rights.

## Declared, Build, CI, And Container Components

| Identifier | Versions/digest | License | Use | Bundling | Redistribution | Review |
| --- | --- | --- | --- | --- | --- | --- |
| nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 | sha256:8aef630a54bc5c5146ae5ce68e6af5caa3df0fb690bb91544175c91f307e4356 | LicenseRef-NVIDIA-Deep-Learning-Container | internal-only CUDA 12.6 container base | declared_or_build_only_not_bundled_in_source_wheel_sdist | blocked_external_container_pending_NVIDIA_and_native_clearance | automated_evidence_recorded_pending_owner_release_approval |
| docker.io/library/python:3.11-slim-bookworm | sha256:f5cf0344c9886ff24d34797578d5d7dd6e8911ae0fe5962bb55d0f89603ec361 | LicenseRef-Docker-Official-Image-Mixed | internal-only CPU/runtime container base | declared_or_build_only_not_bundled_in_source_wheel_sdist | blocked_external_container_pending_exact_image_clearance | automated_evidence_recorded_pending_owner_release_approval |
| ghcr.io/astral-sh/uv:0.11.26 | sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 | Apache-2.0 OR MIT | internal-only container build stage | declared_or_build_only_not_bundled_in_source_wheel_sdist | blocked_external_container_pending_exact_image_clearance | automated_evidence_recorded_pending_owner_release_approval |
| actions/checkout | 11bd71901bbe5b1630ceea73d27597364c9af683 | MIT | private CI source checkout with persisted credentials disabled | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| actions/dependency-review-action | a1d282b36b6f3519aa1f3fc636f609c47dddb294 | MIT | pull-request dependency review; requires eligible private-repository feature | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| astral-sh/setup-uv | 557e51de59eb14aaaba2ed9621916900a91d50c6 | MIT | private CI uv provisioning | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| av | 15.1.0 | BSD-3-Clause | PyAV FFmpeg 7 ABI pin for macOS TorchCodec compatibility | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| cyclonedx-bom | 7.3.0 | Apache-2.0 | SBOM generation only | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| fastapi | 0.137.1 | MIT | optional local API dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| faster-whisper | 1.2.1 | MIT | optional ASR dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| hatchling | 1.27.0 | MIT | pinned PEP 517 build backend | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| huggingface-hub | 0.36.2 | Apache-2.0 | declared runtime dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| jiwer | 4.0.0 | Apache-2.0 | declared runtime dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| mlx-whisper | 0.4.3 | MIT | optional Apple Silicon ASR dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| mypy | 2.1.0 | MIT | type checking only | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| pip-audit | 2.10.1 | Apache-2.0 | connected vulnerability audit only | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| pyannote-audio | 4.0.4 | MIT | optional diarization dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| pydantic | 2.13.4 | MIT | declared runtime dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| pytest | 9.1.0 | MIT | testing only | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| pyyaml | 6.0.3 | MIT | declared runtime dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| reuse | 6.2.0 | GPL-3.0-or-later | build-only license lint/SBOM tool | declared_or_build_only_not_bundled_in_source_wheel_sdist | conditional build-only dependency; never bundled | automated_evidence_recorded_pending_owner_release_approval |
| ruamel-yaml | 0.18.17 | MIT | SpeechBrain HyperPyYAML compatibility pin | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| ruff | 0.15.17 | MIT | linting only | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| speechbrain | 1.1.0 | Apache-2.0 | optional speaker-embedding dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| torch | 2.8.0, 2.8.0+cpu, 2.8.0+cu126, 2.8.0+cu128 | BSD-3-Clause | mutually exclusive CPU/CUDA 12.6/CUDA 12.8 source-workspace groups | declared_or_build_only_not_bundled_in_source_wheel_sdist | not bundled; wheel/native obligations require artifact-specific review | automated_evidence_recorded_pending_owner_release_approval |
| torchaudio | 2.8.0, 2.8.0+cpu, 2.8.0+cu126, 2.8.0+cu128 | BSD-2-Clause | mutually exclusive CPU/CUDA 12.6/CUDA 12.8 source-workspace groups | declared_or_build_only_not_bundled_in_source_wheel_sdist | not bundled; wheel/native obligations require artifact-specific review | automated_evidence_recorded_pending_owner_release_approval |
| torchvision | 0.23.0, 0.23.0+cpu, 0.23.0+cu126, 0.23.0+cu128 | BSD-3-Clause | mutually exclusive CPU/CUDA 12.6/CUDA 12.8 source-workspace groups | declared_or_build_only_not_bundled_in_source_wheel_sdist | not bundled; wheel/native obligations require artifact-specific review | automated_evidence_recorded_pending_owner_release_approval |
| typer | 0.26.7 | MIT | declared runtime dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| types-pyyaml | 6.0.12.20260518 | Apache-2.0 | type stubs only | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| uvicorn | 0.49.0 | BSD-3-Clause | optional local API dependency | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| whisperx | 3.8.6 | BSD-2-Clause | optional forced-alignment dependency; direct Triton edge excluded | declared_or_build_only_not_bundled_in_source_wheel_sdist | not_bundled; installed copy remains under upstream terms | automated_evidence_recorded_pending_owner_release_approval |
| Debian bookworm packages in exact image digest | python-base-digest-and-container-SBOM-specific | NOASSERTION | internal-only container operating-system closure | declared_or_build_only_not_bundled_in_source_wheel_sdist | blocked_external_container_pending_dpkg_notices_and_corresponding_source_review | automated_evidence_recorded_pending_owner_release_approval |
| Debian bookworm ffmpeg package selected at image build | 7:5.1.9-0+deb12u1 | LGPL-2.1-or-later AND LicenseRef-Build-Configuration-Dependent | media inspection/normalization in internal-only container | declared_or_build_only_not_bundled_in_source_wheel_sdist | blocked_external_container_pending_dpkg_and_ffmpeg_build_configuration_evidence | automated_evidence_recorded_pending_owner_release_approval |
| Ubuntu noble ffmpeg package selected for the CUDA image | 7:6.1.1-3ubuntu5 | LGPL-2.1-or-later AND LicenseRef-Build-Configuration-Dependent | media inspection/normalization in internal-only CUDA 12.6 container | declared_or_build_only_not_bundled_in_source_wheel_sdist | blocked_external_container_pending_exact_image_and_ffmpeg_build_configuration_evidence | automated_evidence_recorded_pending_owner_release_approval |

## Models And Datasets

| Kind/key | Immutable revision | License | Acquisition | Runtime | Redistribution | Review |
| --- | --- | --- | --- | --- | --- | --- |
| dataset:ami-full | 46f28f2503e2ec48f8867a84eef356c70476beab | CC-BY-4.0 | mirror_approved | not_runtime_required | permitted_with_attribution | verified |
| dataset:librispeech-dummy | 5be91486e11a2d616f4ec5db8d3fd248585ac07a | NOASSERTION | blocked | not_runtime_required | blocked | blocked_missing_publisher_license |
| dataset:librispeech-full | 71cacbfb7e2354c4226d01e70d77d5fca3d04ba1 | CC-BY-4.0 | mirror_approved | not_runtime_required | permitted_with_attribution | verified |
| dataset:voxceleb-full | b8a8825c87582831cc217b6ff84a36558ced84d6 | CC-BY-4.0 | blocked | not_runtime_required | blocked_pending_rights_review | blocked_underlying_rights_and_biometric_review |
| model:community-1 | 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee | CC-BY-4.0 | approved_after_gated_terms | approved_local_after_gated_terms | permitted_with_attribution_and_gated_terms | verified_gated |
| model:distil-large-v3 | c3058b475261292e64a0412df1d2681c06260fab | MIT | approved | approved_local | permitted_with_license_terms | verified |
| model:distil-large-v3.5 | 9793ccc07920e0f830e1dba0343efcdf0ef8c903 | MIT | approved | approved_local | permitted_with_license_terms | verified |
| model:ecapa | 0f99f2d0ebe89ac095bcc5903c4dd8f72b367286 | Apache-2.0 | approved | approved_local | permitted_with_license_terms | verified_with_training_data_caveat |
| model:en | 22aad52d435eb6dbaf354bdad9b0da84ce7d6156 | Apache-2.0 | approved | approved_local | permitted_with_license_terms | verified |
| model:large-v3 | edaa852ec7e145841d8ffdb056a99866b5f0a478 | MIT | approved | approved_local | permitted_with_license_terms | verified |
| model:mlx-base | 1e3e249fb8d01c655324bd6841b1deadffd6d04c | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-base.en | aa0678c3466ed62c5c6114ec600a0e1f96820089 | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-large | 9310354911111f2406ead1478e0139d9c6ea3acc | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-large-v2 | cce86229e2765266197fef869ce9f7e2550067ab | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-large-v3 | 49e6aa286ad60c14352c404340ded53710378a11 | MIT | manual_only | approved_local | permitted_with_license_terms | verified |
| model:mlx-large-v3-turbo | a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-medium | 7fc08c4eac4c316526498f147dfdee6f6303f975 | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-medium.en | 8d0335069b551e8586b8a4680674403d9417dfe4 | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-small | 45f3915923c7a79a5a5b5a7d909d39aeb0e5630e | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-small.en | 52a88bf6e98b114a210c21bb83e22d6e1505cb73 | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-tiny | 6caf9c55601caafbe6508a8b0d216bdf4783c4e8 | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:mlx-tiny.en | 5f4dafbb28e62a53c1b10426ff7eed36ca733bf7 | NOASSERTION | manual_only | local_evaluation_only | blocked | blocked_missing_publisher_license |
| model:turbo | 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf | MIT | approved | approved_local | permitted_with_license_terms | verified |

Unknown, conditional, gated, or blocked rows remain unavailable for release unless
the exact machine-readable record contains the required owner-approved evidence.
