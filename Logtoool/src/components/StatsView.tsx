import React, { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from 'recharts';
import { LogStats } from '../types';
import { BarChart3, Database, Activity, ShieldAlert } from 'lucide-react';
import { api } from '../api';
import { useChartColors } from '../theme/useChartColors';

export const StatsView: React.FC = () => {
  const [stats, setStats] = useState<LogStats | null>(null);
  const chartColors = useChartColors();

  useEffect(() => {
    api
      .get<LogStats>('/api/logs/stats')
      .then(setStats)
      .catch((err) => console.error('Failed to fetch stats:', err));
  }, []);

  if (!stats) {
    return (
      <div className="bg-surface p-8 rounded-2xl border border-surface-border text-center text-text-muted card-brand-glow">
        Loading log analytics & distributions...
      </div>
    );
  }

  const severityPieData = Object.entries(stats.severity_counts)
    .filter(([, count]) => (count as number) > 0)
    .map(([name, value]) => ({ name, value: value as number }));

  const severityColors: Record<string, string> = {
    CRITICAL: chartColors.sevCritical,
    ERROR: chartColors.sevError,
    WARN: chartColors.sevWarn,
    INFO: chartColors.sevInfo,
    DEBUG: chartColors.sevDebug,
    UNKNOWN: chartColors.textSecondary,
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
        <div className="bg-surface p-5 rounded-2xl border border-surface-border shadow-2xs card-brand-glow">
          <div className="flex items-center justify-between text-text-muted mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Total Log Records</span>
            <Database className="w-4 h-4 text-brand" />
          </div>
          <div className="text-3xl font-extrabold text-text">{totalEvents.toLocaleString()}</div>
        </div>

        <div className="bg-surface p-5 rounded-2xl border border-surface-border shadow-2xs card-brand-glow">
          <div className="flex items-center justify-between text-text-muted mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Critical & Error Rate</span>
            <ShieldAlert className="w-4 h-4 text-error" />
          </div>
          <div className="text-3xl font-extrabold text-error">{errorPercentage}%</div>
          <div className="text-xs font-semibold text-error mt-1">{totalErrors} total error events</div>
        </div>

        <div className="bg-surface p-5 rounded-2xl border border-surface-border shadow-2xs card-brand-glow">
          <div className="flex items-center justify-between text-text-muted mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Source Systems</span>
            <Activity className="w-4 h-4 text-success" />
          </div>
          <div className="text-3xl font-extrabold text-text">{Object.keys(stats.source_distribution).length}</div>
        </div>

        <div className="bg-surface p-5 rounded-2xl border border-surface-border shadow-2xs card-brand-glow">
          <div className="flex items-center justify-between text-text-muted mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Batches Ingested</span>
            <BarChart3 className="w-4 h-4 text-warning" />
          </div>
          <div className="text-3xl font-extrabold text-text">{stats.batches.length}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-surface p-6 rounded-2xl border border-surface-border shadow-2xs card-brand-glow">
          <h3 className="text-sm font-bold text-text mb-4 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-brand"></span>
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
                    <Cell key={entry.name} fill={severityColors[entry.name] || '#949799'} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: '12px' }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="bg-surface p-6 rounded-2xl border border-surface-border shadow-2xs card-brand-glow">
          <h3 className="text-sm font-bold text-text mb-4 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full text-success"></span>
            Log Volume by Source System
          </h3>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
                <BarChart data={sourceBarData} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartColors.surfaceBorder} />
                  <XAxis dataKey="source" stroke={chartColors.textSecondary} fontSize={11} />
                  <YAxis stroke={chartColors.textSecondary} fontSize={11} />
                  <Tooltip contentStyle={{ backgroundColor: chartColors.surface, borderRadius: '8px', border: `1px solid ${chartColors.surfaceBorder}`, color: chartColors.textSecondary, fontSize: '12px' }} />
                  <Bar dataKey="count" fill={chartColors.brand} radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="bg-surface rounded-2xl border border-surface-border shadow-2xs overflow-hidden card-brand-glow">
        <div className="px-5 py-3 border-b border-surface-border bg-surface-alt font-bold text-xs text-text">
          Recent Batches
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-alt text-text-secondary font-bold uppercase tracking-wider text-[10px] border-b border-surface-border">
              <tr>
                <th className="px-4 py-2.5">File</th>
                <th className="px-4 py-2.5">Profile</th>
                <th className="px-4 py-2.5">Events</th>
                <th className="px-4 py-2.5">Match Ratio</th>
                <th className="px-4 py-2.5">Uploaded</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-border text-text font-medium">
              {stats.batches.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-text-muted italic">
                    No batches ingested yet.
                  </td>
                </tr>
              ) : (
                stats.batches.slice(0, 10).map((b) => (
                  <tr key={b.batch_id} className="hover:bg-surface-alt table-row-brand">
                    <td className="px-4 py-3 font-bold text-text">{b.file_name}</td>
                    <td className="px-4 py-3 text-text-secondary">{b.matched_profile || '—'}</td>
                    <td className="px-4 py-3">{b.total_events}</td>
                    <td className="px-4 py-3 font-bold text-success">{(b.match_ratio * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3 font-mono text-[11px] text-text-muted">{b.uploaded_at?.slice(0, 19).replace('T', ' ')}</td>
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
