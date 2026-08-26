"""
Multiline Grouping and Parser Evaluation Engine.
Performs deterministic multiline grouping, profile detection scoring, and log normalization.
"""

import json
import re
import csv
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple
import logging
from datetime import datetime

from backend.core.schema import (
    CanonicalLogEvent,
    LogLevel,
    ParserProfile,
    ProfileType,
    TimestampConfidence,
    normalize_level,
)
from backend.core.timezones import parse_and_convert_timestamp

logger = logging.getLogger("logtool.parse")

# Heuristic timestamp detection regexes for multiline start detection
TIMESTAMP_PREFIX_REGEXES = [
    re.compile(r'^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}'),  # ISO-8601: 2026-08-05 20:17:33
    re.compile(r'^[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}'),    # Syslog: Aug  5 20:17:33
    re.compile(r'^\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}'),     # Apache: 05/Aug/2026:20:17:33
    re.compile(r'^\d{10}(?:\.\d+)?'),                         # Epoch timestamp
    re.compile(r'^\{.*"timestamp".*\}', re.DOTALL),          # JSON line
]


class GroupedLineEvent:
    """Represents a grouped log event consisting of a header line and optional continuation lines."""
    def __init__(self, start_line_no: int, header_line: str):
        self.start_line_no = start_line_no
        self.header_line = header_line
        self.continuation_lines: List[str] = []

    @property
    def full_raw(self) -> str:
        if not self.continuation_lines:
            return self.header_line
        return self.header_line + "\n" + "\n".join(self.continuation_lines)


def group_multiline_logs(lines: List[Tuple[int, str]], profile: Optional[ParserProfile] = None) -> List[GroupedLineEvent]:
    """
    Generic multiline grouping of raw log lines.
    Rules:
    - If a line starts with a new timestamp prefix (or matches profile multiline rule), it starts a new event.
    - Otherwise, indented lines or continuation lines (e.g. Java stack traces) belong to previous event.
    """
    grouped_events: List[GroupedLineEvent] = []

    for line_no, line in lines:
        raw_line = line.rstrip("\r\n")
        if not raw_line.strip():
            continue

        is_new_event = False

        if profile and profile.multiline_config:
            cfg = profile.multiline_config
            if cfg.mode == "regex_pattern" and cfg.pattern:
                try:
                    if re.search(cfg.pattern, raw_line):
                        is_new_event = True
                except Exception:
                    pass
            elif cfg.mode == "indent":
                if not raw_line.startswith((" ", "\t")):
                    is_new_event = True

        if not is_new_event:
            # Fallback heuristic: check if line starts with timestamp
            for ts_re in TIMESTAMP_PREFIX_REGEXES:
                if ts_re.search(raw_line):
                    is_new_event = True
                    break

        # First line always starts a new event
        if not grouped_events or is_new_event:
            grouped_events.append(GroupedLineEvent(start_line_no=line_no, header_line=raw_line))
        else:
            grouped_events[-1].continuation_lines.append(raw_line)

    return grouped_events


def parse_event_with_profile(
    grouped_event: GroupedLineEvent,
    profile: ParserProfile,
    batch_id: str,
    file_name: str,
    upload_time: Optional[datetime] = None
) -> Tuple[Optional[CanonicalLogEvent], bool]:
    """
    Attempts to parse a single GroupedLineEvent using a given ParserProfile.
    Returns (CanonicalLogEvent, valid_timestamp_flag).
    """
    raw_text = grouped_event.full_raw
    header = grouped_event.header_line

    ts_raw = ""
    raw_level = None
    component = None
    message = raw_text
    attributes: Dict[str, Any] = {}
    source_system = profile.default_source_system

    parsed_successfully = False

    try:
        if profile.type == ProfileType.REGEX:
            rx = re.compile(profile.pattern)
            match = rx.search(header)
            if match:
                parsed_successfully = True
                groups = match.groupdict()
                attributes.update(groups)

                ts_raw = groups.get(profile.timestamp_field, "")
                raw_level = groups.get(profile.level_field)
                component = groups.get(profile.component_field) if profile.component_field else None
                message = groups.get(profile.message_field, raw_text)
                if profile.source_system_field and groups.get(profile.source_system_field):
                    source_system = groups[profile.source_system_field]
                if grouped_event.continuation_lines:
                    message = message + "\n" + "\n".join(grouped_event.continuation_lines)

        elif profile.type == ProfileType.JSON:
            try:
                data = json.loads(header)
                if isinstance(data, dict):
                    parsed_successfully = True
                    attributes.update(data)
                    ts_raw = str(data.get(profile.timestamp_field, ""))
                    raw_level = str(data.get(profile.level_field, ""))
                    if profile.component_field:
                        component = str(data.get(profile.component_field, ""))
                    message = str(data.get(profile.message_field, raw_text))
                    if profile.source_system_field and data.get(profile.source_system_field):
                        source_system = str(data[profile.source_system_field])
                    if grouped_event.continuation_lines:
                        message = message + "\n" + "\n".join(grouped_event.continuation_lines)
            except json.JSONDecodeError:
                parsed_successfully = False

        elif profile.type == ProfileType.DELIMITED:
            delimiter = profile.pattern if profile.pattern in [",", "\t", "|", ";"] else ","
            looks_like_json = header.lstrip()[:1] in ("{", "[")
            reader = csv.reader(StringIO(header), delimiter=delimiter)
            row = next(reader, None)
            expected_cols = len(profile.delimiter_fields) if profile.delimiter_fields else None
            # Require the row to actually have the expected column count, and
            # reject anything that's structurally JSON -- without these checks,
            # a JSON line can coincidentally have the same comma count as an
            # expected CSV row and falsely win profile detection.
            if row and not looks_like_json and (expected_cols is None or len(row) == expected_cols):
                parsed_successfully = True
                fields = profile.delimiter_fields or [f"col_{i}" for i in range(len(row))]
                for idx, val in enumerate(row):
                    if idx < len(fields):
                        attributes[fields[idx]] = val

                ts_raw = str(attributes.get(profile.timestamp_field, ""))
                raw_level = str(attributes.get(profile.level_field, ""))
                if profile.component_field:
                    component = str(attributes.get(profile.component_field, ""))
                message = str(attributes.get(profile.message_field, raw_text))
                if profile.source_system_field and attributes.get(profile.source_system_field):
                    source_system = str(attributes[profile.source_system_field])

    except Exception as e:
        logger.debug(f"Parsing error with profile '{profile.name}': {e}")
        parsed_successfully = False

    # Timestamp parsing & conversion
    ts_utc, ts_confidence = parse_and_convert_timestamp(
        raw_ts_str=ts_raw,
        profile_tz_name=profile.timezone,
        expected_format=profile.timestamp_format,
        upload_time=upload_time
    )

    valid_timestamp = ts_confidence in (TimestampConfidence.PARSED, TimestampConfidence.ASSUMED_UTC, TimestampConfidence.ASSUMED_LOCAL)

    # Normalize level
    norm_level = normalize_level(raw_level, custom_map=profile.level_map)

    event = CanonicalLogEvent(
        ts_utc=ts_utc,
        ts_raw=ts_raw if ts_raw else header[:50],
        ts_confidence=ts_confidence,
        level=norm_level,
        source_system=source_system,
        component=component,
        message=message,
        raw=raw_text,
        attributes=attributes,
        line_no=grouped_event.start_line_no,
        batch_id=batch_id,
        file_name=file_name
    )

    return event, (parsed_successfully and valid_timestamp)


def _is_json_line(line: str) -> bool:
    """Quick structural check: does this line look like a JSON object?"""
    stripped = line.lstrip()
    return stripped.startswith("{") and stripped.endswith("}")


def _is_delimited_line(line: str, delimiter: str = ",") -> bool:
    """Quick structural check: does this line contain the expected delimiter
    at least once, and NOT look like JSON?"""
    if _is_json_line(line):
        return False
    return delimiter in line


def evaluate_profile_match(
    grouped_sample: List[GroupedLineEvent],
    profile: ParserProfile
) -> Tuple[float, List[CanonicalLogEvent]]:
    """
    Evaluates profile match quality on sample events (first 200 grouped
    events).  The score is a weighted combination of:

      - Timestamp parse rate (0.6 weight) -- can the profile extract and
        parse a timestamp from most lines?
      - Structural quality (0.4 weight) -- does the parsed event carry a
        non-empty message AND a non-UNKNOWN level?  This penalises
        profiles that "match" every line by capturing a timestamp but
        producing garbage everywhere else.

    A profile must also pass a type-specific pre-filter before scoring:
    JSON profiles only score JSON-looking lines, delimited profiles only
    score delimiter-containing lines, etc.  This prevents false positives
    where a regex accidentally captures a timestamp from a JSON blob.
    """
    if not grouped_sample:
        return 0.0, []

    sample_to_test = grouped_sample[:200]
    valid_ts_count = 0
    structural_ok_count = 0
    parsed_events: List[CanonicalLogEvent] = []

    for item in sample_to_test:
        header = item.header_line

        # --- type-specific pre-filter ---
        if profile.type == ProfileType.JSON and not _is_json_line(header):
            continue
        if profile.type == ProfileType.DELIMITED:
            delim = profile.pattern if profile.pattern in (",", "\t", "|", ";") else ","
            if not _is_delimited_line(header, delim):
                continue

        evt, valid_ts = parse_event_with_profile(
            grouped_event=item,
            profile=profile,
            batch_id="test_batch",
            file_name="sample_eval"
        )
        if evt:
            parsed_events.append(evt)
        if valid_ts:
            valid_ts_count += 1
            # Structural quality: message must be non-empty and level must
            # be something the normaliser could resolve (not UNKNOWN).
            msg_ok = bool(evt and evt.message and evt.message.strip())
            level_ok = bool(evt and evt.level and evt.level.value != "UNKNOWN")
            if msg_ok and level_ok:
                structural_ok_count += 1

    tested = len(sample_to_test) or 1
    ts_score = valid_ts_count / tested
    struct_score = structural_ok_count / tested
    match_ratio = 0.6 * ts_score + 0.4 * struct_score
    return match_ratio, parsed_events


def select_best_profile(
    grouped_sample: List[GroupedLineEvent],
    profiles: List[ParserProfile]
) -> Tuple[Optional[ParserProfile], float]:
    """
    Scores every profile against the sample and returns the winner.
    Pre-filters skip profiles whose type is structurally incompatible
    with the sample (e.g. a JSON profile is skipped when no line looks
    like JSON).  If no profile meets its min_match_ratio threshold the
    best-scoring one is returned anyway with a warning -- this is better
    than blindly picking the first profile in filesystem order.
    """
    best_profile: Optional[ParserProfile] = None
    best_score = -1.0
    scored: List[Tuple[str, float]] = []

    for profile in profiles:
        score, _ = evaluate_profile_match(grouped_sample, profile)
        scored.append((profile.name, round(score, 4)))
        if score >= profile.min_match_ratio and score > best_score:
            best_score = score
            best_profile = profile

    # If nothing met threshold, pick the highest scorer anyway --
    # still better than the arbitrary first-profile fallback.
    if best_profile is None and scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        top_name, top_score = scored[0]
        for p in profiles:
            if p.name == top_name:
                best_profile = p
                best_score = top_score
                logger.warning(
                    "No profile met its min_match_ratio; falling back to "
                    "best scorer '%s' (score=%.4f).  Results may be poor -- "
                    "consider adding a dedicated parser profile for this format.",
                    top_name, top_score,
                )
                break

    return best_profile, (best_score if best_score >= 0 else 0.0)
