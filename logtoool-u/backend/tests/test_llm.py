"""
Unit tests for LLM Ollama integration, profile generation, repair loop, and explain feature.
Mocks Ollama HTTP API so live server is not required during CI.
"""

import pytest
from unittest.mock import MagicMock
from backend.llm.client import OllamaClient
from backend.llm.profile_gen import LLMProfileGenerator, extract_sample_lines
from backend.llm.explain import LogExplainer


def test_extract_sample_lines():
    all_lines = [f"Line {i}" for i in range(1, 101)]
    samples = extract_sample_lines(all_lines)
    # Should include first 20, middle 20, last 10
    assert len(samples) == 50
    assert samples[0] == "Line 1"
    assert samples[19] == "Line 20"
    assert samples[-1] == "Line 100"


def test_profile_generator_repair_loop(mocker):
    mock_client = mocker.MagicMock(spec=OllamaClient)
    mock_client.health_check.return_value = (True, "OK")

    # Attempt 1: Invalid JSON schema -> Attempt 2: Valid generated profile
    valid_profile_json = {
        "name": "Auto Profile",
        "type": "regex",
        "pattern": r'^(?P<timestamp>\d{4}-\d{2}-\d{2}) \[(?P<level>\w+)\] (?P<message>.*)$',
        "timestamp_field": "timestamp",
        "level_field": "level",
        "message_field": "message",
        "min_match_ratio": 0.5
    }

    mock_client.generate_json.side_effect = [
        (None, "Invalid JSON"),
        (valid_profile_json, None)
    ]

    generator = LLMProfileGenerator(mock_client)
    sample_lines = ["2026-08-05 [INFO] Hello World"]

    profile, msg = generator.generate_profile(sample_lines, "Auto Profile")
    assert profile is not None
    assert profile.name == "Auto Profile"
    assert "attempt 2" in msg


def test_log_explainer(mocker):
    mock_client = mocker.MagicMock(spec=OllamaClient)
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate_json.return_value = ({
        "probable_cause": "Out of memory error",
        "explanation": "Process killed by OOM killer",
        "suggested_next_steps": "Increase RAM allocation"
    }, None)

    explainer = LogExplainer(mock_client)
    evt = {"ts_utc": "2026-08-05T10:00:00Z", "level": "CRITICAL", "message": "OOM"}
    ctx = [{"line_no": 1, "level": "CRITICAL", "raw": "OOM", "is_target": True}]

    res, status = explainer.explain_log_event(evt, ctx)
    assert res is not None
    assert res["probable_cause"] == "Out of memory error"
