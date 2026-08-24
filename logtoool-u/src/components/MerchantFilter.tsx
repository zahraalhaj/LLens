import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Store, X } from 'lucide-react';

interface MerchantFilterProps {
  value: string;
  onChange: (value: string) => void;
  options: string[];
}

const MAX_RESULTS = 10;

export const MerchantFilter: React.FC<MerchantFilterProps> = ({ value, onChange, options }) => {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setQuery(value);
  }, [value]);

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery(value);
      }
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [value]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = q ? options.filter((m) => m.toLowerCase().includes(q)) : options;
    return matches.slice(0, MAX_RESULTS);
  }, [query, options]);

  const select = (m: string) => {
    onChange(m);
    setQuery(m);
    setOpen(false);
  };

  const clear = () => {
    onChange('');
    setQuery('');
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Merchant</label>
      <div className="relative">
        <Store className="w-3.5 h-3.5 text-slate-400 absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          type="text"
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          placeholder="All merchants"
          className="text-xs bg-white border border-slate-300 rounded-md pl-7 pr-6 py-1.5 text-slate-700 w-[200px] focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {value && (
          <button
            type="button"
            onClick={clear}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 cursor-pointer"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      {open && (
        <div className="absolute z-10 mt-1 w-[220px] max-h-72 overflow-y-auto bg-white border border-slate-200 rounded-md shadow-lg">
          <button
            type="button"
            onClick={clear}
            className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 cursor-pointer ${
              !value ? 'font-semibold text-blue-600' : 'text-slate-700'
            }`}
          >
            All merchants
          </button>
          {filtered.length === 0 ? (
            <div className="px-3 py-1.5 text-xs text-slate-400 italic">No matches</div>
          ) : (
            filtered.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => select(m)}
                title={m}
                className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 truncate cursor-pointer ${
                  m === value ? 'font-semibold text-blue-600' : 'text-slate-700'
                }`}
              >
                {m}
              </button>
            ))
          )}
          {options.length > MAX_RESULTS && filtered.length === MAX_RESULTS && (
            <div className="px-3 py-1 text-[10px] text-slate-400 border-t border-slate-100">
              Showing {MAX_RESULTS} of {options.length} — keep typing to narrow down
            </div>
          )}
        </div>
      )}
    </div>
  );
};
