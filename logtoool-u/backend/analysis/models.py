"""
Persisted availability state, so alerting fires on a state TRANSITION
(down->up or up->down) rather than once per scheduler tick while the
condition holds -- without this, "V+ is down" would generate a fresh email
every tick for as long as the outage lasts.
"""
from sqlalchemy import Column, Integer, String

from backend.core.store import Base


class ServiceAvailabilityStateModel(Base):
    __tablename__ = "service_availability_state"

    service_name = Column(String, primary_key=True)  # e.g. "vplus_stepup"
    is_down = Column(Integer, nullable=False, default=0)
    down_since = Column(String, nullable=True)
    last_checked_at = Column(String, nullable=False)
    last_alert_sent_at = Column(String, nullable=True)
