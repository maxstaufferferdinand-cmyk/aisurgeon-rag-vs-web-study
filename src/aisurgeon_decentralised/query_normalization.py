"""Deterministic query normalisation without replacing the original question.

The normalised representation is used only for retrieval.  The caller keeps
the original text separately and operational telemetry stores its SHA-256 by
default rather than the text itself.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

_ABBREVIATION_EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bVTE\b", re.IGNORECASE), "venöse Thromboembolie"),
    (re.compile(r"\bNMH\b", re.IGNORECASE), "niedermolekulares Heparin"),
    (re.compile(r"\bUFH\b", re.IGNORECASE), "unfraktioniertes Heparin"),
    (
        re.compile(r"\bDOAKs?\b", re.IGNORECASE),
        "direkte orale Antikoagulanzien",
    ),
    (re.compile(r"\bHIT\b", re.IGNORECASE), "Heparin-induzierte Thrombozytopenie"),
    (re.compile(r"\bIPK\b", re.IGNORECASE), "intermittierende pneumatische Kompression"),
)

_QUESTION_STOPWORDS = frozenset(
    {
        "anhand",
        "antwort",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "eines",
        "für",
        "gilt",
        "gelten",
        "ist",
        "laut",
        "leitlinie",
        "nennt",
        "sagt",
        "sind",
        "soll",
        "sollen",
        "sollte",
        "sollten",
        "und",
        "von",
        "vor",
        "was",
        "wann",
        "welche",
        "welcher",
        "welches",
        "werden",
        "wird",
        "wie",
        "wo",
        "zu",
        "zur",
        "zum",
    }
)


@dataclass(frozen=True)
class NormalizedQuery:
    """Original-preserving query representation shared by CLI and future API."""

    original_text: str
    cleaned_text: str
    lexical_text: str
    normalized_text: str
    query_sha256: str
    applied_expansions: tuple[str, ...]


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("‐", "-").replace("‑", "-").replace("–", "-")
    return " ".join(value.split())


def normalize_query(value: str) -> NormalizedQuery:
    """Create deterministic lexical variants while preserving ``value`` exactly."""

    if not isinstance(value, str):
        raise TypeError("query must be a string")
    cleaned = _plain(value)
    if not cleaned:
        raise ValueError("query must not be empty")

    expansions: list[str] = []
    for pattern, expansion in _ABBREVIATION_EXPANSIONS:
        if pattern.search(cleaned):
            expansions.append(expansion)
    # PostgreSQL's websearch_to_tsquery combines ordinary query words with
    # AND.  Removing only the deterministic question boilerplate avoids a
    # single conversational verb suppressing otherwise valid FTS hits.
    tokens = re.findall(r"[\wÄÖÜäöüß.-]+", cleaned, flags=re.UNICODE)
    retained = [token for token in tokens if token.casefold() not in _QUESTION_STOPWORDS]
    # OR gives the lexical channel useful recall for paraphrases; ranking still
    # rewards records matching multiple retained concepts.
    lexical = " OR ".join(retained) or cleaned
    normalized = _plain(cleaned).casefold()
    return NormalizedQuery(
        original_text=value,
        cleaned_text=cleaned,
        lexical_text=lexical,
        normalized_text=normalized,
        query_sha256=hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
        applied_expansions=tuple(dict.fromkeys(expansions)),
    )


__all__ = ["NormalizedQuery", "normalize_query"]
