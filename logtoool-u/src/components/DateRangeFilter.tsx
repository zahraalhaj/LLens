import React, { createContext, useContext, useState } from 'react';
import { CalendarClock } from 'lucide-react';

export interface DateRangeValue {
  fromDate: string; // YYYY-MM-DD
  fromTime: string; // HH:MM, optional (empty = start of day)
  toDate: string; // YYYY-MM-DD
  toTime: string; // HH:MM, optional (empty = end of day)
}

function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

export function defaultRange(): DateRangeValue {
  const today = todayUtc();
  return { fromDate: today, fromTime: '', toDate: today, toTime: '' };
}

// Builds the ISO 8601 UTC strings the backend's date_from/date_to params
// expect. Times are optional -- an empty from-time means start of day, an
// empty to-time means end of day (harmless even for "today" since there's
// never future data to over-include).
export function toIsoRange(value: DateRangeValue): { dateFrom: string; dateTo: string } {
  const fromTime = value.fromTime || '00:00';
  const toTime = value.toTime || '23:59';
  return {
    dateFrom: `${value.fromDate}T${fromTime}:00Z`,
    dateTo: `${value.toDate}T${toTime}:59Z`,
  };
}

// One date range shared by every analysis page (Payment Rail Monitoring,
// Cross-Log Analytics, ILA Bank, Currency Globe) so picking a range on one
// carries over when the user switches tabs, instead of each page silently
// resetting to "today". Each page still owns its own fetch timing -- this
// only shares the *value*.
const DateRangeContext = createContext<{ range: DateRangeValue; setRange: (value: DateRangeValue) => void } | null>(null);

export const DateRangeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [range, setRange] = useState<DateRangeValue>(defaultRange());
  return <DateRangeContext.Provider value={{ range, setRange }}>{children}</DateRangeContext.Provider>;
};

export function useSharedDateRange(): [DateRangeValue, (value: DateRangeValue) => void] {
  const ctx = useContext(DateRangeContext);
  if (!ctx) {
    throw new Error('useSharedDateRange must be used within a DateRangeProvider');
  }
  return [ctx.range, ctx.setRange];
}

interface DateRangeFilterProps {
  value: DateRangeValue;
  onChange: (value: DateRangeValue) => void;
}

export const DateRangeFilter: React.FC<DateRangeFilterProps> = ({ value, onChange }) => {
  const set = (patch: Partial<DateRangeValue>) => onChange({ ...value, ...patch });

  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex items-center gap-1.5 text-slate-400 pb-2">
        <CalendarClock className="w-3.5 h-3.5" />
        <span className="text-[10px] font-bold uppercase tracking-wider">UTC</span>
      </div>

      <div>
        <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">From</label>
        <div className="flex items-center gap-1.5">
          <input
            type="date"
            value={value.fromDate}
            max={value.toDate}
            onChange={(e) => set({ fromDate: e.target.value })}
            className="text-xs bg-white border border-slate-300 rounded-md px-2.5 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <input
            type="time"
            value={value.fromTime}
            onChange={(e) => set({ fromTime: e.target.value })}
            title="Optional -- defaults to start of day"
            className="text-xs bg-white border border-slate-300 rounded-md px-2.5 py-1.5 text-slate-500 w-[92px] focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      <div>
        <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">To</label>
        <div className="flex items-center gap-1.5">
          <input
            type="date"
            value={value.toDate}
            min={value.fromDate}
            onChange={(e) => set({ toDate: e.target.value })}
            className="text-xs bg-white border border-slate-300 rounded-md px-2.5 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <input
            type="time"
            value={value.toTime}
            onChange={(e) => set({ toTime: e.target.value })}
            title="Optional -- defaults to end of day"
            className="text-xs bg-white border border-slate-300 rounded-md px-2.5 py-1.5 text-slate-500 w-[92px] focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      <button
        type="button"
        onClick={() => onChange(defaultRange())}
        className="text-xs font-semibold text-blue-600 hover:text-blue-700 pb-2 cursor-pointer"
      >
        Reset to today
      </button>
    </div>
  );
};
