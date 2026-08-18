import React, { useState, useEffect } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid, AreaChart, Area } from 'recharts';
import { LogEvent, LogLevel } from '../types';
import { Clock, Filter, BarChart2 } from 'lucide-react';
import { api } from '../api';
import { useChartColors } from '../theme/useChartColors';

export const TimelineView: React.FC = () => {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [chartType, setChartType] = useState<'bar' | 'area'>('bar');
  const chartColors = useChartColors();
  const [selectedLevels, setSelectedLevels] = useState<Record<string, boolean>>({
    CRITICAL: true,
    ERROR: true,
    WARN: true,
    INFO: true,
    DEBUG: true
  });

  useEffect(() => {
    const loadEvents = async () => {
      try {
        const data = await api.get<{ events: LogEvent[] }>('/api/logs/events?pageSize=1000');
        setEvents(data.events || []);
      } catch (err) {
        console.error('Failed to load timeline events:', err);
      }
    };
    loadEvents();
  }, []);

  // Process events into time buckets
  const timeBucketsMap: Record<string, Record<string, number>> = {};

  events.forEach((evt) => {
    if (!selectedLevels[evt.level]) return;
    const d = evt.ts_utc ? new Date(evt.ts_utc) : new Date(NaN);
    const timeKey = isNaN(d.getTime())
      ? '00:00'
      : `${String(d.getUTCHours()).padStart(2, '0')}:${String(Math.floor(d.getUTCMinutes() / 5) * 5).padStart(2, '0')}`;

    if (!timeBucketsMap[timeKey]) {
      timeBucketsMap[timeKey] = { CRITICAL: 0, ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0 };
    }
    timeBucketsMap[timeKey][evt.level] = (timeBucketsMap[timeKey][evt.level] || 0) + 1;
  });

  const chartData = Object.entries(timeBucketsMap)
    .sort(([timeA], [timeB]) => timeA.localeCompare(timeB))
    .map(([time, levels]) => ({
      time,
      ...levels
    }));

  const levelColors: Record<string, string> = {
    CRITICAL: chartColors.sevCritical,
    ERROR: chartColors.sevError,
    WARN: chartColors.sevWarn,
    INFO: chartColors.sevInfo,
    DEBUG: chartColors.sevDebug
  };

  const toggleLevel = (lvl: string) => {
    setSelectedLevels((prev) => ({ ...prev, [lvl]: !prev[lvl] }));
  };

  return (
    <div className="space-y-6">
      <div className="bg-surface p-6 rounded-2xl border border-surface-border shadow-2xs space-y-4 card-brand-glow">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-text flex items-center gap-2">
              <Clock className="w-5 h-5 text-brand" />
              Interactive Log Event Volume Timeline
            </h2>
            <p className="text-xs text-text-muted mt-0.5">
              5-minute time-bucketed event counts grouped by severity level across all ingested sources.
            </p>
          </div>

          <div className="flex items-center gap-2 bg-surface-alt p-1 rounded-lg border border-surface-border">
            <button
              onClick={() => setChartType('bar')}
              className={`px-3 py-1 rounded-md text-xs font-bold transition-all cursor-pointer ${
                chartType === 'bar' ? 'bg-surface text-text shadow-2xs' : 'text-text-secondary'
              }`}
            >
              Stacked Bar
            </button>
            <button
              onClick={() => setChartType('area')}
              className={`px-3 py-1 rounded-md text-xs font-bold transition-all cursor-pointer ${
                chartType === 'area' ? 'bg-surface text-text shadow-2xs' : 'text-text-secondary'
              }`}
            >
              Area Chart
            </button>
          </div>
        </div>

        {/* Severity Toggles */}
        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-surface-border">
          <span className="text-xs font-bold text-text-muted mr-2 flex items-center gap-1">
            <Filter className="w-3.5 h-3.5" /> Toggle Layers:
          </span>
          {Object.keys(selectedLevels).map((lvl) => {
            const isSelected = selectedLevels[lvl];
            return (
              <button
                key={lvl}
                onClick={() => toggleLevel(lvl)}
                className={`px-3 py-1 rounded-full text-xs font-bold transition-all cursor-pointer flex items-center gap-1.5 border ${
                  isSelected
                    ? 'bg-brand text-white border-brand shadow-2xs'
                    : 'bg-surface-alt text-text-muted border-surface-border opacity-60'
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: levelColors[lvl] }}
                ></span>
                <span>{lvl}</span>
              </button>
            );
          })}
        </div>

        {/* Recharts Timeline */}
        <div className="h-96 w-full pt-4">
          {chartData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-text-muted italic text-xs">
              No log data available for timeline visualization. Upload log files or click "Load Samples".
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              {chartType === 'bar' ? (
                <BarChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartColors.surfaceBorder} />
                  <XAxis dataKey="time" stroke={chartColors.textSecondary} fontSize={11} />
                  <YAxis stroke={chartColors.textSecondary} fontSize={11} />
                  <Tooltip
                    contentStyle={{ backgroundColor: chartColors.surface, borderRadius: '8px', border: `1px solid ${chartColors.surfaceBorder}`, color: chartColors.textSecondary, fontSize: '12px' }}
                  />
                  <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
                  {selectedLevels.CRITICAL && <Bar dataKey="CRITICAL" stackId="a" fill={levelColors.CRITICAL} />}
                  {selectedLevels.ERROR && <Bar dataKey="ERROR" stackId="a" fill={levelColors.ERROR} />}
                  {selectedLevels.WARN && <Bar dataKey="WARN" stackId="a" fill={levelColors.WARN} />}
                  {selectedLevels.INFO && <Bar dataKey="INFO" stackId="a" fill={levelColors.INFO} />}
                  {selectedLevels.DEBUG && <Bar dataKey="DEBUG" stackId="a" fill={levelColors.DEBUG} />}
                </BarChart>
              ) : (
                <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartColors.surfaceBorder} />
                  <XAxis dataKey="time" stroke={chartColors.textSecondary} fontSize={11} />
                  <YAxis stroke={chartColors.textSecondary} fontSize={11} />
                  <Tooltip
                    contentStyle={{ backgroundColor: chartColors.surface, borderRadius: '8px', border: `1px solid ${chartColors.surfaceBorder}`, color: chartColors.textSecondary, fontSize: '12px' }}
                  />
                  <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
                  {selectedLevels.CRITICAL && <Area type="monotone" dataKey="CRITICAL" stackId="1" stroke={levelColors.CRITICAL} fill={levelColors.CRITICAL} />}
                  {selectedLevels.ERROR && <Area type="monotone" dataKey="ERROR" stackId="1" stroke={levelColors.ERROR} fill={levelColors.ERROR} />}
                  {selectedLevels.WARN && <Area type="monotone" dataKey="WARN" stackId="1" stroke={levelColors.WARN} fill={levelColors.WARN} />}
                  {selectedLevels.INFO && <Area type="monotone" dataKey="INFO" stackId="1" stroke={levelColors.INFO} fill={levelColors.INFO} />}
                  {selectedLevels.DEBUG && <Area type="monotone" dataKey="DEBUG" stackId="1" stroke={levelColors.DEBUG} fill={levelColors.DEBUG} />}
                </AreaChart>
              )}
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
};
