import React from 'react';
import { Database, Sparkles, RefreshCw, Trash2, Cpu } from 'lucide-react';

interface NavbarProps {
  onLoadSamples: () => void;
  onClearLogs: () => void;
  totalLogs: number;
  ollamaAvailable: boolean;
  canClear: boolean;
}

export const Navbar: React.FC<NavbarProps> = ({ onLoadSamples, onClearLogs, totalLogs, ollamaAvailable, canClear }) => {
  return (
    <header className="bg-white border-b border-slate-200 px-6 py-3 flex flex-wrap items-center justify-between gap-4 shadow-2xs sticky top-0 z-20">
      <div className="flex items-center gap-3">
        <div className="bg-blue-600 text-white p-2 rounded-lg font-bold flex items-center justify-center w-9 h-9 shadow-xs">
          <Cpu className="w-5 h-5" />
        </div>
        <div>
          <h1 className="text-lg font-extrabold text-slate-900 tracking-tight flex items-center gap-2">
            LOGTOOL
          </h1>
          <p className="text-xs text-slate-500">Log Ingestion, Normalization & Analytics</p>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="hidden md:flex items-center gap-3 bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200 text-xs">
          <div className="flex items-center gap-1.5 text-slate-600 font-medium">
            <Database className="w-3.5 h-3.5 text-blue-600" />
            <span>Storage:</span>
            <span className="font-mono text-blue-600 font-bold">SQLite</span>
          </div>
          <div className="h-3 w-px bg-slate-300"></div>
          <div className="flex items-center gap-1.5 text-slate-600 font-medium">
            <Sparkles className={`w-3.5 h-3.5 ${ollamaAvailable ? 'text-amber-500' : 'text-slate-400'}`} />
            <span>Ollama:</span>
            <span className={`font-mono font-bold ${ollamaAvailable ? 'text-emerald-600' : 'text-slate-400'}`}>
              {ollamaAvailable ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>
          <div className="h-3 w-px bg-slate-300"></div>
          <div className="font-semibold text-slate-700">
            Total Ingested: <span className="text-blue-600 font-extrabold">{totalLogs.toLocaleString()}</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onLoadSamples}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-md border border-slate-200 transition-all cursor-pointer"
            title="Reload bundled sample logs"
          >
            <RefreshCw className="w-3.5 h-3.5 text-slate-500" />
            <span>Load Samples</span>
          </button>

          {canClear && (
            <button
              onClick={onClearLogs}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-rose-50 hover:bg-rose-100 text-rose-700 rounded-md border border-rose-200 transition-all cursor-pointer"
              title="Clear current log database (admin only)"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-600" />
              <span>Clear</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
};
