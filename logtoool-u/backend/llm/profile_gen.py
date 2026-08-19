"""
LLM Parser Profile Generator with Repair Loop.
Samples file lines (first 20, middle 20, last 10) and uses Ollama to synthesize valid Parser Profile JSON.
Validates regex, timestamp parsing, and match ratio; retries with error feedback up to 2 times.
"""

import random
import re
from typing import List, Optional, Tuple
import logging

from backend.core.schema import ParserProfile, ProfileType
from backend.core.parse import group_multiline_logs, evaluate_profile_match
from backend.llm.client import OllamaClient

logger = logging.getLogger("logtool.llm.profile_gen")


def extract_sample_lines(all_lines: List[str]) -> List[str]:
    """
    Extracts exactly:
    - First 20 lines
    - 20 random middle lines
    - Last 10 lines
    Never reads/sends full file to Ollama.
    """
    total = len(all_lines)
    if total <= 50:
        return all_lines

    first_20 = all_lines[:20]
    last_10 = all_lines[-10:]

    middle_pool = all_lines[20:-10]
    middle_count = min(20, len(middle_pool))
    middle_20 = random.sample(middle_pool, middle_count) if middle_pool else []

    return first_20 + middle_20 + last_10


class LLMProfileGenerator:
    def __init__(self, ollama_client: OllamaClient):
        self.client = ollama_client

    def generate_profile(
        self,
        sample_lines: List[str],
        suggested_name: str = "Auto-Generated Profile"
    ) -> Tuple[Optional[ParserProfile], str]:
        """
        Generates and validates a ParserProfile from sample lines with repair loop.
        Returns (profile, status_message).
        """
        is_ok, status = self.client.health_check()
        if not is_ok:
            return None, f"Ollama is unavailable: {status}"

        # Group sample lines for testing
        indexed_lines = list(enumerate(sample_lines, start=1))
        grouped_sample = group_multiline_logs(indexed_lines)

        sample_str = "\n".join(sample_lines[:25])

        system_prompt = """You are an expert log parsing AI assistant.
Analyze the provided log sample and output a valid JSON parser profile schema.
Output ONLY JSON in this exact structure:
{
  "name": "Vendor Log Parser",
  "type": "regex",  // "regex", "json", or "delimited"
  "pattern": "^(?P<timestamp>\\\\d{4}-\\\\d{2}-\\\\d{2} [\\\\d:]+) \\\\[(?P<level>\\\\w+)\\\\] (?P<message>.*)$",
  "timestamp_field": "timestamp",
  "level_field": "level",
  "component_field": "component",
  "message_field": "message",
  "default_source_system": "auto_vendor",
  "timezone": "UTC",
  "min_match_ratio": 0.8
}
Rules:
- For 'regex', pattern must contain named capture groups like (?P<timestamp>...) and (?P<message>...).
- For 'json', set pattern to 'root'.
- For 'delimited', pattern must be delimiter char like ',' or '\\t'.
"""

        prompt = f"Analyze these sample log lines:\n\n{sample_str}\n\nGenerate parser profile JSON."

        last_error = ""

        # Repair loop: Up to 2 retries
        for attempt in range(1, 4):
            current_prompt = prompt
            if attempt > 1:
                current_prompt += f"\n\nPrevious attempt failed validation with error:\n{last_error}\nPlease fix the pattern or field names and produce a corrected JSON profile."

            logger.info(f"Ollama profile generation attempt {attempt}/3...")
            json_resp, err = self.client.generate_json(current_prompt, system_prompt=system_prompt)

            if err or not json_resp:
                last_error = f"Ollama generation error: {err}"
                continue

            # Step 1: Schema validation
            try:
                profile_data = json_resp
                profile_data["name"] = suggested_name
                profile = ParserProfile(**profile_data)
            except Exception as val_err:
                last_error = f"Pydantic schema validation error: {val_err}"
                continue

            # Step 2: Regex compilation test
            if profile.type == ProfileType.REGEX:
                try:
                    re.compile(profile.pattern)
                except Exception as re_err:
                    last_error = f"Regex compilation failed: {re_err}"
                    continue

            # Step 3: Match ratio evaluation
            match_ratio, _ = evaluate_profile_match(grouped_sample, profile)
            if match_ratio >= profile.min_match_ratio:
                logger.info(f"Successfully generated profile '{profile.name}' with match ratio {match_ratio:.2f}")
                return profile, f"Successfully generated profile on attempt {attempt} (match ratio: {match_ratio:.2f})"
            else:
                last_error = f"Match ratio too low: achieved {match_ratio:.2f}, required >= {profile.min_match_ratio}"

        return None, f"Profile generation failed after 3 attempts. Last error: {last_error}"
