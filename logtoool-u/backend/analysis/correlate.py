"""
Cross-Log Correlation Engine -- Phase 3 of the LLens Multi-Log Analysis
Strategy.

Takes the NormalizedEvents Phase 2 produces (backend/analysis/normalize.py)
across however many log families/files, and groups them into
CorrelatedFlows (backend/analysis/correlation_schema.py) -- one per
real-world transaction the engine can support with an EXACT identifier
match, in this exact priority order (highest first):

    0. exact duplicate (same family/tracker/event_type/timestamp/raw text)  -- HIGH
    1. transaction_id / ds_transaction_id                                   -- HIGH
    2. stepup_request_id                                                    -- HIGH
    3. tracker_no                                                           -- HIGH
    4. correlation_id                                                       -- HIGH
    5. tran_ref                                                             -- HIGH
    6. oob_tracker_id                                                       -- HIGH
    7. msg_id                                                               -- HIGH
    8. credential_id, validated against issuer_id + card_last4              -- MEDIUM, proposed link only
    9. issuer_id + card_last4 + amount + currency + close timestamp         -- MEDIUM, proposed link only
   10. masked_mobile / masked_email alone                                   -- LOW, informational only, NEVER merged

Deterministic only -- no LLM, no timestamp-only joins, no mobile/email used
as an automatic identity key (points 8-10 never merge flows; only points
0-7 do, and only on an EXACT string match). If two already-distinct flows
each carry a different value for the SAME identifier type when a match on
ANOTHER identifier type would otherwise merge them, the merge is refused
and a CorrelationConflict is recorded instead -- see _would_conflict().
"""
import hashlib
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("logtool.analysis.correlate")

from backend.analysis.correlation_schema import (
    CandidateLink,
    ConflictingIdentifier,
    CorrelatedFlow,
    CorrelationConfidence,
    CorrelationConflict,
    CorrelationKeyMatch,
    CorrelationResult,
    CorrelationStatus,
    EvidenceReference,
    LowConfidenceHint,
)
from backend.analysis.normalized_schema import NormalizedEvent

# Priority-ordered HIGH-confidence merge keys. "exact_duplicate" is a
# synthetic zeroth key (see _dedup_key()) -- the strongest possible signal
# two records are the same real-world event, so it's checked before any
# business identifier.
_DUPLICATE_KEY = "exact_duplicate"
IDENTIFIER_KEY_TYPES: Tuple[str, ...] = (
    "transaction_id",
    "stepup_request_id",
    "tracker_no",
    "correlation_id",
    "tran_ref",
    "oob_tracker_id",
    "msg_id",
)
HIGH_KEY_TYPES: Tuple[str, ...] = (_DUPLICATE_KEY,) + IDENTIFIER_KEY_TYPES

# Default window for the point-9 composite match (issuer + card_last4 +
# amount + currency + close timestamp) -- deliberately conservative and a
# named constant, same convention as vplus_monitoring.py's
# DEFAULT_GAP_THRESHOLD_MINUTES/DEFAULT_EXPECTED_RESPONSE_MS.
DEFAULT_COMPOSITE_WINDOW_SECONDS = 300

# Safety cap for the O(k²) pairwise comparison in _medium_confidence_links().
# During card-testing storms or other pathological datasets a single
# credential_id group can grow to thousands of flows.  Comparing every pair
# would be O(n²) within that group, potentially stalling the pipeline for
# minutes.  We skip groups larger than this and log a warning instead --
# the missed candidate links are informational-only (never auto-merged)
# so this is a safe degradation.
_MAX_PAIRWISE_GROUP_SIZE = 200


def _dedup_key(event: NormalizedEvent) -> Tuple[Any, ...]:
    """Two events are exact duplicates only if EVERY one of these agrees --
    same family, same tracker, same event type, same resolved timestamp,
    and byte-identical raw text. Anything less is a different event, not a
    duplicate."""
    return (event.log_family, event.tracker_no, event.event_type, event.event_timestamp, event.raw_reference)


def _high_key_value(event: NormalizedEvent, key_type: str) -> Optional[str]:
    if key_type == _DUPLICATE_KEY:
        return "|".join(str(part) for part in _dedup_key(event))
    if key_type == "transaction_id":
        return event.transaction_id or event.ds_transaction_id
    return getattr(event, key_type, None)


class _UnionFind:
    def __init__(self, n: int):
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        root = i
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[i] != root:
            self._parent[i], i = root, self._parent[i]
        return root

    def union(self, i: int, j: int) -> int:
        """Unions i and j, returns the resulting root."""
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return ri
        self._parent[ri] = rj
        return rj


def _short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8", errors="replace")).hexdigest()[:10]


# Priority rank of every HIGH-confidence key type, lower = stronger.
# exact_duplicate ranks strongest of all (it means byte-identical raw
# content), ahead of every business identifier.
_PRIORITY_INDEX: Dict[str, int] = {kt: i for i, kt in enumerate(HIGH_KEY_TYPES)}


def _disagreements(
    ids_a: Dict[str, str], ids_b: Dict[str, str], max_priority_index: Optional[int] = None
) -> List[ConflictingIdentifier]:
    """Compares two flows' already-established identifier maps
    (IDENTIFIER_KEY_TYPES only -- exact_duplicate is a merge trigger, not a
    persistent identity attribute). Any identifier type both sides have a
    non-null value for, where those values differ, is a disagreement.

    max_priority_index bounds the comparison to identifier types AT LEAST
    AS STRONG as the one that's proposing a merge (see _would_conflict()):
    a transaction_id match (the strongest identifier) must not be vetoed
    by two events legitimately carrying different tracker numbers for the
    same transaction -- e.g. Cardinal's own flow model tracks MULTIPLE
    tracker numbers per transaction, one per phase (SU/IA/VL). A
    lower-priority identifier disagreeing must not block a higher-priority
    match; the reverse (a stronger identifier disagreeing while a weaker
    one matched) IS a real conflict. Pass None (used for MEDIUM-link
    distinctness checks, not merge decisions) to compare every type."""
    conflicts = []
    for key_type in IDENTIFIER_KEY_TYPES:
        if max_priority_index is not None and _PRIORITY_INDEX[key_type] > max_priority_index:
            continue
        va, vb = ids_a.get(key_type), ids_b.get(key_type)
        if va and vb and va != vb:
            conflicts.append(ConflictingIdentifier(key_type=key_type, flow_a_value=va, flow_b_value=vb))
    return conflicts


def _would_conflict(ids_a: Dict[str, str], ids_b: Dict[str, str], trigger_key_type: str) -> List[ConflictingIdentifier]:
    """Merge-guard variant: only identifiers as strong or stronger than
    trigger_key_type can block the merge it's proposing."""
    return _disagreements(ids_a, ids_b, max_priority_index=_PRIORITY_INDEX[trigger_key_type])


def _sorted_by_time(events: List[NormalizedEvent]) -> List[NormalizedEvent]:
    return sorted(events, key=lambda e: (e.event_timestamp or "", e.event_no))


def _first_non_null(events_sorted: List[NormalizedEvent], field: str) -> Optional[Any]:
    for e in events_sorted:
        value = getattr(e, field, None)
        if value not in (None, ""):
            return value
    return None


def _last_non_null(events_sorted: List[NormalizedEvent], field: str) -> Optional[Any]:
    for e in reversed(events_sorted):
        value = getattr(e, field, None)
        if value not in (None, ""):
            return value
    return None


def _build_evidence_references(events: List[NormalizedEvent]) -> Tuple[List[EvidenceReference], List[str]]:
    """Groups member events by _dedup_key() -- exact duplicates collapse
    into one EvidenceReference (retaining every one of their event_ids and
    batch_ids), while every other distinct event gets its own entry.
    Returns (evidence_references, duplicate_event_ids)."""
    groups: Dict[Tuple[Any, ...], List[NormalizedEvent]] = defaultdict(list)
    order: List[Tuple[Any, ...]] = []
    for e in events:
        key = _dedup_key(e)
        if key not in groups:
            order.append(key)
        groups[key].append(e)

    references: List[EvidenceReference] = []
    duplicate_event_ids: List[str] = []
    for key in order:
        members = groups[key]
        first = members[0]
        references.append(
            EvidenceReference(
                log_family=first.log_family.value,
                source_file=first.source_file,
                event_type=first.event_type,
                event_timestamp=first.event_timestamp,
                raw_reference=first.raw_reference,
                source_event_ids=[m.source_event_id for m in members if m.source_event_id],
                batch_ids=sorted({m.batch_id for m in members if m.batch_id}),
            )
        )
        if len(members) > 1:
            duplicate_event_ids.extend(m.source_event_id for m in members[1:] if m.source_event_id)
    return references, duplicate_event_ids


def _determine_status(events: List[NormalizedEvent], has_conflict: bool) -> CorrelationStatus:
    if has_conflict:
        return CorrelationStatus.CONFLICT
    if len(events) <= 1:
        return CorrelationStatus.UNCORRELATED
    families = {e.log_family for e in events}
    has_terminal_status = any(e.terminal_status for e in events)
    if len(families) >= 2 and has_terminal_status:
        return CorrelationStatus.COMPLETE
    return CorrelationStatus.PARTIAL


def _make_flow_id(root_identifiers: Dict[str, str], events: List[NormalizedEvent]) -> str:
    for key_type in IDENTIFIER_KEY_TYPES:
        value = root_identifiers.get(key_type)
        if value:
            label = f"{key_type}:{value}"
            break
    else:
        label = "orphan" if len(events) == 1 else "group"
    member_ids = sorted(e.source_event_id or f"{e.source_file}:{e.event_no}" for e in events)
    return f"flow:{label}:{_short_hash(*member_ids)}"


def _assemble_flow(
    events: List[NormalizedEvent],
    root_identifiers: Dict[str, str],
    has_conflict: bool,
) -> CorrelatedFlow:
    events_sorted = _sorted_by_time(events)
    evidence_references, duplicate_event_ids = _build_evidence_references(events_sorted)
    credential_id = _first_non_null(events_sorted, "credential_id")

    correlation_keys = [
        CorrelationKeyMatch(key_type=key_type, value=value, confidence=CorrelationConfidence.HIGH)
        for key_type, value in root_identifiers.items()
        if value
    ]
    if credential_id:
        correlation_keys.append(
            CorrelationKeyMatch(key_type="credential_id", value=credential_id, confidence=CorrelationConfidence.MEDIUM)
        )

    status = _determine_status(events, has_conflict)
    if status == CorrelationStatus.UNCORRELATED:
        confidence = None
    elif status == CorrelationStatus.CONFLICT:
        confidence = CorrelationConfidence.HIGH  # the identifier that tried to merge it WAS a high-confidence match
    else:
        confidence = CorrelationConfidence.HIGH  # only HIGH-confidence keys (points 0-7) ever merge >1 event into a flow

    return CorrelatedFlow(
        flow_id=_make_flow_id(root_identifiers, events),
        transaction_id=root_identifiers.get("transaction_id"),
        ds_transaction_id=_first_non_null(events_sorted, "ds_transaction_id"),
        stepup_request_id=root_identifiers.get("stepup_request_id"),
        tracker_no=root_identifiers.get("tracker_no"),
        correlation_id=root_identifiers.get("correlation_id"),
        tran_ref=root_identifiers.get("tran_ref"),
        oob_tracker_id=root_identifiers.get("oob_tracker_id"),
        msg_id=root_identifiers.get("msg_id"),
        credential_id=credential_id,
        linked_event_ids=[e.source_event_id for e in events_sorted if e.source_event_id],
        duplicate_event_ids=duplicate_event_ids,
        source_files=sorted({e.source_file for e in events}),
        log_families=sorted({e.log_family.value for e in events}),
        correlation_keys=correlation_keys,
        evidence_references=evidence_references,
        correlation_confidence=confidence,
        correlation_status=status,
        first_timestamp=events_sorted[0].event_timestamp,
        last_timestamp=events_sorted[-1].event_timestamp,
        issuer_id=_first_non_null(events_sorted, "issuer_id"),
        bank_org=_first_non_null(events_sorted, "bank_org"),
        merchant_name=_first_non_null(events_sorted, "merchant_name"),
        merchant_id=_first_non_null(events_sorted, "merchant_id"),
        amount=_first_non_null(events_sorted, "amount"),
        currency=_first_non_null(events_sorted, "currency"),
        card_last4=_first_non_null(events_sorted, "card_last4"),
        channel=_first_non_null(events_sorted, "channel"),
        authentication_method=_first_non_null(events_sorted, "authentication_method"),
        current_lifecycle_stage=events_sorted[-1].normalized_stage,
        final_status=_last_non_null(events_sorted, "terminal_status"),
        failure_signature=_last_non_null(events_sorted, "failure_signature"),
    )


def _high_confidence_pass(
    events: List[NormalizedEvent],
) -> Tuple[_UnionFind, Dict[int, Dict[str, str]], List[CorrelationConflict]]:
    """Priority-ordered union-find over event indices. Returns the union-find
    structure, a {root_index: {key_type: value}} map of each root's
    currently-established identifiers (IDENTIFIER_KEY_TYPES only), and any
    conflicts hit along the way (root indices in the conflict are resolved
    to FINAL flow ids by the caller, after this whole pass completes)."""
    n = len(events)
    uf = _UnionFind(n)
    root_identifiers: Dict[int, Dict[str, str]] = {i: {} for i in range(n)}
    for i, event in enumerate(events):
        for key_type in IDENTIFIER_KEY_TYPES:
            value = _high_key_value(event, key_type)
            if value:
                root_identifiers[i][key_type] = value

    conflicts: List[CorrelationConflict] = []

    for key_type in HIGH_KEY_TYPES:
        groups: Dict[str, List[int]] = defaultdict(list)
        for i, event in enumerate(events):
            value = _high_key_value(event, key_type)
            if value:
                groups[value].append(i)

        for value, indices in groups.items():
            if len(indices) < 2:
                continue
            anchor = indices[0]
            seen_root_pairs = set()  # avoid re-recording the same conflict once per extra member sharing this value
            for other in indices[1:]:
                ra, rb = uf.find(anchor), uf.find(other)
                if ra == rb:
                    continue
                pair_key = (ra, rb) if ra < rb else (rb, ra)
                if pair_key in seen_root_pairs:
                    continue
                ids_a, ids_b = root_identifiers[ra], root_identifiers[rb]
                disagreements = _would_conflict(ids_a, ids_b, key_type)
                if disagreements:
                    seen_root_pairs.add(pair_key)
                    conflicts.append(
                        CorrelationConflict(
                            conflict_id=f"conflict:{key_type}:{_short_hash(value, str(ra), str(rb))}",
                            triggering_key_type=key_type,
                            triggering_value=value if key_type != _DUPLICATE_KEY else None,
                            conflicting_identifiers=disagreements,
                            affected_flow_ids=[],  # resolved to final flow_ids by the caller
                            source_event_ids_a=[events[i].source_event_id for i in _members_of(uf, ra, n) if events[i].source_event_id],
                            source_event_ids_b=[events[i].source_event_id for i in _members_of(uf, rb, n) if events[i].source_event_id],
                        )
                    )
                    continue
                new_root = uf.union(ra, rb)
                old_root = ra if new_root == rb else rb
                merged = dict(ids_b) if new_root == rb else dict(ids_a)
                other_ids = ids_a if new_root == rb else ids_b
                for kt, v in other_ids.items():
                    merged.setdefault(kt, v)
                root_identifiers[new_root] = merged
                del root_identifiers[old_root]

    return uf, root_identifiers, conflicts


def _members_of(uf: _UnionFind, root: int, n: int) -> List[int]:
    return [i for i in range(n) if uf.find(i) == root]


def _extra_identifier_pass(events: List[NormalizedEvent], uf: _UnionFind) -> List[CorrelationConflict]:
    """Additional union-find pass over NormalizedEvent.extra_identifiers --
    the generic bag non-payment-family events (LogFamily.GENERIC)
    populate via a profile's declared correlation_keys (see normalize.py),
    instead of the 7 fixed IDENTIFIER_KEY_TYPES the payment families use.
    Runs AFTER _high_confidence_pass and continues unioning its already-
    partially-merged state, so this is purely additive: every existing
    payment-family event has an empty extra_identifiers dict (only
    _normalize_generic_event() ever populates it), so the cheap early
    return below means this whole function is a no-op whenever no
    GENERIC-family events are present -- the 5 existing families'
    correlation output is completely unchanged.

    Every extra_identifiers key is treated as equal priority (there's no
    fixed strength ranking for arbitrary per-source declared keys, unlike
    HIGH_KEY_TYPES) -- a merge is refused (conflict recorded instead) if
    the two roots already disagree on ANY OTHER extra_identifier key,
    mirroring _would_conflict()'s safety principle. Recomputes each root's
    current identifiers from live union-find membership on every
    comparison rather than maintaining running bookkeeping across merges
    -- simpler and correct by construction, at the cost of some redundant
    work; acceptable since this only runs at all for non-payment-family
    traffic, not the bulk case."""
    n = len(events)
    if not any(e.extra_identifiers for e in events):
        return []

    conflicts: List[CorrelationConflict] = []
    all_keys = sorted({k for e in events for k, v in e.extra_identifiers.items() if v})

    for key_type in all_keys:
        groups: Dict[str, List[int]] = defaultdict(list)
        for i, event in enumerate(events):
            value = event.extra_identifiers.get(key_type)
            if value:
                groups[value].append(i)

        for value, indices in groups.items():
            if len(indices) < 2:
                continue
            anchor = indices[0]
            seen_root_pairs = set()
            for other in indices[1:]:
                ra, rb = uf.find(anchor), uf.find(other)
                if ra == rb:
                    continue
                pair_key = (ra, rb) if ra < rb else (rb, ra)
                if pair_key in seen_root_pairs:
                    continue

                members_a, members_b = _members_of(uf, ra, n), _members_of(uf, rb, n)
                ids_a = {k: v for i in members_a for k, v in events[i].extra_identifiers.items() if v}
                ids_b = {k: v for i in members_b for k, v in events[i].extra_identifiers.items() if v}
                disagreements = [
                    ConflictingIdentifier(key_type=k, flow_a_value=ids_a[k], flow_b_value=ids_b[k])
                    for k in sorted(set(ids_a) & set(ids_b))
                    if ids_a[k] != ids_b[k]
                ]
                if disagreements:
                    seen_root_pairs.add(pair_key)
                    conflicts.append(
                        CorrelationConflict(
                            conflict_id=f"conflict:{key_type}:{_short_hash(value, str(ra), str(rb))}",
                            triggering_key_type=key_type,
                            triggering_value=value,
                            conflicting_identifiers=disagreements,
                            affected_flow_ids=[],
                            source_event_ids_a=[events[i].source_event_id for i in members_a if events[i].source_event_id],
                            source_event_ids_b=[events[i].source_event_id for i in members_b if events[i].source_event_id],
                        )
                    )
                    continue
                uf.union(ra, rb)

    return conflicts


def _medium_confidence_links(
    flows: List[CorrelatedFlow], window_seconds: int
) -> List[CandidateLink]:
    """Points 8 and 9: proposed (never-merged) links between DISTINCT
    flows. A flow pair already sharing an equal, non-null high-confidence
    identifier is the same flow already (skipped); a flow pair with
    disagreeing non-null high-confidence identifiers is excluded (strong
    evidence they're genuinely different transactions, not just
    unconfirmed)."""
    links: List[CandidateLink] = []

    def _flow_identifiers(flow: CorrelatedFlow) -> Dict[str, str]:
        return {kt: getattr(flow, kt) for kt in IDENTIFIER_KEY_TYPES if getattr(flow, kt)}

    def _definitely_distinct(a: CorrelatedFlow, b: CorrelatedFlow) -> bool:
        # Unbounded here (unlike the merge guard): for suppressing a
        # speculative MEDIUM link, ANY disagreeing identifier -- even a
        # weak one -- is useful evidence the two flows are probably
        # unrelated, since no merge decision is being made.
        return bool(_disagreements(_flow_identifiers(a), _flow_identifiers(b)))

    # -- point 8: credential_id, validated against issuer_id + card_last4 --
    by_credential: Dict[str, List[CorrelatedFlow]] = defaultdict(list)
    for flow in flows:
        if flow.credential_id:
            by_credential[flow.credential_id].append(flow)
    for credential_id, group in by_credential.items():
        if len(group) > _MAX_PAIRWISE_GROUP_SIZE:
            logger.warning(
                "Skipping credential_id group %s: %d flows exceeds cap %d (card-testing storm?)",
                credential_id, len(group), _MAX_PAIRWISE_GROUP_SIZE,
            )
            continue
        for a_idx in range(len(group)):
            for b_idx in range(a_idx + 1, len(group)):
                a, b = group[a_idx], group[b_idx]
                if a.flow_id == b.flow_id or _definitely_distinct(a, b):
                    continue
                if not (a.issuer_id and b.issuer_id and a.issuer_id == b.issuer_id):
                    continue
                if not (a.card_last4 and b.card_last4 and a.card_last4 == b.card_last4):
                    continue
                links.append(
                    CandidateLink(
                        link_type="credential_id_validated",
                        flow_a_id=a.flow_id,
                        flow_b_id=b.flow_id,
                        matching_keys={"credential_id": credential_id, "issuer_id": a.issuer_id, "card_last4": a.card_last4},
                        note="Same credential_id, issuer_id, and card_last4 -- proposed link, not auto-merged.",
                    )
                )

    # -- point 9: issuer + card_last4 + amount + currency + close timestamp --
    by_business_context: Dict[Tuple[str, str, float, str], List[CorrelatedFlow]] = defaultdict(list)
    for flow in flows:
        if flow.issuer_id and flow.card_last4 and flow.amount is not None and flow.currency:
            by_business_context[(flow.issuer_id, flow.card_last4, flow.amount, flow.currency)].append(flow)
    for context_key, group in by_business_context.items():
        if len(group) > _MAX_PAIRWISE_GROUP_SIZE:
            logger.warning(
                "Skipping composite context group %s: %d flows exceeds cap %d",
                context_key, len(group), _MAX_PAIRWISE_GROUP_SIZE,
            )
            continue
        for a_idx in range(len(group)):
            for b_idx in range(a_idx + 1, len(group)):
                a, b = group[a_idx], group[b_idx]
                if a.flow_id == b.flow_id or _definitely_distinct(a, b):
                    continue
                delta = _timestamp_delta_seconds(a.first_timestamp, b.first_timestamp)
                if delta is None or delta > window_seconds:
                    continue
                issuer_id, card_last4, amount, currency = context_key
                links.append(
                    CandidateLink(
                        link_type="composite_business_context",
                        flow_a_id=a.flow_id,
                        flow_b_id=b.flow_id,
                        matching_keys={
                            "issuer_id": issuer_id,
                            "card_last4": card_last4,
                            "amount": amount,
                            "currency": currency,
                            "time_delta_seconds": delta,
                        },
                        note=(
                            f"Same issuer/card_last4/amount/currency within {window_seconds}s "
                            "-- proposed link, not auto-merged."
                        ),
                    )
                )

    return links


def _timestamp_delta_seconds(ts_a: Optional[str], ts_b: Optional[str]) -> Optional[float]:
    if not ts_a or not ts_b:
        return None
    try:
        from datetime import datetime

        a = datetime.fromisoformat(ts_a.replace("Z", "+00:00"))
        b = datetime.fromisoformat(ts_b.replace("Z", "+00:00"))
        return abs((a - b).total_seconds())
    except (ValueError, AttributeError):
        return None


def _low_confidence_hints(flows: List[CorrelatedFlow], events_by_flow: Dict[str, List[NormalizedEvent]]) -> List[LowConfidenceHint]:
    """Point 10: masked_mobile/masked_email shared across DISTINCT flows --
    informational only. Never merged, never proposed as a link -- see the
    module docstring's "never used as an automatic identity key" rule."""
    hints: List[LowConfidenceHint] = []
    for hint_type, field in (("masked_mobile", "masked_mobile"), ("masked_email", "masked_email")):
        by_value: Dict[str, set] = defaultdict(set)
        for flow in flows:
            for event in events_by_flow.get(flow.flow_id, []):
                value = getattr(event, field, None)
                if value:
                    by_value[value].add(flow.flow_id)
        for value, flow_ids in by_value.items():
            if len(flow_ids) > 1:
                hints.append(LowConfidenceHint(hint_type=hint_type, value=value, flow_ids=sorted(flow_ids)))
    return hints


def correlate_events(
    events: List[NormalizedEvent],
    composite_window_seconds: int = DEFAULT_COMPOSITE_WINDOW_SECONDS,
) -> CorrelationResult:
    """Entry point: groups NormalizedEvents into CorrelatedFlows plus
    conflict/candidate-link/low-confidence findings. Deterministic given
    the same input list in the same order."""
    if not events:
        return CorrelationResult()

    n = len(events)
    uf, root_identifiers, conflicts = _high_confidence_pass(events)
    conflicts = conflicts + _extra_identifier_pass(events, uf)

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)

    conflict_root_pairs: List[Tuple[int, int, CorrelationConflict]] = []
    # Re-resolve which FINAL roots each recorded conflict touches (their
    # provisional roots at record-time may have since merged further with
    # unrelated events via a later, non-conflicting key).
    event_id_to_index = {e.source_event_id: i for i, e in enumerate(events) if e.source_event_id}
    conflicted_root_set = set()
    for conflict in conflicts:
        idx_a = [event_id_to_index[eid] for eid in conflict.source_event_ids_a if eid in event_id_to_index]
        idx_b = [event_id_to_index[eid] for eid in conflict.source_event_ids_b if eid in event_id_to_index]
        if not idx_a or not idx_b:
            continue
        root_a, root_b = uf.find(idx_a[0]), uf.find(idx_b[0])
        conflict_root_pairs.append((root_a, root_b, conflict))
        conflicted_root_set.add(root_a)
        conflicted_root_set.add(root_b)

    root_to_flow: Dict[int, CorrelatedFlow] = {}
    for root, indices in groups.items():
        member_events = [events[i] for i in indices]
        flow = _assemble_flow(member_events, root_identifiers.get(root, {}), root in conflicted_root_set)
        root_to_flow[root] = flow

    for root_a, root_b, conflict in conflict_root_pairs:
        conflict.affected_flow_ids = sorted({root_to_flow[root_a].flow_id, root_to_flow[root_b].flow_id})

    flows = list(root_to_flow.values())
    events_by_flow = {root_to_flow[root].flow_id: [events[i] for i in indices] for root, indices in groups.items()}

    candidate_links = _medium_confidence_links(flows, composite_window_seconds)
    low_confidence_hints = _low_confidence_hints(flows, events_by_flow)

    return CorrelationResult(
        flows=flows,
        conflicts=conflicts,
        candidate_links=candidate_links,
        low_confidence_hints=low_confidence_hints,
    )
