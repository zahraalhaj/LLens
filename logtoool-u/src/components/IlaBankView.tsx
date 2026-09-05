import React, { useEffect, useMemo, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  AlertOctagon, AlertTriangle, Building2, FileWarning, Gauge, Layers, RefreshCw, Search, Timer, X,
} from 'lucide-react';
import { api, ApiError } from '../api';
import { IlaReport, IlaSummary, IlaTracker } from '../types';
import { DateRangeFilter, DateRangeValue, defaultRange, toIsoRange } from './DateRangeFilter';
import { maskText } from '../utils/maskSensitive';
import { MaskedBadge } from './MaskedBadge';

/* --------------------------------------------------------------------------
 * Colour
 *
 * Two separate palettes, never mixed:
 *
 * SERIES  -- categorical identity (event types, exceptions, frames). Taken
 *            from the validated categorical palette; the app's older chart
 *            palette fails CVD separation (#8892A1 vs #04ADA4 sit at deltaE 1.5
 *            for deuteranopes and 11.2 even for normal vision), so it is not
 *            reused here. Hues are assigned in fixed order and never cycled.
 * STATUS   -- reserved for severity only (error / warn / info). Status colour
 *            is never spent on "just another series", so red always means
 *            error on this page and nothing else.
 * ------------------------------------------------------------------------ */
const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];

const STATUS = {
  error: '#e34948',
  warn: '#eda100',
  info: '#2a78d6',
  ok: '#1baf7a',
  neutral: '#94a3b8',
};

const PARSE_STATUS_COLOR: Record<string, string> = {
  complete: STATUS.ok,
  partial: STATUS.warn,
  unrecognized: STATUS.neutral,
  unknown: STATUS.neutral,
};

/**
 * Sequential ramp for the duration histogram: one hue, light to dark,
 * monotonically increasing in darkness. Duration buckets are a magnitude
 * axis, not a set of identities, so painting them with categorical hues
 * would imply seven unrelated things instead of one scale.
 */
const DURATION_RAMP = ['#cfe0f6', '#a8c8ee', '#7fb0e5', '#589add', '#2a78d6', '#215fab', '#194780'];

const AXIS_TICK = { fill: '#64748b', fontSize: 11 };
const GRID = '#e2e8f0';

const tooltipStyle = {
  backgroundColor: '#0f172a',
  borderRadius: '8px',
  border: 'none',
  color: '#fff',
  fontSize: '12px',
  padding: '8px 10px',
};
const tooltipLabelStyle = { color: '#cbd5e1', fontWeight: 700, marginBottom: 2 };

/* ---------------------------------------------------------------- helpers */

const fmtTime = (iso?: string | null) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—');

/** Hour bucket -> "09:00", with the date kept for the tooltip. */
const fmtHour = (bucket: string) => bucket.slice(11, 16);
const fmtDay = (bucket: string) => bucket.slice(0, 10);

const fmtMs = (ms: number | null | undefined) => {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(2)} s`;
  return `${(ms / 60000).toFixed(1)} min`;
};

const pct = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;

/**
 * Long identifiers (a .NET exception type, a fully-qualified stack frame)
 * are truncated from the LEFT, keeping the tail: `System.Net.Http.Foo` and
 * `System.Net.Http.Bar` differ only at the end, so a right-truncated label
 * turns two distinct rows into two identical ones.
 */
const shortenTail = (value: string, max = 34) =>
  value.length <= max ? value : `…${value.slice(value.length - max + 1)}`;

const toBarData = (record: Record<string, number> | undefined, limit?: number) => {
  const entries = Object.entries(record || {}).sort((a, b) => b[1] - a[1]);
  return (limit ? entries.slice(0, limit) : entries).map(([name, value]) => ({ name, value }));
};

/* ------------------------------------------------------------- primitives */

const Panel: React.FC<{
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
}> = ({ title, subtitle, icon, right, children }) => (
  <section className="bg-white border border-slate-200 rounded-xl shadow-2xs flex flex-col overflow-hidden">
    <header className="px-5 pt-4 pb-3 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
          {icon}
          {title}
        </h3>
        {subtitle && <p className="text-[11px] text-slate-500 mt-0.5 leading-snug">{subtitle}</p>}
      </div>
      {right}
    </header>
    <div className="px-2 pb-4 flex-1">{children}</div>
  </section>
);

const EmptyPlot: React.FC<{ label: string; height: number }> = ({ label, height }) => (
  <div className="flex items-center justify-center text-[11px] text-slate-400 italic" style={{ height }}>
    {label}
  </div>
);

const StatTile: React.FC<{
  label: string;
  value: string;
  hint?: string;
  icon: React.ReactNode;
  tone?: 'default' | 'error' | 'warn' | 'ok';
}> = ({ label, value, hint, icon, tone = 'default' }) => {
  const valueColor =
    tone === 'error' ? 'text-rose-600' : tone === 'warn' ? 'text-amber-600' : tone === 'ok' ? 'text-emerald-600' : 'text-slate-900';
  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-2xs px-4 py-3.5 flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div className={`text-2xl font-bold tabular-nums leading-tight ${valueColor}`}>{value}</div>
      <div className="text-[11px] text-slate-500 leading-snug min-h-[1rem]">{hint || ''}</div>
    </div>
  );
};

/* ------------------------------------------------------------------- view */

/**
 * The report body. Split out so the `status === 'ok'` narrowing happens
 * once, at the boundary: everything in here receives an IlaReport and can
 * read its fields without a guard, and no hook is called conditionally.
 */
export const IlaReportBody: React.FC<{ report: IlaReport }> = ({ report }) => {
  const eventTypeData = useMemo(() => toBarData(report.event_type_counts), [report]);
  const exceptionData = useMemo(() => toBarData(report.top_exceptions), [report]);
  const frameData = useMemo(() => toBarData(report.top_stack_frames), [report]);
  const httpData = useMemo(() => toBarData(report.http_status_counts), [report]);

  const parseTotal = useMemo(
    () => Object.values(report.parse_status_counts || {}).reduce((a, b) => a + b, 0),
    [report],
  );

  const hasSeverity = (report.severity_timeline?.length ?? 0) > 0;
  const durationBuckets = report.duration_stats.buckets ?? [];
  const hasDurations = (report.duration_stats.count ?? 0) > 0;

  return (
      <>
        {/* ---- Headline figures ---- */}
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <StatTile
            label="Entries"
            value={report.total_events_analyzed.toLocaleString()}
            hint={`${report.multiline_entries.toLocaleString()} with attached detail`}
            icon={<Layers className="w-3 h-3" />}
          />
          <StatTile
            label="Transactions"
            value={report.total_trackers.toLocaleString()}
            hint={`${report.untracked_events.toLocaleString()} entries with no tracker`}
            icon={<Building2 className="w-3 h-3" />}
          />
          <StatTile
            label="Error rate"
            value={pct(report.error_rate)}
            hint={`${report.error_count.toLocaleString()} errors · ${report.warning_count.toLocaleString()} warnings`}
            icon={<AlertOctagon className="w-3 h-3" />}
            tone={report.error_rate > 0 ? 'error' : 'ok'}
          />
          <StatTile
            label="Failing transactions"
            value={report.trackers_with_errors.toLocaleString()}
            hint={
              report.total_trackers
                ? `${pct(report.trackers_with_errors / report.total_trackers)} of transactions`
                : '—'
            }
            icon={<AlertTriangle className="w-3 h-3" />}
            tone={report.trackers_with_errors > 0 ? 'warn' : 'ok'}
          />
          <StatTile
            label="p95 duration"
            value={fmtMs(report.duration_stats.p95_ms)}
            hint={
              hasDurations
                ? `median ${fmtMs(report.duration_stats.p50_ms)} · max ${fmtMs(report.duration_stats.max_ms)}`
                : 'no timings logged'
            }
            icon={<Timer className="w-3 h-3" />}
          />
        </div>

        {/* ---- Severity over time: full width, its own row ---- */}
        <Panel
          title="Severity over time"
          subtitle={`Entries per ${report.severity_granularity}, stacked by log level. Hover a bar for the exact counts.`}
          icon={<Gauge className="w-4 h-4 text-slate-400" />}
        >
          {hasSeverity ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={report.severity_timeline} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="bucket"
                  tickFormatter={(b: string) => (report.severity_granularity === 'day' ? fmtDay(b).slice(5) : fmtHour(b))}
                  tick={AXIS_TICK}
                  tickLine={false}
                  axisLine={{ stroke: GRID }}
                  minTickGap={18}
                />
                <YAxis
                  tick={AXIS_TICK}
                  tickLine={false}
                  axisLine={false}
                  allowDecimals={false}
                  width={40}
                />
                <Tooltip
                  cursor={{ fill: 'rgba(148,163,184,0.12)' }}
                  contentStyle={tooltipStyle}
                  labelStyle={tooltipLabelStyle}
                  labelFormatter={(b: string) =>
                    report.severity_granularity === 'day' ? fmtDay(b) : `${fmtDay(b)} · ${fmtHour(b)}`
                  }
                />
                <Legend
                  verticalAlign="top"
                  align="right"
                  height={28}
                  iconType="circle"
                  iconSize={8}
                  wrapperStyle={{ fontSize: 11, color: '#475569', paddingBottom: 4 }}
                />
                {/* 2px surface gap between stacked segments keeps the
                    boundaries legible without relying on colour alone. */}
                <Bar dataKey="info" name="Info" stackId="s" fill={STATUS.info} stroke="#fff" strokeWidth={1} />
                <Bar dataKey="warn" name="Warning" stackId="s" fill={STATUS.warn} stroke="#fff" strokeWidth={1} />
                <Bar dataKey="error" name="Error" stackId="s" fill={STATUS.error} stroke="#fff" strokeWidth={1} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyPlot label="No timestamped entries in this window" height={260} />
          )}
        </Panel>

        {/* ---- Two-column charts. Horizontal bars so long .NET type names
                get a full-width label track instead of a rotated axis. ---- */}
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel
            title="What the log is doing"
            subtitle="Entries by event type, as classified by the ILA parser."
            icon={<Layers className="w-4 h-4 text-slate-400" />}
          >
            {eventTypeData.length ? (
              <ResponsiveContainer width="100%" height={Math.max(180, eventTypeData.length * 34 + 24)}>
                <BarChart data={eventTypeData} layout="vertical" margin={{ top: 4, right: 44, bottom: 4, left: 8 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: GRID }} allowDecimals={false} />
                  <YAxis
                    type="category"
                    dataKey="name"
                    tick={AXIS_TICK}
                    tickLine={false}
                    axisLine={false}
                    width={104}
                  />
                  <Tooltip cursor={{ fill: 'rgba(148,163,184,0.12)' }} contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} />
                  <Bar dataKey="value" name="Entries" radius={[0, 4, 4, 0]} barSize={16}>
                    {eventTypeData.map((d, i) => (
                      <Cell key={d.name} fill={d.name === 'error' ? STATUS.error : SERIES[i % SERIES.length]} />
                    ))}
                    {/* Direct labels: the count sits beside its own bar, so
                        no one has to read a value off the axis. */}
                    <LabelList dataKey="value" position="right" style={{ fill: '#475569', fontSize: 11, fontWeight: 600 }} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <EmptyPlot label="No event types classified" height={180} />
            )}
          </Panel>

          <Panel
            title="How long requests took"
            subtitle="Explicit durations found in the log, in ascending buckets."
            icon={<Timer className="w-4 h-4 text-slate-400" />}
            right={
              hasDurations ? (
                <span className="text-[11px] text-slate-500 tabular-nums shrink-0">
                  {report.duration_stats.count.toLocaleString()} timed
                </span>
              ) : undefined
            }
          >
            {hasDurations ? (
              <ResponsiveContainer width="100%" height={Math.max(180, durationBuckets.length * 34 + 24)}>
                <BarChart data={durationBuckets} layout="vertical" margin={{ top: 4, right: 44, bottom: 4, left: 8 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: GRID }} allowDecimals={false} />
                  <YAxis type="category" dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} width={104} />
                  <Tooltip cursor={{ fill: 'rgba(148,163,184,0.12)' }} contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} />
                  {/* One sequential hue, light to dark: this is a magnitude
                      axis (time), not a set of identities, so it must not
                      be painted with categorical colours. */}
                  <Bar dataKey="count" name="Requests" radius={[0, 4, 4, 0]} barSize={16}>
                    {durationBuckets.map((b, i) => (
                      <Cell
                        key={b.label}
                        fill={DURATION_RAMP[i] || DURATION_RAMP[DURATION_RAMP.length - 1]}
                      />
                    ))}
                    <LabelList dataKey="count" position="right" style={{ fill: '#475569', fontSize: 11, fontWeight: 600 }} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <EmptyPlot label="No explicit durations logged in this window" height={180} />
            )}
          </Panel>

        </div>

        <Panel
          title="What's failing"
          subtitle="Each row pairs the exception with the call it was thrown from, ranked by how many error entries share it."
          icon={<AlertOctagon className="w-4 h-4 text-rose-500" />}
        >
          {report.headline_failure ? (
            <div className="px-3 flex flex-col gap-3">
              {/* The lead: state the dominant failure outright rather than
                  leaving the reader to infer it from a bar length. */}
              <div className="bg-rose-50/70 border border-rose-200 rounded-lg px-4 py-3.5 flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
                <div className="min-w-0 flex flex-col gap-0.5">
                  <div className="text-lg font-bold text-rose-700 leading-tight break-words">
                    {report.headline_failure.exception}
                  </div>
                  {report.headline_failure.method && (
                    <div className="text-xs text-slate-700">
                      thrown in{' '}
                      <span className="font-mono font-semibold text-slate-900">
                        {report.headline_failure.method}
                      </span>
                    </div>
                  )}
                  {report.headline_failure.owner && (
                    /* The owning type gets its own line: these names run long,
                       and truncating one mid-identifier is what made two
                       different frames read as the same row. */
                    <div className="font-mono text-[11px] text-slate-500 break-all">
                      {report.headline_failure.owner}
                    </div>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <div className="text-2xl font-bold text-rose-700 tabular-nums leading-tight">
                    {pct(report.headline_failure.share)}
                  </div>
                  <div className="text-[11px] text-slate-600 tabular-nums">
                    {report.headline_failure.count.toLocaleString()} of {report.error_count.toLocaleString()} errors
                  </div>
                </div>
              </div>

              {report.failure_signatures.length > 1 && (
                <div className="flex flex-col gap-1">
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                    Other failure signatures
                  </div>
                  <ul className="flex flex-col divide-y divide-slate-100">
                    {report.failure_signatures.slice(1).map((sig, i) => (
                      <li key={`${sig.exception}-${sig.method}-${i}`} className="py-2 flex items-start gap-3">
                        <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                          <div className="text-xs text-slate-800">
                            <span className="font-semibold">{sig.exception}</span>
                            {sig.method && (
                              <>
                                <span className="text-slate-400"> in </span>
                                <span className="font-mono">{sig.method}</span>
                              </>
                            )}
                          </div>
                          {sig.owner && (
                            <div className="font-mono text-[10px] text-slate-400 break-all">{sig.owner}</div>
                          )}
                        </div>
                        <div className="shrink-0 flex items-center gap-2 pt-0.5">
                          {/* A share bar reads at a glance without needing an
                              axis; the exact figures sit beside it. */}
                          <span className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden hidden sm:block">
                            <span
                              className="block h-full rounded-full bg-rose-400"
                              style={{ width: `${Math.max(4, sig.share * 100)}%` }}
                            />
                          </span>
                          <span className="text-[11px] text-slate-400 tabular-nums w-9 text-right">
                            {pct(sig.share)}
                          </span>
                          <span className="text-xs font-bold text-slate-700 tabular-nums w-8 text-right">
                            {sig.count}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <EmptyPlot label="No exceptions thrown in this window" height={140} />
          )}
        </Panel>


        {/* ---- Capture fidelity + HTTP codes ---- */}
        <div className="grid gap-4 lg:grid-cols-3">
          <Panel
            title="Capture fidelity"
            subtitle="The ILA parser preserves every byte; this is how much of it was recognised."
            icon={<Layers className="w-4 h-4 text-slate-400" />}
          >
            <div className="px-3 flex flex-col gap-3">
              <div className="flex h-3 w-full rounded-full overflow-hidden bg-slate-100" role="img" aria-label="Parse status distribution">
                {Object.entries(report.parse_status_counts).map(([status, count]) => (
                  <div
                    key={status}
                    title={`${status}: ${count}`}
                    style={{
                      width: parseTotal ? `${(count / parseTotal) * 100}%` : '0%',
                      backgroundColor: PARSE_STATUS_COLOR[status] || STATUS.neutral,
                    }}
                  />
                ))}
              </div>
              <ul className="flex flex-col gap-1.5">
                {Object.entries(report.parse_status_counts).map(([status, count]) => (
                  <li key={status} className="flex items-center gap-2 text-[11px]">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ backgroundColor: PARSE_STATUS_COLOR[status] || STATUS.neutral }}
                    />
                    <span className="capitalize text-slate-700 flex-1">{status}</span>
                    <span className="font-bold text-slate-600 tabular-nums">{count.toLocaleString()}</span>
                    <span className="text-slate-400 tabular-nums w-12 text-right">
                      {parseTotal ? pct(count / parseTotal) : '—'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </Panel>

          <Panel
            title="HTTP responses"
            subtitle="Status codes quoted in the log text."
            icon={<Gauge className="w-4 h-4 text-slate-400" />}
          >
            {httpData.length ? (
              <ul className="px-3 flex flex-col gap-1.5">
                {httpData.map((h) => {
                  const code = Number(h.name);
                  const tone = code >= 500 ? STATUS.error : code >= 400 ? STATUS.warn : STATUS.ok;
                  return (
                    <li key={h.name} className="flex items-center gap-2 text-[11px]">
                      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: tone }} />
                      <span className="font-mono font-bold text-slate-700">{h.name}</span>
                      <span className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                        <span
                          className="block h-full rounded-full"
                          style={{
                            width: `${(h.value / Math.max(...httpData.map((x) => x.value))) * 100}%`,
                            backgroundColor: tone,
                          }}
                        />
                      </span>
                      <span className="font-bold text-slate-600 tabular-nums">{h.value}</span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <EmptyPlot label="No HTTP status codes in this window" height={140} />
            )}
          </Panel>

          <Panel
            title="Services seen"
            subtitle="Components named at the start of an entry."
            icon={<Building2 className="w-4 h-4 text-slate-400" />}
          >
            {Object.keys(report.top_services).length ? (
              <ul className="px-3 flex flex-col gap-1.5">
                {Object.entries(report.top_services).map(([name, count], i) => (
                  <li key={name} className="flex items-center gap-2 text-[11px]">
                    <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: SERIES[i % SERIES.length] }} />
                    <span className="flex-1 min-w-0 truncate text-slate-700" title={name}>
                      {name}
                    </span>
                    <span className="font-bold text-slate-600 tabular-nums">{count}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyPlot label="No named services in this window" height={140} />
            )}
          </Panel>
        </div>

        {/* ---- Transactions table: failing first ---- */}
        <Panel
          title="Transactions"
          subtitle="One row per Log Tracker No. Failing transactions lead, then longest-running."
          icon={<Building2 className="w-4 h-4 text-slate-400" />}
          right={
            <span className="text-[11px] text-slate-500 tabular-nums shrink-0">
              {report.trackers.length.toLocaleString()} shown
            </span>
          }
        >
          {report.trackers.length ? (
            <div className="overflow-x-auto max-h-96 px-3">
              <table className="w-full text-left text-xs min-w-[46rem]">
                <thead className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-200 sticky top-0 bg-white">
                  <tr>
                    <th className="py-2 pr-3 font-bold">Tracker</th>
                    <th className="py-2 pr-3 font-bold text-right">Entries</th>
                    <th className="py-2 pr-3 font-bold text-right">Errors</th>
                    <th className="py-2 pr-3 font-bold text-right">Span</th>
                    <th className="py-2 pr-3 font-bold">Started</th>
                    <th className="py-2 pr-3 font-bold">Flow</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {report.trackers.map((t: IlaTracker) => (
                    <tr key={t.tracker_id} className={t.errors > 0 ? 'bg-rose-50/40' : undefined}>
                      <td className="py-2 pr-3 font-mono font-semibold text-slate-800 whitespace-nowrap">
                        {t.tracker_id}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums text-slate-600">{t.entries}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {t.errors > 0 ? (
                          <span className="font-bold text-rose-600">{t.errors}</span>
                        ) : (
                          <span className="text-slate-300">0</span>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums text-slate-600 whitespace-nowrap">
                        {fmtMs(t.span_ms)}
                      </td>
                      <td className="py-2 pr-3 tabular-nums text-slate-500 whitespace-nowrap">
                        {fmtTime(t.first_timestamp)}
                      </td>
                      <td className="py-2 pr-3">
                        <div className="flex flex-wrap gap-1">
                          {t.event_types.map((et) => (
                            <span
                              key={et}
                              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
                                et === 'error'
                                  ? 'bg-rose-50 text-rose-700 border-rose-200'
                                  : et === 'warning_message'
                                    ? 'bg-amber-50 text-amber-700 border-amber-200'
                                    : 'bg-slate-50 text-slate-600 border-slate-200'
                              }`}
                            >
                              {et}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyPlot label="No tracker-correlated transactions in this window" height={140} />
          )}
        </Panel>

        {/* ---- Error detail ---- */}
        {report.recent_errors.length > 0 && (
          <Panel
            title="Error entries"
            subtitle="The failing entries behind the counts above."
            icon={<AlertOctagon className="w-4 h-4 text-rose-500" />}
            right={<MaskedBadge />}
          >
            <ul className="px-3 flex flex-col divide-y divide-slate-100 max-h-80 overflow-y-auto">
              {report.recent_errors.map((e, i) => (
                <li key={`${e.tracker_id}-${i}`} className="py-2.5 flex flex-col gap-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-[11px] font-semibold text-slate-700">{e.tracker_id}</span>
                    <span className="text-[11px] text-slate-400 tabular-nums">{fmtTime(e.timestamp)}</span>
                    {e.exceptions.map((ex) => (
                      <span
                        key={ex}
                        title={ex}
                        className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-50 text-rose-700 border border-rose-200 font-mono"
                      >
                        {shortenTail(ex, 28)}
                      </span>
                    ))}
                    {e.http_codes.filter(Boolean).map((c) => (
                      <span
                        key={String(c)}
                        className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-200 tabular-nums"
                      >
                        HTTP {c}
                      </span>
                    ))}
                  </div>
                  <p className="text-[11px] text-slate-600 leading-relaxed break-words">{maskText(e.message)}</p>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </>
  );
};


export const IlaBankView: React.FC = () => {
  const [report, setReport] = useState<IlaSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DateRangeValue>(defaultRange());
  const [trackerFilter, setTrackerFilter] = useState('');
  const [appliedTracker, setAppliedTracker] = useState('');

  const fetchReport = async (r: DateRangeValue, tracker: string) => {
    setLoading(true);
    setError(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      if (tracker) params.set('tracker', tracker);
      setReport(await api.get<IlaSummary>(`/api/ila/summary?${params}`));
      setAppliedTracker(tracker);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load the ILA Bank report');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReport(range, appliedTracker);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);


  return (
    <div className="space-y-5">
      {/* ---- Filters: one row, above every chart ---- */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs px-4 py-3 flex flex-wrap items-end gap-3">
        <DateRangeFilter value={range} onChange={setRange} />

        <div className="flex flex-col gap-1">
          <label htmlFor="ila-tracker" className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
            Log Tracker No.
          </label>
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              id="ila-tracker"
              value={trackerFilter}
              onChange={(e) => setTrackerFilter(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && fetchReport(range, trackerFilter.trim())}
              placeholder="e.g. ILA-77001"
              className="w-52 pl-8 pr-7 py-1.5 text-xs font-medium border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400"
            />
            {trackerFilter && (
              <button
                onClick={() => {
                  setTrackerFilter('');
                  fetchReport(range, '');
                }}
                aria-label="Clear tracker filter"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 cursor-pointer"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        <button
          onClick={() => fetchReport(range, trackerFilter.trim())}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/40"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          {loading ? 'Loading…' : 'Apply'}
        </button>

        {appliedTracker && (
          <span className="text-[11px] font-semibold text-blue-700 bg-blue-50 border border-blue-200 rounded-full px-2.5 py-1">
            Filtered to {appliedTracker}
          </span>
        )}

        {report?.status === 'ok' && (
          <span className="ml-auto text-[11px] text-slate-500 tabular-nums">
            {fmtTime(report.window_start)} → {fmtTime(report.window_end)}
          </span>
        )}
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 rounded-xl px-4 py-3 text-xs font-semibold flex items-center gap-2">
          <AlertOctagon className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {!error && loading && !report && (
        <div className="bg-white border border-slate-200 rounded-xl px-4 py-16 text-center text-xs text-slate-400">
          Loading ILA Bank activity…
        </div>
      )}

      {!error && report?.status === 'no_data' && (
        <div className="bg-white border border-slate-200 rounded-xl px-4 py-16 text-center">
          <Building2 className="w-8 h-8 text-slate-300 mx-auto mb-3" />
          <p className="text-sm font-semibold text-slate-600">No ILA Bank activity in this window</p>
          <p className="text-xs text-slate-400 mt-1">
            {report.message} Widen the date range, or upload a log parsed with the ILA Bank profile.
          </p>
        </div>
      )}

      {!error && report?.status === 'ok' && <IlaReportBody report={report} />}
    </div>
  );
};
