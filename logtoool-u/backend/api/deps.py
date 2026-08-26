"""
Service singletons + auth dependencies, shared across all routes.

Services are process-wide singletons (via lru_cache) because some of them
hold meaningful in-memory state that must not be recreated per-request:
AlertDeduplicationEngine's suppression window, in particular, would be
useless if a fresh instance were created on every call.
"""
from functools import lru_cache

from fastapi import Cookie, Depends, HTTPException, status

from backend.alerts.email import EmailDispatcher
from backend.alerts.notification_groups import NotificationGroupManager
from backend.alerts.rule_manager import AlertRuleManager
from backend.alerts.rules import AlertRulesProcessor
from backend.alerts.state import AlertDeduplicationEngine
from backend.api.config import settings
from backend.auth.rate_limit import LoginRateLimiter
from backend.auth.service import AuthenticatedUser, AuthService
from backend.core.ingest import LogIngestionEngine
from backend.core.profiles import ProfileManager
from backend.core.store import DatabaseManager
from backend.llm.ai_analyst import AIAnalystAssistant
from backend.llm.audit import AIAnalystAuditLog
from backend.llm.chat import LogChatAssistant
from backend.llm.client import OllamaClient
from backend.llm.explain import LogExplainer
from backend.llm.profile_gen import LLMProfileGenerator
from backend.remote.service import RemoteMachineService

SESSION_COOKIE_NAME = "logtool_session"


@lru_cache
def get_db() -> DatabaseManager:
    return DatabaseManager(db_path=settings.db_path)


@lru_cache
def get_profile_manager() -> ProfileManager:
    return ProfileManager(profiles_dir=settings.profiles_dir)


@lru_cache
def get_auth_service() -> AuthService:
    return AuthService(db_path=settings.db_path, session_lifetime_hours=settings.session_lifetime_hours)


@lru_cache
def get_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter()


@lru_cache
def get_ollama_client() -> OllamaClient:
    return OllamaClient(base_url=settings.ollama_url, model=settings.ollama_model)


@lru_cache
def get_ingestion_engine() -> LogIngestionEngine:
    return LogIngestionEngine(
        db_manager=get_db(),
        profile_manager=get_profile_manager(),
        batch_size=settings.batch_size,
    )


@lru_cache
def get_explainer() -> LogExplainer:
    return LogExplainer(ollama_client=get_ollama_client())


@lru_cache
def get_chat_assistant() -> LogChatAssistant:
    return LogChatAssistant(db_manager=get_db(), ollama_client=get_ollama_client())


@lru_cache
def get_ai_analyst_assistant() -> AIAnalystAssistant:
    return AIAnalystAssistant(ollama_client=get_ollama_client())


@lru_cache
def get_ai_analyst_audit_log() -> AIAnalystAuditLog:
    return AIAnalystAuditLog(db_path=settings.db_path)


@lru_cache
def get_profile_generator() -> LLMProfileGenerator:
    return LLMProfileGenerator(ollama_client=get_ollama_client())


# NOT lru_cache'd: SMTP settings can change at runtime via the Settings GUI.
# A cached instance would hold stale values until server restart.

def get_email_dispatcher() -> EmailDispatcher:
    return EmailDispatcher(
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_user=settings.smtp_user,
        smtp_password=settings.smtp_password,
        alert_email_to=settings.alert_email_to,
    )


@lru_cache
def get_dedup_engine() -> AlertDeduplicationEngine:
    return AlertDeduplicationEngine(db_path=settings.db_path)


@lru_cache
def get_alert_rule_manager() -> AlertRuleManager:
    return AlertRuleManager(db_path=settings.db_path)


@lru_cache
def get_notification_group_manager() -> NotificationGroupManager:
    return NotificationGroupManager(db_path=settings.db_path)


# NOT lru_cache'd: depends on get_email_dispatcher which is not cached,
# so a new processor picks up the latest SMTP settings each time.

def get_alert_processor() -> AlertRulesProcessor:
    return AlertRulesProcessor(
        email_dispatcher=get_email_dispatcher(),
        dedup_engine=get_dedup_engine(),
        rule_manager=get_alert_rule_manager(),
        group_manager=get_notification_group_manager(),
        db_path=settings.db_path,
    )


@lru_cache
def get_remote_machine_service() -> RemoteMachineService:
    return RemoteMachineService(
        db_path=settings.db_path,
        ingestion_engine=get_ingestion_engine(),
        db_manager=get_db(),
        alert_processor=get_alert_processor(),
    )


# -- Auth guards --------------------------------------------------------

def get_current_user_unchecked(
    logtool_session: str | None = Cookie(default=None),
    auth: AuthService = Depends(get_auth_service),
) -> AuthenticatedUser:
    """Validates the session but does NOT enforce the must-change-password
    gate -- only for the handful of routes a pending-password-change user
    still needs: /me, /change-password, /logout."""
    user = auth.validate_session(logtool_session) if logtool_session else None
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def get_current_user(
    user: AuthenticatedUser = Depends(get_current_user_unchecked),
) -> AuthenticatedUser:
    """The guard used by every other route. Enforced server-side, not just
    hidden in the UI -- a user whose password is pending change can't work
    around the forced-change screen by calling other endpoints directly."""
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password_change_required",
        )
    return user


def require_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
