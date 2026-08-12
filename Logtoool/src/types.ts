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

export interface AlertRuleInfo {
  name: string;
  description: string;
  configurable: boolean;
}

export interface User {
  user_id: string;
  username: string;
  role: 'admin' | 'member';
  created_at?: string;
  is_active?: boolean;
  must_change_password?: boolean;
}
