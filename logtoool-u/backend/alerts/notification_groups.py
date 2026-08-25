"""
Manages the notification_groups table: reusable named recipient lists that
an alert rule can reference instead of retyping the same email addresses
on every rule -- see backend/alerts/models.py's NotificationGroupModel.
Same CRUD shape as AlertRuleManager (backend/alerts/rule_manager.py).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.alerts.models import NotificationGroupModel
from backend.core.store import Base

logger = logging.getLogger("logtool.alerts.notification_groups")


class GroupNotFoundError(Exception):
    pass


class GroupNameTakenError(Exception):
    pass


class NotificationGroupManager:
    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def list_groups(self) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            groups = session.query(NotificationGroupModel).order_by(NotificationGroupModel.name).all()
            return [self._to_dict(g) for g in groups]
        finally:
            session.close()

    def get_group(self, group_id: str) -> Dict[str, Any]:
        session = self.Session()
        try:
            group = session.query(NotificationGroupModel).filter_by(group_id=group_id).first()
            if not group:
                raise GroupNotFoundError(group_id)
            return self._to_dict(group)
        finally:
            session.close()

    def create_group(self, name: str, emails: str) -> Dict[str, Any]:
        session = self.Session()
        try:
            if session.query(NotificationGroupModel).filter_by(name=name).first():
                raise GroupNameTakenError(f"A notification group named '{name}' already exists")
            now = datetime.now(timezone.utc).isoformat()
            group = NotificationGroupModel(
                group_id=str(uuid.uuid4()), name=name, emails=emails, created_at=now, updated_at=now
            )
            session.add(group)
            session.commit()
            return self._to_dict(group)
        finally:
            session.close()

    def update_group(self, group_id: str, name: Optional[str] = None, emails: Optional[str] = None) -> Dict[str, Any]:
        session = self.Session()
        try:
            group = session.query(NotificationGroupModel).filter_by(group_id=group_id).first()
            if not group:
                raise GroupNotFoundError(group_id)

            if name and name != group.name:
                if session.query(NotificationGroupModel).filter_by(name=name).first():
                    raise GroupNameTakenError(f"A notification group named '{name}' already exists")
                group.name = name

            if emails is not None:
                group.emails = emails

            group.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            return self._to_dict(group)
        finally:
            session.close()

    def delete_group(self, group_id: str) -> None:
        session = self.Session()
        try:
            session.query(NotificationGroupModel).filter_by(group_id=group_id).delete()
            session.commit()
        finally:
            session.close()

    def _to_dict(self, g: NotificationGroupModel) -> Dict[str, Any]:
        return {
            "group_id": g.group_id,
            "name": g.name,
            "emails": g.emails,
            "created_at": g.created_at,
            "updated_at": g.updated_at,
        }
