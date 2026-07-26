from __future__ import annotations

import math
import re
from datetime import datetime, timezone

WORD_RE = re.compile(r"[A-Za-z0-9_./-]+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "in",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate suitable for pre-model budgeting."""
    return max(1, math.ceil(len(text) / 4))


def query_terms(text: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for match in WORD_RE.finditer(text.lower()):
        term = match.group(0).strip("./-_")
        if len(term) < 2 or term in STOP_WORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms[:24]


def fts_query(text: str) -> str:
    terms = query_terms(text)
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def lexical_relevance(query: str, content: str) -> float:
    terms = query_terms(query)
    if not terms:
        return 0.0
    haystack = content.lower()
    matched = sum(1 for term in terms if term in haystack)
    return matched / len(terms)


def recency_score(iso_timestamp: str, half_life_days: float = 90.0) -> float:
    when = datetime.fromisoformat(iso_timestamp)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 86_400)
    return 0.5 ** (age_days / half_life_days)


def context_utility(
    *,
    relevance: float,
    confidence: float,
    importance: float,
    recency: float,
    historical_success: float,
) -> float:
    return (
        0.45 * relevance
        + 0.20 * confidence
        + 0.15 * importance
        + 0.10 * recency
        + 0.10 * historical_success
    )


def token_roi(utility: float, token_cost: int) -> float:
    return utility / max(1, token_cost)
