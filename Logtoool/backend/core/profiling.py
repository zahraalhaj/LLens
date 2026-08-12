"""
Log profiling / anomaly flagging.

IMPORTANT: this is deliberately simple z-score-based outlier detection over
real SQL aggregates -- NOT a trained machine learning model. The previous
implementation of this feature (in the retired Express/TypeScript backend)
claimed to be an "IsolationForest (Unsupervised Ensemble)" with fabricated
hyperparameters and validation metrics; none of that was true, it was a
handful of if-statements. This version does honest, modest statistics and
reports itself accurately: "method" in the response is always
"heuristic_zscore", never anything implying a trained model.
"""
import statistics
from typing import Any, Dict, List

from backend.core.store import DatabaseManager

Z_SCORE_THRESHOLD = 2.0  # flag values more than 2 standard deviations above the mean
MIN_SAMPLES_FOR_STATS = 3  # below this, stdev is too noisy to be meaningful


def _flag_outliers(counts: Dict[str, int]) -> List[Dict[str, Any]]:
    values = list(counts.values())
    if len(values) < MIN_SAMPLES_FOR_STATS:
        return []
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return []
    flagged = []
    for key, count in counts.items():
        z = (count - mean) / stdev
        if z >= Z_SCORE_THRESHOLD:
            flagged.append({"name": key, "count": count, "z_score": round(z, 2)})
    return sorted(flagged, key=lambda x: -x["z_score"])


def compute_anomaly_report(db: DatabaseManager) -> Dict[str, Any]:
    severity_counts = db.get_severity_counts()
    component_error_counts = db.get_component_error_counts()
    hourly = db.get_hourly_distribution()

    total_events = sum(severity_counts.values())
    error_like = severity_counts.get("ERROR", 0) + severity_counts.get("CRITICAL", 0)
    error_ratio = (error_like / total_events) if total_events else 0.0

    # Aggregate hourly (which is per-hour-per-level) into per-hour totals.
    hourly_totals: Dict[str, int] = {}
    for row in hourly:
        hourly_totals[row["hour"]] = hourly_totals.get(row["hour"], 0) + row["count"]

    return {
        "method": "heuristic_zscore",
        "description": (
            "Simple statistical outlier detection (z-score over real event counts). "
            "This is not a trained machine learning model."
        ),
        "total_events": total_events,
        "error_ratio": round(error_ratio, 4),
        "flagged_components": _flag_outliers(component_error_counts),
        "flagged_hours": _flag_outliers(hourly_totals),
    }
