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
  min_level: string;
  source_system_filter: string | null;
  component_filter: string | null;
  message_contains: string | null;
  mode: 'immediate' | 'digest';
  dedup_window_minutes: number;
  recipients: string | null;
  created_at: string;
  updated_at: string;
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
  recovered_at: string;
  duration_minutes: number;
  last_event_before_down: { component: string; correlation_id: string | null };
  first_event_after_recovery: { component: string; correlation_id: string | null };
}

export interface VPlusAvailabilityReport {
  status: 'healthy' | 'down' | 'no_data';
  message?: string;
  gap_threshold_minutes: number;
  total_events_analyzed?: number;
  window_start?: string;
  window_end?: string;
  downtime_windows: VPlusDowntimeWindow[];
  total_downtime_minutes?: number;
  currently_down: boolean | null;
  minutes_since_last_event?: number | null;
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

export interface VPlusFullReport {
  investigation_summary: VPlusInvestigationSummary;
  availability: VPlusAvailabilityReport;
  response_times: VPlusResponseTimeReport;
  sms_analysis: VPlusSmsReport;
}
