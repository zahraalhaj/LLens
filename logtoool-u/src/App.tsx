import React, { useState, useEffect, useCallback } from 'react';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { Login } from './components/Login';
import { ChangePassword } from './components/ChangePassword';
import { Navbar } from './components/Navbar';
import { Sidebar } from './components/Sidebar';
import { TabType, getNavItem, NAV_ITEMS } from './navConfig';
import { UploadView } from './components/UploadView';
import { ExploreView } from './components/ExploreView';
import { StatsView } from './components/StatsView';
import { PaymentMonitoringView } from './components/PaymentMonitoringView';
import { AnalyticsView } from './components/AnalyticsView';
import { ProfilingView } from './components/ProfilingView';
import { AlertsView } from './components/AlertsView';
import { ChatView } from './components/ChatView';
import { AIAnalystView } from './components/AIAnalystView';
import { SettingsView } from './components/SettingsView';
import { UsersView } from './components/UsersView';
import { ControlCenterView } from './components/ControlCenterView';
import { ConfirmProvider } from './components/ConfirmDialog';
import { DateRangeProvider } from './components/DateRangeFilter';
import { ParserProfile, LogStats, DrillThroughTarget } from './types';
import { api } from './api';

const isValidTab = (value: string | null): value is TabType =>
  !!value && NAV_ITEMS.some((item) => item.id === value);

const readTabFromUrl = (): TabType => {
  const tab = new URLSearchParams(window.location.search).get('tab');
  return isValidTab(tab) ? tab : 'explore';
};

function AppShell() {
  const { user, logout } = useAuth();
  // Deep-linkable: the active tab is reflected in ?tab=, readable on load
  // and restorable via browser back/forward, so a tab can be shared by
  // pasting the URL instead of only "switch to X, then tell them where to
  // click."
  const [activeTab, setActiveTabState] = useState<TabType>(readTabFromUrl);
  const setActiveTab = useCallback((tab: TabType) => {
    setActiveTabState(tab);
    const params = new URLSearchParams(window.location.search);
    params.set('tab', tab);
    window.history.pushState(null, '', `${window.location.pathname}?${params.toString()}`);
  }, []);
  useEffect(() => {
    const onPopState = () => setActiveTabState(readTabFromUrl());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);
  const [profiles, setProfiles] = useState<ParserProfile[]>([]);
  const [stats, setStats] = useState<LogStats | null>(null);
  const [ollamaAvailable, setOllamaAvailable] = useState(false);
  // Lets a view OUTSIDE Analytics (e.g. AlertsView's "View Investigation"
  // button) drill through into the Investigation tab -- switches to the
  // Analytics tab and hands the target to AnalyticsView, which forwards it
  // to InvestigationView the same way its own internal charts already do.
  const [analyticsTarget, setAnalyticsTarget] = useState<DrillThroughTarget | null>(null);
  const investigateFromAnywhere = (target: DrillThroughTarget) => {
    setAnalyticsTarget(target);
    setActiveTab('analytics');
  };
  const fetchStats = useCallback(async () => {
    try {
      const data = await api.get<LogStats>('/api/logs/stats');
      setStats(data);
    } catch (err) {
      console.error('Failed to fetch app stats:', err);
    }
  }, []);

  const fetchProfiles = useCallback(async () => {
    try {
      const data = await api.get<ParserProfile[]>('/api/profiles');
      setProfiles(data);
    } catch (err) {
      console.error('Failed to fetch parser profiles:', err);
    }
  }, []);

  const fetchOllamaStatus = useCallback(async () => {
    try {
      const data = await api.get<{ available: boolean }>('/api/ai/ollama/health');
      setOllamaAvailable(data.available);
    } catch {
      setOllamaAvailable(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
    fetchProfiles();
    fetchOllamaStatus();
    // Poll Ollama status occasionally rather than on every interaction --
    // a health check on every rerender was a real perf bug we hit in the
    // Streamlit prototype of this app; don't repeat it here.
    const interval = setInterval(fetchOllamaStatus, 30_000);
    return () => clearInterval(interval);
  }, [fetchStats, fetchProfiles, fetchOllamaStatus]);

  const distinctSources = stats ? Object.keys(stats.source_distribution) : [];
  const distinctComponents: string[] = []; // not currently exposed by /api/logs/stats

  if (!user) return null; // AppRoot below guarantees user is set before rendering this

  const criticalCount = (stats?.severity_counts?.CRITICAL || 0) + (stats?.severity_counts?.ERROR || 0);
  const currentNavItem = getNavItem(activeTab);

  return (
    <div className="min-h-screen bg-slate-100 flex text-slate-900 font-sans antialiased">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        criticalCount={criticalCount}
        userRole={user.role}
        ollamaAvailable={ollamaAvailable}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <Navbar
          pageTitle={currentNavItem?.label ?? 'LLens'}
          pageDescription={currentNavItem?.description ?? ''}
          user={user}
          onLogout={logout}
        />

        <main className="flex-1 p-6 max-w-7xl w-full mx-auto space-y-6">
          <div key={activeTab} className="space-y-6">
            {activeTab === 'upload' && (
              <UploadView
                profiles={profiles}
                onIngestSuccess={() => {
                  fetchStats();
                  fetchProfiles();
                  setActiveTab('explore');
                }}
                isAdmin={user.role === 'admin'}
              />
            )}

            {activeTab === 'explore' && (
              <ExploreView
                sources={distinctSources}
                components={distinctComponents}
                onRefreshStats={fetchStats}
                isAdmin={user.role === 'admin'}
              />
            )}

            {activeTab === 'stats' && <StatsView />}

            {activeTab === 'profiling' && <ProfilingView />}

            {activeTab === 'payment-monitoring' && <PaymentMonitoringView />}

            {activeTab === 'analytics' && (
              <AnalyticsView externalTarget={analyticsTarget} onConsumeExternalTarget={() => setAnalyticsTarget(null)} />
            )}

            {activeTab === 'alerts' && <AlertsView onInvestigate={investigateFromAnywhere} isAdmin={user.role === 'admin'} />}

            {activeTab === 'chat' && <ChatView ollamaAvailable={ollamaAvailable} />}

            {activeTab === 'ai-analyst' && <AIAnalystView ollamaAvailable={ollamaAvailable} isAdmin={user.role === 'admin'} />}

            {activeTab === 'settings' && (
              <SettingsView profiles={profiles} onRefreshProfiles={fetchProfiles} ollamaAvailable={ollamaAvailable} isAdmin={user.role === 'admin'} />
            )}

            {activeTab === 'users' && user.role === 'admin' && <UsersView currentUserId={user.user_id} />}

            {activeTab === 'control-center' && user.role === 'admin' && <ControlCenterView />}
          </div>
        </main>
      </div>
    </div>
  );
}

function AppRoot() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="text-sm text-slate-500 font-medium">Loading…</div>
      </div>
    );
  }

  if (!user) return <Login />;

  if (user.must_change_password) return <ChangePassword />;

  return <AppShell />;
}

export default function App() {
  return (
    <ConfirmProvider>
      <AuthProvider>
        <DateRangeProvider>
          <AppRoot />
        </DateRangeProvider>
      </AuthProvider>
    </ConfirmProvider>
  );
}
