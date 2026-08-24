"""
Phase 10 analytics/dashboard API routes -- Service Overview, Dependency
Health, Queue & Messaging, Investigation, and Security/Data Quality.

Every route builds its response by running backend/analysis/pipeline.py's
run_analysis_pipeline() and reshaping the result via
backend/analysis/dashboards.py -- none of them touch raw log rows or
compute a metric directly. This is the same date-range contract every
other analysis router (vplus, otp-processor, debit-portal, cardinal,
vflex) already uses.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.analysis.dashboards import (
    SEARCHABLE_FIELDS,
    build_correlation_explorer,
    build_dependency_health,
    build_dependency_p95_trend,
    build_investigation_result,
    build_oob_duration_histogram,
    build_queue_messaging,
    build_security_quality,
    build_service_overview,
    list_available_merchants,
    search_flows,
    search_flows_by_raw_text,
)
from backend.analysis.pipeline import run_analysis_pipeline
from backend.api.date_range import resolve_date_range
from backend.api.deps import get_current_user, get_db
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_DATE_FROM_Q = Query(None, description="ISO 8601 UTC start, e.g. 2026-08-20T00:00:00Z")
_DATE_TO_Q = Query(None, description="ISO 8601 UTC end")


def _pipeline(
    db: DatabaseManager,
    lookback_hours: int,
    date_from: Optional[str],
    date_to: Optional[str],
):
    resolved_from, resolved_to = resolve_date_range(lookback_hours, date_from, date_to)
    return run_analysis_pipeline(db, date_from=resolved_from, date_to=resolved_to)


@router.get("/service-overview")
def service_overview(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    trend_bucket_minutes: int = Query(1440, ge=5),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return build_service_overview(bundle, time_bucket_minutes=trend_bucket_minutes)


@router.get("/dependency-health")
def dependency_health(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return build_dependency_health(bundle)


@router.get("/dependency-health/{dependency}/trend")
def dependency_p95_trend(
    dependency: str,
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    bucket_minutes: int = Query(60, ge=5),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return build_dependency_p95_trend(bundle, dependency, bucket_minutes=bucket_minutes)


@router.get("/dependency-health/oob-api/duration-histogram")
def oob_duration_histogram(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return build_oob_duration_histogram(bundle)


@router.get("/queue-messaging")
def queue_messaging(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return build_queue_messaging(bundle)


@router.get("/investigation/merchants")
def investigation_merchants(
    lookback_hours: int = Query(24 * 30, ge=1, le=24 * 365),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return {"merchants": list_available_merchants(bundle)}


@router.get("/investigation/search")
def investigation_search(
    field: str = Query(..., description=f"One of: {', '.join(SEARCHABLE_FIELDS)}"),
    query: str = Query(..., min_length=1),
    lookback_hours: int = Query(24 * 30, ge=1, le=24 * 365),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    if field not in SEARCHABLE_FIELDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"field must be one of: {SEARCHABLE_FIELDS}")
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    flow_ids = search_flows(bundle, field, query)
    results = [build_investigation_result(bundle, flow_id) for flow_id in flow_ids]
    return {"query": query, "field": field, "match_count": len(flow_ids), "results": [r for r in results if r]}


@router.get("/investigation/search-raw")
def investigation_search_raw(
    query: str = Query(..., min_length=1),
    lookback_hours: int = Query(24 * 30, ge=1, le=24 * 365),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    flow_ids = search_flows_by_raw_text(bundle, query)
    results = [build_investigation_result(bundle, flow_id) for flow_id in flow_ids]
    return {"query": query, "match_count": len(flow_ids), "results": [r for r in results if r]}


@router.get("/investigation/{flow_id}")
def investigation_detail(
    flow_id: str,
    lookback_hours: int = Query(24 * 30, ge=1, le=24 * 365),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    result = build_investigation_result(bundle, flow_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flow not found in the analyzed window")
    return result


@router.get("/correlation-explorer")
def correlation_explorer(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return build_correlation_explorer(bundle)


@router.get("/security-quality")
def security_quality(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = _DATE_FROM_Q,
    date_to: Optional[str] = _DATE_TO_Q,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    bundle = _pipeline(db, lookback_hours, date_from, date_to)
    return build_security_quality(bundle)
