import React from 'react';
import { Database, Sparkles, RefreshCw, Trash2, LogOut } from 'lucide-react';
import { User } from '../types';

interface NavbarProps {
  pageTitle: string;
  pageDescription: string;
  onLoadSamples: () => void;
  onClearLogs: () => void;
  totalLogs: number;
  ollamaAvailable: boolean;
  engineOnline: boolean | null;
  canClear: boolean;
  user: User;
  onLogout: () => void;
}

// Sample-data seeding is a dev/demo convenience -- it has no place once the
// app is actually running against real ingested logs, so it's compiled out
// of production builds rather than hidden behind a runtime flag.
const SHOW_SAMPLE_LOADER = !import.meta.env.PROD;

export const Navbar: React.FC<NavbarProps> = ({
  pageTitle,
  pageDescription,
  onLoadSamples,
  onClearLogs,
  totalLogs,
  ollamaAvailable,
  engineOnline,
  canClear,
  user,
  onLogout,
}) => {
  const initials = user.username.slice(0, 2).toUpperCase();

  return (
    <header className="bg-white border-b border-slate-200 px-6 py-3.5 flex flex-wrap items-center justify-between gap-4 shadow-2xs sticky top-0 z-20">
      <div className="min-w-0">
        <div className="text-[10px] font-bold text-blue-600 uppercase tracking-wider mb-0.5">LLens</div>
        <h1 className="text-lg font-extrabold text-slate-900 tracking-tight truncate">{pageTitle}</h1>
        <p className="text-xs text-slate-500 truncate">{pageDescription}</p>
      </div>

      <div className="flex items-center gap-4">
        <div className="hidden lg:flex items-center gap-3 bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200 text-xs">
          <div
            className="flex items-center gap-1.5 font-medium"
            title={engineOnline === null ? 'Checking connection...' : engineOnline ? 'Log engine reachable' : 'Log engine unreachable'}
          >
            <Database className={`w-3.5 h-3.5 ${engineOnline ? 'text-blue-600' : 'text-slate-400'}`} />
            <span className="text-slate-600">Engine:</span>
            <span
              className={`font-mono font-bold ${
                engineOnline === null ? 'text-slate-400' : engineOnline ? 'text-emerald-600' : 'text-rose-600'
              }`}
            >
              {engineOnline === null ? 'CHECKING' : engineOnline ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>
          <div className="h-3 w-px bg-slate-300" />
          <div className="flex items-center gap-1.5 text-slate-600 font-medium">
            <Sparkles className={`w-3.5 h-3.5 ${ollamaAvailable ? 'text-amber-500' : 'text-slate-400'}`} />
            <span>Ollama:</span>
            <span className={`font-mono font-bold ${ollamaAvailable ? 'text-emerald-600' : 'text-slate-400'}`}>
              {ollamaAvailable ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>
          <div className="h-3 w-px bg-slate-300" />
          <div className="font-semibold text-slate-700">
            Ingested: <span className="text-blue-600 font-extrabold">{totalLogs.toLocaleString()}</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {SHOW_SAMPLE_LOADER && (
            <button
              onClick={onLoadSamples}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-md border border-slate-200 transition-colors cursor-pointer"
              title="Reload bundled sample logs (dev only)"
            >
              <RefreshCw className="w-3.5 h-3.5 text-slate-500" />
              <span>Load Samples</span>
            </button>
          )}

          {canClear && (
            <button
              onClick={onClearLogs}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-white hover:bg-rose-50 text-rose-600 rounded-md border border-rose-200 transition-colors cursor-pointer"
              title="Clear current log database (admin only)"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Clear</span>
            </button>
          )}
        </div>

        <div className="h-6 w-px bg-slate-200" />

        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-blue-600 text-white text-xs font-bold flex items-center justify-center shrink-0">
            {initials}
          </div>
          <div className="hidden sm:block min-w-0">
            <div className="text-xs font-semibold text-slate-800 truncate max-w-[120px]">{user.username}</div>
            <div className="text-[10px] text-slate-400 capitalize">{user.role}</div>
          </div>
          <button
            onClick={onLogout}
            title="Sign out"
            className="p-1.5 rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
};
