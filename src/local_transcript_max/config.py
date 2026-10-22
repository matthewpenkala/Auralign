# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Configuration, presets, and model manifest handling."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .cleanup_policy import CleanupRejectedError, validate_cleanup_policy
from .errors import LTMError
from .utils import env_flag, safe_repo_dir

PresetName = Literal["max", "balanced", "fast", "cpu"]
ProductModelClassification = Literal["production", "optional", "evaluation-only", "blocked"]
ModelDelivery = Literal["system-component", "native-download", "unavailable"]


@dataclass(frozen=True)
class ModelEntry:
    key: str
    role: str
    model_id: str
    gated: bool = False
    required_files: tuple[str, ...] = ()
    any_required_files: tuple[str, ...] = ()
    expected_sha256: tuple[tuple[str, str], ...] = ()
    expected_size: tuple[tuple[str, int], ...] = ()
    aliases: tuple[str, ...] = ()
    revision: str | None = None
    optional: bool = False
    pullable: bool = True
    directory_aliases: tuple[str, ...] = ()
    license_expression: str = "NOASSERTION"
    source_url: str = ""
    terms_url: str = ""
    attribution: str = ""
    acquisition_status: str = "blocked"
    runtime_status: str = "blocked"
    redistribution_status: str = "blocked"
    review_status: str = "unverified"
    reviewed_at: str | None = None
    product_classification: ProductModelClassification = "blocked"
    blocker: str | None = "No production clearance has been recorded."
    backend_compatibility: str = ""
    modification_status: str = "unreviewed"
    package_bundle_id: str | None = None
    package_bundle_version: str | None = None
    package_relative_path: str | None = None
    delivery: ModelDelivery = "unavailable"

    @property
    def directory_name(self) -> str:
        return safe_repo_dir(self.model_id)

    @property
    def selection_flag(self) -> str:
        return {
            "asr": "--asr",
            "asr-mlx": "--asr-mlx",
            "diarization": "--diarization",
            "alignment": "--alignment",
            "speaker-embedding": "--speaker-id",
        }.get(self.role, f"--{self.role}")

    @property
    def runtime_files(self) -> tuple[str, ...]:
        """Return the reviewed files that acquisition is allowed to download."""

        return tuple(dict.fromkeys((*self.required_files, *self.any_required_files)))

    @property
    def expected_digest_map(self) -> dict[str, str]:
        """Return repository-reviewed file digests when they are available."""

        return dict(self.expected_sha256)

    @property
    def expected_size_map(self) -> dict[str, int]:
        """Return repository-reviewed byte counts when they are available."""

        return dict(self.expected_size)

    @property
    def production_selectable(self) -> bool:
        """Return whether every release-critical model authority is complete."""

        return (
            self.product_classification in {"production", "optional"}
            and self.blocker is None
            and self.revision is not None
            and len(self.revision) == 40
            and bool(self.runtime_files)
            and set(self.expected_digest_map) == set(self.runtime_files)
            and set(self.expected_size_map) == set(self.runtime_files)
            and all(size > 0 for size in self.expected_size_map.values())
            and self.license_expression != "NOASSERTION"
            and self.runtime_status.startswith("approved_local")
            and self.redistribution_status.startswith("permitted_")
            and self.review_status.startswith("verified")
            and bool(self.backend_compatibility)
            and self.modification_status != "unreviewed"
            and bool(self.package_bundle_id)
            and bool(self.package_bundle_version)
            and bool(self.package_relative_path)
            and self.delivery != "unavailable"
        )

    def pull_command(self, cache_dir: str = "models") -> str:
        if not self.pullable:
            return (
                f"place local model files for {self.model_id} under "
                f"{cache_dir}/{self.directory_name}"
            )
        return f"ltm models pull {self.selection_flag} {self.key} --cache-dir {cache_dir}"

    def verify_command(self, cache_dir: str = "models") -> str:
        if not self.pullable:
            return "ltm models audit --config CONFIG_WITH_REVIEWED_LOCAL_MODEL_PATH"
        return f"ltm models verify {self.selection_flag} {self.key} --cache-dir {cache_dir}"


@dataclass(frozen=True)
class MlxModelAuthority:
    revision: str
    config_size: int
    config_sha256: str
    weight_filename: str
    weight_size: int
    weight_sha256: str


MLX_MODEL_AUTHORITIES = {
    "mlx-community/whisper-tiny-mlx": MlxModelAuthority(
        revision="6caf9c55601caafbe6508a8b0d216bdf4783c4e8",
        config_size=262,
        config_sha256="aaff20ce8f69beddee3fe0cc1e08f4e92f58586cb9f12ba00a6f73cbfec1cb1c",
        weight_filename="weights.npz",
        weight_size=74_418_540,
        weight_sha256="0e03a5993d6eea43b07ee2dcc772b0e4cef5bb227257dacc24bf289387d49186",
    ),
    "mlx-community/whisper-tiny.en-mlx": MlxModelAuthority(
        revision="5f4dafbb28e62a53c1b10426ff7eed36ca733bf7",
        config_size=262,
        config_sha256="452e616242261333a661bc86db762b3720d6cc7e2eccd3d8e19a631871c26dd9",
        weight_filename="weights.npz",
        weight_size=74_417_804,
        weight_sha256="4627203f0807d6496dda0d200e893b04ff24c98e73aa51c485c06203a0330b17",
    ),
    "mlx-community/whisper-base-mlx": MlxModelAuthority(
        revision="1e3e249fb8d01c655324bd6841b1deadffd6d04c",
        config_size=262,
        config_sha256="737220a6d958b3ad48e78f840fa991556266983c84ea2ca40e413389c62e4c2f",
        weight_filename="weights.npz",
        weight_size=143_724_204,
        weight_sha256="2f57d5f3ef473054c638961f90716f4ee415e8108de81313eccb2c5fd62eff0b",
    ),
    "mlx-community/whisper-base.en-mlx": MlxModelAuthority(
        revision="aa0678c3466ed62c5c6114ec600a0e1f96820089",
        config_size=262,
        config_sha256="b0f55e86f8092c1188306dc346742253f759aecea0da7b00ae526e566fd53b14",
        weight_filename="weights.npz",
        weight_size=143_723_132,
        weight_sha256="b6c8ee500656e04e8e57c2949a5253f0dda002ba36cd1561846574dbcf01132e",
    ),
    "mlx-community/whisper-small-mlx": MlxModelAuthority(
        revision="45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
        config_size=266,
        config_sha256="e8f58e638208af66d5d5d67801259dc7a12d199e971967a9f9d33a8e3635668e",
        weight_filename="weights.npz",
        weight_size=481_307_592,
        weight_sha256="55b6674c9b339702d486e2b1573839a66f8ec8f821ed2886993ef717a86b09f5",
    ),
    "mlx-community/whisper-small.en-mlx": MlxModelAuthority(
        revision="52a88bf6e98b114a210c21bb83e22d6e1505cb73",
        config_size=266,
        config_sha256="40028e43687458e79ba89cc16bf14184c5414a5cbc00fd511f8f78d138647afd",
        weight_filename="weights.npz",
        weight_size=481_306_200,
        weight_sha256="1bb29b030aca711a035f7a084a0eefac6251ecc2bdd356fa748858fbad082f5a",
    ),
    "mlx-community/whisper-medium-mlx": MlxModelAuthority(
        revision="7fc08c4eac4c316526498f147dfdee6f6303f975",
        config_size=268,
        config_sha256="3ff0b3f17a5a3a614327ffd835a3c8f6c78f39cbd39e84dbff4b0ae267c4d2e4",
        weight_filename="weights.npz",
        weight_size=1_524_924_912,
        weight_sha256="10b597c2bcb1bcc38b2d3d24cd4f0885f461a7cd70e8444d6ad5a763ece549ea",
    ),
    "mlx-community/whisper-medium.en-mlx": MlxModelAuthority(
        revision="8d0335069b551e8586b8a4680674403d9417dfe4",
        config_size=268,
        config_sha256="20f1b6b70cab52e6669c2a095fd3bcc16aa25c7e367d29060f876d4728832b45",
        weight_filename="weights.npz",
        weight_size=1_524_923_056,
        weight_sha256="66cda8c3df03af0b922764620d46734240ab82c54348c5848238c8604bdadbdb",
    ),
    "mlx-community/whisper-large-v1-mlx": MlxModelAuthority(
        revision="e2cb9fbf9c7aefad760be1dc9b48c075b21288c8",
        config_size=268,
        config_sha256="77d68bd8db0be90465fd7d35f8e4fa418297e449a27525060517a7e0df432c4b",
        weight_filename="weights.npz",
        weight_size=3_083_149_200,
        weight_sha256="56824c4ff897d62ba53a84a1738b0b33b237970a7451d5f933de5f2aef5140cf",
    ),
    "mlx-community/whisper-large-v2-mlx": MlxModelAuthority(
        revision="cce86229e2765266197fef869ce9f7e2550067ab",
        config_size=268,
        config_sha256="77d68bd8db0be90465fd7d35f8e4fa418297e449a27525060517a7e0df432c4b",
        weight_filename="weights.npz",
        weight_size=3_083_149_424,
        weight_sha256="c9888a2d03b4e9906c2864151f56dc21e58d617938da0eb818152e3f99adc3f1",
    ),
    "mlx-community/whisper-large-v3-mlx": MlxModelAuthority(
        revision="49e6aa286ad60c14352c404340ded53710378a11",
        config_size=269,
        config_sha256="34982ce6ae286095000f82ae9583b3431639e8b092bf60c961f203745e6500e3",
        weight_filename="weights.npz",
        weight_size=3_083_520_416,
        weight_sha256="05ff791ce3630fae47e7c51004e9666204d786246ec07cac6110af768099b40d",
    ),
    "mlx-community/whisper-large-v3-turbo": MlxModelAuthority(
        revision="a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
        config_size=268,
        config_sha256="b34fc29e4e11e0a25e812775dd67f4dd16fc2c8eb43d28ae25ff7d660ecb6379",
        weight_filename="weights.safetensors",
        weight_size=1_613_977_612,
        weight_sha256="951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6",
    ),
}

MLX_REVISIONS = {
    model_id: authority.revision for model_id, authority in MLX_MODEL_AUTHORITIES.items()
}


def mlx_model_entry(key: str, model_id: str, aliases: tuple[str, ...] = ()) -> ModelEntry:
    authority = MLX_MODEL_AUTHORITIES[model_id]
    package_key = key.replace(".", "-")
    return ModelEntry(
        key=key,
        role="asr-mlx",
        model_id=model_id,
        required_files=("config.json", authority.weight_filename),
        expected_sha256=(
            ("config.json", authority.config_sha256),
            (authority.weight_filename, authority.weight_sha256),
        ),
        expected_size=(
            ("config.json", authority.config_size),
            (authority.weight_filename, authority.weight_size),
        ),
        aliases=aliases,
        directory_aliases=(key, *(alias for alias in aliases if "/" not in alias)),
        optional=True,
        revision=authority.revision,
        license_expression="MIT",
        source_url=f"https://huggingface.co/{model_id}",
        terms_url=f"https://huggingface.co/{model_id}",
        attribution=(
            "MLX Community conversion of the corresponding OpenAI Whisper MIT model; "
            "mlx-whisper conversion/runtime source is MIT."
        ),
        acquisition_status="approved_exact_pinned_files",
        runtime_status="approved_local_real_inference",
        redistribution_status="permitted_with_mit_chain_and_owner_clearance",
        review_status="verified_exact_files_inference_and_upstream_mit_chain",
        reviewed_at="2026-07-25",
        product_classification="optional",
        blocker=None,
        backend_compatibility=(
            "mlx-whisper 0.4.3 / MLX 0.31.2 Apple-silicon runtime; real "
            "network-denied local inference passed"
        ),
        modification_status="unmodified exact pinned conversion files",
        package_bundle_id=f"com.auralign.studio.modelpack.{package_key}.v1",
        package_bundle_version="1.0.0",
        package_relative_path=f"models/{safe_repo_dir(model_id)}",
        delivery="native-download",
    )


MODEL_MANIFEST: dict[str, ModelEntry] = {
    "large-v3": ModelEntry(
        key="large-v3",
        role="asr",
        model_id="Systran/faster-whisper-large-v3",
        required_files=(
            "model.bin",
            "config.json",
            "tokenizer.json",
            "preprocessor_config.json",
            "vocabulary.json",
        ),
        expected_sha256=(
            ("model.bin", "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"),
            ("config.json", "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9"),
            ("tokenizer.json", "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca"),
            (
                "preprocessor_config.json",
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            (
                "vocabulary.json",
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ),
        expected_size=(
            ("model.bin", 3_087_284_237),
            ("config.json", 2_394),
            ("tokenizer.json", 2_480_617),
            ("preprocessor_config.json", 340),
            ("vocabulary.json", 1_068_114),
        ),
        aliases=("max", "whisper-large-v3"),
        revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
        license_expression="MIT",
        source_url="https://huggingface.co/Systran/faster-whisper-large-v3",
        terms_url="https://huggingface.co/Systran/faster-whisper-large-v3",
        attribution="SYSTRAN faster-whisper conversion of OpenAI Whisper large-v3.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified",
        reviewed_at="2026-07-10",
        product_classification="production",
        blocker=None,
        backend_compatibility="faster-whisper CTranslate2 CPU runtime",
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.modelpack.large-v3.v1",
        package_bundle_version="1.0.0",
        package_relative_path="models/Systran--faster-whisper-large-v3",
        delivery="native-download",
    ),
    "turbo": ModelEntry(
        key="turbo",
        role="asr",
        model_id="dropbox-dash/faster-whisper-large-v3-turbo",
        required_files=(
            "model.bin",
            "config.json",
            "tokenizer.json",
            "preprocessor_config.json",
            "vocabulary.json",
        ),
        expected_sha256=(
            ("model.bin", "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
            ("config.json", "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
            ("tokenizer.json", "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
            (
                "preprocessor_config.json",
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            (
                "vocabulary.json",
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ),
        expected_size=(
            ("model.bin", 1_617_884_929),
            ("config.json", 2_263),
            ("tokenizer.json", 2_710_337),
            ("preprocessor_config.json", 340),
            ("vocabulary.json", 1_068_114),
        ),
        aliases=(
            "large-v3-turbo",
            "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        ),
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        optional=True,
        license_expression="MIT",
        source_url="https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo",
        terms_url="https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo",
        attribution="Dropbox Dash faster-whisper conversion of OpenAI Whisper large-v3-turbo.",
        acquisition_status="approved_exact_pinned_files",
        runtime_status="approved_local_real_inference",
        redistribution_status="permitted_with_license_terms",
        review_status="verified_exact_files_and_inference",
        reviewed_at="2026-07-25",
        product_classification="production",
        blocker=None,
        backend_compatibility=(
            "faster-whisper 1.2.1 / CTranslate2 4.8.0 CPU; multilingual; "
            "real local int8_float32 inference passed"
        ),
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.modelpack.turbo.v1",
        package_bundle_version="1.0.0",
        package_relative_path="models/dropbox-dash--faster-whisper-large-v3-turbo",
        delivery="native-download",
    ),
    "distil-large-v3": ModelEntry(
        key="distil-large-v3",
        role="asr",
        model_id="Systran/faster-distil-whisper-large-v3",
        required_files=(
            "model.bin",
            "config.json",
            "tokenizer.json",
            "preprocessor_config.json",
            "vocabulary.json",
        ),
        expected_sha256=(
            ("model.bin", "b79368e19b6623813609431a6e5ee309a71506701ebc49fd7820e692dec7c5f5"),
            ("config.json", "90c55f775cc4e0bb17293d0bf12f96557a486f20dea886fabd8e6075a3588b21"),
            ("tokenizer.json", "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca"),
            (
                "preprocessor_config.json",
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            (
                "vocabulary.json",
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ),
        expected_size=(
            ("model.bin", 1_512_927_867),
            ("config.json", 2_690),
            ("tokenizer.json", 2_480_617),
            ("preprocessor_config.json", 340),
            ("vocabulary.json", 1_068_114),
        ),
        revision="c3058b475261292e64a0412df1d2681c06260fab",
        optional=True,
        license_expression="MIT",
        source_url="https://huggingface.co/Systran/faster-distil-whisper-large-v3",
        terms_url="https://huggingface.co/Systran/faster-distil-whisper-large-v3",
        attribution="SYSTRAN CTranslate2 conversion of Distil-Whisper.",
        acquisition_status="approved_exact_pinned_files",
        runtime_status="approved_local_real_inference",
        redistribution_status="permitted_with_license_terms",
        review_status="verified_exact_files_and_inference",
        reviewed_at="2026-07-25",
        product_classification="optional",
        blocker=None,
        backend_compatibility=(
            "faster-whisper 1.2.1 / CTranslate2 4.8.0 CPU; English-only; "
            "real local int8_float32 inference passed"
        ),
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.modelpack.distil-large-v3.v1",
        package_bundle_version="1.0.0",
        package_relative_path="models/Systran--faster-distil-whisper-large-v3",
        delivery="native-download",
    ),
    "distil-large-v3.5": ModelEntry(
        key="distil-large-v3.5",
        role="asr",
        model_id="distil-whisper/distil-large-v3.5-ct2",
        required_files=(
            "model.bin",
            "config.json",
            "tokenizer.json",
            "preprocessor_config.json",
            "vocabulary.json",
        ),
        expected_sha256=(
            ("model.bin", "c58b88b8585ffcd2135fddaaf421ce72cb223b32edea70d156aed1dea319a119"),
            ("config.json", "90c55f775cc4e0bb17293d0bf12f96557a486f20dea886fabd8e6075a3588b21"),
            ("tokenizer.json", "b3c8202bbf06d8ee4232c5984baa563784ac4737e2e7fdc42fa180200d3cfcdb"),
            (
                "preprocessor_config.json",
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            (
                "vocabulary.json",
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ),
        expected_size=(
            ("model.bin", 1_512_927_867),
            ("config.json", 2_690),
            ("tokenizer.json", 2_480_645),
            ("preprocessor_config.json", 340),
            ("vocabulary.json", 1_068_114),
        ),
        revision="9793ccc07920e0f830e1dba0343efcdf0ef8c903",
        optional=True,
        license_expression="MIT",
        source_url="https://huggingface.co/distil-whisper/distil-large-v3.5-ct2",
        terms_url="https://huggingface.co/distil-whisper/distil-large-v3.5-ct2",
        attribution="Distil-Whisper CTranslate2 conversion.",
        acquisition_status="approved_exact_pinned_files",
        runtime_status="approved_local_real_inference",
        redistribution_status="permitted_with_license_terms",
        review_status="verified_exact_files_and_inference",
        reviewed_at="2026-07-25",
        product_classification="production",
        blocker=None,
        backend_compatibility=(
            "faster-whisper 1.2.1 / CTranslate2 4.8.0 CPU; English-only; "
            "real local int8_float32 inference passed"
        ),
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.modelpack.distil-large-v3-5.v1",
        package_bundle_version="1.0.0",
        package_relative_path="models/distil-whisper--distil-large-v3.5-ct2",
        delivery="native-download",
    ),
    "mlx-tiny": mlx_model_entry(
        "mlx-tiny",
        "mlx-community/whisper-tiny-mlx",
        aliases=("tiny", "whisper-tiny-mlx", "mlx-community/whisper-tiny-mlx"),
    ),
    "mlx-tiny.en": mlx_model_entry(
        "mlx-tiny.en",
        "mlx-community/whisper-tiny.en-mlx",
        aliases=("tiny.en", "whisper-tiny.en-mlx", "mlx-community/whisper-tiny.en-mlx"),
    ),
    "mlx-base": mlx_model_entry(
        "mlx-base",
        "mlx-community/whisper-base-mlx",
        aliases=("base", "whisper-base-mlx", "mlx-community/whisper-base-mlx"),
    ),
    "mlx-base.en": mlx_model_entry(
        "mlx-base.en",
        "mlx-community/whisper-base.en-mlx",
        aliases=("base.en", "whisper-base.en-mlx", "mlx-community/whisper-base.en-mlx"),
    ),
    "mlx-small": mlx_model_entry(
        "mlx-small",
        "mlx-community/whisper-small-mlx",
        aliases=("small", "whisper-small-mlx", "mlx-community/whisper-small-mlx"),
    ),
    "mlx-small.en": mlx_model_entry(
        "mlx-small.en",
        "mlx-community/whisper-small.en-mlx",
        aliases=("small.en", "whisper-small.en-mlx", "mlx-community/whisper-small.en-mlx"),
    ),
    "mlx-medium": mlx_model_entry(
        "mlx-medium",
        "mlx-community/whisper-medium-mlx",
        aliases=("medium", "whisper-medium-mlx", "mlx-community/whisper-medium-mlx"),
    ),
    "mlx-medium.en": mlx_model_entry(
        "mlx-medium.en",
        "mlx-community/whisper-medium.en-mlx",
        aliases=("medium.en", "whisper-medium.en-mlx", "mlx-community/whisper-medium.en-mlx"),
    ),
    "mlx-large": mlx_model_entry(
        "mlx-large",
        "mlx-community/whisper-large-v1-mlx",
        aliases=(
            "large",
            "large-v1",
            "whisper-large-mlx",
            "whisper-large-v1-mlx",
            "mlx-community/whisper-large-mlx",
        ),
    ),
    "mlx-large-v2": mlx_model_entry(
        "mlx-large-v2",
        "mlx-community/whisper-large-v2-mlx",
        aliases=("large-v2", "whisper-large-v2-mlx", "mlx-community/whisper-large-v2-mlx"),
    ),
    "mlx-large-v3": mlx_model_entry(
        "mlx-large-v3",
        "mlx-community/whisper-large-v3-mlx",
        aliases=("large-v3", "whisper-large-v3-mlx", "mlx-community/whisper-large-v3-mlx"),
    ),
    "mlx-large-v3-turbo": mlx_model_entry(
        "mlx-large-v3-turbo",
        "mlx-community/whisper-large-v3-turbo",
        aliases=(
            "mlx-turbo",
            "large-v3-turbo-mlx",
            "large-v3-turbo",
            "turbo",
            "mlx-community/whisper-large-v3-turbo",
        ),
    ),
    "community-1": ModelEntry(
        key="community-1",
        role="diarization",
        model_id="pyannote/speaker-diarization-community-1",
        gated=True,
        required_files=(
            "config.yaml",
            "segmentation/pytorch_model.bin",
            "embedding/pytorch_model.bin",
            "plda/plda.npz",
            "plda/xvec_transform.npz",
        ),
        expected_sha256=(
            (
                "config.yaml",
                "5ce2bfa9a938dc132cec1172592d65173cbb8f444ea1e4133f10f9391de155be",
            ),
            (
                "segmentation/pytorch_model.bin",
                "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e",
            ),
            (
                "embedding/pytorch_model.bin",
                "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929",
            ),
            ("plda/plda.npz", "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255"),
            (
                "plda/xvec_transform.npz",
                "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f",
            ),
        ),
        expected_size=(
            ("config.yaml", 444),
            ("segmentation/pytorch_model.bin", 5_906_507),
            ("embedding/pytorch_model.bin", 26_646_242),
            ("plda/plda.npz", 133_852),
            ("plda/xvec_transform.npz", 134_376),
        ),
        aliases=("pyannote-community-1",),
        revision="3533c8cf8e369892e6b79ff1bf80f7b0286a54ee",
        license_expression="CC-BY-4.0",
        source_url="https://huggingface.co/pyannote/speaker-diarization-community-1",
        terms_url="https://huggingface.co/pyannote/speaker-diarization-community-1",
        attribution="pyannote speaker-diarization-community-1; attribution and gated terms apply.",
        acquisition_status="approved_after_gated_terms",
        runtime_status="approved_local_after_gated_terms",
        redistribution_status="permitted_with_attribution_and_gated_terms",
        review_status="verified_gated",
        reviewed_at="2026-07-20",
        product_classification="production",
        blocker=None,
        backend_compatibility="pyannote.audio CPU diarization runtime",
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.models.bootstrap.v2",
        package_bundle_version="2.0.0",
        package_relative_path=("models/pyannote--speaker-diarization-community-1"),
        delivery="system-component",
    ),
    "en": ModelEntry(
        key="en",
        role="alignment",
        model_id="facebook/wav2vec2-base-960h",
        required_files=(
            "config.json",
            "preprocessor_config.json",
            "tokenizer_config.json",
            "vocab.json",
            "model.safetensors",
        ),
        expected_sha256=(
            ("config.json", "d3ec255c063d9f95057b553b19c20135b259875834a4fe9deb218a6be25b4cf3"),
            (
                "preprocessor_config.json",
                "b225d617c025463b9e157e06afea8b90dc7078fc70b013c533328423e0486b4a",
            ),
            (
                "tokenizer_config.json",
                "dc790594f5bc351a4311c6624f40acd95850d4aaf2a5cb3c656c9b610720b608",
            ),
            ("vocab.json", "19727f8944fe6459fc3f240ae2c198395b740f6a029bd23e06656266b83bcf64"),
            (
                "model.safetensors",
                "8aa76ab2243c81747a1f832954586bc566090c83a0ac167df6f31f0fa917d74a",
            ),
        ),
        expected_size=(
            ("config.json", 1_596),
            ("preprocessor_config.json", 159),
            ("tokenizer_config.json", 163),
            ("vocab.json", 291),
            ("model.safetensors", 377_607_901),
        ),
        aliases=("auto", "english"),
        optional=True,
        revision="22aad52d435eb6dbaf354bdad9b0da84ce7d6156",
        license_expression="Apache-2.0",
        source_url="https://huggingface.co/facebook/wav2vec2-base-960h",
        terms_url="https://huggingface.co/facebook/wav2vec2-base-960h",
        attribution="Meta wav2vec2-base-960h.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified",
        reviewed_at="2026-07-10",
        product_classification="production",
        blocker=None,
        backend_compatibility="WhisperX wav2vec2 forced-alignment CPU runtime",
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.models.bootstrap.v2",
        package_bundle_version="2.0.0",
        package_relative_path="models/facebook--wav2vec2-base-960h",
        delivery="system-component",
    ),
    "ecapa": ModelEntry(
        key="ecapa",
        role="speaker-embedding",
        model_id="speechbrain/spkrec-ecapa-voxceleb",
        required_files=(
            "hyperparams.yaml",
            "embedding_model.ckpt",
            "classifier.ckpt",
            "mean_var_norm_emb.ckpt",
            "label_encoder.txt",
            "config.json",
        ),
        expected_sha256=(
            (
                "hyperparams.yaml",
                "6f78854fa04ba59e761437b76a2575d3aba5e5016de3e9b69f0c9a5077fb1a41",
            ),
            (
                "embedding_model.ckpt",
                "0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2",
            ),
            (
                "classifier.ckpt",
                "fd9e3634fe68bd0a427c95e354c0c677374f62b3f434e45b78599950d860d535",
            ),
            (
                "mean_var_norm_emb.ckpt",
                "cd70225b05b37be64fc5a95e24395d804231d43f74b2e1e5a513db7b69b34c33",
            ),
            (
                "label_encoder.txt",
                "e13c3a167bb4112685670ee896d20e2b565af16b3a4ceeaa8689fa4d22adb8b9",
            ),
            ("config.json", "15ff7afad09adec0936c31493a7397c13f855914f4579a8393fe82649778a664"),
        ),
        expected_size=(
            ("hyperparams.yaml", 1_919),
            ("embedding_model.ckpt", 83_316_686),
            ("classifier.ckpt", 5_534_328),
            ("mean_var_norm_emb.ckpt", 1_921),
            ("label_encoder.txt", 128_619),
            ("config.json", 51),
        ),
        aliases=("speechbrain-ecapa",),
        optional=True,
        revision="0f99f2d0ebe89ac095bcc5903c4dd8f72b367286",
        license_expression="Apache-2.0",
        source_url="https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb",
        terms_url="https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb",
        attribution="SpeechBrain ECAPA-TDNN model; upstream VoxCeleb data caveats apply.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified_with_training_data_caveat",
        reviewed_at="2026-07-10",
        product_classification="production",
        blocker=None,
        backend_compatibility="SpeechBrain ECAPA-TDNN CPU runtime",
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.models.bootstrap.v2",
        package_bundle_version="2.0.0",
        package_relative_path=("models/speechbrain--spkrec-ecapa-voxceleb"),
        delivery="system-component",
    ),
}


def resolve_model_entry(role: str, value: str | None) -> ModelEntry | None:
    if not value:
        return None
    normalized = value.strip()
    for entry in MODEL_MANIFEST.values():
        if entry.role != role:
            continue
        if normalized in {entry.key, entry.model_id, *entry.aliases}:
            return entry
    return None


class ASRConfig(BaseModel):
    backend: Literal["faster-whisper", "mlx-whisper"] = "faster-whisper"
    model: str = "large-v3"
    mlx_model: str = "mlx-large-v3"
    mlx_model_path: Path | None = None
    mlx_fp16: bool = True
    mlx_vad_windowed: bool = False
    mlx_vad_window_seconds: float = Field(default=30.0, ge=1.0)
    mlx_vad_window_padding_seconds: float = Field(default=0.0, ge=0.0)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    cpu_threads: int | None = Field(default=None, gt=0)
    num_workers: int | None = Field(default=None, gt=0)
    batched: bool = False
    beam_size: int = 5
    best_of: int = 5
    patience: float | None = None
    temperature: float | list[float] = Field(default_factory=lambda: [0.0, 0.2, 0.4])
    compression_ratio_threshold: float = 2.4
    logprob_threshold: float = -1.0
    no_speech_threshold: float = 0.6
    condition_on_previous_text: bool = False
    language: str | None = None
    task: str = "transcribe"
    initial_prompt: str | None = None
    prefix: str | None = None
    glossary_file: Path | None = None
    word_timestamps: bool = True
    vad_filter: bool = False
    batch_size: int = Field(default=8, gt=0)


class DiarizationConfig(BaseModel):
    backend: Literal["auto", "pyannote", "off"] = "pyannote"
    model: str = "community-1"
    num_speakers: int | None = Field(default=None, ge=1, le=128)
    min_speakers: int | None = Field(default=None, ge=1, le=128)
    max_speakers: int | None = Field(default=None, ge=1, le=128)
    device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    enabled: bool = True
    preserve_overlap: bool = True
    exclusive_diarization: bool = True

    @model_validator(mode="after")
    def validate_speaker_range(self) -> DiarizationConfig:
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("min_speakers must be less than or equal to max_speakers")
        return self


def diarization_runtime_enabled(config: DiarizationConfig) -> bool:
    """Return whether the configured diarization backend participates in runtime."""

    return config.enabled and config.backend in {"auto", "pyannote"}


class AgenticDiarizationConfig(BaseModel):
    enabled: bool = False
    candidates: int | None = Field(default=None, ge=1, le=128)
    candidate_range: str = "2:8"
    known_speakers: int | None = Field(default=None, ge=1, le=128)
    write_decision_report: bool = True

    @field_validator("candidate_range")
    @classmethod
    def validate_candidate_range(cls, value: str) -> str:
        try:
            left, right = value.split(":", maxsplit=1)
            start = int(left)
            end = int(right)
        except ValueError as exc:
            raise ValueError("candidate_range must be an inclusive range like 2:8") from exc
        if start < 1 or end < start or end > 128:
            raise ValueError("candidate_range must satisfy 1 <= start <= end <= 128")
        return value


class AlignmentConfig(BaseModel):
    backend: Literal["auto", "forced", "fallback", "whisper", "off"] = "auto"
    language: str | None = None
    model: str | None = None
    device: Literal["auto", "cpu", "cuda", "mps"] | None = None
    allow_experimental_mps: bool = False
    enabled: bool = True


class AudioConfig(BaseModel):
    stream_index: int | None = Field(default=None, ge=0, strict=True)
    sample_rate: int = 16000
    mono: bool = True
    normalize_loudness: bool = False
    denoise: Literal["off", "mild", "strong"] = "off"
    separate_vocals: Literal["off", "experimental"] = "off"
    preserve_channels: bool = False


class QAConfig(BaseModel):
    enabled: bool = True
    targeted_redecode: bool = False


MAXIMUM_ADAPTIVE_BEAM_SIZE = 10
"""Widest reviewed re-decode beam.

A wider global beam is not reliably better for Whisper: it costs runtime and can
introduce hallucinations, omissions, and repetition. Beam values above this
ceiling stay unavailable until a separately reviewed held-out evaluation shows a
material benefit.
"""


class RedecodeConfig(BaseModel):
    mode: Literal["off", "low-confidence", "hallucination", "all-suspect"] = "off"
    selection_policy: Literal["candidate-only", "adaptive-safe"] = "candidate-only"
    max_spans: int = Field(default=5, ge=1, le=100)
    pad_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    beam_size: int = Field(default=5, ge=1, le=20)
    adaptive_wide_beam_size: int = Field(default=10, ge=2, le=MAXIMUM_ADAPTIVE_BEAM_SIZE)
    adaptive_max_span_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    adaptive_max_total_window_seconds: float = Field(default=300.0, gt=0.0, le=3600.0)
    adaptive_min_alignment_coverage: float = Field(default=0.9, ge=0.0, le=1.0)
    adaptive_min_logprob_margin: float = Field(default=0.05, ge=0.0, le=2.0)
    adaptive_protected_margin_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)
    adaptive_max_plan_bytes: int = Field(default=4_194_304, ge=4_096, le=67_108_864)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    write_report: bool = False
    write_repaired_transcript: bool = False

    @field_validator("write_repaired_transcript")
    @classmethod
    def reject_repaired_transcript_output(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "write_repaired_transcript=true is no longer supported: targeted "
                "re-decode is candidate-only. Set it to false and review "
                "transcript.redecode_candidates.json instead."
            )
        return value

    @model_validator(mode="after")
    def validate_selection_policy(self) -> RedecodeConfig:
        if self.selection_policy == "adaptive-safe":
            # The ladder only exists for adaptive selection. Enforcing it in
            # candidate-only mode would reject existing configurations whose
            # single re-decode beam is already 10 or wider.
            if self.adaptive_wide_beam_size <= self.beam_size:
                raise ValueError(
                    "redecode beam ladder must strictly increase: "
                    "adaptive_wide_beam_size must exceed beam_size "
                    f"(got {self.beam_size} then {self.adaptive_wide_beam_size})."
                )
            if self.mode == "off":
                raise ValueError(
                    "redecode.selection_policy=adaptive-safe requires a re-decode mode "
                    "that can select suspicious spans; set redecode.mode."
                )
            if self.temperature != 0.0:
                raise ValueError(
                    "redecode.selection_policy=adaptive-safe requires "
                    "redecode.temperature=0.0 so both beam candidates stay deterministic."
                )
        return self

    @property
    def beam_ladder(self) -> tuple[int, ...]:
        """Return the deterministic control-then-wide beam ladder."""

        if self.selection_policy != "adaptive-safe":
            return (self.beam_size,)
        return (self.beam_size, self.adaptive_wide_beam_size)


class SpeakerIDConfig(BaseModel):
    mode: Literal["off", "auto"] = "auto"
    model: str = "ecapa"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    minimum_segment_seconds: float = Field(default=1.0, ge=1.0, le=60.0)
    maximum_speech_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    profile_dir: Path | None = None
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    ambiguity_margin: float = Field(default=0.05, ge=0.0, le=1.0)


class IdentityResolverConfig(BaseModel):
    """Opt-in post-transcription speaker-identity resolver configuration."""

    mode: Literal["off", "review", "apply-safe", "apply-approved"] = "off"
    known_speakers: int | None = Field(default=None, ge=1, le=128)
    roster_path: Path | None = None
    external_transcript: Path | None = None
    approved_map_path: Path | None = None
    sibling_jobs: list[Path] = Field(default_factory=list, max_length=100)
    sibling_root: Path | None = None
    sibling_manifest: Path | None = None
    stability_config_path: Path | None = None
    media_review_report_path: Path | None = None
    write_identity_mapped_exports: bool = False
    write_media_review_queue: bool = False
    audit_csv: bool = True
    deliverable_format: Literal["srt", "all"] = "srt"
    source_media_review: Literal["none", "queue", "reviewed-report"] = "queue"
    turn_boundary_repair: Literal["report-only", "label-only"] = "report-only"
    unattributed_label: str = "Unattributed"
    min_apply_confidence: float = Field(default=0.90, ge=0.0, le=1.0)
    profile_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    profile_ambiguity_margin: float = Field(default=0.05, ge=0.0, le=1.0)
    max_fragment_duration: float = Field(default=4.0, gt=0.0, le=60.0)
    max_fragment_words: int = Field(default=12, ge=1, le=500)
    max_sibling_jobs: int = Field(default=20, ge=1, le=100)

    @field_validator("unattributed_label")
    @classmethod
    def validate_unattributed_label(cls, value: str) -> str:
        from .speaker_id.identity_models import normalize_display_label

        return normalize_display_label(value, allow_reserved=True)

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> IdentityResolverConfig:
        if self.mode == "apply-approved" and self.approved_map_path is None:
            raise ValueError("apply-approved requires approved_map_path")
        if self.mode not in {"off", "apply-approved"} and self.approved_map_path is not None:
            raise ValueError("approved_map_path is consumed only by apply-approved")
        if (
            self.mode in {"apply-safe", "apply-approved"}
            and self.known_speakers is not None
            and self.roster_path is None
        ):
            raise ValueError("identity apply with known_speakers requires roster_path")
        if self.source_media_review == "reviewed-report" and self.media_review_report_path is None:
            raise ValueError("reviewed-report requires media_review_report_path")
        if (
            self.source_media_review != "reviewed-report"
            and self.media_review_report_path is not None
        ):
            raise ValueError(
                "media_review_report_path is consumed only by source_media_review=reviewed-report"
            )
        return self


class CleanupConfig(BaseModel):
    mode: Literal["off", "local-llm", "apple-foundation"] = "off"
    presentation_decoder: Literal["global-viterbi-v1"] = "global-viterbi-v1"
    quality_profile: Literal[
        "semantically-faithful-clean-v1",
        "strict-verbatim-v1",
    ] = "semantically-faithful-clean-v1"
    endpoint: str = "http://127.0.0.1:11434/v1"
    model: str | None = None
    scope: Literal["punctuation", "readability"] = "punctuation"
    allow_remote_cleanup: bool = False
    write_diff: bool = False
    max_word_delta_ratio: float = 0.15
    required_complete: bool = False
    proper_nouns: list[str] = Field(default_factory=list, max_length=128)
    presentation_scorers: PresentationScorersConfig = Field(
        default_factory=lambda: PresentationScorersConfig()
    )

    @field_validator("proper_nouns")
    @classmethod
    def validate_proper_nouns(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            candidate = value.strip()
            if not candidate or len(candidate) > 80:
                raise ValueError("cleanup proper nouns must contain 1 to 80 characters")
            if any(unicodedata.category(character).startswith("C") for character in candidate):
                raise ValueError("cleanup proper nouns must not contain control characters")
            key = unicodedata.normalize("NFKC", candidate).casefold()
            if key in seen:
                raise ValueError("cleanup proper nouns must be unique ignoring case")
            seen.add(key)
            normalized.append(candidate)
        return normalized

    @model_validator(mode="after")
    def validate_endpoint_policy(self) -> CleanupConfig:
        if self.required_complete and self.mode == "off":
            raise ValueError("cleanup.required_complete=true requires an enabled cleanup mode")
        if self.mode == "apple-foundation" and self.model is not None:
            raise ValueError(
                "cleanup.model is not configurable for the Apple Foundation Models backend"
            )
        if (
            self.required_complete
            and self.mode == "local-llm"
            and (self.model is None or not self.model.strip())
        ):
            raise ValueError(
                "cleanup.required_complete=true with local-llm requires an explicit model"
            )
        try:
            validate_cleanup_policy(
                mode=self.mode,
                endpoint=self.endpoint,
                allow_remote_cleanup=self.allow_remote_cleanup,
            )
        except CleanupRejectedError as exc:
            raise ValueError(str(exc)) from exc
        return self


class SaTPresentationScorerConfig(BaseModel):
    """Local, exact-pinned SaT sentence-boundary proposal source."""

    mode: Literal["off", "optional", "required"] = "optional"
    model_dir: Path | None = None
    tokenizer_dir: Path | None = None
    signal_weight: float = Field(default=0.75, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_local_assets(self) -> SaTPresentationScorerConfig:
        configured = (self.model_dir is not None, self.tokenizer_dir is not None)
        if self.mode == "off" and any(configured):
            raise ValueError("SaT paths are not consumed while the scorer is off")
        if configured[0] != configured[1]:
            raise ValueError("SaT model_dir and tokenizer_dir must be configured together")
        if self.mode == "required" and not all(configured):
            raise ValueError("required SaT scoring needs exact local model and tokenizer paths")
        return self


class TokenPresentationScorerConfig(BaseModel):
    """Production routing contract for an approved word-level classifier.

    No checkpoint currently clears Auralign's conversational-quality and
    provenance gates, so the only selectable built-in backend is ``none``.
    The executable adapter is exercised with deterministic test providers;
    adding a real backend requires an exact registered model contract.
    """

    mode: Literal["off", "optional", "required"] = "off"
    backend: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_registered_backend(self) -> TokenPresentationScorerConfig:
        if self.mode != "off":
            raise ValueError("no word-level punctuation/casing checkpoint is production-approved")
        return self


class PresentationScorersConfig(BaseModel):
    sat: SaTPresentationScorerConfig = Field(default_factory=SaTPresentationScorerConfig)
    token_classifier: TokenPresentationScorerConfig = Field(
        default_factory=TokenPresentationScorerConfig
    )


class SubtitleExportConfig(BaseModel):
    split_long_cues: bool = True
    max_line_width: int | None = Field(default=120, gt=0)
    max_line_count: int | None = Field(default=4, gt=0)
    max_cue_duration: float = Field(default=30.0, gt=0.0, le=60.0)
    speaker_labels: bool = True
    use_mapped_names: bool = True
    highlight_words: bool = False


class RuntimeConfig(BaseModel):
    model_lifecycle: Literal["auto", "persistent", "per-job"] = "auto"


class PipelineConfig(BaseModel):
    preset: PresetName = "max"
    model_cache: Path = Path("models")
    offline: bool = False
    audio: AudioConfig = Field(default_factory=AudioConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    diarization: DiarizationConfig = Field(default_factory=DiarizationConfig)
    agentic_diarization: AgenticDiarizationConfig = Field(default_factory=AgenticDiarizationConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    redecode: RedecodeConfig = Field(default_factory=RedecodeConfig)
    speaker_id: SpeakerIDConfig = Field(default_factory=SpeakerIDConfig)
    speaker_identity: IdentityResolverConfig = Field(default_factory=IdentityResolverConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    subtitle_export: SubtitleExportConfig = Field(default_factory=SubtitleExportConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    @field_validator("cleanup")
    @classmethod
    def validate_cleanup_offline_policy(
        cls,
        cleanup: CleanupConfig,
        info: ValidationInfo,
    ) -> CleanupConfig:
        try:
            validate_cleanup_policy(
                mode=cleanup.mode,
                endpoint=cleanup.endpoint,
                allow_remote_cleanup=cleanup.allow_remote_cleanup,
                offline=bool(info.data.get("offline", False)),
            )
        except CleanupRejectedError as exc:
            raise ValueError(str(exc)) from exc
        return cleanup

    @model_validator(mode="after")
    def validate_adaptive_redecode_requirements(self) -> PipelineConfig:
        validate_adaptive_selection(self)
        return self


def validate_adaptive_selection(config: PipelineConfig) -> None:
    """Fail closed when adaptive selection lacks its required local evidence.

    Raises ``ValueError`` so the message reads identically from Pydantic
    validation and from a direct CLI override.
    """

    if config.redecode.selection_policy != "adaptive-safe":
        return
    if not config.qa.enabled:
        raise ValueError(
            "redecode.selection_policy=adaptive-safe requires qa.enabled=true: "
            "suspicious spans and regression gates come from typed QA evidence."
        )
    if not config.alignment.enabled or config.alignment.backend not in {"auto", "forced"}:
        raise ValueError(
            "redecode.selection_policy=adaptive-safe requires forced alignment "
            "(alignment.backend=auto or forced). Whisper word timestamps come from the "
            "same decode being judged and are not independent evidence."
        )
    if config.asr.backend != "faster-whisper":
        raise ValueError(
            "redecode.selection_policy=adaptive-safe requires the faster-whisper ASR "
            f"backend; '{config.asr.backend}' does not support beam search."
        )
    if config.cleanup.mode != "off":
        raise ValueError(
            "redecode.selection_policy=adaptive-safe cannot publish a derived lane while "
            "cleanup is enabled: cleanup depends on the selected ASR text and is not "
            "regenerated inside the derived lane. Disable one of them."
        )
    if config.speaker_identity.mode != "off":
        raise ValueError(
            "redecode.selection_policy=adaptive-safe cannot publish a derived lane while "
            "the speaker-identity resolver is enabled: identity composition depends on "
            "the selected ASR text and is not regenerated inside the derived lane. "
            "Disable one of them."
        )


def required_model_requests(config: PipelineConfig) -> tuple[tuple[str, str], ...]:
    """Return deterministic model roles and selectors required by ``config``."""

    requests: list[tuple[str, str]] = []
    if config.asr.backend == "mlx-whisper":
        requests.append(("asr-mlx", config.asr.mlx_model))
    else:
        requests.append(("asr", config.asr.model))
    if diarization_runtime_enabled(config.diarization):
        requests.append(("diarization", config.diarization.model))
    if config.alignment.enabled and config.alignment.backend in {"auto", "forced"}:
        requests.append(("alignment", config.alignment.model or "auto"))
    if config.speaker_id.mode == "auto":
        requests.append(("speaker-embedding", config.speaker_id.model))
    return tuple(requests)


def default_model_cache(cwd: Path | None = None) -> Path:
    value = os.getenv("LTM_MODEL_CACHE")
    if value:
        return Path(value).expanduser()
    return (cwd or Path.cwd()) / "models"


def default_hf_home(cwd: Path | None = None) -> Path:
    return Path(os.getenv("HF_HOME", str((cwd or Path.cwd()) / ".hf-cache"))).expanduser()


def default_hf_hub_cache(cwd: Path | None = None) -> Path:
    return Path(os.getenv("HF_HUB_CACHE", str(default_hf_home(cwd) / "hub"))).expanduser()


def hf_token() -> str | None:
    return os.getenv("LTM_HF_TOKEN") or os.getenv("HF_TOKEN")


def token_present() -> bool:
    return bool(hf_token())


def _env_positive_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise LTMError(f"{name} must be a positive integer when set.") from exc
    if parsed <= 0:
        raise LTMError(f"{name} must be a positive integer when set.")
    return parsed


def _env_non_negative_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise LTMError(f"{name} must be a non-negative number when set.") from exc
    if parsed < 0:
        raise LTMError(f"{name} must be a non-negative number when set.")
    return parsed


def preset_config(preset: PresetName, cwd: Path | None = None) -> PipelineConfig:
    model_cache = default_model_cache(cwd)
    offline = env_flag("LTM_OFFLINE")
    asr_backend_override = os.getenv("LTM_ASR_BACKEND")
    asr_mlx_model_override = os.getenv("LTM_ASR_MLX_MODEL")
    asr_mlx_model_path_override = os.getenv("LTM_ASR_MLX_MODEL_PATH")
    asr_device_override = os.getenv("LTM_ASR_DEVICE") or os.getenv("LTM_DEVICE")
    diarization_device_override = os.getenv("LTM_DIARIZATION_DEVICE") or os.getenv("LTM_DEVICE")
    alignment_device_override = os.getenv("LTM_ALIGNMENT_DEVICE")
    model_lifecycle_override = os.getenv("LTM_MODEL_LIFECYCLE")
    config = PipelineConfig(preset=preset, model_cache=model_cache, offline=offline)
    if preset == "balanced":
        config.asr.model = "turbo"
        config.asr.beam_size = 5
        config.asr.compute_type = "int8_float32"
    elif preset == "fast":
        config.asr.model = "distil-large-v3.5"
        config.asr.language = "en"
        config.asr.beam_size = 1
        config.asr.compute_type = "int8"
        config.alignment.backend = "fallback"
    elif preset == "cpu":
        config.asr.model = "turbo"
        config.asr.device = "cpu"
        config.asr.compute_type = "int8"
        config.asr.beam_size = 1
    if asr_backend_override:
        config.asr.backend = _validate_asr_backend(asr_backend_override)
    if asr_mlx_model_override:
        config.asr.mlx_model = asr_mlx_model_override
    if asr_mlx_model_path_override:
        config.asr.mlx_model_path = Path(asr_mlx_model_path_override).expanduser()
    if os.getenv("LTM_ASR_MLX_FP16") is not None:
        config.asr.mlx_fp16 = env_flag("LTM_ASR_MLX_FP16", config.asr.mlx_fp16)
    if os.getenv("LTM_ASR_MLX_VAD_WINDOWED") is not None:
        config.asr.mlx_vad_windowed = env_flag(
            "LTM_ASR_MLX_VAD_WINDOWED", config.asr.mlx_vad_windowed
        )
    mlx_vad_window_seconds = _env_non_negative_float("LTM_ASR_MLX_VAD_WINDOW_SECONDS")
    if mlx_vad_window_seconds is not None:
        if mlx_vad_window_seconds < 1.0:
            raise LTMError("LTM_ASR_MLX_VAD_WINDOW_SECONDS must be at least 1 second when set.")
        config.asr.mlx_vad_window_seconds = mlx_vad_window_seconds
    mlx_vad_window_padding_seconds = _env_non_negative_float(
        "LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS"
    )
    if mlx_vad_window_padding_seconds is not None:
        config.asr.mlx_vad_window_padding_seconds = mlx_vad_window_padding_seconds
    if asr_device_override:
        config.asr.device = _validate_asr_device(asr_device_override)
    if diarization_device_override:
        config.diarization.device = _validate_diarization_device(diarization_device_override)
    if alignment_device_override:
        config.alignment.device = _validate_alignment_device(alignment_device_override)
    if model_lifecycle_override:
        config.runtime.model_lifecycle = _validate_model_lifecycle(model_lifecycle_override)
    config.alignment.allow_experimental_mps = env_flag(
        "LTM_ALIGNMENT_ALLOW_EXPERIMENTAL_MPS",
        config.alignment.allow_experimental_mps,
    )
    asr_cpu_threads = _env_positive_int("LTM_ASR_CPU_THREADS")
    if asr_cpu_threads is not None:
        config.asr.cpu_threads = asr_cpu_threads
    asr_num_workers = _env_positive_int("LTM_ASR_NUM_WORKERS")
    if asr_num_workers is not None:
        config.asr.num_workers = asr_num_workers
    if os.getenv("LTM_ASR_BATCHED") is not None:
        config.asr.batched = env_flag("LTM_ASR_BATCHED")
    asr_batch_size = _env_positive_int("LTM_ASR_BATCH_SIZE")
    if asr_batch_size is not None:
        config.asr.batch_size = asr_batch_size
    return config


def _deep_update(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _normalize_yaml_literals(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            child_key: _normalize_yaml_literals(child_value, child_key)
            for child_key, child_value in value.items()
        }
    if value is False and key in {"backend", "mode", "denoise"}:
        return "off"
    return value


def load_config(
    preset: PresetName = "max",
    config_file: Path | None = None,
    cwd: Path | None = None,
) -> PipelineConfig:
    config = preset_config(preset, cwd=cwd)
    if config_file:
        try:
            with config_file.expanduser().open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            location = (
                f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            )
            raise LTMError(f"Invalid config file: yaml_syntax{location}.") from exc
        except UnicodeError as exc:
            raise LTMError("Invalid config file: invalid_utf8_encoding.") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise LTMError("Invalid config file: configuration (mapping_type).")
        loaded = _normalize_yaml_literals(loaded)
        merged = _deep_update(config.model_dump(mode="python"), loaded)
        try:
            config = PipelineConfig.model_validate(merged)
        except ValidationError as exc:
            raise LTMError(f"Invalid config file: {configuration_validation_summary(exc)}") from exc
    if os.getenv("LTM_MODEL_CACHE"):
        config.model_cache = default_model_cache(cwd)
    config.offline = env_flag("LTM_OFFLINE", config.offline)
    if config.qa.targeted_redecode and config.redecode.mode == "off":
        config.redecode.mode = "low-confidence"
    if os.getenv("LTM_ASR_DEVICE") or os.getenv("LTM_DEVICE"):
        config.asr.device = _validate_asr_device(
            os.environ.get("LTM_ASR_DEVICE", os.environ.get("LTM_DEVICE", "auto"))
        )
    if os.getenv("LTM_ASR_BACKEND"):
        config.asr.backend = _validate_asr_backend(os.environ["LTM_ASR_BACKEND"])
    if os.getenv("LTM_ASR_MLX_MODEL"):
        config.asr.mlx_model = os.environ["LTM_ASR_MLX_MODEL"]
    if os.getenv("LTM_ASR_MLX_MODEL_PATH"):
        config.asr.mlx_model_path = Path(os.environ["LTM_ASR_MLX_MODEL_PATH"]).expanduser()
    if os.getenv("LTM_ASR_MLX_FP16") is not None:
        config.asr.mlx_fp16 = env_flag("LTM_ASR_MLX_FP16", config.asr.mlx_fp16)
    if os.getenv("LTM_ASR_MLX_VAD_WINDOWED") is not None:
        config.asr.mlx_vad_windowed = env_flag(
            "LTM_ASR_MLX_VAD_WINDOWED", config.asr.mlx_vad_windowed
        )
    mlx_vad_window_seconds = _env_non_negative_float("LTM_ASR_MLX_VAD_WINDOW_SECONDS")
    if mlx_vad_window_seconds is not None:
        if mlx_vad_window_seconds < 1.0:
            raise LTMError("LTM_ASR_MLX_VAD_WINDOW_SECONDS must be at least 1 second when set.")
        config.asr.mlx_vad_window_seconds = mlx_vad_window_seconds
    mlx_vad_window_padding_seconds = _env_non_negative_float(
        "LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS"
    )
    if mlx_vad_window_padding_seconds is not None:
        config.asr.mlx_vad_window_padding_seconds = mlx_vad_window_padding_seconds
    if os.getenv("LTM_DIARIZATION_DEVICE") or os.getenv("LTM_DEVICE"):
        config.diarization.device = _validate_diarization_device(
            os.environ.get(
                "LTM_DIARIZATION_DEVICE",
                os.environ.get("LTM_DEVICE", "auto"),
            )
        )
    if os.getenv("LTM_ALIGNMENT_DEVICE"):
        config.alignment.device = _validate_alignment_device(os.environ["LTM_ALIGNMENT_DEVICE"])
    if os.getenv("LTM_MODEL_LIFECYCLE"):
        config.runtime.model_lifecycle = _validate_model_lifecycle(
            os.environ["LTM_MODEL_LIFECYCLE"]
        )
    config.alignment.allow_experimental_mps = env_flag(
        "LTM_ALIGNMENT_ALLOW_EXPERIMENTAL_MPS",
        config.alignment.allow_experimental_mps,
    )
    asr_cpu_threads = _env_positive_int("LTM_ASR_CPU_THREADS")
    if asr_cpu_threads is not None:
        config.asr.cpu_threads = asr_cpu_threads
    asr_num_workers = _env_positive_int("LTM_ASR_NUM_WORKERS")
    if asr_num_workers is not None:
        config.asr.num_workers = asr_num_workers
    if os.getenv("LTM_ASR_BATCHED") is not None:
        config.asr.batched = env_flag("LTM_ASR_BATCHED", config.asr.batched)
    asr_batch_size = _env_positive_int("LTM_ASR_BATCH_SIZE")
    if asr_batch_size is not None:
        config.asr.batch_size = asr_batch_size
    if os.getenv("LTM_CLEANUP_ENDPOINT"):
        config.cleanup.endpoint = os.environ["LTM_CLEANUP_ENDPOINT"]
    try:
        return PipelineConfig.model_validate(config.model_dump(mode="python"))
    except ValidationError as exc:
        raise LTMError(
            f"Invalid effective configuration: {configuration_validation_summary(exc)}"
        ) from exc


def configuration_validation_summary(exc: ValidationError) -> str:
    """Describe fields and categories without private inputs or validator messages."""

    known_fields: set[str] = set()
    pending: list[type[BaseModel]] = [PipelineConfig]
    visited: set[type[BaseModel]] = set()
    while pending:
        model = pending.pop()
        if model in visited:
            continue
        visited.add(model)
        known_fields.update(model.model_fields)
        for field in model.model_fields.values():
            nested = field.annotation
            if isinstance(nested, type) and issubclass(nested, BaseModel):
                pending.append(nested)
    summaries: list[str] = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        location = ".".join(
            str(part) if isinstance(part, int) or part in known_fields else "<field>"
            for part in error["loc"]
        )
        summaries.append(f"{location or 'configuration'} ({error['type']})")
    return "; ".join(summaries)


def _validate_asr_backend(value: str) -> Literal["faster-whisper", "mlx-whisper"]:
    if value not in {"faster-whisper", "mlx-whisper"}:
        raise LTMError("LTM_ASR_BACKEND must be one of: faster-whisper, mlx-whisper.")
    return cast(Literal["faster-whisper", "mlx-whisper"], value)


def _validate_asr_device(value: str) -> Literal["auto", "cpu", "cuda"]:
    if value not in {"auto", "cpu", "cuda"}:
        raise LTMError(
            "LTM_ASR_DEVICE/LTM_DEVICE must be one of: auto, cpu, cuda. "
            "Use LTM_DIARIZATION_DEVICE=mps for an explicit pyannote MPS experiment."
        )
    return cast(Literal["auto", "cpu", "cuda"], value)


def _validate_diarization_device(value: str) -> Literal["auto", "cpu", "cuda", "mps"]:
    if value not in {"auto", "cpu", "cuda", "mps"}:
        raise LTMError("LTM_DIARIZATION_DEVICE/LTM_DEVICE must be one of: auto, cpu, cuda, mps.")
    return cast(Literal["auto", "cpu", "cuda", "mps"], value)


def _validate_alignment_device(value: str) -> Literal["auto", "cpu", "cuda", "mps"]:
    if value not in {"auto", "cpu", "cuda", "mps"}:
        raise LTMError("LTM_ALIGNMENT_DEVICE must be one of: auto, cpu, cuda, mps.")
    return cast(Literal["auto", "cpu", "cuda", "mps"], value)


def _validate_model_lifecycle(
    value: str,
) -> Literal["auto", "persistent", "per-job"]:
    if value not in {"auto", "persistent", "per-job"}:
        raise LTMError("LTM_MODEL_LIFECYCLE must be one of: auto, persistent, per-job.")
    return cast(Literal["auto", "persistent", "per-job"], value)


def model_local_path(entry: ModelEntry, cache_dir: Path) -> Path:
    return cache_dir.expanduser() / entry.directory_name


def model_present(entry: ModelEntry, cache_dir: Path) -> bool:
    path = model_local_path(entry, cache_dir)
    if not path.exists():
        return False
    required_ready = all((path / required).exists() for required in entry.required_files)
    if entry.any_required_files:
        return required_ready and any(
            (path / required).exists() for required in entry.any_required_files
        )
    return required_ready
