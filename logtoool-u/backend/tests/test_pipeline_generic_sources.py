"""
Tests for run_analysis_pipeline()'s handling of non-payment-family
source_systems -- declarative profiles (and the 4 custom parsers outside
the 5 LogFamily families) now flow into the pipeline as LogFamily.GENERIC
events instead of being silently dropped. Kept in its own file, separate
from test_pipeline.py, since that file has pre-existing, unrelated
cross-test flakiness from the pipeline's module-level result cache.
"""
import pytest

from backend.analysis.pipeline import run_analysis_pipeline
from backend.analysis.normalized_schema import LogFamily
from backend.core.profiles import ProfileManager
from backend.core.schema import BatchRecord, CanonicalLogEvent, LogLevel, ParserProfile, ProfileType, TimestampConfidence
from backend.core.store import DatabaseManager


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(db_path=str(tmp_path / "test_logs.db"))


@pytest.fixture
def profile_manager(tmp_path):
    return ProfileManager(profiles_dir=str(tmp_path / "profiles"))


def _insert(db, event_id, ts, source_system, correlation_id=None, session_id=None, batch_id=None):
    batch_id = batch_id or f"batch-{event_id}"
    batch = BatchRecord(batch_id=batch_id, file_name="test.log", file_size_bytes=100, total_events=1)
    attrs = {}
    if correlation_id is not None:
        attrs["correlation_id"] = correlation_id
    if session_id is not None:
        attrs["session_id"] = session_id
    event = CanonicalLogEvent(
        event_id=event_id, batch_id=batch_id, file_name="test.log", line_no=1,
        ts_utc=ts, ts_raw=ts, ts_confidence=TimestampConfidence.PARSED,
        level=LogLevel.INFO, source_system=source_system, component="app",
        message="demo", raw=f"raw-{event_id}", attributes=attrs,
    )
    db.insert_batch_and_events(batch, [event])


def test_generic_source_system_reaches_the_pipeline(db):
    """Previously dropped entirely by normalize_events() -- must now show
    up in the bundle as a GENERIC event, not be silently absent."""
    _insert(db, "g1", "2026-08-21T09:00:00Z", "my_syslog_profile")

    bundle = run_analysis_pipeline(db, source_systems=["my_syslog_profile"])

    assert len(bundle.events) == 1
    assert bundle.events[0].log_family == LogFamily.GENERIC


def test_default_source_systems_includes_non_family_sources(db):
    """Omitting source_systems entirely (the normal route-handler case)
    must pick up every distinct source_system actually present, not just
    the 5 hardcoded payment families.

    Passes a date range unique to this test -- run_analysis_pipeline()'s
    module-level result cache is keyed on (date_from, date_to,
    source_systems, limit_per_family) only, and every other call in this
    file/suite that also omits source_systems would otherwise share this
    same all-defaults cache key within the TTL (a pre-existing
    characteristic of the cache, unrelated to and out of scope for this
    change)."""
    _insert(db, "g1", "2026-08-21T09:00:00Z", "my_syslog_profile")

    bundle = run_analysis_pipeline(db, date_from="2020-01-01T00:00:00Z", date_to="2030-01-01T00:00:00Z")

    assert any(e.log_family == LogFamily.GENERIC for e in bundle.events)


def test_generic_events_correlate_via_profile_declared_correlation_keys(db, profile_manager):
    """End-to-end: a declarative profile's correlation_keys lets two
    otherwise-unrelated generic events correlate into one flow through
    the real pipeline, not just the unit-level correlate.py tests.

    Uses a source_system name distinct from the other tests in this file
    -- run_analysis_pipeline()'s module-level result cache is keyed on
    (date_from, date_to, source_systems, limit_per_family) only, not on
    which DatabaseManager/ProfileManager instance was passed, so two
    tests calling it with the identical source_systems list within the
    cache TTL would otherwise collide (a pre-existing characteristic of
    the cache, unrelated to and out of scope for this change)."""
    profile_manager.save_profile(
        ParserProfile(
            name="My Syslog",
            type=ProfileType.REGEX,
            pattern=r"^(?P<timestamp>.*)$",
            default_source_system="correlation_keys_syslog_profile",
            correlation_keys=["session_id"],
        )
    )
    _insert(db, "g1", "2026-08-21T09:00:00Z", "correlation_keys_syslog_profile", session_id="SESS-1")
    _insert(db, "g2", "2026-08-21T09:00:05Z", "correlation_keys_syslog_profile", session_id="SESS-1")

    bundle = run_analysis_pipeline(
        db, source_systems=["correlation_keys_syslog_profile"], profile_manager=profile_manager
    )

    assert len(bundle.flows) == 1
    assert sorted(bundle.flows[0].linked_event_ids) == ["g1", "g2"]


def test_generic_events_without_profile_manager_still_correlate_via_correlation_id(db):
    """No profile_manager passed -- generic events still get
    attributes.correlation_id for free (the universal custom-parser
    convention), so correlation isn't fully lost even without profile
    configuration."""
    _insert(db, "g1", "2026-08-21T09:00:00Z", "some_custom_parser", correlation_id="TXN-G1")
    _insert(db, "g2", "2026-08-21T09:00:05Z", "some_custom_parser", correlation_id="TXN-G1")

    bundle = run_analysis_pipeline(db, source_systems=["some_custom_parser"])

    assert len(bundle.flows) == 1
    assert sorted(bundle.flows[0].linked_event_ids) == ["g1", "g2"]


def test_payment_family_events_unaffected_by_generic_source_presence(db, profile_manager):
    """A payment-family flow must correlate identically whether or not
    unrelated generic-source events are also present in the same
    pipeline run."""
    _insert(db, "c1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", correlation_id="TXN-C1")
    _insert(db, "g1", "2026-08-21T09:00:00Z", "my_syslog_profile", session_id="SESS-1")

    bundle = run_analysis_pipeline(
        db, source_systems=["cardinal_stepup_oob_log", "my_syslog_profile"], profile_manager=profile_manager
    )

    assert len(bundle.flows) == 2  # cardinal event and generic event stay in separate flows
    families_by_flow = {frozenset(f.log_families) for f in bundle.flows}
    assert frozenset({"cardinal_stepup_oob_log"}) in families_by_flow
    assert frozenset({"generic_profile"}) in families_by_flow
