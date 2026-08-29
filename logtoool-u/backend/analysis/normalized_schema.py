"""
Normalized Event Model -- Phase 2 of the LLens Multi-Log Analysis Strategy.

Defines the single common shape that every payment-log family's
already-parsed `attributes.details` (see each parser's `parse_log_file`
adapter in backend/custom_parsers/, and each family's
backend/analysis/<family>.py module) gets mapped into. This is the
foundation future analytics and cross-log correlation (Phase 3+) will be
built on, so it deliberately holds no per-family logic itself -- just the
schema and the small set of helpers every per-family extractor shares
(tracker-type/phase derivation, PII masking, safe nested lookups, failure
signatures, stage classification).

Deliberately has no dependency on backend/custom_parsers or on any
family-specific backend/analysis/<family>.py module -- those modules import
*from* this one (to build their own normalize_<family>_event()), so this
file must not import back from them (see backend/analysis/normalize.py for
the dispatcher that wires the two directions together).

SECURITY: nothing in this model or its helpers ever stores a raw OTP code,
a raw PAN, a verification/session token value, or an unmasked mobile/email
address -- see mask_mobile()/mask_email()/extract_card_last4() below and
each family's normalize_<family>_event() for what gets deliberately left
out of the mapping.
"""
import re
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, BeforeValidator, Field


class LogFamily(str, Enum):
    """Values are deliberately the same source_system strings each parser's
    DISPLAY_NAME/DEFAULT_SOURCE_SYSTEM constants use (see
    backend/core/custom_parser_registry.py) -- duplicated here rather than
    imported, same convention backend/analysis/otp_processor.py already
    uses for its own DEFAULT_SOURCE_SYSTEM ("kept here too... so this
    analysis module has no dependency on the custom_parsers package").
    Duplicating the literal lets LogFamily(source_system) construct the
    right member directly, with no separate mapping table to keep in sync."""

    CARDINAL = "cardinal_stepup_oob_log"
    NETCETERA_VPLUS = "afs_netcetera_3ds_stepup"
    DEBIT_PORTAL = "debit_portal_log"
    VFLEX = "vflex_transaction_log"
    OTP_PROCESSOR = "otp_online_processor"

    # Fallback for any source_system with no registered family normalizer
    # (declarative profiles, and the custom parsers that aren't one of the
    # 5 families above) -- see backend/analysis/normalize.py's
    # _normalize_generic_event(). These events get correlation (via
    # NormalizedEvent.extra_identifiers below) but not the family-specific
    # dependency-health/lifecycle-stage modeling the 5 families above have.
    GENERIC = "generic_profile"


# ---------------------------------------------------------------------------
# Shared helpers used by every family's normalize_<family>_event()
# ---------------------------------------------------------------------------

_TRACKER_PHASE_MAP = {"SU": "STEP_UP", "IA": "INITIATE_ACTION", "VL": "VALIDATE"}
_TRACKER_SHAPE_RE = re.compile(r"^[A-Za-z]{2}\d+$")
_LAST4_RE = re.compile(r"(\d{4})\D*$")

_STAGE_KEYWORDS = [
    # Checked in order -- first match wins, so more specific keywords are
    # listed before the generic ones they'd otherwise be shadowed by.
    ("unparsed", "UNPARSED"),
    ("otp_success", "AUTH_RESULT"),
    ("otp_processed", "AUTH_RESULT"),
    ("force_verify", "AUTH_RESULT"),
    ("verify", "AUTH_CHALLENGE"),
    ("stepup", "AUTH_CHALLENGE"),
    ("initiate_action", "AUTH_CHALLENGE"),
    ("oob", "OOB_PROCESSING"),
    ("queue", "QUEUE_DELIVERY"),
    ("sms", "QUEUE_DELIVERY"),
    ("email", "QUEUE_DELIVERY"),
    ("response", "RESPONSE_RECEIVED"),
    ("input", "INITIATED"),
    ("request", "INITIATED"),
    ("exception", "ERROR"),
    ("error", "ERROR"),
    ("fail", "ERROR"),
]


def dig(source: Optional[Dict[str, Any]], *path: str, default: Any = None) -> Any:
    """Safe nested dict lookup: dig({"a": {"b": 1}}, "a", "b") -> 1. Never
    raises on a missing key, a non-dict intermediate value, or a None
    input -- every family's `details` shape varies by parse_status
    (partial/failed records carry a much smaller dict), so every lookup
    into it needs to tolerate that without special-casing each one."""
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def looks_like_tracker(value: Optional[str]) -> bool:
    """The tracker-number convention shared by every family's parser:
    two letters + digits (e.g. "SU12345", "IA67890")."""
    return bool(value) and bool(_TRACKER_SHAPE_RE.match(str(value)))


def derive_tracker_type_and_phase(tracker_no: Optional[str]) -> "tuple[Optional[str], Optional[str]]":
    """Same prefix convention parser_Cardinal.py's infer_phase() and
    parser_VFlex.py's tracker_phase() each already implement -- re-derived
    here rather than trusting each family's own field (not every family's
    adapter surfaces a resolved tracker_type/phase in `details`), so this
    is a single, family-independent source of truth."""
    if not tracker_no:
        return None, None
    prefix = str(tracker_no)[:2].upper()
    return prefix, _TRACKER_PHASE_MAP.get(prefix, prefix)


def mask_mobile(value: Optional[str]) -> Optional[str]:
    """Keeps only the last 2 digits -- enough to eyeball "same customer
    calling back" without retaining a reversible phone number."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    if len(digits) <= 2:
        return "*" * len(digits)
    return ("*" * (len(digits) - 2)) + digits[-2:]


def mask_email(value: Optional[str]) -> Optional[str]:
    """Keeps only the first character of the local part and the full
    domain -- enough to distinguish providers/domains in aggregate
    analysis without retaining a reversible address."""
    value = str(value) if value else ""
    if "@" not in value:
        return None
    local, _, domain = value.partition("@")
    if not domain:
        return None
    prefix = local[0] if local else ""
    return f"{prefix}***@{domain}"


def extract_card_last4(*candidates: Optional[str]) -> Optional[str]:
    """Extracts ONLY the last 4 digits from a masked-card-style string
    (e.g. "411111XXXXXX1111", or a bare "1111"). Deliberately anchored to
    the END of the string (not "strip all non-digits and take the last
    4") so a malformed/oddly-masked value can't accidentally splice digits
    from an unrelated earlier group into a false last4. Returns None
    (never partial garbage) if no candidate ends in 4 digits."""
    for candidate in candidates:
        if not candidate:
            continue
        match = _LAST4_RE.search(str(candidate))
        if match:
            return match.group(1)
    return None


def classify_normalized_stage(event_type: Optional[str], level: Optional[str]) -> str:
    """Coarse, family-independent lifecycle stage, purely from the
    event's own type/level strings -- deterministic substring
    classification, same style as backend/core/schema.py's
    normalize_level() heuristic fallback. Not a substitute for each
    family's own richer event_type; just a common bucket every family's
    events can be grouped by without needing five separate stage tables."""
    if level in ("ERROR", "CRITICAL"):
        return "ERROR"
    text = (event_type or "").lower()
    for keyword, stage in _STAGE_KEYWORDS:
        if keyword in text:
            return stage
    return "OTHER" if event_type else "UNKNOWN"


def build_failure_signature(log_family: "LogFamily", event_type: Optional[str], reason: Optional[str]) -> str:
    """Deterministic, stable key for grouping "the same kind of failure"
    across events/files -- not clustering itself (that's a later phase),
    just a reproducible fingerprint for it to key off of."""
    reason_key = re.sub(r"\s+", "_", (reason or "unknown").strip().lower())[:60]
    family_value = log_family.value if isinstance(log_family, LogFamily) else str(log_family)
    return f"{family_value}:{event_type or 'unknown'}:{reason_key}"


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Pydantic v2 no longer auto-coerces int/float -> str (unlike v1), but
# several source fields (http status codes, numeric IDs) can legitimately
# arrive as non-string JSON values from the parsers' own payloads. Every
# Optional[str]-typed field below uses this alias so that coercion happens
# once, centrally, instead of every family extractor needing its own
# str()-wrapping at each assignment site.
OptStr = Annotated[Optional[str], BeforeValidator(_coerce_str)]
OptFloat = Annotated[Optional[float], BeforeValidator(_coerce_float)]


class NormalizedEvent(BaseModel):
    """One canonical event, normalized. Every field below except the
    SOURCE IDENTITY group is optional -- a given family/event legitimately
    won't populate most of them (e.g. a Debit Portal event has no
    stepup_request_id; an OTP Processor event has no issuer_id), and that
    absence is meaningful signal, not a parsing failure."""

    # -- SOURCE IDENTITY (always present; this is the trace-back-to-source contract) --
    source_file: str
    log_family: LogFamily
    event_no: int  # position in this file's parsed event stream (== CanonicalLogEvent.line_no)
    physical_line_start: Optional[int] = None  # true original file line number, ONLY when the parser retained it (see limitations)
    raw_reference: str  # full raw/reconstructed text block for this event -- the actual trace-back evidence
    source_event_id: OptStr = None  # CanonicalLogEvent.event_id, for joining back to the stored row
    batch_id: OptStr = None

    # -- EVENT IDENTITY --
    event_timestamp: OptStr = None  # ts_utc, ISO-8601 UTC
    level: OptStr = None  # the canonical event's own severity (INFO/WARN/ERROR/CRITICAL/...)
    tracker_no: OptStr = None
    tracker_type: OptStr = None
    phase: OptStr = None
    event_type: OptStr = None

    # -- CORRELATION --
    transaction_id: OptStr = None
    ds_transaction_id: OptStr = None  # 3DS Directory Server transaction id -- only Cardinal/Netcetera are 3DS-flow families
    stepup_request_id: OptStr = None
    credential_id: OptStr = None
    correlation_id: OptStr = None  # the parser-resolved id already stored in attributes.correlation_id
    tran_ref: OptStr = None
    oob_tracker_id: OptStr = None
    msg_id: OptStr = None

    # Generic correlation bag, alongside (not replacing) the named fields
    # above -- populated ONLY by the generic-event fallback in normalize.py
    # for source_systems outside the 5 hardcoded families, from a
    # profile's declared ParserProfile.correlation_keys. Always empty for
    # the 5 existing families' own normalize_<family>_event() functions,
    # so this field's existence is zero-behavior-change for them. See
    # backend/analysis/correlate.py's extra-identifier union-find pass.
    extra_identifiers: Dict[str, str] = Field(default_factory=dict)

    # -- BUSINESS CONTEXT --
    issuer_id: OptStr = None
    bank_org: OptStr = None
    merchant_name: OptStr = None
    merchant_id: OptStr = None
    amount: OptFloat = None
    currency: OptStr = None
    card_last4: OptStr = None
    channel: OptStr = None

    # -- CUSTOMER/CONTACT (masked -- see mask_mobile()/mask_email() above) --
    masked_mobile: OptStr = None
    masked_email: OptStr = None
    customer_id: OptStr = None

    # -- AUTHENTICATION --
    authentication_method: OptStr = None
    credential_type: OptStr = None
    verification_token_present: bool = False
    otp_reference_code: OptStr = None  # a non-sensitive reference id -- NEVER the raw OTP value
    stepup_status: OptStr = None
    oob_status: OptStr = None
    card_blocked: Optional[bool] = None

    # -- DEPENDENCY --
    dependency_name: OptStr = None
    endpoint: OptStr = None
    queue_name: OptStr = None
    response_code: OptStr = None
    http_status: OptStr = None
    business_error_code: OptStr = None
    latency_ms: OptFloat = None

    # -- ANALYSIS --
    normalized_stage: str = "UNKNOWN"
    terminal_status: OptStr = None
    failure_signature: OptStr = None
    parse_status: str = "parsed"  # "parsed" | "partial" | "failed"
    used_fallback_parsing: bool = False  # succeeded, but via a secondary/regex recovery path rather than the primary parse method
    correlation_confidence: float = 0.0  # 0.0-1.0, see each family extractor's confidence heuristic
    evidence_level: str = "full"  # "full" | "partial" | "minimal"

    # -- SECURITY --
    sensitive_fields_removed: List[str] = Field(default_factory=list)
