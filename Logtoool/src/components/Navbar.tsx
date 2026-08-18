import React from 'react';
import { RefreshCw, Trash2, Monitor, Eye, LogOut, Sparkles } from 'lucide-react';
import { useTheme } from '../theme/ThemeContext';
import { User } from '../types';

interface NavbarProps {
  onLoadSamples: () => void;
  onClearLogs: () => void;
  totalLogs: number;
  ollamaAvailable: boolean;
  canClear: boolean;
  user: User;
  onLogout: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({ onLoadSamples, onClearLogs, totalLogs, ollamaAvailable, canClear, user, onLogout }) => {
  const { theme, toggle } = useTheme();

  return (
    <header className="bg-surface border-b border-surface-border px-6 py-3 flex flex-wrap items-center justify-between gap-4 sticky top-0 z-20 backdrop-blur-sm bg-surface/90">
      <div className="flex items-center gap-3">
        <div className="bg-brand text-white p-2 rounded-lg font-bold flex items-center justify-center w-9 h-9 shadow-md shadow-brand/20">
          <span className="text-sm font-extrabold">LL</span>
        </div>
        <div>
          <h1 className="text-lg font-extrabold text-text tracking-tight flex items-center gap-2">
            LLENS
          </h1>
          <p className="text-xs text-text-secondary">Log Ingestion, Normalization & Analytics</p>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <button
            onClick={toggle}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-surface-alt hover:bg-surface-border text-text-secondary rounded-md border border-surface-border transition-all cursor-pointer"
            title={theme === 'observatory' ? 'Switch to default theme' : 'Switch to Dark theme'}
          >
            {theme === 'observatory' ? <Monitor className="w-3.5 h-3.5 text-success" /> : <Eye className="w-3.5 h-3.5" />}
            <span>{theme === 'observatory' ? 'Default' : 'Dark'}</span>
          </button>

          <button
            onClick={onLoadSamples}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-surface-alt hover:bg-surface-border text-text-secondary rounded-md border border-surface-border transition-all cursor-pointer"
            title="Reload bundled sample logs"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Load Samples</span>
          </button>

          {canClear && (
            <button
              onClick={onClearLogs}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-error-light hover:bg-error/10 text-error rounded-md border border-error/20 transition-all cursor-pointer"
              title="Clear current log database (admin only)"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Clear</span>
            </button>
          )}

          <div className="h-5 w-px bg-surface-border"></div>

          <div className="hidden md:flex items-center gap-1.5 text-xs text-text-secondary font-medium">
            <Sparkles className={`w-3.5 h-3.5 ${ollamaAvailable ? 'text-warning' : 'text-text-muted'}`} />
            <span>Ollama:</span>
            <span className={`font-mono font-bold ${ollamaAvailable ? 'text-success' : 'text-text-muted'}`}>
              {ollamaAvailable ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>

          <div className="h-5 w-px bg-surface-border"></div>

          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-full bg-brand/15 text-brand flex items-center justify-center text-xs font-extrabold">
              {user.username.charAt(0).toUpperCase()}
            </div>
            <div className="hidden sm:block">
              <div className="text-xs font-bold text-text leading-tight">{user.username}</div>
              <div className="text-[10px] text-text-muted capitalize leading-tight">{user.role}</div>
            </div>
            <button
              onClick={onLogout}
              title="Sign out"
              className="p-1.5 rounded-md text-text-muted hover:text-error hover:bg-error-light transition-all cursor-pointer"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};
