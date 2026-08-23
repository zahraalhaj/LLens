"""
AI Investigation & Explanation -- Phase 7 of the LLens Multi-Log Analysis
Strategy.

Wraps backend/analysis/investigation.py's deterministic InvestigationCase
with an LLM-generated narrative. The deterministic engine remains
authoritative: this module NEVER lets the LLM set/change any field except
case_summary.narrative, and even that is validated line-by-line against
the deterministic facts before being accepted -- see _validate_statements().

WHAT THE LLM SEES: only the safe, already-normalized/masked projection
built by backend/analysis/investigation.build_llm_safe_timeline() plus the
already-computed case_summary/findings/correlation/data_quality fields --
never raw_reference, never a raw log line, never an unmasked contact/card
value (Phase 2 already guarantees the latter; this module additionally
guarantees the former by construction, not just by prompt instruction).
"""
import json
import logging
import re
from typing import List, Optional, Tuple

from backend.analysis.investigation import build_llm_safe_timeline
from backend.analysis.investigation_schema import (
    InvestigationCase,
    NarrativeRole,
    NarrativeStatement,
    StatementType,
)
from backend.llm.client import OllamaClient

logger = logging.getLogger("logtool.llm.investigate")

_VALID_ROLES = {r.value for r in NarrativeRole}
_VALID_TYPES = {t.value for t in StatementType}

_FORBIDDEN_SUBSTRING_RULES: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bfraud(ulent)?\b", re.IGNORECASE), "fraud inference is never permitted"),
    (
        re.compile(r"\bcustomer\s+(likely\s+)?(intend|wanted|tried\s+to|meant\s+to|was\s+trying)", re.IGNORECASE),
        "customer intent/motivation inference is never permitted",
    ),
    (re.compile(r"\bdelivered\b", re.IGNORECASE), "delivery cannot be claimed -- no DELIVERED state exists in the data model"),
    # A bare run of 6+ digits could be an OTP, partial PAN, or phone number
    # that was never in the safe payload we gave the model -- defense in
    # depth against fabrication, on top of Phase 2's masking guarantees.
    (re.compile(r"\b\d{6,}\b"), "a long digit sequence that could be a secret/PII value"),
)

_SYSTEM_PROMPT = """You are a senior payment-systems support analyst writing an internal case note.

You are given a DETERMINISTICALLY computed case file for one correlated transaction flow. Every fact in it (status, stages, findings, correlation, timestamps, event IDs) was already computed by a rule-based engine BEFORE you were called. You were not given any raw log text -- only already-structured, already-masked facts. Do not claim to have analyzed "the logs" directly; you are explaining what the structured case file already shows.

Write narrative statements for exactly these 9 topics (the "role" field):
what_happened, journey_summary, where_flow_stopped, technical_boundary, supporting_evidence, missing_evidence, next_investigation_area, similar_incidents, analyst_case_summary

For EVERY statement:
- Set "statement_type" to exactly one of: "FACT" (directly stated in the given data), "INTERPRETATION" (a reasonable inference, clearly framed as inference), or "MISSING EVIDENCE" (describing what is absent).
- Set "evidence_event_ids" using ONLY event IDs that literally appear in the timeline/findings you were given. Never invent an ID.
- Set "source_files" using ONLY files that appear in the given data.

ABSOLUTE RULES -- any statement violating these will be discarded:
1. When information is absent, say "not found in captured logs". Use no other phrasing for absence.
2. Absence of evidence is never proof of failure. Say what is unknown; do not call it a failure.
3. Never say an SMS/OTP/email was delivered to the customer based on a queue-confirmation event alone. Queued is not delivered.
4. Never call an OOB status of PENDING a technical failure unless the case's final_status already shows a terminal error -- if final_status is PENDING_AT_LOG_END, that is explicitly NOT a failure.
5. Never speculate about fraud.
6. Never speculate about customer intent, motivation, or behavior.
7. Never name or blame an individual person. You may reference a TEAM from the routing section, never a person.
8. Never assert a correlation/link between trackers, transactions, or flows beyond what the given correlation section already states. Do not invent a new correlation.
9. Never contradict or restate-differently the given final_status, last_successful_stage, missing_next_stage, or correlation_status -- your job is to explain them, not change them.
10. If the correlation section shows is_conflict=true, your relevant statement MUST start with the exact phrase "CORRELATION CONFLICT" and explain the conflicting evidence given.
11. Never output a raw OTP, card number, or full contact detail -- none were given to you, so none should appear in your answer.
12. Use concise, professional enterprise support language. No speculation dressed as fact.

Respond with ONLY a JSON object of this exact shape, nothing else:
{"statements": [{"role": "...", "statement_type": "...", "text": "...", "evidence_event_ids": [...], "source_files": [...]}]}
"""


def _build_user_payload(case: InvestigationCase, similar_incident_flow_ids: List[str]) -> dict:
    return {
        "flow_id": case.flow_id,
        "case_summary_facts": {
            "transaction_id": case.case_summary.transaction_id,
            "merchant_name": case.case_summary.merchant_name,
            "amount": case.case_summary.amount,
            "currency": case.case_summary.currency,
            "issuer_id": case.case_summary.issuer_id,
            "authentication_method": case.case_summary.authentication_method,
            "final_status": case.case_summary.final_status,
            "last_successful_stage": case.case_summary.last_successful_stage,
            "missing_next_stage": case.case_summary.missing_next_stage,
        },
        "timeline": build_llm_safe_timeline(case.timeline),
        "findings": [f.model_dump() for f in case.findings],
        "correlation": case.correlation.model_dump(),
        "routing": [r.model_dump() for r in case.routing],
        "data_quality": case.data_quality.model_dump(),
        "similar_incident_flow_ids": similar_incident_flow_ids,
    }


def _known_event_ids(case: InvestigationCase) -> set:
    ids = {e.source_event_id for e in case.timeline if e.source_event_id}
    for f in case.findings:
        ids.update(f.evidence_event_ids)
    return ids


def _known_source_files(case: InvestigationCase) -> set:
    return {e.source_file for e in case.timeline if e.source_file}


def _violates_forbidden_content(text: str) -> Optional[str]:
    for pattern, reason in _FORBIDDEN_SUBSTRING_RULES:
        if pattern.search(text):
            return reason
    return None


def _violates_pending_as_failure(text: str, final_status: Optional[str]) -> bool:
    if final_status != "PENDING_AT_LOG_END":
        return False
    return bool(re.search(r"\bfail(ed|ure)?\b", text, re.IGNORECASE))


def _validate_statements(
    raw_statements: List[dict],
    case: InvestigationCase,
) -> List[NarrativeStatement]:
    """Never trusts the LLM's output at face value -- every statement is
    checked against the deterministic facts and either accepted as-is or
    discarded outright. Discarding (not attempting to "fix" the text) is
    deliberate: a statement that fabricated an event ID or made a
    forbidden claim can't be trusted to have gotten the rest right either."""
    known_event_ids = _known_event_ids(case)
    known_source_files = _known_source_files(case)
    validated: List[NarrativeStatement] = []

    for item in raw_statements:
        role = item.get("role")
        statement_type = item.get("statement_type")
        text = (item.get("text") or "").strip()
        evidence_event_ids = item.get("evidence_event_ids") or []
        source_files = item.get("source_files") or []

        if role not in _VALID_ROLES or statement_type not in _VALID_TYPES or not text:
            logger.warning("Discarding AI narrative statement with invalid role/type/empty text: %r", item)
            continue
        if not set(evidence_event_ids) <= known_event_ids:
            logger.warning("Discarding AI narrative statement citing unknown event id(s): %s", evidence_event_ids)
            continue
        if not set(source_files) <= known_source_files:
            logger.warning("Discarding AI narrative statement citing unknown source file(s): %s", source_files)
            continue

        violation = _violates_forbidden_content(text)
        if violation:
            logger.warning("Discarding AI narrative statement (%s): %r", violation, text)
            continue
        if _violates_pending_as_failure(text, case.case_summary.final_status):
            logger.warning("Discarding AI narrative statement calling PENDING_AT_LOG_END a failure: %r", text)
            continue
        if case.correlation.is_conflict and role in ("where_flow_stopped", "technical_boundary") and "CORRELATION CONFLICT" not in text:
            # Not a hard discard -- other roles don't need the phrase, and
            # the mandatory conflict statement is guaranteed separately in
            # explain_flow() regardless of what the model produced here.
            pass

        validated.append(
            NarrativeStatement(
                role=NarrativeRole(role),
                statement_type=StatementType(statement_type),
                text=text,
                evidence_event_ids=list(evidence_event_ids),
                source_files=list(source_files),
            )
        )

    return validated


def _mandatory_deterministic_statements(case: InvestigationCase) -> List[NarrativeStatement]:
    """Statements that must always be present regardless of what (or
    whether) the LLM produced anything -- generated directly from the
    deterministic engine's own output, never left to the LLM's discretion."""
    statements: List[NarrativeStatement] = []

    if case.correlation.is_conflict and case.correlation.conflict_statement:
        statements.append(
            NarrativeStatement(
                role=NarrativeRole.STOPPAGE,
                statement_type=StatementType.FACT,
                text=case.correlation.conflict_statement,
                evidence_event_ids=[],
                source_files=[],
            )
        )

    if case.case_summary.missing_next_stage:
        statements.append(
            NarrativeStatement(
                role=NarrativeRole.MISSING_EVIDENCE,
                statement_type=StatementType.MISSING_EVIDENCE,
                text=case.case_summary.missing_next_stage,
                evidence_event_ids=[],
                source_files=[],
            )
        )

    return statements


def _has_role(statements: List[NarrativeStatement], role: NarrativeRole) -> bool:
    return any(s.role == role for s in statements)


class InvestigationAssistant:
    def __init__(self, ollama_client: OllamaClient):
        self.client = ollama_client

    def explain_flow(
        self,
        case: InvestigationCase,
        similar_incident_flow_ids: Optional[List[str]] = None,
    ) -> Tuple[InvestigationCase, str]:
        """Returns (case_with_narrative, status_message). Always returns a
        usable case -- if Ollama is unavailable or returns something
        unusable, the deterministic fields are untouched and the narrative
        falls back to the mandatory deterministic statements only."""
        similar_incident_flow_ids = similar_incident_flow_ids or []
        is_ok, status = self.client.health_check()
        mandatory = _mandatory_deterministic_statements(case)

        if not is_ok:
            case.case_summary.narrative = mandatory
            case.ai_available = False
            case.ai_status_message = f"Ollama unavailable: {status}"
            return case, case.ai_status_message

        payload = _build_user_payload(case, similar_incident_flow_ids)
        prompt = f"Case file (JSON):\n{json.dumps(payload, default=str)}\n\nProduce the 9 narrative statements now."
        json_resp, err = self.client.generate_json(prompt, system_prompt=_SYSTEM_PROMPT)

        if err or not json_resp or not isinstance(json_resp.get("statements"), list):
            case.case_summary.narrative = mandatory
            case.ai_available = False
            case.ai_status_message = f"AI narrative generation failed: {err or 'malformed response'}"
            return case, case.ai_status_message

        validated = _validate_statements(json_resp["statements"], case)

        # Guarantee compliance with the hard rules regardless of what the
        # model actually produced -- never rely on prompt-following alone.
        for statement in mandatory:
            if not _has_role(validated, statement.role) or (
                statement.role == NarrativeRole.STOPPAGE and not any("CORRELATION CONFLICT" in s.text for s in validated)
            ):
                validated.append(statement)
        if not similar_incident_flow_ids and not _has_role(validated, NarrativeRole.SIMILAR_INCIDENTS):
            validated.append(
                NarrativeStatement(
                    role=NarrativeRole.SIMILAR_INCIDENTS,
                    statement_type=StatementType.MISSING_EVIDENCE,
                    text="No similar incidents found in the analyzed window.",
                )
            )

        case.case_summary.narrative = validated
        case.ai_available = True
        case.ai_status_message = "AI narrative generated and validated successfully."
        return case, case.ai_status_message
