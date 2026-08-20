from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from backend.analysis.cardinal import DEFAULT_SOURCE_SYSTEM, compute_cardinal_summary
from backend.api.deps import get_current_user, get_db
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/cardinal", tags=["cardinal"])


@router.get("/summary")
def summary(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    source_system: str = Query(DEFAULT_SOURCE_SYSTEM),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    date_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = db.get_events_for_analysis(source_system=source_system, date_from=date_from)
    return compute_cardinal_summary(events)
