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
    parser_Cardinal,
    parser_Debit_Transaction,
    parser_ILA_Bank,
    parser_OTP_Processor,
    parser_VFlex,
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
    "otp_processor": parser_OTP_Processor,
    "debit_portal_log": parser_Debit_Transaction,
    "cardinal": parser_Cardinal,
    "vflex": parser_VFlex,
    # Registered last: its detect() is the broadest here (any ISO-8601 +
    # bracketed-level header), so on the ambiguous samples it deliberately
    # does claim, being last means the field-yield tie-break decides on
    # merit rather than it winning on dict-insertion order.
    "ila_bank": parser_ILA_Bank,
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


def _count_filled_leaves(value: Any) -> int:
    """Recursively counts non-empty scalar leaves inside a `details` value.
    Used by _score_module to measure how much a parser actually decoded,
    as opposed to just returning a dict with the right shape."""
    if isinstance(value, dict):
        return sum(_count_filled_leaves(v) for v in value.values())
    if isinstance(value, list):
        return sum(_count_filled_leaves(v) for v in value)
    return 0 if value in (None, "", [], {}) else 1


def _score_module(module: Any, sample_text: str) -> float:
    """Runs a parser against the sample and scores how well it actually
    understood the format -- used only to break ties when more than one
    module's detect() matches (see the Debit-family ambiguity note in
    parser_ABCE_Debit.py).

    Two prior metrics were tried and rejected before this one:

    - "does `action` have a value": every parser in this registry has a
      generic catch-all fallback branch that echoes the raw content back
      as `action` for ANY input, including input that belongs to a
      totally different format -- trivially satisfiable by the wrong
      parser regardless of format.
    - "is `details` non-empty": every parser's flat-record adapter always
      builds `details` as a dict with several fixed keys (tracker_no,
      event_type, etc.), so the dict itself is virtually never empty even
      when every one of those keys holds None -- this saturates at 1.0
      for almost any competent parser and effectively falls back to
      dict-insertion-order in _MODULES for the tie-break (confirmed: on a
      real Cardinal-branded sample, this metric scored the AFS/Netcetera
      parser and the actual Cardinal parser identically at 1.0, and
      AFS/Netcetera won only because it's registered earlier).

    This metric instead combines two signals, both computed from the
    flat-record shape run_custom_parser() relies on (see module docstring):

    - Correlation coverage: fraction of records with a non-null
      `correlation_id`. A parser that can't correlate a record to its
      transaction/tracker either doesn't understand the format or is
      losing continuation lines (e.g. a multi-line stack trace it failed
      to attach to its parent event) -- confirmed on a real
      Debit-Netcetera error log, where AFS/Netcetera's line-by-line
      matching orphaned the exception/stack-trace lines as unrelated
      "unparsed" records (correlation_id=None) while the correct
      Debit-portal parser kept the whole block together.
    - Filled-field density: average count of non-empty leaf values inside
      `details` per record, saturated via x/(1+x) so it stays bounded
      without an arbitrary normalization constant, and uses an absolute
      count (not a ratio of filled/total) so a parser with a
      deliberately-comprehensive schema (many legitimately-null optional
      fields for a given event type) isn't penalized relative to a
      parser with a small always-full schema.

    Correlation coverage gates the score multiplicatively: a parser that
    fails to correlate half its records gets its density score halved
    too, regardless of how rich the fields it did fill in are.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as tf:
        tf.write(sample_text)
        tmp_path = tf.name
    try:
        records = module.parse_log_file(tmp_path)
        if not records:
            return 0.0
        correlation_score = sum(1 for r in records if r.get("correlation_id")) / len(records)
        avg_filled_leaves = sum(_count_filled_leaves(r.get("details")) for r in records) / len(records)
        density_score = avg_filled_leaves / (1 + avg_filled_leaves)
        return correlation_score * density_score
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
