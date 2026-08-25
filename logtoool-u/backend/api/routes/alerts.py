from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.alerts.email import EmailDispatcher
from backend.alerts.notification_groups import GroupNameTakenError, GroupNotFoundError, NotificationGroupManager
from backend.alerts.rule_manager import AlertRuleManager, RuleNameTakenError, RuleNotFoundError
from backend.alerts.rules import AlertRulesProcessor
from backend.alerts.state import AlertDeduplicationEngine
from backend.api.deps import (
    get_alert_processor,
    get_alert_rule_manager,
    get_current_user,
    get_dedup_engine,
    get_email_dispatcher,
    get_notification_group_manager,
    require_admin,
)
from backend.auth.service import AuthenticatedUser

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class CreateRuleRequest(BaseModel):
    name: str
    min_level: str = "ERROR"
    mode: str = "immediate"  # 'immediate' | 'digest'
    source_system_filter: Optional[str] = None
    component_filter: Optional[str] = None
    message_contains: Optional[str] = None
    dedup_window_minutes: int = Field(default=60, ge=1)
    recipients: Optional[str] = None  # comma-separated; empty = server default
    notification_group_id: Optional[str] = None  # takes precedence over `recipients` when set
    dedup_windows: Optional[Dict[str, int]] = None  # severity name -> minutes; unset severities fall back to dedup_window_minutes


class UpdateRuleRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    min_level: Optional[str] = None
    mode: Optional[str] = None
    source_system_filter: Optional[str] = None
    component_filter: Optional[str] = None
    message_contains: Optional[str] = None
    dedup_window_minutes: Optional[int] = Field(default=None, ge=1)
    recipients: Optional[str] = None
    notification_group_id: Optional[str] = None
    dedup_windows: Optional[Dict[str, int]] = None


class TestAlertRequest(BaseModel):
    recipient_override: Optional[str] = None


class CreateGroupRequest(BaseModel):
    name: str
    emails: str  # comma-separated


class UpdateGroupRequest(BaseModel):
    name: Optional[str] = None
    emails: Optional[str] = None


@router.get("/rules")
def list_rules(
    _user: AuthenticatedUser = Depends(get_current_user),
    manager: AlertRuleManager = Depends(get_alert_rule_manager),
):
    return manager.list_rules()


@router.post("/rules")
def create_rule(
    body: CreateRuleRequest,
    admin: AuthenticatedUser = Depends(require_admin),
    manager: AlertRuleManager = Depends(get_alert_rule_manager),
):
    try:
        return manager.create_rule(**body.model_dump(), created_by_user_id=admin.user_id)
    except RuleNameTakenError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/rules/{rule_id}")
def get_rule(
    rule_id: str,
    _user: AuthenticatedUser = Depends(get_current_user),
    manager: AlertRuleManager = Depends(get_alert_rule_manager),
):
    try:
        return manager.get_rule(rule_id)
    except RuleNotFoundError:
        raise HTTPException(status_code=404, detail="Rule not found")


@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: str,
    body: UpdateRuleRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
    manager: AlertRuleManager = Depends(get_alert_rule_manager),
):
    try:
        return manager.update_rule(rule_id, **body.model_dump())
    except RuleNotFoundError:
        raise HTTPException(status_code=404, detail="Rule not found")
    except RuleNameTakenError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    manager: AlertRuleManager = Depends(get_alert_rule_manager),
):
    manager.delete_rule(rule_id)
    return {"ok": True}


@router.get("/history")
def get_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    _user: AuthenticatedUser = Depends(get_current_user),
    processor: AlertRulesProcessor = Depends(get_alert_processor),
):
    return processor.get_dispatch_history(page=page, page_size=page_size)


@router.post("/test")
def test_alert(
    body: TestAlertRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    dispatcher: EmailDispatcher = Depends(get_email_dispatcher),
    processor: AlertRulesProcessor = Depends(get_alert_processor),
):
    subject = "[Test] Log Visualization Tool alert dispatch check"
    success, status_msg = dispatcher.send_alert_email(
        subject=subject,
        body_text="This is a test alert triggered manually from the Alerts page.",
        recipient_override=body.recipient_override,
    )
    recipient_used = body.recipient_override or dispatcher.alert_email_to
    processor.log_manual_dispatch(subject, success, status_msg, recipient_used)
    return {"success": success, "status": status_msg}


@router.post("/dedup/reset")
def reset_dedup_state(
    _admin: AuthenticatedUser = Depends(require_admin),
    dedup: AlertDeduplicationEngine = Depends(get_dedup_engine),
):
    dedup.clear_state()
    return {"ok": True}


@router.get("/groups")
def list_groups(
    _user: AuthenticatedUser = Depends(get_current_user),
    manager: NotificationGroupManager = Depends(get_notification_group_manager),
):
    return manager.list_groups()


@router.post("/groups")
def create_group(
    body: CreateGroupRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
    manager: NotificationGroupManager = Depends(get_notification_group_manager),
):
    try:
        return manager.create_group(body.name, body.emails)
    except GroupNameTakenError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/groups/{group_id}")
def get_group(
    group_id: str,
    _user: AuthenticatedUser = Depends(get_current_user),
    manager: NotificationGroupManager = Depends(get_notification_group_manager),
):
    try:
        return manager.get_group(group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=404, detail="Notification group not found")


@router.put("/groups/{group_id}")
def update_group(
    group_id: str,
    body: UpdateGroupRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
    manager: NotificationGroupManager = Depends(get_notification_group_manager),
):
    try:
        return manager.update_group(group_id, name=body.name, emails=body.emails)
    except GroupNotFoundError:
        raise HTTPException(status_code=404, detail="Notification group not found")
    except GroupNameTakenError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/groups/{group_id}")
def delete_group(
    group_id: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    manager: NotificationGroupManager = Depends(get_notification_group_manager),
):
    manager.delete_group(group_id)
    return {"ok": True}
