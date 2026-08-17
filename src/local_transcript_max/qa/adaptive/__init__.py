# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Bounded, deterministic adaptive beam re-decoding for MAX-quality jobs."""

from __future__ import annotations

from .decision import evaluate_span
from .evidence import CandidateEvidence, candidate_evidence
from .lane import AdaptiveRedecodeOutcome, run_adaptive_redecode
from .plan import (
    ADAPTIVE_PLAN_SCHEMA_VERSION,
    AdaptiveRedecodePlan,
    canonical_plan_bytes,
    load_sealed_plan,
    plan_sha256,
    seal_plan,
)
from .policy import ADAPTIVE_POLICY_VERSION
from .stitch import apply_selected_spans

__all__ = [
    "ADAPTIVE_PLAN_SCHEMA_VERSION",
    "ADAPTIVE_POLICY_VERSION",
    "AdaptiveRedecodeOutcome",
    "AdaptiveRedecodePlan",
    "CandidateEvidence",
    "apply_selected_spans",
    "candidate_evidence",
    "canonical_plan_bytes",
    "evaluate_span",
    "load_sealed_plan",
    "plan_sha256",
    "run_adaptive_redecode",
    "seal_plan",
]
