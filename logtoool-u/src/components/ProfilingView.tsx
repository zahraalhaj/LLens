import React, { useState, useEffect } from 'react';
import { ActivitySquare, RefreshCw, AlertTriangle, Info } from 'lucide-react';
import { AnomalyReport } from '../types';
import { api, ApiError } from '../api';

export const ProfilingView: React.FC = () => {
  const [report, setReport] = useState<AnomalyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recomputing, setRecomputing] = useState(false);

  const fetchReport = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<AnomalyReport>('/api/logs/profiling');
      setReport(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load anomaly report');
    } finally {
      setLoading(false);
    }
  };

  const handleRecompute = async () => {
    setRecomputing(true);
    try {
      const data = await api.post<AnomalyReport>('/api/logs/ml/train');
      setReport(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to recompute');
    } finally {
      setRecomputing(false);
    }
  };

  useEffect(() => {
    fetchReport();
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-slate-400">
        <ActivitySquare className="w-10 h-10 animate-spin text-blue-500 mb-3" />
        <p className="text-sm font-medium">Computing anomaly statistics…</p>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
        <p className="font-semibold text-sm">Failed to load the anomaly report</p>
        <p className="text-xs text-rose-600 mt-1">{error}</p>
        <button
          onClick={fetchReport}
          className="mt-4 px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded text-xs font-semibold cursor-pointer"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white border border-slate-200 p-5 rounded-xl shadow-2xs">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Anomaly Insights</h1>
          <p className="text-xs text-slate-500 mt-1">Simple statistical outlier detection over your ingested events.</p>
        </div>
        <button
          onClick={handleRecompute}
          disabled={recomputing}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer self-start md:self-auto"
        >
          <RefreshCw className={`w-4 h-4 ${recomputing ? 'animate-spin' : ''}`} />
          Recompute now
        </button>
      </div>

      <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-xs text-blue-900">
        <Info className="w-4 h-4 mt-0.5 shrink-0" />
        <div>
          <strong>How this works:</strong> {report.description} Values more than 2 standard deviations
          above the mean are flagged. This method: <code className="font-mono bg-blue-100 px-1 py-0.5 rounded">{report.method}</code>.
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Total Events</div>
          <div className="text-2xl font-extrabold text-slate-900">{report.total_events.toLocaleString()}</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Error/Critical Ratio</div>
          <div className="text-2xl font-extrabold text-rose-600">{(report.error_ratio * 100).toFixed(1)}%</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-500" />
            Components with unusually high error counts
          </div>
          {report.flagged_components.length === 0 ? (
            <div className="px-5 py-6 text-center text-xs text-slate-400 italic">Nothing flagged.</div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
                <tr>
                  <th className="px-4 py-2">Component</th>
                  <th className="px-4 py-2">Error count</th>
                  <th className="px-4 py-2">Z-score</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {report.flagged_components.map((f) => (
                  <tr key={f.name}>
                    <td className="px-4 py-2 font-mono text-slate-700">{f.name}</td>
                    <td className="px-4 py-2 font-bold text-rose-600">{f.count}</td>
                    <td className="px-4 py-2 text-slate-500">{f.z_score.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-500" />
            Hours with unusual event volume
          </div>
          {report.flagged_hours.length === 0 ? (
            <div className="px-5 py-6 text-center text-xs text-slate-400 italic">Nothing flagged.</div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 font-bold uppercase text-[10px] border-b border-slate-200">
                <tr>
                  <th className="px-4 py-2">Hour</th>
                  <th className="px-4 py-2">Event count</th>
                  <th className="px-4 py-2">Z-score</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {report.flagged_hours.map((f) => (
                  <tr key={f.name}>
                    <td className="px-4 py-2 font-mono text-slate-700">{f.name}</td>
                    <td className="px-4 py-2 font-bold text-rose-600">{f.count}</td>
                    <td className="px-4 py-2 text-slate-500">{f.z_score.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};
