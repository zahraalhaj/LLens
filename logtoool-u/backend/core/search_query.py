"""
Small query-string parser for Explore's search box.

Recognizes `field:value` pairs for the fixed set of equality-filter
columns Explore already supports via dropdowns (level, source, component)
plus "quoted phrases", leaving everything else as bare free-text terms
for full-text search. Deliberately not a grammar/parser-combinator
library -- the supported syntax is small and fixed.

Does NOT support scoping into `attributes.*` (arbitrary per-parser JSON
fields) -- only the fixed level/source/component columns. That's a real,
documented gap, not a silently-ignored one: an unrecognized `field:value`
token (e.g. "merchant:acme") is treated as a plain free-text term instead
of being dropped, so at worst it's searched for literally rather than
applied as a filter.
"""
import re
import shlex
from typing import Dict, List, NamedTuple, Optional

_FIELD_ALIASES = {
    "level": "level",
    "source": "source_system",
    "source_system": "source_system",
    "component": "component",
}

_FIELD_TOKEN_RE = re.compile(r"^([a-zA-Z_]+):(.+)$")


class ParsedSearch(NamedTuple):
    field_filters: Dict[str, str]  # e.g. {"level": "ERROR", "source_system": "cardinal"}
    free_text_terms: List[str]  # bare words and quoted phrases, already unquoted, for FTS matching


def parse_search_query(raw: Optional[str]) -> ParsedSearch:
    """Splits `raw` into recognized field:value filters and a list of
    free-text terms (bare words or "quoted phrases", each already
    stripped of its quotes by shlex)."""
    if not raw or not raw.strip():
        return ParsedSearch(field_filters={}, free_text_terms=[])

    try:
        tokens = shlex.split(raw)
    except ValueError:
        # Unbalanced quotes -- fall back to the raw string as one free-text term
        # rather than raising on what's still a reasonable, if malformed, query.
        return ParsedSearch(field_filters={}, free_text_terms=[raw.strip()])

    field_filters: Dict[str, str] = {}
    free_text_terms: List[str] = []

    for token in tokens:
        match = _FIELD_TOKEN_RE.match(token)
        if match:
            field_name, value = match.group(1).lower(), match.group(2)
            mapped = _FIELD_ALIASES.get(field_name)
            if mapped and value:
                # level is an enum-like column (DEBUG/INFO/WARN/ERROR/CRITICAL)
                # stored uppercase -- normalize so "level:error" matches the
                # same way the ERROR dropdown button already does.
                field_filters[mapped] = value.upper() if mapped == "level" else value
                continue
        if token:
            free_text_terms.append(token)

    return ParsedSearch(field_filters=field_filters, free_text_terms=free_text_terms)


def build_fts_match_query(free_text_terms: List[str]) -> Optional[str]:
    """Builds an FTS5 MATCH query string ANDing every free-text term.
    Every term is quoted as an FTS5 phrase -- not just multi-word ones --
    so a term containing FTS5 query-syntax characters (log content
    routinely has "-", "*", parentheses, etc.) is matched literally
    instead of raising an FTS5 syntax error or being silently
    misinterpreted as a query operator."""
    if not free_text_terms:
        return None
    parts = []
    for term in free_text_terms:
        escaped = term.replace('"', '""')  # FTS5 phrase syntax: double an embedded quote to escape it
        parts.append(f'"{escaped}"')
    return " AND ".join(parts)
