"""
Cross-source currency-by-geography aggregation, for the global currency
map/globe view. Deliberately combines all five payment-family log sources
(Cardinal, VFlex, Debit Portal, OTP Processor, AFS/Netcetera) into one
count-by-currency, since a currency isn't specific to any one log format --
unlike each family's own compute_*_summary(), which is scoped to a single
source_system.

Reuses normalize_event() (backend/analysis/normalize.py) rather than
re-deriving currency per family, so this always sees the same
already-ISO-4217-resolved value (backend/core/currency.py) every other view
does -- no separate extraction logic to keep in sync.
"""
from collections import Counter
from typing import Any, Dict, List

from backend.analysis.normalize import normalize_events
from backend.core.currency_geo import CURRENCY_GEO


def compute_currency_map_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = normalize_events(events)

    counts: Counter = Counter()
    for event in normalized:
        if event.currency:
            counts[event.currency] += 1

    if not counts:
        return {"status": "no_data", "message": "No transactions with a resolved currency found in the analyzed window."}

    points = []
    unmapped: Counter = Counter()
    for code, count in counts.items():
        geo = CURRENCY_GEO.get(code)
        if geo:
            points.append(
                {
                    "currency": code,
                    "country": geo["country"],
                    "lat": geo["lat"],
                    "lng": geo["lng"],
                    "count": count,
                }
            )
        else:
            unmapped[code] += count

    return {
        "status": "ok",
        "total_transactions": sum(counts.values()),
        "distinct_currencies": len(counts),
        "points": sorted(points, key=lambda p: p["count"], reverse=True),
        "unmapped_currencies": dict(unmapped.most_common()),
    }
