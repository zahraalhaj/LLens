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
    # server's default ALERT_EMAIL_TO env var.
    recipients = Column(String, nullable=True)

    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    created_by_user_id = Column(String, nullable=True)


class AlertDispatchLogModel(Base):
    """Persisted record of every alert email actually sent (or attempted) --
    addresses the 'no dispatch history' gap flagged in the original audit."""

    __tablename__ = "alert_dispatch_log"

    dispatch_id = Column(String, primary_key=True)
    rule_id = Column(String, nullable=True)  # nullable: manual test-sends have no rule
    rule_name = Column(
        String, nullable=False
    )  # denormalized -- survives the rule being deleted later
    batch_id = Column(String, nullable=True)
    triggered_at = Column(String, nullable=False)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    success = Column(Integer, nullable=False)
    status_message = Column(Text, nullable=True)
    event_count = Column(Integer, nullable=False, default=1)


class AlertDedupStateModel(Base):
    """Persisted (not in-memory) suppression-window tracking, keyed by
    (rule_id, source, component, message signature) -- an in-memory dict
    was the original implementation and lost all suppression state on
    every restart, which could cause an alert storm right after a
    deploy/restart, exactly when you don't want one."""

    __tablename__ = "alert_dedup_state"

    dedup_key = Column(String, primary_key=True)  # composite key, pre-joined
    last_fired_at = Column(String, nullable=False)


class AlertRuleSeedMarkerModel(Base):
    """A single row that flags 'default rules have already been seeded
    once.' Deliberately separate from checking whether any rules currently
    exist -- an admin who deletes every rule and then restarts the app
    should NOT see the defaults silently reappear. Row count == 0 doesn't
    distinguish 'never seeded' from 'seeded, then everything deleted';
    this marker does."""

    __tablename__ = "alert_rule_seed_marker"

    marker_id = Column(String, primary_key=True)  # always "seeded"
