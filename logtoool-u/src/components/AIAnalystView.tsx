import React, { useEffect, useState } from 'react';
import { BrainCircuit, Sparkles, Send, AlertTriangle, ShieldCheck, ShieldAlert, ChevronDown, ChevronUp, History, RefreshCw } from 'lucide-react';
import {
  AiAnalystAuditEntry,
  AnalystAnswer,
  EvidenceType,
  ValidationMetricsResponse,
  ValidationReasonClass,
} from '../types';
import { api, ApiError } from '../api';
import { maskText, maskJsonString } from '../utils/maskSensitive';
import { MaskedBadge } from './MaskedBadge';

interface AIAnalystViewProps {
  ollamaAvailable: boolean;
  isAdmin: boolean;
}

interface ConversationTurn {
  id: string;
  question: string;
  timestamp: string;
  answer?: AnalystAnswer;
  errorText?: string;
}

const SAMPLE_QUESTIONS = [
  'What happened to transaction TXN1?',
  'Is V+ having issues?',
  'How many V+ responses were delayed?',
  'Which issuer has the highest failure rate?',
  'Are there queue handoff problems?',
  'What are the top failures today?',
  'Are there recurring incidents?',
  'Show me incomplete OOB transactions.',
  'Are there correlation problems in the logs?',
];

const EVIDENCE_STYLES: Record<EvidenceType, { label: string; className: string }> = {
  observed_fact: { label: 'Observed Fact', className: 'bg-blue-50 text-blue-700 border-blue-200' },
  calculated_metric: { label: 'Calculated Metric', className: 'bg-purple-50 text-purple-700 border-purple-200' },
  inferred_interpretation: { label: 'Inferred Interpretation', className: 'bg-amber-50 text-amber-800 border-amber-200' },
  missing_evidence: { label: 'Missing Evidence', className: 'bg-slate-100 text-slate-600 border-slate-300' },
};

const CONFIDENCE_STYLES: Record<string, string> = {
  HIGH: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  MEDIUM: 'bg-amber-100 text-amber-800 border-amber-300',
  LOW: 'bg-slate-200 text-slate-700 border-slate-300',
};

const nowLabel = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

const MetricsPanel: React.FC<{ metrics: Record<string, any> }> = ({ metrics }) => {
  const [open, setOpen] = useState(false);
  if (!metrics || Object.keys(metrics).length === 0) return null;
  return (
    <div className="pt-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider cursor-pointer hover:text-slate-700"
      >
        {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        Metrics (raw deterministic tool result)
        <MaskedBadge className="ml-1" />
      </button>
      {open && (
        <pre className="mt-1.5 bg-slate-950 text-emerald-400 font-mono text-[10px] p-3 rounded-lg overflow-x-auto border border-slate-800 max-h-64">
          {maskJsonString(metrics)}
        </pre>
      )}
    </div>
  );
};

const AnswerCard: React.FC<{ answer: AnalystAnswer }> = ({ answer }) => (
  <div className="space-y-3">
    <div className="flex items-start justify-between gap-3">
      <p className="text-xs font-medium leading-relaxed flex-1">{maskText(answer.answer)}</p>
      <span
        className={`shrink-0 text-[10px] font-bold px-2 py-0.5 rounded-full border ${CONFIDENCE_STYLES[answer.confidence] || CONFIDENCE_STYLES.LOW}`}
      >
        {answer.confidence} CONFIDENCE
      </span>
    </div>

    {answer.unsupported && answer.limitation_explanation && (
      <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-[11px] text-amber-800">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>{maskText(answer.limitation_explanation)}</span>
      </div>
    )}

    {answer.evidence.length > 0 && (
      <div className="space-y-1.5">
        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Evidence</div>
        <div className="space-y-1.5">
          {answer.evidence.map((e, i) => {
            const style = EVIDENCE_STYLES[e.evidence_type] || EVIDENCE_STYLES.observed_fact;
            return (
              <div key={i} className={`text-[11px] rounded-lg border px-2.5 py-1.5 ${style.className}`}>
                <span className="font-bold uppercase text-[9px] tracking-wide mr-1.5">{style.label}:</span>
                {maskText(e.text)}
              </div>
            );
          })}
        </div>
      </div>
    )}

    {answer.relevant_flow_ids.length > 0 && (
      <div className="space-y-1">
        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Relevant Flows</div>
        <div className="flex flex-wrap gap-1.5">
          {answer.relevant_flow_ids.map((fid) => (
            <span key={fid} className="text-[10px] font-mono bg-slate-100 border border-slate-200 rounded px-2 py-0.5 text-slate-700">
              {fid}
            </span>
          ))}
        </div>
      </div>
    )}

    {answer.recommended_investigation_area && (
      <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-[11px] text-blue-800">
        <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
        <span>
          <span className="font-bold">Recommended investigation area: </span>
          {answer.recommended_investigation_area}
        </span>
      </div>
    )}

    <MetricsPanel metrics={answer.metrics} />

    {answer.tool_used && (
      <div className="text-[9px] text-slate-400 font-mono pt-1">
        deterministic tool: {answer.tool_used}
        {Object.keys(answer.tool_parameters).length > 0 ? ` (${JSON.stringify(answer.tool_parameters)})` : ''}
      </div>
    )}
  </div>
);

const AuditLogPanel: React.FC = () => {
  const [entries, setEntries] = useState<AiAnalystAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchEntries = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.get<{ entries: AiAnalystAuditEntry[] }>('/api/ai-analyst/audit-log');
      setEntries(resp.entries);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load the audit log');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEntries();
  }, []);

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center justify-between">
        <span className="flex items-center gap-2">
          <History className="w-4 h-4 text-slate-500" />
          AI Analyst Audit Log ({entries.length})
        </span>
        <button
          onClick={fetchEntries}
          disabled={loading}
          className="flex items-center gap-1.5 text-[11px] font-bold text-blue-600 hover:text-blue-500 disabled:opacity-50 cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>
      {error && <div className="px-5 py-4 text-xs text-rose-600">{error}</div>}
      {!error && loading && entries.length === 0 && <div className="px-5 py-8 text-center text-xs text-slate-400">Loading…</div>}
      {!error && !loading && entries.length === 0 && (
        <div className="px-5 py-8 text-center text-xs text-slate-400 italic">No AI Analyst questions recorded yet.</div>
      )}
      {entries.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
              <tr>
                <th className="px-4 py-2">User</th>
                <th className="px-4 py-2">Asked At</th>
                <th className="px-4 py-2">Question</th>
                <th className="px-4 py-2">Tool</th>
                <th className="px-4 py-2">Confidence</th>
                <th className="px-4 py-2">Model</th>
                <th className="px-4 py-2">Engine Version</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {entries.map((e) => (
                <tr key={e.audit_id}>
                  <td className="px-4 py-2 font-semibold text-slate-700">{e.username}</td>
                  <td className="px-4 py-2 font-mono text-slate-500">{e.asked_at.slice(0, 19).replace('T', ' ')}</td>
                  <td className="px-4 py-2 text-slate-700 max-w-xs truncate" title={e.question}>
                    {e.question}
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-500">{e.tool_used || '—'}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${
                        CONFIDENCE_STYLES[e.confidence || ''] || CONFIDENCE_STYLES.LOW
                      }`}
                    >
                      {e.confidence || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-400">{e.model_name}</td>
                  <td className="px-4 py-2 font-mono text-slate-400">{e.engine_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const REASON_CLASS_STYLES: Record<ValidationReasonClass, { label: string; className: string; hint: string }> = {
  hallucination: {
    label: 'Hallucination',
    className: 'bg-rose-50 text-rose-700 border-rose-200',
    hint: 'The model referenced an event id, source file, flow id or tool that does not exist. A rising rate here means it is losing its grounding.',
  },
  policy_violation: {
    label: 'Policy',
    className: 'bg-amber-50 text-amber-700 border-amber-200',
    hint: 'Well-formed output that broke a content rule (forbidden claims, calling a pending flow failed, unsafe SQL). Rising means prompt/guardrail drift.',
  },
  malformed_output: {
    label: 'Malformed',
    className: 'bg-slate-100 text-slate-600 border-slate-300',
    hint: 'The response was not usable JSON or shape at all. Rising usually points at a model/version/prompt-format problem rather than reasoning quality.',
  },
};

const REASON_LABELS: Record<string, string> = {
  unknown_event_id: 'Cited a non-existent event id',
  unknown_source_file: 'Cited a non-existent source file',
  unknown_flow_id: 'Cited a non-existent flow id',
  unknown_tool: 'Invented a tool name',
  forbidden_content: 'Forbidden content (fraud/intent/blame)',
  pending_treated_as_failure: 'Called a pending flow a failure',
  sql_failed_security_validation: 'Generated SQL failed the safety gate',
  malformed_item: 'Item was not a JSON object',
  invalid_role_type_or_empty_text: 'Invalid role/type or empty text',
  malformed_json_response: 'Response was not usable JSON',
  tool_parameter_mismatch: 'Tool parameters did not match the spec',
};

const pct = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;

/** Rejection rate coloured by severity, not by whether it is non-zero: a
 *  low steady rejection rate is the gate working, not an incident. */
const rateTone = (rate: number) =>
  rate >= 0.25 ? 'text-rose-600' : rate >= 0.1 ? 'text-amber-600' : 'text-emerald-600';

const ValidationMetricsPanel: React.FC = () => {
  const [data, setData] = useState<ValidationMetricsResponse | null>(null);
  const [lookbackHours, setLookbackHours] = useState(24 * 7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = async (hours: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(
        await api.get<ValidationMetricsResponse>(
          `/api/ai-analyst/validation-metrics?lookback_hours=${hours}`
        )
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load validation metrics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics(lookbackHours);
  }, [lookbackHours]);

  const s = data?.summary;
  // A day counts as a signal only once there is something to compare it
  // against -- a single day of history cannot show a trend.
  const trend = (() => {
    if (!s || s.daily.length < 2) return null;
    const latest = s.daily[s.daily.length - 1];
    const prior = s.daily.slice(0, -1);
    const priorResponses = prior.reduce((n, d) => n + d.responses, 0);
    const priorRejected = prior.reduce((n, d) => n + d.responses_with_rejection, 0);
    if (priorResponses === 0 || latest.responses === 0) return null;
    return {
      latestRate: latest.responses_with_rejection / latest.responses,
      priorRate: priorRejected / priorResponses,
    };
  })();

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center justify-between gap-3">
          <span className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-slate-500" />
            AI Output Validation
          </span>
          <div className="flex items-center gap-2">
            <select
              value={lookbackHours}
              onChange={(e) => setLookbackHours(Number(e.target.value))}
              className="text-[11px] font-bold text-slate-600 bg-white border border-slate-200 rounded px-1.5 py-1 cursor-pointer"
            >
              <option value={24}>Last 24h</option>
              <option value={24 * 7}>Last 7 days</option>
              <option value={24 * 30}>Last 30 days</option>
            </select>
            <button
              onClick={() => fetchMetrics(lookbackHours)}
              disabled={loading}
              className="flex items-center gap-1.5 text-[11px] font-bold text-blue-600 hover:text-blue-500 disabled:opacity-50 cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        <div className="px-5 py-3 text-[11px] text-slate-500 leading-relaxed border-b border-slate-100">
          Every AI response passes a validation gate that discards anything it cannot tie back to the
          deterministic engine. Those discards are counted here. A low steady rate is the gate doing its job;
          a <strong className="text-slate-700">rising</strong> rate — especially hallucinations — is the
          earliest signal that model quality is degrading.
        </div>

        {error && <div className="px-5 py-4 text-xs text-rose-600">{error}</div>}
        {!error && loading && !data && <div className="px-5 py-8 text-center text-xs text-slate-400">Loading…</div>}

        {!error && s && s.responses_validated === 0 && (
          <div className="px-5 py-8 text-center text-xs text-slate-400 italic">
            No AI responses validated in this window yet.
          </div>
        )}

        {!error && s && s.responses_validated > 0 && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 divide-x divide-slate-100 border-b border-slate-100">
              <div className="px-4 py-3">
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Responses Checked</div>
                <div className="text-xl font-bold text-slate-900 mt-0.5">{s.responses_validated}</div>
              </div>
              <div className="px-4 py-3">
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Rejection Rate</div>
                <div className={`text-xl font-bold mt-0.5 ${rateTone(s.response_rejection_rate)}`}>
                  {pct(s.response_rejection_rate)}
                </div>
                <div className="text-[10px] text-slate-400">{s.responses_with_rejection} responses affected</div>
              </div>
              <div className="px-4 py-3">
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Hallucinations</div>
                <div className={`text-xl font-bold mt-0.5 ${s.hallucination_rejections > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                  {s.hallucination_rejections}
                </div>
                <div className="text-[10px] text-slate-400">fabricated ids / tools</div>
              </div>
              <div className="px-4 py-3">
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Statements Dropped</div>
                <div className="text-xl font-bold text-slate-900 mt-0.5">{s.items_rejected}</div>
                <div className="text-[10px] text-slate-400">of {s.items_total} ({pct(s.item_rejection_rate)})</div>
              </div>
            </div>

            {trend && (
              <div
                className={`px-5 py-2.5 text-[11px] font-semibold border-b border-slate-100 ${
                  trend.latestRate > trend.priorRate ? 'bg-rose-50 text-rose-700' : 'bg-emerald-50 text-emerald-700'
                }`}
              >
                {trend.latestRate > trend.priorRate ? '▲' : '▼'} Latest day at {pct(trend.latestRate)} vs{' '}
                {pct(trend.priorRate)} across the prior days in this window.
              </div>
            )}

            <div className="px-5 py-4 space-y-3">
              <div className="flex flex-wrap gap-2">
                {(Object.keys(REASON_CLASS_STYLES) as ValidationReasonClass[]).map((cls) => (
                  <span
                    key={cls}
                    title={REASON_CLASS_STYLES[cls].hint}
                    className={`text-[10px] font-bold px-2 py-1 rounded-full border ${REASON_CLASS_STYLES[cls].className}`}
                  >
                    {REASON_CLASS_STYLES[cls].label}: {s.by_class[cls] ?? 0}
                  </span>
                ))}
              </div>

              {Object.keys(s.by_reason).length > 0 && (
                <div className="space-y-1">
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Why output was rejected</div>
                  {Object.entries(s.by_reason).map(([reason, count]) => (
                    <div key={reason} className="flex items-center gap-2 text-[11px]">
                      <span className="text-slate-700 flex-1">{REASON_LABELS[reason] || reason}</span>
                      <span className="font-mono font-bold text-slate-500">{count}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {data && data.recent.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            Recent Rejections ({data.recent.length})
            <MaskedBadge />
          </div>
          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200 sticky top-0">
                <tr>
                  <th className="px-4 py-2">When</th>
                  <th className="px-4 py-2">Gate</th>
                  <th className="px-4 py-2">Class</th>
                  <th className="px-4 py-2">Dropped</th>
                  <th className="px-4 py-2">What the model got wrong</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.recent.map((r) => (
                  <tr key={r.event_id}>
                    <td className="px-4 py-2 font-mono text-slate-500 whitespace-nowrap">
                      {r.occurred_at.slice(0, 19).replace('T', ' ')}
                    </td>
                    <td className="px-4 py-2 font-mono text-slate-500">{r.surface}</td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        {r.reason_classes.map((cls) => (
                          <span
                            key={cls}
                            title={REASON_CLASS_STYLES[cls]?.hint}
                            className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                              REASON_CLASS_STYLES[cls]?.className || ''
                            }`}
                          >
                            {REASON_CLASS_STYLES[cls]?.label || cls}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-2 font-mono text-slate-600">
                      {r.response_rejected ? 'whole response' : `${r.items_rejected}/${r.items_total}`}
                    </td>
                    <td className="px-4 py-2 text-slate-600 max-w-md truncate" title={r.sample || ''}>
                      {r.sample ? maskText(r.sample) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export const AIAnalystView: React.FC<AIAnalystViewProps> = ({ ollamaAvailable, isAdmin }) => {
  const [viewMode, setViewMode] = useState<'ask' | 'audit' | 'quality'>('ask');
  const [turns, setTurns] = useState<ConversationTurn[]>([]);
  const [inputQuestion, setInputQuestion] = useState('');
  const [isAsking, setIsAsking] = useState(false);

  const handleAsk = async (questionText?: string) => {
    const q = questionText || inputQuestion;
    if (!q.trim() || isAsking) return;

    const turnId = `t-${Date.now()}`;
    setTurns((prev) => [...prev, { id: turnId, question: q, timestamp: nowLabel() }]);
    setInputQuestion('');
    setIsAsking(true);

    try {
      const data = await api.post<AnalystAnswer>('/api/ai-analyst/ask', { question: q });
      setTurns((prev) => prev.map((t) => (t.id === turnId ? { ...t, answer: data } : t)));
    } catch (err) {
      const detail =
        err instanceof ApiError
          ? err.status === 503
            ? 'The AI Analyst is unavailable right now -- try again once Ollama is running.'
            : err.detail
          : 'Something went wrong answering that.';
      setTurns((prev) => prev.map((t) => (t.id === turnId ? { ...t, errorText: detail } : t)));
    } finally {
      setIsAsking(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs space-y-4">
        <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <BrainCircuit className="w-5 h-5 text-blue-600" />
          LLens AI Analyst
        </h2>
        <p className="text-xs text-slate-500">
          Ask questions about analyzed transactions and logs. Every answer is generated ONLY from LLens's
          deterministic analytical model (Phases 2-10) via a local Ollama model -- the AI selects from a
          fixed set of pre-built analyses, it never writes its own queries, joins, or metrics. Sensitive
          values stay masked, and every question is audit-logged.
        </p>

        {isAdmin && (
          <div className="flex gap-1 bg-slate-100 rounded-lg p-1 w-fit">
            <button
              onClick={() => setViewMode('ask')}
              className={`px-3 py-1.5 rounded-md text-[11px] font-bold cursor-pointer transition-colors ${
                viewMode === 'ask' ? 'bg-white text-blue-700 shadow-2xs' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              Ask
            </button>
            <button
              onClick={() => setViewMode('audit')}
              className={`px-3 py-1.5 rounded-md text-[11px] font-bold cursor-pointer transition-colors ${
                viewMode === 'audit' ? 'bg-white text-blue-700 shadow-2xs' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              Audit Log
            </button>
            <button
              onClick={() => setViewMode('quality')}
              className={`px-3 py-1.5 rounded-md text-[11px] font-bold cursor-pointer transition-colors ${
                viewMode === 'quality' ? 'bg-white text-blue-700 shadow-2xs' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              AI Quality
            </button>
          </div>
        )}

        {viewMode === 'ask' && !ollamaAvailable && (
          <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3.5 py-2.5 text-xs text-amber-800">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            Ollama is currently offline -- questions will fail until it's running. Check Settings.
          </div>
        )}

        {viewMode === 'ask' && (
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-100">
            <span className="text-xs font-bold text-slate-500 mr-1 flex items-center gap-1">
              <Sparkles className="w-3.5 h-3.5 text-amber-500" /> Example Questions:
            </span>
            {SAMPLE_QUESTIONS.map((prompt, idx) => (
              <button
                key={idx}
                onClick={() => handleAsk(prompt)}
                className="text-xs bg-slate-100 hover:bg-blue-50 text-slate-700 hover:text-blue-700 font-medium px-3 py-1 rounded-full border border-slate-200 hover:border-blue-300 transition-all cursor-pointer"
              >
                {prompt}
              </button>
            ))}
          </div>
        )}
      </div>

      {viewMode === 'audit' ? (
        <AuditLogPanel />
      ) : viewMode === 'quality' ? (
        <ValidationMetricsPanel />
      ) : (
        <>
      <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
        {turns.length === 0 && (
          <div className="text-center text-xs text-slate-400 py-10">
            Ask a question above to get started, or pick an example question.
          </div>
        )}

        {turns.map((turn) => (
          <div key={turn.id} className="space-y-3">
            <div className="p-4 rounded-xl border bg-blue-600 text-white border-blue-700 ml-12 shadow-sm">
              <div className="flex items-center justify-between border-b pb-2 mb-2 border-opacity-20 border-slate-400">
                <span className="font-extrabold text-xs tracking-wide">🧑‍💻 You</span>
                <span className="text-[10px] opacity-70 font-mono">{turn.timestamp}</span>
              </div>
              <p className="text-xs font-medium leading-relaxed">{turn.question}</p>
            </div>

            <div className="p-5 rounded-xl border bg-white text-slate-800 border-slate-200 mr-12 shadow-2xs">
              <div className="flex items-center justify-between border-b pb-2 mb-3 border-slate-100">
                <span className="font-extrabold text-xs tracking-wide flex items-center gap-1.5">
                  🤖 LLens AI Analyst
                </span>
              </div>
              {turn.answer ? (
                <AnswerCard answer={turn.answer} />
              ) : turn.errorText ? (
                <p className="text-xs font-medium text-red-700">{turn.errorText}</p>
              ) : (
                <div className="flex items-center gap-2 text-xs text-slate-600">
                  <Sparkles className="w-4 h-4 text-amber-500 animate-spin" />
                  <span>Selecting a deterministic analysis and generating the answer…</span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="bg-white p-3 rounded-xl border border-slate-200 shadow-sm flex items-center gap-3">
        <input
          type="text"
          value={inputQuestion}
          onChange={(e) => setInputQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAsk()}
          placeholder="Ask about a transaction, dependency, failure pattern, or queue issue..."
          className="flex-1 text-xs bg-slate-50 border border-slate-300 rounded-lg px-4 py-2.5 font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={() => handleAsk()}
          disabled={isAsking || !inputQuestion.trim()}
          className="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold text-xs rounded-lg transition-all cursor-pointer flex items-center gap-1.5 shadow-2xs"
        >
          <Send className="w-3.5 h-3.5" />
          <span>Ask</span>
        </button>
      </div>
        </>
      )}
    </div>
  );
};
