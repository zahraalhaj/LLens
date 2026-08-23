import time

import pytest

from backend.alerts.email import EmailDispatcher
from backend.alerts.rule_manager import AlertRuleManager, RuleNameTakenError, RuleNotFoundError, level_meets_threshold
from backend.alerts.rules import AlertRulesProcessor
from backend.alerts.state import AlertDeduplicationEngine


@pytest.fixture
def rule_manager(tmp_path):
    return AlertRuleManager(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def processor(tmp_path, rule_manager):
    db_path = str(tmp_path / "test.db")
    return AlertRulesProcessor(
        email_dispatcher=EmailDispatcher(),
        dedup_engine=AlertDeduplicationEngine(db_path=db_path),
        rule_manager=rule_manager,
        db_path=db_path,
    )


def make_event(level="ERROR", source="svc-a", component="db", message="something broke", line_no=1, correlation_id=None):
    event = {
        "level": level, "source_system": source, "component": component, "message": message,
        "line_no": line_no, "ts_utc": "2026-08-15T10:00:00Z", "raw": f"raw: {message}",
    }
    if correlation_id is not None:
        event["attributes"] = {"correlation_id": correlation_id}
    return event


# -- level_meets_threshold ---------------------------------------------------

def test_level_threshold_exact_match():
    assert level_meets_threshold("WARN", "WARN") is True


def test_level_threshold_higher_severity_passes():
    assert level_meets_threshold("CRITICAL", "ERROR") is True


def test_level_threshold_lower_severity_fails():
    assert level_meets_threshold("INFO", "WARN") is False


def test_level_threshold_unknown_never_implicitly_passes():
    assert level_meets_threshold("UNKNOWN", "DEBUG") is False


# -- AlertRuleManager CRUD ----------------------------------------------------

def test_default_rules_seeded_on_first_use(rule_manager):
    rules = rule_manager.list_rules()
    names = {r["name"] for r in rules}
    assert "CRITICAL Event Immediate Alert" in names
    assert "ERROR Batch Summary Digest" in names


def test_defaults_not_reseeded_after_deletion(tmp_path):
    db_path = str(tmp_path / "test.db")
    mgr1 = AlertRuleManager(db_path=db_path)
    for r in mgr1.list_rules():
        mgr1.delete_rule(r["rule_id"])
    assert mgr1.list_rules() == []

    mgr2 = AlertRuleManager(db_path=db_path)  # re-instantiate, simulating a restart
    assert mgr2.list_rules() == []  # should NOT reseed once rules have existed before


def test_create_rule_rejects_invalid_level(rule_manager):
    with pytest.raises(ValueError):
        rule_manager.create_rule(name="bad", min_level="SUPER_BAD", mode="immediate")


def test_create_rule_rejects_invalid_mode(rule_manager):
    with pytest.raises(ValueError):
        rule_manager.create_rule(name="bad", min_level="ERROR", mode="carrier_pigeon")


def test_create_rule_rejects_duplicate_name(rule_manager):
    rule_manager.create_rule(name="dup", min_level="ERROR", mode="immediate")
    with pytest.raises(RuleNameTakenError):
        rule_manager.create_rule(name="dup", min_level="WARN", mode="digest")


def test_update_nonexistent_rule_raises(rule_manager):
    with pytest.raises(RuleNotFoundError):
        rule_manager.update_rule("does-not-exist", enabled=False)


def test_disable_rule_excludes_it_from_enabled_list(rule_manager):
    r = rule_manager.create_rule(name="toggle-me", min_level="ERROR", mode="immediate")
    rule_manager.update_rule(r["rule_id"], enabled=False)
    enabled_ids = {x["rule_id"] for x in rule_manager.list_enabled_rules()}
    assert r["rule_id"] not in enabled_ids


def test_update_filter_field_to_empty_string_clears_it(rule_manager):
    r = rule_manager.create_rule(name="with-filter", min_level="ERROR", mode="immediate", component_filter="db")
    updated = rule_manager.update_rule(r["rule_id"], component_filter="")
    assert updated["component_filter"] is None


def test_delete_rule_removes_it(rule_manager):
    r = rule_manager.create_rule(name="temp", min_level="ERROR", mode="immediate")
    rule_manager.delete_rule(r["rule_id"])
    with pytest.raises(RuleNotFoundError):
        rule_manager.get_rule(r["rule_id"])


# -- AlertRulesProcessor evaluation -------------------------------------------

def test_immediate_rule_fires_for_each_matching_event(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])  # start clean, no default rules interfering
    rule_manager.create_rule(name="critical-now", min_level="CRITICAL", mode="immediate", dedup_window_minutes=60)

    events = [make_event(level="CRITICAL", line_no=1), make_event(level="CRITICAL", component="cache", line_no=2)]
    triggered = processor.evaluate_batch_alerts("batch-1", events)
    assert len(triggered) == 2


def test_rule_does_not_fire_below_threshold(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="critical-only", min_level="CRITICAL", mode="immediate")

    events = [make_event(level="WARN")]
    triggered = processor.evaluate_batch_alerts("batch-1", events)
    assert triggered == []


def test_disabled_rule_never_fires(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    r = rule_manager.create_rule(name="disabled-rule", min_level="ERROR", mode="immediate")
    rule_manager.update_rule(r["rule_id"], enabled=False)

    triggered = processor.evaluate_batch_alerts("batch-1", [make_event(level="ERROR")])
    assert triggered == []


def test_source_system_filter_narrows_matches(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="payments-only", min_level="ERROR", mode="immediate", source_system_filter="payments")

    events = [make_event(level="ERROR", source="payments-api"), make_event(level="ERROR", source="auth-api")]
    triggered = processor.evaluate_batch_alerts("batch-1", events)
    assert len(triggered) == 1
    assert triggered[0]["source"] == "payments-api"


def test_message_contains_filter(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="oom-only", min_level="ERROR", mode="immediate", message_contains="out of memory")

    events = [make_event(level="ERROR", message="out of memory killer invoked"), make_event(level="ERROR", message="disk full")]
    triggered = processor.evaluate_batch_alerts("batch-1", events)
    assert len(triggered) == 1


def test_digest_mode_fires_once_for_multiple_matches(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="error-digest", min_level="ERROR", mode="digest")

    events = [make_event(level="ERROR", line_no=i) for i in range(5)]
    triggered = processor.evaluate_batch_alerts("batch-1", events)
    assert len(triggered) == 1
    assert triggered[0]["count"] == 5


def test_dedup_suppresses_repeat_within_window(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="critical-now", min_level="CRITICAL", mode="immediate", dedup_window_minutes=60)

    event = make_event(level="CRITICAL", message="same exact failure")
    first = processor.evaluate_batch_alerts("batch-1", [event])
    second = processor.evaluate_batch_alerts("batch-2", [event])  # identical signature, different batch
    assert len(first) == 1
    assert len(second) == 0  # suppressed as duplicate


def test_dedup_does_not_suppress_across_different_rules(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="rule-a", min_level="CRITICAL", mode="immediate")
    rule_manager.create_rule(name="rule-b", min_level="ERROR", mode="immediate")  # CRITICAL also satisfies ERROR threshold

    triggered = processor.evaluate_batch_alerts("batch-1", [make_event(level="CRITICAL")])
    assert len(triggered) == 2  # both rules fire independently, not deduped against each other


def test_dedup_persists_across_new_engine_instance(tmp_path, rule_manager, processor):
    """The whole point of moving off in-memory dedup: a fresh instance
    (simulating a process restart) must still remember prior suppression."""
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="critical-now", min_level="CRITICAL", mode="immediate", dedup_window_minutes=60)

    event = make_event(level="CRITICAL", message="persists across restart")
    processor.evaluate_batch_alerts("batch-1", [event])

    db_path = str(tmp_path / "test.db")
    fresh_dedup = AlertDeduplicationEngine(db_path=db_path)  # new instance, same DB file
    fresh_processor = AlertRulesProcessor(
        email_dispatcher=EmailDispatcher(), dedup_engine=fresh_dedup, rule_manager=rule_manager, db_path=db_path
    )
    triggered = fresh_processor.evaluate_batch_alerts("batch-2", [event])
    assert triggered == []  # still suppressed, even though the engine object is brand new


def test_dispatch_history_records_immediate_alert(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="critical-now", min_level="CRITICAL", mode="immediate")

    processor.evaluate_batch_alerts("batch-1", [make_event(level="CRITICAL")])
    history = processor.get_dispatch_history()
    assert history["total"] == 1
    assert history["entries"][0]["rule_name"] == "critical-now"
    assert history["entries"][0]["event_count"] == 1


def test_dispatch_history_records_digest_with_event_count(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="error-digest", min_level="ERROR", mode="digest")

    events = [make_event(level="ERROR", line_no=i) for i in range(3)]
    processor.evaluate_batch_alerts("batch-1", events)
    history = processor.get_dispatch_history()
    assert history["entries"][0]["event_count"] == 3


def test_dispatch_history_pagination(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="critical-now", min_level="CRITICAL", mode="immediate", dedup_window_minutes=0)

    for i in range(3):
        processor.evaluate_batch_alerts(f"batch-{i}", [make_event(level="CRITICAL", message=f"unique failure {i}")])

    page1 = processor.get_dispatch_history(page=1, page_size=2)
    assert len(page1["entries"]) == 2
    assert page1["total"] == 3


# ---------------------------------------------------------------------------
# Feature 6: Alert -> Investigation deep link (correlation_field/correlation_value)
# ---------------------------------------------------------------------------


def test_immediate_alert_persists_correlation_identifier(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="critical-now", min_level="CRITICAL", mode="immediate", dedup_window_minutes=0)

    processor.evaluate_batch_alerts("batch-1", [make_event(level="CRITICAL", correlation_id="TXN-ALERT-1")])
    entry = processor.get_dispatch_history()["entries"][0]
    assert entry["correlation_field"] == "correlation_id"
    assert entry["correlation_value"] == "TXN-ALERT-1"


def test_immediate_alert_without_correlation_id_leaves_fields_none(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="critical-now", min_level="CRITICAL", mode="immediate", dedup_window_minutes=0)

    processor.evaluate_batch_alerts("batch-1", [make_event(level="CRITICAL")])
    entry = processor.get_dispatch_history()["entries"][0]
    assert entry["correlation_field"] is None
    assert entry["correlation_value"] is None


def test_digest_alert_persists_first_matching_events_identifier(rule_manager, processor):
    for r in rule_manager.list_rules():
        rule_manager.delete_rule(r["rule_id"])
    rule_manager.create_rule(name="error-digest", min_level="ERROR", mode="digest")

    events = [
        make_event(level="ERROR", line_no=1, correlation_id="TXN-FIRST"),
        make_event(level="ERROR", line_no=2, correlation_id="TXN-SECOND"),
    ]
    processor.evaluate_batch_alerts("batch-1", events)
    entry = processor.get_dispatch_history()["entries"][0]
    assert entry["correlation_value"] == "TXN-FIRST"  # first matching event only, documented limitation


def test_manual_dispatch_leaves_correlation_fields_none(rule_manager, processor):
    processor.log_manual_dispatch("test", True, "ok", "a@b.com")
    entry = processor.get_dispatch_history()["entries"][0]
    assert entry["correlation_field"] is None
    assert entry["correlation_value"] is None
