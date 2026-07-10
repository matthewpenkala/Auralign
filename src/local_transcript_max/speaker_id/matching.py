# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Speaker embedding matching."""

from __future__ import annotations

import math
from collections.abc import Sequence
from numbers import Real

MAX_EMBEDDING_DIMENSIONS = 4096


def validate_embedding(
    vector: Sequence[object],
    *,
    label: str = "embedding",
) -> list[float]:
    """Return a finite, resource-bounded embedding or reject it.

    Embeddings are persisted data and therefore untrusted input.  In
    particular, accepting NaN values makes comparisons and threshold checks
    non-deterministic, while accepting an unbounded vector makes a tiny JSON
    artifact an avoidable CPU/memory denial-of-service primitive.
    """

    if not vector:
        raise ValueError(f"{label} must not be empty.")
    if len(vector) > MAX_EMBEDDING_DIMENSIONS:
        raise ValueError(f"{label} exceeds the {MAX_EMBEDDING_DIMENSIONS}-dimension limit.")
    validated: list[float] = []
    for index, raw_value in enumerate(vector):
        if isinstance(raw_value, bool) or not isinstance(raw_value, Real):
            raise ValueError(f"{label}[{index}] must be a real number.")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"{label}[{index}] must be finite.")
        validated.append(value)
    return validated


def cosine_similarity(left: list[float], right: list[float]) -> float:
    validated_left = validate_embedding(left, label="left embedding")
    validated_right = validate_embedding(right, label="right embedding")
    if len(validated_left) != len(validated_right):
        raise ValueError("Embedding dimensions must match.")

    # math.hypot avoids overflow for large-but-finite components.  Computing
    # the dot product after normalization then keeps every product bounded.
    norm_left = math.hypot(*validated_left)
    norm_right = math.hypot(*validated_right)
    if norm_left == 0 or norm_right == 0:
        raise ValueError("Cosine similarity is undefined for a zero vector.")
    score = math.fsum(
        (a / norm_left) * (b / norm_right)
        for a, b in zip(validated_left, validated_right, strict=True)
    )
    if not math.isfinite(score):  # pragma: no cover - guarded above, defense in depth
        raise ValueError("Cosine similarity must be finite.")
    # Floating-point accumulation can exceed the mathematical range by a few
    # ulps.  Clamp only that representational drift, not invalid input.
    return max(-1.0, min(1.0, score))
