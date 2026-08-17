# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Versioned, deterministic thresholds and protected-material detection.

Everything in this module is part of the promotion contract. Changing a value
changes the decision a replay reproduces, so the version identifier must move
with it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal

ADAPTIVE_POLICY_VERSION: Final[Literal["adaptive_redecode_policy.v1"]] = (
    "adaptive_redecode_policy.v1"
)

# Hard invariants. A candidate violating any of these is rejected outright.
MAXIMUM_NO_SPEECH_PROBABILITY: Final = 0.6
MAXIMUM_COMPRESSION_RATIO: Final = 2.4
MAXIMUM_REPETITION_RATIO: Final = 0.5
MINIMUM_WORDS_PER_SECOND: Final = 0.2
MAXIMUM_WORDS_PER_SECOND: Final = 8.0
WINDOW_BOUNDS_TOLERANCE_SECONDS: Final = 0.05

# Regression tolerances. A wide-beam candidate may not lose ground on an
# independent dimension by more than these amounts.
ALIGNMENT_COVERAGE_TOLERANCE: Final = 0.005
COMPRESSION_RATIO_TOLERANCE: Final = 0.05
REPETITION_RATIO_TOLERANCE: Final = 0.02
NO_SPEECH_TOLERANCE: Final = 0.05

# Improvement margins for independent (non-model-score) evidence.
ALIGNMENT_COVERAGE_IMPROVEMENT: Final = 0.02
COMPRESSION_RATIO_IMPROVEMENT: Final = 0.05
REPETITION_RATIO_IMPROVEMENT: Final = 0.02
NO_SPEECH_IMPROVEMENT: Final = 0.05

# Independent supporting dimensions required before promotion.
REQUIRED_INDEPENDENT_SUPPORT: Final = 1
REQUIRED_PROTECTED_INDEPENDENT_SUPPORT: Final = 2

# Resource ceilings that hold regardless of configuration.
ABSOLUTE_MAXIMUM_SPANS: Final = 100
ABSOLUTE_MAXIMUM_CANDIDATES_PER_SPAN: Final = 2

_NEGATION_TOKENS: Final = frozenset(
    {
        "no",
        "not",
        "none",
        "nobody",
        "nor",
        "nothing",
        "never",
        "neither",
        "without",
        "cannot",
        "cant",
        "dont",
        "didnt",
        "doesnt",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "wont",
        "wouldnt",
        "shouldnt",
        "couldnt",
        "havent",
        "hasnt",
        "hadnt",
        "aint",
    }
)

_MODALITY_TOKENS: Final = frozenset(
    {
        "may",
        "might",
        "must",
        "should",
        "could",
        "would",
        "can",
        "shall",
        "will",
        "maybe",
        "perhaps",
        "possibly",
        "probably",
        "allegedly",
        "apparently",
        "supposedly",
        "reportedly",
        "likely",
        "unlikely",
    }
)

_ATTRIBUTION_TOKENS: Final = frozenset(
    {
        "said",
        "says",
        "say",
        "told",
        "tells",
        "asked",
        "claimed",
        "claims",
        "stated",
        "states",
        "according",
        "quoted",
        "wrote",
        "writes",
        "argued",
        "denied",
        "confirmed",
    }
)

_TEMPORAL_TOKENS: Final = frozenset(
    {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "today",
        "tomorrow",
        "yesterday",
        "am",
        "pm",
        "noon",
        "midnight",
        "oclock",
    }
)

_QUANTITY_TOKENS: Final = frozenset(
    {
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "percent",
        "half",
        "double",
        "triple",
        "dozen",
    }
)

ProtectedClass = Literal[
    "attribution",
    "contraction",
    "date_or_time",
    "false_start",
    "modality",
    "negation",
    "proper_noun",
    "quantity",
    "repetition_emphasis",
]

_WORD_RE: Final = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_TRAILING_DASH_RE: Final = re.compile(r"[-–—]$")


def tokenize(text: str) -> list[str]:
    """Split text into deterministic comparison tokens."""

    return _WORD_RE.findall(unicodedata.normalize("NFC", text))


def normalized_tokens(text: str) -> list[str]:
    """Return case-folded, punctuation-free tokens for surface comparison."""

    return [_fold(token) for token in tokenize(text)]


def _fold(token: str) -> str:
    folded = unicodedata.normalize("NFKD", token.casefold())
    return "".join(character for character in folded if character.isalnum())


def normalized_text(text: str) -> str:
    """Return the canonical surface used to decide whether two texts agree."""

    return " ".join(token for token in normalized_tokens(text) if token)


def repetition_ratio(text: str) -> float:
    """Return the share of tokens that merely repeat their predecessor.

    A degenerate Whisper loop drives this toward 1.0 while ordinary emphatic
    repetition stays low, so it separates loops from legitimate speech without
    needing a language model.
    """

    tokens = normalized_tokens(text)
    if len(tokens) < 2:
        return 0.0
    repeats = sum(1 for index in range(1, len(tokens)) if tokens[index] == tokens[index - 1])
    return repeats / (len(tokens) - 1)


def protected_classes(before: str, after: str) -> tuple[ProtectedClass, ...]:
    """Classify the material a candidate would change.

    A difference here is not automatically wrong — the first pass may be the
    mistaken side — but it demands stronger independent support before any
    promotion.
    """

    found: set[ProtectedClass] = set()
    before_tokens = tokenize(before)
    after_tokens = tokenize(after)
    before_folded = [_fold(token) for token in before_tokens]
    after_folded = [_fold(token) for token in after_tokens]
    if before_folded == after_folded:
        return ()
    changed_before = _multiset_difference(before_folded, after_folded)
    changed_after = _multiset_difference(after_folded, before_folded)
    changed = changed_before | changed_after
    for token in changed:
        if token in _NEGATION_TOKENS:
            found.add("negation")
        if token in _MODALITY_TOKENS:
            found.add("modality")
        if token in _ATTRIBUTION_TOKENS:
            found.add("attribution")
        if token in _TEMPORAL_TOKENS:
            found.add("date_or_time")
        if token in _QUANTITY_TOKENS or any(character.isdigit() for character in token):
            found.add("quantity")
    if any(character.isdigit() for character in before + after) and changed:
        found.add("quantity")
    if _contraction_changed(before_tokens, after_tokens):
        found.add("contraction")
    if _proper_noun_changed(before_tokens, after_tokens):
        found.add("proper_noun")
    if _false_start_changed(before, after):
        found.add("false_start")
    if _emphasis_repetition_changed(before_folded, after_folded):
        found.add("repetition_emphasis")
    return tuple(sorted(found))


def _multiset_difference(left: list[str], right: list[str]) -> set[str]:
    remaining = list(right)
    difference: set[str] = set()
    for token in left:
        if token in remaining:
            remaining.remove(token)
        else:
            difference.add(token)
    return difference


def _contraction_changed(before: list[str], after: list[str]) -> bool:
    return _contractions(before) != _contractions(after)


def _contractions(tokens: list[str]) -> set[str]:
    return {token.casefold() for token in tokens if "'" in token or "’" in token}


def _proper_noun_changed(before: list[str], after: list[str]) -> bool:
    return _proper_nouns(before) != _proper_nouns(after)


def _proper_nouns(tokens: list[str]) -> set[str]:
    # Skip the first token: a sentence-initial capital carries no name evidence.
    return {
        token.casefold()
        for index, token in enumerate(tokens)
        if index > 0 and token[:1].isupper() and not token.isupper()
    }


def _false_start_changed(before: str, after: str) -> bool:
    return _false_starts(before) != _false_starts(after)


def _false_starts(text: str) -> int:
    return sum(1 for chunk in text.split() if _TRAILING_DASH_RE.search(chunk))


def _emphasis_repetition_changed(before: list[str], after: list[str]) -> bool:
    return _adjacent_repeats(before) != _adjacent_repeats(after)


def _adjacent_repeats(tokens: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            tokens[index]
            for index in range(1, len(tokens))
            if tokens[index] and tokens[index] == tokens[index - 1]
        )
    )
