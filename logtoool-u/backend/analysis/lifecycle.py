"""
Deterministic Transaction Reconstruction & Lifecycle Analysis -- Phase 4 of
the LLens Multi-Log Analysis Strategy.

Takes a CorrelatedFlow (Phase 3) and its member NormalizedEvents (Phase 2)
and maps them onto the 12 canonical lifecycle stages
(backend/analysis/lifecycle_schema.py), purely via deterministic event_type
lookup tables -- no LLM, no natural-language generation.

STAGE-MAPPING RATIONALE (event_type -> stage, per family)
-----------------------------------------------------------------------
Every event_type below is a REAL classify_event()/event_type value from the
corresponding backend/custom_parsers/parser_*.py (verified against the
parser source, not guessed). Where the exact same literal marker string is
classified by two different parsers (e.g. "StepupCall V+ Input/Response
Message" appears in BOTH parser_Cardinal.py and parser_AFS_Netcetera.py --
it's the same real-world wire event, captured independently from each
side of the call), it is mapped to the SAME stage in both families for
consistency.

Only event types with a clear, defensible business meaning are mapped.
Operational/error event types (timeouts, exceptions, generic "message"/
"error" catch-alls) are deliberately left unmapped -- they inform
terminal_status/failure_boundary directly (via event level and the
flow's business-outcome fields), not stage progress. Two of the twelve
canonical stages (DOWNSTREAM_QUEUE_SELECTED, and CARD_LOOKUP_* outside of
VFlex) have no mapping in any of the five parsers' current vocabulary --
this is a genuine, documented gap in what these logs capture today, not
an oversight; see the Phase 4 report for details.
"""
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from backend.analysis.correlation_schema import CorrelatedFlow
from backend.analysis.lifecycle_schema import (
    AuthTemplate,
    FailureBoundary,
    FlowLifecycle,
    LifecycleStage,
    StageDuration,
    TerminalStatus,
    TimelineEntry,
)
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent

# ---------------------------------------------------------------------------
# Master stage order and the two auth-method templates
# ---------------------------------------------------------------------------

MASTER_STAGE_ORDER: Tuple[str, ...] = tuple(stage.value for stage in LifecycleStage)

_OTP_ONLY_STAGES = {LifecycleStage.PROCESSOR_RECEIVED.value, LifecycleStage.DOWNSTREAM_QUEUE_SELECTED.value, LifecycleStage.OOB_INITIATED.value}
_OOB_ONLY_STAGES = {LifecycleStage.OTP_GENERATED.value, LifecycleStage.APPLICATION_QUEUE_CONFIRMED.value}

OTP_TEMPLATE_STAGES: Tuple[str, ...] = tuple(s for s in MASTER_STAGE_ORDER if s not in _OTP_ONLY_STAGES)
OOB_TEMPLATE_STAGES: Tuple[str, ...] = tuple(s for s in MASTER_STAGE_ORDER if s not in _OOB_ONLY_STAGES)

TEMPLATES: Dict[str, Tuple[str, ...]] = {
    AuthTemplate.OTP.value: OTP_TEMPLATE_STAGES,
    AuthTemplate.OOB.value: OOB_TEMPLATE_STAGES,
}

# ---------------------------------------------------------------------------
# Per-family event_type -> canonical stage maps
# ---------------------------------------------------------------------------

_S = LifecycleStage

CARDINAL_STAGE_MAP: Dict[str, str] = {
    "request_body": _S.REQUEST_RECEIVED,
    "vplus_input": _S.CHALLENGE_SELECTED,  # "StepupCall V+ Input Message" -- Cardinal invoking the stepup/challenge call
    "vplus_response": _S.PROCESSOR_RECEIVED,  # V+ acknowledging Cardinal's stepup call
    "otp_input": _S.OTP_GENERATED,  # InitiateActionCallController "Input Message"
    "otp_queue": _S.APPLICATION_QUEUE_CONFIRMED,  # InitiateActionCallController "Placed In Queue"
    "oob_authenticate_api": _S.OOB_INITIATED,  # "/process/authenticate"
    "oob_status_poll": _S.CUSTOMER_RESPONSE_PENDING,  # "/process/status/" polling while waiting
    "oob_validate_api": _S.CUSTOMER_RESPONSE_RECEIVED,  # "/process/validate" -- validating the customer's OOB response
    "otp_success": _S.CUSTOMER_RESPONSE_RECEIVED,  # "OTP Processed Successfully"
    "cardinal_validate_response": _S.AUTH_COMPLETED,  # "ValidateCall response to cardinal" -- final validation outcome
    "vplus_result": _S.AUTH_COMPLETED,  # "ResponseCode=" -- final result code
}

VFLEX_STAGE_MAP: Dict[str, str] = {
    "vf_input": _S.REQUEST_RECEIVED,  # "StepupCall VF Input Message"
    "bank_request": _S.CARD_LOOKUP_SENT,  # "Get Data From Bank Payload"
    "bank_api_success_response": _S.CARD_LOOKUP_COMPLETED,  # a lookup ERROR response is not "completed" -- see module docstring
    "sms_input": _S.OTP_GENERATED,
    "sms_queue": _S.APPLICATION_QUEUE_CONFIRMED,
    "otp_success": _S.CUSTOMER_RESPONSE_RECEIVED,
    "netcetera_stepup_response": _S.AUTH_COMPLETED,  # "Stepup response to Netcetra" -- VFlex's own terminal record
}

DEBIT_STAGE_MAP: Dict[str, str] = {
    "request_body_json": _S.REQUEST_RECEIVED,
    "debit_request_json": _S.REQUEST_RECEIVED,
    "netcetera_response_json": _S.CUSTOMER_RESPONSE_RECEIVED,
    "msg_received_xml": _S.OTP_GENERATED,
    "sms_input_xml": _S.OTP_GENERATED,
    "email_xml": _S.OTP_GENERATED,
    "queue": _S.APPLICATION_QUEUE_CONFIRMED,
    "queue_msg_id": _S.APPLICATION_QUEUE_CONFIRMED,
    "otp_success": _S.CUSTOMER_RESPONSE_RECEIVED,
    "debit_response_json": _S.AUTH_COMPLETED,  # this portal's own terminal transaction record
}

NETCETERA_STAGE_MAP: Dict[str, str] = {
    "request_body": _S.REQUEST_RECEIVED,
    "vplus_input": _S.CHALLENGE_SELECTED,  # same wire event as Cardinal's vplus_input -- mapped identically, see module docstring
    "vplus_response": _S.PROCESSOR_RECEIVED,  # same wire event as Cardinal's vplus_response
    "sms_input": _S.OTP_GENERATED,
    "sms_queue": _S.APPLICATION_QUEUE_CONFIRMED,
    "email_message": _S.OTP_GENERATED,
    "otp_success": _S.CUSTOMER_RESPONSE_RECEIVED,
    "netcetera_response": _S.AUTH_COMPLETED,  # "Stepup responce to Netcetra" -- this family's own terminal record
}

OTP_PROCESSOR_STAGE_MAP: Dict[str, str] = {
    "msg_received_sms_xml": _S.REQUEST_RECEIVED,
    "sms_input_xml": _S.OTP_GENERATED,
    "email_xml": _S.OTP_GENERATED,
    "queue": _S.APPLICATION_QUEUE_CONFIRMED,
    "sms_queue_msg_id": _S.APPLICATION_QUEUE_CONFIRMED,
    "otp_success": _S.CUSTOMER_RESPONSE_RECEIVED,
    "force_verify_by_mobile": _S.CUSTOMER_RESPONSE_RECEIVED,
}

# Normalize every map's values to plain strings (LifecycleStage -> str) so
# comparisons against TimelineEntry.stage (a plain OptStr) are trivial.
_RAW_STAGE_MAPS: Dict[str, Dict[str, LifecycleStage]] = {
    LogFamily.CARDINAL.value: CARDINAL_STAGE_MAP,
    LogFamily.VFLEX.value: VFLEX_STAGE_MAP,
    LogFamily.DEBIT_PORTAL.value: DEBIT_STAGE_MAP,
    LogFamily.NETCETERA_VPLUS.value: NETCETERA_STAGE_MAP,
    LogFamily.OTP_PROCESSOR.value: OTP_PROCESSOR_STAGE_MAP,
}
STAGE_MAP_BY_FAMILY: Dict[str, Dict[str, str]] = {
    family: {event_type: stage.value for event_type, stage in mapping.items()}
    for family, mapping in _RAW_STAGE_MAPS.items()
}

# Derived: which families can EVER produce a given stage. A flow that has
# none of these families in its evidence can never observe that stage --
# NOT_OBSERVABLE, not MISSING (see reconstruct_lifecycle()).
STAGE_OBSERVABILITY: Dict[str, Set[str]] = {stage: set() for stage in MASTER_STAGE_ORDER}
for _family, _mapping in STAGE_MAP_BY_FAMILY.items():
    for _stage_value in _mapping.values():
        STAGE_OBSERVABILITY[_stage_value].add(_family)

# ---------------------------------------------------------------------------
# Business-outcome keyword classification (for terminal_status / rejected flows)
# ---------------------------------------------------------------------------

_POSITIVE_OUTCOME_KEYWORDS = ("SUCCESS", "SUCCEEDED", "APPROVED", "COMPLETED", "OK")
_NEGATIVE_OUTCOME_KEYWORDS = ("FAIL", "REJECT", "DECLIN", "DENIED", "ERROR", "TIMEOUT")


def _classify_outcome_word(word: Optional[str]) -> Optional[str]:
    """Returns "POSITIVE" | "NEGATIVE" | None (inconclusive, e.g. Cardinal's
    "CHECK" integrity status -- needs review, not a clean outcome either
    way) for a single business-status string."""
    if not word:
        return None
    upper = word.upper()
    if any(keyword in upper for keyword in _NEGATIVE_OUTCOME_KEYWORDS):
        return "NEGATIVE"
    if any(keyword in upper for keyword in _POSITIVE_OUTCOME_KEYWORDS):
        return "POSITIVE"
    return None


def _map_event_to_stage(event: NormalizedEvent) -> Optional[str]:
    family_map = STAGE_MAP_BY_FAMILY.get(event.log_family.value, {})
    return family_map.get(event.event_type or "")


def _sort_key(event: NormalizedEvent) -> Tuple[str, int]:
    return (event.event_timestamp or "", event.event_no)


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _infer_template(flow: CorrelatedFlow, mapped_stages: Set[str]) -> Optional[AuthTemplate]:
    """Priority: (1) an unambiguous authentication_method value already
    resolved by Phase 2/3; (2) structural evidence -- did any member event
    map to a stage that ONLY exists in one template's exclusive stage set."""
    method = (flow.authentication_method or "").upper()
    if method == "OTP":
        return AuthTemplate.OTP
    if method in ("OUTOFBAND", "OOB"):
        return AuthTemplate.OOB

    oob_signal_stages = _OTP_ONLY_STAGES | {LifecycleStage.CUSTOMER_RESPONSE_PENDING.value}
    if mapped_stages & oob_signal_stages:
        return AuthTemplate.OOB
    if mapped_stages & _OOB_ONLY_STAGES:
        return AuthTemplate.OTP
    return None


def _resolve_business_outcome(events_sorted: List[NormalizedEvent]) -> Optional[str]:
    """Scans backward (most recent first) for the latest known business
    outcome word -- terminal_status, then oob_status, then stepup_status,
    whichever this event has. Returns the classified POSITIVE/NEGATIVE/None."""
    for event in reversed(events_sorted):
        for field in ("terminal_status", "oob_status", "stepup_status"):
            value = getattr(event, field, None)
            classified = _classify_outcome_word(value)
            if classified:
                return classified
    return None


def reconstruct_lifecycle(flow: CorrelatedFlow, events: List[NormalizedEvent]) -> FlowLifecycle:
    """Deterministically reconstructs one CorrelatedFlow's lifecycle. Steps
    1-9 from the Phase 4 spec are implemented in order below."""
    linked_ids = set(flow.linked_event_ids)
    duplicate_ids = set(flow.duplicate_event_ids)
    # Step 1/2: chronological sort, event_no as tie-breaker (see _sort_key).
    # Exact duplicates (already identified by Phase 3) are excluded from
    # the timeline -- they'd otherwise double-count a single real event.
    member_events = [e for e in events if e.source_event_id in linked_ids and e.source_event_id not in duplicate_ids]
    events_sorted = sorted(member_events, key=_sort_key)

    if not events_sorted:
        return FlowLifecycle(flow_id=flow.flow_id, authentication_method=flow.authentication_method)

    # Step 3: chronological timeline, with each event mapped to a stage where possible.
    timeline: List[TimelineEntry] = []
    for event in events_sorted:
        stage = _map_event_to_stage(event)
        timeline.append(
            TimelineEntry(
                event_no=event.event_no,
                source_event_id=event.source_event_id,
                log_family=event.log_family.value,
                source_file=event.source_file,
                event_timestamp=event.event_timestamp,
                event_type=event.event_type,
                stage=stage,
                level=event.level,
                raw_reference=event.raw_reference,
            )
        )

    mapped_stage_values = {entry.stage for entry in timeline if entry.stage}
    auth_template = _infer_template(flow, mapped_stage_values)
    template_stages = TEMPLATES.get(auth_template.value) if auth_template else None

    # Step 4: furthest confirmed stage -- highest template-position stage observed.
    last_confirmed_stage: Optional[str] = None
    last_confirmed_index = -1
    first_seen_at: Dict[str, TimelineEntry] = {}
    last_seen_at: Dict[str, TimelineEntry] = {}
    if template_stages:
        stage_position = {stage: i for i, stage in enumerate(template_stages)}
        for entry in timeline:
            if entry.stage and entry.stage in stage_position:
                first_seen_at.setdefault(entry.stage, entry)
                last_seen_at[entry.stage] = entry
                if stage_position[entry.stage] > last_confirmed_index:
                    last_confirmed_index = stage_position[entry.stage]
                    last_confirmed_stage = entry.stage

    # Steps 6/7: missing vs not-observable expected stages, and the single
    # next-expected-stage summary fields.
    missing_stages: List[str] = []
    not_observable_stages: List[str] = []
    expected_next_stage: Optional[str] = None
    missing_next_stage: Optional[str] = None
    flow_families = set(flow.log_families)
    if template_stages:
        confirmed = set(first_seen_at.keys())
        for stage in template_stages:
            if stage in confirmed:
                continue
            observable_by = STAGE_OBSERVABILITY.get(stage, set())
            if observable_by & flow_families:
                missing_stages.append(stage)
            else:
                not_observable_stages.append(stage)

        remaining = template_stages[last_confirmed_index + 1 :]
        if remaining:
            expected_next_stage = remaining[0]
            observable_by = STAGE_OBSERVABILITY.get(expected_next_stage, set())
            if observable_by & flow_families:
                missing_next_stage = f"{expected_next_stage}: not found in captured logs"
            else:
                missing_next_stage = f"{expected_next_stage}: NOT_OBSERVABLE"

    # Step 9: gaps between consecutive CONFIRMED stages, in template order.
    stage_durations: List[StageDuration] = []
    if template_stages:
        confirmed_in_order = [s for s in template_stages if s in first_seen_at]
        for prev_stage, next_stage in zip(confirmed_in_order, confirmed_in_order[1:]):
            prev_entry, next_entry = first_seen_at[prev_stage], first_seen_at[next_stage]
            prev_ts, next_ts = _parse_ts(prev_entry.event_timestamp), _parse_ts(next_entry.event_timestamp)
            duration = (next_ts - prev_ts).total_seconds() if prev_ts and next_ts else None
            stage_durations.append(
                StageDuration(
                    from_stage=prev_stage,
                    to_stage=next_stage,
                    from_event_id=prev_entry.source_event_id,
                    to_event_id=next_entry.source_event_id,
                    duration_seconds=duration,
                )
            )

    # Step 5/8: terminal status and failure boundary.
    last_event_entry = timeline[-1]
    terminal_status, failure_boundary = _determine_terminal_status(
        auth_template=auth_template,
        last_confirmed_stage=last_confirmed_stage,
        events_sorted=events_sorted,
        timeline=timeline,
    )

    return FlowLifecycle(
        flow_id=flow.flow_id,
        authentication_method=flow.authentication_method,
        auth_template=auth_template,
        first_event=timeline[0],
        last_event=last_event_entry,
        timeline=timeline,
        last_confirmed_stage=last_confirmed_stage,
        expected_next_stage=expected_next_stage,
        missing_next_stage=missing_next_stage,
        missing_stages=missing_stages,
        not_observable_stages=not_observable_stages,
        terminal_status=terminal_status,
        failure_boundary=failure_boundary,
        stage_durations=stage_durations,
    )


def _determine_terminal_status(
    auth_template: Optional[AuthTemplate],
    last_confirmed_stage: Optional[str],
    events_sorted: List[NormalizedEvent],
    timeline: List[TimelineEntry],
) -> Tuple[TerminalStatus, Optional[FailureBoundary]]:
    if auth_template is None:
        return TerminalStatus.UNDETERMINED, None

    reached_auth_completed = last_confirmed_stage == LifecycleStage.AUTH_COMPLETED.value

    # Tier 1: an explicit, resolved business outcome (issuer/processor's own
    # decision) always wins -- this is what makes a REJECTED OOB challenge
    # distinct from a technical failure.
    business_outcome = _resolve_business_outcome(events_sorted)
    if business_outcome == "NEGATIVE":
        last_entry = timeline[-1]
        return TerminalStatus.FAILED, FailureBoundary(
            after_stage=last_confirmed_stage,
            at_event_id=last_entry.source_event_id,
            log_family=last_entry.log_family,
            event_type=last_entry.event_type,
            level=last_entry.level,
            reason="Business outcome resolved to a rejected/declined/failed status.",
        )
    if business_outcome == "POSITIVE" and reached_auth_completed:
        return TerminalStatus.SUCCESS, None

    # Tier 2: a technical error at the very end of the captured evidence.
    last_event = events_sorted[-1]
    if last_event.level in ("ERROR", "CRITICAL"):
        last_entry = timeline[-1]
        return TerminalStatus.FAILED, FailureBoundary(
            after_stage=last_confirmed_stage,
            at_event_id=last_entry.source_event_id,
            log_family=last_entry.log_family,
            event_type=last_entry.event_type,
            level=last_entry.level,
            reason=last_event.failure_signature or "Terminal event recorded at ERROR/CRITICAL level.",
        )

    if reached_auth_completed:
        return TerminalStatus.SUCCESS, None

    # Tier 3: OOB-specific -- still pending as of the last captured event is
    # NOT a failure, and must be reported as its own distinct status.
    if auth_template == AuthTemplate.OOB and last_confirmed_stage == LifecycleStage.CUSTOMER_RESPONSE_PENDING.value:
        return TerminalStatus.PENDING_AT_LOG_END, None

    return TerminalStatus.INCOMPLETE, None


def reconstruct_lifecycles(flows: List[CorrelatedFlow], events: List[NormalizedEvent]) -> List[FlowLifecycle]:
    return [reconstruct_lifecycle(flow, events) for flow in flows]
