# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic configuration binding without persisting private config values."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal

from .config import PipelineConfig, resolve_model_entry
from .redaction import privacy_safe_obj

SOURCE_CONFIGURATION_SCHEMA: Final[Literal["privacy_safe_configuration.v1"]] = (
    "privacy_safe_configuration.v1"
)
SOURCE_CONFIG_SHA256_SCOPE: Final[Literal["complete_runtime_configuration.v1"]] = (
    "complete_runtime_configuration.v1"
)

_PRIVATE_TEXT_FIELDS = frozenset(
    {
        ("asr", "initial_prompt"),
        ("asr", "prefix"),
        ("cleanup", "endpoint"),
        ("cleanup", "proper_nouns"),
        ("speaker_identity", "unattributed_label"),
    }
)

# Names only. Values may contain private paths or credential-bearing URLs.
CONFIGURATION_ENV_NAMES: Final = (
    "LTM_MODEL_CACHE", "LTM_OFFLINE", "LTM_DEVICE", "LTM_ASR_BACKEND",
    "LTM_ASR_MLX_MODEL", "LTM_ASR_MLX_MODEL_PATH", "LTM_ASR_MLX_FP16",
    "LTM_ASR_MLX_VAD_WINDOWED", "LTM_ASR_MLX_VAD_WINDOW_SECONDS",
    "LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS", "LTM_ASR_DEVICE",
    "LTM_DIARIZATION_DEVICE", "LTM_ALIGNMENT_DEVICE", "LTM_MODEL_LIFECYCLE",
    "LTM_ALIGNMENT_ALLOW_EXPERIMENTAL_MPS", "LTM_ASR_CPU_THREADS",
    "LTM_ASR_NUM_WORKERS", "LTM_ASR_BATCHED", "LTM_ASR_BATCH_SIZE",
    "LTM_CLEANUP_ENDPOINT",
)


def configuration_preview(config: PipelineConfig) -> dict[str, Any]:
    """Describe the resolved config without loading models or revealing private values."""
    projection, digest = configuration_evidence(config)
    return {
        "schema_version": "effective_configuration_preview.v1",
        "source_configuration_schema": SOURCE_CONFIGURATION_SCHEMA,
        "source_config_sha256_scope": SOURCE_CONFIG_SHA256_SCOPE,
        "source_config_sha256": digest,
        "source_configuration": privacy_safe_obj(projection),
        "present_environment_overrides": sorted(
            name for name in CONFIGURATION_ENV_NAMES if name in os.environ
        ),
        "precedence": ["preset", "yaml", "environment", "explicit_cli"],
        "inference_performed": False,
        "models_verified": False,
        "media_probed": False,
    }


def configuration_evidence(config: PipelineConfig) -> tuple[dict[str, Any], str]:
    """Return a privacy-safe projection and hash the complete in-memory config.

    The projection is suitable for persistent local metadata. The SHA-256 is
    deliberately computed from the complete JSON configuration before
    projection so canary comparisons retain exact binding without copying
    prompts, credentials, or local paths into artifacts.
    """

    complete_payload = config.model_dump(mode="json")
    encoded = json.dumps(
        complete_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    safe_payload = _privacy_safe_config_value(
        config.model_dump(mode="python"),
        field_path=(),
    )
    if not isinstance(safe_payload, dict):  # pragma: no cover - PipelineConfig invariant
        raise TypeError("Pipeline configuration projection must be an object.")
    return safe_payload, hashlib.sha256(encoded).hexdigest()


def _privacy_safe_config_value(
    value: object,
    *,
    field_path: tuple[str, ...],
) -> Any:
    if isinstance(value, Path):
        return {
            "configured": True,
            "kind": "local_path",
        }
    if (
        field_path == ("alignment", "model")
        and isinstance(value, str)
        and resolve_model_entry("alignment", value) is None
    ):
        return {
            "configured": bool(value),
            "kind": "local_path",
        }
    if field_path in _PRIVATE_TEXT_FIELDS:
        return {
            "configured": bool(value),
            "kind": "private_text",
        }
    if isinstance(value, Mapping):
        return {
            str(key): _privacy_safe_config_value(
                item,
                field_path=(*field_path, str(key)),
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _privacy_safe_config_value(
                item,
                field_path=field_path,
            )
            for item in value
        ]
    return value
