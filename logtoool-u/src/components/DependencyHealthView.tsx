import React, { useEffect, useState } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts';
import { Network, RefreshCw, AlertTriangle } from 'lucide-react';
import { api, ApiError } from '../api';
import { DependencyHealth, DependencyMetricView, DependencyTrendBucket, DrillThroughTarget, OobDurationSample } from '../types';
import { BRAND } from '../theme';
import { DateRangeFilter, DateRangeValue, defaultRange, toIsoRange } from './DateRangeFilter';

const DEPENDENCY_LABELS: Record<string, string> = {
  V_PLUS: 'V+',
  POSTILION: 'Postilion',
  DATABASE_SQL: 'Database / SQL',
  BANK_API: 'Bank API',
  OOB_API: 'OOB API',
  OTP_ONLINE_PROCESSOR: 'OTP Online Processor',
};

const tooltipStyle = { backgroundColor: BRAND.slate900, borderRadius: 8, border: 'none', color: '#fff', fontSize: 12 };

const fmtMs = (ms: number | null) => (ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(0)}ms`);
const fmtPct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);

interface Props {
  onInvestigate: (target: DrillThroughTarget) => void;
}

export const DependencyHealthView: React.FC<Props> = ({ onInvestigate }) => {
  const [data, setData] = useState<DependencyHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DateRangeValue>(defaultRange());
  const [selectedDep, setSelectedDep] = useState<string>('V_PLUS');
  const [trend, setTrend] = useState<DependencyTrendBucket[]>([]);
  const [oobSamples, setOobSamples] = useState<OobDurationSample[]>([]);

  const fetchOverview = async (r: DateRangeValue) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const resp = await api.get<DependencyHealth>(`/api/analytics/dependency-health?${params}`);
      setData(resp);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load dependency health');
    } finally {
      setLoading(false);
    }
  };

  const fetchTrend = async (r: DateRangeValue, dependency: string) => {
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, bucket_minutes: '60' });
      const resp = await api.get<{ buckets: DependencyTrendBucket[] }>(`/api/analytics/dependency-health/${dependency}/trend?${params}`);
      setTrend(resp.buckets);
    } catch {
      setTrend([]);
    }
  };

  const fetchOobHistogram = async (r: DateRangeValue) => {
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const resp = await api.get<{ samples: OobDurationSample[] }>(`/api/analytics/dependency-health/oob-api/duration-histogram?${params}`);
      setOobSamples(resp.samples);
    } catch {
      setOobSamples([]);
    }
  };

  useEffect(() => {
    fetchOverview(range);
    fetchOobHistogram(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  useEffect(() => {
    fetchTrend(range, selectedDep);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, selectedDep]);

  if (loading && !data) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-slate-400">
        <Network className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
        <p className="text-sm font-medium">Pairing dependency requests and responses…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load dependency health</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
      </div>
    );
  }

  // Histogram bucketing (presentation only -- raw samples come from the API).
  const histBucketMs = 500;
  const histBuckets: Record<number, number> = {};
  oobSamples.forEach((s) => {
    const bucket = Math.floor(s.latency_ms / histBucketMs) * histBucketMs;
    histBuckets[bucket] = (histBuckets[bucket] || 0) + 1;
  });
  const histogramData = Object.entries(histBuckets)
    .map(([bucket, count]) => ({ bucket: `${(Number(bucket) / 1000).toFixed(1)}s`, count, bucketMs: Number(bucket) }))
    .sort((a, b) => a.bucketMs - b.bucketMs);

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white border border-slate-200 p-5 rounded-xl shadow-2xs">
        <div>
          <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Network className="w-5 h-5 text-blue-600" />
            Dependency Health
          </h1>
          <p className="text-xs text-slate-500 mt-1">V+, Postilion, Database/SQL, Bank API, OOB API, and queue throughput.</p>
        </div>
        <div className="flex items-end gap-3">
          <DateRangeFilter value={range} onChange={setRange} />
          <button
            onClick={() => {
              fetchOverview(range);
              fetchOobHistogram(range);
            }}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 mb-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {Object.entries(data.dependencies).map(([name, dep]) => {
          const m = dep as DependencyMetricView;
          return (
          <button
            key={name}
            onClick={() => setSelectedDep(name)}
            className={`text-left bg-white p-4 rounded-xl border shadow-2xs cursor-pointer transition-colors ${
              selectedDep === name ? 'border-blue-400 ring-2 ring-blue-100' : 'border-slate-200 hover:border-slate-300'
            }`}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-slate-800">{DEPENDENCY_LABELS[name] || name}</span>
              {m.note && <AlertTriangle className="w-3.5 h-3.5 text-amber-500" aria-label={m.note} />}
            </div>
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              <MetricCell label="Requests" value={m.request_volume.toLocaleString()} />
              <MetricCell label="Success Rate" value={fmtPct(m.success_rate)} valueClassName="text-emerald-600" />
              <MetricCell label="Error Rate" value={fmtPct(m.error_rate)} valueClassName={m.error_rate && m.error_rate > 0 ? 'text-rose-600' : ''} />
              <MetricCell label="Median Latency" value={fmtMs(m.median_latency_ms)} />
              <MetricCell label="P95 Latency" value={fmtMs(m.p95_latency_ms)} />
              <MetricCell label="Timeouts" value={m.timeout_count} valueClassName={m.timeout_count > 0 ? 'text-rose-600' : ''} />
              <MetricCell label="Incomplete" value={m.incomplete_count} valueClassName={m.incomplete_count > 0 ? 'text-amber-600' : ''} />
            </div>
            {m.note && <p className="text-[10px] text-slate-400 mt-2 italic">{m.note}</p>}
          </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Chart: Dependency P95 trend */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">
            {DEPENDENCY_LABELS[selectedDep] || selectedDep} — P95 Latency Trend
          </h3>
          <p className="text-xs text-slate-500 mb-4">Hourly buckets. Select a dependency card above to change.</p>
          {trend.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No time-series data in this window.</div>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trend} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} vertical={false} />
                  <XAxis dataKey="bucket_start" stroke={BRAND.slate500} fontSize={10} tickFormatter={(v: string) => v?.slice(11, 16)} />
                  <YAxis stroke={BRAND.slate500} fontSize={11} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmtMs(v)} />
                  <Line type="monotone" dataKey="p95_latency_ms" name="P95 (ms)" stroke={BRAND.rose500} strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="median_latency_ms" name="Median (ms)" stroke={BRAND.blue500} strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Chart: OOB duration histogram */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">OOB Duration Histogram</h3>
          <p className="text-xs text-slate-500 mb-4">Completed OOB API request/response latencies. Click a bar to drill through.</p>
          {histogramData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No completed OOB pairs in this window.</div>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={histogramData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} vertical={false} />
                  <XAxis dataKey="bucket" stroke={BRAND.slate500} fontSize={11} />
                  <YAxis stroke={BRAND.slate500} fontSize={11} allowDecimals={false} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar
                    dataKey="count"
                    name="OOB pairs"
                    fill={BRAND.blue600}
                    radius={[4, 4, 0, 0]}
                    cursor="pointer"
                    onClick={(d: any) => {
                      const sample = oobSamples.find((s) => Math.floor(s.latency_ms / histBucketMs) * histBucketMs === d.bucketMs);
                      // join_key is the tracker/correlation id the pair was matched on (see
                      // RequestResponsePair.join_key) -- resolved via the tracker search, since
                      // individual dependency pairs don't carry a resolved flow_id directly.
                      if (sample?.join_key) onInvestigate({ kind: 'tracker', value: sample.join_key });
                    }}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* Queue summary */}
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-4">Queue Throughput</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {Object.entries(data.queues).map(([key, value]) => (
            <div key={key} className="bg-slate-50 rounded-lg border border-slate-200 p-3">
              <div className="text-[10px] font-bold text-slate-500 uppercase">{key.replace(/_/g, ' ')}</div>
              <div className="text-xl font-extrabold text-slate-900">{value}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

const MetricCell: React.FC<{ label: string; value: string | number; valueClassName?: string }> = ({ label, value, valueClassName }) => (
  <div>
    <div className="text-slate-400">{label}</div>
    <div className={`font-bold ${valueClassName || 'text-slate-800'}`}>{value}</div>
  </div>
);
