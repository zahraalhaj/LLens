import React, { useState, useEffect, useCallback } from 'react';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { Login } from './components/Login';
import { ChangePassword } from './components/ChangePassword';
import { Navbar } from './components/Navbar';
import { Sidebar, TabType } from './components/Sidebar';
import { UploadView } from './components/UploadView';
import { ExploreView } from './components/ExploreView';
import { TimelineView } from './components/TimelineView';
import { StatsView } from './components/StatsView';
import { ProfilingView } from './components/ProfilingView';
import { VPlusMonitoringView } from './components/VPlusMonitoringView';
import { OtpProcessorView } from './components/OtpProcessorView';
import { AlertsView } from './components/AlertsView';
import { ChatView } from './components/ChatView';
import { SettingsView } from './components/SettingsView';
import { UsersView } from './components/UsersView';
import { ControlCenterView } from './components/ControlCenterView';
import { ParserProfile, LogStats } from './types';
import { api } from './api';

function AppShell() {
  const { user, logout } = useAuth();
  const [activeTab, setActiveTab] = useState<TabType>('explore');
  const [profiles, setProfiles] = useState<ParserProfile[]>([]);
  const [stats, setStats] = useState<LogStats | null>(null);
  const [ollamaAvailable, setOllamaAvailable] = useState(false);

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

  const handleLoadSamples = async () => {
    try {
      await api.post('/api/logs/ingest-sample');
      fetchStats();
    } catch (err) {
      console.error('Failed to reload sample logs:', err);
    }
  };

  const handleClearLogs = async () => {
    if (!window.confirm('This permanently deletes every ingested log event. Continue?')) return;
    try {
      await api.delete('/api/logs/clear');
      fetchStats();
    } catch (err) {
      console.error('Failed to clear logs:', err);
    }
  };

  if (!user) return null; // AppRoot below guarantees user is set before rendering this

  const criticalCount = (stats?.severity_counts?.CRITICAL || 0) + (stats?.severity_counts?.ERROR || 0);

  return (
    <div className="min-h-screen bg-slate-100 flex text-slate-900 font-sans antialiased">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        criticalCount={criticalCount}
        user={user}
        ollamaAvailable={ollamaAvailable}
        onLogout={logout}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <Navbar
          onLoadSamples={handleLoadSamples}
          onClearLogs={handleClearLogs}
          totalLogs={Object.values(stats?.severity_counts || {}).reduce((a: number, b: number) => a + b, 0)}
          ollamaAvailable={ollamaAvailable}
          canClear={user.role === 'admin'}
        />

        <main className="flex-1 p-6 max-w-7xl w-full mx-auto space-y-6">
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
            <ExploreView sources={distinctSources} components={distinctComponents} onRefreshStats={fetchStats} />
          )}

          {activeTab === 'timeline' && <TimelineView />}

          {activeTab === 'stats' && <StatsView />}

          {activeTab === 'profiling' && <ProfilingView />}

          {activeTab === 'vplus' && <VPlusMonitoringView />}

          {activeTab === 'otp-processor' && <OtpProcessorView />}

          {activeTab === 'alerts' && <AlertsView />}

          {activeTab === 'chat' && <ChatView ollamaAvailable={ollamaAvailable} />}

          {activeTab === 'settings' && (
            <SettingsView profiles={profiles} onRefreshProfiles={fetchProfiles} ollamaAvailable={ollamaAvailable} isAdmin={user.role === 'admin'} />
          )}

          {activeTab === 'users' && user.role === 'admin' && <UsersView currentUserId={user.user_id} />}

          {activeTab === 'control-center' && user.role === 'admin' && <ControlCenterView />}
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
    <AuthProvider>
      <AppRoot />
    </AuthProvider>
  );
}
