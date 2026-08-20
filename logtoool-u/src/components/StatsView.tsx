import React, { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, LabelList } from 'recharts';
import { LogStats } from '../types';
import { BarChart3, Database, Activity, ShieldAlert, LayoutDashboard, Clock } from 'lucide-react';
import { api } from '../api';
import { BRAND, SEVERITY_COLORS } from '../theme';
import { TimelineView } from './TimelineView';

type StatsTab = 'overview' | 'timeline';

export const StatsView: React.FC = () => {
  const [tab, setTab] = useState<StatsTab>('overview');

  return (
    <div className="space-y-6">
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-1.5 flex flex-wrap gap-1">
        <button
          onClick={() => setTab('overview')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-bold transition-colors cursor-pointer ${
            tab === 'overview' ? 'bg-blue-600 text-white shadow-2xs' : 'text-slate-600 hover:bg-slate-100'
          }`}
        >
          <LayoutDashboard className={`w-4 h-4 ${tab === 'overview' ? 'text-white' : 'text-slate-400'}`} />
          Overview
        </button>
        <button
          onClick={() => setTab('timeline')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-bold transition-colors cursor-pointer ${
            tab === 'timeline' ? 'bg-blue-600 text-white shadow-2xs' : 'text-slate-600 hover:bg-slate-100'
          }`}
        >
          <Clock className={`w-4 h-4 ${tab === 'timeline' ? 'text-white' : 'text-slate-400'}`} />
          Timeline
        </button>
      </div>

      {tab === 'overview' ? <StatsOverview /> : <TimelineView />}
    </div>
  );
};

const StatsOverview: React.FC = () => {
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
    .map(([name, value]) => ({ name, value: value as number }))
    .sort((a, b) => b.value - a.value);

  const sourceBarData = Object.entries(stats.source_distribution)
    .map(([source, count]) => ({ source, count: count as number }))
    .sort((a, b) => b.count - a.count);

  const totalEvents = (Object.values(stats.severity_counts) as number[]).reduce((sum, c) => sum + (c || 0), 0);
  const totalErrors = (stats.severity_counts.CRITICAL || 0) + (stats.severity_counts.ERROR || 0);
  const errorPercentage = totalEvents > 0 ? ((totalErrors / totalEvents) * 100).toFixed(1) : '0.0';

  const barChartHeight = Math.max(240, sourceBarData.length * 42);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total Log Records"
          value={totalEvents.toLocaleString()}
          icon={<Database className="w-4 h-4 text-blue-600" />}
          iconBg="bg-blue-50"
        />
        <StatCard
          label="Critical & Error Rate"
          value={`${errorPercentage}%`}
          valueClassName="text-rose-600"
          subtext={`${totalErrors.toLocaleString()} total error events`}
          subtextClassName="text-rose-700"
          icon={<ShieldAlert className="w-4 h-4 text-rose-600" />}
          iconBg="bg-rose-50"
        />
        <StatCard
          label="Source Systems"
          value={Object.keys(stats.source_distribution).length}
          icon={<Activity className="w-4 h-4 text-emerald-600" />}
          iconBg="bg-emerald-50"
        />
        <StatCard
          label="Batches Ingested"
          value={stats.batches.length}
          icon={<BarChart3 className="w-4 h-4 text-amber-500" />}
          iconBg="bg-amber-50"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">Severity Level Breakdown</h3>
          <p className="text-xs text-slate-500 mb-4">Share of ingested events by severity</p>

          <div className="flex flex-col sm:flex-row items-center gap-6">
            <div className="relative h-56 w-56 shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={severityPieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={68}
                    outerRadius={95}
                    paddingAngle={3}
                    stroke="none"
                  >
                    {severityPieData.map((entry) => (
                      <Cell key={entry.name} fill={SEVERITY_COLORS[entry.name] || BRAND.slate400} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(value: number, name: string) => [
                      `${value.toLocaleString()} (${totalEvents > 0 ? ((value / totalEvents) * 100).toFixed(1) : '0.0'}%)`,
                      name,
                    ]}
                    contentStyle={{ backgroundColor: BRAND.slate900, borderRadius: 8, border: 'none', color: '#fff', fontSize: 12 }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <div className="text-2xl font-extrabold text-slate-900">{totalEvents.toLocaleString()}</div>
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Total Events</div>
              </div>
            </div>

            <div className="flex-1 w-full min-w-0 space-y-2">
              {severityPieData.map((entry) => {
                const pct = totalEvents > 0 ? ((entry.value / totalEvents) * 100).toFixed(1) : '0.0';
                return (
                  <div key={entry.name} className="flex items-center justify-between gap-3 text-xs">
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className="w-2.5 h-2.5 rounded-sm shrink-0"
                        style={{ backgroundColor: SEVERITY_COLORS[entry.name] || BRAND.slate400 }}
                      />
                      <span className="font-semibold text-slate-700 truncate">{entry.name}</span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 font-mono text-slate-500">
                      <span className="text-slate-900 font-bold">{entry.value.toLocaleString()}</span>
                      <span className="w-12 text-right text-slate-400">{pct}%</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
          <h3 className="text-sm font-bold text-slate-900 mb-1">Log Volume by Source System</h3>
          <p className="text-xs text-slate-500 mb-4">Events ingested per source, highest first</p>
          <div style={{ height: barChartHeight }} className="w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={sourceBarData} layout="vertical" margin={{ top: 4, right: 36, left: 4, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={BRAND.slate100} horizontal={false} />
                <XAxis type="number" stroke={BRAND.slate500} fontSize={11} tickLine={false} axisLine={false} />
                <YAxis
                  type="category"
                  dataKey="source"
                  stroke={BRAND.slate700}
                  fontSize={12}
                  fontWeight={600}
                  width={120}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  cursor={{ fill: BRAND.slate100 }}
                  formatter={(value: number) => [value.toLocaleString(), 'Events']}
                  contentStyle={{ backgroundColor: BRAND.slate900, borderRadius: 8, border: 'none', color: '#fff', fontSize: 12 }}
                />
                <Bar dataKey="count" fill={BRAND.blue600} radius={[0, 6, 6, 0]} barSize={18}>
                  <LabelList
                    dataKey="count"
                    position="right"
                    fontSize={11}
                    fontWeight={700}
                    fill={BRAND.slate700}
                    formatter={(value: number) => value.toLocaleString()}
                  />
                </Bar>
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
            <thead className="bg-slate-100 text-slate-600 font-bold uppercase tracking-wider text-[11px] border-b border-slate-200">
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

interface StatCardProps {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  iconBg: string;
  valueClassName?: string;
  subtext?: string;
  subtextClassName?: string;
}

const StatCard: React.FC<StatCardProps> = ({ label, value, icon, iconBg, valueClassName, subtext, subtextClassName }) => (
  <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
    <div className="flex items-center justify-between mb-3">
      <span className="text-xs font-semibold text-slate-500">{label}</span>
      <span className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconBg}`}>{icon}</span>
    </div>
    <div className={`text-3xl font-extrabold text-slate-900 ${valueClassName || ''}`}>{value}</div>
    {subtext && <div className={`text-xs font-semibold mt-1 ${subtextClassName || 'text-slate-500'}`}>{subtext}</div>}
  </div>
);
