"""ILA Bank dashboard analytics (backend/analysis/ila_bank.py)."""
import copy

import pytest

from backend.analysis.ila_bank import compute_ila_summary


def _event(ts, level="INFO", tracker="ILA-1", **details):
    base = {"event_type": "message", "parse_status": "complete", "tracker_id": tracker}
    base.update(details)
    return {
        "ts_utc": ts,
        "level": level,
        "message": details.pop("message", "an entry"),
        "attributes": {"correlation_id": tracker, "details": base},
    }


@pytest.fixture
def events():
    return [
        _event("2026-08-27T09:10:02Z", tracker="ILA-1"),
        _event(
            "2026-08-27T09:10:02Z", level="ERROR", tracker="ILA-1", event_type="error",
            exceptions=["System.Net.Http.HttpRequestException"],
            stack_frames=[{"method": "Afs.Core.Adapter.PostAsync"}],
            status_codes=[{"code": 500, "reason": "Internal Server Error"}],
        ),
        _event(
            "2026-08-27T09:10:04Z", tracker="ILA-1", event_type="request_end",
            durations=[{"milliseconds": 1549}],
        ),
        _event("2026-08-27T09:22:00Z", level="WARN", tracker="ILA-2", event_type="warning_message"),
        _event(
            "2026-08-27T10:05:00Z", tracker="ILA-3", event_type="request_end",
            durations=[{"milliseconds": 12300}],
        ),
    ]


def test_no_data_is_a_status_not_an_exception():
    assert compute_ila_summary([])["status"] == "no_data"


def test_headline_counts(events):
    s = compute_ila_summary(events)
    assert s["total_events_analyzed"] == 5
    assert s["total_trackers"] == 3
    assert s["error_count"] == 1
    assert s["warning_count"] == 1
    assert s["error_rate"] == 0.2
    assert s["trackers_with_errors"] == 1


def test_untracked_entries_counted_separately_not_dropped(events):
    """An entry with no Log Tracker No. is still real activity -- it must
    appear in the totals even though it cannot join a transaction."""
    events.append(
        {"ts_utc": "2026-08-27T10:30:00Z", "level": "INFO", "message": "SMS dispatched",
         "attributes": {"correlation_id": None, "details": {"event_type": "message", "parse_status": "complete"}}}
    )
    s = compute_ila_summary(events)
    assert s["untracked_events"] == 1
    assert s["total_events_analyzed"] == 6
    assert s["total_trackers"] == 3


def test_exceptions_frames_and_http_codes_extracted(events):
    s = compute_ila_summary(events)
    # Grouped on the type, not the raw string -- a namespaced and a bare
    # spelling of one exception are one failure (see the dedupe tests below).
    assert s["top_exceptions"] == {"HttpRequestException": 1}
    assert s["top_stack_frames"] == {"Afs.Core.Adapter.PostAsync": 1}
    assert s["http_status_counts"] == {"500": 1}


def test_duration_buckets_are_in_magnitude_order_not_frequency_order(events):
    """The histogram is read left to right as a scale, so the axis order is
    fixed by the buckets -- sorting by count would make it meaningless."""
    s = compute_ila_summary(events)
    labels = [b["label"] for b in s["duration_stats"]["buckets"]]
    assert labels == ["<250ms", "250ms-500ms", "500ms-1s", "1s-2s", "2s-5s", "5s-10s", ">10s"]
    counts = {b["label"]: b["count"] for b in s["duration_stats"]["buckets"]}
    assert counts["1s-2s"] == 1
    assert counts[">10s"] == 1


def test_percentiles_are_none_not_zero_when_nothing_is_timed():
    """"no timing data" and "0 ms" are different facts and the tile renders
    them differently."""
    s = compute_ila_summary([_event("2026-08-27T09:00:00Z")])
    assert s["duration_stats"]["count"] == 0
    assert s["duration_stats"]["p50_ms"] is None
    assert s["duration_stats"]["max_ms"] is None


def test_trackers_ranked_failing_first(events):
    """An analyst opens this view because something broke, so the rows that
    broke lead regardless of size or recency."""
    s = compute_ila_summary(events)
    assert s["trackers"][0]["tracker_id"] == "ILA-1"
    assert s["trackers"][0]["errors"] == 1
    assert s["trackers"][0]["entries"] == 3


def test_severity_timeline_has_all_three_series_on_every_bucket(events):
    """A stacked chart needs a stable series set: a bucket with no errors
    must still carry error=0, or the stack silently changes shape."""
    s = compute_ila_summary(events)
    assert s["severity_timeline"]
    for point in s["severity_timeline"]:
        assert set(point) == {"bucket", "error", "warn", "info"}
    assert [p["bucket"] for p in s["severity_timeline"]] == sorted(p["bucket"] for p in s["severity_timeline"])


def test_granularity_switches_to_daily_for_long_windows(events):
    """720 hourly bars in a 30-day window render as unreadable slivers."""
    assert compute_ila_summary(events)["severity_granularity"] == "hour"

    stretched = copy.deepcopy(events)
    for i, e in enumerate(stretched):
        e["ts_utc"] = f"2026-08-{10 + i:02d}T09:00:00Z"
    wide = compute_ila_summary(stretched)
    assert wide["severity_granularity"] == "day"
    assert len(wide["severity_timeline"]) == len(stretched)


def test_parse_fidelity_reported(events):
    """Byte-exact capture means unrecognised entries are a fact about the
    log, and the analyst needs it before trusting any count above."""
    events.append(_event("2026-08-27T11:00:00Z", parse_status="unrecognized", event_type=None))
    s = compute_ila_summary(events)
    assert s["parse_status_counts"]["complete"] == 5
    assert s["parse_status_counts"]["unrecognized"] == 1


# --- failure signatures -------------------------------------------------------
# Reproduces what a real ILA error log produced on screen: the same exception
# named two ways, and two frames whose difference fell past the truncation
# point so they rendered as identical rows.

def _error(exceptions, frames, ts="2026-08-27T09:00:00Z"):
    return {
        "ts_utc": ts, "level": "ERROR", "message": "boom",
        "attributes": {"correlation_id": "T1", "details": {
            "event_type": "error", "parse_status": "complete", "tracker_id": "T1",
            "exceptions": exceptions, "stack_frames": [{"method": m} for m in frames]}},
    }


_MQ_FRAMES = [
    "AFSMW_ILACreditServices.IBM_MQ.ConnectMQ_Out(String strQueueManagerName, String strChannel)",
    "AFSMW_ILACreditServices.AFSMW_ILACreditServices.ConnectToIBMInQVPut(String strMsg, String q)",
]


def test_one_exception_named_two_ways_counts_once():
    """A .NET entry names the same exception both qualified and bare. Counting
    raw strings showed one failure as two rows with identical bars."""
    s = compute_ila_summary([_error(["System.NullReferenceException", "NullReferenceException"], _MQ_FRAMES)])
    assert s["top_exceptions"] == {"NullReferenceException": 1}


def test_repeated_namespace_segment_is_collapsed():
    """A class named after its own namespace renders as
    'AFSMW_ILACreditServices.AFSMW_ILACreditServices.X', spending the whole
    label on one repeated word."""
    s = compute_ila_summary([_error(["System.NullReferenceException"], _MQ_FRAMES)])
    assert "AFSMW_ILACreditServices.ConnectToIBMInQVPut" in s["top_stack_frames"]
    assert not any("AFSMW_ILACreditServices.AFSMW_ILACreditServices" in k for k in s["top_stack_frames"])


def test_overloads_of_one_method_are_one_row():
    """Parameter lists are dropped: the same method with two signatures is one
    place in the code, and keeping the params made them two rows once the
    label was truncated."""
    s = compute_ila_summary([
        _error(["System.NullReferenceException"], ["A.B.Put(String a)"]),
        _error(["System.NullReferenceException"], ["A.B.Put(String a, Int32 b)"]),
    ])
    assert s["top_stack_frames"] == {"A.B.Put": 2}


def test_headline_pairs_the_exception_with_its_throw_site():
    events = [_error(["System.NullReferenceException", "NullReferenceException"], _MQ_FRAMES) for _ in range(15)]
    events += [_error(["System.Net.WebException"], ["System.Net.HttpWebRequest.GetResponse()"]) for _ in range(2)]
    s = compute_ila_summary(events)

    headline = s["headline_failure"]
    assert headline["exception"] == "NullReferenceException"
    # The FIRST frame is the throw site; later frames are its callers.
    assert headline["method"] == "ConnectMQ_Out"
    assert headline["owner"] == "AFSMW_ILACreditServices.IBM_MQ"
    assert headline["count"] == 15
    assert headline["share"] == round(15 / 17, 4)

    assert [sig["exception"] for sig in s["failure_signatures"]] == ["NullReferenceException", "WebException"]


def test_headline_is_none_when_nothing_threw():
    s = compute_ila_summary([_event("2026-08-27T09:00:00Z")])
    assert s["headline_failure"] is None
    assert s["failure_signatures"] == []
