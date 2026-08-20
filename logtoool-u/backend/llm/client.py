"""
Local Ollama API Client.
Provides synchronous interface for local LLM inference with temperature=0 and JSON constraints.
Gracefully handles offline/unavailable Ollama instances.
"""

import json
import requests
from typing import Any, Dict, Optional, Tuple
import logging

logger = logging.getLogger("logtool.llm.client")


class OllamaClient:
    """
    Client for interacting with local Ollama model instance.
    """
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:8b",
        timeout_seconds: float = 120.0
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def health_check(self) -> Tuple[bool, str]:
        """
        Checks if local Ollama server is running and accessible.
        Returns (is_available, status_message).
        """
        try:
            resp = requests.get(f"{self.base_url}/api/version", timeout=3.0)
            if resp.status_code == 200:
                ver = resp.json().get("version", "unknown")
                return True, f"Ollama operational (v{ver}, model: {self.model})"
            return False, f"Ollama returned HTTP status {resp.status_code}"
        except Exception as e:
            return False, f"Ollama unavailable at {self.base_url}: {str(e)}"

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Sends generation request to Ollama with temperature=0.
        Returns (response_text, error_message).
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.0,
            }
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout_seconds
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("response", ""), None
            return None, f"Ollama API returned status {resp.status_code}: {resp.text}"
        except Exception as e:
            logger.warning(f"Ollama request failed: {e}")
            return None, f"Failed connecting to Ollama: {str(e)}"

    def generate_json(self, prompt: str, system_prompt: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Sends request to Ollama enforcing format='json' and parses result.
        Retries once if JSON parsing fails.
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.0,
            }
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout_seconds
            )
            if resp.status_code == 200:
                text_response = resp.json().get("response", "")
                try:
                    data = json.loads(text_response)
                    return data, None
                except json.JSONDecodeError:
                    # Retry once with explicit extraction
                    logger.debug("First JSON decode failed, attempting cleanup...")
                    cleaned = self._extract_json_substring(text_response)
                    if cleaned:
                        try:
                            return json.loads(cleaned), None
                        except json.JSONDecodeError as e:
                            return None, f"JSON decode error: {e}"
                    return None, "Failed to parse JSON from Ollama response"
            return None, f"Ollama HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return None, f"Ollama connection error: {str(e)}"

    def _extract_json_substring(self, text: str) -> Optional[str]:
        """Extracts JSON object from markdown code blocks or text."""
        if "```json" in text:
            parts = text.split("```json")
            if len(parts) > 1:
                return parts[1].split("```")[0].strip()
        elif "```" in text:
            parts = text.split("```")
            if len(parts) > 1:
                return parts[1].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end+1]
        return None
