# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Exact-pinned, local SaT proposal source for transcript presentation.

The source accepts only pre-provisioned allowlisted files, performs no model
acquisition, and imports its optional runtime only after exact verification.
SaT contributes lexical sentence-boundary evidence only: timing, speaker turns,
subtitle cues, and paragraph structure are deliberately outside this adapter.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import re
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, cast

from .platform_security import UnsafePathError, reject_path_indirection
from .presentation import (
    DecoderWord,
    EvidenceRole,
    PresentationProposal,
    ScorerOutput,
    ScorerProvenance,
    TrailingPunctuation,
    lexical_text_digest,
)

WTPSPLIT_VERSION = "2.2.1"
WTPSPLIT_REVISION = "e48922c0ad240c7d2cb1ef8afbc0cd413372bc83"
SAT_MODEL_ID = "segment-any-text/sat-3l-sm"
SAT_MODEL_REVISION = "137da054051ad9f1eac42025f758db4ac9f22535"
SAT_TOKENIZER_ID = "facebookAI/xlm-roberta-base"
SAT_TOKENIZER_REVISION = "e73636d4f797dec63c3081bb6ed5c7b0bb3f2089"
SAT_SOURCE_KEY = "sat-local"
SAT_RECEIPT_SCHEMA = "auralign.presentation.sat.receipt.v2"
_DIGEST_LENGTH = 64
_HASH_CHUNK_BYTES = 1024 * 1024
_PROBABILITY_EPSILON = 1e-9
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}$")


def _require_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_probability(value: float, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{label} must be a finite probability")


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a stable identifier")


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """One exact local file required by the production scorer."""

    size: int
    sha256: str

    def __post_init__(self) -> None:
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ValueError("artifact size must be a non-negative integer")
        _require_sha256(self.sha256, "artifact sha256")


SAT_MODEL_FILES: Mapping[str, ArtifactFile] = MappingProxyType(
    {
        "config.json": ArtifactFile(
            size=965,
            sha256="d61498bc239f773126ee794446cdcad88822967e0b02ae7bd070f725c98be791",
        ),
        "model_optimized.onnx": ArtifactFile(
            size=427_756_189,
            sha256="8573277b4dbea9c5fb1b4cfd8c21e5aa628069ac8258d1342ba664e1b64ada6d",
        ),
    }
)
SAT_TOKENIZER_FILES: Mapping[str, ArtifactFile] = MappingProxyType(
    {
        "config.json": ArtifactFile(
            size=615,
            sha256="d66ed8cd4f2a93b358c245e50736fa389ed4f35c0bae7aad0b32abb20c62b579",
        ),
        "sentencepiece.bpe.model": ArtifactFile(
            size=5_069_051,
            sha256="cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
        ),
        "tokenizer.json": ArtifactFile(
            size=9_096_718,
            sha256="a898ea75433890f6610f4e470b8ebeb0c21dce5c8dd61f892eb09eb5919d2e2c",
        ),
        "tokenizer_config.json": ArtifactFile(
            size=25,
            sha256="994f46754c5bf4014f1aa92d34b1374319c3a6b3f702105cd5b742beaecd18ce",
        ),
    }
)


class SaTMode(StrEnum):
    """Config-facing execution policy for the registered proposal source."""

    OFF = "off"
    OPTIONAL = "optional"
    REQUIRED = "required"


class SaTScoreStatus(StrEnum):
    """Deterministic scorer receipt status."""

    SUCCEEDED = "succeeded"
    ABSTAINED = "abstained"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SaTSourceOptions:
    """Values root/config integration must project into this adapter."""

    mode: SaTMode = SaTMode.OFF
    model_dir: Path | None = None
    tokenizer_dir: Path | None = None
    signal_weight: float = 1.0
    offline: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SaTMode):
            raise ValueError("SaT mode is invalid")
        if not isinstance(self.offline, bool):
            raise ValueError("SaT offline flag must be boolean")
        _require_probability(self.signal_weight, "SaT signal_weight")
        if self.mode is SaTMode.OFF:
            if self.model_dir is not None or self.tokenizer_dir is not None:
                raise ValueError("SaT off mode must not retain model paths")
            return
        if (self.model_dir is None) != (self.tokenizer_dir is None):
            raise ValueError("SaT model and tokenizer paths must be configured together")
        if self.mode is SaTMode.REQUIRED and self.model_dir is None:
            raise ValueError("SaT required mode needs exact model and tokenizer paths")
        if self.model_dir is not None and (
            not isinstance(self.model_dir, Path) or not isinstance(self.tokenizer_dir, Path)
        ):
            raise ValueError("SaT model and tokenizer paths must be pathlib.Path values")


@dataclass(frozen=True, slots=True)
class BoundaryCalibration:
    """Pinned transform from backend probability to proposal signal.

    The built-in authority deliberately uses the upstream ``predict_proba``
    value unchanged.  It is an explicit, reproducible calibration boundary,
    not a claim that cross-backend confidence has been empirically calibrated.
    """

    calibration_id: str
    method: Literal["identity", "logit_affine"]
    temperature: float
    logit_bias: float
    evidence_digest: str

    def __post_init__(self) -> None:
        if (
            not self.calibration_id
            or not self.calibration_id.replace("-", "").replace(".", "").isalnum()
        ):
            raise ValueError("calibration_id must be a stable identifier")
        if self.method not in {"identity", "logit_affine"}:
            raise ValueError("unsupported SaT calibration method")
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (float, int))
            or not math.isfinite(self.temperature)
            or self.temperature <= 0
        ):
            raise ValueError("calibration temperature must be finite and positive")
        if (
            isinstance(self.logit_bias, bool)
            or not isinstance(self.logit_bias, (float, int))
            or not math.isfinite(self.logit_bias)
        ):
            raise ValueError("calibration logit_bias must be finite")
        _require_sha256(self.evidence_digest, "calibration evidence_digest")

    def apply(self, probability: float, *, signal_weight: float) -> float:
        """Return a deterministic probability, shrunk toward neutral by weight."""

        _require_probability(probability, "raw SaT boundary probability")
        _require_probability(signal_weight, "SaT signal_weight")
        if self.method == "identity":
            calibrated = probability
        else:
            bounded = min(1.0 - _PROBABILITY_EPSILON, max(_PROBABILITY_EPSILON, probability))
            logit = math.log(bounded / (1.0 - bounded))
            calibrated = 1.0 / (1.0 + math.exp(-((logit + self.logit_bias) / self.temperature)))
        return 0.5 + signal_weight * (calibrated - 0.5)


SAT_BOUNDARY_CALIBRATION = BoundaryCalibration(
    calibration_id="sat-3l-sm-upstream-probability-identity-v1",
    method="identity",
    temperature=1.0,
    logit_bias=0.0,
    evidence_digest=hashlib.sha256(
        (
            f"{SAT_MODEL_ID}@{SAT_MODEL_REVISION}:"
            "wtpsplit-predict-proba-identity:no-cross-backend-confidence-claim"
        ).encode()
    ).hexdigest(),
)


@dataclass(frozen=True, slots=True)
class VerifiedAssetReceipt:
    """Privacy-safe proof that one exact asset contract was reverified."""

    identifier: str
    revision: str
    manifest_digest: str
    file_count: int
    verified_bytes: int
    verification_passes: int

    def __post_init__(self) -> None:
        _require_identifier(self.identifier, "verified asset identifier")
        if (
            not isinstance(self.revision, str)
            or len(self.revision) != 40
            or any(character not in "0123456789abcdef" for character in self.revision)
        ):
            raise ValueError("verified asset revision must be an immutable commit")
        _require_sha256(self.manifest_digest, "verified asset manifest_digest")
        if (
            isinstance(self.file_count, bool)
            or not isinstance(self.file_count, int)
            or self.file_count <= 0
        ):
            raise ValueError("verified asset file_count must be positive")
        if (
            isinstance(self.verified_bytes, bool)
            or not isinstance(self.verified_bytes, int)
            or self.verified_bytes <= 0
        ):
            raise ValueError("verified asset byte count must be positive")
        if isinstance(self.verification_passes, bool) or self.verification_passes not in {
            1,
            2,
        }:
            raise ValueError("verified asset pass count must be one or two")


@dataclass(frozen=True, slots=True)
class SaTScoreReceipt:
    """Deterministic, path-free receipt for one production scoring attempt."""

    status: SaTScoreStatus
    reason_code: str | None
    mode: SaTMode
    abstention_visible: bool
    offline_enforced: bool
    network_access: bool
    word_count: int
    input_digest: str | None
    lexical_input_digest: str | None
    scorer_provenance: ScorerProvenance
    calibration: BoundaryCalibration
    signal_weight: float
    model_asset: VerifiedAssetReceipt | None
    tokenizer_asset: VerifiedAssetReceipt | None
    proposal_count: int
    output_digest: str | None
    turn_features_used: bool = False
    cue_boundaries_used: bool = False
    schema_version: str = SAT_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.status, SaTScoreStatus) or not isinstance(self.mode, SaTMode):
            raise ValueError("SaT receipt status or mode is invalid")
        if self.schema_version != SAT_RECEIPT_SCHEMA:
            raise ValueError("SaT receipt schema version is invalid")
        for label, value in (
            ("abstention_visible", self.abstention_visible),
            ("offline_enforced", self.offline_enforced),
            ("network_access", self.network_access),
            ("turn_features_used", self.turn_features_used),
            ("cue_boundaries_used", self.cue_boundaries_used),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"SaT receipt {label} must be boolean")
        if not isinstance(self.scorer_provenance, ScorerProvenance):
            raise ValueError("SaT receipt scorer provenance is invalid")
        if not isinstance(self.calibration, BoundaryCalibration):
            raise ValueError("SaT receipt calibration is invalid")
        if self.mode is SaTMode.OFF:
            raise ValueError("off mode does not produce a SaT receipt")
        _require_probability(self.signal_weight, "SaT receipt signal_weight")
        zero_signal_success = self.status is SaTScoreStatus.SUCCEEDED and self.signal_weight == 0.0
        if self.status is SaTScoreStatus.SUCCEEDED:
            if zero_signal_success:
                if self.reason_code != "signal_weight_zero" or not self.abstention_visible:
                    raise ValueError(
                        "zero-signal SaT success must visibly abstain with its exact reason"
                    )
            elif self.reason_code is not None:
                raise ValueError("successful SaT receipt cannot contain a reason code")
        elif not self.reason_code:
            raise ValueError("non-success SaT receipt requires a reason code")
        if self.reason_code is not None:
            _require_identifier(self.reason_code, "SaT receipt reason_code")
        if zero_signal_success:
            pass
        elif self.status is SaTScoreStatus.ABSTAINED:
            if self.mode is not SaTMode.OPTIONAL or not self.abstention_visible:
                raise ValueError("only optional SaT mode can visibly abstain")
        elif self.status is SaTScoreStatus.FAILED:
            if self.mode is not SaTMode.REQUIRED or self.abstention_visible:
                raise ValueError("only required SaT mode can fail")
        elif self.abstention_visible:
            raise ValueError("successful SaT scoring cannot report an abstention")
        if not self.offline_enforced or self.network_access:
            raise ValueError("SaT receipts must prove local/offline execution")
        if self.turn_features_used or self.cue_boundaries_used:
            raise ValueError("SaT must not conflate turns or cues with sentence boundaries")
        if isinstance(self.word_count, bool) or not isinstance(self.word_count, int):
            raise ValueError("SaT receipt word_count must be an integer")
        if self.word_count < 0:
            raise ValueError("SaT receipt word_count must be non-negative")
        for digest in (self.input_digest, self.lexical_input_digest, self.output_digest):
            if digest is not None:
                _require_sha256(digest, "SaT receipt digest")
        if (self.input_digest is None) != (self.lexical_input_digest is None):
            raise ValueError("SaT receipt input digests must be present together")
        if self.output_digest is None:
            raise ValueError("SaT receipt must bind its scorer output")
        if (
            isinstance(self.proposal_count, bool)
            or not isinstance(self.proposal_count, int)
            or self.proposal_count < 0
        ):
            raise ValueError("SaT receipt proposal_count must be non-negative")
        if self.status is SaTScoreStatus.SUCCEEDED:
            if (
                self.word_count == 0
                or self.input_digest is None
                or self.lexical_input_digest is None
                or self.model_asset is None
                or self.tokenizer_asset is None
                or self.model_asset.verification_passes != 2
                or self.tokenizer_asset.verification_passes != 2
            ):
                raise ValueError("successful SaT receipt is incomplete")
            expected_proposal_count = (
                0 if zero_signal_success else self.word_count * len(TrailingPunctuation)
            )
            if self.proposal_count != expected_proposal_count:
                raise ValueError("successful SaT receipt proposal count is invalid")
        elif self.proposal_count != 0:
            raise ValueError("unsuccessful SaT receipts cannot publish proposals")


@dataclass(frozen=True, slots=True)
class SaTScoreResult:
    """Presentation-compatible scorer output paired with its exact receipt."""

    output: ScorerOutput
    receipt: SaTScoreReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.output, ScorerOutput) or not isinstance(
            self.receipt, SaTScoreReceipt
        ):
            raise ValueError("SaT score result members are invalid")
        if self.receipt.status is SaTScoreStatus.FAILED:
            raise ValueError("required SaT failures must be raised, not returned")
        if self.output.provenance != self.receipt.scorer_provenance:
            raise ValueError("SaT output and receipt provenance disagree")
        if len(self.output.proposals) != self.receipt.proposal_count:
            raise ValueError("SaT output and receipt proposal counts disagree")
        if self.receipt.status is SaTScoreStatus.SUCCEEDED:
            if self.receipt.signal_weight == 0.0:
                if self.output.abstention_reason != "signal_weight_zero":
                    raise ValueError("zero-signal SaT success must publish its abstention")
            elif self.output.abstention_reason is not None:
                raise ValueError("successful SaT output cannot abstain")
        elif self.output.abstention_reason != self.receipt.reason_code:
            raise ValueError("SaT abstention output and receipt reason disagree")
        if _scorer_output_digest(self.output) != self.receipt.output_digest:
            raise ValueError("SaT output and receipt digest disagree")


@dataclass(frozen=True, slots=True)
class ProductionProposalSourceRegistration:
    """Immutable registry descriptor for one executable proposal source."""

    key: str
    scorer_id: str
    model_id: str
    model_revision: str
    tokenizer_id: str
    tokenizer_revision: str
    runtime_extra: str
    local_only: bool
    offline_required: bool
    production: bool
    standalone_included: bool

    def __post_init__(self) -> None:
        if not self.key or not self.scorer_id or not self.runtime_extra:
            raise ValueError("proposal source registration identifiers must not be empty")
        if not (self.local_only and self.offline_required and self.production):
            raise ValueError("production SaT registration must be local/offline")
        if self.standalone_included:
            raise ValueError("SaT must remain outside the macOS 14 standalone product")


class SaTBackend(Protocol):
    """Minimal boundary-probability interface used from ``wtpsplit.SaT``."""

    def predict_proba(
        self,
        text: str,
        *,
        stride: int,
        block_size: int,
        batch_size: int,
        weighting: str,
        remove_whitespace_before_inference: bool,
        verbose: bool,
    ) -> object: ...


BackendFactory = Callable[[Path, Path], SaTBackend]
FailurePolicy = Literal["abstain", "raise"]


class SaTScorerError(RuntimeError):
    """A fail-closed scorer error with a deterministic optional receipt."""

    def __init__(self, reason: str, receipt: SaTScoreReceipt | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.receipt = receipt


class SaTBoundaryScorer:
    """Score word endings with exact local SaT assets and no network path."""

    def __init__(
        self,
        model_dir: Path,
        tokenizer_dir: Path,
        *,
        backend_factory: BackendFactory | None = None,
        calibration: BoundaryCalibration = SAT_BOUNDARY_CALIBRATION,
    ) -> None:
        if not isinstance(model_dir, Path) or not isinstance(tokenizer_dir, Path):
            raise ValueError("SaT model and tokenizer paths must be pathlib.Path values")
        self._model_dir = model_dir
        self._tokenizer_dir = tokenizer_dir
        self._backend_factory = backend_factory or _load_local_backend
        self._backend: SaTBackend | None = None
        self._calibration = calibration
        self._provenance = ScorerProvenance(
            scorer_id="wtpsplit-sat-3l-sm",
            scorer_version=WTPSPLIT_VERSION,
            configuration_digest=_configuration_digest(calibration),
        )

    @property
    def provenance(self) -> ScorerProvenance:
        """Return exact scorer identity without importing optional dependencies."""

        return self._provenance

    def score(
        self,
        words: Sequence[DecoderWord],
        *,
        failure_policy: FailurePolicy = "abstain",
        signal_weight: float = 1.0,
        offline: bool = True,
    ) -> ScorerOutput:
        """Compatibility adapter returning only ``ScorerOutput``."""

        if failure_policy not in {"abstain", "raise"}:
            raise ValueError("failure_policy must be 'abstain' or 'raise'")
        mode = SaTMode.OPTIONAL if failure_policy == "abstain" else SaTMode.REQUIRED
        return self.score_with_receipt(
            words,
            mode=mode,
            signal_weight=signal_weight,
            offline=offline,
        ).output

    def score_with_receipt(
        self,
        words: Sequence[DecoderWord],
        *,
        mode: SaTMode = SaTMode.OPTIONAL,
        signal_weight: float = 1.0,
        offline: bool = True,
    ) -> SaTScoreResult:
        """Return proposals and receipt, or raise with receipt in required mode."""

        if mode not in {SaTMode.OPTIONAL, SaTMode.REQUIRED}:
            raise ValueError("SaTBoundaryScorer requires optional or required mode")
        _require_probability(signal_weight, "SaT signal_weight")
        bound_words: tuple[DecoderWord, ...] = ()
        input_digest: str | None = None
        lexical_input_digest: str | None = None
        model_receipt: VerifiedAssetReceipt | None = None
        tokenizer_receipt: VerifiedAssetReceipt | None = None
        try:
            bound_words = _validate_words(words)
            input_digest = _input_digest(bound_words, include_context=True)
            lexical_input_digest = _input_digest(bound_words, include_context=False)
            if not offline:
                raise SaTScorerError("offline_required")
            if not bound_words:
                raise SaTScorerError("empty_input")

            if signal_weight == 0.0:
                model_receipt, tokenizer_receipt = self._verify_assets_twice()
                if input_digest != _input_digest(bound_words, include_context=True):
                    raise SaTScorerError("input_changed_during_scoring")
                if lexical_input_digest != _input_digest(bound_words, include_context=False):
                    raise SaTScorerError("input_changed_during_scoring")
                output = ScorerOutput.abstain(self._provenance, "signal_weight_zero")
                receipt = self._receipt(
                    status=SaTScoreStatus.SUCCEEDED,
                    reason_code="signal_weight_zero",
                    mode=mode,
                    word_count=len(bound_words),
                    input_digest=input_digest,
                    lexical_input_digest=lexical_input_digest,
                    signal_weight=signal_weight,
                    model_asset=model_receipt,
                    tokenizer_asset=tokenizer_receipt,
                    output=output,
                )
                return SaTScoreResult(output=output, receipt=receipt)

            backend, model_receipt, tokenizer_receipt = self._verified_backend()
            text, boundary_positions = _scorer_text_and_boundaries(bound_words)
            raw_probabilities = backend.predict_proba(
                text,
                stride=256,
                block_size=512,
                batch_size=1,
                weighting="uniform",
                remove_whitespace_before_inference=False,
                verbose=False,
            )
            probabilities = _validate_probabilities(raw_probabilities, len(text))
            proposals = _boundary_proposals(
                bound_words,
                probabilities,
                boundary_positions,
                calibration=self._calibration,
                signal_weight=signal_weight,
            )

            if input_digest != _input_digest(bound_words, include_context=True):
                raise SaTScorerError("input_changed_during_scoring")
            if lexical_input_digest != _input_digest(bound_words, include_context=False):
                raise SaTScorerError("input_changed_during_scoring")
            model_after = _verify_exact_directory(
                self._model_dir,
                SAT_MODEL_FILES,
                identifier=SAT_MODEL_ID,
                revision=SAT_MODEL_REVISION,
                missing_reason="missing_model_directory",
                invalid_reason="invalid_model_artifacts",
            )[1]
            tokenizer_after = _verify_exact_directory(
                self._tokenizer_dir,
                SAT_TOKENIZER_FILES,
                identifier=SAT_TOKENIZER_ID,
                revision=SAT_TOKENIZER_REVISION,
                missing_reason="missing_tokenizer_directory",
                invalid_reason="invalid_tokenizer_artifacts",
            )[1]
            if model_after != model_receipt or tokenizer_after != tokenizer_receipt:
                raise SaTScorerError("assets_changed_during_scoring")
            model_receipt = replace(model_receipt, verification_passes=2)
            tokenizer_receipt = replace(tokenizer_receipt, verification_passes=2)

            output = ScorerOutput(provenance=self._provenance, proposals=proposals)
            receipt = self._receipt(
                status=SaTScoreStatus.SUCCEEDED,
                reason_code=None,
                mode=mode,
                word_count=len(bound_words),
                input_digest=input_digest,
                lexical_input_digest=lexical_input_digest,
                signal_weight=signal_weight,
                model_asset=model_receipt,
                tokenizer_asset=tokenizer_receipt,
                output=output,
            )
            return SaTScoreResult(output=output, receipt=receipt)
        except SaTScorerError as exc:
            return self._failure(
                exc,
                mode=mode,
                word_count=len(bound_words),
                input_digest=input_digest,
                lexical_input_digest=lexical_input_digest,
                signal_weight=signal_weight,
                model_asset=model_receipt,
                tokenizer_asset=tokenizer_receipt,
            )
        except Exception as exc:
            return self._failure(
                SaTScorerError("inference_failed"),
                mode=mode,
                word_count=len(bound_words),
                input_digest=input_digest,
                lexical_input_digest=lexical_input_digest,
                signal_weight=signal_weight,
                model_asset=model_receipt,
                tokenizer_asset=tokenizer_receipt,
                cause=exc,
            )

    def _verified_backend(
        self,
    ) -> tuple[SaTBackend, VerifiedAssetReceipt, VerifiedAssetReceipt]:
        model_dir, model_receipt = _verify_exact_directory(
            self._model_dir,
            SAT_MODEL_FILES,
            identifier=SAT_MODEL_ID,
            revision=SAT_MODEL_REVISION,
            missing_reason="missing_model_directory",
            invalid_reason="invalid_model_artifacts",
        )
        tokenizer_dir, tokenizer_receipt = _verify_exact_directory(
            self._tokenizer_dir,
            SAT_TOKENIZER_FILES,
            identifier=SAT_TOKENIZER_ID,
            revision=SAT_TOKENIZER_REVISION,
            missing_reason="missing_tokenizer_directory",
            invalid_reason="invalid_tokenizer_artifacts",
        )
        if self._backend is None:
            try:
                self._backend = self._backend_factory(model_dir, tokenizer_dir)
            except SaTScorerError:
                raise
            except (ImportError, ModuleNotFoundError) as exc:
                raise SaTScorerError("backend_unavailable") from exc
            except Exception as exc:
                raise SaTScorerError("backend_initialization_failed") from exc
        return self._backend, model_receipt, tokenizer_receipt

    def _verify_assets_twice(
        self,
    ) -> tuple[VerifiedAssetReceipt, VerifiedAssetReceipt]:
        """Verify a no-op scorer's configured assets twice without loading it."""

        _model_dir, model_before = _verify_exact_directory(
            self._model_dir,
            SAT_MODEL_FILES,
            identifier=SAT_MODEL_ID,
            revision=SAT_MODEL_REVISION,
            missing_reason="missing_model_directory",
            invalid_reason="invalid_model_artifacts",
        )
        _tokenizer_dir, tokenizer_before = _verify_exact_directory(
            self._tokenizer_dir,
            SAT_TOKENIZER_FILES,
            identifier=SAT_TOKENIZER_ID,
            revision=SAT_TOKENIZER_REVISION,
            missing_reason="missing_tokenizer_directory",
            invalid_reason="invalid_tokenizer_artifacts",
        )
        _model_after_dir, model_after = _verify_exact_directory(
            self._model_dir,
            SAT_MODEL_FILES,
            identifier=SAT_MODEL_ID,
            revision=SAT_MODEL_REVISION,
            missing_reason="missing_model_directory",
            invalid_reason="invalid_model_artifacts",
        )
        _tokenizer_after_dir, tokenizer_after = _verify_exact_directory(
            self._tokenizer_dir,
            SAT_TOKENIZER_FILES,
            identifier=SAT_TOKENIZER_ID,
            revision=SAT_TOKENIZER_REVISION,
            missing_reason="missing_tokenizer_directory",
            invalid_reason="invalid_tokenizer_artifacts",
        )
        if model_before != model_after or tokenizer_before != tokenizer_after:
            raise SaTScorerError("assets_changed_during_scoring")
        return (
            replace(model_before, verification_passes=2),
            replace(tokenizer_before, verification_passes=2),
        )

    def _failure(
        self,
        error: SaTScorerError,
        *,
        mode: SaTMode,
        word_count: int,
        input_digest: str | None,
        lexical_input_digest: str | None,
        signal_weight: float,
        model_asset: VerifiedAssetReceipt | None,
        tokenizer_asset: VerifiedAssetReceipt | None,
        cause: Exception | None = None,
    ) -> SaTScoreResult:
        status = SaTScoreStatus.ABSTAINED if mode is SaTMode.OPTIONAL else SaTScoreStatus.FAILED
        output = ScorerOutput.abstain(self._provenance, error.reason)
        receipt = self._receipt(
            status=status,
            reason_code=error.reason,
            mode=mode,
            word_count=word_count,
            input_digest=input_digest,
            lexical_input_digest=lexical_input_digest,
            signal_weight=signal_weight,
            model_asset=model_asset,
            tokenizer_asset=tokenizer_asset,
            output=output,
        )
        if mode is SaTMode.REQUIRED:
            raised = SaTScorerError(error.reason, receipt)
            if cause is not None:
                raise raised from cause
            raise raised from error
        return SaTScoreResult(output=output, receipt=receipt)

    def _receipt(
        self,
        *,
        status: SaTScoreStatus,
        reason_code: str | None,
        mode: SaTMode,
        word_count: int,
        input_digest: str | None,
        lexical_input_digest: str | None,
        signal_weight: float,
        model_asset: VerifiedAssetReceipt | None,
        tokenizer_asset: VerifiedAssetReceipt | None,
        output: ScorerOutput,
    ) -> SaTScoreReceipt:
        return SaTScoreReceipt(
            status=status,
            reason_code=reason_code,
            mode=mode,
            abstention_visible=(
                status is SaTScoreStatus.ABSTAINED
                or (status is SaTScoreStatus.SUCCEEDED and signal_weight == 0.0)
            ),
            offline_enforced=True,
            network_access=False,
            word_count=word_count,
            input_digest=input_digest,
            lexical_input_digest=lexical_input_digest,
            scorer_provenance=self._provenance,
            calibration=self._calibration,
            signal_weight=signal_weight,
            model_asset=model_asset,
            tokenizer_asset=tokenizer_asset,
            proposal_count=len(output.proposals),
            output_digest=_scorer_output_digest(output),
        )


def _load_local_backend(model_dir: Path, tokenizer_dir: Path) -> SaTBackend:
    """Load the pinned optional implementation from verified local paths only."""

    try:
        installed_version = importlib.metadata.version("wtpsplit")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SaTScorerError("backend_unavailable") from exc
    if installed_version != WTPSPLIT_VERSION:
        raise SaTScorerError("backend_version_mismatch")
    module = importlib.import_module("wtpsplit")
    constructor = module.SaT
    backend = constructor(
        str(model_dir),
        tokenizer_name_or_path=str(tokenizer_dir),
        from_pretrained_kwargs={"local_files_only": True},
        ort_providers=["CPUExecutionProvider"],
        hub_prefix=None,
    )
    return cast(SaTBackend, backend)


def _verify_exact_directory(
    directory: Path,
    expected: Mapping[str, ArtifactFile],
    *,
    identifier: str,
    revision: str,
    missing_reason: str,
    invalid_reason: str,
) -> tuple[Path, VerifiedAssetReceipt]:
    try:
        candidate = reject_path_indirection(directory)
        directory_metadata = candidate.lstat()
    except (OSError, UnsafePathError) as exc:
        raise SaTScorerError(missing_reason) from exc
    if not stat.S_ISDIR(directory_metadata.st_mode):
        raise SaTScorerError(missing_reason)
    try:
        entries = {entry.name: entry for entry in candidate.iterdir()}
    except OSError as exc:
        raise SaTScorerError(invalid_reason) from exc
    if set(entries) != set(expected):
        raise SaTScorerError(invalid_reason)
    for name, contract in sorted(expected.items()):
        _verify_exact_file(entries[name], contract, invalid_reason)
    try:
        after_metadata = candidate.lstat()
        after_entries = {entry.name for entry in candidate.iterdir()}
    except OSError as exc:
        raise SaTScorerError(invalid_reason) from exc
    if (directory_metadata.st_dev, directory_metadata.st_ino) != (
        after_metadata.st_dev,
        after_metadata.st_ino,
    ) or after_entries != set(expected):
        raise SaTScorerError(invalid_reason)
    return candidate, VerifiedAssetReceipt(
        identifier=identifier,
        revision=revision,
        manifest_digest=_asset_manifest_digest(identifier, revision, expected),
        file_count=len(expected),
        verified_bytes=sum(contract.size for contract in expected.values()),
        verification_passes=1,
    )


def _verify_exact_file(path: Path, contract: ArtifactFile, invalid_reason: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SaTScorerError(invalid_reason) from exc
    try:
        before = os.fstat(descriptor)
        path_before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != contract.size
            or (before.st_dev, before.st_ino) != (path_before.st_dev, path_before.st_ino)
        ):
            raise SaTScorerError(invalid_reason)
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, _HASH_CHUNK_BYTES):
            digest.update(chunk)
        after = os.fstat(descriptor)
        path_after = path.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (after.st_dev, after.st_ino) != (path_after.st_dev, path_after.st_ino)
            or digest.hexdigest() != contract.sha256
        ):
            raise SaTScorerError(invalid_reason)
    except OSError as exc:
        raise SaTScorerError(invalid_reason) from exc
    finally:
        os.close(descriptor)


def _validate_words(words: Sequence[DecoderWord]) -> tuple[DecoderWord, ...]:
    try:
        bound = tuple(words)
    except TypeError as exc:
        raise SaTScorerError("invalid_word_sequence") from exc
    if any(not isinstance(word, DecoderWord) for word in bound):
        raise SaTScorerError("invalid_word_sequence")
    if tuple(word.word_index for word in bound) != tuple(range(len(bound))):
        raise SaTScorerError("invalid_word_sequence")
    return bound


def _scorer_text_and_boundaries(
    words: tuple[DecoderWord, ...],
) -> tuple[str, tuple[int, ...]]:
    parts: list[str] = []
    boundaries: list[int] = []
    offset = 0
    for position, word in enumerate(words):
        if position:
            parts.append(" ")
            offset += 1
        parts.append(word.lexical_text)
        offset += len(word.lexical_text)
        boundaries.append(offset - 1)
    return "".join(parts), tuple(boundaries)


def _validate_probabilities(raw: object, expected_count: int) -> tuple[float, ...]:
    if isinstance(raw, (str, bytes, Mapping)):
        raise SaTScorerError("malformed_probabilities")
    try:
        iterator = iter(cast(Iterable[object], raw))
        probabilities_list: list[float] = []
        for value in iterator:
            if isinstance(value, (str, bytes, bool)):
                raise SaTScorerError("malformed_probabilities")
            probabilities_list.append(float(cast(float | int, value)))
        probabilities = tuple(probabilities_list)
    except SaTScorerError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise SaTScorerError("malformed_probabilities") from exc
    if len(probabilities) != expected_count or any(
        not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in probabilities
    ):
        raise SaTScorerError("malformed_probabilities")
    return probabilities


def _boundary_proposals(
    words: tuple[DecoderWord, ...],
    probabilities: tuple[float, ...],
    boundary_positions: tuple[int, ...],
    *,
    calibration: BoundaryCalibration,
    signal_weight: float,
) -> tuple[PresentationProposal, ...]:
    proposals: list[PresentationProposal] = []
    punctuation_classes = tuple(TrailingPunctuation)
    neutral_punctuation_probability = 1.0 / len(punctuation_classes)
    for word, character_index in zip(words, boundary_positions, strict=True):
        probability = calibration.apply(
            probabilities[character_index],
            signal_weight=signal_weight,
        )
        text_digest = lexical_text_digest(word.lexical_text)
        # SaT supplies sentence-boundary evidence only. Emit a complete uniform
        # punctuation distribution so the scorer is neutral among punctuation
        # classes; encoding a boundary as NONE/PERIOD would suppress questions
        # and double-count the same signal in the global decoder.
        proposals.extend(
            PresentationProposal(
                word_index=word.word_index,
                lexical_signature=word.lexical_signature,
                lexical_text_digest=text_digest,
                trailing_punctuation=punctuation,
                punctuation_probability=neutral_punctuation_probability,
                capitalization_probability=0.5,
                sentence_break_probability=probability,
                paragraph_break_probability=0.5,
                evidence_roles=(EvidenceRole.BOUNDARY_ONLY,),
            )
            for punctuation in punctuation_classes
        )
    return tuple(proposals)


def _input_digest(words: tuple[DecoderWord, ...], *, include_context: bool) -> str:
    payload: list[dict[str, object]] = []
    for word in words:
        record: dict[str, object] = {
            "word_index": word.word_index,
            "lexical_text": word.lexical_text,
            "lexical_signature": word.lexical_signature,
            "lexical_text_digest": lexical_text_digest(word.lexical_text),
        }
        if include_context:
            record.update(
                {
                    "start_seconds": word.start_seconds,
                    "end_seconds": word.end_seconds,
                    "pause_after_seconds": word.pause_after_seconds,
                    "speaker_id": word.speaker_id,
                    "begins_turn": word.begins_turn,
                    "ends_turn": word.ends_turn,
                    "incomplete_continuation": word.incomplete_continuation,
                    "unsupported_same_speaker_short_continuation": (
                        word.unsupported_same_speaker_short_continuation
                    ),
                    "question_evidence": word.question_evidence,
                }
            )
        payload.append(record)
    return _canonical_digest(
        {
            "schema": (
                "auralign.presentation.sat.input.context.v1"
                if include_context
                else "auralign.presentation.sat.input.lexical.v1"
            ),
            "words": payload,
        }
    )


def _scorer_output_digest(output: ScorerOutput) -> str:
    return _canonical_digest(
        {
            "provenance": _provenance_payload(output.provenance),
            "abstention_reason": output.abstention_reason,
            "proposals": [
                {
                    "word_index": proposal.word_index,
                    "lexical_signature": proposal.lexical_signature,
                    "lexical_text_digest": proposal.lexical_text_digest,
                    "trailing_punctuation": proposal.trailing_punctuation.value,
                    "punctuation_probability": proposal.punctuation_probability,
                    "capitalization_probability": proposal.capitalization_probability,
                    "sentence_break_probability": proposal.sentence_break_probability,
                    "paragraph_break_probability": proposal.paragraph_break_probability,
                    "casing_surface": proposal.casing_surface,
                    "casing_probability": proposal.casing_probability,
                    "evidence_roles": [role.value for role in proposal.evidence_roles],
                }
                for proposal in output.proposals
            ],
        }
    )


def serialize_sat_score_receipt(receipt: SaTScoreReceipt) -> bytes:
    """Return canonical JSON bytes for an artifact-bound SaT score receipt."""

    if not isinstance(receipt, SaTScoreReceipt):
        raise ValueError("receipt must be a SaTScoreReceipt")
    return _canonical_bytes(sat_score_receipt_to_dict(receipt))


def sat_score_receipt_to_dict(receipt: SaTScoreReceipt) -> dict[str, object]:
    """Return a stable, path-free object suitable for cleanup/run reports."""

    if not isinstance(receipt, SaTScoreReceipt):
        raise ValueError("receipt must be a SaTScoreReceipt")
    return {
        "schema_version": receipt.schema_version,
        "status": receipt.status.value,
        "reason_code": receipt.reason_code,
        "mode": receipt.mode.value,
        "abstention_visible": receipt.abstention_visible,
        "offline_enforced": receipt.offline_enforced,
        "network_access": receipt.network_access,
        "word_count": receipt.word_count,
        "input_digest": receipt.input_digest,
        "lexical_input_digest": receipt.lexical_input_digest,
        "scorer_provenance": _provenance_payload(receipt.scorer_provenance),
        "calibration": {
            "calibration_id": receipt.calibration.calibration_id,
            "method": receipt.calibration.method,
            "temperature": receipt.calibration.temperature,
            "logit_bias": receipt.calibration.logit_bias,
            "evidence_digest": receipt.calibration.evidence_digest,
        },
        "signal_weight": receipt.signal_weight,
        "model_asset": _asset_receipt_payload(receipt.model_asset),
        "tokenizer_asset": _asset_receipt_payload(receipt.tokenizer_asset),
        "proposal_count": receipt.proposal_count,
        "output_digest": receipt.output_digest,
        "turn_features_used": receipt.turn_features_used,
        "cue_boundaries_used": receipt.cue_boundaries_used,
    }


def validate_sat_score_receipt_payload(
    payload: Mapping[str, object],
) -> SaTScoreReceipt:
    """Validate and type an untrusted schema-v2 SaT receipt mapping.

    The mapping is the base receipt object emitted by
    :func:`sat_score_receipt_to_dict`; an outer ``receipt_sha256`` field is not
    part of this contract.  Validation is exact and production-bound: unknown
    members, structurally loose nested values, stale asset manifests, and
    provenance/calibration substitutions are rejected.
    """

    record = _require_exact_mapping(
        payload,
        {
            "schema_version",
            "status",
            "reason_code",
            "mode",
            "abstention_visible",
            "offline_enforced",
            "network_access",
            "word_count",
            "input_digest",
            "lexical_input_digest",
            "scorer_provenance",
            "calibration",
            "signal_weight",
            "model_asset",
            "tokenizer_asset",
            "proposal_count",
            "output_digest",
            "turn_features_used",
            "cue_boundaries_used",
        },
        "SaT score receipt",
    )
    schema_version = _require_string(record["schema_version"], "schema_version")
    if schema_version != SAT_RECEIPT_SCHEMA:
        raise ValueError("SaT receipt schema version is invalid")
    try:
        status = SaTScoreStatus(_require_string(record["status"], "status"))
        mode = SaTMode(_require_string(record["mode"], "mode"))
    except ValueError as exc:
        raise ValueError("SaT receipt status or mode is invalid") from exc

    reason_value = record["reason_code"]
    reason_code = None if reason_value is None else _require_string(reason_value, "reason_code")
    calibration = _calibration_from_payload(record["calibration"])
    if calibration != SAT_BOUNDARY_CALIBRATION:
        raise ValueError("SaT receipt calibration does not match the production pin")
    provenance = _provenance_from_payload(record["scorer_provenance"])
    expected_provenance = ScorerProvenance(
        scorer_id="wtpsplit-sat-3l-sm",
        scorer_version=WTPSPLIT_VERSION,
        configuration_digest=_configuration_digest(calibration),
    )
    if provenance != expected_provenance:
        raise ValueError("SaT receipt scorer provenance does not match the production pin")
    model_asset = _asset_receipt_from_payload(
        record["model_asset"],
        label="model_asset",
        identifier=SAT_MODEL_ID,
        revision=SAT_MODEL_REVISION,
        files=SAT_MODEL_FILES,
    )
    tokenizer_asset = _asset_receipt_from_payload(
        record["tokenizer_asset"],
        label="tokenizer_asset",
        identifier=SAT_TOKENIZER_ID,
        revision=SAT_TOKENIZER_REVISION,
        files=SAT_TOKENIZER_FILES,
    )
    return SaTScoreReceipt(
        schema_version=schema_version,
        status=status,
        reason_code=reason_code,
        mode=mode,
        abstention_visible=_require_boolean(record["abstention_visible"], "abstention_visible"),
        offline_enforced=_require_boolean(record["offline_enforced"], "offline_enforced"),
        network_access=_require_boolean(record["network_access"], "network_access"),
        word_count=_require_integer(record["word_count"], "word_count"),
        input_digest=_require_optional_string(record["input_digest"], "input_digest"),
        lexical_input_digest=_require_optional_string(
            record["lexical_input_digest"], "lexical_input_digest"
        ),
        scorer_provenance=provenance,
        calibration=calibration,
        signal_weight=_require_number(record["signal_weight"], "signal_weight"),
        model_asset=model_asset,
        tokenizer_asset=tokenizer_asset,
        proposal_count=_require_integer(record["proposal_count"], "proposal_count"),
        output_digest=_require_optional_string(record["output_digest"], "output_digest"),
        turn_features_used=_require_boolean(record["turn_features_used"], "turn_features_used"),
        cue_boundaries_used=_require_boolean(record["cue_boundaries_used"], "cue_boundaries_used"),
    )


def deserialize_sat_score_receipt(serialized: bytes) -> SaTScoreReceipt:
    """Parse an exact canonical receipt serialization and validate its pins."""

    if not isinstance(serialized, bytes):
        raise ValueError("serialized SaT receipt must be bytes")
    try:
        decoded = serialized.decode("utf-8")
        parsed = json.loads(decoded, object_pairs_hook=_reject_duplicate_json_members)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("serialized SaT receipt is not valid canonical JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("serialized SaT receipt must contain an object")
    receipt = validate_sat_score_receipt_payload(cast(Mapping[str, object], parsed))
    if serialize_sat_score_receipt(receipt) != serialized:
        raise ValueError("serialized SaT receipt is not in canonical form")
    return receipt


def sat_score_receipt_sha256(receipt: SaTScoreReceipt) -> str:
    """Hash the exact canonical SaT receipt serialization."""

    return hashlib.sha256(serialize_sat_score_receipt(receipt)).hexdigest()


def sat_score_input_digests(
    words: Sequence[DecoderWord],
) -> tuple[str, str]:
    """Return the exact context and lexical digests bound by a SaT receipt."""

    bound_words = _validate_words(words)
    if not bound_words:
        raise ValueError("SaT receipt input binding requires at least one word")
    return (
        _input_digest(bound_words, include_context=True),
        _input_digest(bound_words, include_context=False),
    )


def _reject_duplicate_json_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate JSON member: {key}")
        record[key] = value
    return record


def _require_exact_mapping(
    value: object,
    expected_keys: set[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    try:
        record = dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an object") from exc
    if any(not isinstance(key, str) for key in record):
        raise ValueError(f"{label} keys must be strings")
    if set(record) != expected_keys:
        raise ValueError(f"{label} has missing or unknown members")
    return cast(dict[str, object], record)


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"SaT receipt {label} must be a string")
    return value


def _require_optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label)


def _require_boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"SaT receipt {label} must be boolean")
    return value


def _require_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"SaT receipt {label} must be an integer")
    return value


def _require_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"SaT receipt {label} must be numeric")
    return cast(float, value)


def _provenance_from_payload(value: object) -> ScorerProvenance:
    record = _require_exact_mapping(
        value,
        {"scorer_id", "scorer_version", "configuration_digest"},
        "SaT scorer provenance",
    )
    return ScorerProvenance(
        scorer_id=_require_string(record["scorer_id"], "scorer_id"),
        scorer_version=_require_string(record["scorer_version"], "scorer_version"),
        configuration_digest=_require_optional_string(
            record["configuration_digest"], "configuration_digest"
        ),
    )


def _calibration_from_payload(value: object) -> BoundaryCalibration:
    record = _require_exact_mapping(
        value,
        {
            "calibration_id",
            "method",
            "temperature",
            "logit_bias",
            "evidence_digest",
        },
        "SaT calibration",
    )
    method = _require_string(record["method"], "calibration method")
    if method not in {"identity", "logit_affine"}:
        raise ValueError("unsupported SaT calibration method")
    return BoundaryCalibration(
        calibration_id=_require_string(record["calibration_id"], "calibration_id"),
        method=cast(Literal["identity", "logit_affine"], method),
        temperature=_require_number(record["temperature"], "calibration temperature"),
        logit_bias=_require_number(record["logit_bias"], "calibration logit_bias"),
        evidence_digest=_require_string(record["evidence_digest"], "calibration evidence_digest"),
    )


def _asset_receipt_from_payload(
    value: object,
    *,
    label: str,
    identifier: str,
    revision: str,
    files: Mapping[str, ArtifactFile],
) -> VerifiedAssetReceipt | None:
    if value is None:
        return None
    record = _require_exact_mapping(
        value,
        {
            "identifier",
            "revision",
            "manifest_digest",
            "file_count",
            "verified_bytes",
            "verification_passes",
        },
        f"SaT {label}",
    )
    receipt = VerifiedAssetReceipt(
        identifier=_require_string(record["identifier"], f"{label} identifier"),
        revision=_require_string(record["revision"], f"{label} revision"),
        manifest_digest=_require_string(record["manifest_digest"], f"{label} manifest_digest"),
        file_count=_require_integer(record["file_count"], f"{label} file_count"),
        verified_bytes=_require_integer(record["verified_bytes"], f"{label} verified_bytes"),
        verification_passes=_require_integer(
            record["verification_passes"], f"{label} verification_passes"
        ),
    )
    expected = VerifiedAssetReceipt(
        identifier=identifier,
        revision=revision,
        manifest_digest=_asset_manifest_digest(identifier, revision, files),
        file_count=len(files),
        verified_bytes=sum(contract.size for contract in files.values()),
        verification_passes=receipt.verification_passes,
    )
    if receipt != expected:
        raise ValueError(f"SaT receipt {label} does not match the production pin")
    return receipt


def _asset_receipt_payload(
    receipt: VerifiedAssetReceipt | None,
) -> dict[str, object] | None:
    if receipt is None:
        return None
    return {
        "identifier": receipt.identifier,
        "revision": receipt.revision,
        "manifest_digest": receipt.manifest_digest,
        "file_count": receipt.file_count,
        "verified_bytes": receipt.verified_bytes,
        "verification_passes": receipt.verification_passes,
    }


def _provenance_payload(provenance: ScorerProvenance) -> dict[str, str | None]:
    return {
        "scorer_id": provenance.scorer_id,
        "scorer_version": provenance.scorer_version,
        "configuration_digest": provenance.configuration_digest,
    }


def _asset_manifest_digest(
    identifier: str,
    revision: str,
    files: Mapping[str, ArtifactFile],
) -> str:
    return _canonical_digest(
        {
            "identifier": identifier,
            "revision": revision,
            "files": {
                name: {"size": contract.size, "sha256": contract.sha256}
                for name, contract in sorted(files.items())
            },
        }
    )


def _configuration_digest(calibration: BoundaryCalibration) -> str:
    return _canonical_digest(
        {
            "backend": "wtpsplit.SaT.ONNX.CPUExecutionProvider",
            "boundary_mapping": "joined-source-word-final-character-v1",
            "evidence_role_contract": EvidenceRole.BOUNDARY_ONLY.value,
            "calibration": {
                "calibration_id": calibration.calibration_id,
                "method": calibration.method,
                "temperature": calibration.temperature,
                "logit_bias": calibration.logit_bias,
                "evidence_digest": calibration.evidence_digest,
            },
            "model": {
                "manifest_digest": _asset_manifest_digest(
                    SAT_MODEL_ID,
                    SAT_MODEL_REVISION,
                    SAT_MODEL_FILES,
                ),
                "id": SAT_MODEL_ID,
                "revision": SAT_MODEL_REVISION,
            },
            "tokenizer": {
                "manifest_digest": _asset_manifest_digest(
                    SAT_TOKENIZER_ID,
                    SAT_TOKENIZER_REVISION,
                    SAT_TOKENIZER_FILES,
                ),
                "id": SAT_TOKENIZER_ID,
                "revision": SAT_TOKENIZER_REVISION,
            },
            "wtpsplit": {"revision": WTPSPLIT_REVISION, "version": WTPSPLIT_VERSION},
        }
    )


def _canonical_digest(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


SAT_SOURCE_REGISTRATION = ProductionProposalSourceRegistration(
    key=SAT_SOURCE_KEY,
    scorer_id="wtpsplit-sat-3l-sm",
    model_id=SAT_MODEL_ID,
    model_revision=SAT_MODEL_REVISION,
    tokenizer_id=SAT_TOKENIZER_ID,
    tokenizer_revision=SAT_TOKENIZER_REVISION,
    runtime_extra="presentation-sat",
    local_only=True,
    offline_required=True,
    production=True,
    standalone_included=False,
)
PRODUCTION_PROPOSAL_SOURCES: Mapping[str, ProductionProposalSourceRegistration] = MappingProxyType(
    {SAT_SOURCE_REGISTRATION.key: SAT_SOURCE_REGISTRATION}
)


def registered_production_proposal_sources() -> tuple[ProductionProposalSourceRegistration, ...]:
    """Return the immutable production registry in deterministic key order."""

    return tuple(PRODUCTION_PROPOSAL_SOURCES[key] for key in sorted(PRODUCTION_PROPOSAL_SOURCES))


def create_registered_proposal_source(
    key: str,
    model_dir: Path,
    tokenizer_dir: Path,
    *,
    backend_factory: BackendFactory | None = None,
    calibration: BoundaryCalibration = SAT_BOUNDARY_CALIBRATION,
) -> SaTBoundaryScorer:
    """Instantiate one exact registered source without dynamic discovery."""

    if key not in PRODUCTION_PROPOSAL_SOURCES:
        raise KeyError(f"unknown production presentation source: {key}")
    return SaTBoundaryScorer(
        model_dir,
        tokenizer_dir,
        backend_factory=backend_factory,
        calibration=calibration,
    )


def run_registered_sat_source(
    words: Sequence[DecoderWord],
    options: SaTSourceOptions,
    *,
    backend_factory: BackendFactory | None = None,
    calibration: BoundaryCalibration = SAT_BOUNDARY_CALIBRATION,
) -> SaTScoreResult | None:
    """Config-facing registry adapter for off/optional/required execution."""

    if not isinstance(options, SaTSourceOptions):
        raise ValueError("options must be SaTSourceOptions")
    if options.mode is SaTMode.OFF:
        return None
    if options.model_dir is None or options.tokenizer_dir is None:
        scorer = SaTBoundaryScorer(
            Path(".sat-model-not-configured"),
            Path(".sat-tokenizer-not-configured"),
            backend_factory=backend_factory,
            calibration=calibration,
        )
        bound_words: tuple[DecoderWord, ...] = ()
        input_digest: str | None = None
        lexical_input_digest: str | None = None
        try:
            bound_words = _validate_words(words)
            input_digest = _input_digest(bound_words, include_context=True)
            lexical_input_digest = _input_digest(bound_words, include_context=False)
        except SaTScorerError as exc:
            return scorer._failure(
                exc,
                mode=SaTMode.OPTIONAL,
                word_count=0,
                input_digest=None,
                lexical_input_digest=None,
                signal_weight=options.signal_weight,
                model_asset=None,
                tokenizer_asset=None,
            )
        return scorer._failure(
            SaTScorerError("assets_not_configured" if options.offline else "offline_required"),
            mode=SaTMode.OPTIONAL,
            word_count=len(bound_words),
            input_digest=input_digest,
            lexical_input_digest=lexical_input_digest,
            signal_weight=options.signal_weight,
            model_asset=None,
            tokenizer_asset=None,
        )
    scorer = create_registered_proposal_source(
        SAT_SOURCE_KEY,
        options.model_dir,
        options.tokenizer_dir,
        backend_factory=backend_factory,
        calibration=calibration,
    )
    return scorer.score_with_receipt(
        words,
        mode=options.mode,
        signal_weight=options.signal_weight,
        offline=options.offline,
    )
