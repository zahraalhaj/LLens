import re
from typing import List, Tuple


def score(entries: List[dict], profile: dict) -> float:
    """Fraction of entries whose first line matches the profile's line_regex."""
    rx = re.compile(profile["line_regex"])
    total = len(entries) or 1
    matched = sum(1 for e in entries if rx.match(e["raw"].strip()))
    return matched / total


def rank(entries: List[dict], profiles: List[dict]) -> List[Tuple[dict, float]]:
    scored = [(p, score(entries, p)) for p in profiles]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def select(entries: List[dict], profiles: List[dict]) -> Tuple[dict, float]:
    """Return the best matching profile and its ratio.

    A profile is a confident match when its ratio clears its min_confidence
    threshold. Otherwise the best effort is still returned so the caller can
    flag it for AI-assisted onboarding.
    """
    best, ratio = rank(entries, profiles)[0]
    return best, ratio
