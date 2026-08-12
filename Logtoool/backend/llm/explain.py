"""
AI Error Log Explanation Feature.
Provides root cause analysis, plain English explanation, and actionable remediation steps using local Ollama.
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

from backend.llm.client import OllamaClient

logger = logging.getLogger("logtool.llm.explain")


class LogExplainer:
    def __init__(self, ollama_client: OllamaClient):
        self.client = ollama_client

    def explain_log_event(
        self,
        event: Dict[str, Any],
        context_events: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, str]], str]:
        """
        Explains a log event given its surrounding context.
        Returns ({'probable_cause': ..., 'explanation': ..., 'suggested_next_steps': ...}, status_msg).
        """
        is_ok, status = self.client.health_check()
        if not is_ok:
            return None, f"Ollama unavailable: {status}"

        # Format context lines
        context_lines = []
        for ctx in context_events:
            marker = ">>> TARGET EVENT >>> " if ctx.get("is_target") else "    "
            context_lines.append(f"{marker}Line {ctx.get('line_no')}: [{ctx.get('level')}] {ctx.get('raw')}")

        context_str = "\n".join(context_lines)

        system_prompt = """You are a senior support engineer and SRE expert.
Analyze the targeted log event and surrounding log context.
Output JSON with these exact keys:
{
  "probable_cause": "Short summary of likely root cause",
  "explanation": "Clear, plain English explanation of what happened",
  "suggested_next_steps": "Actionable, numbered troubleshooting steps"
}
"""

        prompt = f"""Target Log Event:
Timestamp: {event.get('ts_utc')}
Level: {event.get('level')}
Source: {event.get('source_system')}
Component: {event.get('component')}
Message: {event.get('message')}

Surrounding Log Context:
{context_str}

Provide root cause explanation and remediation steps in JSON format."""

        json_resp, err = self.client.generate_json(prompt, system_prompt=system_prompt)

        if err or not json_resp:
            return None, f"AI Explanation generation failed: {err}"

        explanation_result = {
            "probable_cause": str(json_resp.get("probable_cause", "Analysis unavailable.")),
            "explanation": str(json_resp.get("explanation", "Could not generate detailed explanation.")),
            "suggested_next_steps": str(json_resp.get("suggested_next_steps", "Inspect application logs."))
        }

        return explanation_result, "AI explanation generated successfully."
