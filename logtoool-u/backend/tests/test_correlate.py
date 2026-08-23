"""
Tests for the Phase 3 cross-log correlation engine:
backend/analysis/correlate.py + backend/analysis/correlation_schema.py.

Two layers of test data are used deliberately:
- _ne(...) builds NormalizedEvent instances directly -- precise, decoupled
  from Phase 2's family-specific mapping quirks, used for engine-focused
  unit tests (priority order, conflict detection, dedup, medium/low
  confidence).
- The realistic end-to-end examples at the bottom go through
  normalize_event() on raw canonical-event dicts per family, proving
  Phase 2 -> Phase 3 actually connects for each supported log family.
"""
from backend.analysis.correlate import correlate_events
from backend.analysis.correlation_schema import CorrelationConfidence, CorrelationStatus
from backend.analysis.normalize import normalize_event
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent


def _ne(event_no, source_event_id, log_family=LogFamily.CARDINAL, ts="2026-08-20T10:00:00Z", **overrides):
    defaults = dict(
        source_file="sample.log",
        log_family=log_family,
        event_no=event_no,
        raw_reference=f"raw-{source_event_id}",
        source_event_id=source_event_id,
        batch_id="batch-1",
        event_timestamp=ts,
        event_type="some_event",
    )
    defaults.update(overrides)
    return NormalizedEvent(**defaults)


def _flow_for(result, event_id):
    for flow in result.flows:
        if event_id in flow.linked_event_ids:
            return flow
    return None


# ---------------------------------------------------------------------------
# Priority order: exact TransactionId
# ---------------------------------------------------------------------------

def test_exact_transaction_id_match_merges_across_families():
    a = _ne(1, "a", log_family=LogFamily.CARDINAL, transaction_id="TXN1")
    b = _ne(1, "b", log_family=LogFamily.NETCETERA_VPLUS, transaction_id="TXN1", ts="2026-08-20T10:00:05Z")
    result = correlate_events([a, b])

    assert len(result.flows) == 1
    flow = result.flows[0]
    assert sorted(flow.linked_event_ids) == ["a", "b"]
    assert flow.transaction_id == "TXN1"
    assert flow.correlation_confidence == CorrelationConfidence.HIGH
    assert set(flow.log_families) == {LogFamily.CARDINAL.value, LogFamily.NETCETERA_VPLUS.value}


def test_ds_transaction_id_also_merges_when_transaction_id_absent():
    a = _ne(1, "a", ds_transaction_id="DS1")
    b = _ne(1, "b", ds_transaction_id="DS1")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert sorted(result.flows[0].linked_event_ids) == ["a", "b"]


# ---------------------------------------------------------------------------
# Priority order: StepupRequestId
# ---------------------------------------------------------------------------

def test_stepup_request_id_match_merges_when_no_transaction_id():
    a = _ne(1, "a", stepup_request_id="SREQ1")
    b = _ne(1, "b", stepup_request_id="SREQ1", ts="2026-08-20T10:00:02Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert result.flows[0].stepup_request_id == "SREQ1"
    assert result.flows[0].correlation_confidence == CorrelationConfidence.HIGH


def test_transaction_id_takes_priority_over_stepup_request_id_for_flow_labeling():
    """Both identifiers present and consistent -- the flow should surface
    both, but transaction_id (priority 1) is the anchor identifier."""
    a = _ne(1, "a", transaction_id="TXN9", stepup_request_id="SREQ9")
    b = _ne(1, "b", transaction_id="TXN9", stepup_request_id="SREQ9")
    result = correlate_events([a, b])
    flow = result.flows[0]
    assert flow.transaction_id == "TXN9"
    assert flow.stepup_request_id == "SREQ9"


# ---------------------------------------------------------------------------
# Priority order: Tracker No
# ---------------------------------------------------------------------------

def test_tracker_no_match_merges_when_no_stronger_identifier():
    a = _ne(1, "a", log_family=LogFamily.OTP_PROCESSOR, tracker_no="IA1001")
    b = _ne(2, "b", log_family=LogFamily.OTP_PROCESSOR, tracker_no="IA1001", ts="2026-08-20T10:00:03Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert result.flows[0].tracker_no == "IA1001"


# ---------------------------------------------------------------------------
# Priority order: correlation_id (VFlex-style) and tran_ref
# ---------------------------------------------------------------------------

def test_vflex_correlation_id_match_merges():
    a = _ne(1, "a", log_family=LogFamily.VFLEX, correlation_id="CORR-77")
    b = _ne(2, "b", log_family=LogFamily.VFLEX, correlation_id="CORR-77", ts="2026-08-20T10:00:01Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert result.flows[0].correlation_id == "CORR-77"
    assert result.flows[0].correlation_confidence == CorrelationConfidence.HIGH


def test_tran_ref_match_merges():
    a = _ne(1, "a", log_family=LogFamily.VFLEX, tran_ref="BREF-42")
    b = _ne(2, "b", log_family=LogFamily.VFLEX, tran_ref="BREF-42", ts="2026-08-20T10:00:04Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert result.flows[0].tran_ref == "BREF-42"


# ---------------------------------------------------------------------------
# Priority order: OOB Tracker ID / MsgId
# ---------------------------------------------------------------------------

def test_oob_tracker_id_match_merges():
    a = _ne(1, "a", log_family=LogFamily.CARDINAL, oob_tracker_id="OOB-5")
    b = _ne(2, "b", log_family=LogFamily.CARDINAL, oob_tracker_id="OOB-5", ts="2026-08-20T10:00:09Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert result.flows[0].oob_tracker_id == "OOB-5"


def test_msg_id_matching_merges():
    a = _ne(1, "a", log_family=LogFamily.OTP_PROCESSOR, msg_id="MSGID-1")
    b = _ne(2, "b", log_family=LogFamily.OTP_PROCESSOR, msg_id="MSGID-1", ts="2026-08-20T10:00:02Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert result.flows[0].msg_id == "MSGID-1"
    assert result.flows[0].correlation_confidence == CorrelationConfidence.HIGH


# ---------------------------------------------------------------------------
# Priority ordering itself: a lower-priority shared key must not prevent a
# higher-priority identifier from being the one that defines the flow.
# ---------------------------------------------------------------------------

def test_higher_priority_identifier_still_merges_even_when_lower_priority_ones_differ():
    """transaction_id matches (priority 1) even though tracker_no differs
    between the two events (a legitimate scenario -- e.g. a retry used a
    new tracker for the same business transaction)."""
    a = _ne(1, "a", transaction_id="TXN5", tracker_no="SU1")
    b = _ne(2, "b", transaction_id="TXN5", tracker_no="SU2", ts="2026-08-20T10:00:30Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 1
    assert result.flows[0].correlation_status != CorrelationStatus.CONFLICT


# ---------------------------------------------------------------------------
# Conflicting high-confidence identifiers -- must NOT merge
# ---------------------------------------------------------------------------

def test_conflicting_transaction_ids_via_shared_tracker_are_not_merged():
    """A and B share transaction_id TXN1 (merge). C and D share a
    different transaction_id TXN2 (merge). But B and C share the SAME
    tracker_no -- a lower-priority identifier -- while their higher-
    priority transaction_ids disagree. The engine must refuse to merge
    the two groups and record a CORRELATION_CONFLICT instead."""
    a = _ne(1, "a", transaction_id="TXN1", tracker_no="SU-SHARED")
    b = _ne(2, "b", transaction_id="TXN1", tracker_no="SU-SHARED", ts="2026-08-20T10:00:01Z")
    c = _ne(3, "c", transaction_id="TXN2", tracker_no="SU-SHARED", ts="2026-08-20T10:05:00Z")
    d = _ne(4, "d", transaction_id="TXN2", tracker_no="SU-SHARED", ts="2026-08-20T10:05:01Z")

    result = correlate_events([a, b, c, d])

    assert len(result.flows) == 2
    for flow in result.flows:
        assert flow.correlation_status == CorrelationStatus.CONFLICT

    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.triggering_key_type == "tracker_no"
    assert conflict.triggering_value == "SU-SHARED"
    conflicting_types = {ci.key_type for ci in conflict.conflicting_identifiers}
    assert "transaction_id" in conflicting_types
    assert sorted(conflict.source_event_ids_a) == ["a", "b"]
    assert sorted(conflict.source_event_ids_b) == ["c", "d"]
    assert len(conflict.affected_flow_ids) == 2


def test_conflict_finding_records_exact_conflicting_values():
    a = _ne(1, "a", transaction_id="TXN-ALPHA", tracker_no="SHARED")
    b = _ne(2, "b", transaction_id="TXN-BETA", tracker_no="SHARED", ts="2026-08-20T10:00:01Z")
    result = correlate_events([a, b])

    assert len(result.flows) == 2
    assert len(result.conflicts) == 1
    ci = result.conflicts[0].conflicting_identifiers[0]
    assert ci.key_type == "transaction_id"
    assert {ci.flow_a_value, ci.flow_b_value} == {"TXN-ALPHA", "TXN-BETA"}


def test_conflict_does_not_prevent_correlation_of_unrelated_events():
    """A conflict between two specific flows must not affect an entirely
    separate, cleanly-correlated third flow."""
    a = _ne(1, "a", transaction_id="TXN1", tracker_no="SHARED")
    b = _ne(2, "b", transaction_id="TXN2", tracker_no="SHARED", ts="2026-08-20T10:00:01Z")
    c = _ne(3, "c", transaction_id="TXN-CLEAN")
    d = _ne(4, "d", transaction_id="TXN-CLEAN", ts="2026-08-20T11:00:00Z")

    result = correlate_events([a, b, c, d])
    assert len(result.flows) == 3
    clean_flow = _flow_for(result, "c")
    assert clean_flow.correlation_status != CorrelationStatus.CONFLICT
    assert sorted(clean_flow.linked_event_ids) == ["c", "d"]


# ---------------------------------------------------------------------------
# Medium-confidence matching
# ---------------------------------------------------------------------------

def test_credential_id_medium_match_validated_by_issuer_and_card_last4():
    a = _ne(1, "a", credential_id="CRED1", issuer_id="ISS1", card_last4="1234")
    b = _ne(2, "b", credential_id="CRED1", issuer_id="ISS1", card_last4="1234", ts="2026-08-20T10:30:00Z")
    result = correlate_events([a, b])

    # never auto-merged
    assert len(result.flows) == 2
    assert len(result.candidate_links) == 1
    link = result.candidate_links[0]
    assert link.link_type == "credential_id_validated"
    assert link.confidence == CorrelationConfidence.MEDIUM
    assert link.matching_keys["credential_id"] == "CRED1"


def test_credential_id_alone_without_issuer_or_card_last4_does_not_link():
    a = _ne(1, "a", credential_id="CRED1")
    b = _ne(2, "b", credential_id="CRED1", ts="2026-08-20T10:30:00Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 2
    assert result.candidate_links == []


def test_composite_business_context_medium_match_within_time_window():
    a = _ne(1, "a", issuer_id="ISS9", card_last4="9999", amount=25.0, currency="USD")
    b = _ne(2, "b", issuer_id="ISS9", card_last4="9999", amount=25.0, currency="USD", ts="2026-08-20T10:02:00Z")
    result = correlate_events([a, b], composite_window_seconds=300)

    assert len(result.flows) == 2  # proposed link only, never merged
    assert len(result.candidate_links) == 1
    link = result.candidate_links[0]
    assert link.link_type == "composite_business_context"
    assert link.matching_keys["time_delta_seconds"] == 120.0


def test_composite_business_context_outside_time_window_does_not_link():
    a = _ne(1, "a", issuer_id="ISS9", card_last4="9999", amount=25.0, currency="USD", ts="2026-08-20T10:00:00Z")
    b = _ne(2, "b", issuer_id="ISS9", card_last4="9999", amount=25.0, currency="USD", ts="2026-08-20T12:00:00Z")
    result = correlate_events([a, b], composite_window_seconds=300)
    assert result.candidate_links == []


def test_composite_match_suppressed_when_flows_already_definitely_distinct():
    """Two flows with different, resolved transaction_ids are definitely
    different transactions -- even if their business context happens to
    match, no candidate link should be proposed (it would be noise)."""
    a = _ne(1, "a", transaction_id="TXN-A", issuer_id="ISS9", card_last4="9999", amount=25.0, currency="USD")
    b = _ne(
        2, "b", transaction_id="TXN-B", issuer_id="ISS9", card_last4="9999", amount=25.0, currency="USD",
        ts="2026-08-20T10:01:00Z",
    )
    result = correlate_events([a, b])
    assert result.candidate_links == []


# ---------------------------------------------------------------------------
# Low-confidence candidate: mobile/email/timestamp alone
# ---------------------------------------------------------------------------

def test_shared_mobile_alone_never_merges_and_is_surfaced_as_low_confidence_hint():
    a = _ne(1, "a", transaction_id="TXN1", masked_mobile="*******89")
    b = _ne(2, "b", transaction_id="TXN2", masked_mobile="*******89", ts="2026-08-20T14:00:00Z")
    result = correlate_events([a, b])

    assert len(result.flows) == 2
    assert result.candidate_links == []  # never even a proposed link
    assert len(result.low_confidence_hints) == 1
    hint = result.low_confidence_hints[0]
    assert hint.hint_type == "masked_mobile"
    assert hint.value == "*******89"
    assert set(hint.flow_ids) == {_flow_for(result, "a").flow_id, _flow_for(result, "b").flow_id}


def test_shared_email_alone_never_merges():
    a = _ne(1, "a", transaction_id="TXN1", masked_email="j***@example.com")
    b = _ne(2, "b", transaction_id="TXN2", masked_email="j***@example.com", ts="2026-08-20T15:00:00Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 2
    assert result.candidate_links == []


def test_timestamp_alone_never_merges_two_otherwise_unidentified_events():
    """No shared identifier of any kind, only near-identical timestamps --
    must remain fully uncorrelated, not even a candidate link."""
    a = _ne(1, "a", ts="2026-08-20T10:00:00Z")
    b = _ne(2, "b", ts="2026-08-20T10:00:01Z")
    result = correlate_events([a, b])
    assert len(result.flows) == 2
    assert result.candidate_links == []
    for flow in result.flows:
        assert flow.correlation_status == CorrelationStatus.UNCORRELATED


# ---------------------------------------------------------------------------
# Duplicate events
# ---------------------------------------------------------------------------

def test_exact_duplicate_events_collapse_but_retain_all_source_references():
    a = _ne(1, "a", transaction_id="TXN1", tracker_no="SU1", raw_reference="identical text")
    a_dup = _ne(
        1, "a-dup", transaction_id="TXN1", tracker_no="SU1", raw_reference="identical text", batch_id="batch-2"
    )
    result = correlate_events([a, a_dup])

    assert len(result.flows) == 1
    flow = result.flows[0]
    assert sorted(flow.linked_event_ids) == ["a", "a-dup"]
    assert flow.duplicate_event_ids == ["a-dup"]
    assert len(flow.evidence_references) == 1
    ref = flow.evidence_references[0]
    assert sorted(ref.source_event_ids) == ["a", "a-dup"]
    assert sorted(ref.batch_ids) == ["batch-1", "batch-2"]


def test_near_duplicate_with_different_raw_text_is_not_collapsed():
    """Same tracker/timestamp but genuinely different raw content (e.g.
    two distinct log lines for the same transaction) must NOT be treated
    as a duplicate -- both stay as separate evidence entries."""
    a = _ne(1, "a", transaction_id="TXN1", raw_reference="request sent")
    b = _ne(1, "b", transaction_id="TXN1", raw_reference="response received")
    result = correlate_events([a, b])

    flow = result.flows[0]
    assert flow.duplicate_event_ids == []
    assert len(flow.evidence_references) == 2


def test_duplicate_of_an_orphan_event_still_collapses():
    a = _ne(1, "a", raw_reference="garbled unparsed content", event_type="unparsed")
    a_dup = _ne(1, "a-dup", raw_reference="garbled unparsed content", event_type="unparsed")
    result = correlate_events([a, a_dup])

    assert len(result.flows) == 1
    flow = result.flows[0]
    assert sorted(flow.linked_event_ids) == ["a", "a-dup"]
    assert flow.duplicate_event_ids == ["a-dup"]
    # 2 collapsed-duplicate events correlated only via the dedup pass is
    # itself a real correlation (not a singleton), so PARTIAL not
    # UNCORRELATED -- both events are still one log family though.
    assert flow.correlation_status == CorrelationStatus.PARTIAL


# ---------------------------------------------------------------------------
# Orphan events
# ---------------------------------------------------------------------------

def test_orphan_event_with_no_identifiers_is_uncorrelated():
    a = _ne(1, "solo")
    result = correlate_events([a])
    assert len(result.flows) == 1
    flow = result.flows[0]
    assert flow.linked_event_ids == ["solo"]
    assert flow.correlation_status == CorrelationStatus.UNCORRELATED
    assert flow.correlation_confidence is None


def test_orphan_among_correlated_events_stays_separate():
    a = _ne(1, "a", transaction_id="TXN1")
    b = _ne(2, "b", transaction_id="TXN1", ts="2026-08-20T10:00:05Z")
    orphan = _ne(3, "orphan", ts="2026-08-20T10:10:00Z")
    result = correlate_events([a, b, orphan])

    assert len(result.flows) == 2
    orphan_flow = _flow_for(result, "orphan")
    assert orphan_flow.correlation_status == CorrelationStatus.UNCORRELATED
    assert orphan_flow.linked_event_ids == ["orphan"]


# ---------------------------------------------------------------------------
# Unrelated transactions with similar timestamps
# ---------------------------------------------------------------------------

def test_unrelated_transactions_with_near_identical_timestamps_stay_separate():
    a = _ne(1, "a", transaction_id="TXN-X", ts="2026-08-20T10:00:00Z")
    b = _ne(2, "b", transaction_id="TXN-Y", ts="2026-08-20T10:00:00Z")
    c = _ne(3, "c", tracker_no="SU-Z", ts="2026-08-20T10:00:00Z")
    result = correlate_events([a, b, c])

    assert len(result.flows) == 3
    assert result.conflicts == []
    assert result.candidate_links == []
    ids = {flow.flow_id for flow in result.flows}
    assert len(ids) == 3  # deterministic distinct flow ids, no accidental collision


def test_unrelated_transactions_same_business_context_but_far_apart_in_time():
    a = _ne(1, "a", issuer_id="ISS1", card_last4="1234", amount=100.0, currency="USD", ts="2026-08-20T09:00:00Z")
    b = _ne(2, "b", issuer_id="ISS1", card_last4="1234", amount=100.0, currency="USD", ts="2026-08-21T09:00:00Z")
    result = correlate_events([a, b], composite_window_seconds=300)
    assert len(result.flows) == 2
    assert result.candidate_links == []


# ---------------------------------------------------------------------------
# Determinism / status transitions
# ---------------------------------------------------------------------------

def test_correlate_events_is_deterministic_across_repeated_calls():
    a = _ne(1, "a", transaction_id="TXN1")
    b = _ne(2, "b", transaction_id="TXN1", ts="2026-08-20T10:00:01Z")
    r1 = correlate_events([a, b])
    r2 = correlate_events([a, b])
    assert r1.model_dump() == r2.model_dump()


def test_empty_input_returns_empty_result():
    result = correlate_events([])
    assert result.flows == []
    assert result.conflicts == []
    assert result.candidate_links == []
    assert result.low_confidence_hints == []


def test_complete_status_requires_multi_family_and_terminal_status():
    single_family = [
        _ne(1, "a", log_family=LogFamily.CARDINAL, transaction_id="TXN1", terminal_status="OK"),
        _ne(2, "b", log_family=LogFamily.CARDINAL, transaction_id="TXN1", ts="2026-08-20T10:00:01Z"),
    ]
    result = correlate_events(single_family)
    assert result.flows[0].correlation_status == CorrelationStatus.PARTIAL  # single family, no COMPLETE

    multi_family_no_terminal = [
        _ne(1, "a", log_family=LogFamily.CARDINAL, transaction_id="TXN2"),
        _ne(2, "b", log_family=LogFamily.NETCETERA_VPLUS, transaction_id="TXN2", ts="2026-08-20T10:00:01Z"),
    ]
    result2 = correlate_events(multi_family_no_terminal)
    assert result2.flows[0].correlation_status == CorrelationStatus.PARTIAL  # multi-family but no terminal status

    multi_family_with_terminal = [
        _ne(1, "a", log_family=LogFamily.CARDINAL, transaction_id="TXN3"),
        _ne(2, "b", log_family=LogFamily.NETCETERA_VPLUS, transaction_id="TXN3", ts="2026-08-20T10:00:01Z", terminal_status="SUCCESS"),
    ]
    result3 = correlate_events(multi_family_with_terminal)
    assert result3.flows[0].correlation_status == CorrelationStatus.COMPLETE


# ---------------------------------------------------------------------------
# Realistic end-to-end examples -- Phase 2 normalize_event() -> Phase 3 correlate_events()
# ---------------------------------------------------------------------------

def _canonical(source_system, event_id, ts, component, correlation_id, details, level="INFO", raw=None):
    return {
        "event_id": event_id,
        "batch_id": "batch-demo",
        "file_name": f"{source_system}.log",
        "line_no": 1,
        "ts_utc": ts,
        "level": level,
        "source_system": source_system,
        "component": component,
        "message": "demo",
        "raw": raw or f"raw-{event_id}",
        "attributes": {"correlation_id": correlation_id, "details": details},
    }


def test_realistic_cardinal_to_netcetera_correlation_via_transaction_id():
    """A single 3DS StepUp transaction: Cardinal resolves it via its OOB
    flow, Netcetera/V+ resolves the same transaction independently -- both
    logs share the same TransactionId, exactly as they would in
    production."""
    cardinal_evt = normalize_event(
        _canonical(
            "cardinal_stepup_oob_log", "card-1", "2026-08-21T09:00:00Z", "vplus_response", "TXN-REAL-1",
            {
                "phase": "STEP_UP",
                "parse_status": "parsed",
                "flow": {
                    "transaction_id": "TXN-REAL-1",
                    "trackers": ["SU7001"],
                    "issuer_id": "ISS-BANK-A",
                    "authentication": {"type": "OTP", "status": "SUCCESS"},
                    "integrity_status": "OK",
                },
            },
        )
    )
    netcetera_evt = normalize_event(
        _canonical(
            "afs_netcetera_3ds_stepup", "nc-1", "2026-08-21T09:00:03Z", "stepup_message", "TXN-REAL-1",
            {
                "tracker_no": "SU7001",
                "transaction": {
                    "transaction_id": "TXN-REAL-1",
                    "issuer_id": "ISS-BANK-A",
                    "derived": {"has_stepup": True, "is_success": True},
                    "stepup_status": "SUCCESS",
                },
            },
        )
    )
    result = correlate_events([cardinal_evt, netcetera_evt])

    assert len(result.flows) == 1
    flow = result.flows[0]
    assert flow.transaction_id == "TXN-REAL-1"
    assert flow.correlation_status == CorrelationStatus.COMPLETE
    assert set(flow.log_families) == {"cardinal_stepup_oob_log", "afs_netcetera_3ds_stepup"}
    assert flow.issuer_id == "ISS-BANK-A"


def test_realistic_debit_and_otp_processor_stay_separate_without_shared_identifier():
    """Debit Portal and OTP Processor logs for two genuinely unrelated
    customer actions must not be correlated just because they're close in
    time -- no shared identifier exists between them."""
    debit_evt = normalize_event(
        _canonical(
            "debit_portal_log", "debit-1", "2026-08-21T09:00:00Z", "debit_response", "TXND-9",
            {"parse_status": "parsed", "transaction": {"transaction_id": "TXND-9", "trackers": ["IA9001"], "issuer_id": "ISS-B"}},
        )
    )
    otp_evt = normalize_event(
        _canonical(
            "otp_online_processor", "otp-1", "2026-08-21T09:00:02Z", "otp_success", None,
            {"tracker_no": "IA5555", "record": {"tracker_no": "IA5555", "otp_processed": True}},
        )
    )
    result = correlate_events([debit_evt, otp_evt])
    assert len(result.flows) == 2
    assert result.conflicts == []


def test_realistic_vflex_conflict_scenario():
    """Two VFlex transactions that happen to reuse the same tracker number
    (a plausible data anomaly) but have distinct transaction_ids -- must
    surface as a conflict, not merge."""
    e1 = normalize_event(
        _canonical(
            "vflex_transaction_log", "vf-1", "2026-08-21T09:00:00Z", "bank_api_response", "TXNV-1",
            {"parse_status": "parsed", "transaction": {"transaction_id": "TXNV-1", "tracker_no": "SU-REUSED"}},
        )
    )
    e2 = normalize_event(
        _canonical(
            "vflex_transaction_log", "vf-2", "2026-08-21T09:00:01Z", "bank_api_response", "TXNV-2",
            {"parse_status": "parsed", "transaction": {"transaction_id": "TXNV-2", "tracker_no": "SU-REUSED"}},
        )
    )
    result = correlate_events([e1, e2])
    assert len(result.flows) == 2
    assert len(result.conflicts) == 1
    assert result.conflicts[0].triggering_key_type == "tracker_no"
