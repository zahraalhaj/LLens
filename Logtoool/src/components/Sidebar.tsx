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
  Users as UsersIcon,
  ServerCog,
} from 'lucide-react';
import { User } from '../types';

export type TabType = 'upload' | 'explore' | 'timeline' | 'stats' | 'profiling' | 'alerts' | 'chat' | 'settings' | 'users' | 'control-center';

interface SidebarProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  criticalCount: number;
  user: User;
  ollamaAvailable: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab, criticalCount, user, ollamaAvailable }) => {
  const navItems = [
    { id: 'upload' as TabType, label: 'Upload & Ingest', icon: Upload, badge: null },
    { id: 'explore' as TabType, label: 'Explore Events', icon: Search, badge: null },
    { id: 'timeline' as TabType, label: 'Timeline View', icon: BarChart3, badge: null },
    { id: 'stats' as TabType, label: 'Analytics & Stats', icon: Terminal, badge: null },
    { id: 'profiling' as TabType, label: 'Anomaly Insights', icon: ActivitySquare, badge: null },
    { id: 'alerts' as TabType, label: 'Alerts Center', icon: BellRing, badge: criticalCount > 0 ? `${criticalCount} Critical` : null },
    { id: 'chat' as TabType, label: 'Chat With Logs', icon: MessageSquareCode, badge: ollamaAvailable ? 'AI' : null },
    { id: 'settings' as TabType, label: 'Profiles & Settings', icon: Settings, badge: null },
    ...(user.role === 'admin' ? [{ id: 'users' as TabType, label: 'Users', icon: UsersIcon, badge: null }] : []),
    ...(user.role === 'admin' ? [{ id: 'control-center' as TabType, label: 'Control Center', icon: ServerCog, badge: null }] : []),
  ];

  return (
    <aside className="w-64 bg-sidebar border-r border-sidebar-border flex flex-col justify-between shrink-0 min-h-screen text-sidebar-text">
      <div>
        {/* Brand */}
        <div className="p-5 border-b border-sidebar-border flex items-center gap-3">
          <div className="w-9 h-9 bg-brand text-white font-extrabold text-sm rounded-lg flex items-center justify-center shadow-lg shadow-brand/20">
            LL
          </div>
          <div>
            <div className="font-extrabold text-white tracking-wide text-sm">LLENS</div>
            <div className="text-[10px] font-medium text-sidebar-text">Log Analysis Platform</div>
          </div>
        </div>

        {/* Navigation */}
        <div className="px-3 py-4">
          <div className="text-[10px] font-bold text-sidebar-text uppercase tracking-wider px-3 mb-2 opacity-60">
            Navigation Menu
          </div>
          <nav className="space-y-0.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id)}
                  className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-lg text-xs font-semibold cursor-pointer transition-all duration-150 ${
                    isActive
                      ? 'bg-brand text-white shadow-md shadow-brand/25 sidebar-active-indicator'
                      : 'text-sidebar-text hover:text-white hover:bg-sidebar-hover'
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className={`w-4 h-4 ${isActive ? 'text-white' : 'text-sidebar-text'}`} />
                    <span>{item.label}</span>
                  </div>
                  {item.badge && (
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                        item.badge === 'AI'
                          ? 'bg-brand-pressed/20 text-brand-pressed border border-brand-pressed/30'
                          : 'bg-error/20 text-error border border-error/30 badge-pulse'
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
    </aside>
  );
};
