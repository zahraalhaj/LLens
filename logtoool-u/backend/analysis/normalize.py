"""
Normalization dispatcher -- Phase 2 of the LLens Multi-Log Analysis
Strategy.

Routes a stored CanonicalLogEvent dict (as returned by
DatabaseManager.get_events_for_analysis()/query_events()) to the right
family-specific normalize_<family>_event() function, based on its
source_system, and returns one common NormalizedEvent
(backend/analysis/normalized_schema.py).

This is the only module that imports both normalized_schema.py (the
schema) AND every family's backend/analysis/<family>.py module (each of
which imports helpers FROM normalized_schema.py to build its own
normalize_<family>_event()) -- keeping the dispatch wiring here, instead
of in normalized_schema.py itself, is what avoids a circular import
between the schema and the five family modules.

Deterministic only -- no LLM involved anywhere in this module or in any
normalize_<family>_event() it calls.
"""
from typing import Any, Callable, Dict, List, Optional

from backend.analysis.cardinal import normalize_cardinal_event
from backend.analysis.debit_portal import normalize_debit_portal_event
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent, classify_normalized_stage
from backend.analysis.otp_processor import normalize_otp_event
from backend.analysis.vflex import normalize_vflex_event
from backend.analysis.vplus_monitoring import normalize_netcetera_event

_DISPATCH: Dict[str, Callable[[Dict[str, Any]], NormalizedEvent]] = {
    LogFamily.CARDINAL.value: normalize_cardinal_event,
    LogFamily.NETCETERA_VPLUS.value: normalize_netcetera_event,
    LogFamily.DEBIT_PORTAL.value: normalize_debit_portal_event,
    LogFamily.VFLEX.value: normalize_vflex_event,
    LogFamily.OTP_PROCESSOR.value: normalize_otp_event,
}


def normalize_event(
    event: Dict[str, Any],
    correlation_keys_by_source: Optional[Dict[str, List[str]]] = None,
) -> NormalizedEvent:
    """For one of the 5 registered payment families, this never raises and
    never drops the event: if the family extractor itself throws on an
    unexpected/malformed `details` shape, a minimal, honestly-degraded
    NormalizedEvent is returned instead (parse_status="failed",
    evidence_level="minimal"), consistent with how every parser in this
    codebase never silently discards a line.

    For any OTHER source_system (declarative profiles, or the 4 custom
    parsers that aren't one of the 5 families), returns a minimal
    LogFamily.GENERIC NormalizedEvent via _normalize_generic_event()
    instead of None -- these events get correlation (Phase 3) but not the
    family-specific dependency-health/lifecycle modeling (Phases 4-5),
    which is inherently family-specific business-flow knowledge, not
    something config alone can supply. `correlation_keys_by_source` (built
    once by the caller from ProfileManager.list_profiles(), see
    pipeline.py) maps a declarative profile's default_source_system to its
    declared ParserProfile.correlation_keys; omit/empty dict for callers
    that don't need this (e.g. existing tests), which only means generic
    events get no correlation identifiers beyond attributes.correlation_id
    when present -- never an error."""
    source_system = event.get("source_system")
    extractor = _DISPATCH.get(source_system)
    if extractor is None:
        return _normalize_generic_event(event, (correlation_keys_by_source or {}).get(source_system, []))
    try:
        return extractor(event)
    except Exception:
        return _degraded_fallback(event, LogFamily(source_system))


def _normalize_generic_event(event: Dict[str, Any], correlation_keys: List[str]) -> NormalizedEvent:
    """Minimal, family-agnostic normalization for any source_system with
    no registered family extractor. Only the fields every CanonicalLogEvent
    always carries are mapped -- no business-context/dependency/lifecycle
    fields, since those require family-specific knowledge of what a
    payload's fields mean that a generic mapping can't supply.

    extra_identifiers always includes attributes.correlation_id when
    present (every custom parser, including the 4 outside the 5 named
    families, already writes this by convention -- see
    custom_parser_registry.py), plus whichever of the profile's declared
    correlation_keys have a non-empty value in attributes."""
    attrs = event.get("attributes") or {}
    extra_identifiers: Dict[str, str] = {}
    if attrs.get("correlation_id"):
        extra_identifiers["correlation_id"] = str(attrs["correlation_id"])
    for key in correlation_keys:
        value = attrs.get(key)
        if value:
            extra_identifiers[key] = str(value)

    return NormalizedEvent(
        source_file=event.get("file_name") or "unknown",
        log_family=LogFamily.GENERIC,
        event_no=event.get("line_no") or 0,
        physical_line_start=None,
        raw_reference=event.get("raw") or "",
        source_event_id=event.get("event_id"),
        batch_id=event.get("batch_id"),
        event_timestamp=event.get("ts_utc"),
        level=event.get("level"),
        event_type=event.get("component"),
        correlation_id=attrs.get("correlation_id"),
        extra_identifiers=extra_identifiers,
        normalized_stage=classify_normalized_stage(event.get("component"), event.get("level")),
        parse_status="parsed",
        correlation_confidence=1.0 if extra_identifiers else 0.0,
        evidence_level="partial",
    )


def _degraded_fallback(event: Dict[str, Any], family: LogFamily) -> NormalizedEvent:
    attrs = event.get("attributes") or {}
    return NormalizedEvent(
        source_file=event.get("file_name") or "unknown",
        log_family=family,
        event_no=event.get("line_no") or 0,
        physical_line_start=None,
        raw_reference=event.get("raw") or "",
        source_event_id=event.get("event_id"),
        batch_id=event.get("batch_id"),
        event_timestamp=event.get("ts_utc"),
        level=event.get("level"),
        event_type=event.get("component"),
        correlation_id=attrs.get("correlation_id"),
        normalized_stage=classify_normalized_stage(event.get("component"), event.get("level")),
        parse_status="failed",
        correlation_confidence=0.0,
        evidence_level="minimal",
    )


def normalize_events(
    events: List[Dict[str, Any]],
    correlation_keys_by_source: Optional[Dict[str, List[str]]] = None,
) -> List[NormalizedEvent]:
    """Normalizes a batch. Every event now gets a NormalizedEvent back
    (see normalize_event()) -- there's no longer a "no registered family"
    drop case, so this never filters anything out."""
    return [normalize_event(event, correlation_keys_by_source) for event in events]
