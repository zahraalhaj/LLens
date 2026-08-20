from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.analysis.otp_processor import DEFAULT_SOURCE_SYSTEM, compute_otp_summary
from backend.api.date_range import resolve_date_range
from backend.api.deps import get_current_user, get_db
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/otp-processor", tags=["otp-processor"])


@router.get("/summary")
def summary(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = Query(None, description="ISO 8601 UTC start, e.g. 2026-08-20T00:00:00Z"),
    date_to: Optional[str] = Query(None, description="ISO 8601 UTC end"),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    resolved_from, resolved_to = resolve_date_range(lookback_hours, date_from, date_to)
    events = db.get_events_for_analysis(source_system=source_system, date_from=resolved_from, date_to=resolved_to)
    return compute_otp_summary(events)
