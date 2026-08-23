"""
User-Facing AI Analyst -- Phase 11 of the LLens Multi-Log Analysis
Strategy.

OLLAMA-ONLY BY DESIGN: this module talks to backend.llm.client.OllamaClient
exclusively. There is no cloud/external AI provider anywhere in this app --
the payment/transaction data this feature reasons over is sensitive and
must stay on the local network (see backend/api/routes/ai.py's /api/ai/config
docstring, which states the same policy for every other AI feature).

THE DETERMINISTIC ENGINE REMAINS AUTHORITATIVE. The LLM is used for exactly
two narrow steps, and neither is trusted blindly:

1. TOOL SELECTION (_select_tool): given the user's question and the fixed
   TOOL_SPECS list from backend/analysis/query_tools.py, the model picks
   ONE tool name + parameters. It cannot invent a tool, and any parameter
   name outside that tool's own spec is silently dropped (_sanitize_params).
   If the model names an unknown tool, that is treated as "no match" --
   never coerced to the nearest real tool.

2. NARRATION (_narrate): given the ALREADY-COMPUTED JSON result of the
   selected tool (never raw logs, never a hand-built query), the model
   phrases an answer. Every statement it writes is validated against the
   tool's own JSON output before being trusted -- see _validate_narration():
   cited event/flow ids must literally appear somewhere in the tool result,
   forbidden content (fraud, customer intent, "delivered", blame-a-person)
   is discarded, and when the tool result carries no evidence
   (query_tools.result_has_no_evidence), a MISSING_EVIDENCE statement with
   the exact phrase "Not found in the captured logs." is guaranteed
   regardless of what the model produced.

The model NEVER writes SQL, NEVER chooses which analysis function to run
beyond picking from the fixed list, and NEVER computes a metric itself --
every number in every answer was already computed by Phases 2-10.
"""
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from backend.analysis.ai_analyst_schema import AnalystAnswer, AnalystStatement, Confidence, EvidenceType
from backend.analysis.pipeline import AnalysisBundle
from backend.analysis.query_tools import TOOL_PARAM_ALLOWLIST, TOOL_REGISTRY, TOOL_SPECS, result_has_no_evidence
from backend.llm.client import OllamaClient

logger = logging.getLogger("logtool.llm.ai_analyst")

_VALID_EVIDENCE_TYPES = {t.value for t in EvidenceType}
_VALID_CONFIDENCE = {c.value for c in Confidence}

_NOT_FOUND_PHRASE = "Not found in the captured logs."

_FORBIDDEN_SUBSTRING_RULES = (
    (re.compile(r"\bfraud(ulent)?\b", re.IGNORECASE), "fraud inference is never permitted"),
    (
        re.compile(r"\bcustomer\s+(likely\s+)?(intend|wanted|tried\s+to|meant\s+to|was\s+trying)", re.IGNORECASE),
        "customer intent/motivation inference is never permitted",
    ),
    (re.compile(r"\bdelivered\b", re.IGNORECASE), "delivery cannot be claimed -- no DELIVERED state exists in the data model"),
    (re.compile(r"\b\d{6,}\b"), "a long digit sequence that could be a secret/PII value"),
)

_INT_PARAMS_BY_TOOL = {"top_failures": ("limit",), "recurring_incidents": ("min_affected_flows",)}

_UNSUPPORTED_MESSAGE = (
    "This question needs data or a computation LLens has not built as a deterministic capability. "
    "Supported question types: exact-identifier transaction lookup, dependency health, queue handoff "
    "issues, top/recurring failures, correlation quality, issuer failure rates, and status-filtered "
    "transaction lists."
)

_TOOL_SELECTION_SYSTEM_PROMPT = f"""You are a tool-selection component for LLens, a payment-log analysis system.
You NEVER access raw logs, write SQL, or invent a metric yourself. Your ONLY job is to pick the ONE
deterministic tool from the fixed list below that can answer the user's question, and extract its
parameters from the question text.

Available tools (JSON):
{json.dumps(TOOL_SPECS, indent=2)}

Rules:
- Respond with ONLY a JSON object: {{"tool": "<exact tool name from the list above>", "parameters": {{...}}}}
- If NO tool in the list can answer the question, respond with EXACTLY:
  {{"tool": null, "reason": "<one sentence on what data/capability is missing>"}}
- Never invent a tool name that is not in the list above.
- Never invent a parameter name that is not in that tool's own "parameters" object.
- Extract parameter VALUES only from the user's question text -- never guess a value the question did not mention.
"""

_NARRATION_SYSTEM_PROMPT = """You are a senior payment-systems support analyst answering an investigator's
question using ONLY the structured JSON tool result you are given below. Every number, id, and status in
it was already computed by a rule-based engine before you were called -- you were not given raw logs and
must not claim to have analyzed "the logs" directly.

Respond with ONLY a JSON object of this exact shape:
{
  "answer": "...",
  "evidence": [
    {"evidence_type": "observed_fact"|"calculated_metric"|"inferred_interpretation"|"missing_evidence",
     "text": "...", "evidence_event_ids": [...], "flow_ids": [...]}
  ],
  "confidence": "HIGH"|"MEDIUM"|"LOW",
  "recommended_investigation_area": "..." or null
}

How to classify evidence_type -- this is the most important field, get it right:
- "observed_fact": a value taken directly from the tool result (a status, an id, a timestamp) -- not computed.
- "calculated_metric": a count, rate, average, or latency number that is already IN the tool result (you are
  restating an already-computed number, not computing one).
- "inferred_interpretation": your own reasonable reading of the facts/metrics above. The text MUST be
  explicitly hedged (e.g. "this suggests...", "this may indicate..."). NEVER state an interpretation as if
  it were a fact or a metric.
- "missing_evidence": something the question asked about that is absent from the tool result.

ABSOLUTE RULES -- any statement violating these will be discarded:
1. Use ONLY event ids, flow ids, and values that literally appear in the tool result JSON below. Never invent one.
2. When something is absent, the statement text must say EXACTLY: "Not found in the captured logs." Use no other phrasing for absence.
3. Absence of evidence is never proof of failure.
4. Never claim an SMS/OTP/email was delivered to a customer from a queue-confirmation event alone.
5. Never call a PENDING_AT_LOG_END status a technical failure.
6. Never speculate about fraud.
7. Never speculate about customer intent, motivation, or behavior.
8. Never name or blame an individual person -- reference only a team/routing area.
9. Never assert a correlation/link beyond what the tool result already states.
10. Never output a raw OTP, card number, or full contact detail.
11. Never present an inferred_interpretation as an observed_fact or calculated_metric.
12. Use concise, professional enterprise support language.
"""


def _flatten_strings(obj: Any) -> set:
    """Every leaf string value anywhere in the tool's own JSON output --
    used as the allow-list for evidence_event_ids/flow_ids the narration
    step is permitted to cite. A cited id that never appears anywhere in
    the tool result was fabricated and is discarded."""
    found: set = set()
    if isinstance(obj, dict):
        for v in obj.values():
            found |= _flatten_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _flatten_strings(v)
    elif isinstance(obj, str):
        found.add(obj)
    return found


def _violates_forbidden_content(text: str) -> Optional[str]:
    for pattern, reason in _FORBIDDEN_SUBSTRING_RULES:
        if pattern.search(text):
            return reason
    return None


def _sanitize_params(tool_name: str, raw_params: Optional[dict]) -> dict:
    allowed = TOOL_PARAM_ALLOWLIST.get(tool_name, set())
    clean = {k: v for k, v in (raw_params or {}).items() if k in allowed}
    for int_key in _INT_PARAMS_BY_TOOL.get(tool_name, ()):
        if int_key in clean:
            try:
                clean[int_key] = int(clean[int_key])
            except (TypeError, ValueError):
                clean.pop(int_key)
    return clean


def _unavailable_answer(question: str, status_message: str) -> AnalystAnswer:
    return AnalystAnswer(
        question=question,
        answer="The AI Analyst is currently unavailable.",
        confidence=Confidence.LOW,
        unsupported=True,
        limitation_explanation=f"Ollama unavailable: {status_message}",
        ai_available=False,
        ai_status_message=f"Ollama unavailable: {status_message}",
    )


def _unsupported_answer(question: str, reason: str, ai_available: bool, status_message: str) -> AnalystAnswer:
    return AnalystAnswer(
        question=question,
        answer=_UNSUPPORTED_MESSAGE,
        evidence=[
            AnalystStatement(evidence_type=EvidenceType.MISSING_EVIDENCE, text=_NOT_FOUND_PHRASE),
        ],
        confidence=Confidence.LOW,
        unsupported=True,
        limitation_explanation=reason,
        ai_available=ai_available,
        ai_status_message=status_message,
    )


class AIAnalystAssistant:
    def __init__(self, ollama_client: OllamaClient):
        self.client = ollama_client

    def ask(self, question: str, bundle: AnalysisBundle) -> AnalystAnswer:
        is_ok, status_msg = self.client.health_check()
        if not is_ok:
            return _unavailable_answer(question, status_msg)

        tool_name, params, select_err = self._select_tool(question)
        if not tool_name:
            return _unsupported_answer(
                question,
                select_err or "No supported tool matches this question.",
                ai_available=True,
                status_message="Question routed to no supported tool.",
            )

        tool_result, call_err = self._call_tool_safely(tool_name, params, bundle)
        if call_err:
            return _unsupported_answer(
                question, call_err, ai_available=True, status_message="Tool invocation failed validation."
            )

        answer = self._narrate(question, tool_name, params, tool_result)
        return answer

    def _select_tool(self, question: str) -> Tuple[Optional[str], dict, Optional[str]]:
        prompt = f'User question: "{question}"\n\nSelect the tool now.'
        json_resp, err = self.client.generate_json(prompt, system_prompt=_TOOL_SELECTION_SYSTEM_PROMPT)
        if err or not isinstance(json_resp, dict):
            return None, {}, f"AI tool-selection failed: {err or 'malformed response'}"

        tool_name = json_resp.get("tool")
        if tool_name is None:
            return None, {}, json_resp.get("reason") or "No supported tool matches this question."
        if tool_name not in TOOL_REGISTRY:
            logger.warning("AI Analyst selected an unknown tool %r -- treating as unsupported.", tool_name)
            return None, {}, "No supported tool matches this question."

        params = _sanitize_params(tool_name, json_resp.get("parameters"))
        return tool_name, params, None

    def _call_tool_safely(self, tool_name: str, params: dict, bundle: AnalysisBundle) -> Tuple[Optional[dict], Optional[str]]:
        fn = TOOL_REGISTRY[tool_name]
        try:
            return fn(bundle, **params), None
        except TypeError as e:
            logger.warning("AI Analyst tool call %s(%r) failed parameter validation: %s", tool_name, params, e)
            return None, f"Tool parameter mismatch for '{tool_name}': {e}"

    def _narrate(self, question: str, tool_name: str, params: dict, tool_result: dict) -> AnalystAnswer:
        prompt = (
            f"Tool used: {tool_name}\n"
            f"Tool result (JSON):\n{json.dumps(tool_result, default=str)}\n\n"
            f'Question: "{question}"\n\nProduce the answer now.'
        )
        json_resp, err = self.client.generate_json(prompt, system_prompt=_NARRATION_SYSTEM_PROMPT)

        no_evidence = result_has_no_evidence(tool_name, tool_result)

        if err or not isinstance(json_resp, dict):
            return self._fallback_answer(question, tool_name, params, tool_result, no_evidence, f"AI narration failed: {err or 'malformed response'}")

        evidence = self._validate_evidence(json_resp.get("evidence") or [], tool_result)
        answer_text = (json_resp.get("answer") or "").strip() or self._fallback_answer_text(tool_result, no_evidence)
        confidence = json_resp.get("confidence")
        confidence = Confidence(confidence) if confidence in _VALID_CONFIDENCE else Confidence.MEDIUM
        recommended_area = json_resp.get("recommended_investigation_area") or None

        if no_evidence:
            confidence = Confidence.LOW
            if not any(e.evidence_type == EvidenceType.MISSING_EVIDENCE and _NOT_FOUND_PHRASE in e.text for e in evidence):
                evidence.append(AnalystStatement(evidence_type=EvidenceType.MISSING_EVIDENCE, text=_NOT_FOUND_PHRASE))

        if tool_name == "correlation_quality" and tool_result.get("conflict_count", 0) > 0:
            if not any("CORRELATION CONFLICT" in e.text for e in evidence):
                evidence.append(
                    AnalystStatement(
                        evidence_type=EvidenceType.OBSERVED_FACT,
                        text=f"CORRELATION CONFLICT: {tool_result['conflict_count']} conflicting identifier(s) found in the analyzed window.",
                    )
                )

        relevant_flow_ids = sorted({fid for e in evidence for fid in e.flow_ids})

        return AnalystAnswer(
            question=question,
            answer=answer_text,
            evidence=evidence,
            relevant_flow_ids=relevant_flow_ids,
            metrics=tool_result,
            confidence=confidence,
            recommended_investigation_area=recommended_area,
            tool_used=tool_name,
            tool_parameters=params,
            unsupported=False,
            ai_available=True,
            ai_status_message="AI answer generated and validated successfully.",
        )

    def _validate_evidence(self, raw_items, tool_result: dict):
        known_strings = _flatten_strings(tool_result)
        validated = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            evidence_type = item.get("evidence_type")
            text = (item.get("text") or "").strip()
            event_ids = item.get("evidence_event_ids") or []
            flow_ids = item.get("flow_ids") or []

            if evidence_type not in _VALID_EVIDENCE_TYPES or not text:
                logger.warning("Discarding AI Analyst evidence with invalid type/empty text: %r", item)
                continue
            if not set(event_ids) <= known_strings:
                logger.warning("Discarding AI Analyst evidence citing unknown event id(s): %s", event_ids)
                continue
            if not set(flow_ids) <= known_strings:
                logger.warning("Discarding AI Analyst evidence citing unknown flow id(s): %s", flow_ids)
                continue
            violation = _violates_forbidden_content(text)
            if violation:
                logger.warning("Discarding AI Analyst evidence (%s): %r", violation, text)
                continue

            validated.append(
                AnalystStatement(
                    evidence_type=EvidenceType(evidence_type),
                    text=text,
                    evidence_event_ids=list(event_ids),
                    flow_ids=list(flow_ids),
                )
            )
        return validated

    def _fallback_answer_text(self, tool_result: dict, no_evidence: bool) -> str:
        if no_evidence:
            return _NOT_FOUND_PHRASE
        return "See the metrics below for the deterministic result."

    def _fallback_answer(
        self, question: str, tool_name: str, params: dict, tool_result: dict, no_evidence: bool, status_message: str
    ) -> AnalystAnswer:
        """Used when the LLM's narration call itself fails or returns
        something unusable -- the deterministic tool result is still
        returned in full (metrics), just without an AI-written narrative."""
        evidence = [AnalystStatement(evidence_type=EvidenceType.MISSING_EVIDENCE, text=_NOT_FOUND_PHRASE)] if no_evidence else []
        return AnalystAnswer(
            question=question,
            answer=self._fallback_answer_text(tool_result, no_evidence),
            evidence=evidence,
            metrics=tool_result,
            confidence=Confidence.LOW,
            tool_used=tool_name,
            tool_parameters=params,
            unsupported=False,
            ai_available=False,
            ai_status_message=status_message,
        )
