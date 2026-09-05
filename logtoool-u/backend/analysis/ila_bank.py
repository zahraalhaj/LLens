"""
ILA Bank application-log analytics.

Reads the stored canonical events produced by
backend/custom_parsers/parser_ILA_Bank.py and derives the summary the
ILA dashboard renders. Same shape and conventions as the other family
analytics modules (see backend/analysis/vflex.py): a single
compute_*_summary(events) -> dict, a "no_data" status when the window is
empty, and no LLM anywhere in the path.

WHAT THIS MEASURES, AND WHY THOSE THINGS
----------------------------------------
The ILA parser is deliberately format-driven rather than business-flow
driven -- unlike Cardinal or VFlex it has no notion of a 3DS step-up or a
bank API call, so there is no issuer/status/channel breakdown to compute.
What it does extract is what an application log actually carries:
severity, tracker correlation, exceptions with stack frames, explicit
durations, HTTP status codes, and its own parse fidelity. Those are the
axes an analyst works along when a payment posting failed and they need
to know what broke, when, in which transaction, and how slow it was.

Parse fidelity is reported as a first-class metric rather than an
internal detail: this parser is byte-exact by design, so "3 entries in
this window were not recognised" is a statement about the log, not about
a bug, and an analyst needs to see it before trusting a count.
"""
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULT_SOURCE_SYSTEM = "ila_bank_app_log"

_TOP_EXCEPTIONS_LIMIT = 8
_TOP_FRAMES_LIMIT = 8
_TOP_SIGNATURES_LIMIT = 8
_TOP_SERVICES_LIMIT = 8
_MAX_TRACKERS = 100
_MAX_ERROR_ITEMS = 50

# Buckets in milliseconds. Open-ended on the right so a pathological
# outlier lands in a labelled bucket instead of stretching the axis.
_DURATION_BUCKETS: List[tuple] = [
    (0, 250, "<250ms"),
    (250, 500, "250ms-500ms"),
    (500, 1000, "500ms-1s"),
    (1000, 2000, "1s-2s"),
    (2000, 5000, "2s-5s"),
    (5000, 10000, "5s-10s"),
    (10000, None, ">10s"),
]

_ERROR_LEVELS = {"ERROR", "CRITICAL"}
_WARN_LEVELS = {"WARN", "WARNING"}


def _details(event: Dict[str, Any]) -> Dict[str, Any]:
    return ((event.get("attributes") or {}).get("details")) or {}


def _percentile(sorted_values: List[float], fraction: float) -> Optional[float]:
    """Nearest-rank percentile. Returns None for an empty series rather
    than 0, so "no timing data" is distinguishable from "0 ms"."""
    if not sorted_values:
        return None
    index = max(0, min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1)))))
    return sorted_values[index]


def _time_bucket(ts: Optional[str], granularity: str) -> Optional[str]:
    """Truncates an ISO timestamp to the hour or the day. String slicing
    rather than datetime parsing: ts_utc is already normalised ISO-8601 UTC
    by ingestion, and a malformed one should be skipped, not raise."""
    if not ts:
        return None
    if granularity == "day":
        return ts[:10] + "T00:00" if len(ts) >= 10 else None
    return ts[:13] + ":00" if len(ts) >= 13 else None


def _pick_granularity(first_ts: Optional[str], last_ts: Optional[str]) -> str:
    """Hourly buckets stop being readable once the window is long: a 30-day
    range is 720 bars, which renders as sub-pixel slivers with unlabelable
    ticks. Past two days the series switches to daily so every bar stays
    wide enough to hover and every tick stays legible."""
    span = _span_ms(first_ts, last_ts)
    return "day" if span is not None and span > 48 * 3600 * 1000 else "hour"


def _span_ms(first: Optional[str], last: Optional[str]) -> Optional[int]:
    if not first or not last:
        return None
    try:
        start = datetime.fromisoformat(first.replace("Z", "+00:00"))
        end = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = (end - start).total_seconds() * 1000
    return int(delta) if delta >= 0 else None


def _bucket_label(milliseconds: float) -> str:
    for low, high, label in _DURATION_BUCKETS:
        if milliseconds >= low and (high is None or milliseconds < high):
            return label
    return _DURATION_BUCKETS[-1][2]


def _exception_type(name: str) -> str:
    """The bare type name from a possibly-namespaced exception.

    A .NET log routinely names the same exception both ways in one entry --
    "System.NullReferenceException:" on the message line, a bare
    "NullReferenceException" in the trailing text. The parser keeps both
    verbatim (it is byte-exact by design), so counting raw strings shows one
    failure as two rows with identical bars. Grouping on the type is what
    makes the count mean "how often this went wrong".
    """
    return name.rsplit(".", 1)[-1] if name else name


def _exception_namespace(name: str) -> Optional[str]:
    return name.rsplit(".", 1)[0] if name and "." in name else None


def _split_frame(method: str) -> tuple:
    """(owner, method_name) from a .NET stack frame.

    Parameter lists are dropped: two frames for the same method with
    different overloads are the same place in the code, and keeping the
    signature makes them read as two unrelated rows once the label is
    truncated. Consecutively repeated namespace segments are collapsed too --
    a class named after its own namespace renders as
    "AFSMW_ILACreditServices.AFSMW_ILACreditServices.X", which wastes the
    whole label on one repeated word.
    """
    if not method:
        return None, None
    bare = method.split("(", 1)[0].strip()
    parts = [p for p in bare.split(".") if p]
    collapsed: List[str] = []
    for part in parts:
        if not collapsed or collapsed[-1] != part:
            collapsed.append(part)
    if not collapsed:
        return None, None
    if len(collapsed) == 1:
        return None, collapsed[0]
    return ".".join(collapsed[:-1]), collapsed[-1]


def compute_ila_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Severity, event-type, exception, duration, tracker and parse-fidelity
    breakdowns over one window of ILA Bank events."""
    if not events:
        return {"status": "no_data", "message": "No ILA Bank activity found in the analyzed window."}

    level_counts: Counter = Counter()
    event_type_counts: Counter = Counter()
    parse_status_counts: Counter = Counter()
    exception_counts: Counter = Counter()
    frame_counts: Counter = Counter()
    signature_counts: Counter = Counter()
    signature_meta: Dict[tuple, Dict[str, Any]] = {}
    service_counts: Counter = Counter()
    http_status_counts: Counter = Counter()
    duration_bucket_counts: Counter = Counter()

    granularity = _pick_granularity(events[0].get("ts_utc"), events[-1].get("ts_utc"))
    timeline: Dict[str, Dict[str, int]] = {}
    durations_ms: List[float] = []
    trackers: Dict[str, Dict[str, Any]] = {}
    untracked_events = 0
    multiline_entries = 0
    sensitive_flagged = 0
    error_items: List[Dict[str, Any]] = []

    for event in events:
        details = _details(event)
        level = (event.get("level") or "UNKNOWN").upper()
        is_error = level in _ERROR_LEVELS
        is_warn = level in _WARN_LEVELS

        level_counts[level] += 1
        event_type_counts[details.get("event_type") or "unclassified"] += 1
        parse_status_counts[details.get("parse_status") or "unknown"] += 1

        if details.get("service"):
            service_counts[details["service"]] += 1
        if details.get("multiline_content"):
            multiline_entries += 1
        if details.get("contains_sensitive_field_names"):
            sensitive_flagged += 1

        # Severity over time. Three named keys rather than one per observed
        # level so the stacked chart has a stable, ordered series set and
        # never grows an unplanned fourth colour.
        bucket = _time_bucket(event.get("ts_utc"), granularity)
        if bucket:
            row = timeline.setdefault(bucket, {"bucket": bucket, "error": 0, "warn": 0, "info": 0})
            row["error" if is_error else "warn" if is_warn else "info"] += 1

        raw_exceptions = details.get("exceptions") or []
        frames = details.get("stack_frames") or []
        # Deduped per entry: one entry naming the exception both ways
        # ("System.NullReferenceException" and a bare "NullReferenceException")
        # is one failure, not two.
        for exception_type in {_exception_type(e) for e in raw_exceptions}:
            exception_counts[exception_type] += 1
        for frame in frames:
            owner, name = _split_frame(frame.get("method") or "")
            if name:
                frame_counts[f"{owner}.{name}" if owner else name] += 1

        # A failure signature pairs WHAT was thrown with WHERE it was thrown.
        # Two independent frequency tables cannot answer "which exception came
        # from which call", which is the only question worth asking of them.
        # The first frame is the throw site; later frames are its callers.
        if raw_exceptions:
            qualified = max(raw_exceptions, key=lambda e: (e.count("."), len(e)))
            exc_type = _exception_type(qualified)
            owner, name = _split_frame((frames[0].get("method") if frames else "") or "")
            key = (exc_type, name)
            signature_counts[key] += 1
            signature_meta.setdefault(
                key,
                {
                    "exception": exc_type,
                    "exception_namespace": _exception_namespace(qualified),
                    "method": name,
                    "owner": owner,
                },
            )
        for status in details.get("status_codes") or []:
            code = status.get("code")
            if code is not None:
                http_status_counts[str(code)] += 1
        for duration in details.get("durations") or []:
            value = duration.get("milliseconds")
            if isinstance(value, (int, float)):
                durations_ms.append(float(value))
                duration_bucket_counts[_bucket_label(float(value))] += 1

        tracker_id = details.get("tracker_id") or (event.get("attributes") or {}).get("correlation_id")
        if not tracker_id:
            untracked_events += 1
            continue

        tracker = trackers.setdefault(
            tracker_id,
            {
                "tracker_id": tracker_id,
                "entries": 0,
                "errors": 0,
                "warnings": 0,
                "first_timestamp": event.get("ts_utc"),
                "last_timestamp": event.get("ts_utc"),
                "event_types": [],
                "exceptions": [],
            },
        )
        tracker["entries"] += 1
        tracker["last_timestamp"] = event.get("ts_utc") or tracker["last_timestamp"]
        if is_error:
            tracker["errors"] += 1
        if is_warn:
            tracker["warnings"] += 1
        event_type = details.get("event_type")
        if event_type and event_type not in tracker["event_types"]:
            tracker["event_types"].append(event_type)
        for exception in details.get("exceptions") or []:
            if exception not in tracker["exceptions"]:
                tracker["exceptions"].append(exception)

        if is_error and len(error_items) < _MAX_ERROR_ITEMS:
            error_items.append(
                {
                    "timestamp": event.get("ts_utc"),
                    "tracker_id": tracker_id,
                    "message": (event.get("message") or "")[:200],
                    "exceptions": details.get("exceptions") or [],
                    "http_codes": [s.get("code") for s in (details.get("status_codes") or [])],
                }
            )

    for tracker in trackers.values():
        tracker["span_ms"] = _span_ms(tracker["first_timestamp"], tracker["last_timestamp"])

    # Failing trackers first, then longest-running: an analyst opens this
    # view because something broke, so the rows that broke lead.
    ranked_trackers = sorted(
        trackers.values(),
        key=lambda t: (-t["errors"], -(t["span_ms"] or 0), -t["entries"]),
    )[:_MAX_TRACKERS]

    # Ranked failure signatures, each carrying its share of all error
    # entries so the leading one can be stated as a headline rather than
    # left for the reader to work out from a bar length.
    error_total = sum(signature_counts.values())
    signatures = [
        {
            **signature_meta[key],
            "count": count,
            "share": round(count / error_total, 4) if error_total else 0.0,
        }
        for key, count in signature_counts.most_common(_TOP_SIGNATURES_LIMIT)
    ]

    durations_ms.sort()
    total_events = len(events)
    error_count = sum(level_counts[lvl] for lvl in _ERROR_LEVELS if lvl in level_counts)
    warn_count = sum(level_counts[lvl] for lvl in _WARN_LEVELS if lvl in level_counts)

    return {
        "status": "ok",
        "window_start": events[0].get("ts_utc"),
        "window_end": events[-1].get("ts_utc"),
        "total_events_analyzed": total_events,
        "total_trackers": len(trackers),
        "untracked_events": untracked_events,
        "error_count": error_count,
        "warning_count": warn_count,
        "error_rate": round(error_count / total_events, 4) if total_events else 0.0,
        "trackers_with_errors": sum(1 for t in trackers.values() if t["errors"]),
        "multiline_entries": multiline_entries,
        "sensitive_field_entries": sensitive_flagged,
        "level_counts": dict(level_counts.most_common()),
        "event_type_counts": dict(event_type_counts.most_common()),
        "parse_status_counts": dict(parse_status_counts.most_common()),
        "severity_granularity": granularity,
        "severity_timeline": [timeline[k] for k in sorted(timeline)],
        "top_exceptions": dict(exception_counts.most_common(_TOP_EXCEPTIONS_LIMIT)),
        "top_stack_frames": dict(frame_counts.most_common(_TOP_FRAMES_LIMIT)),
        "failure_signatures": signatures,
        "headline_failure": signatures[0] if signatures else None,
        "top_services": dict(service_counts.most_common(_TOP_SERVICES_LIMIT)),
        "http_status_counts": dict(http_status_counts.most_common()),
        "duration_stats": {
            "count": len(durations_ms),
            "p50_ms": _percentile(durations_ms, 0.50),
            "p95_ms": _percentile(durations_ms, 0.95),
            "max_ms": durations_ms[-1] if durations_ms else None,
            # Emitted in bucket order, not count order -- a distribution
            # read left-to-right is only meaningful if the axis is ordered
            # by magnitude rather than by frequency.
            "buckets": [
                {"label": label, "count": duration_bucket_counts.get(label, 0)}
                for _, _, label in _DURATION_BUCKETS
            ],
        },
        "trackers": ranked_trackers,
        "recent_errors": error_items,
    }
