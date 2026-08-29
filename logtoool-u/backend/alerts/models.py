"""
Alert system models. Shares the same declarative Base as everything else --
one SQLite file, Base.metadata.create_all() picks up every model.
"""
from sqlalchemy import Column, Integer, String, Text

from backend.core.store import Base


class AlertRuleModel(Base):
    __tablename__ = "alert_rules"

    rule_id = Column(String, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    enabled = Column(Integer, nullable=False, default=1)

    # 'severity' (default) = the classic per-event min_level/filters match
    # below. 'anomaly' = fires from the statistical outlier detector in
    # core/profiling.py instead (see AlertRulesProcessor.evaluate_anomaly_rules)
    # -- min_level/source_system_filter/component_filter/message_contains are
    # ignored for anomaly-triggered rules.
    trigger_type = Column(String, nullable=False, default="severity")

    # Fires for events at or above this severity (DEBUG < INFO < WARN < ERROR < CRITICAL).
    min_level = Column(String, nullable=False, default="ERROR")

    # Optional narrowing filters -- empty/null means "match anything".
    source_system_filter = Column(String, nullable=True)
    component_filter = Column(String, nullable=True)
    message_contains = Column(String, nullable=True)

    # 'immediate' = one email per matching event. 'digest' = one summary
    # email per batch covering every matching event in it.
    mode = Column(String, nullable=False, default="immediate")

    dedup_window_minutes = Column(Integer, nullable=False, default=60)

    # Comma-separated email addresses. Empty/null falls back to the
    # server's default ALERT_EMAIL_TO env var. Ignored at dispatch time
    # when notification_group_id is set -- see rules.py's
    # _effective_recipients().
    recipients = Column(String, nullable=True)

    # When set, dispatch uses this NotificationGroupModel's emails instead
    # of `recipients` above -- lets one named group be reused across rules
    # instead of retyping the same email list on each one.
    notification_group_id = Column(String, nullable=True)

    # JSON object mapping severity name -> suppression window in minutes,
    # e.g. {"CRITICAL": 0, "ERROR": 15, "WARN": 60} -- lets one rule notify
    # more or less often depending on the SEVERITY OF THE TRIGGERING EVENT,
    # not just the rule's own min_level threshold. NULL means "not
    # configured": every severity then falls back to the flat
    # dedup_window_minutes above, so every rule created before this field
    # existed keeps behaving exactly as it did. See rules.py's
    # _effective_dedup_window().
    dedup_windows_json = Column(Text, nullable=True)

    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    created_by_user_id = Column(String, nullable=True)


class NotificationGroupModel(Base):
    """A reusable named list of recipients (e.g. "Payments Team",
    "On-Call") that an AlertRuleModel can reference by id instead of every
    rule retyping the same email addresses -- see
    AlertRuleModel.notification_group_id."""

    __tablename__ = "notification_groups"

    group_id = Column(String, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    emails = Column(String, nullable=False)  # comma-separated, same convention as AlertRuleModel.recipients

    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class AlertDispatchLogModel(Base):
    """Persisted record of every alert email actually sent (or attempted) --
    addresses the 'no dispatch history' gap flagged in the original audit."""
    __tablename__ = "alert_dispatch_log"

    dispatch_id = Column(String, primary_key=True)
    rule_id = Column(String, nullable=True)  # nullable: manual test-sends have no rule
    rule_name = Column(String, nullable=False)  # denormalized -- survives the rule being deleted later
    batch_id = Column(String, nullable=True)
    triggered_at = Column(String, nullable=False)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    success = Column(Integer, nullable=False)
    status_message = Column(Text, nullable=True)
    event_count = Column(Integer, nullable=False, default=1)

    # Best-effort correlation identifier for the triggering event, so the
    # frontend can deep-link "View Investigation" straight into the
    # correlated flow/case model (backend/analysis/dashboards.py's
    # search_flows()). Nullable -- older rows, manual test-sends, and any
    # event whose attributes carried no correlation_id all leave these
    # None. For digest mode (many events per dispatch), this is the FIRST
    # matching event's identifier only, not a per-event mapping.
    correlation_field = Column(String, nullable=True)
    correlation_value = Column(String, nullable=True)


class AlertDedupStateModel(Base):
    """Persisted (not in-memory) suppression-window tracking, keyed by
    (rule_id, source, component, message signature) -- an in-memory dict
    was the original implementation and lost all suppression state on
    every restart, which could cause an alert storm right after a
    deploy/restart, exactly when you don't want one.

    This row doubles as the "active alert" record: one row per distinct
    (rule, source, component, message-signature) is exactly the identity a
    firing/acknowledged/resolved lifecycle needs, so rather than a
    parallel table, the lifecycle columns below live right alongside the
    existing suppression timer. dedup_key itself is a one-way hash (see
    state.py's _make_key), so the plaintext context columns are what let
    the UI show anything readable."""
    __tablename__ = "alert_dedup_state"

    dedup_key = Column(String, primary_key=True)  # composite key, pre-joined
    last_fired_at = Column(String, nullable=False)

    # Lifecycle: 'firing' | 'acknowledged' | 'resolved'. New signatures
    # start 'firing'; a signature that fires again after being 'resolved'
    # reopens back to 'firing' (see state.py's should_fire_alert) rather
    # than silently staying 'resolved' while still actively occurring --
    # same reopen-on-recurrence convention PagerDuty/Opsgenie use.
    status = Column(String, nullable=False, default="firing")

    # Plaintext context for the UI -- dedup_key can't be reversed.
    rule_id = Column(String, nullable=True)
    rule_name = Column(String, nullable=True)
    source_system = Column(String, nullable=True)
    component = Column(String, nullable=True)
    message_snippet = Column(String, nullable=True)
    correlation_field = Column(String, nullable=True)
    correlation_value = Column(String, nullable=True)

    first_fired_at = Column(String, nullable=True)
    acknowledged_at = Column(String, nullable=True)
    acknowledged_by_user_id = Column(String, nullable=True)
    resolved_at = Column(String, nullable=True)
    resolved_by_user_id = Column(String, nullable=True)


class AlertRuleSeedMarkerModel(Base):
    """A single row that flags 'default rules have already been seeded
    once.' Deliberately separate from checking whether any rules currently
    exist -- an admin who deletes every rule and then restarts the app
    should NOT see the defaults silently reappear. Row count == 0 doesn't
    distinguish 'never seeded' from 'seeded, then everything deleted';
    this marker does."""
    __tablename__ = "alert_rule_seed_marker"

    marker_id = Column(String, primary_key=True)  # always "seeded"
