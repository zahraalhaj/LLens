import React, { useEffect, useMemo, useState } from 'react';
import { Search, FileSearch, ListTree, AlertTriangle, ShieldQuestion, Link2, Copy, Check, ExternalLink, Waypoints } from 'lucide-react';
import { api, ApiError } from '../api';
import { DrillThroughTarget, InvestigationCaseView, InvestigationSearchResponse, InvestigationRawSearchResponse, TimelineEntryView, SearchField } from '../types';
import { SEVERITY_COLORS } from '../theme';
import { MerchantFilter } from './MerchantFilter';

const SEARCH_FIELDS: { id: SearchField; label: string }[] = [
  { id: 'transaction_id', label: 'TransactionId' },
  { id: 'tracker', label: 'Tracker No' },
  { id: 'stepup_request_id', label: 'StepupRequestId' },
  { id: 'mobile', label: 'Mobile' },
  { id: 'card_last4', label: 'Card Last 4' },
  { id: 'msg_id', label: 'MsgId' },
  { id: 'correlation_id', label: 'CorrelationId' },
  { id: 'merchant_name', label: 'Merchant' },
];

const CONFIDENCE_STYLES: Record<string, string> = {
  HIGH: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  MEDIUM: 'bg-amber-50 text-amber-700 border-amber-200',
  LOW: 'bg-slate-50 text-slate-600 border-slate-200',
};

const SEVERITY_STYLES: Record<string, string> = {
  CRITICAL: 'bg-rose-50 text-rose-800 border-rose-300',
  HIGH: 'bg-orange-50 text-orange-800 border-orange-300',
  MEDIUM: 'bg-amber-50 text-amber-800 border-amber-300',
  LOW: 'bg-slate-50 text-slate-600 border-slate-200',
};

const fmtTime = (iso?: string | null) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—');

interface InvestigationViewProps {
  pendingTarget: DrillThroughTarget | null;
  onConsumePendingTarget: () => void;
}

export const InvestigationView: React.FC<InvestigationViewProps> = ({ pendingTarget, onConsumePendingTarget }) => {
  const [mode, setMode] = useState<'field' | 'raw'>('field');
  const [field, setField] = useState<SearchField>('transaction_id');
  const [query, setQuery] = useState('');
  const [rawQuery, setRawQuery] = useState('');
  const [results, setResults] = useState<InvestigationCaseView[]>([]);
  const [selected, setSelected] = useState<InvestigationCaseView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [availableMerchants, setAvailableMerchants] = useState<string[]>([]);

  useEffect(() => {
    if (field !== 'merchant_name' || availableMerchants.length > 0) return;
    api
      .get<{ merchants: string[] }>(`/api/analytics/investigation/merchants?lookback_hours=${24 * 90}`)
      .then((resp) => setAvailableMerchants(resp.merchants))
      .catch(() => {
        // best-effort -- the merchant field still works as free text if this fails
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field]);

  const handleCopy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(key);
      setTimeout(() => setCopiedId((cur) => (cur === key ? null : cur)), 1500);
    } catch {
      // clipboard unavailable/denied -- nothing to recover from
    }
  };

  const loadByFlowId = async (flowId: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<InvestigationCaseView>(`/api/analytics/investigation/${encodeURIComponent(flowId)}?lookback_hours=${24 * 90}`);
      setResults([result]);
      setSelected(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load transaction');
    } finally {
      setLoading(false);
    }
  };

  const runSearch = async (searchField: SearchField, searchQuery: string) => {
    if (!searchQuery.trim()) return;
    setLoading(true);
    setError(null);
    setSelected(null);
    try {
      const params = new URLSearchParams({ field: searchField, query: searchQuery.trim(), lookback_hours: String(24 * 90) });
      const resp = await api.get<InvestigationSearchResponse>(`/api/analytics/investigation/search?${params}`);
      setResults(resp.results);
      setSelected(resp.results[0] || null);
      if (resp.results.length === 0) setError(`No transaction found for ${searchField} = "${searchQuery}" in the last 90 days.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Search failed');
    } finally {
      setLoading(false);
    }
  };

  const runRawSearch = async (searchQuery: string) => {
    if (!searchQuery.trim()) return;
    setLoading(true);
    setError(null);
    setSelected(null);
    try {
      const params = new URLSearchParams({ query: searchQuery.trim(), lookback_hours: String(24 * 90) });
      const resp = await api.get<InvestigationRawSearchResponse>(`/api/analytics/investigation/search-raw?${params}`);
      setResults(resp.results);
      setSelected(resp.results[0] || null);
      if (resp.results.length === 0) setError(`No transaction found containing "${searchQuery}" in the last 90 days.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Search failed');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!pendingTarget) return;
    if (pendingTarget.kind === 'flow') {
      loadByFlowId(pendingTarget.value);
    } else {
      setField(pendingTarget.kind);
      setQuery(pendingTarget.value);
      runSearch(pendingTarget.kind, pendingTarget.value);
    }
    onConsumePendingTarget();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingTarget]);

  return (
    <div className="space-y-6">
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
        <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-1">
          <Search className="w-4 h-4 text-blue-600" />
          Investigation Search
        </h2>
        <p className="text-xs text-slate-500 mb-3">
          {mode === 'field'
            ? 'Search the correlated flow model by an exact identifier.'
            : 'Search raw log text across every ingested event -- returns the correlated flows/cases that contain a match, not flat rows.'}
        </p>
        <div className="flex gap-1 mb-4 bg-slate-100 rounded-lg p-1 w-fit">
          <button
            onClick={() => setMode('field')}
            className={`px-3 py-1.5 rounded-md text-[11px] font-bold cursor-pointer transition-colors ${
              mode === 'field' ? 'bg-white text-blue-700 shadow-2xs' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Exact field
          </button>
          <button
            onClick={() => setMode('raw')}
            className={`px-3 py-1.5 rounded-md text-[11px] font-bold cursor-pointer transition-colors ${
              mode === 'raw' ? 'bg-white text-blue-700 shadow-2xs' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Raw text
          </button>
        </div>
        {mode === 'field' ? (
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Field</label>
              <select
                value={field}
                onChange={(e) => setField(e.target.value as SearchField)}
                className="text-xs bg-white border border-slate-300 rounded-md px-2.5 py-2 text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {SEARCH_FIELDS.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-1 min-w-[200px]">
              {field === 'merchant_name' ? (
                <MerchantFilter value={query} onChange={setQuery} options={availableMerchants} />
              ) : (
                <>
                  <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Value</label>
                  <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && runSearch(field, query)}
                    placeholder={`Enter ${SEARCH_FIELDS.find((f) => f.id === field)?.label}…`}
                    className="w-full text-xs bg-white border border-slate-300 rounded-md px-2.5 py-2 text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </>
              )}
            </div>
            <button
              onClick={() => runSearch(field, query)}
              disabled={loading || !query.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer"
            >
              <Search className="w-3.5 h-3.5" />
              Search
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[240px]">
              <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Raw text fragment</label>
              <input
                type="text"
                value={rawQuery}
                onChange={(e) => setRawQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runRawSearch(rawQuery)}
                placeholder="e.g. a fragment of a reference id, merchant name, or error message…"
                className="w-full text-xs bg-white border border-slate-300 rounded-md px-2.5 py-2 text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <button
              onClick={() => runRawSearch(rawQuery)}
              disabled={loading || !rawQuery.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer"
            >
              <Search className="w-3.5 h-3.5" />
              Search
            </button>
          </div>
        )}
      </div>

      {loading && (
        <div className="flex items-center justify-center h-32 text-slate-400 text-sm">Loading…</div>
      )}

      {error && !loading && (
        <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 text-xs font-medium">{error}</div>
      )}

      {results.length > 1 && !loading && (
        <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700">
            {results.length} matching flows
          </div>
          <div className="divide-y divide-slate-100">
            {results.map((r) => (
              <button
                key={r.flow_id}
                onClick={() => setSelected(r)}
                className={`w-full text-left px-5 py-2.5 text-xs hover:bg-slate-50 cursor-pointer ${
                  selected?.flow_id === r.flow_id ? 'bg-blue-50' : ''
                }`}
              >
                <span className="font-mono text-slate-700">{r.flow_id}</span>
                <span className="ml-3 text-slate-500">{r.case_summary.transaction_id || '—'}</span>
                <span className="ml-3 font-bold text-slate-600">{r.case_summary.final_status}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {selected && !loading && <InvestigationCaseDetail investigationCase={selected} onCopy={handleCopy} copiedId={copiedId} />}
    </div>
  );
};

// Visual per-transaction timeline: one horizontal lane per log family,
// events plotted as dots positioned by elapsed time since the first event.
// Kept deliberately simple (fixed-width lanes, linear time scale) -- this
// supplements the exact timeline table below it, never replaces it.
const Swimlane: React.FC<{
  timeline: TimelineEntryView[];
  hoveredEventId: string | null;
  onHover: (id: string | null) => void;
}> = ({ timeline, hoveredEventId, onHover }) => {
  const families = useMemo(() => Array.from(new Set(timeline.map((e) => e.log_family))), [timeline]);

  const times = timeline.map((e) => (e.event_timestamp ? new Date(e.event_timestamp).getTime() : null)).filter((t): t is number => t !== null);
  const minTime = times.length ? Math.min(...times) : 0;
  const maxTime = times.length ? Math.max(...times) : 0;
  const span = Math.max(1, maxTime - minTime);

  const positionPct = (ts: string | null) => {
    if (!ts) return 0;
    const t = new Date(ts).getTime();
    return ((t - minTime) / span) * 100;
  };

  if (families.length === 0) return null;

  return (
    <div className="space-y-2">
      {families.map((family) => (
        <div key={family} className="flex items-center gap-3">
          <div className="w-32 shrink-0 text-[10px] font-bold text-slate-500 truncate" title={family}>
            {family}
          </div>
          <div className="relative flex-1 h-6 bg-slate-50 rounded border border-slate-100">
            {timeline
              .filter((e) => e.log_family === family)
              .map((e, i) => (
                <div
                  key={i}
                  className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 cursor-pointer transition-transform"
                  style={{
                    left: `${positionPct(e.event_timestamp)}%`,
                    transform: `translate(-50%, -50%) scale(${hoveredEventId === e.source_event_id ? 1.6 : 1})`,
                  }}
                  onMouseEnter={() => onHover(e.source_event_id)}
                  onMouseLeave={() => onHover(null)}
                >
                  <div
                    className={`w-2.5 h-2.5 rounded-full ${e.stage ? 'ring-2 ring-offset-1 ring-blue-400' : ''}`}
                    style={{ backgroundColor: SEVERITY_COLORS[e.level || 'UNKNOWN'] || SEVERITY_COLORS.UNKNOWN }}
                    title={`${e.event_type || 'event'} @ ${e.event_timestamp || '—'}${e.stage ? ` (${e.stage})` : ''}`}
                  />
                </div>
              ))}
          </div>
        </div>
      ))}
      <div className="flex justify-between text-[9px] text-slate-400 pl-[8.5rem]">
        <span>{timeline[0]?.event_timestamp?.slice(11, 19) || ''}</span>
        <span>{timeline[timeline.length - 1]?.event_timestamp?.slice(11, 19) || ''}</span>
      </div>
    </div>
  );
};

const InvestigationCaseDetail: React.FC<{
  investigationCase: InvestigationCaseView;
  onCopy: (text: string, key: string) => void;
  copiedId: string | null;
}> = ({ investigationCase: c, onCopy, copiedId }) => {
  const cs = c.case_summary;
  const [hoveredEventId, setHoveredEventId] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      {/* Business summary */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
            <FileSearch className="w-4 h-4 text-blue-600" />
            Business Summary
          </h3>
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-[11px] text-slate-400">{c.flow_id}</span>
            <button
              onClick={() => onCopy(c.flow_id, 'flow-id')}
              className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer"
            >
              {copiedId === 'flow-id' ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
          <Field label="Transaction" value={cs.transaction_id} mono />
          <Field label="Merchant" value={cs.merchant_name} />
          <Field label="Amount" value={cs.amount != null ? `${cs.amount} ${cs.currency || ''}` : null} />
          <Field label="Issuer" value={cs.issuer_id} />
          <Field label="Auth Method" value={cs.authentication_method} />
          <Field
            label="Final Status"
            value={cs.final_status}
            valueClassName={
              cs.final_status === 'SUCCESS'
                ? 'text-emerald-600'
                : cs.final_status === 'FAILED'
                ? 'text-rose-600'
                : cs.final_status === 'PENDING_AT_LOG_END'
                ? 'text-amber-600'
                : 'text-slate-600'
            }
          />
          <Field label="Last Successful Stage" value={cs.last_successful_stage} />
          <Field label="Missing Next Stage" value={cs.missing_next_stage} valueClassName="text-amber-600" small />
        </div>

        {cs.narrative.length > 0 && (
          <div className="mt-4 pt-4 border-t border-slate-100 space-y-2">
            {cs.narrative.map((n, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span
                  className={`shrink-0 mt-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${
                    n.statement_type === 'FACT'
                      ? 'bg-blue-50 text-blue-700'
                      : n.statement_type === 'INTERPRETATION'
                      ? 'bg-purple-50 text-purple-700'
                      : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {n.statement_type}
                </span>
                <span className="text-slate-700">{n.text}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Correlation confidence */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
          <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-3">
            <Link2 className="w-4 h-4 text-blue-600" />
            Correlation
          </h3>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold text-slate-500">Status:</span>
            <span
              className={`px-2 py-0.5 rounded text-[11px] font-bold border ${
                c.correlation.correlation_status === 'CONFLICT'
                  ? 'bg-rose-50 text-rose-700 border-rose-200'
                  : c.correlation.correlation_status === 'COMPLETE'
                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                  : 'bg-slate-50 text-slate-600 border-slate-200'
              }`}
            >
              {c.correlation.correlation_status}
            </span>
            {c.correlation.correlation_confidence && (
              <span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${CONFIDENCE_STYLES[c.correlation.correlation_confidence] || ''}`}>
                {c.correlation.correlation_confidence} confidence
              </span>
            )}
          </div>
          {c.correlation.is_conflict && c.correlation.conflict_statement && (
            <div className="bg-rose-50 border border-rose-200 rounded-lg px-3 py-2 text-xs text-rose-800 font-medium mb-3">
              {c.correlation.conflict_statement}
            </div>
          )}
          <div className="space-y-1">
            {c.correlation.correlation_keys.map((k, i) => (
              <div key={i} className="flex justify-between text-xs">
                <span className="text-slate-500">{k.key_type}</span>
                <span className="font-mono text-slate-700">{k.value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Suggested route */}
        <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
          <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2 mb-3">
            <ShieldQuestion className="w-4 h-4 text-blue-600" />
            Suggested Route
          </h3>
          {c.routing.length === 0 ? (
            <div className="text-xs text-slate-400 italic">No routing recommendation -- no findings for this flow.</div>
          ) : (
            <div className="space-y-2">
              {c.routing.map((r, i) => (
                <div key={i} className="flex items-start justify-between gap-2 bg-slate-50 rounded-lg border border-slate-200 px-3 py-2">
                  <div>
                    <div className="text-xs font-bold text-slate-800">{r.suggested_team}</div>
                    <div className="text-[11px] text-slate-500">{r.reason}</div>
                  </div>
                  <span className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold border ${CONFIDENCE_STYLES[r.confidence] || ''}`}>
                    {r.confidence}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Failure explanation / findings */}
      {c.findings.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-500" />
            Failure Explanation ({c.findings.length})
          </div>
          <div className="divide-y divide-slate-100">
            {c.findings.map((f, i) => (
              <div key={i} className={`px-5 py-3 border-l-4 ${SEVERITY_STYLES[f.severity] || ''}`}>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-800">{f.finding_type}</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${CONFIDENCE_STYLES[f.confidence] || ''}`}>
                    {f.confidence} confidence
                  </span>
                </div>
                <p className="text-xs text-slate-600 mt-1">{f.statement}</p>
                <p className="text-[10px] font-mono text-slate-400 mt-1">
                  Evidence: {f.evidence_event_ids.join(', ') || '—'}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Visual swimlane -- supplements, never replaces, the exact table below */}
      {c.timeline.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
          <h3 className="text-xs font-bold text-slate-700 mb-3 flex items-center gap-1.5">
            <Waypoints className="w-3.5 h-3.5 text-slate-400" />
            Visual Timeline
          </h3>
          <Swimlane timeline={c.timeline} hoveredEventId={hoveredEventId} onHover={setHoveredEventId} />
        </div>
      )}

      {/* Correlated timeline / lifecycle stages */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
          <ListTree className="w-4 h-4 text-slate-500" />
          Correlated Timeline ({c.timeline.length} events)
        </div>
        {c.timeline.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No events in this window.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
                <tr>
                  <th className="px-4 py-2">Time</th>
                  <th className="px-4 py-2">Family</th>
                  <th className="px-4 py-2">Event Type</th>
                  <th className="px-4 py-2">Stage</th>
                  <th className="px-4 py-2">Level</th>
                  <th className="px-4 py-2">Source</th>
                  <th className="px-4 py-2">Event ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {c.timeline.map((e, i) => (
                  <tr
                    key={i}
                    onMouseEnter={() => setHoveredEventId(e.source_event_id)}
                    onMouseLeave={() => setHoveredEventId(null)}
                    className={
                      hoveredEventId === e.source_event_id
                        ? 'bg-blue-50'
                        : e.level === 'ERROR' || e.level === 'CRITICAL'
                        ? 'bg-rose-50/40'
                        : ''
                    }
                  >
                    <td className="px-4 py-2 font-mono text-slate-600">{fmtTime(e.event_timestamp)}</td>
                    <td className="px-4 py-2 text-slate-600">{e.log_family}</td>
                    <td className="px-4 py-2 font-semibold text-slate-800">{e.event_type || '—'}</td>
                    <td className="px-4 py-2">
                      {e.stage ? (
                        <span className="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 text-[10px] font-bold">{e.stage}</span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className={`px-4 py-2 font-bold ${e.level === 'ERROR' || e.level === 'CRITICAL' ? 'text-rose-600' : 'text-slate-500'}`}>
                      {e.level}
                    </td>
                    <td className="px-4 py-2 font-mono text-slate-400 truncate max-w-[140px]">{e.source_file}</td>
                    <td className="px-4 py-2 font-mono text-slate-400">{e.source_event_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Data quality / evidence */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
          <h3 className="text-xs font-bold text-slate-700 mb-3">Evidence Coverage</h3>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-slate-500">Evidence level</span>
              <span className="font-bold text-slate-800">{c.data_quality.evidence_level || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Parse status</span>
              <span className="font-bold text-slate-800">{c.data_quality.parse_status || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Missing stages</span>
              <span className="font-bold text-amber-600">{c.data_quality.missing_stages.length}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Not observable stages</span>
              <span className="font-bold text-slate-500">{c.data_quality.not_observable_stages.length}</span>
            </div>
          </div>
        </div>

        {c.similar_incident_flow_ids.length > 0 && (
          <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
            <h3 className="text-xs font-bold text-slate-700 mb-3 flex items-center gap-1.5">
              <ExternalLink className="w-3.5 h-3.5 text-slate-400" />
              Similar Incidents ({c.similar_incident_flow_ids.length})
            </h3>
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {c.similar_incident_flow_ids.map((id) => (
                <div key={id} className="font-mono text-[11px] text-slate-500 truncate">
                  {id}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const Field: React.FC<{ label: string; value: string | null; mono?: boolean; small?: boolean; valueClassName?: string }> = ({
  label,
  value,
  mono,
  small,
  valueClassName,
}) => (
  <div>
    <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-0.5">{label}</div>
    <div className={`${small ? 'text-[11px]' : 'text-sm'} font-bold ${mono ? 'font-mono' : ''} ${valueClassName || 'text-slate-800'}`}>
      {value || '—'}
    </div>
  </div>
);
