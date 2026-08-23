import React, { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell, LabelList } from 'recharts';
import { ShieldCheck, RefreshCw, ShieldAlert, Repeat, FileWarning } from 'lucide-react';
import { api, ApiError } from '../api';
import { DrillThroughTarget, SecurityQuality } from '../types';
import { BRAND } from '../theme';
import { DateRangeFilter, DateRangeValue, defaultRange, toIsoRange } from './DateRangeFilter';

const tooltipStyle = { backgroundColor: BRAND.slate900, borderRadius: 8, border: 'none', color: '#fff', fontSize: 12 };

const CONFIDENCE_COLORS: Record<string, string> = {
  HIGH: BRAND.emerald500,
  MEDIUM: BRAND.amber500,
  LOW: BRAND.slate400,
  NONE: BRAND.slate200,
};

interface Props {
  onInvestigate: (target: DrillThroughTarget) => void;
}

export const SecurityQualityView: React.FC<Props> = ({ onInvestigate }) => {
  const [data, setData] = useState<SecurityQuality | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DateRangeValue>(defaultRange());

  const fetchData = async (r: DateRangeValue) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const resp = await api.get<SecurityQuality>(`/api/analytics/security-quality?${params}`);
      setData(resp);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load security / data quality data');
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
        <ShieldCheck className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
        <p className="text-sm font-medium">Scoring analytical confidence…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load security / data quality data</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
      </div>
    );
  }

  const confidenceData = Object.entries(data.correlation_quality_breakdown.by_confidence).map(([confidence, count]) => ({
    confidence,
    count,
  }));

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white border border-slate-200 p-5 rounded-xl shadow-2xs">
        <div>
          <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-blue-600" />
            Security / Data Quality
          </h1>
          <p className="text-xs text-slate-500 mt-1">Is there enough reliable evidence to trust these conclusions?</p>
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

      {/* Scorecard */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-slate-900">Data-Quality Scorecard</h3>
          <div className="text-3xl font-extrabold text-slate-900">{data.scorecard.overall_score.toFixed(1)}</div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Object.entries(data.scorecard.score_breakdown).map(([label, value]) => (
            <div key={label} className="bg-slate-50 rounded-lg border border-slate-200 p-3">
              <div className="text-[10px] font-bold text-slate-500 uppercase">{label.replace(/_/g, ' ')}</div>
              <div className="text-lg font-extrabold text-slate-900">{(value as number).toFixed(1)}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Key counts -- kept as separate, explicit metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        <MetricCard label="Mismatches" value={data.scorecard.field_mismatch_count} icon={<FileWarning className="w-4 h-4 text-rose-600" />} iconBg="bg-rose-50" />
        <MetricCard label="Repeated Attempts" value={data.repeated_attempts} icon={<Repeat className="w-4 h-4 text-amber-600" />} iconBg="bg-amber-50" />
        <MetricCard label="Sensitive-Data Exceptions" value={data.sensitive_data_findings.length} icon={<ShieldAlert className="w-4 h-4 text-rose-600" />} iconBg="bg-rose-50" />
        <MetricCard label="Incomplete Flows" value={data.incomplete_flows} icon={<ShieldCheck className="w-4 h-4 text-amber-600" />} iconBg="bg-amber-50" />
        <MetricCard label="Uncorrelated Flows" value={data.uncorrelated_flows} icon={<ShieldCheck className="w-4 h-4 text-slate-500" />} iconBg="bg-slate-100" />
        <MetricCard label="Correlation Conflicts" value={data.scorecard.correlation_quality.correlation_conflicts} icon={<ShieldAlert className="w-4 h-4 text-rose-600" />} iconBg="bg-rose-50" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Chart: Correlation quality */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">Correlation Quality</h3>
          <p className="text-xs text-slate-500 mb-4">Flow count by correlation confidence level.</p>
          {confidenceData.length === 0 ? (
            <div className="h-56 flex items-center justify-center text-xs text-slate-400 italic">No flows in this window.</div>
          ) : (
            <div className="h-56 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={confidenceData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} vertical={false} />
                  <XAxis dataKey="confidence" stroke={BRAND.slate500} fontSize={11} />
                  <YAxis stroke={BRAND.slate500} fontSize={11} allowDecimals={false} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="count" name="Flows" radius={[4, 4, 0, 0]}>
                    {confidenceData.map((entry) => (
                      <Cell key={entry.confidence} fill={CONFIDENCE_COLORS[entry.confidence] || BRAND.slate400} />
                    ))}
                    <LabelList dataKey="count" position="top" fontSize={11} fontWeight={700} fill={BRAND.slate700} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <p className="text-[10px] text-slate-400 mt-2">{data.correlation_quality_breakdown.parser_note}</p>
        </div>

        {/* Chart: Field mismatch heatmap */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">Field Mismatch Heatmap</h3>
          <p className="text-xs text-slate-500 mb-4">Mismatch count by field, across correlated flows. Click a bar to drill through.</p>
          {data.field_mismatch_heatmap.length === 0 ? (
            <div className="h-56 flex items-center justify-center text-xs text-slate-400 italic">No field mismatches in this window.</div>
          ) : (
            <div style={{ height: Math.max(200, data.field_mismatch_heatmap.length * 32) }} className="w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.field_mismatch_heatmap} layout="vertical" margin={{ top: 4, right: 30, left: 4, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} horizontal={false} />
                  <XAxis type="number" stroke={BRAND.slate500} fontSize={11} allowDecimals={false} />
                  <YAxis type="category" dataKey="field_name" stroke={BRAND.slate700} fontSize={11} width={110} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar
                    dataKey="mismatch_count"
                    fill={BRAND.rose500}
                    radius={[0, 6, 6, 0]}
                    barSize={18}
                    cursor="pointer"
                    onClick={(d: any) => d?.sample_flow_ids?.[0] && onInvestigate({ kind: 'flow', value: d.sample_flow_ids[0] })}
                  >
                    <LabelList dataKey="mismatch_count" position="right" fontSize={11} fontWeight={700} fill={BRAND.slate700} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* Parser quality */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-4">Parser Quality</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatBlock label="Parse Success" value={data.scorecard.parse_quality.parse_success} valueClassName="text-emerald-600" />
          <StatBlock label="Partial Parsing" value={data.scorecard.parse_quality.partial_parsing} valueClassName="text-amber-600" />
          <StatBlock label="Parse Failure" value={data.scorecard.parse_quality.parse_failure} valueClassName="text-rose-600" />
          <StatBlock label="Fallback Parsing" value={data.scorecard.parse_quality.fallback_parsing} valueClassName="text-slate-600" />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-3">
          <StatBlock label="Missing Identifiers" value={data.scorecard.evidence_quality.missing_identifiers} />
          <StatBlock label="Missing Timestamps" value={data.scorecard.evidence_quality.missing_timestamps} />
          <StatBlock label="Unknown Merchant" value={data.scorecard.evidence_quality.unknown_merchant} />
          <StatBlock label="Unmatched Events" value={data.scorecard.evidence_quality.unmatched_events} />
          <StatBlock label="Uncorrelated Events" value={data.scorecard.evidence_quality.uncorrelated_events} />
        </div>
      </div>

      {/* Sensitive data exceptions -- category + protected reference only, NEVER the raw value */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-rose-500" />
          Sensitive-Data Exceptions ({data.sensitive_data_findings.length})
        </div>
        {data.sensitive_data_findings.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No sensitive-data patterns detected in raw text this window.</div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
              <tr>
                <th className="px-4 py-2">Category</th>
                <th className="px-4 py-2">Protected Reference</th>
                <th className="px-4 py-2">Safe Hint</th>
                <th className="px-4 py-2">Source File</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.sensitive_data_findings.map((f, i) => (
                <tr key={i}>
                  <td className="px-4 py-2 font-bold text-rose-700">{f.category}</td>
                  <td className="px-4 py-2 font-mono text-slate-500">{f.protected_reference}</td>
                  <td className="px-4 py-2 text-slate-600">{f.safe_hint || '—'}</td>
                  <td className="px-4 py-2 font-mono text-slate-400 truncate max-w-[160px]">{f.source_file}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Exception table */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700">
          Exception Table ({data.exception_table.length})
        </div>
        {data.exception_table.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No exceptions in this window.</div>
        ) : (
          <div className="max-h-96 overflow-y-auto divide-y divide-slate-100">
            {data.exception_table.map((e, i) => (
              <button
                key={i}
                onClick={() => e.flow_id && onInvestigate({ kind: 'flow', value: e.flow_id })}
                disabled={!e.flow_id}
                className="w-full text-left px-5 py-2.5 hover:bg-slate-50 cursor-pointer disabled:cursor-default flex items-start gap-3"
              >
                <span className="shrink-0 px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-bold uppercase">
                  {e.category.replace(/_/g, ' ')}
                </span>
                <span className="text-xs text-slate-700">{e.description}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

const MetricCard: React.FC<{ label: string; value: number; icon: React.ReactNode; iconBg: string }> = ({ label, value, icon, iconBg }) => (
  <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-2xs">
    <div className="flex items-center justify-between mb-2">
      <span className="text-[10px] font-semibold text-slate-500">{label}</span>
      <span className={`w-6 h-6 rounded-lg flex items-center justify-center ${iconBg}`}>{icon}</span>
    </div>
    <div className="text-xl font-extrabold text-slate-900">{value.toLocaleString()}</div>
  </div>
);

const StatBlock: React.FC<{ label: string; value: number; valueClassName?: string }> = ({ label, value, valueClassName }) => (
  <div className="bg-slate-50 rounded-lg border border-slate-200 p-3">
    <div className="text-[10px] font-bold text-slate-500 uppercase">{label}</div>
    <div className={`text-lg font-extrabold ${valueClassName || 'text-slate-900'}`}>{value}</div>
  </div>
);
