from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.analysis.vplus_monitoring import (
    DEFAULT_EXPECTED_RESPONSE_MS,
    DEFAULT_EXPECTED_SMS_QUEUE_MS,
    DEFAULT_GAP_THRESHOLD_MINUTES,
    DEFAULT_SOURCE_SYSTEM,
    DEFAULT_UNRESPONDED_GRACE_MS,
    compute_investigation_summary,
    compute_response_time_stats,
    compute_sms_analysis,
    compute_transaction_breakdown,
    compute_vplus_availability,
)
from backend.api.date_range import resolve_date_range
from backend.api.deps import get_current_user, get_db
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/vplus", tags=["vplus-monitoring"])


def _lookback_events(
    db: DatabaseManager,
    lookback_hours: int,
    source_system: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    resolved_from, resolved_to = resolve_date_range(lookback_hours, date_from, date_to)
    return db.get_events_for_analysis(source_system=source_system, date_from=resolved_from, date_to=resolved_to)


_DATE_FROM_Q = Query(None, description="ISO 8601 UTC start, e.g. 2026-08-20T00:00:00Z")
_DATE_TO_Q = Query(None, description="ISO 8601 UTC end")


@router.get("/availability")
def availability(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    gap_threshold_minutes: int = Query(DEFAULT_GAP_THRESHOLD_MINUTES, ge=1),
    expected_response_ms: int = Query(DEFAULT_EXPECTED_RESPONSE_MS, ge=1),
    unresponded_grace_ms: int = Query(DEFAULT_UNRESPONDED_GRACE_MS, ge=0),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events = _lookback_events(db, lookback_hours, source_system, date_from, date_to)
    return compute_vplus_availability(
        events,
        gap_threshold_minutes=gap_threshold_minutes,
        expected_response_ms=expected_response_ms,
        unresponded_grace_ms=unresponded_grace_ms,
    )


@router.get("/response-times")
def response_times(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    expected_response_ms: int = Query(DEFAULT_EXPECTED_RESPONSE_MS, ge=1),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    include_all_pairs: bool = Query(False),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events = _lookback_events(db, lookback_hours, source_system, date_from, date_to)
    return compute_response_time_stats(events, expected_response_ms=expected_response_ms, include_all_pairs=include_all_pairs)


@router.get("/sms-analysis")
def sms_analysis(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    expected_queue_ms: int = Query(DEFAULT_EXPECTED_SMS_QUEUE_MS, ge=1),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events = _lookback_events(db, lookback_hours, source_system, date_from, date_to)
    return compute_sms_analysis(events, expected_queue_ms=expected_queue_ms)


@router.get("/transactions")
def transactions(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events = _lookback_events(db, lookback_hours, source_system, date_from, date_to)
    return compute_transaction_breakdown(events)


@router.get("/investigation-summary")
def investigation_summary(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    gap_threshold_minutes: int = Query(DEFAULT_GAP_THRESHOLD_MINUTES, ge=1),
    expected_response_ms: int = Query(DEFAULT_EXPECTED_RESPONSE_MS, ge=1),
    unresponded_grace_ms: int = Query(DEFAULT_UNRESPONDED_GRACE_MS, ge=0),
    expected_queue_ms: int = Query(DEFAULT_EXPECTED_SMS_QUEUE_MS, ge=1),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    """The comprehensive, correlation-focused report -- computes the other
    three reports internally (one shared event fetch, not three separate
    round trips) and cross-references them into a ranked findings list."""
    events = _lookback_events(db, lookback_hours, source_system, date_from, date_to)
    avail = compute_vplus_availability(
        events,
        gap_threshold_minutes=gap_threshold_minutes,
        expected_response_ms=expected_response_ms,
        unresponded_grace_ms=unresponded_grace_ms,
    )
    rt = compute_response_time_stats(events, expected_response_ms=expected_response_ms)
    sms = compute_sms_analysis(events, expected_queue_ms=expected_queue_ms)
    tx_breakdown = compute_transaction_breakdown(events)
    summary = compute_investigation_summary(events, avail, rt, sms)
    return {
        "investigation_summary": summary,
        "availability": avail,
        "response_times": rt,
        "sms_analysis": sms,
        "transaction_breakdown": tx_breakdown,
    }
