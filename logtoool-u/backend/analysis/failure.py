"""
Deterministic Failure Analysis -- Phase 6 of the LLens Multi-Log Analysis
Strategy.

Classifies technical failures into reusable signatures, in two tiers:

  1. EXACT RULES (event_type match, a numeric field threshold, or a fixed
     regex over the event's own message/raw text) -- checked first, in
     priority order, HIGH confidence.
  2. Only when no exact rule matches: normalize the error text (lowercase,
     strip punctuation/digits/ids), tokenize it, and check it against each
     known signature's keyword set (conservative AND-match). A hit is
     MEDIUM confidence; no hit at all is UNKNOWN_ERROR at LOW confidence.

Two signature types are NOT text-derived at all -- they're read directly
from already-computed, already-tested structured output from earlier
phases, which is far more reliable than re-deriving them from text:
  - CORRELATION_CONFLICT <- backend/analysis/correlate.py's CorrelationConflict
  - QUEUE_GAP / MISSING_PROCESSOR_RECEIPT <- backend/analysis/dependency.py's
    QueueHandoffReport (compute_otp_handoff_chain())

No LLM anywhere in this module. `statement` is a fixed, deterministic
template per signature, never freely generated text.

AGGREGATION: "count affected flows, not raw error lines" -- every raw
per-event classification is grouped by (finding_type, flow_id) before
being turned into a Finding, so five retried timeout lines inside one
transaction become ONE finding whose evidence_event_ids lists all five,
not five separate findings.
"""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from backend.analysis.correlation_schema import CorrelatedFlow, CorrelationConflict
from backend.analysis.dependency import compute_otp_handoff_chain
from backend.analysis.dependency_schema import QueueHandoffReport
from backend.analysis.failure_schema import (
    Confidence,
    FailureAnalysisResult,
    FailureSignature,
    Finding,
    RoutingArea,
    Severity,
)
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent

_FS = FailureSignature

# ---------------------------------------------------------------------------
# Tier 1: exact rules, priority order (first match wins)
# ---------------------------------------------------------------------------

_INVALID_STEPUP_ID_RE = re.compile(r"invalid\s*stepup\s*(request\s*)?id", re.IGNORECASE)
_EMPTY_RESPONSE_RE = re.compile(r"(response|status)\s*(is\s*)?(returned\s*)?(null|empty)", re.IGNORECASE)


def _text_of(event: NormalizedEvent) -> str:
    return f"{event.event_type or ''} {event.raw_reference or ''}"


def _exact_rule_match(event: NormalizedEvent) -> Optional[FailureSignature]:
    # -- Cardinal-specific event_type markers (verified against
    # parser_Cardinal.py's classify_event(), same vocabulary Phases 4-5 use) --
    if event.event_type == "vplus_mq_timeout":
        return _FS.V_PLUS_MQ_TIMEOUT
    if event.event_type == "oob_http_error":
        return _FS.OOB_HTTP_ERROR
    if event.event_type == "oob_empty_status_response":
        return _FS.EMPTY_RESPONSE
    if event.event_type in ("oob_status_api_error", "oob_validate_exception"):
        return _FS.OOB_BUSINESS_ERROR

    # -- text-pattern exact rules (deterministic regex, not fuzzy tokenization --
    # still Tier 1 because it's a fixed, specific pattern, not a bag-of-words match) --
    text = _text_of(event)
    if _INVALID_STEPUP_ID_RE.search(text):
        return _FS.INVALID_STEPUP_ID
    if _EMPTY_RESPONSE_RE.search(text):
        return _FS.EMPTY_RESPONSE

    # -- structured-field rules (reuse Phase 2's already-extracted fields
    # rather than re-parsing text) --
    if event.http_status:
        try:
            if int(event.http_status) >= 400:
                return _FS.HOST_RESPONSE_CODE_FAILURE
        except (TypeError, ValueError):
            pass
    if event.business_error_code and event.log_family == LogFamily.VFLEX:
        return _FS.HOST_RESPONSE_CODE_FAILURE

    if event.parse_status == "failed":
        return _FS.PARSER_FAILURE

    return None


# ---------------------------------------------------------------------------
# Tier 2: normalize -> tokenize -> keyword classification (only reached when
# Tier 1 found nothing AND the event still qualifies as a failure)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z]+")

# Conservative AND-match: every keyword in the set must appear as its own
# token. Checked in this fixed priority order. Deliberately excludes
# QUEUE_GAP/MISSING_PROCESSOR_RECEIPT/CORRELATION_CONFLICT -- those are
# never text-classified (see module docstring).
_KEYWORD_SIGNATURES: Tuple[Tuple[frozenset, FailureSignature], ...] = (
    (frozenset({"stepup", "invalid"}), _FS.INVALID_STEPUP_ID),
    (frozenset({"timeout", "mq"}), _FS.V_PLUS_MQ_TIMEOUT),
    (frozenset({"empty"}), _FS.EMPTY_RESPONSE),
    (frozenset({"null"}), _FS.EMPTY_RESPONSE),
    (frozenset({"http", "oob"}), _FS.OOB_HTTP_ERROR),
    (frozenset({"oob", "error"}), _FS.OOB_BUSINESS_ERROR),
    (frozenset({"parse", "failed"}), _FS.PARSER_FAILURE),
    (frozenset({"parsing", "error"}), _FS.PARSER_FAILURE),
)


def _normalize_and_tokenize(text: str) -> frozenset:
    """Lowercase, strip everything but letters, split into a token set --
    a deterministic normalization, not an LLM summarization."""
    return frozenset(_TOKEN_RE.findall(text.lower()))


def _keyword_fallback_match(event: NormalizedEvent) -> Optional[FailureSignature]:
    tokens = _normalize_and_tokenize(_text_of(event))
    for keywords, signature in _KEYWORD_SIGNATURES:
        if keywords <= tokens:
            return signature
    return None


def _is_failure_event(event: NormalizedEvent) -> bool:
    return event.level in ("ERROR", "CRITICAL") or bool(event.failure_signature) or event.parse_status == "failed"


def classify_event(event: NormalizedEvent) -> Optional[Tuple[FailureSignature, Confidence]]:
    """Returns None for an event that isn't a failure at all. Tier 1 exact
    rules are checked regardless of level/parse_status (a structured field
    like http_status>=400 is meaningful evidence on its own); the Tier 2
    keyword fallback and UNKNOWN_ERROR are only reached for events that
    already qualify as a failure by level/failure_signature/parse_status --
    otherwise routine INFO-level log noise would get bucketed as
    UNKNOWN_ERROR just for mentioning an unrelated word."""
    exact = _exact_rule_match(event)
    if exact:
        return exact, Confidence.HIGH

    if not _is_failure_event(event):
        return None

    fallback = _keyword_fallback_match(event)
    if fallback:
        return fallback, Confidence.MEDIUM

    return _FS.UNKNOWN_ERROR, Confidence.LOW


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

_DEFAULT_ROUTE: Dict[FailureSignature, RoutingArea] = {
    _FS.V_PLUS_MQ_TIMEOUT: RoutingArea.MIDDLEWARE_VPLUS,
    _FS.INVALID_STEPUP_ID: RoutingArea.OOB_INTEGRATION,
    _FS.OOB_HTTP_ERROR: RoutingArea.OOB_INTEGRATION,
    _FS.OOB_BUSINESS_ERROR: RoutingArea.OOB_INTEGRATION,
    _FS.EMPTY_RESPONSE: RoutingArea.MIDDLEWARE_VPLUS,
    _FS.HOST_RESPONSE_CODE_FAILURE: RoutingArea.ISSUER_HOST,
    _FS.QUEUE_GAP: RoutingArea.MESSAGING_QUEUE,
    _FS.MISSING_PROCESSOR_RECEIPT: RoutingArea.MESSAGING_QUEUE,
    _FS.CORRELATION_CONFLICT: RoutingArea.APPLICATION_SUPPORT,
    _FS.PARSER_FAILURE: RoutingArea.PARSER_DATA_QUALITY,
    _FS.UNKNOWN_ERROR: RoutingArea.APPLICATION_SUPPORT,
}

_DEFAULT_SEVERITY: Dict[FailureSignature, Severity] = {
    _FS.V_PLUS_MQ_TIMEOUT: Severity.HIGH,
    _FS.INVALID_STEPUP_ID: Severity.HIGH,
    _FS.OOB_HTTP_ERROR: Severity.HIGH,
    _FS.OOB_BUSINESS_ERROR: Severity.MEDIUM,
    _FS.EMPTY_RESPONSE: Severity.MEDIUM,
    _FS.HOST_RESPONSE_CODE_FAILURE: Severity.HIGH,
    _FS.QUEUE_GAP: Severity.HIGH,
    _FS.MISSING_PROCESSOR_RECEIPT: Severity.HIGH,
    _FS.CORRELATION_CONFLICT: Severity.MEDIUM,
    _FS.PARSER_FAILURE: Severity.LOW,
    _FS.UNKNOWN_ERROR: Severity.MEDIUM,
}


def _route_for(signature: FailureSignature, event: NormalizedEvent) -> RoutingArea:
    """Signature-level default, with one context-aware override: a host
    response code failure surfaced through the Bank API dependency
    (VFlex) is more actionable routed to Bank API than the generic
    Issuer Host bucket."""
    if signature == _FS.HOST_RESPONSE_CODE_FAILURE and event.log_family == LogFamily.VFLEX:
        return RoutingArea.BANK_API
    return _DEFAULT_ROUTE[signature]


_STATEMENT_TEMPLATES: Dict[FailureSignature, str] = {
    _FS.V_PLUS_MQ_TIMEOUT: "V+ MQ timeout detected in {flow_count} flow(s) ({event_count} event(s)).",
    _FS.INVALID_STEPUP_ID: "Invalid StepUpId detected in {flow_count} flow(s) ({event_count} event(s)).",
    _FS.OOB_HTTP_ERROR: "OOB HTTP-level error detected in {flow_count} flow(s) ({event_count} event(s)).",
    _FS.OOB_BUSINESS_ERROR: "OOB business/API error detected in {flow_count} flow(s) ({event_count} event(s)).",
    _FS.EMPTY_RESPONSE: "Empty or null response detected in {flow_count} flow(s) ({event_count} event(s)).",
    _FS.HOST_RESPONSE_CODE_FAILURE: "Host returned a failure response code in {flow_count} flow(s) ({event_count} event(s)).",
    _FS.QUEUE_GAP: "Processor received a message with no downstream-queue routing evidence in {flow_count} tracker(s).",
    _FS.MISSING_PROCESSOR_RECEIPT: "Application queued a message the OTP processor never recorded receiving, in {flow_count} tracker(s).",
    _FS.CORRELATION_CONFLICT: "Conflicting high-confidence identifiers prevented an automatic correlation merge in {flow_count} case(s).",
    _FS.PARSER_FAILURE: "Parser failed to fully extract {flow_count} flow(s)' worth of events ({event_count} event(s)).",
    _FS.UNKNOWN_ERROR: "Unclassified technical error detected in {flow_count} flow(s) ({event_count} event(s)) -- no known signature matched.",
}


# ---------------------------------------------------------------------------
# Aggregation: per-event classification -> per-(signature, flow) findings
# ---------------------------------------------------------------------------


def _flow_id_map(flows: Optional[List[CorrelatedFlow]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for flow in flows or []:
        for event_id in flow.linked_event_ids:
            mapping[event_id] = flow.flow_id
    return mapping


def _event_level_findings(events: List[NormalizedEvent], flows: Optional[List[CorrelatedFlow]]) -> List[Finding]:
    event_to_flow = _flow_id_map(flows)

    # group_key -> [events]; a group is exactly one (signature, flow) pair,
    # so its finding always represents ONE affected flow (see analyze_failures()).
    groups: Dict[Tuple[FailureSignature, str], List[NormalizedEvent]] = defaultdict(list)
    confidences: Dict[Tuple[FailureSignature, str], Confidence] = {}
    known_flow: Dict[Tuple[FailureSignature, str], bool] = {}

    for event in events:
        result = classify_event(event)
        if result is None:
            continue
        signature, confidence = result
        resolved_flow_id = event_to_flow.get(event.source_event_id)
        # A single event with no resolvable flow (no `flows` passed, or the
        # event isn't part of any correlated flow) groups under its own
        # event id -- never silently dropped, but affected_flow_ids stays
        # empty for it (see analyze_failures()'s per-finding count).
        flow_key = resolved_flow_id or event.source_event_id or f"unlinked:{event.event_no}"
        group_key = (signature, flow_key)
        groups[group_key].append(event)
        known_flow[group_key] = resolved_flow_id is not None
        # HIGH confidence wins if any member event matched an exact rule.
        existing = confidences.get(group_key)
        if existing is None or confidence == Confidence.HIGH:
            confidences[group_key] = confidence

    findings: List[Finding] = []
    for (signature, flow_key), group_events in groups.items():
        representative = group_events[0]
        findings.append(
            Finding(
                finding_type=signature,
                severity=_DEFAULT_SEVERITY[signature],
                statement=_STATEMENT_TEMPLATES[signature].format(flow_count=1, event_count=len(group_events)),
                confidence=confidences[(signature, flow_key)],
                evidence_event_ids=[e.source_event_id for e in group_events if e.source_event_id],
                source_files=sorted({e.source_file for e in group_events}),
                suggested_route=_route_for(signature, representative),
                affected_flow_ids=[flow_key] if known_flow[(signature, flow_key)] else [],
                occurrence_count=len(group_events),
            )
        )
    return findings


def _queue_and_conflict_findings(
    queue_handoff: Optional[QueueHandoffReport],
    conflicts: Optional[List[CorrelationConflict]],
) -> List[Finding]:
    findings: List[Finding] = []

    if queue_handoff:
        if queue_handoff.orphan_tracker_nos:
            orphan_event_ids = [
                event_id
                for record in queue_handoff.tracker_records
                if record.tracker_no in queue_handoff.orphan_tracker_nos
                for event_id in record.stage_event_ids.values()
            ]
            findings.append(
                Finding(
                    finding_type=_FS.QUEUE_GAP,
                    severity=_DEFAULT_SEVERITY[_FS.QUEUE_GAP],
                    statement=_STATEMENT_TEMPLATES[_FS.QUEUE_GAP].format(flow_count=len(queue_handoff.orphan_tracker_nos)),
                    confidence=Confidence.HIGH,
                    evidence_event_ids=orphan_event_ids,
                    source_files=[],
                    suggested_route=_DEFAULT_ROUTE[_FS.QUEUE_GAP],
                    affected_flow_ids=list(queue_handoff.orphan_tracker_nos),
                    occurrence_count=len(queue_handoff.orphan_tracker_nos),
                )
            )
        if queue_handoff.unmatched_tracker_nos:
            unmatched_event_ids = [
                event_id
                for record in queue_handoff.tracker_records
                if record.tracker_no in queue_handoff.unmatched_tracker_nos
                for event_id in record.stage_event_ids.values()
            ]
            findings.append(
                Finding(
                    finding_type=_FS.MISSING_PROCESSOR_RECEIPT,
                    severity=_DEFAULT_SEVERITY[_FS.MISSING_PROCESSOR_RECEIPT],
                    statement=_STATEMENT_TEMPLATES[_FS.MISSING_PROCESSOR_RECEIPT].format(
                        flow_count=len(queue_handoff.unmatched_tracker_nos)
                    ),
                    confidence=Confidence.HIGH,
                    evidence_event_ids=unmatched_event_ids,
                    source_files=[],
                    suggested_route=_DEFAULT_ROUTE[_FS.MISSING_PROCESSOR_RECEIPT],
                    affected_flow_ids=list(queue_handoff.unmatched_tracker_nos),
                    occurrence_count=len(queue_handoff.unmatched_tracker_nos),
                )
            )

    for conflict in conflicts or []:
        evidence = sorted(set(conflict.source_event_ids_a) | set(conflict.source_event_ids_b))
        findings.append(
            Finding(
                finding_type=_FS.CORRELATION_CONFLICT,
                severity=_DEFAULT_SEVERITY[_FS.CORRELATION_CONFLICT],
                statement=_STATEMENT_TEMPLATES[_FS.CORRELATION_CONFLICT].format(flow_count=len(conflict.affected_flow_ids)),
                confidence=Confidence.HIGH,
                evidence_event_ids=evidence,
                source_files=[],
                suggested_route=_DEFAULT_ROUTE[_FS.CORRELATION_CONFLICT],
                affected_flow_ids=list(conflict.affected_flow_ids),
                occurrence_count=1,
            )
        )

    return findings


def analyze_failures(
    events: List[NormalizedEvent],
    flows: Optional[List[CorrelatedFlow]] = None,
    conflicts: Optional[List[CorrelationConflict]] = None,
    queue_handoff: Optional[QueueHandoffReport] = None,
) -> FailureAnalysisResult:
    """Deterministic entry point. `flows` enables flow-aware aggregation
    (required for "count affected flows, not raw error lines" to be
    meaningful) -- without it, each event is its own aggregation unit,
    which is honest but coarser. `conflicts`/`queue_handoff` are optional
    pass-throughs from Phase 3's correlate_events() and Phase 5's
    compute_otp_handoff_chain(); if omitted, this function computes
    queue_handoff itself from `events` so CORRELATION_CONFLICT/QUEUE_GAP/
    MISSING_PROCESSOR_RECEIPT are never silently skipped just because the
    caller forgot to pass them in."""
    if queue_handoff is None:
        queue_handoff = compute_otp_handoff_chain(events)

    findings = _event_level_findings(events, flows) + _queue_and_conflict_findings(queue_handoff, conflicts)

    signature_flow_counts: Dict[str, int] = defaultdict(int)
    all_affected_flows = set()
    for finding in findings:
        # Each event-level finding already represents exactly ONE affected
        # flow (grouped that way in _event_level_findings()); each
        # queue/conflict finding can bundle several at once
        # (_queue_and_conflict_findings()) -- either way,
        # len(affected_flow_ids) is the correct flow count, falling back
        # to 1 only when the flow itself couldn't be resolved at all.
        signature_flow_counts[finding.finding_type.value] += len(finding.affected_flow_ids) or 1
        all_affected_flows.update(finding.affected_flow_ids)

    return FailureAnalysisResult(
        findings=findings,
        signature_flow_counts=dict(signature_flow_counts),
        total_raw_events_analyzed=len(events),
        total_affected_flows=len(all_affected_flows),
    )
