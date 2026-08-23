"""
Data-Quality & Analytical-Confidence Monitoring -- Phase 9 of the LLens
Multi-Log Analysis Strategy.

Measures whether the pipeline itself (Phases 2-3) has enough reliable
evidence to support what's built on top of it (Phases 4-8). Operates on
NormalizedEvents, CorrelatedFlows, and a CorrelationResult -- never re-reads
raw log files.

No LLM anywhere in this module. Every count, check, and score is a
deterministic computation.

FIELD CONSISTENCY: normalizes formatting before comparing (case, whitespace,
numeric representation) and treats a null-vs-value pairing as
MISSING_VALUE, never MISMATCH -- only genuinely conflicting non-null
values are a MISMATCH. See _normalize_field_value() and
check_field_consistency().

SENSITIVE DATA: scan_for_sensitive_data() is the one place in this whole
system that deliberately reads `raw_reference` (every other analysis/LLM
module is built to never need it -- see Phase 2's masking guarantee and
Phase 7's build_llm_safe_timeline()). It exists specifically to AUDIT for
leftover raw sensitive content, and its own output is held to the same
rule as everywhere else: no raw value is ever stored in a
SensitiveDataFinding, only a category, a non-reversible hash reference,
and (only where Phase 2's own masking utilities already define a safe
partial representation) a safe_hint.
"""
import hashlib
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from backend.analysis.correlation_schema import CorrelatedFlow, CorrelationResult
from backend.analysis.normalized_schema import NormalizedEvent
from backend.analysis.quality_schema import (
    CorrelationQualityBreakdown,
    CorrelationQualityCounts,
    DataQualityScorecard,
    EvidenceQualityCounts,
    ExceptionEntry,
    FieldCheckStatus,
    FieldConsistencyResult,
    ParseQualityCounts,
    QualityAnalysisResult,
    SensitiveDataCategory,
    SensitiveDataFinding,
)

FIELD_CONSISTENCY_CHECKS: tuple = (
    "transaction_id",
    "stepup_request_id",
    "masked_mobile",
    "masked_email",
    "amount",
    "currency",
    "merchant_name",
    "card_last4",
    "issuer_id",
)

_DISPLAY_NAME_BY_FIELD = {
    "transaction_id": "TransactionId",
    "stepup_request_id": "StepupRequestId",
    "masked_mobile": "mobile",
    "masked_email": "email",
    "amount": "amount",
    "currency": "currency",
    "merchant_name": "merchant",
    "card_last4": "card_last4",
    "issuer_id": "issuer",
}


def _normalize_field_value(field_name: str, value) -> Optional[str]:
    """Formatting normalization BEFORE comparison -- case/whitespace for
    text fields, numeric rounding for amount. Returns None only when the
    input itself was null (callers must never compare a None)."""
    if value is None or value == "":
        return None
    if field_name == "amount":
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value).strip()
    if field_name in ("currency", "card_last4"):
        return str(value).strip().upper()
    if field_name == "merchant_name":
        return " ".join(str(value).split()).strip().lower()
    return str(value).strip()


def check_field_consistency(flow: CorrelatedFlow, events: List[NormalizedEvent]) -> List[FieldConsistencyResult]:
    """Only compares non-null values -- a field present on some events and
    absent on others is MISSING_VALUE, never MISMATCH (per the Phase 9
    spec: "a null-versus-value difference is NOT a mismatch")."""
    linked_ids = set(flow.linked_event_ids)
    member_events = [e for e in events if e.source_event_id in linked_ids]

    results: List[FieldConsistencyResult] = []
    for field_name in FIELD_CONSISTENCY_CHECKS:
        values_by_normalized: Dict[str, set] = defaultdict(set)
        event_ids_by_normalized: Dict[str, List[str]] = defaultdict(list)
        for event in member_events:
            raw_value = getattr(event, field_name, None)
            normalized = _normalize_field_value(field_name, raw_value)
            if normalized is None:
                continue
            values_by_normalized[normalized].add(str(raw_value))
            event_ids_by_normalized[normalized].append(event.source_event_id)

        display_name = _DISPLAY_NAME_BY_FIELD[field_name]
        if not values_by_normalized:
            results.append(
                FieldConsistencyResult(flow_id=flow.flow_id, field_name=display_name, status=FieldCheckStatus.MISSING_VALUE)
            )
        elif len(values_by_normalized) == 1:
            results.append(FieldConsistencyResult(flow_id=flow.flow_id, field_name=display_name, status=FieldCheckStatus.CONSISTENT))
        else:
            results.append(
                FieldConsistencyResult(
                    flow_id=flow.flow_id,
                    field_name=display_name,
                    status=FieldCheckStatus.MISMATCH,
                    distinct_values=sorted(values_by_normalized.keys()),
                    event_ids_by_value={k: v for k, v in event_ids_by_normalized.items()},
                )
            )
    return results


# ---------------------------------------------------------------------------
# Sensitive-data scanning -- the one module that reads raw_reference
# ---------------------------------------------------------------------------

_PAN_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_OTP_RE = re.compile(r"\botp\b[^0-9]{0,20}(\d{4,8})\b", re.IGNORECASE)
_SECRET_RE = re.compile(r"\b(password|secret|verificationtoken|apikey|api_key|auth_token)\b[\"'\s:=]{1,5}([A-Za-z0-9\-_.]{6,})", re.IGNORECASE)
_MOBILE_RE = re.compile(r"\b\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b")


def _hash_reference(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def scan_for_sensitive_data(events: List[NormalizedEvent]) -> List[SensitiveDataFinding]:
    """Scans raw_reference for patterns that look like a PAN, OTP, full
    mobile number, full email, or a secret/token -- and NEVER stores the
    matched text. Only a category, a non-reversible hash reference, and
    (PAN only) a last-4 safe_hint using the same convention Phase 2's own
    extract_card_last4() already established survive into the finding."""
    findings: List[SensitiveDataFinding] = []

    for event in events:
        text = event.raw_reference or ""
        if not text:
            continue

        for match in _EMAIL_RE.finditer(text):
            findings.append(
                SensitiveDataFinding(
                    category=SensitiveDataCategory.FULL_EMAIL,
                    event_id=event.source_event_id,
                    source_file=event.source_file,
                    protected_reference=_hash_reference(match.group()),
                )
            )

        for match in _OTP_RE.finditer(text):
            findings.append(
                SensitiveDataFinding(
                    category=SensitiveDataCategory.OTP,
                    event_id=event.source_event_id,
                    source_file=event.source_file,
                    protected_reference=_hash_reference(match.group(1)),
                )
            )

        for match in _SECRET_RE.finditer(text):
            findings.append(
                SensitiveDataFinding(
                    category=SensitiveDataCategory.SECRET,
                    event_id=event.source_event_id,
                    source_file=event.source_file,
                    protected_reference=_hash_reference(match.group(2)),
                )
            )

        for match in _PAN_RE.finditer(text):
            digits = re.sub(r"\D", "", match.group())
            if len(digits) < 13:
                continue
            findings.append(
                SensitiveDataFinding(
                    category=SensitiveDataCategory.PAN,
                    event_id=event.source_event_id,
                    source_file=event.source_file,
                    protected_reference=_hash_reference(digits),
                    safe_hint=f"ends in {digits[-4:]}",
                )
            )

        for match in _MOBILE_RE.finditer(text):
            digits = re.sub(r"\D", "", match.group())
            if len(digits) < 8:  # short numeric sequences are too ambiguous to call a phone number
                continue
            findings.append(
                SensitiveDataFinding(
                    category=SensitiveDataCategory.FULL_MOBILE,
                    event_id=event.source_event_id,
                    source_file=event.source_file,
                    protected_reference=_hash_reference(digits),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Correlation quality breakdown
# ---------------------------------------------------------------------------


def compute_correlation_quality(flows: List[CorrelatedFlow]) -> CorrelationQualityBreakdown:
    by_family: Dict[str, Counter] = defaultdict(Counter)
    by_file: Dict[str, Counter] = defaultdict(Counter)
    by_confidence: Counter = Counter()

    for flow in flows:
        status = flow.correlation_status.value
        for family in flow.log_families:
            by_family[family][status] += 1
        for source_file in flow.source_files:
            by_file[source_file][status] += 1
        confidence_key = flow.correlation_confidence.value if flow.correlation_confidence else "NONE"
        by_confidence[confidence_key] += 1

    return CorrelationQualityBreakdown(
        by_log_family={family: dict(counts) for family, counts in by_family.items()},
        by_source_file={f: dict(counts) for f, counts in by_file.items()},
        by_confidence=dict(by_confidence),
    )


# ---------------------------------------------------------------------------
# Overall score -- deterministic composite, fully transparent breakdown
# ---------------------------------------------------------------------------


def compute_overall_score(scorecard: DataQualityScorecard) -> tuple:
    """A simple, fully-transparent weighted composite -- not a black box.
    Each component is a rate already bounded to [0, 1]; missing
    denominators contribute their component as 100 (nothing to penalize)
    rather than 0 (which would wrongly imply total failure)."""
    total_events = scorecard.total_events_analyzed or 1
    total_flows = scorecard.total_flows_analyzed or 1

    parse_component = 100.0 * scorecard.parse_quality.parse_success / total_events
    correlation_component = 100.0 * scorecard.flow_classification_counts.get("COMPLETE", 0) / total_flows if scorecard.total_flows_analyzed else 100.0
    consistency_penalty = 100.0 * scorecard.field_mismatch_count / total_flows if scorecard.total_flows_analyzed else 100.0
    consistency_component = max(0.0, 100.0 - consistency_penalty)
    conflict_penalty = 100.0 * scorecard.correlation_quality.correlation_conflicts / total_flows if scorecard.total_flows_analyzed else 0.0
    conflict_component = max(0.0, 100.0 - conflict_penalty)

    breakdown = {
        "parse_success_rate": round(parse_component, 2),
        "flow_completeness_rate": round(correlation_component, 2),
        "field_consistency": round(consistency_component, 2),
        "conflict_free_rate": round(conflict_component, 2),
    }
    overall = round(sum(breakdown.values()) / len(breakdown), 2)
    return overall, breakdown


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def analyze_data_quality(
    events: List[NormalizedEvent],
    flows: List[CorrelatedFlow],
    correlation_result: Optional[CorrelationResult] = None,
) -> QualityAnalysisResult:
    events_by_flow: Dict[str, List[NormalizedEvent]] = defaultdict(list)
    event_by_id = {e.source_event_id: e for e in events if e.source_event_id}
    for flow in flows:
        events_by_flow[flow.flow_id] = [event_by_id[eid] for eid in flow.linked_event_ids if eid in event_by_id]

    # -- parse quality --
    parse_quality = ParseQualityCounts()
    for event in events:
        if event.parse_status == "parsed":
            parse_quality.parse_success += 1
        elif event.parse_status == "partial":
            parse_quality.partial_parsing += 1
        elif event.parse_status == "failed":
            parse_quality.parse_failure += 1
        if event.used_fallback_parsing:
            parse_quality.fallback_parsing += 1

    # -- evidence quality --
    evidence_quality = EvidenceQualityCounts()
    uncorrelated_flow_ids = {f.flow_id for f in flows if f.correlation_status.value == "UNCORRELATED"}
    for event in events:
        has_identifier = bool(event.tracker_no or event.transaction_id or event.correlation_id)
        if not has_identifier:
            evidence_quality.missing_identifiers += 1
        if not event.event_timestamp:
            evidence_quality.missing_timestamps += 1
        if not event.merchant_name:
            evidence_quality.unknown_merchant += 1
    for flow in flows:
        member_events = events_by_flow[flow.flow_id]
        if flow.flow_id not in uncorrelated_flow_ids:
            continue
        evidence_quality.uncorrelated_events += len(member_events)
        # Phase 3 marks EVERY singleton flow UNCORRELATED, whether or not
        # it carries an identifier -- uncorrelated_events counts all of
        # them, while unmatched_events is the more specific, non-exclusive
        # subset that DID carry an identifier and still found no partner
        # (as opposed to having nothing to match on in the first place,
        # which is what missing_identifiers already covers).
        if len(flow.linked_event_ids) == 1 and member_events:
            solo_event = member_events[0]
            if solo_event.tracker_no or solo_event.transaction_id or solo_event.correlation_id:
                evidence_quality.unmatched_events += 1

    # -- correlation quality --
    correlation_quality = CorrelationQualityCounts()
    if correlation_result:
        correlation_quality.correlation_conflicts = len(correlation_result.conflicts)
        correlation_quality.low_confidence_correlations = len(correlation_result.candidate_links) + len(
            correlation_result.low_confidence_hints
        )

    # -- flow classification --
    flow_classification_counts: Counter = Counter(f.correlation_status.value for f in flows)

    # -- field consistency --
    field_consistency: List[FieldConsistencyResult] = []
    for flow in flows:
        field_consistency.extend(check_field_consistency(flow, events_by_flow[flow.flow_id]))
    field_mismatch_count = sum(1 for r in field_consistency if r.status == FieldCheckStatus.MISMATCH)

    # -- sensitive data --
    sensitive_findings = scan_for_sensitive_data(events)

    scorecard = DataQualityScorecard(
        total_events_analyzed=len(events),
        total_flows_analyzed=len(flows),
        parse_quality=parse_quality,
        evidence_quality=evidence_quality,
        correlation_quality=correlation_quality,
        flow_classification_counts=dict(flow_classification_counts),
        field_mismatch_count=field_mismatch_count,
        sensitive_data_finding_count=len(sensitive_findings),
    )
    scorecard.overall_score, scorecard.score_breakdown = compute_overall_score(scorecard)

    exception_table: List[ExceptionEntry] = []
    for event in events:
        if event.parse_status == "failed":
            exception_table.append(
                ExceptionEntry(
                    category="parse_failure",
                    description=f"Event failed to parse (failure_signature={event.failure_signature or 'none'}).",
                    event_id=event.source_event_id,
                    source_file=event.source_file,
                )
            )
    for result in field_consistency:
        if result.status == FieldCheckStatus.MISMATCH:
            exception_table.append(
                ExceptionEntry(
                    category="field_mismatch",
                    description=f"{result.field_name} disagrees across this flow's evidence: {result.distinct_values}.",
                    flow_id=result.flow_id,
                )
            )
    for flow in flows:
        if flow.correlation_status.value == "CONFLICT":
            exception_table.append(
                ExceptionEntry(category="correlation_conflict", description="Flow involved in an unresolved correlation conflict.", flow_id=flow.flow_id)
            )
    for finding in sensitive_findings:
        exception_table.append(
            ExceptionEntry(
                category="sensitive_data",
                description=f"{finding.category.value} pattern detected in raw log text (see protected_reference, not the raw value).",
                event_id=finding.event_id,
                source_file=finding.source_file,
            )
        )

    return QualityAnalysisResult(
        scorecard=scorecard,
        correlation_quality_breakdown=compute_correlation_quality(flows),
        field_consistency=field_consistency,
        sensitive_data_findings=sensitive_findings,
        exception_table=exception_table,
    )
