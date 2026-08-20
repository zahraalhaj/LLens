import React from 'react';
import { NAV_SECTIONS, TabType } from '../navConfig';
import { Logo } from './Logo';

export type { TabType };

interface SidebarProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  criticalCount: number;
  userRole: 'admin' | 'member';
  ollamaAvailable: boolean;
  engineOnline: boolean | null;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  criticalCount,
  userRole,
  ollamaAvailable,
  engineOnline,
}) => {
  const engineStatus =
    engineOnline === null ? 'checking' : engineOnline ? 'ready' : 'offline';

  const engineStatusStyles: Record<string, string> = {
    checking: 'text-slate-400',
    ready: 'text-emerald-400',
    offline: 'text-rose-400',
  };

  const engineStatusLabel: Record<string, string> = {
    checking: 'CHECKING',
    ready: 'READY',
    offline: 'OFFLINE',
  };

  return (
    <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col justify-between shrink-0 min-h-screen text-slate-300">
      <div className="min-h-0 overflow-y-auto">
        <div className="p-5 border-b border-slate-800/80">
          <Logo variant="dark" markClassName="w-8 h-6" />
          <div className="text-[11px] text-slate-400 mt-1.5">Payment Log Analytics</div>
        </div>

        <nav className="px-3 py-4 space-y-5">
          {NAV_SECTIONS.map((section) => {
            const items = section.items.filter((item) => !item.adminOnly || userRole === 'admin');
            if (items.length === 0) return null;

            return (
              <div key={section.title}>
                <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider px-3 mb-1.5">
                  {section.title}
                </div>
                <div className="space-y-0.5">
                  {items.map((item) => {
                    const Icon = item.icon;
                    const isActive = activeTab === item.id;
                    const badge =
                      item.id === 'alerts' && criticalCount > 0
                        ? `${criticalCount} Critical`
                        : item.id === 'chat' && ollamaAvailable
                        ? 'AI'
                        : null;

                    return (
                      <button
                        key={item.id}
                        onClick={() => setActiveTab(item.id)}
                        title={item.description}
                        className={`relative w-full flex items-center justify-between pl-3.5 pr-3 py-2 rounded-lg text-[13px] font-medium transition-colors cursor-pointer ${
                          isActive
                            ? 'bg-blue-600/15 text-white'
                            : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/60'
                        }`}
                      >
                        {isActive && (
                          <span className="absolute left-0 top-1/2 -translate-y-1/2 h-4 w-[3px] rounded-full bg-blue-400" />
                        )}
                        <div className="flex items-center gap-2.5 min-w-0">
                          <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-blue-400' : 'text-slate-500'}`} />
                          <span className="truncate">{item.label}</span>
                        </div>
                        {badge && (
                          <span
                            className={`shrink-0 text-[10px] font-bold px-2 py-0.5 rounded-full ${
                              badge === 'AI'
                                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                            }`}
                          >
                            {badge}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>
      </div>

      <div className="p-4 border-t border-slate-800/80 shrink-0">
        <div className="bg-slate-950 rounded-lg p-3 border border-slate-800">
          <div className="flex items-center justify-between text-[11px] mb-1">
            <span className="text-slate-400">Log Engine</span>
            <span className={`flex items-center gap-1.5 font-bold ${engineStatusStyles[engineStatus]}`}>
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  engineStatus === 'ready'
                    ? 'bg-emerald-400 animate-pulse'
                    : engineStatus === 'offline'
                    ? 'bg-rose-400'
                    : 'bg-slate-500 animate-pulse'
                }`}
              />
              {engineStatusLabel[engineStatus]}
            </span>
          </div>
          <div className="text-[10px] font-mono text-slate-500 truncate">
            SQLite (local) + Ollama {ollamaAvailable ? '(online)' : '(offline)'}
          </div>
        </div>
      </div>
    </aside>
  );
};
