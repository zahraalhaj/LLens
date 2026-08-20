"""Shared date-range resolution for the analysis routes (vplus, otp-processor,
debit-portal, cardinal, vflex). The frontend's date pickers send an explicit
(date_from, date_to) window; older/simpler callers can still pass
lookback_hours for a rolling window. Centralized so all five routers resolve
"what window am I querying" the same way.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def resolve_date_range(
    lookback_hours: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """An explicit date_from takes precedence over lookback_hours. When
    date_from is given without date_to, the window is open-ended at "now"."""
    if date_from:
        return date_from, date_to or datetime.now(timezone.utc).strftime(ISO_FMT)
    resolved_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime(ISO_FMT)
    return resolved_from, date_to
