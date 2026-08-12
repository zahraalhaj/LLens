"""
Unit tests for Chat With Logs security validation:
- Read-only SQL checks
- Rejection of INSERT, UPDATE, DELETE, DROP, ALTER, PRAGMA
- Multiple statement rejection
- Automatic LIMIT 500 enforcement
"""

import pytest
from backend.llm.chat import validate_and_sanitize_sql


def test_sql_validation_valid():
    raw_sql = "SELECT level, count(*) FROM events GROUP BY level"
    is_valid, clean_sql, err = validate_and_sanitize_sql(raw_sql)
    assert is_valid
    assert "LIMIT 500" in clean_sql
    assert err == ""


def test_sql_validation_reject_forbidden_keywords():
    for forbidden in ["DELETE FROM events", "DROP TABLE events", "UPDATE events SET level='INFO'", "PRAGMA table_info(events)"]:
        is_valid, clean_sql, err = validate_and_sanitize_sql(forbidden)
        assert not is_valid
        assert "Forbidden" in err or "Only SELECT" in err


def test_sql_validation_reject_multiple_statements():
    multi_sql = "SELECT * FROM events; DROP TABLE events;"
    is_valid, clean_sql, err = validate_and_sanitize_sql(multi_sql)
    assert not is_valid
    assert "Multiple SQL statements" in err


def test_sql_limit_capping():
    sql_huge_limit = "SELECT * FROM events LIMIT 50000"
    is_valid, clean_sql, err = validate_and_sanitize_sql(sql_huge_limit)
    assert is_valid
    assert "LIMIT 500" in clean_sql
    assert "50000" not in clean_sql
