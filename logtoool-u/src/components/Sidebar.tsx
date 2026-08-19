import React from 'react';
import {
  Upload,
  Search,
  BarChart3,
  BellRing,
  MessageSquareCode,
  Settings,
  Terminal,
  ActivitySquare,
  Activity,
  KeyRound,
  Users as UsersIcon,
  ServerCog,
  LogOut,
} from 'lucide-react';
import { User } from '../types';

export type TabType = 'upload' | 'explore' | 'timeline' | 'stats' | 'profiling' | 'vplus' | 'otp-processor' | 'alerts' | 'chat' | 'settings' | 'users' | 'control-center';

interface SidebarProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  criticalCount: number;
  user: User;
  ollamaAvailable: boolean;
  onLogout: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab, criticalCount, user, ollamaAvailable, onLogout }) => {
  const navItems = [
    { id: 'upload' as TabType, label: 'Upload & Ingest', icon: Upload, badge: null },
    { id: 'explore' as TabType, label: 'Explore Events', icon: Search, badge: null },
    { id: 'timeline' as TabType, label: 'Timeline View', icon: BarChart3, badge: null },
    { id: 'stats' as TabType, label: 'Analytics & Stats', icon: Terminal, badge: null },
    { id: 'profiling' as TabType, label: 'Anomaly Insights', icon: ActivitySquare, badge: null },
    { id: 'vplus' as TabType, label: 'V+ Monitoring', icon: Activity, badge: null },
    { id: 'otp-processor' as TabType, label: 'OTP Processor', icon: KeyRound, badge: null },
    { id: 'alerts' as TabType, label: 'Alerts Center', icon: BellRing, badge: criticalCount > 0 ? `${criticalCount} Critical` : null },
    { id: 'chat' as TabType, label: 'Chat With Logs', icon: MessageSquareCode, badge: ollamaAvailable ? 'AI' : null },
    { id: 'settings' as TabType, label: 'Profiles & Settings', icon: Settings, badge: null },
    ...(user.role === 'admin' ? [{ id: 'users' as TabType, label: 'Users', icon: UsersIcon, badge: null }] : []),
    ...(user.role === 'admin' ? [{ id: 'control-center' as TabType, label: 'Control Center', icon: ServerCog, badge: null }] : []),
  ];

  return (
    <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col justify-between shrink-0 min-h-screen text-slate-300">
      <div>
        <div className="p-5 border-b border-slate-800/80 flex items-center gap-3">
          <div className="w-9 h-9 bg-blue-600 text-white font-extrabold text-sm rounded-lg flex items-center justify-center shadow-md">
            LT
          </div>
          <div>
            <div className="font-extrabold text-white tracking-wide text-sm">LOGTOOL</div>
            <div className="text-[10px] font-medium text-slate-400">Internal Log Analytics</div>
          </div>
        </div>

        <div className="px-3 py-4">
          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider px-3 mb-2">
            Navigation Menu
          </div>
          <nav className="space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id)}
                  className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                    isActive
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className={`w-4 h-4 ${isActive ? 'text-white' : 'text-slate-400'}`} />
                    <span>{item.label}</span>
                  </div>
                  {item.badge && (
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                        item.badge === 'AI'
                          ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                          : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                      }`}
                    >
                      {item.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>
      </div>

      <div className="p-4 border-t border-slate-800/80 space-y-3">
        <div className="bg-slate-950 rounded-lg p-3 border border-slate-800">
          <div className="flex items-center justify-between text-[11px] mb-1">
            <span className="text-slate-400 font-medium">Log Engine</span>
            <span className="flex items-center gap-1 text-emerald-400 font-bold">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              READY
            </span>
          </div>
          <div className="text-[10px] font-mono text-slate-500 truncate">
            SQLite (local) + Ollama {ollamaAvailable ? '(online)' : '(offline)'}
          </div>
        </div>

        <div className="flex items-center justify-between px-1">
          <div className="min-w-0">
            <div className="text-xs font-semibold text-slate-200 truncate">{user.username}</div>
            <div className="text-[10px] text-slate-500 capitalize">{user.role}</div>
          </div>
          <button
            onClick={onLogout}
            title="Sign out"
            className="p-2 rounded-md text-slate-400 hover:text-white hover:bg-slate-800 transition-all cursor-pointer"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>
  );
};
