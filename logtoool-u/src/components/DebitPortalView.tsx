import React, { useEffect, useState } from 'react';
import {
  BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid,
} from 'recharts';
import {
  CreditCard, RefreshCw, Info, Layers, Store, Building2, AlertOctagon, ShieldCheck, ShieldAlert, Coins,
} from 'lucide-react';
import { api, ApiError } from '../api';
import { DebitPortalSummary } from '../types';
import { DateRangeFilter, DateRangeValue, defaultRange, toIsoRange } from './DateRangeFilter';
import { MerchantFilter } from './MerchantFilter';

const PALETTE = ['#052460', '#036FD0', '#00AEEF', '#04ADA4', '#8892A1', '#3C4B72', '#54C029', '#FF8800', '#2A2F34', '#64748b'];

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: '#54C029',
  OTP_PROCESSED: '#036FD0',
  FAILURE: '#FF2F2F',
  FAILWITHFEEDBACK: '#FF8800',
  CHECK: '#FF8800',
  OK: '#54C029',
  UNKNOWN: '#8892A1',
};

const tooltipStyle = { backgroundColor: '#15171A', borderRadius: '8px', border: 'none', color: '#fff', fontSize: '12px' };

const fmtTime = (iso?: string | null) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—');

const toChartData = (record: Record<string, number> | undefined, limit?: number) => {
  const entries = Object.entries(record || {}).sort((a, b) => b[1] - a[1]);
  return (limit ? entries.slice(0, limit) : entries).map(([name, value]) => ({ name, value }));
};

export const DebitPortalView: React.FC = () => {
  const [report, setReport] = useState<DebitPortalSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DateRangeValue>(defaultRange());
  const [merchant, setMerchant] = useState('');
  const [availableMerchants, setAvailableMerchants] = useState<string[]>([]);

  const fetchReport = async (r: DateRangeValue, m: string) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      if (m) params.set('merchant', m);
      const data = await api.get<DebitPortalSummary>(`/api/debit-portal/summary?${params}`);
      setReport(data);
      setAvailableMerchants(data.available_merchants || []);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load Debit Portal report');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReport(range, merchant);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, merchant]);

  if (loading && !report) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-slate-400">
        <CreditCard className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
        <p className="text-sm font-medium">Analyzing Debit Portal activity…</p>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load Debit Portal data</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
        <button
          onClick={() => fetchReport(range, merchant)}
          className="mt-4 px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded text-xs font-semibold cursor-pointer"
        >
          Retry
        </button>
      </div>
    );
  }

  const header = (
    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white border border-slate-200 p-5 rounded-xl shadow-2xs">
      <div>
        <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <CreditCard className="w-5 h-5 text-blue-600" />
          Debit Portal Analytics
        </h1>
        <p className="text-xs text-slate-500 mt-1">
          Issuer, status, and merchant distribution, plus failed/error events for the Debit Portal (Transactions + Errors) log stream.
        </p>
      </div>
      <div className="flex items-end gap-3">
        <DateRangeFilter value={range} onChange={setRange} />
        <MerchantFilter value={merchant} onChange={setMerchant} options={availableMerchants} />
        <button
          onClick={() => fetchReport(range, merchant)}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 mb-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>
    </div>
  );

  if (report.status !== 'ok') {
    return (
      <div className="space-y-6">
        {header}
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-8 text-center">
          <Info className="w-8 h-8 text-slate-400 mx-auto mb-2" />
          <p className="text-sm font-medium text-slate-500">{report.message || 'No Debit Portal activity found in this window.'}</p>
        </div>
      </div>
    );
  }

  const statusData = toChartData(report.by_status);
  const issuerData = toChartData(report.by_issuer);
  const currencyData = toChartData(report.by_currency);
  const merchantData = toChartData(report.top_merchants);
  const failedReasonData = toChartData(report.failed_events?.reason_counts);
  const failedItems = report.failed_events?.items || [];
  const failedCount = report.failed_events?.count || 0;

  return (
    <div className="space-y-6">
      {header}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Debit Records</span>
            <CreditCard className="w-4 h-4 text-blue-600" />
          </div>
          <div className="text-2xl font-extrabold text-slate-900">{report.total_records}</div>
          <div className="text-[11px] text-slate-500 mt-1">{report.total_events_analyzed} events analyzed</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">OTP Processed</span>
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-2xl font-extrabold text-emerald-600">{report.otp_processed_count}</div>
          <div className="text-[11px] text-slate-500 mt-1">of {report.total_records} records</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Needs Check</span>
            <ShieldAlert className="w-4 h-4 text-amber-600" />
          </div>
          <div className="text-2xl font-extrabold text-amber-600">{report.checks_needed_count}</div>
          <div className="text-[11px] text-slate-500 mt-1">records with integrity warnings</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Failed / Error Events</span>
            <AlertOctagon className="w-4 h-4 text-rose-600" />
          </div>
          <div className="text-2xl font-extrabold text-rose-600">{failedCount}</div>
          <div className="text-[11px] text-slate-500 mt-1">not discarded — surfaced below</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
            <Layers className="w-4 h-4 text-blue-600" />
            By Status
          </h3>
          {statusData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No status data in this window.</div>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={statusData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={90}
                    paddingAngle={3}
                    label={(props: any) => `${props.name}: ${((props.percent || 0) * 100).toFixed(0)}%`}
                  >
                    {statusData.map((entry, i) => (
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
            By Issuer
          </h3>
          {issuerData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No issuer data in this window.</div>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={issuerData} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E7E7E7" />
                  <XAxis dataKey="name" stroke="#8892A1" fontSize={10} angle={-20} textAnchor="end" interval={0} height={50} />
                  <YAxis stroke="#8892A1" fontSize={11} allowDecimals={false} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="value" name="Records" fill="#052460" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
          <Coins className="w-4 h-4 text-emerald-600" />
          By Currency
        </h3>
        {currencyData.length === 0 ? (
          <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No currency data in this window.</div>
        ) : (
          <div className="h-64 w-full max-w-md mx-auto">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={currencyData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={90}
                  paddingAngle={3}
                  label={(props: any) => `${props.name}: ${((props.percent || 0) * 100).toFixed(0)}%`}
                >
                  {currencyData.map((entry, i) => (
                    <Cell key={entry.name} fill={PALETTE[i % PALETTE.length]} />
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
          <Store className="w-4 h-4 text-amber-600" />
          Top Merchants
        </h3>
        {merchantData.length === 0 ? (
          <div className="h-64 flex items-center justify-center text-xs text-slate-400 italic">No merchant data in this window.</div>
        ) : (
          <div style={{ height: Math.max(220, merchantData.length * 34) }} className="w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={merchantData} layout="vertical" margin={{ top: 10, right: 20, left: 10, bottom: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E7E7E7" />
                <XAxis type="number" stroke="#8892A1" fontSize={11} allowDecimals={false} />
                <YAxis type="category" dataKey="name" stroke="#8892A1" fontSize={11} width={160} tick={{ width: 150 }} />
                <Tooltip contentStyle={tooltipStyle} />
                <Bar dataKey="value" name="Records" fill="#FF8800" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
            <AlertOctagon className="w-4 h-4 text-rose-600" />
            Failed / Error Events by Reason
          </h3>
          {failedReasonData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-xs text-slate-400 italic">No failed or error events — clean run.</div>
          ) : (
            <div style={{ height: Math.max(180, failedReasonData.length * 34) }} className="w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={failedReasonData} layout="vertical" margin={{ top: 10, right: 20, left: 10, bottom: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E7E7E7" />
                  <XAxis type="number" stroke="#8892A1" fontSize={11} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" stroke="#8892A1" fontSize={10} width={180} tick={{ width: 170 }} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="value" name="Events" fill="#FF2F2F" radius={[0, 6, 6, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            <AlertOctagon className="w-4 h-4 text-slate-500" />
            Failed / Error Events ({failedItems.length}{failedCount > failedItems.length ? ` of ${failedCount}` : ''})
          </div>
          {failedItems.length === 0 ? (
            <div className="px-5 py-6 text-center text-xs text-slate-400 italic">Nothing to show — every event parsed cleanly.</div>
          ) : (
            <div className="overflow-y-auto max-h-72">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200 sticky top-0">
                  <tr>
                    <th className="px-4 py-2">Time</th>
                    <th className="px-4 py-2">Transaction</th>
                    <th className="px-4 py-2">Reason</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {failedItems.map((f, i) => (
                    <tr key={i}>
                      <td className="px-4 py-2 font-mono text-slate-500 whitespace-nowrap">{fmtTime(f.timestamp)}</td>
                      <td className="px-4 py-2 font-mono text-slate-700 truncate max-w-[140px]">{f.correlation_id || '—'}</td>
                      <td className="px-4 py-2 text-rose-700 font-medium">{f.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
