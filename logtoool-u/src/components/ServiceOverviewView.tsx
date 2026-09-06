import React, { useEffect, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell, LineChart, Line, Legend, LabelList,
} from 'recharts';
import { Activity, RefreshCw, CheckCircle2, XCircle, Clock, HelpCircle, Building2, Flame } from 'lucide-react';
import { api, ApiError } from '../api';
import { DrillThroughTarget, ServiceOverview } from '../types';
import { BRAND } from '../theme';
import { DateRangeFilter, DateRangeValue, toIsoRange, useSharedDateRange } from './DateRangeFilter';

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// Single-hue intensity scale for the heatmap -- 0 failures is the lightest
// cell, max failures in the current window is BRAND.blue600 (darkest).
// Interpolated linearly, not logarithmically -- fine at this data scale.
function heatCellColor(count: number, max: number): string {
  if (count === 0 || max === 0) return '#f1f5f9'; // slate-100
  const t = Math.min(1, count / max);
  // interpolate slate-100 (#f1f5f9) -> BRAND.blue600 (#052460)
  const from = { r: 0xf1, g: 0xf5, b: 0xf9 };
  const to = { r: 0x05, g: 0x24, b: 0x60 };
  const r = Math.round(from.r + (to.r - from.r) * t);
  const g = Math.round(from.g + (to.g - from.g) * t);
  const b = Math.round(from.b + (to.b - from.b) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

const OUTCOME_COLORS: Record<string, string> = {
  SUCCESS: BRAND.emerald500,
  FAILED: BRAND.rose500,
  PENDING_AT_LOG_END: BRAND.amber500,
  INCOMPLETE: BRAND.slate400,
  UNDETERMINED: BRAND.slate400,
};

const tooltipStyle = { backgroundColor: BRAND.slate900, borderRadius: 8, border: 'none', color: '#fff', fontSize: 12 };

interface Props {
  onInvestigate: (target: DrillThroughTarget) => void;
}

export const ServiceOverviewView: React.FC<Props> = ({ onInvestigate }) => {
  const [data, setData] = useState<ServiceOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useSharedDateRange();

  const fetchData = async (r: DateRangeValue) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const resp = await api.get<ServiceOverview>(`/api/analytics/service-overview?${params}`);
      setData(resp);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load service overview');
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
        <Activity className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
        <p className="text-sm font-medium">Building the correlated flow model…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load service overview</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
      </div>
    );
  }

  // Outcome 100% stacked bar -- built from outcome_trend, one bucket per bar.
  const stackKeys = ['SUCCESS', 'FAILED', 'PENDING_AT_LOG_END', 'INCOMPLETE'];
  const stackedData = data.outcome_trend.map((row) => {
    const total = stackKeys.reduce((sum, k) => sum + (Number(row[k]) || 0), 0) || 1;
    const pct: Record<string, number> = { bucket: row.bucket as any };
    stackKeys.forEach((k) => (pct[k] = ((Number(row[k]) || 0) / total) * 100));
    return pct;
  });

  // Failure Pareto -- bars = affected_flows, line = cumulative %.
  const totalFailures = data.top_failure_signatures.reduce((sum, f) => sum + f.affected_flows, 0) || 1;
  let cumulative = 0;
  const paretoData = data.top_failure_signatures.map((f) => {
    cumulative += f.affected_flows;
    return { pattern: f.pattern, affected_flows: f.affected_flows, cumulative_pct: (cumulative / totalFailures) * 100, evidence: f.representative_evidence };
  });

  const funnelMax = data.lifecycle_funnel[0]?.flow_count || 1;

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

      {/* KPI cards -- pending/incomplete/error kept strictly separate, per spec */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
        <KpiCard label="Total Flows" value={data.total_flows} icon={<Activity className="w-4 h-4 text-blue-600" />} iconBg="bg-blue-50" />
        <KpiCard label="Success" value={data.success} icon={<CheckCircle2 className="w-4 h-4 text-emerald-600" />} iconBg="bg-emerald-50" valueClassName="text-emerald-600" />
        <KpiCard label="Error" value={data.error} icon={<XCircle className="w-4 h-4 text-rose-600" />} iconBg="bg-rose-50" valueClassName="text-rose-600" />
        <KpiCard label="Pending" value={data.pending} icon={<Clock className="w-4 h-4 text-amber-600" />} iconBg="bg-amber-50" valueClassName="text-amber-600" />
        <KpiCard label="Incomplete" value={data.incomplete} icon={<HelpCircle className="w-4 h-4 text-slate-500" />} iconBg="bg-slate-100" valueClassName="text-slate-600" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Chart: Lifecycle funnel */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">Lifecycle Funnel</h3>
          <p className="text-xs text-slate-500 mb-4">Cumulative flow count reaching each stage. Click a stage to drill through.</p>
          {data.lifecycle_funnel.every((f) => f.flow_count === 0) ? (
            <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No lifecycle data in this window.</div>
          ) : (
            <div className="space-y-1.5">
              {data.lifecycle_funnel.map((stage) => (
                <button
                  key={stage.stage}
                  onClick={() => stage.sample_flow_ids[0] && onInvestigate({ kind: 'flow', value: stage.sample_flow_ids[0] })}
                  disabled={stage.sample_flow_ids.length === 0}
                  title={stage.sample_flow_ids.length ? `Drill through: ${stage.sample_flow_ids.length} sample flow(s)` : 'No flows'}
                  className="w-full text-left group cursor-pointer disabled:cursor-default"
                >
                  <div className="flex items-center justify-between text-[11px] mb-0.5">
                    <span className="font-semibold text-slate-600 group-hover:text-blue-600">{stage.stage}</span>
                    <span className="font-bold text-slate-800">{stage.flow_count}</span>
                  </div>
                  <div className="h-4 bg-slate-100 rounded overflow-hidden">
                    <div
                      className="h-full bg-blue-600 group-hover:bg-blue-500 transition-all"
                      style={{ width: `${Math.max(2, (stage.flow_count / funnelMax) * 100)}%` }}
                    />
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Chart: Outcome 100% stacked bar */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">Outcome Trend</h3>
          <p className="text-xs text-slate-500 mb-4">100% stacked by outcome, per time bucket -- pending/incomplete/error kept separate.</p>
          {stackedData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No outcome data in this window.</div>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={stackedData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} vertical={false} />
                  <XAxis dataKey="bucket" stroke={BRAND.slate500} fontSize={10} tickFormatter={(v: string) => v?.slice(5, 10)} />
                  <YAxis stroke={BRAND.slate500} fontSize={11} unit="%" domain={[0, 100]} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => `${v.toFixed(1)}%`} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {stackKeys.map((k) => (
                    <Bar key={k} dataKey={k} stackId="outcome" fill={OUTCOME_COLORS[k]} name={k} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* Chart: Failure Pareto */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-1">Failure Signature Pareto</h3>
        <p className="text-xs text-slate-500 mb-4">
          Ranked by affected flow count (never by percentage alone -- see the low-sample warning on small patterns). Click a bar to drill through.
        </p>
        {paretoData.length === 0 ? (
          <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No failures in this window.</div>
        ) : (
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={paretoData} margin={{ top: 4, right: 30, left: 0, bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} vertical={false} />
                <XAxis dataKey="pattern" stroke={BRAND.slate500} fontSize={10} angle={-30} textAnchor="end" height={70} interval={0} />
                <YAxis yAxisId="left" stroke={BRAND.slate500} fontSize={11} allowDecimals={false} />
                <YAxis yAxisId="right" orientation="right" stroke={BRAND.amber500} fontSize={11} unit="%" domain={[0, 100]} />
                <Tooltip contentStyle={tooltipStyle} />
                <Bar
                  yAxisId="left"
                  dataKey="affected_flows"
                  name="Affected flows"
                  fill={BRAND.blue600}
                  radius={[4, 4, 0, 0]}
                  cursor="pointer"
                  onClick={(d: any) => d?.evidence?.[0]?.flow_id && onInvestigate({ kind: 'flow', value: d.evidence[0].flow_id })}
                >
                  <LabelList dataKey="affected_flows" position="top" fontSize={11} fontWeight={700} fill={BRAND.slate700} />
                </Bar>
                <Line yAxisId="right" type="monotone" dataKey="cumulative_pct" name="Cumulative %" stroke={BRAND.amber500} strokeWidth={2} dot={{ r: 3 }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        {data.top_failure_signatures.some((f) => f.low_sample_warning) && (
          <div className="mt-3 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            {data.top_failure_signatures
              .filter((f) => f.low_sample_warning)
              .map((f) => (
                <div key={f.pattern}>
                  <span className="font-bold">{f.pattern}:</span> {f.low_sample_warning}
                </div>
              ))}
          </div>
        )}
      </div>

      {/* OTP/OOB mix */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-4">OTP / OOB Authentication Mix</h3>
        {Object.keys(data.otp_oob_mix).length === 0 ? (
          <div className="text-xs text-slate-400 italic">No authentication method resolved in this window.</div>
        ) : (
          <div className="flex gap-6">
            {Object.entries(data.otp_oob_mix).map(([method, count]) => (
              <div key={method} className="bg-slate-50 rounded-lg border border-slate-200 px-4 py-3">
                <div className="text-[10px] font-bold text-slate-500 uppercase">{method}</div>
                <div className="text-2xl font-extrabold text-slate-900">{count}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Chart: Issuer failure rate */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-1 flex items-center gap-2">
          <Building2 className="w-4 h-4 text-blue-600" />
          Issuer Failure Rate
        </h3>
        <p className="text-xs text-slate-500 mb-4">Failed flows over total flows, per issuer -- both counts always shown alongside the rate.</p>
        {data.issuer_failure_rates.length === 0 ? (
          <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No issuer data in this window.</div>
        ) : (
          <div style={{ height: Math.max(160, data.issuer_failure_rates.length * 32) }} className="w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data.issuer_failure_rates.map((r) => ({ ...r, failure_rate_pct: (r.failure_rate || 0) * 100 }))}
                layout="vertical"
                margin={{ top: 4, right: 40, left: 8, bottom: 4 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} horizontal={false} />
                <XAxis type="number" stroke={BRAND.slate500} fontSize={10} unit="%" domain={[0, 100]} />
                <YAxis type="category" dataKey="issuer_id" stroke={BRAND.slate500} fontSize={11} width={90} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v: number, _n, p: any) => [`${v.toFixed(1)}% (${p.payload.failed_flows}/${p.payload.total_flows})`, 'Failure rate']}
                />
                <Bar dataKey="failure_rate_pct" fill={BRAND.rose500} radius={[0, 4, 4, 0]}>
                  <LabelList dataKey="failure_rate_pct" position="right" fontSize={10} formatter={(v: number) => `${v.toFixed(0)}%`} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Chart: Failure heatmap by time-of-day */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-1 flex items-center gap-2">
          <Flame className="w-4 h-4 text-rose-500" />
          Failure Heatmap by Time of Day
        </h3>
        <p className="text-xs text-slate-500 mb-4">Failed-flow density by hour of day and day of week. Hover a cell for exact counts.</p>
        {data.failure_heatmap.every((c) => c.failed_count === 0) ? (
          <div className="h-40 flex items-center justify-center text-xs text-slate-400 italic">No failures in this window.</div>
        ) : (
          (() => {
            const maxFailed = Math.max(1, ...data.failure_heatmap.map((c) => c.failed_count));
            const cellByKey = new Map(data.failure_heatmap.map((c) => [`${c.day_of_week}-${c.hour}`, c]));
            return (
              <div className="overflow-x-auto">
                <div className="inline-grid" style={{ gridTemplateColumns: `40px repeat(24, 20px)`, gap: '2px' }}>
                  <div />
                  {Array.from({ length: 24 }, (_, h) => (
                    <div key={h} className="text-[8px] text-slate-400 text-center">
                      {h % 4 === 0 ? h : ''}
                    </div>
                  ))}
                  {DAY_LABELS.map((label, day) => (
                    <React.Fragment key={day}>
                      <div className="text-[10px] font-semibold text-slate-500 flex items-center">{label}</div>
                      {Array.from({ length: 24 }, (_, hour) => {
                        const cell = cellByKey.get(`${day}-${hour}`);
                        return (
                          <div
                            key={hour}
                            className="w-5 h-5 rounded-sm"
                            style={{ backgroundColor: heatCellColor(cell?.failed_count || 0, maxFailed) }}
                            title={cell ? `${label} ${hour}:00 -- ${cell.failed_count} failed / ${cell.total_count} total` : `${label} ${hour}:00 -- no flows`}
                          />
                        );
                      })}
                    </React.Fragment>
                  ))}
                </div>
              </div>
            );
          })()
        )}
      </div>
    </div>
  );
};

const KpiCard: React.FC<{ label: string; value: number; icon: React.ReactNode; iconBg: string; valueClassName?: string }> = ({
  label,
  value,
  icon,
  iconBg,
  valueClassName,
}) => (
  <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-2xs">
    <div className="flex items-center justify-between mb-2">
      <span className="text-[11px] font-semibold text-slate-500">{label}</span>
      <span className={`w-7 h-7 rounded-lg flex items-center justify-center ${iconBg}`}>{icon}</span>
    </div>
    <div className={`text-2xl font-extrabold text-slate-900 ${valueClassName || ''}`}>{value.toLocaleString()}</div>
  </div>
);
