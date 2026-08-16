# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Generate the signed native model-delivery catalog.

The catalog is data, not acquisition authority.  It is generated from the
reviewed Python manifest, embedded in the signed macOS application, and
validated again before Swift creates any download task.  Runtime processing
never consults remote model identifiers or this catalog for network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast
from urllib.parse import quote, urlparse

from .config import MODEL_MANIFEST, ModelEntry
from .model_pack import model_pack_contract, model_pack_sbom

MODEL_DELIVERY_CATALOG_SCHEMA_VERSION: Final = "auralign-model-delivery-catalog.v1"
MODEL_DELIVERY_CATALOG_VERSION: Final = "1.1.0"
MODEL_DELIVERY_PRODUCT_VERSION: Final = "0.3.1"
BOOTSTRAP_BUNDLE_ID: Final = "com.auralign.studio.models.bootstrap.v2"
BOOTSTRAP_BUNDLE_VERSION: Final = "2.0.0"
BOOTSTRAP_MODEL_KEYS: Final = ("community-1", "en", "ecapa")
ALLOWED_INITIAL_HOSTS: Final = ("huggingface.co",)
# Hugging Face's official download/firewall documentation identifies these
# HTTPS storage and CDN endpoints. Keep the signed catalog explicit instead of
# accepting an arbitrary redirect below ``hf.co``.
ALLOWED_REDIRECT_HOST_SUFFIXES: Final = (
    "huggingface.co",
    "cas-server.xethub.hf.co",
    "cas-server.xethub-eu.hf.co",
    "transfer.xethub.hf.co",
    "transfer.xethub-eu.hf.co",
    "us.aws.cdn.hf.co",
    "us.gcp.cdn.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cdn-lfs-eu-1.hf.co",
)
NORMAL_PRESET_MODELS: Final = {
    "max": "large-v3",
    "balanced": "turbo",
    "fast": "distil-large-v3.5",
    "cpu": "turbo",
}
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_BUNDLE_ID_PATTERN: Final = re.compile(r"^com\.auralign\.studio\.[a-z0-9.-]+$")
_VERSION_PATTERN: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


MODEL_PRESENTATION: Final[dict[str, dict[str, str]]] = {
    "large-v3": {
        "display_name": "Whisper Large v3",
        "description": "Highest-quality multilingual transcription for the Max preset.",
        "quality": "Highest",
        "speed": "Slowest",
        "memory": "Very high",
    },
    "turbo": {
        "display_name": "Whisper Large v3 Turbo",
        "description": "Fast multilingual transcription for Balanced and CPU presets.",
        "quality": "High",
        "speed": "Fast",
        "memory": "High",
    },
    "distil-large-v3": {
        "display_name": "Distil-Whisper Large v3",
        "description": "Optional English-only distilled model with a speed-focused tradeoff.",
        "quality": "High in English",
        "speed": "Fast",
        "memory": "High",
    },
    "distil-large-v3.5": {
        "display_name": "Distil-Whisper Large v3.5",
        "description": "Fast English-only transcription for the Fast preset.",
        "quality": "High in English",
        "speed": "Fastest CTranslate2",
        "memory": "High",
    },
    "mlx-tiny": {
        "display_name": "MLX Whisper Tiny",
        "description": "Small multilingual MLX model for maximum speed.",
        "quality": "Basic",
        "speed": "Very fast",
        "memory": "Low",
    },
    "mlx-tiny.en": {
        "display_name": "MLX Whisper Tiny English",
        "description": "Small English-only MLX model for maximum speed.",
        "quality": "Basic in English",
        "speed": "Very fast",
        "memory": "Low",
    },
    "mlx-base": {
        "display_name": "MLX Whisper Base",
        "description": "Compact multilingual MLX model with improved accuracy over Tiny.",
        "quality": "Entry",
        "speed": "Very fast",
        "memory": "Low",
    },
    "mlx-base.en": {
        "display_name": "MLX Whisper Base English",
        "description": "Compact English-only MLX model with improved accuracy over Tiny.",
        "quality": "Entry in English",
        "speed": "Very fast",
        "memory": "Low",
    },
    "mlx-small": {
        "display_name": "MLX Whisper Small",
        "description": "Balanced compact multilingual MLX model.",
        "quality": "Good",
        "speed": "Fast",
        "memory": "Moderate",
    },
    "mlx-small.en": {
        "display_name": "MLX Whisper Small English",
        "description": "Balanced compact English-only MLX model.",
        "quality": "Good in English",
        "speed": "Fast",
        "memory": "Moderate",
    },
    "mlx-medium": {
        "display_name": "MLX Whisper Medium",
        "description": "Higher-quality multilingual MLX model with a larger memory footprint.",
        "quality": "Very good",
        "speed": "Moderate",
        "memory": "High",
    },
    "mlx-medium.en": {
        "display_name": "MLX Whisper Medium English",
        "description": "Higher-quality English-only MLX model with a larger memory footprint.",
        "quality": "Very good in English",
        "speed": "Moderate",
        "memory": "High",
    },
    "mlx-large": {
        "display_name": "MLX Whisper Large v1",
        "description": "Original Large multilingual model converted for Apple silicon.",
        "quality": "Very high",
        "speed": "Slower",
        "memory": "Very high",
    },
    "mlx-large-v2": {
        "display_name": "MLX Whisper Large v2",
        "description": "Second-generation Large multilingual model for Apple silicon.",
        "quality": "Very high",
        "speed": "Slower",
        "memory": "Very high",
    },
    "mlx-large-v3": {
        "display_name": "MLX Whisper Large v3",
        "description": "Highest-quality Large v3 multilingual model for Apple silicon.",
        "quality": "Highest",
        "speed": "Slower",
        "memory": "Very high",
    },
    "mlx-large-v3-turbo": {
        "display_name": "MLX Whisper Large v3 Turbo",
        "description": "Speed-optimized Large v3 multilingual model for Apple silicon.",
        "quality": "High",
        "speed": "Fast",
        "memory": "High",
    },
    "community-1": {
        "display_name": "Pyannote Community-1",
        "description": "Bundled speaker-diarization pipeline used by normal presets.",
        "quality": "Production diarization",
        "speed": "Automatic",
        "memory": "Moderate",
    },
    "en": {
        "display_name": "English Forced Alignment",
        "description": "Bundled English word-alignment model.",
        "quality": "Production alignment",
        "speed": "Automatic",
        "memory": "Moderate",
    },
    "ecapa": {
        "display_name": "ECAPA Speaker Embeddings",
        "description": "Bundled speaker-embedding model for local speaker identity.",
        "quality": "Production embeddings",
        "speed": "Automatic",
        "memory": "Moderate",
    },
}


class ModelDeliveryCatalogError(ValueError):
    """Raised when signed model-delivery data is incomplete or inconsistent."""


def model_delivery_catalog(
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> dict[str, Any]:
    """Return the deterministic catalog consumed by the native setup window."""

    if set(manifest) != set(MODEL_PRESENTATION):
        raise ModelDeliveryCatalogError("presentation authority does not cover all manifest models")
    models = [_model_record(manifest[key]) for key in manifest]
    payload: dict[str, Any] = {
        "schema_version": MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
        "catalog_version": MODEL_DELIVERY_CATALOG_VERSION,
        "product_version": MODEL_DELIVERY_PRODUCT_VERSION,
        "allowed_initial_hosts": list(ALLOWED_INITIAL_HOSTS),
        "allowed_redirect_host_suffixes": list(ALLOWED_REDIRECT_HOST_SUFFIXES),
        "bootstrap": {
            "bundle_id": BOOTSTRAP_BUNDLE_ID,
            "bundle_version": BOOTSTRAP_BUNDLE_VERSION,
            "model_keys": list(BOOTSTRAP_MODEL_KEYS),
        },
        "pack_contract": model_pack_contract(),
        "normal_presets": dict(NORMAL_PRESET_MODELS),
        "models": models,
    }
    validate_model_delivery_catalog(payload, manifest=manifest)
    return payload


def dump_model_delivery_catalog(
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> str:
    """Serialize the reviewed catalog with stable human-reviewable formatting."""

    return (
        json.dumps(
            model_delivery_catalog(manifest),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def model_delivery_catalog_sha256(
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> str:
    return hashlib.sha256(dump_model_delivery_catalog(manifest).encode("utf-8")).hexdigest()


def write_model_delivery_catalog(
    destination: Path,
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> Path:
    """Write the generated source resource without following an existing link."""

    destination = Path(destination)
    if destination.is_symlink():
        raise ModelDeliveryCatalogError("catalog destination must not be a symbolic link")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(dump_model_delivery_catalog(manifest), encoding="utf-8")
    return destination


def validate_model_delivery_catalog(
    payload: Mapping[str, Any],
    *,
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> None:
    """Reconcile a decoded catalog against the exact Python model authority."""

    expected = _expected_catalog_without_validation(manifest)
    if payload != expected:
        raise ModelDeliveryCatalogError("model delivery catalog does not match Python authority")
    _validate_catalog_structure(payload)


def _expected_catalog_without_validation(
    manifest: Mapping[str, ModelEntry],
) -> dict[str, Any]:
    if set(manifest) != set(MODEL_PRESENTATION):
        raise ModelDeliveryCatalogError("presentation authority does not cover all manifest models")
    return {
        "schema_version": MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
        "catalog_version": MODEL_DELIVERY_CATALOG_VERSION,
        "product_version": MODEL_DELIVERY_PRODUCT_VERSION,
        "allowed_initial_hosts": list(ALLOWED_INITIAL_HOSTS),
        "allowed_redirect_host_suffixes": list(ALLOWED_REDIRECT_HOST_SUFFIXES),
        "bootstrap": {
            "bundle_id": BOOTSTRAP_BUNDLE_ID,
            "bundle_version": BOOTSTRAP_BUNDLE_VERSION,
            "model_keys": list(BOOTSTRAP_MODEL_KEYS),
        },
        "pack_contract": model_pack_contract(),
        "normal_presets": dict(NORMAL_PRESET_MODELS),
        "models": [_model_record(manifest[key]) for key in manifest],
    }


def _model_record(entry: ModelEntry) -> dict[str, Any]:
    if not entry.production_selectable:
        raise ModelDeliveryCatalogError(f"model is not production selectable: {entry.key}")
    presentation = MODEL_PRESENTATION[entry.key]
    required_for_presets = [
        preset for preset, key in NORMAL_PRESET_MODELS.items() if key == entry.key
    ]
    files: list[dict[str, Any]] = []
    for relative_path in entry.runtime_files:
        record: dict[str, Any] = {
            "path": relative_path,
            "size": entry.expected_size_map[relative_path],
            "sha256": entry.expected_digest_map[relative_path],
        }
        if entry.delivery == "native-download":
            record["url"] = _download_url(entry, relative_path)
        files.append(record)
    record = {
        "key": entry.key,
        "role": entry.role,
        "model_id": entry.model_id,
        "aliases": list(entry.aliases),
        "directory_aliases": list(entry.directory_aliases),
        **presentation,
        "accessibility_label": (
            f"{presentation['display_name']}. {presentation['description']} "
            f"Quality: {presentation['quality']}. Speed: {presentation['speed']}. "
            f"Memory: {presentation['memory']}."
        ),
        "revision": entry.revision,
        "delivery": entry.delivery,
        "pack_id": entry.package_bundle_id,
        "pack_version": entry.package_bundle_version,
        "pack_relative_path": entry.package_relative_path,
        "required_for_presets": required_for_presets,
        "total_size": sum(entry.expected_size_map.values()),
        "files": files,
        "gated": entry.gated,
        "license_expression": entry.license_expression,
        "source_url": entry.source_url,
        "terms_url": entry.terms_url,
        "attribution": entry.attribution,
        "acquisition_status": entry.acquisition_status,
        "runtime_status": entry.runtime_status,
        "redistribution_status": entry.redistribution_status,
        "review_status": entry.review_status,
        "reviewed_at": entry.reviewed_at,
        "product_classification": entry.product_classification,
        "backend_compatibility": entry.backend_compatibility,
        "modification_status": entry.modification_status,
    }
    if entry.delivery == "native-download":
        record["pack_sbom"] = model_pack_sbom(entry)
    return record


def _download_url(entry: ModelEntry, relative_path: str) -> str:
    revision = cast(str, entry.revision)
    return (
        f"https://huggingface.co/{entry.model_id}/resolve/{revision}/"
        f"{quote(relative_path, safe='/')}"
    )


def _validate_catalog_structure(payload: Mapping[str, Any]) -> None:
    models = payload.get("models")
    if not isinstance(models, Sequence) or isinstance(models, (str, bytes)):
        raise ModelDeliveryCatalogError("catalog models must be a sequence")
    seen_keys: set[str] = set()
    seen_pack_ids: set[str] = set()
    for untyped_record in models:
        if not isinstance(untyped_record, Mapping):
            raise ModelDeliveryCatalogError("catalog model record must be an object")
        record = cast(Mapping[str, Any], untyped_record)
        key = _required_string(record, "key")
        if key in seen_keys:
            raise ModelDeliveryCatalogError("catalog model keys must be unique")
        seen_keys.add(key)
        revision = _required_string(record, "revision")
        if _REVISION_PATTERN.fullmatch(revision) is None:
            raise ModelDeliveryCatalogError(f"catalog revision is not immutable: {key}")
        delivery = _required_string(record, "delivery")
        if delivery not in {"system-component", "native-download"}:
            raise ModelDeliveryCatalogError(f"catalog delivery is invalid: {key}")
        pack_id = _required_string(record, "pack_id")
        if _BUNDLE_ID_PATTERN.fullmatch(pack_id) is None:
            raise ModelDeliveryCatalogError(f"catalog pack identifier is invalid: {key}")
        if delivery == "native-download" and pack_id in seen_pack_ids:
            raise ModelDeliveryCatalogError("native model pack identifiers must be unique")
        seen_pack_ids.add(pack_id)
        if _VERSION_PATTERN.fullmatch(_required_string(record, "pack_version")) is None:
            raise ModelDeliveryCatalogError(f"catalog pack version is invalid: {key}")
        _safe_relative_path(_required_string(record, "pack_relative_path"))
        total_size = record.get("total_size")
        files = record.get("files")
        if (
            not isinstance(total_size, int)
            or total_size <= 0
            or not isinstance(files, Sequence)
            or isinstance(files, (str, bytes))
            or not files
        ):
            raise ModelDeliveryCatalogError(f"catalog file contract is invalid: {key}")
        observed_size = 0
        observed_paths: set[str] = set()
        for untyped_file in files:
            if not isinstance(untyped_file, Mapping):
                raise ModelDeliveryCatalogError(f"catalog file record is invalid: {key}")
            file_record = cast(Mapping[str, Any], untyped_file)
            relative_path = _required_string(file_record, "path")
            _safe_relative_path(relative_path)
            if relative_path in observed_paths:
                raise ModelDeliveryCatalogError(f"catalog file paths are duplicated: {key}")
            observed_paths.add(relative_path)
            size = file_record.get("size")
            digest = _required_string(file_record, "sha256")
            if not isinstance(size, int) or size <= 0 or _SHA256_PATTERN.fullmatch(digest) is None:
                raise ModelDeliveryCatalogError(f"catalog file integrity is invalid: {key}")
            observed_size += size
            url = file_record.get("url")
            if delivery == "native-download":
                if not isinstance(url, str):
                    raise ModelDeliveryCatalogError(f"catalog download URL is missing: {key}")
                parsed = urlparse(url)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname not in ALLOWED_INITIAL_HOSTS
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ModelDeliveryCatalogError(f"catalog download URL is unsafe: {key}")
                expected_suffix = f"/resolve/{revision}/{quote(relative_path, safe='/')}"
                if not parsed.path.endswith(expected_suffix):
                    raise ModelDeliveryCatalogError(
                        f"catalog download URL is not revision-bound: {key}"
                    )
            elif url is not None:
                raise ModelDeliveryCatalogError(
                    f"system component must not expose a download URL: {key}"
                )
        if observed_size != total_size:
            raise ModelDeliveryCatalogError(f"catalog total byte count is inconsistent: {key}")
        for field in (
            "display_name",
            "description",
            "quality",
            "speed",
            "memory",
            "accessibility_label",
            "license_expression",
            "source_url",
            "terms_url",
            "attribution",
            "redistribution_status",
            "review_status",
            "modification_status",
        ):
            _required_string(record, field)


def _required_string(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ModelDeliveryCatalogError(f"catalog field is missing or invalid: {key}")
    return value


def _safe_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
    ):
        raise ModelDeliveryCatalogError("catalog relative path is unsafe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args(argv)
    if args.write is not None:
        destination = write_model_delivery_catalog(args.write)
        print(destination)
    else:
        print(dump_model_delivery_catalog(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
