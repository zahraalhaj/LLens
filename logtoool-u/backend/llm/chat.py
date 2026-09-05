"""
Natural Language to SQL Assistant ("Chat With Logs").
Converts natural language questions into safe SQL SELECT queries, validates execution, and summarizes results.
Enforces strict read-only SQL security constraints.
"""

import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
import logging

from backend.core.store import DatabaseManager
from backend.llm.client import OllamaClient
from backend.llm.validation_metrics import (
    LLMValidationMetrics,
    RejectionReason,
    Surface,
    ValidationOutcome,
)

logger = logging.getLogger("logtool.llm.chat")

FORBIDDEN_SQL_KEYWORDS = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b", r"\bALTER\b",
    r"\bATTACH\b", r"\bDETACH\b", r"\bPRAGMA\b", r"\bVACUUM\b", r"\bCREATE\b",
    r"\bREINDEX\b", r"\bTRANSACTION\b"
]


def validate_and_sanitize_sql(raw_sql: str) -> Tuple[bool, str, str]:
    """
    Validates that query is purely a single read-only SELECT statement.
    Enforces LIMIT 500.
    Returns (is_valid, sanitized_sql, error_message).
    """
    cleaned = raw_sql.strip()

    # Remove markdown code block fences if present
    if "```sql" in cleaned:
        cleaned = cleaned.split("```sql")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].strip()

    # Reject empty query
    if not cleaned:
        return False, "", "Generated SQL was empty."

    # Reject multiple statements
    if ";" in cleaned.rstrip(";"):
        return False, "", "Multiple SQL statements are strictly forbidden."

    cleaned = cleaned.rstrip(";")

    # Reject non-SELECT queries
    if not cleaned.upper().startswith("SELECT") and not cleaned.upper().startswith("WITH"):
        return False, "", "Only SELECT or WITH queries are permitted."

    # Reject forbidden state-altering keywords
    for pattern in FORBIDDEN_SQL_KEYWORDS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return False, "", f"Forbidden SQL keyword detected matching '{pattern}'."

    # Enforce LIMIT 500
    if not re.search(r"\bLIMIT\s+\d+", cleaned, re.IGNORECASE):
        cleaned = f"{cleaned} LIMIT 500"
    else:
        # Cap limit at 500
        match = re.search(r"\bLIMIT\s+(\d+)", cleaned, re.IGNORECASE)
        if match:
            limit_val = int(match.group(1))
            if limit_val > 500:
                cleaned = re.sub(r"\bLIMIT\s+\d+", "LIMIT 500", cleaned, flags=re.IGNORECASE)

    return True, cleaned, ""


class LogChatAssistant:
    def __init__(
        self,
        db_manager: DatabaseManager,
        ollama_client: OllamaClient,
        validation_metrics: Optional[LLMValidationMetrics] = None,
    ):
        self.db_manager = db_manager
        self.client = ollama_client
        # Optional -- see AIAnalystAssistant.__init__ for the reasoning.
        self.validation_metrics = validation_metrics

    def _record_validation(self, outcome: ValidationOutcome) -> None:
        if self.validation_metrics is not None:
            self.validation_metrics.record(outcome, model_name=f"ollama:{self.client.model}")

    def answer_natural_language_query(
        self,
        user_question: str
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]], Optional[str], str]:
        """
        Executes Chat With Logs workflow:
        1. NL -> SQL
        2. Validate & Sanitize SQL
        3. Execute query read-only
        4. Summarize results
        Returns (generated_sql, raw_results, ai_summary, status_msg).
        """
        is_ok, status = self.client.health_check()
        if not is_ok:
            return None, None, None, f"Ollama unavailable: {status}"

        # Schema context for Ollama
        schema_prompt = """You are a SQLite data analyst assistant.
Convert the user's natural language question into a valid SQLite SELECT query.
Table 'events' schema:
- event_id TEXT
- batch_id TEXT
- file_name TEXT
- line_no INTEGER
- ts_utc TEXT (ISO-8601 UTC timestamp e.g. '2026-08-05T20:17:33Z')
- ts_confidence TEXT ('parsed', 'assumed_utc', 'assumed_local', 'unparseable')
- level TEXT ('DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL', 'UNKNOWN')
- source_system TEXT
- component TEXT
- message TEXT
- raw TEXT

Table 'batches' schema:
- batch_id TEXT
- file_name TEXT
- total_events INTEGER
- matched_profile TEXT
- uploaded_at TEXT

RULES:
- Return ONLY the raw SQL query string inside ```sql ... ``` code fence.
- Use ONLY SELECT statements.
- Never write INSERT, UPDATE, DELETE, or DROP.
"""

        prompt = f"User Question: {user_question}\nGenerate SQL query."

        raw_llm_out, err = self.client.generate(prompt, system_prompt=schema_prompt)
        if err or not raw_llm_out:
            return None, None, None, f"Failed generating SQL from AI: {err}"

        # Validate SQL
        is_valid, clean_sql, val_err = validate_and_sanitize_sql(raw_llm_out)
        if not is_valid:
            # The model wrote SQL the safety gate refused. Counted, not just
            # returned as an error string, because a rising rate here is the
            # model drifting toward unsafe queries -- worth seeing as a trend.
            outcome = ValidationOutcome(surface=Surface.CHAT_SQL)
            outcome.reject_response(RejectionReason.SQL_FAILED_SECURITY_VALIDATION, val_err)
            self._record_validation(outcome)
            return None, None, None, f"Generated SQL failed security validation: {val_err}"

        accepted = ValidationOutcome(surface=Surface.CHAT_SQL)
        accepted.accept()
        self._record_validation(accepted)

        # Execute Query Read-Only
        raw_results: List[Dict[str, Any]] = []
        try:
            conn = sqlite3.connect(f"file:{self.db_manager.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(clean_sql)
            rows = cursor.fetchall()
            raw_results = [dict(row) for row in rows]
            conn.close()
        except Exception as exec_err:
            return clean_sql, None, None, f"SQL Execution error: {exec_err}"

        # Step 4: AI Summary of query results
        summary_prompt = f"""User asked: "{user_question}"
Executed SQL: {clean_sql}
Returned {len(raw_results)} rows.
Sample Results (up to 10 rows):
{raw_results[:10]}

Provide a concise, executive summary of these query results in 2-3 bullet points."""

        ai_summary, sum_err = self.client.generate(summary_prompt)

        return clean_sql, raw_results, (ai_summary if ai_summary else "No summary generated."), "Query executed successfully."
