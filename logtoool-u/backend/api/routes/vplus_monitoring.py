from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from backend.analysis.vplus_monitoring import (
    DEFAULT_EXPECTED_RESPONSE_MS,
    DEFAULT_EXPECTED_SMS_QUEUE_MS,
    DEFAULT_GAP_THRESHOLD_MINUTES,
    DEFAULT_SOURCE_SYSTEM,
    compute_investigation_summary,
    compute_response_time_stats,
    compute_sms_analysis,
    compute_vplus_availability,
)
from backend.api.deps import get_current_user, get_db
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/vplus", tags=["vplus-monitoring"])


def _lookback_events(db: DatabaseManager, lookback_hours: int, source_system: str):
    date_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.get_events_for_analysis(source_system=source_system, date_from=date_from)


@router.get("/availability")
def availability(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    gap_threshold_minutes: int = Query(DEFAULT_GAP_THRESHOLD_MINUTES, ge=1),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events = _lookback_events(db, lookback_hours, source_system)
    return compute_vplus_availability(events, gap_threshold_minutes=gap_threshold_minutes)


@router.get("/response-times")
def response_times(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    expected_response_ms: int = Query(DEFAULT_EXPECTED_RESPONSE_MS, ge=1),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    include_all_pairs: bool = Query(False),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events = _lookback_events(db, lookback_hours, source_system)
    return compute_response_time_stats(events, expected_response_ms=expected_response_ms, include_all_pairs=include_all_pairs)


@router.get("/sms-analysis")
def sms_analysis(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    expected_queue_ms: int = Query(DEFAULT_EXPECTED_SMS_QUEUE_MS, ge=1),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events = _lookback_events(db, lookback_hours, source_system)
    return compute_sms_analysis(events, expected_queue_ms=expected_queue_ms)


@router.get("/investigation-summary")
def investigation_summary(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    gap_threshold_minutes: int = Query(DEFAULT_GAP_THRESHOLD_MINUTES, ge=1),
    expected_response_ms: int = Query(DEFAULT_EXPECTED_RESPONSE_MS, ge=1),
    expected_queue_ms: int = Query(DEFAULT_EXPECTED_SMS_QUEUE_MS, ge=1),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    """The comprehensive, correlation-focused report -- computes the other
    three reports internally (one shared event fetch, not three separate
    round trips) and cross-references them into a ranked findings list."""
    events = _lookback_events(db, lookback_hours, source_system)
    avail = compute_vplus_availability(events, gap_threshold_minutes=gap_threshold_minutes)
    rt = compute_response_time_stats(events, expected_response_ms=expected_response_ms)
    sms = compute_sms_analysis(events, expected_queue_ms=expected_queue_ms)
    summary = compute_investigation_summary(events, avail, rt, sms)
    return {
        "investigation_summary": summary,
        "availability": avail,
        "response_times": rt,
        "sms_analysis": sms,
    }
