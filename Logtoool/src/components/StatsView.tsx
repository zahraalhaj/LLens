import React, { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from 'recharts';
import { LogStats } from '../types';
import { BarChart3, Database, Activity, ShieldAlert } from 'lucide-react';
import { api } from '../api';

export const StatsView: React.FC = () => {
  const [stats, setStats] = useState<LogStats | null>(null);

  useEffect(() => {
    api
      .get<LogStats>('/api/logs/stats')
      .then(setStats)
      .catch((err) => console.error('Failed to fetch stats:', err));
  }, []);

  if (!stats) {
    return (
      <div className="bg-white p-8 rounded-xl border border-slate-200 text-center text-slate-500">
        Loading log analytics & distributions...
      </div>
    );
  }

  const severityPieData = Object.entries(stats.severity_counts)
    .filter(([, count]) => (count as number) > 0)
    .map(([name, value]) => ({ name, value: value as number }));

  const severityColors: Record<string, string> = {
    CRITICAL: '#dc2626',
    ERROR: '#ef4444',
    WARN: '#f59e0b',
    INFO: '#2563eb',
    DEBUG: '#64748b',
    UNKNOWN: '#94a3b8',
  };

  const sourceBarData = Object.entries(stats.source_distribution)
    .map(([source, count]) => ({ source, count: count as number }))
    .sort((a, b) => b.count - a.count);

  const totalEvents = (Object.values(stats.severity_counts) as number[]).reduce((sum, c) => sum + (c || 0), 0);
  const totalErrors = (stats.severity_counts.CRITICAL || 0) + (stats.severity_counts.ERROR || 0);
  const errorPercentage = totalEvents > 0 ? ((totalErrors / totalEvents) * 100).toFixed(1) : '0.0';

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Total Log Records</span>
            <Database className="w-4 h-4 text-blue-600" />
          </div>
          <div className="text-3xl font-extrabold text-slate-900">{totalEvents.toLocaleString()}</div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Critical & Error Rate</span>
            <ShieldAlert className="w-4 h-4 text-rose-600" />
          </div>
          <div className="text-3xl font-extrabold text-rose-600">{errorPercentage}%</div>
          <div className="text-xs font-semibold text-rose-700 mt-1">{totalErrors} total error events</div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Source Systems</span>
            <Activity className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-3xl font-extrabold text-slate-900">{Object.keys(stats.source_distribution).length}</div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Batches Ingested</span>
            <BarChart3 className="w-4 h-4 text-amber-500" />
          </div>
          <div className="text-3xl font-extrabold text-slate-900">{stats.batches.length}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-blue-600"></span>
            Severity Level Breakdown
          </h3>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={severityPieData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={90}
                  paddingAngle={3}
                  label={(props: any) => `${props.name}: ${((props.percent || 0) * 100).toFixed(0)}%`}
                >
                  {severityPieData.map((entry) => (
                    <Cell key={entry.name} fill={severityColors[entry.name] || '#64748b'} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: '12px' }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-600"></span>
            Log Volume by Source System
          </h3>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={sourceBarData} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="source" stroke="#64748b" fontSize={11} />
                <YAxis stroke="#64748b" fontSize={11} />
                <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderRadius: '8px', border: 'none', color: '#fff', fontSize: '12px' }} />
                <Bar dataKey="count" fill="#2563eb" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-800">
          Recent Batches
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-100 text-slate-600 font-bold uppercase tracking-wider text-[10px] border-b border-slate-200">
              <tr>
                <th className="px-4 py-2.5">File</th>
                <th className="px-4 py-2.5">Profile</th>
                <th className="px-4 py-2.5">Events</th>
                <th className="px-4 py-2.5">Match Ratio</th>
                <th className="px-4 py-2.5">Uploaded</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-slate-800 font-medium">
              {stats.batches.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-slate-500 italic">
                    No batches ingested yet.
                  </td>
                </tr>
              ) : (
                stats.batches.slice(0, 10).map((b) => (
                  <tr key={b.batch_id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-bold text-slate-900">{b.file_name}</td>
                    <td className="px-4 py-3 text-slate-600">{b.matched_profile || '—'}</td>
                    <td className="px-4 py-3">{b.total_events}</td>
                    <td className="px-4 py-3 font-bold text-emerald-600">{(b.match_ratio * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3 font-mono text-[11px] text-slate-500">{b.uploaded_at?.slice(0, 19).replace('T', ' ')}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
