"""
Registry for custom parser plugins -- log formats too complex for the
declarative regex/json/delimited profile system (multi-branch parsing,
embedded XML extraction, function-call argument parsing, etc.).

Each module in backend/custom_parsers/ must expose:
    DISPLAY_NAME: str
    DEFAULT_SOURCE_SYSTEM: str
    detect(sample_text: str) -> bool
    parse_log_file(log_file_path, output_json_path=None) -> List[dict]

Deliberately static imports (not filesystem/importlib discovery) -- this is
a small, known set of customer-provided formats, and static imports fail
loudly at startup if a module is broken, rather than silently vanishing
from the registry.
"""
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.custom_parsers import (
    parser_ABCE_Credit,
    parser_ABCE_Debit,
    parser_AFS_Netcetera,
    parser_ASBB_Debit,
    parser_ASBB_MW_Credit,
)
from backend.core.schema import (
    CanonicalLogEvent,
    LogLevel,
    ParserProfile,
    ProfileType,
    TimestampConfidence,
    normalize_level,
)
from backend.core.timezones import parse_and_convert_timestamp

logger = logging.getLogger("logtool.custom_parsers")

_MODULES: Dict[str, Any] = {
    "abce_credit": parser_ABCE_Credit,
    "asbb_mw_credit": parser_ASBB_MW_Credit,
    "abce_debit": parser_ABCE_Debit,
    "asbb_debit": parser_ASBB_Debit,
    "afs_netcetera": parser_AFS_Netcetera,
}


def _virtual_profile(key: str, module: Any) -> ParserProfile:
    """A ParserProfile stand-in so custom parsers show up in profile
    listings/pickers alongside declarative ones. `pattern` is unused for
    type=CUSTOM but required by the schema, so it's set to a human-readable
    placeholder rather than left empty."""
    return ParserProfile(
        name=module.DISPLAY_NAME,
        type=ProfileType.CUSTOM,
        pattern=f"<custom Python parser: {key}>",
        default_source_system=module.DEFAULT_SOURCE_SYSTEM,
        custom_parser_module=key,
    )


def list_custom_profiles() -> List[ParserProfile]:
    return [_virtual_profile(key, mod) for key, mod in _MODULES.items()]


def get_custom_profile_by_name(name: str) -> Optional[ParserProfile]:
    for key, mod in _MODULES.items():
        if mod.DISPLAY_NAME == name:
            return _virtual_profile(key, mod)
    return None


def _score_module(module: Any, sample_text: str) -> float:
    """Runs a parser against the sample and returns the fraction of records
    that produced genuinely *structured* extraction (a non-empty `details`
    dict) -- used only to break ties when more than one module's detect()
    matches (see the Debit-family ambiguity note in parser_ABCE_Debit.py).

    Deliberately NOT scored by "does `action` have a value": every parser
    in this registry has a generic catch-all fallback branch that echoes
    the raw content back as `action` for ANY input, including input that
    belongs to a totally different format -- that made the old metric
    trivially satisfiable by the wrong parser (confirmed: ABCE_Credit's
    generic fallback scored 100% against an AFS/Netcetera-formatted
    sample, because "message content exists" is true regardless of
    format). Scoring by non-empty `details` only rewards a parser that
    actually recognized something format-specific (XML/JSON payload,
    function parameters, etc.), not just that a line existed.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as tf:
        tf.write(sample_text)
        tmp_path = tf.name
    try:
        records = module.parse_log_file(tmp_path)
        if not records:
            return 0.0
        successful = sum(1 for r in records if r.get("details"))
        return successful / len(records)
    except Exception:
        logger.exception("Custom parser %s failed while scoring detection sample", module.__name__)
        return 0.0
    finally:
        os.unlink(tmp_path)


def detect_custom_parser(sample_text: str) -> Tuple[Optional[ParserProfile], List[str]]:
    """Returns (profile_or_None, warnings). Tries every registered custom
    parser's detect() against the sample; if more than one matches, the
    highest-scoring one (by _score_module) wins and a warning is appended
    noting the ambiguity so the user can override manually if it guessed
    wrong."""
    candidates = [(key, mod) for key, mod in _MODULES.items() if mod.detect(sample_text)]
    if not candidates:
        return None, []

    if len(candidates) == 1:
        key, mod = candidates[0]
        return _virtual_profile(key, mod), []

    scored = [(key, mod, _score_module(mod, sample_text)) for key, mod in candidates]
    scored.sort(key=lambda x: -x[2])
    best_key, best_mod, best_score = scored[0]
    names = ", ".join(f"'{mod.DISPLAY_NAME}'" for _, mod, _ in scored)
    warning = (
        f"Multiple custom parsers matched this format ({names}) -- these formats look "
        f"identical from content alone. Selected '{best_mod.DISPLAY_NAME}' (best field-extraction "
        f"yield on the sample). If this is wrong, re-upload and force the correct profile manually."
    )
    return _virtual_profile(best_key, best_mod), [warning]


def _map_level(record: Dict[str, Any]) -> LogLevel:
    # ASBB_MW_Credit's parser extracts a real bracketed level -- trust it.
    if record.get("log_level"):
        return normalize_level(record["log_level"])
    # The other 3 parsers only produce a `log_type` *category*
    # (INFO/WARNING/XML_PAYLOAD/FUNCTION_INPUT/STEP_EVENT), not a real
    # severity. Only "WARNING" carries real severity signal; everything
    # else is a normal operational category, not evidence of an error, so
    # it maps to INFO rather than being marked UNKNOWN.
    if record.get("log_type") == "WARNING":
        return LogLevel.WARN
    return LogLevel.INFO


def run_custom_parser(
    profile: ParserProfile,
    file_path: str,
    batch_id: str,
    file_name: str,
    upload_time: datetime,
) -> List[CanonicalLogEvent]:
    module = _MODULES[profile.custom_parser_module]
    records = module.parse_log_file(file_path)

    events: List[CanonicalLogEvent] = []
    for line_no, record in enumerate(records, start=1):
        ts_utc, ts_confidence = parse_and_convert_timestamp(
            record.get("timestamp"), profile_tz_name=profile.timezone, upload_time=upload_time
        )
        details = record.get("details") or {}
        events.append(
            CanonicalLogEvent(
                ts_utc=ts_utc,
                ts_raw=str(record.get("timestamp") or ""),
                ts_confidence=ts_confidence,
                level=_map_level(record),
                source_system=profile.default_source_system,
                component=record.get("log_type"),
                message=record.get("action") or "(no action extracted)",
                raw=record.get("_raw_block", json.dumps(record)),
                attributes={"correlation_id": record.get("correlation_id"), "details": details},
                line_no=line_no,
                batch_id=batch_id,
                file_name=file_name,
            )
        )
    return events
