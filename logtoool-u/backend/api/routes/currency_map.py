from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.analysis.cardinal import DEFAULT_SOURCE_SYSTEM as CARDINAL_SOURCE
from backend.analysis.currency_map import compute_currency_map_summary
from backend.analysis.debit_portal import DEFAULT_SOURCE_SYSTEM as DEBIT_PORTAL_SOURCE
from backend.analysis.otp_processor import DEFAULT_SOURCE_SYSTEM as OTP_PROCESSOR_SOURCE
from backend.analysis.vflex import DEFAULT_SOURCE_SYSTEM as VFLEX_SOURCE
from backend.analysis.vplus_monitoring import DEFAULT_SOURCE_SYSTEM as VPLUS_SOURCE
from backend.api.date_range import resolve_date_range
from backend.api.deps import get_current_user, get_db
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/currency-map", tags=["currency-map"])

# Every payment-family log source with a resolvable transaction currency --
# unlike each family's own /summary route (scoped to one source_system),
# this view is deliberately cross-source.
_ALL_PAYMENT_SOURCES = [CARDINAL_SOURCE, DEBIT_PORTAL_SOURCE, OTP_PROCESSOR_SOURCE, VFLEX_SOURCE, VPLUS_SOURCE]


@router.get("/summary")
def summary(
    lookback_hours: int = Query(24, ge=1, le=24 * 30),
    date_from: Optional[str] = Query(None, description="ISO 8601 UTC start, e.g. 2026-08-20T00:00:00Z"),
    date_to: Optional[str] = Query(None, description="ISO 8601 UTC end"),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    resolved_from, resolved_to = resolve_date_range(lookback_hours, date_from, date_to)
    events = []
    for source_system in _ALL_PAYMENT_SOURCES:
        events.extend(db.get_events_for_analysis(source_system=source_system, date_from=resolved_from, date_to=resolved_to))
    return compute_currency_map_summary(events)
