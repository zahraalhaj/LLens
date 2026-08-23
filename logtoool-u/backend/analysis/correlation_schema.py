"""
Correlated Flow Model -- Phase 3 of the LLens Multi-Log Analysis Strategy.

Defines the output shapes for the cross-log correlation engine
(backend/analysis/correlate.py): one CorrelatedFlow per group of
NormalizedEvents (backend/analysis/normalized_schema.py) the engine
decided belong to the same real-world transaction/flow, plus the
CorrelationConflict/CandidateLink/LowConfidenceHint findings the engine
surfaces instead of silently guessing.

No dependency on backend/analysis/correlate.py or on any family module --
this is schema only, same split as normalized_schema.py/normalize.py in
Phase 2.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.analysis.normalized_schema import OptFloat, OptStr


class CorrelationConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CorrelationStatus(str, Enum):
    COMPLETE = "COMPLETE"  # correlated across >=2 log families AND a terminal status resolved
    PARTIAL = "PARTIAL"  # correlated (>=2 events) but single-family and/or no terminal status yet
    UNCORRELATED = "UNCORRELATED"  # a true orphan -- no other event shared any identifier with it
    CONFLICT = "CONFLICT"  # a high-confidence key match was blocked by a disagreeing identifier -- see CorrelationConflict


class CorrelationKeyMatch(BaseModel):
    """One identifier this flow is known by, and how confidently."""

    key_type: str
    value: OptStr = None
    confidence: CorrelationConfidence


class EvidenceReference(BaseModel):
    """One distinct piece of source evidence backing this flow. When
    multiple stored events are byte-identical duplicates of each other
    (same family/tracker/event_type/timestamp/raw text -- see
    correlate.py's _dedup_key()), they collapse into ONE evidence entry
    here, but every one of their event_ids/batch_ids is still retained in
    source_event_ids/batch_ids -- nothing is dropped, just de-duplicated
    for display."""

    log_family: str
    source_file: str
    event_type: OptStr = None
    event_timestamp: OptStr = None
    raw_reference: str
    source_event_ids: List[str] = Field(default_factory=list)
    batch_ids: List[str] = Field(default_factory=list)


class CorrelatedFlow(BaseModel):
    """One correlated transaction/flow, assembled from one or more
    NormalizedEvents the engine determined (via exact high-confidence
    identifier matches only -- see correlate.py) belong together."""

    flow_id: str

    # -- identifiers --
    transaction_id: OptStr = None
    ds_transaction_id: OptStr = None
    stepup_request_id: OptStr = None
    tracker_no: OptStr = None
    correlation_id: OptStr = None
    tran_ref: OptStr = None
    oob_tracker_id: OptStr = None
    msg_id: OptStr = None
    credential_id: OptStr = None  # coalesced for display/MEDIUM-matching only -- never used to merge (see correlate.py)

    # -- membership / traceability --
    linked_event_ids: List[str] = Field(default_factory=list)  # every source_event_id, full list, duplicates included
    duplicate_event_ids: List[str] = Field(default_factory=list)  # subset of linked_event_ids identified as exact duplicates
    source_files: List[str] = Field(default_factory=list)
    log_families: List[str] = Field(default_factory=list)
    correlation_keys: List[CorrelationKeyMatch] = Field(default_factory=list)
    evidence_references: List[EvidenceReference] = Field(default_factory=list)

    # -- confidence / status --
    correlation_confidence: Optional[CorrelationConfidence] = None  # None only for a true UNCORRELATED singleton
    correlation_status: CorrelationStatus

    # -- timestamps --
    first_timestamp: OptStr = None
    last_timestamp: OptStr = None

    # -- business context (first non-null across member events, earliest-timestamp-first) --
    issuer_id: OptStr = None
    bank_org: OptStr = None
    merchant_name: OptStr = None
    merchant_id: OptStr = None
    amount: OptFloat = None
    currency: OptStr = None
    card_last4: OptStr = None
    channel: OptStr = None

    # -- authentication / lifecycle / outcome --
    authentication_method: OptStr = None
    current_lifecycle_stage: OptStr = None  # normalized_stage of the most recent member event
    final_status: OptStr = None  # most recent non-null terminal_status across member events
    failure_signature: OptStr = None  # most recent non-null failure_signature across member events


class ConflictingIdentifier(BaseModel):
    key_type: str
    flow_a_value: OptStr = None
    flow_b_value: OptStr = None


class CorrelationConflict(BaseModel):
    """A high-confidence key matched across two already-distinct flows,
    but another high-confidence identifier disagreed between them -- the
    engine did NOT merge them. This is a finding to surface for human
    review, not something to silently resolve."""

    conflict_id: str
    triggering_key_type: str
    triggering_value: OptStr = None
    conflicting_identifiers: List[ConflictingIdentifier] = Field(default_factory=list)
    affected_flow_ids: List[str] = Field(default_factory=list)
    source_event_ids_a: List[str] = Field(default_factory=list)
    source_event_ids_b: List[str] = Field(default_factory=list)


class CandidateLink(BaseModel):
    """A MEDIUM-confidence proposed relationship between two flows that
    were NOT merged -- point 6 (credential_id, issuer/card_last4-validated)
    or point 7 (issuer+card_last4+amount+currency+close timestamp) of the
    Phase 3 correlation priority. Always a suggestion for human review,
    never an automatic merge."""

    link_type: str  # "credential_id_validated" | "composite_business_context"
    confidence: CorrelationConfidence = CorrelationConfidence.MEDIUM
    flow_a_id: str
    flow_b_id: str
    matching_keys: dict = Field(default_factory=dict)
    note: str


class LowConfidenceHint(BaseModel):
    """A LOW-confidence observation (shared masked mobile/email only) --
    surfaced for visibility, NEVER used to merge or even propose a link.
    Deliberately carries only the masked value, matching the
    sensitive-data policy already enforced at normalization (Phase 2)."""

    hint_type: str  # "masked_mobile" | "masked_email"
    value: OptStr = None
    flow_ids: List[str] = Field(default_factory=list)
    note: str = "Shared contact identifier only -- insufficient for automatic correlation."


class CorrelationResult(BaseModel):
    flows: List[CorrelatedFlow] = Field(default_factory=list)
    conflicts: List[CorrelationConflict] = Field(default_factory=list)
    candidate_links: List[CandidateLink] = Field(default_factory=list)
    low_confidence_hints: List[LowConfidenceHint] = Field(default_factory=list)
