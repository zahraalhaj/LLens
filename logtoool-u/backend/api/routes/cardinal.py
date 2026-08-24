from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.analysis.cardinal import DEFAULT_SOURCE_SYSTEM, compute_cardinal_summary, extract_merchant_name
from backend.api.date_range import resolve_date_range
from backend.api.deps import get_current_user, get_db
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/cardinal", tags=["cardinal"])


@router.get("/summary")
def summary(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = Query(None, description="ISO 8601 UTC start, e.g. 2026-08-20T00:00:00Z"),
    date_to: Optional[str] = Query(None, description="ISO 8601 UTC end"),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    merchant: Optional[str] = Query(None),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    resolved_from, resolved_to = resolve_date_range(lookback_hours, date_from, date_to)
    events = db.get_events_for_analysis(source_system=source_system, date_from=resolved_from, date_to=resolved_to)
    available_merchants = sorted({m for e in events if (m := extract_merchant_name(e))})
    if merchant:
        events = [e for e in events if extract_merchant_name(e) == merchant]
    result = compute_cardinal_summary(events)
    result["available_merchants"] = available_merchants
    return result
