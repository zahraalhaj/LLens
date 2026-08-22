import React, { useEffect, useState } from 'react';
import {
  BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid,
} from 'recharts';
import {
  Activity, RefreshCw, AlertTriangle, CheckCircle2, XCircle, Clock,
  MessageSquareText, Search, ShieldAlert, Info, TrendingDown, Copy, Check, Building2, Layers,
} from 'lucide-react';
import { api, ApiError } from '../api';
import { VPlusFullReport } from '../types';
import { DateRangeFilter, DateRangeValue, defaultRange, toIsoRange } from './DateRangeFilter';

const PALETTE = ['#052460', '#54C029', '#FF8800', '#CC1F1F', '#3C4B72', '#2398C9', '#04ADA4', '#2A2F34', '#FF8800', '#8892A1'];

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: '#54C029',
  OTP_PROCESSED: '#052460',
  FAILURE: '#CC1F1F',
  FAILWITHFEEDBACK: '#FF8800',
  UNKNOWN: '#8892A1',
};

const tooltipStyle = { backgroundColor: '#15171A', borderRadius: '8px', border: 'none', color: '#fff', fontSize: '12px' };

const toChartData = (record: Record<string, number> | undefined) =>
  Object.entries(record || {}).sort((a, b) => b[1] - a[1]).map(([name, value]) => ({ name, value }));

const SEVERITY_STYLES: Record<string, string> = {
  critical: 'bg-rose-50 text-rose-800 border-rose-300',
  high: 'bg-orange-50 text-orange-800 border-orange-300',
  medium: 'bg-amber-50 text-amber-800 border-amber-300',
  low: 'bg-slate-50 text-slate-600 border-slate-200',
};

const SEVERITY_ICON: Record<string, React.ReactNode> = {
  critical: <ShieldAlert className="w-4 h-4 text-rose-600" />,
  high: <AlertTriangle className="w-4 h-4 text-orange-600" />,
  medium: <AlertTriangle className="w-4 h-4 text-amber-600" />,
  low: <Info className="w-4 h-4 text-slate-400" />,
};

const fmtTime = (iso?: string | null) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—');
const fmtMs = (ms?: number | null) => (ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(0)}ms`);

export const VPlusMonitoringView: React.FC = () => {
  const [report, setReport] = useState<VPlusFullReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DateRangeValue>(defaultRange());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const handleCopy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(key);
      setTimeout(() => setCopiedId((cur) => (cur === key ? null : cur)), 1500);
    } catch {
      // clipboard API unavailable/denied -- nothing to recover from, leave silently uncopied
    }
  };

  const fetchReport = async (r: DateRangeValue) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const data = await api.get<VPlusFullReport>(`/api/vplus/investigation-summary?${params}`);
      setReport(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load V+ monitoring report');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReport(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  if (loading && !report) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-slate-400">
        <Activity className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
        <p className="text-sm font-medium">Analyzing V+ / StepUp activity…</p>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load V+ monitoring data</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
        <button
          onClick={() => fetchReport(range)}
          className="mt-4 px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded text-xs font-semibold cursor-pointer"
        >
          Retry
        </button>
      </div>
    );
  }

  const { availability: avail, response_times: rt, sms_analysis: sms, investigation_summary: inv, transaction_breakdown: txBreakdown } = report;
  const statusCountsData = toChartData(txBreakdown?.status_counts);
  const issuerCountsData = toChartData(txBreakdown?.issuer_counts);

  const statusColor =
    avail.status === 'healthy' ? 'text-emerald-600' : avail.status === 'down' ? 'text-rose-600' : 'text-slate-400';
  const statusBg =
    avail.status === 'healthy' ? 'bg-emerald-50 border-emerald-200' : avail.status === 'down' ? 'bg-rose-50 border-rose-200' : 'bg-slate-50 border-slate-200';

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white border border-slate-200 p-5 rounded-xl shadow-2xs">
        <div>
          <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <Activity className="w-5 h-5 text-blue-600" />
            V+ / StepUp Monitoring
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Availability, response times, SMS OTP flow, and investigation-focused correlation for the AFS/Netcetera 3DS StepUp log stream.
          </p>
        </div>
        <div className="flex items-end gap-3">
          <DateRangeFilter value={range} onChange={setRange} />
          <button
            onClick={() => fetchReport(range)}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 mb-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      <div className={`rounded-xl border p-5 ${statusBg}`}>
        <div className="flex items-center gap-3">
          {avail.status === 'healthy' ? (
            <CheckCircle2 className="w-8 h-8 text-emerald-600 shrink-0" />
          ) : avail.status === 'down' ? (
            <XCircle className="w-8 h-8 text-rose-600 shrink-0" />
          ) : (
            <Info className="w-8 h-8 text-slate-400 shrink-0" />
          )}
          <div>
            <div className={`text-lg font-extrabold uppercase tracking-wide ${statusColor}`}>
              {avail.status === 'healthy' ? 'V+ is healthy' : avail.status === 'down' ? 'V+ appears DOWN' : 'No data available'}
            </div>
            <div className="text-xs text-slate-600">
              {avail.status === 'no_data'
                ? avail.message
                : avail.status === 'down'
                ? `${avail.unresponded_count} of ${avail.total_inputs_analyzed} request(s) got no vplus_response (expected within ${avail.expected_response_ms}ms)`
                : `${avail.responded_count}/${avail.total_inputs_analyzed} requests responded · expected response ≤ ${avail.expected_response_ms}ms`}
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
          <Search className="w-4 h-4 text-slate-500" />
          Top Findings ({inv.top_findings.length})
        </div>
        {inv.top_findings.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No notable findings in this window.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {inv.top_findings.map((f, i) => (
              <div key={i} className={`flex items-start gap-2 px-5 py-3 text-xs font-medium border-l-4 ${SEVERITY_STYLES[f.severity]}`}>
                {SEVERITY_ICON[f.severity]}
                <span>{f.finding}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Downtime Windows</div>
          <div className="text-2xl font-extrabold text-slate-900">{avail.downtime_windows.length}</div>
          <div className="text-[11px] text-slate-500 mt-1">{avail.total_downtime_minutes ?? 0} min total</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">V+ Response Delay Rate</div>
          <div className="text-2xl font-extrabold text-rose-600">
            {rt.status === 'ok' ? `${rt.delayed_pct}%` : '—'}
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            {rt.status === 'ok' ? `${rt.delayed_count}/${rt.total_pairs_analyzed} pairs delayed` : 'No V+ pairs found'}
          </div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Avg V+ Response Time</div>
          <div className="text-2xl font-extrabold text-slate-900">{rt.status === 'ok' ? fmtMs(rt.stats?.avg_ms) : '—'}</div>
          <div className="text-[11px] text-slate-500 mt-1">expected ≤ {fmtMs(rt.expected_response_ms)}</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">SMS OTP Confirmed</div>
          <div className="text-2xl font-extrabold text-emerald-600">
            {sms.status === 'ok' ? `${sms.outcome_counts?.otp_confirmed || 0}/${sms.total_sms_transactions}` : '—'}
          </div>
          <div className="text-[11px] text-slate-500 mt-1">{sms.status === 'ok' ? 'flows confirmed' : 'No SMS activity'}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            <Clock className="w-4 h-4 text-slate-500" />
            Downtime Windows
          </div>
          {avail.downtime_windows.length === 0 ? (
            <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No gaps detected — continuous activity.</div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
                <tr>
                  <th className="px-4 py-2">Down Since</th>
                  <th className="px-4 py-2">Recovered At</th>
                  <th className="px-4 py-2">Duration</th>
                  <th className="px-4 py-2">Unresponded</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {avail.downtime_windows.map((w, i) => (
                  <tr key={i}>
                    <td className="px-4 py-2 font-mono text-slate-700">{fmtTime(w.down_since)}</td>
                    <td className="px-4 py-2 font-mono text-slate-700">
                      {w.recovered_at ? fmtTime(w.recovered_at) : <span className="text-rose-600 font-bold">Ongoing</span>}
                    </td>
                    <td className="px-4 py-2 font-bold text-rose-600">{w.duration_minutes != null ? `${w.duration_minutes}m` : '—'}</td>
                    <td className="px-4 py-2 font-bold text-slate-700">{w.unresponded_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            <TrendingDown className="w-4 h-4 text-slate-500" />
            Worst V+ Response Delays
          </div>
          {rt.status !== 'ok' || !rt.worst_delays?.length ? (
            <div className="px-5 py-6 text-center text-xs text-slate-400 italic">
              {rt.status === 'ok' ? 'No delayed responses.' : 'No vplus_input → vplus_response pairs found in this window.'}
            </div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
                <tr>
                  <th className="px-4 py-2">Transaction</th>
                  <th className="px-4 py-2">Input Time</th>
                  <th className="px-4 py-2">Delay</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rt.worst_delays.map((p, i) => (
                  <tr key={i}>
                    <td className="px-4 py-2 font-mono text-slate-700">
                      <div className="flex items-center gap-1.5 max-w-[160px]">
                        <span className="truncate">{p.correlation_id}</span>
                        <button
                          onClick={() => handleCopy(p.correlation_id, `wd-${i}`)}
                          title="Copy transaction ID"
                          className="shrink-0 p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 cursor-pointer transition-colors"
                        >
                          {copiedId === `wd-${i}` ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
                        </button>
                      </div>
                    </td>
                    <td className="px-4 py-2 font-mono text-slate-500">{fmtTime(p.input_time)}</td>
                    <td className="px-4 py-2 font-bold text-rose-600">{fmtMs(p.response_time_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
          <MessageSquareText className="w-4 h-4 text-slate-500" />
          SMS OTP Flow
        </div>
        <div className="p-5 space-y-3">
          {sms.status !== 'ok' ? (
            <div className="text-xs text-slate-400 italic">{sms.message || 'No SMS OTP activity in this window.'}</div>
          ) : (
            <>
              <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3.5 py-2.5 text-xs text-blue-900">
                <Info className="w-4 h-4 mt-0.5 shrink-0" />
                {sms.aggregator_note}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {Object.entries(sms.outcome_counts || {}).map(([outcome, count]) => (
                  <div key={outcome} className="bg-slate-50 rounded-lg border border-slate-200 p-3">
                    <div className="text-[10px] font-bold text-slate-500 uppercase">{outcome.replace(/_/g, ' ')}</div>
                    <div className="text-xl font-extrabold text-slate-900">{count}</div>
                  </div>
                ))}
                {sms.queue_delay_stats && (
                  <div className="bg-slate-50 rounded-lg border border-slate-200 p-3">
                    <div className="text-[10px] font-bold text-slate-500 uppercase">Avg Queue Delay</div>
                    <div className="text-xl font-extrabold text-slate-900">{fmtMs(sms.queue_delay_stats.avg_ms)}</div>
                  </div>
                )}
              </div>
              {sms.unresolved && sms.unresolved.length > 0 && (
                <table className="w-full text-left text-xs mt-3">
                  <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
                    <tr>
                      <th className="px-3 py-2">Transaction</th>
                      <th className="px-3 py-2">SMS Sent</th>
                      <th className="px-3 py-2">Outcome</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {sms.unresolved.map((r, i) => (
                      <tr key={i}>
                        <td className="px-3 py-2 font-mono text-slate-700 truncate max-w-[140px]">{r.correlation_id}</td>
                        <td className="px-3 py-2 font-mono text-slate-500">{fmtTime(r.sms_input_time)}</td>
                        <td className="px-3 py-2 font-bold text-amber-600">{r.outcome.replace(/_/g, ' ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      </div>

      {txBreakdown?.status === 'ok' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
            <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
              <Layers className="w-4 h-4 text-blue-600" />
              Netcetera Transactions by Status ({txBreakdown.total_transactions})
            </h3>
            {statusCountsData.length === 0 ? (
              <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No status data in this window.</div>
            ) : (
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={statusCountsData}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={90}
                      paddingAngle={3}
                      label={(props: any) => `${props.name}: ${((props.percent || 0) * 100).toFixed(0)}%`}
                    >
                      {statusCountsData.map((entry, i) => (
                        <Cell key={entry.name} fill={STATUS_COLORS[entry.name] || PALETTE[i % PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={tooltipStyle} />
                    <Legend wrapperStyle={{ fontSize: '12px' }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
            <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
              <Building2 className="w-4 h-4 text-emerald-600" />
              Netcetera Transactions by Issuer
            </h3>
            {issuerCountsData.length === 0 ? (
              <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No issuer data in this window.</div>
            ) : (
              <div style={{ height: Math.max(220, issuerCountsData.length * 34) }} className="w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={issuerCountsData} layout="vertical" margin={{ top: 10, right: 20, left: 10, bottom: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E7E7E7" />
                    <XAxis type="number" stroke="#8892A1" fontSize={11} allowDecimals={false} />
                    <YAxis type="category" dataKey="name" stroke="#8892A1" fontSize={11} width={120} tick={{ width: 110 }} />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Bar dataKey="value" name="Transactions" fill="#052460" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>
      )}

      {(Object.keys(inv.most_affected_merchants).length > 0 || Object.keys(inv.most_affected_issuers).length > 0) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-slate-200 shadow-2xs p-5">
            <div className="text-xs font-bold text-slate-700 mb-3">Most-Affected Merchants (errors)</div>
            {Object.entries(inv.most_affected_merchants).length === 0 ? (
              <div className="text-xs text-slate-400 italic">None.</div>
            ) : (
              <div className="space-y-1.5">
                {Object.entries(inv.most_affected_merchants).map(([m, c]) => (
                  <div key={m} className="flex justify-between text-xs">
                    <span className="text-slate-700 font-medium">{m}</span>
                    <span className="font-bold text-rose-600">{c}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="bg-white rounded-xl border border-slate-200 shadow-2xs p-5">
            <div className="text-xs font-bold text-slate-700 mb-3">Most-Affected Issuers (errors)</div>
            {Object.entries(inv.most_affected_issuers).length === 0 ? (
              <div className="text-xs text-slate-400 italic">None.</div>
            ) : (
              <div className="space-y-1.5">
                {Object.entries(inv.most_affected_issuers).map(([iss, c]) => (
                  <div key={iss} className="flex justify-between text-xs">
                    <span className="text-slate-700 font-medium">{iss}</span>
                    <span className="font-bold text-rose-600">{c}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
