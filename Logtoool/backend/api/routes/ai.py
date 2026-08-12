from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.config import settings
from backend.api.deps import (
    get_chat_assistant,
    get_current_user,
    get_db,
    get_explainer,
    get_ollama_client,
)
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager
from backend.llm.chat import LogChatAssistant
from backend.llm.client import OllamaClient
from backend.llm.explain import LogExplainer

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ExplainRequest(BaseModel):
    event_id: str
    context_lines: int = 5


class ChatRequest(BaseModel):
    question: str


@router.get("/ollama/health")
def ollama_health(
    _user: AuthenticatedUser = Depends(get_current_user),
    client: OllamaClient = Depends(get_ollama_client),
):
    is_ok, status = client.health_check()
    return {"available": is_ok, "status": status, "model": client.model, "base_url": client.base_url}


@router.get("/config")
def get_ai_config(_user: AuthenticatedUser = Depends(get_current_user)):
    # Ollama-only by design (sensitive log data stays on the local network --
    # there is no cloud AI fallback path in this app).
    return {"provider": "ollama", "ollama_url": settings.ollama_url, "ollama_model": settings.ollama_model}


@router.post("/explain")
def explain_event(
    body: ExplainRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    explainer: LogExplainer = Depends(get_explainer),
    db: DatabaseManager = Depends(get_db),
):
    event = db.get_event_by_id(body.event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    context = db.get_surrounding_context(body.event_id, context_lines=body.context_lines)
    result, status_msg = explainer.explain_log_event(event, context)
    if result is None:
        raise HTTPException(status_code=503, detail=status_msg)
    return {"explanation": result, "status": status_msg}


@router.post("/chat")
def chat(
    body: ChatRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    assistant: LogChatAssistant = Depends(get_chat_assistant),
):
    sql, results, summary, status_msg = assistant.answer_natural_language_query(body.question)
    if sql is None and results is None:
        raise HTTPException(status_code=503, detail=status_msg)
    return {"sql": sql, "results": results, "summary": summary, "status": status_msg}
