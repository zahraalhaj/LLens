// These types mirror backend/core/schema.py and the FastAPI route response
// shapes exactly (backend/api/routes/*.py). Keep them in sync when either
// side changes -- there's no shared codegen between Python and TS here.

export type LogLevel = 'CRITICAL' | 'ERROR' | 'WARN' | 'INFO' | 'DEBUG' | 'UNKNOWN';

export type TsConfidence = 'parsed' | 'assumed_utc' | 'assumed_local' | 'unparseable';

export type ProfileType = 'regex' | 'json' | 'delimited';

export interface MultilineConfig {
  mode: string;
  pattern?: string | null;
}

export interface ParserProfile {
  name: string;
  type: ProfileType;
  pattern: string;
  timestamp_field: string;
  timestamp_format?: string | null;
  level_field: string;
  component_field?: string | null;
  message_field: string;
  source_system_field?: string | null;
  default_source_system: string;
  timezone?: string | null;
  multiline_config?: MultilineConfig;
  min_match_ratio: number;
  level_map?: Record<string, string> | null;
  delimiter_fields?: string[] | null;
  correlation_keys?: string[] | null;
}

export interface LogEvent {
  event_id: string;
  batch_id: string;
  file_name: string;
  line_no: number;
  raw: string;
  ts_utc: string | null;
  ts_raw: string;
  ts_confidence: TsConfidence;
  level: LogLevel;
  source_system: string;
  component?: string | null;
  message: string;
  attributes: Record<string, any>;
}

export interface IngestionSummary {
  batch_id: string;
  file_name: string;
  total_lines: number;
  grouped_events_count: number;
  parsed_events_count: number;
  matched_profile: string;
  match_ratio: number;
  warnings: string[];
}

export interface Batch {
  batch_id: string;
  file_name: string;
  file_size_bytes: number;
  total_events: number;
  matched_profile: string | null;
  match_ratio: number;
  uploaded_at: string;
}

export interface LogStats {
  severity_counts: Partial<Record<LogLevel, number>>;
  source_distribution: Record<string, number>;
  hourly_distribution: Array<{ hour: string; level: LogLevel; count: number }>;
  batches: Batch[];
}

export interface AnomalyFlag {
  name: string;
  count: number;
  z_score: number;
}

// Honest heuristic report -- NOT a trained ML model. See
// backend/core/profiling.py for why this is worded this way.
export interface AnomalyReport {
  method: 'heuristic_zscore';
  description: string;
  total_events: number;
  error_ratio: number;
  flagged_components: AnomalyFlag[];
  flagged_hours: AnomalyFlag[];
}

export interface AIExplanation {
  probable_cause: string;
  explanation: string;
  suggested_next_steps: string[];
  confidence: 'High' | 'Medium' | 'Low';
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'assistant';
  text: string;
  sql_query?: string;
  results?: Record<string, any>[];
  timestamp: string;
}

export interface AlertRule {
  rule_id: string;
  name: string;
  enabled: boolean;
  trigger_type: 'severity' | 'anomaly';
  min_level: string;
  source_system_filter: string | null;
  component_filter: string | null;
  message_contains: string | null;
  mode: 'immediate' | 'digest';
  dedup_window_minutes: number;
  recipients: string | null;
  notification_group_id: string | null;
  dedup_windows: Record<string, number> | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationGroup {
  group_id: string;
  name: string;
  emails: string;
  created_at: string;
  updated_at: string;
}

export interface ActiveAlert {
  dedup_key: string;
  status: 'firing' | 'acknowledged' | 'resolved';
  rule_id: string | null;
  rule_name: string | null;
  source_system: string | null;
  component: string | null;
  message_snippet: string | null;
  correlation_field: string | null;
  correlation_value: string | null;
  last_fired_at: string;
  first_fired_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by_user_id: string | null;
  resolved_at: string | null;
  resolved_by_user_id: string | null;
}

export interface AlertDispatchLogEntry {
  dispatch_id: string;
  rule_id: string | null;
  rule_name: string;
  batch_id: string | null;
  triggered_at: string;
  recipient: string;
  subject: string;
  success: boolean;
  status_message: string | null;
  event_count: number;
  correlation_field: string | null;
  correlation_value: string | null;
}

export interface AlertDispatchHistory {
  total: number;
  page: number;
  page_size: number;
  entries: AlertDispatchLogEntry[];
}

export interface User {
  user_id: string;
  username: string;
  role: 'admin' | 'member';
  created_at?: string;
  is_active?: boolean;
  must_change_password?: boolean;
}

export type MachineAuthType = 'password' | 'key';

export interface RemoteMachine {
  machine_id: string;
  label: string;
  host: string;
  port: number;
  username: string;
  auth_type: MachineAuthType;
  remote_directory: string;
  recursive: boolean;
  poll_interval_minutes: number;
  enabled: boolean;
  host_key_fingerprint: string | null;
  created_at: string;
  last_polled_at: string | null;
  last_status: 'success' | 'error' | null;
  last_error: string | null;
  last_files_ingested: number | null;
}

export interface PollResult {
  machine_id: string;
  files_found: number;
  files_ingested: number;
  files_unchanged: number;
  files_rotated: number;
  total_events_ingested: number;
  errors: string[];
}

// -- V+/StepUp monitoring (backend/analysis/vplus_monitoring.py) -----------

export interface VPlusDowntimeWindow {
  down_since: string;
  recovered_at: string | null;
  duration_minutes: number | null;
  unresponded_count: number;
  sample_tracker_no: string | null;
}

export interface VPlusUnrespondedInput {
  correlation_id: string;
  tracker_no: string | null;
  input_time: string;
}

export interface VPlusAvailabilityReport {
  status: 'healthy' | 'down' | 'no_data';
  message?: string;
  expected_response_ms: number;
  gap_threshold_minutes?: number;
  window_start?: string;
  window_end?: string;
  total_inputs_analyzed?: number;
  responded_count?: number;
  unresponded_count?: number;
  unresponded_pct?: number;
  delayed_count?: number;
  delayed_pct?: number;
  downtime_windows: VPlusDowntimeWindow[];
  total_downtime_minutes?: number;
  currently_down: boolean | null;
  worst_unresponded?: VPlusUnrespondedInput[];
}

export interface VPlusResponsePair {
  correlation_id: string;
  input_time: string;
  response_time: string;
  response_time_ms: number;
  is_delayed: boolean;
  tracker_no: string | null;
}

export interface VPlusResponseTimeReport {
  status: 'ok' | 'no_data';
  message?: string;
  expected_response_ms: number;
  total_pairs_analyzed?: number;
  stats?: { total_ms: number; avg_ms: number; min_ms: number; max_ms: number };
  delayed_count?: number;
  delayed_pct?: number;
  worst_delays?: VPlusResponsePair[];
}

export interface VPlusSmsResult {
  correlation_id: string;
  sms_input_time: string;
  queued_time: string | null;
  queue_delay_ms: number | null;
  is_queue_delayed: boolean;
  otp_confirmed_time: string | null;
  completion_delay_ms: number | null;
  outcome: 'otp_confirmed' | 'queued_no_confirmation' | 'queue_failed';
}

export interface VPlusSmsReport {
  status: 'ok' | 'no_data';
  message?: string;
  aggregator_note?: string;
  expected_queue_ms: number;
  total_sms_transactions?: number;
  outcome_counts?: Record<string, number>;
  queue_delay_stats?: { total_ms: number; avg_ms: number; min_ms: number; max_ms: number; delayed_pct: number } | null;
  unresolved?: VPlusSmsResult[];
}

export interface VPlusFinding {
  severity: 'critical' | 'high' | 'medium' | 'low';
  finding: string;
}

export interface VPlusInvestigationSummary {
  generated_at: string;
  total_events_analyzed: number;
  total_errors: number;
  top_findings: VPlusFinding[];
  error_message_frequency: Record<string, number>;
  most_affected_merchants: Record<string, number>;
  most_affected_issuers: Record<string, number>;
}

export interface VPlusTransactionBreakdown {
  status: 'ok' | 'no_data';
  message?: string;
  total_transactions?: number;
  issuer_counts?: Record<string, number>;
  status_counts?: Record<string, number>;
  currency_counts?: Record<string, number>;
}

export interface VPlusFullReport {
  investigation_summary: VPlusInvestigationSummary;
  availability: VPlusAvailabilityReport;
  response_times: VPlusResponseTimeReport;
  sms_analysis: VPlusSmsReport;
  transaction_breakdown: VPlusTransactionBreakdown;
  available_merchants: string[];
}

// -- OTP Online Processor analysis (backend/analysis/otp_processor.py) -----

export interface OtpFailedEvent {
  timestamp: string | null;
  tracker_no: string | null;
  reason: string;
  message: string;
}

export interface OtpFailedEventsReport {
  count: number;
  reason_counts: Record<string, number>;
  items: OtpFailedEvent[];
}

export interface OtpProcessorSummary {
  status: 'ok' | 'no_data';
  message?: string;
  window_start?: string;
  window_end?: string;
  total_events_analyzed?: number;
  total_records?: number;
  event_type_counts?: Record<string, number>;
  by_org?: Record<string, number>;
  by_queue?: Record<string, number>;
  by_currency?: Record<string, number>;
  top_merchants?: Record<string, number>;
  available_merchants?: string[];
  otp_processed_count?: number;
  otp_success_rate_pct?: number | null;
  force_verify_count?: number;
  failed_events?: OtpFailedEventsReport;
}

// -- Shared failed-event shape reused across the Debit/Cardinal/VFlex report types --

export interface GenericFailedEvent {
  timestamp: string | null;
  correlation_id: string | null;
  reason: string;
  message: string;
}

export interface GenericFailedEventsReport {
  count: number;
  reason_counts: Record<string, number>;
  items: GenericFailedEvent[];
}

// -- Debit Portal analysis (backend/analysis/debit_portal.py) --------------

export interface DebitPortalSummary {
  status: 'ok' | 'no_data';
  message?: string;
  window_start?: string;
  window_end?: string;
  total_events_analyzed?: number;
  total_records?: number;
  event_type_counts?: Record<string, number>;
  by_issuer?: Record<string, number>;
  by_status?: Record<string, number>;
  by_currency?: Record<string, number>;
  top_merchants?: Record<string, number>;
  available_merchants?: string[];
  otp_processed_count?: number;
  checks_needed_count?: number;
  failed_events?: GenericFailedEventsReport;
}

// -- Cardinal analysis (backend/analysis/cardinal.py) -----------------------

export interface CardinalSummary {
  status: 'ok' | 'no_data';
  message?: string;
  window_start?: string;
  window_end?: string;
  total_events_analyzed?: number;
  total_flows?: number;
  event_type_counts?: Record<string, number>;
  by_issuer?: Record<string, number>;
  by_status?: Record<string, number>;
  by_bank_org?: Record<string, number>;
  by_currency?: Record<string, number>;
  oob_status_counts?: Record<string, number>;
  top_merchants?: Record<string, number>;
  available_merchants?: string[];
  otp_processed_count?: number;
  checks_needed_count?: number;
  failed_events?: GenericFailedEventsReport;
}

// -- VFlex analysis (backend/analysis/vflex.py) ------------------------------

// -- Phase 10 analytics dashboards (backend/analysis/dashboards.py) -------
// These mirror backend/analysis/*_schema.py's model_dump() shapes exactly
// as returned by /api/analytics/*.

export interface EvidenceSample {
  flow_id: string;
  event_ids: string[];
  source_files: string[];
}

export interface TopFailureSignature {
  pattern: string;
  rank: number | null;
  affected_flows: number;
  total_flows: number;
  failure_rate: number;
  low_sample_warning: string | null;
  representative_evidence: EvidenceSample[];
}

export interface FunnelStage {
  stage: string;
  flow_count: number;
  sample_flow_ids: string[];
}

export interface IssuerFailureRate {
  issuer_id: string;
  total_flows: number;
  failed_flows: number;
  failure_rate: number | null;
}

export interface FailureHeatmapCell {
  day_of_week: number; // 0=Monday .. 6=Sunday
  hour: number; // 0-23
  failed_count: number;
  total_count: number;
}

export interface ServiceOverview {
  total_flows: number;
  success: number;
  error: number;
  pending: number;
  incomplete: number;
  outcome_trend: Array<Record<string, string | number>>;
  otp_oob_mix: Record<string, number>;
  top_failure_signatures: TopFailureSignature[];
  lifecycle_funnel: FunnelStage[];
  missing_stage_counts: FunnelStage[];
  issuer_failure_rates: IssuerFailureRate[];
  failure_heatmap: FailureHeatmapCell[];
}

export interface DependencyMetricView {
  request_volume: number;
  success_rate: number | null;
  error_rate: number | null;
  median_latency_ms: number | null;
  p95_latency_ms: number | null;
  timeout_count: number;
  incomplete_count: number;
  note: string | null;
}

export interface DependencyHealth {
  dependencies: Record<string, DependencyMetricView>;
  queues: Record<string, number>;
}

export interface DependencyTrendBucket {
  bucket_start: string;
  volume: number;
  median_latency_ms: number | null;
  p95_latency_ms: number | null;
  error_rate: number | null;
  incomplete_rate: number | null;
}

export interface OobDurationSample {
  latency_ms: number;
  outcome: string;
  request_event_id: string | null;
  response_event_id: string | null;
  join_key: string | null;
}

export interface QueueMessaging {
  generated: number;
  application_queued: number;
  processor_received: number;
  downstream_routed: number;
  validated: number;
  unmatched: number;
  orphan: number;
  queue_distribution: Record<string, number>;
  handoff_latency: Array<{ from_stage: string; to_stage: string; sample_count: number; median_ms: number | null; p95_ms: number | null }>;
  unmatched_tracker_nos: string[];
  orphan_tracker_nos: string[];
}

export type SearchField = 'transaction_id' | 'tracker' | 'stepup_request_id' | 'mobile' | 'card_last4' | 'msg_id' | 'correlation_id' | 'merchant_name';

// A drill-through target: either a resolved flow_id (direct GET, e.g. from
// a chart aggregation that already carries sample_flow_ids), or a
// searchable identifier (e.g. a tracker_no surfaced without a resolved
// flow_id) resolved via the Investigation search endpoint.
export type DrillThroughTarget = { kind: 'flow'; value: string } | { kind: SearchField; value: string };

// ---------------------------------------------------------------------------
// Correlation Explorer -- surfaces Phase 3's CorrelationConflict /
// CandidateLink / LowConfidenceHint, computed all along but never shown by
// any earlier dashboard (see /api/analytics/correlation-explorer).
// ---------------------------------------------------------------------------

export interface CorrelationExplorerConflict {
  conflict_id: string;
  triggering_key_type: string;
  triggering_value: string | null;
  conflicting_identifiers: Array<{ key_type: string; flow_a_value: string | null; flow_b_value: string | null }>;
  affected_flow_ids: string[];
}

export interface CorrelationCandidateLinkView {
  link_type: string;
  confidence: string;
  flow_a_id: string;
  flow_b_id: string;
  matching_keys: Record<string, any>;
  note: string;
}

export interface CorrelationLowConfidenceHintView {
  hint_type: string;
  value: string | null;
  flow_ids: string[];
  note: string;
}

export interface CorrelationGraphNode {
  flow_id: string;
  transaction_id: string | null;
  correlation_status: string | null;
}

export type CorrelationEdgeType = 'conflict' | 'candidate_link' | 'low_confidence_hint';

export interface CorrelationGraphEdge {
  source: string;
  target: string;
  edge_type: CorrelationEdgeType;
  label: string;
}

export interface CorrelationExplorerResult {
  conflicts: CorrelationExplorerConflict[];
  candidate_links: CorrelationCandidateLinkView[];
  low_confidence_hints: CorrelationLowConfidenceHintView[];
  graph: { nodes: CorrelationGraphNode[]; edges: CorrelationGraphEdge[] };
}

export interface NarrativeStatement {
  role: string;
  statement_type: 'FACT' | 'INTERPRETATION' | 'MISSING EVIDENCE';
  text: string;
  evidence_event_ids: string[];
  source_files: string[];
}

export interface CaseSummary {
  transaction_id: string | null;
  merchant_name: string | null;
  amount: number | null;
  currency: string | null;
  issuer_id: string | null;
  authentication_method: string | null;
  final_status: string;
  last_successful_stage: string | null;
  missing_next_stage: string | null;
  narrative: NarrativeStatement[];
}

export interface TimelineEntryView {
  event_no: number;
  source_event_id: string | null;
  log_family: string;
  source_file: string;
  event_timestamp: string | null;
  event_type: string | null;
  stage: string | null;
  level: string | null;
  raw_reference: string;
}

export interface FindingSummaryView {
  finding_type: string;
  severity: string;
  statement: string;
  confidence: string;
  evidence_event_ids: string[];
}

export interface RoutingRecommendationView {
  suggested_team: string;
  reason: string;
  confidence: string;
}

export interface CorrelationSummaryView {
  correlation_status: string;
  correlation_confidence: string | null;
  correlation_keys: Array<{ key_type: string; value: string | null; confidence: string }>;
  is_conflict: boolean;
  conflict_statement: string | null;
}

export interface InvestigationCaseView {
  flow_id: string;
  case_summary: CaseSummary;
  timeline: TimelineEntryView[];
  findings: FindingSummaryView[];
  correlation: CorrelationSummaryView;
  routing: RoutingRecommendationView[];
  chart_metrics: Array<{ label: string; value: number | null; unit: string | null }>;
  data_quality: {
    evidence_level: string | null;
    parse_status: string | null;
    missing_stages: string[];
    not_observable_stages: string[];
    duplicate_event_ids: string[];
  };
  ai_available: boolean;
  ai_status_message: string | null;
  similar_incident_flow_ids: string[];
}

export interface InvestigationSearchResponse {
  query: string;
  field: SearchField;
  match_count: number;
  results: InvestigationCaseView[];
}

export interface InvestigationRawSearchResponse {
  query: string;
  match_count: number;
  results: InvestigationCaseView[];
}

export interface FieldConsistencyExceptionView {
  flow_id: string;
  field_name: string;
  status: 'MISMATCH' | 'MISSING_VALUE';
  distinct_values: string[];
  event_ids_by_value: Record<string, string[]>;
}

export interface SensitiveDataFindingView {
  category: string;
  event_id: string | null;
  source_file: string | null;
  protected_reference: string;
  safe_hint: string | null;
}

export interface SecurityQuality {
  scorecard: {
    total_events_analyzed: number;
    total_flows_analyzed: number;
    parse_quality: { parse_success: number; parse_failure: number; partial_parsing: number; fallback_parsing: number };
    evidence_quality: {
      missing_identifiers: number;
      missing_timestamps: number;
      unknown_merchant: number;
      unmatched_events: number;
      uncorrelated_events: number;
    };
    correlation_quality: { correlation_conflicts: number; low_confidence_correlations: number };
    flow_classification_counts: Record<string, number>;
    field_mismatch_count: number;
    sensitive_data_finding_count: number;
    overall_score: number;
    score_breakdown: Record<string, number>;
  };
  correlation_quality_breakdown: {
    by_log_family: Record<string, Record<string, number>>;
    by_source_file: Record<string, Record<string, number>>;
    by_confidence: Record<string, number>;
    parser_note: string;
  };
  field_consistency_exceptions: FieldConsistencyExceptionView[];
  field_mismatch_heatmap: Array<{ field_name: string; mismatch_count: number; sample_flow_ids: string[] }>;
  sensitive_data_findings: SensitiveDataFindingView[];
  repeated_attempts: number;
  incomplete_flows: number;
  uncorrelated_flows: number;
  exception_table: Array<{ category: string; description: string; flow_id: string | null; event_id: string | null; source_file: string | null }>;
}

export interface VFlexSummary {
  status: 'ok' | 'no_data';
  message?: string;
  window_start?: string;
  window_end?: string;
  total_events_analyzed?: number;
  total_records?: number;
  event_type_counts?: Record<string, number>;
  by_issuer?: Record<string, number>;
  by_status?: Record<string, number>;
  by_bank_operation?: Record<string, number>;
  by_channel?: Record<string, number>;
  by_currency?: Record<string, number>;
  top_merchants?: Record<string, number>;
  available_merchants?: string[];
  otp_processed_count?: number;
  bank_api_success_count?: number;
  checks_needed_count?: number;
  failed_events?: GenericFailedEventsReport;
}

// -- Global currency map (backend/analysis/currency_map.py) ----------------
// Cross-source: combines Cardinal/VFlex/Debit Portal/OTP Processor/AFS
// Netcetera into one currency-by-geography view, unlike every summary type
// above (each scoped to its own single log source).

export interface CurrencyGeoPoint {
  currency: string;
  country: string;
  lat: number;
  lng: number;
  count: number;
}

export interface CurrencyMapSummary {
  status: 'ok' | 'no_data';
  message?: string;
  total_transactions?: number;
  distinct_currencies?: number;
  points?: CurrencyGeoPoint[];
  unmapped_currencies?: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Phase 11: user-facing AI Analyst
// ---------------------------------------------------------------------------

export type EvidenceType = 'observed_fact' | 'calculated_metric' | 'inferred_interpretation' | 'missing_evidence';
export type AnalystConfidence = 'HIGH' | 'MEDIUM' | 'LOW';

export interface AnalystStatement {
  evidence_type: EvidenceType;
  text: string;
  evidence_event_ids: string[];
  flow_ids: string[];
}

export interface AnalystAnswer {
  question: string;
  answer: string;
  evidence: AnalystStatement[];
  relevant_flow_ids: string[];
  metrics: Record<string, any>;
  confidence: AnalystConfidence;
  recommended_investigation_area: string | null;
  tool_used: string | null;
  tool_parameters: Record<string, any>;
  unsupported: boolean;
  limitation_explanation: string | null;
  ai_available: boolean;
  ai_status_message: string | null;
}

export interface AiAnalystAuditEntry {
  audit_id: string;
  user_id: string;
  username: string;
  asked_at: string;
  question: string;
  tool_used: string | null;
  tool_parameters: Record<string, any>;
  data_sources_used: string[];
  result_summary: string;
  confidence: string | null;
  model_name: string;
  engine_version: string;
  ai_available: boolean;
}

/**
 * Hallucination / validation monitoring -- how often the LLM validation
 * gates rejected model output. Mirrors
 * GET /api/ai-analyst/validation-metrics (backend/llm/validation_metrics.py).
 */
export type ValidationReasonClass = 'hallucination' | 'policy_violation' | 'malformed_output';

export interface ValidationDailyPoint {
  day: string;
  responses: number;
  responses_with_rejection: number;
  rejections: number;
  hallucination: number;
  policy_violation: number;
  malformed_output: number;
}

export interface ValidationSurfaceStats {
  responses: number;
  responses_with_rejection: number;
  items_total: number;
  items_rejected: number;
}

export interface ValidationMetricsSummary {
  lookback_hours: number;
  responses_validated: number;
  responses_with_rejection: number;
  responses_fully_rejected: number;
  /** 0-1 fraction, not a percentage. */
  response_rejection_rate: number;
  items_total: number;
  items_rejected: number;
  item_rejection_rate: number;
  hallucination_rejections: number;
  by_reason: Record<string, number>;
  by_class: Record<ValidationReasonClass, number>;
  by_surface: Record<string, ValidationSurfaceStats>;
  daily: ValidationDailyPoint[];
}

export interface ValidationRejectionEntry {
  event_id: string;
  occurred_at: string;
  surface: string;
  model_name: string;
  items_total: number;
  items_accepted: number;
  items_rejected: number;
  response_rejected: boolean;
  reason_counts: Record<string, number>;
  reason_classes: ValidationReasonClass[];
  /** Redacted excerpt of what the model got wrong -- may be null. */
  sample: string | null;
}

export interface ValidationMetricsResponse {
  summary: ValidationMetricsSummary;
  recent: ValidationRejectionEntry[];
}

/**
 * ILA Bank application-log dashboard.
 * Mirrors GET /api/ila/summary (backend/analysis/ila_bank.py).
 */
export interface IlaSeverityPoint {
  bucket: string;
  error: number;
  warn: number;
  info: number;
}

export interface IlaDurationBucket {
  label: string;
  count: number;
}

export interface IlaDurationStats {
  count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
  buckets: IlaDurationBucket[];
}

/**
 * What was thrown, paired with where it was thrown. Two independent
 * frequency tables (exceptions, stack frames) cannot answer "which
 * exception came from which call" -- this pairing can, so it is what the
 * dashboard leads with.
 */
export interface IlaFailureSignature {
  exception: string;
  exception_namespace: string | null;
  method: string | null;
  owner: string | null;
  count: number;
  /** 0-1 share of all error entries carrying an exception. */
  share: number;
}

export interface IlaTracker {
  tracker_id: string;
  entries: number;
  errors: number;
  warnings: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  span_ms: number | null;
  event_types: string[];
  exceptions: string[];
}

export interface IlaErrorItem {
  timestamp: string | null;
  tracker_id: string;
  message: string;
  exceptions: string[];
  http_codes: (number | null)[];
}

/**
 * The endpoint returns one of two shapes. Modelling them as a discriminated
 * union (rather than one interface with optional fields) is what makes
 * TypeScript force a `status === 'ok'` check before any metric is read --
 * the earlier single-interface version declared every field required and
 * let `report.duration_stats.buckets` compile against a `no_data` response
 * that carries neither.
 */
export interface IlaNoData {
  status: 'no_data';
  message: string;
}

export interface IlaReport {
  status: 'ok';
  window_start: string | null;
  window_end: string | null;
  total_events_analyzed: number;
  total_trackers: number;
  untracked_events: number;
  error_count: number;
  warning_count: number;
  /** 0-1 fraction, not a percentage. */
  error_rate: number;
  trackers_with_errors: number;
  multiline_entries: number;
  sensitive_field_entries: number;
  level_counts: Record<string, number>;
  event_type_counts: Record<string, number>;
  parse_status_counts: Record<string, number>;
  /** 'hour' for windows up to 2 days, 'day' beyond -- keeps bars readable. */
  severity_granularity: 'hour' | 'day';
  severity_timeline: IlaSeverityPoint[];
  top_exceptions: Record<string, number>;
  top_stack_frames: Record<string, number>;
  failure_signatures: IlaFailureSignature[];
  headline_failure: IlaFailureSignature | null;
  top_services: Record<string, number>;
  http_status_counts: Record<string, number>;
  duration_stats: IlaDurationStats;
  trackers: IlaTracker[];
  recent_errors: IlaErrorItem[];
}

export type IlaSummary = IlaNoData | IlaReport;
