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


def normalize_event(event: Dict[str, Any]) -> Optional[NormalizedEvent]:
    """Returns None for a source_system with no registered family
    normalizer (e.g. a declarative-profile / non-payment-family event) --
    callers should treat None as "not applicable to this layer", not as an
    error. For a registered family, this never raises and never drops the
    event: if the family extractor itself throws on an unexpected/malformed
    `details` shape, a minimal, honestly-degraded NormalizedEvent is
    returned instead (parse_status="failed", evidence_level="minimal"),
    consistent with how every parser in this codebase never silently
    discards a line."""
    source_system = event.get("source_system")
    extractor = _DISPATCH.get(source_system)
    if extractor is None:
        return None
    try:
        return extractor(event)
    except Exception:
        return _degraded_fallback(event, LogFamily(source_system))


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


def normalize_events(events: List[Dict[str, Any]]) -> List[NormalizedEvent]:
    """Normalizes a batch, silently skipping events whose source_system has
    no registered family normalizer (see normalize_event())."""
    normalized: List[NormalizedEvent] = []
    for event in events:
        result = normalize_event(event)
        if result is not None:
            normalized.append(result)
    return normalized
