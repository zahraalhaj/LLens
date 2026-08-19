"""
Timestamp Parsing and Timezone Resolution Engine.
Strictly implements the Timestamp Policy.
"""

from datetime import datetime, timezone
import dateutil.parser
import dateutil.tz
from typing import Optional, Tuple
import logging

from backend.core.schema import TimestampConfidence

logger = logging.getLogger("logtool.timezones")


def parse_and_convert_timestamp(
    raw_ts_str: Optional[str],
    profile_tz_name: Optional[str] = "UTC",
    expected_format: Optional[str] = None,
    upload_time: Optional[datetime] = None
) -> Tuple[Optional[str], TimestampConfidence]:
    """
    Parses a raw timestamp string and converts it to UTC ISO-8601 string.
    Returns (iso_utc_str, confidence_enum).
    
    Timestamp Policy Rules:
    1. Timezone present in raw string -> convert to UTC -> confidence: parsed
    2. Profile timezone provided -> parse & interpret in profile timezone -> convert to UTC -> confidence: parsed
    3. Unzoned timestamp parsed successfully -> assume UTC -> confidence: assumed_utc
    4. Cannot parse timestamp -> use upload time -> confidence: unparseable
    """
    if not upload_time:
        upload_time = datetime.now(timezone.utc)
    
    if not raw_ts_str or not str(raw_ts_str).strip():
        return upload_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), TimestampConfidence.UNPARSEABLE

    cleaned_str = str(raw_ts_str).strip()

    # Attempt 1: Custom strftime format if provided
    parsed_dt = None
    if expected_format:
        try:
            parsed_dt = datetime.strptime(cleaned_str, expected_format)
        except Exception:
            parsed_dt = None

    # Attempt 2: Flexible dateutil parsing
    if not parsed_dt:
        try:
            parsed_dt = dateutil.parser.parse(cleaned_str, fuzzy=True)
        except Exception as e:
            logger.debug(f"Failed dateutil parse on '{cleaned_str}': {e}")
            parsed_dt = None

    if not parsed_dt:
        # Fallback to upload time
        return upload_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), TimestampConfidence.UNPARSEABLE

    # Determine timezone behavior
    has_tz_in_string = parsed_dt.tzinfo is not None and parsed_dt.tzinfo.utcoffset(parsed_dt) is not None

    if has_tz_in_string:
        # Convert existing TZ to UTC
        utc_dt = parsed_dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), TimestampConfidence.PARSED

    # Unzoned datetime: Apply profile timezone if provided
    profile_tz = None
    if profile_tz_name:
        profile_tz = dateutil.tz.gettz(profile_tz_name)

    if profile_tz:
        localized_dt = parsed_dt.replace(tzinfo=profile_tz)
        utc_dt = localized_dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), TimestampConfidence.PARSED
    else:
        # Assume UTC by default when no timezone specified
        localized_dt = parsed_dt.replace(tzinfo=timezone.utc)
        return localized_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), TimestampConfidence.ASSUMED_UTC
