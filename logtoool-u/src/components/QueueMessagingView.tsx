import React, { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, LabelList } from 'recharts';
import { Workflow, RefreshCw, AlertTriangle } from 'lucide-react';
import { api, ApiError } from '../api';
import { DrillThroughTarget, QueueMessaging } from '../types';
import { BRAND } from '../theme';
import { DateRangeFilter, DateRangeValue, toIsoRange, useSharedDateRange } from './DateRangeFilter';

const tooltipStyle = { backgroundColor: BRAND.slate900, borderRadius: 8, border: 'none', color: '#fff', fontSize: 12 };

interface Props {
  onInvestigate: (target: DrillThroughTarget) => void;
}

export const QueueMessagingView: React.FC<Props> = ({ onInvestigate }) => {
  const [data, setData] = useState<QueueMessaging | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useSharedDateRange();

  const fetchData = async (r: DateRangeValue) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const resp = await api.get<QueueMessaging>(`/api/analytics/queue-messaging?${params}`);
      setData(resp);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load queue & messaging data');
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
        <Workflow className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
        <p className="text-sm font-medium">Joining application and processor events by IA tracker…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load queue & messaging data</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
      </div>
    );
  }

  const funnelStages = [
    { name: 'Generated', value: data.generated, fill: BRAND.blue600 },
    { name: 'App Queued', value: data.application_queued, fill: BRAND.blue500 },
    { name: 'Processor Received', value: data.processor_received, fill: BRAND.blue400 },
    { name: 'Downstream Routed', value: data.downstream_routed, fill: BRAND.emerald600 },
    { name: 'Validated', value: data.validated, fill: BRAND.emerald500 },
  ];
  const funnelMax = funnelStages[0]?.value || 1;

  const queueDistData = Object.entries(data.queue_distribution)
    .map(([queue, count]) => ({ queue, count: count as number }))
    .sort((a, b) => b.count - a.count);

  return (
    <div className="space-y-6">
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs px-4 py-3 flex flex-wrap items-end gap-3">
        <DateRangeFilter value={range} onChange={setRange} />
        <button
          onClick={() => fetchData(range)}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/40"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          {loading ? 'Loading…' : 'Apply'}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Chart: OTP handoff funnel */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">OTP Handoff Funnel</h3>
          <p className="text-xs text-slate-500 mb-4">
            Joined on the exact IA tracker. Never claims delivery -- "Downstream Routed" means the processor selected a queue, not that the SMS reached the customer.
          </p>
          <div className="space-y-2">
            {funnelStages.map((stage) => (
              <div key={stage.name}>
                <div className="flex items-center justify-between text-[11px] mb-0.5">
                  <span className="font-semibold text-slate-600">{stage.name}</span>
                  <span className="font-bold text-slate-800">{stage.value}</span>
                </div>
                <div className="h-5 bg-slate-100 rounded overflow-hidden">
                  <div
                    className="h-full transition-all"
                    style={{ width: `${Math.max(2, (stage.value / funnelMax) * 100)}%`, backgroundColor: stage.fill }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Chart: Queue routing */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">Queue Routing</h3>
          <p className="text-xs text-slate-500 mb-4">Message distribution by named downstream queue.</p>
          {queueDistData.length === 0 ? (
            <div className="h-56 flex items-center justify-center text-xs text-slate-400 italic">No queue-name data in this window.</div>
          ) : (
            <div style={{ height: Math.max(220, queueDistData.length * 34) }} className="w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={queueDistData} layout="vertical" margin={{ top: 4, right: 30, left: 4, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} horizontal={false} />
                  <XAxis type="number" stroke={BRAND.slate500} fontSize={11} allowDecimals={false} />
                  <YAxis type="category" dataKey="queue" stroke={BRAND.slate700} fontSize={11} width={140} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="count" fill={BRAND.blue600} radius={[0, 6, 6, 0]} barSize={18}>
                    <LabelList dataKey="count" position="right" fontSize={11} fontWeight={700} fill={BRAND.slate700} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* Handoff latency */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-4">Handoff Transition Latency</h3>
        {data.handoff_latency.length === 0 ? (
          <div className="text-xs text-slate-400 italic">No stage-to-stage transitions with resolvable timestamps in this window.</div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
              <tr>
                <th className="px-3 py-2">Transition</th>
                <th className="px-3 py-2">Samples</th>
                <th className="px-3 py-2">Median</th>
                <th className="px-3 py-2">P95</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.handoff_latency.map((t, i) => (
                <tr key={i}>
                  <td className="px-3 py-2 font-semibold text-slate-700">
                    {t.from_stage} → {t.to_stage}
                  </td>
                  <td className="px-3 py-2 text-slate-500">{t.sample_count}</td>
                  <td className="px-3 py-2 font-bold text-slate-800">{t.median_ms != null ? `${t.median_ms.toFixed(0)}ms` : '—'}</td>
                  <td className="px-3 py-2 font-bold text-slate-800">{t.p95_ms != null ? `${t.p95_ms.toFixed(0)}ms` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Unmatched / orphan */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <TrackerGapCard
          title="Unmatched Messages"
          description="Application queued the message, but the processor never recorded receiving it."
          count={data.unmatched}
          trackers={data.unmatched_tracker_nos}
          onInvestigate={onInvestigate}
        />
        <TrackerGapCard
          title="Orphan Messages"
          description="Processor received a message with no application-side event at all."
          count={data.orphan}
          trackers={data.orphan_tracker_nos}
          onInvestigate={onInvestigate}
        />
      </div>
    </div>
  );
};

const TrackerGapCard: React.FC<{
  title: string;
  description: string;
  count: number;
  trackers: string[];
  onInvestigate: (target: DrillThroughTarget) => void;
}> = ({ title, description, count, trackers, onInvestigate }) => (
  <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
    <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
      <div>
        <div className="text-xs font-bold text-slate-800 flex items-center gap-1.5">
          {count > 0 && <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />}
          {title} ({count})
        </div>
        <p className="text-[11px] text-slate-500 mt-0.5">{description}</p>
      </div>
    </div>
    {trackers.length === 0 ? (
      <div className="px-5 py-6 text-center text-xs text-slate-400 italic">None in this window.</div>
    ) : (
      <div className="max-h-48 overflow-y-auto divide-y divide-slate-100">
        {trackers.map((t) => (
          <button
            key={t}
            onClick={() => onInvestigate({ kind: 'tracker', value: t })}
            className="w-full text-left px-5 py-2 text-xs font-mono text-slate-600 hover:bg-slate-50 hover:text-blue-600 cursor-pointer"
          >
            {t}
          </button>
        ))}
      </div>
    )}
  </div>
);
