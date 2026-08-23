import React, { useEffect, useMemo, useState } from 'react';
import { GitFork, RefreshCw, AlertTriangle, Link2, Users } from 'lucide-react';
import { api, ApiError } from '../api';
import { CorrelationExplorerResult, DrillThroughTarget } from '../types';
import { DateRangeFilter, DateRangeValue, defaultRange, toIsoRange } from './DateRangeFilter';

interface Props {
  onInvestigate: (target: DrillThroughTarget) => void;
}

const EDGE_COLORS: Record<string, string> = {
  conflict: '#e51f1f',
  candidate_link: '#ff8800',
  low_confidence_hint: '#8892a1',
};

const EDGE_LABELS: Record<string, string> = {
  conflict: 'Conflict',
  candidate_link: 'Candidate link',
  low_confidence_hint: 'Low-confidence hint',
};

// Small, deliberately simple circular layout -- no force-simulation library.
// These clusters are typically a handful of flows (a conflict/candidate-link
// neighborhood), never hundreds, so a fixed circular placement is legible
// without needing physics.
const GraphPanel: React.FC<{ result: CorrelationExplorerResult; onInvestigate: (t: DrillThroughTarget) => void }> = ({
  result,
  onInvestigate,
}) => {
  const { nodes, edges } = result.graph;
  const size = 360;
  const center = size / 2;
  const radius = size / 2 - 48;

  const positions = useMemo(() => {
    const map: Record<string, { x: number; y: number }> = {};
    nodes.forEach((n, i) => {
      const angle = (i / Math.max(1, nodes.length)) * 2 * Math.PI - Math.PI / 2;
      map[n.flow_id] = { x: center + radius * Math.cos(angle), y: center + radius * Math.sin(angle) };
    });
    return map;
  }, [nodes]);

  if (nodes.length === 0) {
    return <div className="h-72 flex items-center justify-center text-xs text-slate-400 italic">No correlation conflicts, candidate links, or low-confidence hints in this window.</div>;
  }

  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size} className="overflow-visible">
        {edges.map((e, i) => {
          const a = positions[e.source];
          const b = positions[e.target];
          if (!a || !b) return null;
          return (
            <line
              key={i}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={EDGE_COLORS[e.edge_type] || '#8892a1'}
              strokeWidth={2}
              strokeDasharray={e.edge_type === 'low_confidence_hint' ? '4 3' : undefined}
              opacity={0.8}
            >
              <title>{`${EDGE_LABELS[e.edge_type]}: ${e.label}`}</title>
            </line>
          );
        })}
        {nodes.map((n) => {
          const pos = positions[n.flow_id];
          if (!pos) return null;
          return (
            <g key={n.flow_id} className="cursor-pointer" onClick={() => onInvestigate({ kind: 'flow', value: n.flow_id })}>
              <circle cx={pos.x} cy={pos.y} r={9} fill="#036fd0" stroke="#fff" strokeWidth={2} />
              <text x={pos.x} y={pos.y - 14} textAnchor="middle" fontSize={10} fontWeight={700} fill="#2a2f34">
                {n.transaction_id || n.flow_id.slice(0, 14)}
              </text>
              <title>{`${n.flow_id} (${n.correlation_status || 'unknown'})`}</title>
            </g>
          );
        })}
      </svg>
      <div className="flex flex-wrap gap-4 mt-3">
        {Object.entries(EDGE_LABELS).map(([type, label]) => (
          <div key={type} className="flex items-center gap-1.5 text-[11px] text-slate-600">
            <span className="w-3 h-0.5 rounded" style={{ backgroundColor: EDGE_COLORS[type] }} />
            {label}
          </div>
        ))}
      </div>
    </div>
  );
};

export const CorrelationExplorerView: React.FC<Props> = ({ onInvestigate }) => {
  const [data, setData] = useState<CorrelationExplorerResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DateRangeValue>(defaultRange());

  const fetchData = async (r: DateRangeValue) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const resp = await api.get<CorrelationExplorerResult>(`/api/analytics/correlation-explorer?${params}`);
      setData(resp);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load correlation explorer');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  if (loading && !data) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-slate-400">
        <GitFork className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
        <p className="text-sm font-medium">Loading correlation model…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load correlation explorer</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white border border-slate-200 p-5 rounded-xl shadow-2xs">
        <div>
          <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <GitFork className="w-5 h-5 text-blue-600" />
            Correlation Explorer
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Every conflicting merge, proposed medium-confidence link, and shared-contact hint the correlation engine
            found -- never silently merged or hidden. Click any flow to investigate it.
          </p>
        </div>
        <div className="flex items-end gap-3">
          <DateRangeFilter value={range} onChange={setRange} />
          <button
            onClick={() => fetchData(range)}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 mb-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-1">Correlation Graph</h3>
        <p className="text-xs text-slate-500 mb-4">Nodes are flows; edges show why they were flagged, never merged automatically.</p>
        <GraphPanel result={data} onInvestigate={onInvestigate} />
      </div>

      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-500" />
          Correlation Conflicts ({data.conflicts.length})
        </div>
        {data.conflicts.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No conflicts in this window.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {data.conflicts.map((c) => (
              <div key={c.conflict_id} className="px-5 py-3">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-bold text-slate-800">
                    Triggering key: <span className="font-mono text-rose-600">{c.triggering_key_type}</span>
                  </span>
                  <div className="flex gap-1.5">
                    {c.affected_flow_ids.map((fid) => (
                      <button
                        key={fid}
                        onClick={() => onInvestigate({ kind: 'flow', value: fid })}
                        className="text-[10px] font-mono bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded px-2 py-0.5 cursor-pointer"
                      >
                        {fid.slice(0, 20)}…
                      </button>
                    ))}
                  </div>
                </div>
                {c.conflicting_identifiers.map((ci, i) => (
                  <div key={i} className="text-[11px] text-slate-500 font-mono">
                    {ci.key_type}: {ci.flow_a_value} ≠ {ci.flow_b_value}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
          <Link2 className="w-4 h-4 text-amber-500" />
          Candidate Links ({data.candidate_links.length})
        </div>
        {data.candidate_links.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No medium-confidence candidate links in this window.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {data.candidate_links.map((l, i) => (
              <div key={i} className="px-5 py-3 flex items-center justify-between">
                <div>
                  <span className="text-xs font-bold text-slate-800">{l.link_type}</span>
                  <p className="text-[11px] text-slate-500">{l.note}</p>
                </div>
                <div className="flex gap-1.5">
                  {[l.flow_a_id, l.flow_b_id].map((fid) => (
                    <button
                      key={fid}
                      onClick={() => onInvestigate({ kind: 'flow', value: fid })}
                      className="text-[10px] font-mono bg-amber-50 hover:bg-amber-100 text-amber-700 border border-amber-200 rounded px-2 py-0.5 cursor-pointer"
                    >
                      {fid.slice(0, 20)}…
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
          <Users className="w-4 h-4 text-slate-500" />
          Low-Confidence Hints ({data.low_confidence_hints.length})
        </div>
        {data.low_confidence_hints.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No shared-contact hints in this window.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {data.low_confidence_hints.map((h, i) => (
              <div key={i} className="px-5 py-3 flex items-center justify-between">
                <div>
                  <span className="text-xs font-bold text-slate-800">{h.hint_type}</span>
                  <span className="ml-2 font-mono text-[11px] text-slate-500">{h.value}</span>
                  <p className="text-[11px] text-slate-500">{h.note}</p>
                </div>
                <div className="flex flex-wrap gap-1.5 max-w-xs justify-end">
                  {h.flow_ids.map((fid) => (
                    <button
                      key={fid}
                      onClick={() => onInvestigate({ kind: 'flow', value: fid })}
                      className="text-[10px] font-mono bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-200 rounded px-2 py-0.5 cursor-pointer"
                    >
                      {fid.slice(0, 20)}…
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
