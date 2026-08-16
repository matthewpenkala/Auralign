# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Versioned JSONL bridge between the native macOS GUI and the local engine.

The bridge is a short-lived subprocess protocol, not a network service. Each
invocation runs one command; ``transcribe`` streams pipeline lifecycle events
as JSON Lines on the protocol stream until the job completes or fails.

Protocol hygiene: heavyweight inference backends may write to stdout. The
bridge therefore duplicates the original stdout file descriptor for exclusive
protocol use and redirects fd 1 to stderr before importing or running any
backend code, so stray library output can never corrupt the event stream.
"""

from __future__ import annotations

import json
import math
import os
import signal
import stat
import sys
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import __version__
from .config import (
    MODEL_MANIFEST,
    ModelEntry,
    PipelineConfig,
    PresetName,
    preset_config,
    required_model_requests,
    resolve_model_entry,
)
from .errors import (
    BackendUnavailableError,
    CommandExecutionError,
    LTMError,
    ModelCacheError,
    OfflineModeError,
)
from .platform_security import (
    UnsafePathError,
    ensure_private_directory,
    reject_path_indirection,
)
from .redaction import privacy_safe_obj, privacy_safe_text
from .schemas import QAReport

BRIDGE_PROTOCOL_VERSION = "auralign_gui_bridge.v1"
MANIFEST_SCHEMA_VERSION = "gui_config_manifest.v1"
MODEL_STATUS_SCHEMA_VERSION = "gui_model_status.v1"
JOB_RESULT_SCHEMA_VERSION = "gui_job_result.v1"
JOB_ACCEPTANCE_SCHEMA_VERSION = "gui_job_acceptance.v1"

MAX_RESULT_ARTIFACTS = 512
MAX_RESULT_DIRECTORY_ENTRIES = 1_024
MAX_RESULT_ARTIFACT_NAME_BYTES = 255
MAX_RESULT_COUNT = 1_000_000_000
MAX_SIGNED_64_BIT_INTEGER = (1 << 63) - 1
MAX_RESULT_QA_BYTES = 1024 * 1024
MAX_RESULT_QA_FLAGS = 128
MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES = 64 * 1024
MAX_STDIN_PAYLOAD_BYTES = 1024 * 1024

BRIDGE_COMMANDS = (
    "handshake",
    "schema",
    "presets",
    "validate",
    "doctor",
    "models",
    "probe",
    "transcribe",
)

_REQUEST_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "handshake": (frozenset(), frozenset()),
    "schema": (frozenset(), frozenset()),
    "presets": (frozenset(), frozenset()),
    "validate": (frozenset({"config"}), frozenset({"config"})),
    "doctor": (
        frozenset({"config"}),
        frozenset({"config", "startup"}),
    ),
    "models": (
        frozenset(),
        frozenset({"model_cache", "config"}),
    ),
    "probe": (frozenset({"path"}), frozenset({"path"})),
    "transcribe": (
        frozenset({"input", "output_root", "out_dir", "config"}),
        frozenset({"input", "output_root", "out_dir", "config", "debug_report"}),
    ),
}

#: Fields whose string value selects an entry from the model manifest role.
MODEL_ROLE_FIELDS: dict[str, str] = {
    "asr.model": "asr",
    "asr.mlx_model": "asr-mlx",
    "diarization.model": "diarization",
    "alignment.model": "alignment",
    "speaker_id.model": "speaker-embedding",
}


class BridgeUsageError(LTMError):
    """Raised for malformed bridge invocations or payloads."""


class ResultContractError(LTMError):
    """Raised when a completed pipeline cannot publish trustworthy result facts."""


@dataclass
class AcceptedOutputDirectory:
    """Fresh output identity accepted before the pipeline receives the path."""

    root: Path
    path: Path
    root_identity: ResultFileIdentity
    output_identity: ResultFileIdentity
    root_descriptor: int | None
    output_descriptor: int | None

    def close(self) -> None:
        for descriptor in (self.output_descriptor, self.root_descriptor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.output_descriptor = None
        self.root_descriptor = None


class ModelStatusCompleteness(BaseModel):
    """Strict versioned coverage declaration for GUI model-status rows."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["gui_model_status.v1"] = "gui_model_status.v1"
    complete: bool
    expected_ids: list[str]
    observed_ids: list[str]
    expected_count: int
    observed_count: int

    @model_validator(mode="after")
    def validate_completeness(self) -> ModelStatusCompleteness:
        for label, identifiers in (
            ("expected_ids", self.expected_ids),
            ("observed_ids", self.observed_ids),
        ):
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"model status {label} contains duplicate identifiers")
            if identifiers != sorted(identifiers):
                raise ValueError(f"model status {label} is not canonically ordered")
        if self.expected_count != len(self.expected_ids):
            raise ValueError("model status expected_count does not match expected_ids")
        if self.observed_count != len(self.observed_ids):
            raise ValueError("model status observed_count does not match observed_ids")
        if not set(self.observed_ids).issubset(self.expected_ids):
            raise ValueError("model status observed_ids contains an unexpected identifier")
        if self.complete != (self.observed_ids == self.expected_ids):
            raise ValueError("model status complete does not match observed row coverage")
        return self


class ResultFileIdentity(BaseModel):
    """Stable local filesystem identity carried by one result envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)

    device: int = Field(ge=0, le=MAX_SIGNED_64_BIT_INTEGER)
    inode: int = Field(ge=0, le=MAX_SIGNED_64_BIT_INTEGER)


class ResultArtifact(BaseModel):
    """One immediate, regular, non-linked file in a completed job directory."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    size_bytes: int = Field(ge=0, le=MAX_SIGNED_64_BIT_INTEGER)
    identity: ResultFileIdentity

    @model_validator(mode="after")
    def validate_name(self) -> ResultArtifact:
        encoded = self.name.encode("utf-8")
        if (
            not encoded
            or len(encoded) > MAX_RESULT_ARTIFACT_NAME_BYTES
            or self.name in {".", ".."}
            or self.name.startswith(".")
            or "/" in self.name
            or "\x00" in self.name
        ):
            raise ValueError("result artifact has an unsafe name")
        return self


class ResultQAFlag(BaseModel):
    """One bounded, schema-derived deterministic QA finding."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str
    severity: Literal["info", "warning", "error"]
    message: str

    @model_validator(mode="after")
    def validate_text(self) -> ResultQAFlag:
        if (
            not self.code
            or len(self.code.encode("utf-8")) > 128
            or len(self.message.encode("utf-8")) > 1_024
        ):
            raise ValueError("result QA flag text is invalid")
        return self


class ResultQAProjection(BaseModel):
    """Bounded projection of the real QAReport schema."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["available", "disabled", "missing", "invalid", "oversized"]
    source_artifact: Literal["qa_report.json"] = "qa_report.json"
    byte_limit: Literal[1048576] = 1048576
    overall_quality_summary: Literal["no_deterministic_warnings", "review_warnings"] | None
    flag_count: int | None = Field(default=None, ge=0, le=MAX_RESULT_COUNT)
    error_flag_count: int | None = Field(default=None, ge=0, le=MAX_RESULT_COUNT)
    warning_flag_count: int | None = Field(default=None, ge=0, le=MAX_RESULT_COUNT)
    info_flag_count: int | None = Field(default=None, ge=0, le=MAX_RESULT_COUNT)
    changed_span_count: int | None = Field(default=None, ge=0, le=MAX_RESULT_COUNT)
    flags: list[ResultQAFlag] = Field(default_factory=list, max_length=MAX_RESULT_QA_FLAGS)
    omitted_flag_count: int = Field(default=0, ge=0, le=MAX_RESULT_COUNT)

    @model_validator(mode="after")
    def validate_status(self) -> ResultQAProjection:
        counts = (
            self.flag_count,
            self.error_flag_count,
            self.warning_flag_count,
            self.info_flag_count,
            self.changed_span_count,
        )
        if self.status == "available":
            if self.overall_quality_summary is None or any(value is None for value in counts):
                raise ValueError("available QA evidence is incomplete")
            assert self.flag_count is not None
            assert self.error_flag_count is not None
            assert self.warning_flag_count is not None
            assert self.info_flag_count is not None
            if (
                self.error_flag_count + self.warning_flag_count + self.info_flag_count
                != self.flag_count
            ):
                raise ValueError("QA severity counts do not match the flag count")
            projected_error_count = sum(flag.severity == "error" for flag in self.flags)
            projected_warning_count = sum(flag.severity == "warning" for flag in self.flags)
            projected_info_count = sum(flag.severity == "info" for flag in self.flags)
            if (
                projected_error_count > self.error_flag_count
                or projected_warning_count > self.warning_flag_count
                or projected_info_count > self.info_flag_count
            ):
                raise ValueError("QA projected severities exceed their totals")
            if self.flag_count != len(self.flags) + self.omitted_flag_count:
                raise ValueError("QA omitted flag count is inconsistent")
            expected_overall = "review_warnings" if self.flag_count else "no_deterministic_warnings"
            if self.overall_quality_summary != expected_overall:
                raise ValueError("QA overall summary is inconsistent")
        elif self.status == "disabled":
            if (
                self.overall_quality_summary is not None
                or counts != (0, 0, 0, 0, 0)
                or self.flags
                or self.omitted_flag_count
            ):
                raise ValueError("disabled QA evidence contains unsupported facts")
        elif (
            self.overall_quality_summary is not None
            or any(value is not None for value in counts)
            or self.flags
            or self.omitted_flag_count
        ):
            raise ValueError("unavailable QA evidence contains unsupported facts")
        return self


class ResultTranscriptPreview(BaseModel):
    """Bounded UTF-8 preview metadata for transcript.txt."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["available", "missing", "empty"]
    source_artifact: Literal["transcript.txt"] = "transcript.txt"
    byte_limit: Literal[65536] = 65536
    text: str | None
    displayed_bytes: int = Field(ge=0, le=MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES)
    source_bytes: int | None = Field(default=None, ge=0, le=MAX_SIGNED_64_BIT_INTEGER)
    truncated: bool

    @model_validator(mode="after")
    def validate_status(self) -> ResultTranscriptPreview:
        if self.status == "available":
            if (
                not self.text
                or self.source_bytes is None
                or self.displayed_bytes != len(self.text.encode("utf-8"))
                or self.truncated != (self.displayed_bytes < self.source_bytes)
            ):
                raise ValueError("available transcript preview is inconsistent")
        elif self.status == "empty":
            if (
                self.text != ""
                or self.displayed_bytes != 0
                or self.source_bytes != 0
                or self.truncated
            ):
                raise ValueError("empty transcript preview is inconsistent")
        elif self.status == "missing":
            if (
                self.text is not None
                or self.displayed_bytes != 0
                or self.source_bytes is not None
                or self.truncated
            ):
                raise ValueError("missing transcript preview is inconsistent")
        return self


class JobResultEnvelope(BaseModel):
    """Strict result facts published only after the pipeline function returns."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["gui_job_result.v1"] = "gui_job_result.v1"
    output_directory: str
    output_identity: ResultFileIdentity
    artifact_scope: Literal["immediate_regular_files"] = "immediate_regular_files"
    artifact_count: int = Field(ge=0, le=MAX_RESULT_ARTIFACTS)
    language: str | None
    speaker_turns: int = Field(ge=0, le=MAX_RESULT_COUNT)
    words: int = Field(ge=0, le=MAX_RESULT_COUNT)
    segments: int = Field(ge=0, le=MAX_RESULT_COUNT)
    artifacts: list[ResultArtifact] = Field(max_length=MAX_RESULT_ARTIFACTS)
    qa: ResultQAProjection
    transcript_preview: ResultTranscriptPreview

    @model_validator(mode="after")
    def validate_envelope(self) -> JobResultEnvelope:
        path = Path(self.output_directory)
        if not path.is_absolute():
            raise ValueError("result output directory must be absolute")
        names = [artifact.name for artifact in self.artifacts]
        if self.artifact_count != len(self.artifacts):
            raise ValueError("result artifact_count does not match artifacts")
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("result artifacts must be sorted and unique")
        if self.language is not None:
            if (
                not self.language
                or len(self.language.encode("utf-8")) > 32
                or not all(
                    character.isalnum() or character in {"-", "_"} for character in self.language
                )
            ):
                raise ValueError("result language is invalid")
        return self


def _annotation_spec(annotation: Any) -> dict[str, Any]:
    """Classify one field annotation into a GUI-consumable kind."""

    optional = False
    origin = typing.get_origin(annotation)
    if origin in {typing.Union, types.UnionType}:
        args = [arg for arg in typing.get_args(annotation) if arg is not types.NoneType]
        optional = len(args) < len(typing.get_args(annotation))
        if len(args) == 1:
            annotation = args[0]
            origin = typing.get_origin(annotation)
        elif {float, list[float]} == set(args):
            return {"kind": "float_or_float_list", "optional": optional}
        else:
            raise BridgeUsageError(f"Unsupported union annotation: {annotation}")
    if origin is Literal:
        values = [str(value) for value in typing.get_args(annotation)]
        return {"kind": "enum", "optional": optional, "choices": values}
    if origin is list:
        (item,) = typing.get_args(annotation)
        if item is str:
            return {"kind": "string_list", "optional": optional}
        if item is Path:
            return {"kind": "path_list", "optional": optional}
        if item is float:
            return {"kind": "float_list", "optional": optional}
        raise BridgeUsageError(f"Unsupported list annotation: {annotation}")
    if annotation is bool:
        return {"kind": "bool", "optional": optional}
    if annotation is int:
        return {"kind": "int", "optional": optional}
    if annotation is float:
        return {"kind": "float", "optional": optional}
    if annotation is str:
        return {"kind": "string", "optional": optional}
    if annotation is Path:
        return {"kind": "path", "optional": optional}
    raise BridgeUsageError(f"Unsupported annotation: {annotation}")


def _constraint_values(metadata: list[Any]) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    for item in metadata:
        for attribute in ("ge", "le", "gt", "lt", "max_length", "min_length"):
            value = getattr(item, attribute, None)
            if value is not None:
                constraints[attribute] = value
    return constraints


def _walk_fields(
    model: type[BaseModel],
    defaults: dict[str, Any],
    prefix: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for name, info in model.model_fields.items():
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            fields.extend(_walk_fields(annotation, defaults[name], (*prefix, name)))
            continue
        spec = _annotation_spec(annotation)
        key = ".".join((*prefix, name))
        entry: dict[str, Any] = {"key": key, **spec, "default": defaults[name]}
        entry.update(_constraint_values(list(info.metadata)))
        if key in MODEL_ROLE_FIELDS:
            entry["model_role"] = MODEL_ROLE_FIELDS[key]
        fields.append(entry)
    return fields


def config_manifest() -> dict[str, Any]:
    """Return the deterministic, environment-independent configuration manifest."""

    defaults = PipelineConfig().model_dump(mode="json")
    models = [_model_contract(entry) for entry in MODEL_MANIFEST.values()]
    fields = _walk_fields(PipelineConfig, defaults)
    for field in fields:
        if field["key"] == "cleanup.mode":
            # The native macOS 14+ standalone intentionally excludes the
            # macOS 26+/Xcode 26 Apple Foundation Models SDK. Keep that
            # source/headless-only backend out of the product picker rather
            # than exposing an option the bundled engine cannot execute.
            field["choices"] = ["off", "local-llm"]
        elif field["key"] == "cleanup.presentation_scorers.token_classifier.mode":
            # The executable word-model adapter has no cleared production
            # checkpoint. Keep the bundled schema aligned with the validator:
            # source and product users may select only the truthful off state
            # until a model passes provenance and measured-quality gates.
            field["choices"] = ["off"]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "package_version": __version__,
        "fields": fields,
        "model_manifest": models,
    }


def preset_payload() -> dict[str, Any]:
    """Return environment-aware effective baselines for every preset."""

    from .pipeline import environment_overrides

    presets: dict[str, Any] = {}
    for name in typing.get_args(PresetName):
        presets[name] = preset_config(name).model_dump(mode="json")
    return {
        "presets": presets,
        "environment_overrides": environment_overrides(),
    }


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _require_request_shape("validate", payload)
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise BridgeUsageError("validate payload requires a 'config' object")
    try:
        config = PipelineConfig.model_validate(raw_config)
    except ValidationError as exc:
        return {
            "ok": False,
            "errors": [
                {
                    "loc": [str(part) for part in error["loc"]],
                    "message": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors(include_url=False, include_input=False)
            ],
        }
    policy_errors = _product_model_policy_errors(config)
    if policy_errors:
        return {"ok": False, "errors": policy_errors}
    return {"ok": True, "config": config.model_dump(mode="json")}


def doctor_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _require_request_shape("doctor", payload)
    from .doctor import run_doctor

    config = _require_config(payload)
    startup_value = payload.get("startup", False)
    if not isinstance(startup_value, bool):
        raise BridgeUsageError("doctor 'startup' must be a boolean")
    startup = startup_value
    report = run_doctor(config, include_docker=False, startup=startup)
    # The GUI renders and copies this payload directly. Keep it shareable by
    # default: status remains exact, while credentials and machine-local paths
    # are removed at the bridge boundary.
    safe_report = privacy_safe_obj(report.model_dump(mode="json"))
    if not isinstance(safe_report, dict):  # pragma: no cover - defensive contract guard
        raise BridgeUsageError("doctor returned an invalid report")
    return safe_report


def models_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _require_request_shape("models", payload)
    from .models import all_statuses

    cache_value = payload.get("model_cache")
    if cache_value is None or cache_value == "":
        cache_dir = Path("models")
    elif isinstance(cache_value, str):
        cache_dir = Path(cache_value).expanduser()
    else:
        raise BridgeUsageError("models 'model_cache' must be a path string")
    selected = _selected_model_ids(payload)
    model_statuses = sorted(
        all_statuses(cache_dir),
        key=lambda status: _model_status_id(status.entry),
    )
    statuses = []
    for status in model_statuses:
        statuses.append(
            {
                **_model_contract(status.entry),
                "status_id": _model_status_id(status.entry),
                "present": status.present,
                "partial": status.partial,
                "revision_verified": status.revision_verified,
                "selected_for_config": (status.entry.role, status.entry.key) in selected,
                "problem": None
                if status.present
                else status.problem_summary(cache_dir, shareable=True),
            }
        )
    expected_ids = sorted(_model_status_id(entry) for entry in MODEL_MANIFEST.values())
    observed_ids = [_model_status_id(status.entry) for status in model_statuses]
    completeness = ModelStatusCompleteness(
        complete=observed_ids == expected_ids,
        expected_ids=expected_ids,
        observed_ids=observed_ids,
        expected_count=len(expected_ids),
        observed_count=len(observed_ids),
    )
    return {
        **completeness.model_dump(mode="json"),
        "model_cache": privacy_safe_text(str(cache_dir)),
        "models": statuses,
    }


def _model_status_id(entry: ModelEntry) -> str:
    return f"{entry.role}:{entry.key}"


def _model_contract(entry: ModelEntry) -> dict[str, Any]:
    """Return authoritative, GUI-safe model provenance and policy metadata."""

    return {
        "key": entry.key,
        "role": entry.role,
        "model_id": entry.model_id,
        "aliases": list(entry.aliases),
        "directory_name": entry.directory_name,
        "required_files": list(entry.required_files),
        "any_required_files": list(entry.any_required_files),
        "runtime_files": list(entry.runtime_files),
        "expected_sha256": entry.expected_digest_map,
        "expected_size": entry.expected_size_map,
        "gated": entry.gated,
        "optional": entry.optional,
        "pullable": entry.pullable,
        "revision": entry.revision,
        "license_expression": entry.license_expression,
        "acquisition_status": entry.acquisition_status,
        "runtime_status": entry.runtime_status,
        "redistribution_status": entry.redistribution_status,
        "review_status": entry.review_status,
        "reviewed_at": entry.reviewed_at,
        "product_classification": entry.product_classification,
        "product_selectable": entry.production_selectable,
        "blocker": entry.blocker,
        "backend_compatibility": entry.backend_compatibility,
        "modification_status": entry.modification_status,
        "package_bundle_id": entry.package_bundle_id,
        "package_bundle_version": entry.package_bundle_version,
        "package_relative_path": entry.package_relative_path,
        "delivery": entry.delivery,
        "source_url": entry.source_url,
        "terms_url": entry.terms_url,
        "attribution": entry.attribution,
    }


def _product_model_policy_errors(config: PipelineConfig) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if config.asr.mlx_model_path is not None:
        errors.append(
            {
                "loc": ["model_policy", "asr-mlx", "path"],
                "message": (
                    "The production product resolves MLX models only through its exact "
                    "verified native model-pack contract; arbitrary model paths are not "
                    "accepted."
                ),
                "type": "model_policy_blocked",
            }
        )
    if config.asr.vad_filter:
        errors.append(
            {
                "loc": ["model_policy", "asr", "vad_filter"],
                "message": (
                    "asr.vad_filter is unavailable in the production product because "
                    "its upstream Silero model is not part of the verified model "
                    "component; Auralign's local EnergyVAD remains active."
                ),
                "type": "model_policy_blocked",
            }
        )
    for role, value in required_model_requests(config):
        entry = resolve_model_entry(role, value)
        if entry is None:
            errors.append(
                {
                    "loc": ["model_policy", role],
                    "message": f"Unknown {role} model '{value}'.",
                    "type": "model_policy_unknown",
                }
            )
            continue
        if not entry.production_selectable:
            blocker = entry.blocker or "release-critical model authority is incomplete"
            errors.append(
                {
                    "loc": ["model_policy", role],
                    "message": (
                        f"{role} model '{entry.key}' is unavailable in the production "
                        f"product: {blocker}"
                    ),
                    "type": "model_policy_blocked",
                }
            )
    return errors


def _selected_model_ids(payload: dict[str, Any]) -> set[tuple[str, str]]:
    """Resolve model entries required by an optional effective configuration."""

    if "config" not in payload:
        return set()
    config = _require_config(payload)
    selected: set[tuple[str, str]] = set()
    for role, value in required_model_requests(config):
        entry = resolve_model_entry(role, value)
        if entry is not None:
            selected.add((entry.role, entry.key))
    return selected


def probe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _require_request_shape("probe", payload)
    from .audio.inspect import inspect_media

    path_value = payload.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise BridgeUsageError("probe payload requires a 'path'")
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise BridgeUsageError("probe path does not exist or is not a regular file")
    metadata = inspect_media(path)
    return {
        "duration_seconds": metadata.duration_seconds,
        "sample_rate": metadata.sample_rate,
        "channels": metadata.channels,
        "codec": metadata.codec,
        "format_name": metadata.format_name,
    }


def _require_request_shape(command: str, payload: dict[str, Any]) -> None:
    """Reject missing or extra request fields before any command side effect."""

    required, allowed = _REQUEST_KEYS[command]
    observed = frozenset(payload)
    missing = sorted(required - observed)
    if missing:
        raise BridgeUsageError(f"{command} payload requires: {', '.join(missing)}")
    if not observed.issubset(allowed):
        raise BridgeUsageError(f"{command} payload contains unsupported fields")


def _configuration_leaf_keys(
    value: object,
    prefix: tuple[str, ...] = (),
) -> set[str]:
    if not isinstance(value, dict):
        return {".".join(prefix)} if prefix else set()
    keys: set[str] = set()
    for key, child in value.items():
        if not isinstance(key, str):
            continue
        path = (*prefix, key)
        if isinstance(child, dict):
            keys.update(_configuration_leaf_keys(child, path))
        else:
            keys.add(".".join(path))
    return keys


def _require_complete_effective_config(raw_config: dict[str, Any]) -> None:
    expected = {field["key"] for field in config_manifest()["fields"]}
    if _configuration_leaf_keys(raw_config) != expected:
        raise BridgeUsageError(
            "transcribe 'config' must contain every effective configuration field"
        )


def _require_config(payload: dict[str, Any]) -> PipelineConfig:
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise BridgeUsageError("payload requires a 'config' object")
    try:
        return PipelineConfig.model_validate(raw_config)
    except ValidationError as exc:
        details = "; ".join(
            ".".join(str(part) for part in error["loc"]) + ": " + error["msg"]
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise BridgeUsageError(f"invalid pipeline configuration: {details}") from exc


def _filesystem_identity(metadata: os.stat_result) -> ResultFileIdentity:
    """Project native metadata into the signed-64-bit bridge contract."""

    try:
        return ResultFileIdentity(device=int(metadata.st_dev), inode=int(metadata.st_ino))
    except ValidationError as exc:
        raise BridgeUsageError("result filesystem identity is unsupported") from exc


def _completed_filesystem_identity(metadata: os.stat_result) -> ResultFileIdentity:
    """Convert identity failures during publication into the result error lane."""

    try:
        return _filesystem_identity(metadata)
    except BridgeUsageError as exc:
        raise ResultContractError("completed filesystem identity is unsupported") from exc


def _prepare_output_directory(
    output_root_value: object,
    output_value: object,
) -> AcceptedOutputDirectory:
    """Create one fresh direct child and retain its accepted identity."""

    if (
        not isinstance(output_root_value, str)
        or not output_root_value
        or not isinstance(output_value, str)
        or not output_value
    ):
        raise BridgeUsageError("transcribe payload requires 'output_root' and 'out_dir'")
    raw_root = Path(output_root_value).expanduser()
    raw_output = Path(output_value).expanduser()
    if not raw_root.is_absolute() or not raw_output.is_absolute():
        raise BridgeUsageError("transcribe output paths must be absolute")
    try:
        root = reject_path_indirection(raw_root)
        output = reject_path_indirection(raw_output)
    except (OSError, UnsafePathError) as exc:
        raise BridgeUsageError("transcribe output paths contain unsafe indirection") from exc
    if (
        output.parent != root
        or not output.name
        or output.name in {".", ".."}
        or "/" in output.name
        or "\x00" in output.name
    ):
        raise BridgeUsageError("transcribe out_dir must be one fresh child of output_root")
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise BridgeUsageError("transcribe output_root is unavailable") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise BridgeUsageError("transcribe output_root must be a non-linked directory")

    if os.name == "nt":
        try:
            output.mkdir(mode=0o700, parents=False, exist_ok=False)
            ensure_private_directory(output)
            output_metadata = output.lstat()
        except (OSError, UnsafePathError) as exc:
            raise BridgeUsageError("transcribe out_dir must be fresh and private") from exc
        return AcceptedOutputDirectory(
            root=root,
            path=output,
            root_identity=_filesystem_identity(root_metadata),
            output_identity=_filesystem_identity(output_metadata),
            root_descriptor=None,
            output_descriptor=None,
        )

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise BridgeUsageError("transcribe output_root could not be opened safely") from exc
    output_descriptor: int | None = None
    try:
        opened_root_metadata = os.fstat(root_descriptor)
        if not stat.S_ISDIR(opened_root_metadata.st_mode) or (
            opened_root_metadata.st_dev,
            opened_root_metadata.st_ino,
        ) != (root_metadata.st_dev, root_metadata.st_ino):
            raise BridgeUsageError("transcribe output_root changed during authorization")
        try:
            os.mkdir(output.name, mode=0o700, dir_fd=root_descriptor)
            output_descriptor = os.open(
                output.name,
                directory_flags,
                dir_fd=root_descriptor,
            )
        except OSError as exc:
            raise BridgeUsageError("transcribe out_dir must be fresh and private") from exc
        os.fchmod(output_descriptor, 0o700)
        output_metadata = os.fstat(output_descriptor)
        try:
            path_metadata = output.lstat()
        except OSError as exc:
            raise BridgeUsageError("transcribe out_dir could not be verified") from exc
        if (
            not stat.S_ISDIR(output_metadata.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
            or (output_metadata.st_dev, output_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise BridgeUsageError("transcribe out_dir changed during authorization")
        return AcceptedOutputDirectory(
            root=root,
            path=output,
            root_identity=_filesystem_identity(opened_root_metadata),
            output_identity=_filesystem_identity(output_metadata),
            root_descriptor=root_descriptor,
            output_descriptor=output_descriptor,
        )
    except BaseException:
        if output_descriptor is not None:
            os.close(output_descriptor)
        os.close(root_descriptor)
        raise


def _result_language(value: object) -> str | None:
    """Return one bounded language tag and reject malformed non-null evidence."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ResultContractError("completed job language evidence is invalid")
    if (
        not value
        or len(value.encode("utf-8")) > 32
        or not all(character.isalnum() or character in {"-", "_"} for character in value)
    ):
        raise ResultContractError("completed job language evidence is invalid")
    return value


def _verify_accepted_output(accepted: AcceptedOutputDirectory) -> os.stat_result:
    """Verify retained and pathname identities still name the accepted directories."""

    try:
        root_path_metadata = accepted.root.lstat()
        output_path_metadata = accepted.path.lstat()
        if accepted.root_descriptor is not None:
            root_metadata = os.fstat(accepted.root_descriptor)
        else:
            root_metadata = root_path_metadata
        if accepted.output_descriptor is not None:
            output_metadata = os.fstat(accepted.output_descriptor)
        else:
            output_metadata = output_path_metadata
    except OSError as exc:
        raise ResultContractError("completed job directory authority is unavailable") from exc
    root_identity = _completed_filesystem_identity(root_metadata)
    output_identity = _completed_filesystem_identity(output_metadata)
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or not stat.S_ISDIR(output_metadata.st_mode)
        or stat.S_ISLNK(root_path_metadata.st_mode)
        or stat.S_ISLNK(output_path_metadata.st_mode)
        or root_identity != accepted.root_identity
        or output_identity != accepted.output_identity
        or _completed_filesystem_identity(root_path_metadata) != accepted.root_identity
        or _completed_filesystem_identity(output_path_metadata) != accepted.output_identity
    ):
        raise ResultContractError("completed job directory identity changed")
    return output_metadata


def _open_result_artifact(
    accepted: AcceptedOutputDirectory,
    artifact: ResultArtifact,
) -> int:
    """Open one declared artifact without following links and revalidate identity."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        if accepted.output_descriptor is not None:
            metadata = os.stat(
                artifact.name,
                dir_fd=accepted.output_descriptor,
                follow_symlinks=False,
            )
            descriptor = os.open(
                artifact.name,
                flags,
                dir_fd=accepted.output_descriptor,
            )
        else:
            candidate = accepted.path / artifact.name
            metadata = candidate.lstat()
            descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ResultContractError("completed job artifact changed during publication") from exc
    try:
        opened_metadata = os.fstat(descriptor)
        expected = artifact.identity
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or not stat.S_ISREG(opened_metadata.st_mode)
            or _completed_filesystem_identity(metadata) != expected
            or _completed_filesystem_identity(opened_metadata) != expected
            or metadata.st_size != artifact.size_bytes
            or opened_metadata.st_size != artifact.size_bytes
        ):
            raise ResultContractError("completed job artifact changed during publication")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_result_artifact_prefix(
    accepted: AcceptedOutputDirectory,
    artifact: ResultArtifact,
    maximum_bytes: int,
) -> bytes:
    """Read an exact bounded prefix through a revalidated no-follow descriptor."""

    descriptor = _open_result_artifact(accepted, artifact)
    expected_bytes = min(artifact.size_bytes, maximum_bytes)
    chunks: list[bytes] = []
    remaining = expected_bytes
    try:
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        final_metadata = os.fstat(descriptor)
        if (
            remaining
            or _completed_filesystem_identity(final_metadata) != artifact.identity
            or final_metadata.st_size != artifact.size_bytes
        ):
            raise ResultContractError("completed job artifact changed while it was being read")
    except OSError as exc:
        raise ResultContractError("completed job artifact could not be read safely") from exc
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _result_qa_projection(
    accepted: AcceptedOutputDirectory,
    artifacts: dict[str, ResultArtifact],
    *,
    enabled: bool,
) -> ResultQAProjection:
    """Project the real QA schema without exposing evidence or transcript content."""

    artifact = artifacts.get("qa_report.json")
    if not enabled:
        if artifact is None or artifact.size_bytes > MAX_RESULT_QA_BYTES:
            raise ResultContractError("disabled QA did not publish its expected empty report")
        data = _read_result_artifact_prefix(accepted, artifact, MAX_RESULT_QA_BYTES)
        try:
            disabled_report = QAReport.model_validate_json(data, strict=True)
        except ValidationError as exc:
            raise ResultContractError("disabled QA report is malformed") from exc
        if disabled_report.flags or disabled_report.changed_spans or disabled_report.summary:
            raise ResultContractError("disabled QA report contains unsupported evidence")
        return ResultQAProjection(
            status="disabled",
            overall_quality_summary=None,
            flag_count=0,
            error_flag_count=0,
            warning_flag_count=0,
            info_flag_count=0,
            changed_span_count=0,
        )
    if artifact is None:
        return ResultQAProjection(status="missing", overall_quality_summary=None)
    if artifact.size_bytes > MAX_RESULT_QA_BYTES:
        return ResultQAProjection(status="oversized", overall_quality_summary=None)
    data = _read_result_artifact_prefix(accepted, artifact, MAX_RESULT_QA_BYTES)
    try:
        report = QAReport.model_validate_json(data, strict=True)
        flag_count = len(report.flags)
        error_count = sum(flag.severity == "error" for flag in report.flags)
        warning_count = sum(flag.severity == "warning" for flag in report.flags)
        info_count = sum(flag.severity == "info" for flag in report.flags)
        changed_span_count = len(report.changed_spans)
        if any(
            count > MAX_RESULT_COUNT
            for count in (
                flag_count,
                error_count,
                warning_count,
                info_count,
                changed_span_count,
            )
        ):
            raise ValueError("QA count exceeds the result contract")

        summary = report.summary

        def require_summary_count(key: str, expected: int) -> None:
            value = summary.get(key)
            if type(value) is not int or value != expected:  # noqa: E721
                raise ValueError(f"QA summary {key} is inconsistent")

        require_summary_count("flag_count", flag_count)
        require_summary_count("error_flag_count", error_count)
        require_summary_count("warning_flag_count", warning_count)
        overall = summary.get("overall_quality_summary")
        expected_overall = "review_warnings" if report.flags else "no_deterministic_warnings"
        if overall != expected_overall:
            raise ValueError("QA overall summary is inconsistent")
        projected = [
            ResultQAFlag(
                code=flag.code,
                severity=flag.severity,
                message=privacy_safe_text(flag.message),
            )
            for flag in report.flags[:MAX_RESULT_QA_FLAGS]
        ]
        return ResultQAProjection(
            status="available",
            overall_quality_summary=overall,
            flag_count=flag_count,
            error_flag_count=error_count,
            warning_flag_count=warning_count,
            info_flag_count=info_count,
            changed_span_count=changed_span_count,
            flags=projected,
            omitted_flag_count=flag_count - len(projected),
        )
    except (ValidationError, ValueError):
        return ResultQAProjection(status="invalid", overall_quality_summary=None)


def _decode_transcript_prefix(data: bytes, *, source_bytes: int) -> str:
    """Decode a UTF-8 prefix, trimming only an incomplete final scalar."""

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as original:
        if (
            source_bytes <= len(data)
            or original.reason != "unexpected end of data"
            or original.start < max(0, len(data) - 3)
        ):
            raise ResultContractError(
                "completed transcript preview is not valid UTF-8"
            ) from original
        for trimmed_count in range(1, min(3, len(data)) + 1):
            try:
                return data[:-trimmed_count].decode("utf-8")
            except UnicodeDecodeError:
                continue
        raise ResultContractError("completed transcript preview is not valid UTF-8") from original


def _result_transcript_preview(
    accepted: AcceptedOutputDirectory,
    artifacts: dict[str, ResultArtifact],
) -> ResultTranscriptPreview:
    artifact = artifacts.get("transcript.txt")
    if artifact is None:
        return ResultTranscriptPreview(
            status="missing",
            text=None,
            displayed_bytes=0,
            source_bytes=None,
            truncated=False,
        )
    if artifact.size_bytes == 0:
        return ResultTranscriptPreview(
            status="empty",
            text="",
            displayed_bytes=0,
            source_bytes=0,
            truncated=False,
        )
    data = _read_result_artifact_prefix(
        accepted,
        artifact,
        MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES,
    )
    text = _decode_transcript_prefix(data, source_bytes=artifact.size_bytes)
    displayed_bytes = len(text.encode("utf-8"))
    return ResultTranscriptPreview(
        status="available",
        text=text,
        displayed_bytes=displayed_bytes,
        source_bytes=artifact.size_bytes,
        truncated=displayed_bytes < artifact.size_bytes,
    )


def _completed_result_envelope(
    accepted: AcceptedOutputDirectory,
    transcript: Any,
    *,
    qa_enabled: bool,
) -> JobResultEnvelope:
    """Snapshot completed facts through the retained output authority."""

    directory_metadata = _verify_accepted_output(accepted)
    artifacts: list[ResultArtifact] = []
    entry_count = 0
    scan_target: int | Path = (
        accepted.output_descriptor if accepted.output_descriptor is not None else accepted.path
    )
    try:
        entries = os.scandir(scan_target)
    except OSError as exc:
        raise ResultContractError("completed job artifacts could not be enumerated") from exc
    with entries:
        for entry in entries:
            entry_count += 1
            if entry_count > MAX_RESULT_DIRECTORY_ENTRIES:
                raise ResultContractError("completed job contains too many entries")
            name = entry.name
            if name.startswith("."):
                continue
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ResultContractError(
                    "completed job contains an unreadable artifact entry"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ResultContractError("completed job contains an unsafe artifact link")
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ResultContractError("completed job contains an unsafe artifact type")
            if len(artifacts) >= MAX_RESULT_ARTIFACTS:
                raise ResultContractError("completed job contains too many file artifacts")
            try:
                artifact = ResultArtifact(
                    name=name,
                    size_bytes=metadata.st_size,
                    identity=_completed_filesystem_identity(metadata),
                )
            except (UnicodeError, ValidationError) as exc:
                raise ResultContractError("completed job contains an invalid artifact") from exc
            descriptor = _open_result_artifact(accepted, artifact)
            os.close(descriptor)
            artifacts.append(artifact)

    artifacts.sort(key=lambda artifact: artifact.name)
    artifact_map = {artifact.name: artifact for artifact in artifacts}
    try:
        envelope = JobResultEnvelope(
            output_directory=str(accepted.path),
            output_identity=_completed_filesystem_identity(directory_metadata),
            artifact_count=len(artifacts),
            language=_result_language(getattr(transcript, "language", None)),
            speaker_turns=len(transcript.speaker_turns),
            words=len(transcript.words),
            segments=len(transcript.segments),
            artifacts=artifacts,
            qa=_result_qa_projection(
                accepted,
                artifact_map,
                enabled=qa_enabled,
            ),
            transcript_preview=_result_transcript_preview(
                accepted,
                artifact_map,
            ),
        )
    except (AttributeError, TypeError, ValidationError) as exc:
        raise ResultContractError("completed job result facts are invalid") from exc
    _verify_accepted_output(accepted)
    return envelope


def run_transcribe(
    payload: dict[str, Any],
    emit: Callable[[str, dict[str, Any]], None],
    *,
    transcribe: Callable[..., Any] | None = None,
) -> int:
    """Run one transcription job, streaming lifecycle events through ``emit``."""

    _require_request_shape("transcribe", payload)
    if transcribe is None:
        from .pipeline import transcribe_file

        run_pipeline: Callable[..., Any] = transcribe_file
    else:
        run_pipeline = transcribe

    input_value = payload["input"]
    out_value = payload["out_dir"]
    if not isinstance(input_value, str):
        raise BridgeUsageError("transcribe payload requires 'input'")
    debug_report = payload.get("debug_report", False)
    if not isinstance(debug_report, bool):
        raise BridgeUsageError("transcribe 'debug_report' must be a boolean")
    input_file = Path(input_value).expanduser()
    if not input_file.is_file():
        raise BridgeUsageError("transcribe input does not exist or is not a regular file")
    raw_config = payload["config"]
    if not isinstance(raw_config, dict) or not raw_config:
        raise BridgeUsageError("transcribe 'config' must be a nonempty object")
    _require_complete_effective_config(raw_config)
    try:
        config = PipelineConfig.model_validate(raw_config)
    except ValidationError as exc:
        emit(
            "config_invalid",
            {
                "errors": [
                    {
                        "loc": [str(part) for part in error["loc"]],
                        "message": error["msg"],
                        "type": error["type"],
                    }
                    for error in exc.errors(include_url=False, include_input=False)
                ]
            },
        )
        return 2
    policy_errors = _product_model_policy_errors(config)
    if policy_errors:
        emit("config_invalid", {"errors": policy_errors})
        return 2
    accepted = _prepare_output_directory(payload.get("output_root"), out_value)
    debug_report_path = accepted.path / "debug_report.json" if debug_report else None
    try:
        emit(
            "job_accepted",
            {
                "schema_version": JOB_ACCEPTANCE_SCHEMA_VERSION,
                "output_directory": str(accepted.path),
                "output_identity": accepted.output_identity.model_dump(mode="json"),
            },
        )
        try:

            def safe_progress(
                event: str,
                event_payload: dict[str, Any],
            ) -> None:
                # The pipeline's terminal failure event can contain arbitrary
                # backend exception text. Suppress it at the GUI boundary; the
                # caught exception below emits one fixed, categorized failure.
                if event == "failed":
                    return
                emit(event, event_payload)

            transcript = run_pipeline(
                input_file,
                accepted.path,
                config,
                debug_report_path=debug_report_path,
                progress=safe_progress,
            )
        except LTMError as exc:
            emit("bridge_error", _public_pipeline_failure(exc))
            return 1
        except Exception as exc:  # noqa: BLE001 - report, then fail the job honestly.
            emit("bridge_error", _public_pipeline_failure(exc))
            return 1
        try:
            result = _completed_result_envelope(
                accepted,
                transcript,
                qa_enabled=config.qa.enabled,
            )
            emit(
                "result",
                result.model_dump(mode="json"),
            )
            return 0
        except ResultContractError as exc:
            del exc
            emit(
                "bridge_error",
                {
                    "type": "ResultContractError",
                    "code": "result_contract_invalid",
                    "message": ("The completed job output could not be verified safely."),
                    "remediation": (
                        "Preserve the partial local output and start a fresh job after "
                        "reviewing the output destination."
                    ),
                },
            )
            return 1
    finally:
        accepted.close()


def _public_pipeline_failure(exc: Exception) -> dict[str, Any]:
    """Project only allowlisted failure categories into the GUI protocol."""

    if isinstance(exc, (OfflineModeError, ModelCacheError)):
        return {
            "type": "ModelUnavailableError",
            "code": "model_unavailable",
            "message": "A required local model is unavailable or incomplete.",
            "remediation": "Review Runtime & Models, then run readiness checks again.",
        }
    if isinstance(exc, BackendUnavailableError):
        return {
            "type": "BackendUnavailableError",
            "code": "backend_unavailable",
            "message": "A required local processing backend is unavailable.",
            "remediation": "Review Runtime & Models and the selected configuration.",
        }
    if isinstance(exc, CommandExecutionError):
        return {
            "type": "CommandExecutionError",
            "code": "local_command_failed",
            "message": "A required local processing command could not be completed.",
            "remediation": "Review the local runtime and retry with a fresh job folder.",
        }
    if isinstance(exc, LTMError):
        return {
            "type": "EngineError",
            "code": "engine_failure",
            "message": "The local engine could not complete this job.",
            "remediation": "Review runtime readiness and the job configuration.",
        }
    return {
        "type": "InternalEngineError",
        "code": "internal_engine_failure",
        "message": ("The local engine stopped because of an unexpected internal error."),
        "remediation": "Enable the debug report and retry for a redacted local trace.",
    }


def handshake_payload() -> dict[str, Any]:
    return {
        "protocol": BRIDGE_PROTOCOL_VERSION,
        "package_version": __version__,
        "python": sys.version.split()[0],
        "commands": list(BRIDGE_COMMANDS),
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "job_acceptance_schema_version": JOB_ACCEPTANCE_SCHEMA_VERSION,
        "job_result_schema_version": JOB_RESULT_SCHEMA_VERSION,
    }


def dump_manifest_json(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in pairs:
        if key in decoded:
            raise ValueError("duplicate JSON object key")
        decoded[key] = value
    return decoded


def _strict_json_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise ValueError("non-finite JSON number")
    return decoded


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-standard JSON constant")


def _decode_stdin_payload(data: bytes) -> dict[str, Any]:
    if not data.strip():
        return {}
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
            parse_float=_strict_json_float,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BridgeUsageError("stdin payload is not valid strict JSON") from exc
    if not isinstance(payload, dict):
        raise BridgeUsageError("stdin payload must be a JSON object")
    return payload


def _read_stdin_payload() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    data = stream.read(MAX_STDIN_PAYLOAD_BYTES + 1)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_STDIN_PAYLOAD_BYTES:
        raise BridgeUsageError("stdin payload exceeds the bridge safety limit")
    return _decode_stdin_payload(data)


def _validate_arguments(arguments: list[str]) -> str:
    if not arguments or arguments[0] not in BRIDGE_COMMANDS:
        raise BridgeUsageError("bridge command is missing or unsupported")
    command = arguments[0]
    if command == "schema":
        if len(arguments) == 1:
            return command
        if len(arguments) == 3 and arguments[1] == "--write" and bool(arguments[2]):
            return command
        raise BridgeUsageError("schema command arguments are invalid")
    if len(arguments) != 1:
        raise BridgeUsageError("bridge command arguments are invalid")
    return command


def _establish_owned_process_group() -> None:
    if os.name != "posix":
        return
    try:
        os.setpgrp()
    except OSError as exc:
        if os.getpgrp() != os.getpid():
            raise BridgeUsageError("bridge process ownership could not be established") from exc


def _install_sigterm_handler() -> None:
    def _terminate(signum: int, frame: object) -> None:
        del signum, frame
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, _terminate)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    # Reserve the true stdout for the protocol before any backend can print.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr

    def emit(event: str, payload_data: dict[str, Any]) -> None:
        protocol.write(
            json.dumps(
                {"event": event, "payload": payload_data},
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
        protocol.flush()

    try:
        command = _validate_arguments(arguments)
        _establish_owned_process_group()
        payload = _read_stdin_payload()
        _require_request_shape(command, payload)
        if command == "handshake":
            emit("handshake", handshake_payload())
            return 0
        if command == "schema":
            manifest = config_manifest()
            if len(arguments) >= 3 and arguments[1] == "--write":
                Path(arguments[2]).write_text(dump_manifest_json(manifest), encoding="utf-8")
                emit("schema_written", {"path": arguments[2]})
                return 0
            emit("schema", manifest)
            return 0
        if command == "presets":
            emit("presets", preset_payload())
            return 0
        if command == "validate":
            emit("validate", validate_payload(payload))
            return 0
        if command == "doctor":
            emit("doctor", doctor_payload(payload))
            return 0
        if command == "models":
            emit("models", models_payload(payload))
            return 0
        if command == "probe":
            emit("probe", probe_payload(payload))
            return 0
        _install_sigterm_handler()
        return run_transcribe(payload, emit)
    except BridgeUsageError as exc:
        emit(
            "bridge_error",
            {"type": "BridgeUsageError", "message": privacy_safe_text(str(exc))},
        )
        return 64
    except Exception:  # noqa: BLE001 - preserve a fixed, content-private failure.
        emit(
            "bridge_error",
            {
                "type": "BridgeInternalError",
                "message": ("The bridge stopped because of an unexpected internal error."),
            },
        )
        return 70
    finally:
        protocol.flush()
        protocol.close()


if __name__ == "__main__":
    raise SystemExit(main())
