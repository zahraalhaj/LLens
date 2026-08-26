import React, { useState, useEffect } from 'react';
import { Settings, CheckCircle2, AlertTriangle, Play, Save, Cpu, Terminal, RefreshCw, Server, Zap, Lock, Mail, Bell } from 'lucide-react';
import { ParserProfile, ProfileType } from '../types';
import { api, ApiError } from '../api';

interface SettingsViewProps {
  profiles: ParserProfile[];
  onRefreshProfiles: () => void;
  ollamaAvailable: boolean;
  isAdmin: boolean;
}

interface SmtpConfig {
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password_set: boolean;
  alert_email_to: string;
}

export const SettingsView: React.FC<SettingsViewProps> = ({ profiles, onRefreshProfiles, ollamaAvailable, isAdmin }) => {
  const [activeTab, setActiveTab] = useState<'manager' | 'builder' | 'ai' | 'notifications'>('ai');

  // Profile Builder State
  const [pName, setPName] = useState<string>('Custom Log Format');
  const [pType, setPType] = useState<ProfileType>('regex');
  const [pPattern, setPPattern] = useState<string>(
    '^(?P<timestamp>\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})\\s+\\[(?P<level>\\w+)\\]\\s+(?P<message>.*)$'
  );
  const [pTsField, setPTsField] = useState<string>('timestamp');
  const [pLvlField, setPLvlField] = useState<string>('level');
  const [pMsgField, setPMsgField] = useState<string>('message');
  const [pComponentField, setPComponentField] = useState<string>('component');

  const [testLine, setTestLine] = useState<string>('2026-08-05 20:17:33 [ERROR] Database connection pool closed unexpectedly');
  const [testResult, setTestResult] = useState<any | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  // AI config -- read-only display of what the server is actually configured
  // with (env vars). There is no runtime provider toggle: this app is
  // Ollama-only by design (sensitive log data stays on the local network),
  // so there's nothing to switch and nothing here should imply otherwise.
  const [aiConfig, setAiConfig] = useState<{ ollama_url: string; ollama_model: string } | null>(null);
  const [ollamaTestResult, setOllamaTestResult] = useState<{ available: boolean; status: string } | null>(null);
  const [testingOllama, setTestingOllama] = useState<boolean>(false);

  // SMTP / Notification config
  const [smtpConfig, setSmtpConfig] = useState<SmtpConfig | null>(null);
  const [smtpForm, setSmtpForm] = useState({ smtp_host: '', smtp_port: 587, smtp_user: '', smtp_password: '', alert_email_to: '' });
  const [smtpSaving, setSmtpSaving] = useState<boolean>(false);
  const [smtpStatus, setSmtpStatus] = useState<string | null>(null);
  const [smtpError, setSmtpError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ ollama_url: string; ollama_model: string }>('/api/ai/config')
      .then(setAiConfig)
      .catch((err) => console.error('Failed to load AI config:', err));
    if (isAdmin) {
      api
        .get<SmtpConfig>('/api/settings/smtp')
        .then((cfg) => {
          setSmtpConfig(cfg);
          setSmtpForm({ smtp_host: cfg.smtp_host, smtp_port: cfg.smtp_port, smtp_user: cfg.smtp_user, smtp_password: '', alert_email_to: cfg.alert_email_to });
        })
        .catch((err) => console.error('Failed to load SMTP config:', err));
    }
  }, [isAdmin]);

  const handleTestOllama = async () => {
    setTestingOllama(true);
    setOllamaTestResult(null);
    try {
      const data = await api.get<{ available: boolean; status: string }>('/api/ai/ollama/health');
      setOllamaTestResult(data);
    } catch (err) {
      setOllamaTestResult({ available: false, status: err instanceof ApiError ? err.detail : 'Connection test failed' });
    } finally {
      setTestingOllama(false);
    }
  };

  const handleTestRegex = () => {
    setTestResult(null);
    setTestError(null);
    try {
      if (pType === 'json') {
        setTestResult(JSON.parse(testLine));
      } else {
        const reg = new RegExp(pPattern);
        const match = reg.exec(testLine);
        if (match?.groups) setTestResult(match.groups);
        else setTestError('Pattern did not match the test line.');
      }
    } catch (err: any) {
      setTestError(`Parser expression error: ${err.message}`);
    }
  };

  const handleSaveProfile = async () => {
    setSaveStatus(null);
    setSaveError(null);
    try {
      const newProf: Partial<ParserProfile> = {
        name: pName,
        type: pType,
        pattern: pPattern,
        timestamp_field: pTsField,
        level_field: pLvlField,
        message_field: pMsgField,
        component_field: pComponentField || null,
      };
      await api.post('/api/profiles', newProf);
      setSaveStatus(`Profile '${pName}' saved.`);
      onRefreshProfiles();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.detail : 'Failed to save profile');
    }
  };

  const handleSaveSmtp = async () => {
    setSmtpSaving(true);
    setSmtpStatus(null);
    setSmtpError(null);
    try {
      await api.put('/api/settings/smtp', smtpForm);
      setSmtpStatus('SMTP settings saved. Restart the server for changes to take effect.');
      const cfg = await api.get<SmtpConfig>('/api/settings/smtp');
      setSmtpConfig(cfg);
    } catch (err) {
      setSmtpError(err instanceof ApiError ? err.detail : 'Failed to save SMTP settings');
    } finally {
      setSmtpSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs space-y-4">
        <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <Settings className="w-5 h-5 text-blue-600" />
          Settings
        </h2>
        <p className="text-xs text-slate-500">Local Ollama AI configuration and parser profile management.</p>

        <div className="flex items-center gap-2 border-b border-slate-200 pt-2">
          <button
            onClick={() => setActiveTab('ai')}
            className={`px-4 py-2 font-bold text-xs border-b-2 transition-all cursor-pointer flex items-center gap-1.5 ${
              activeTab === 'ai' ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-800'
            }`}
          >
            <Cpu className="w-3.5 h-3.5 text-blue-600" />
            <span>Ollama & AI</span>
          </button>
          <button
            onClick={() => setActiveTab('manager')}
            className={`px-4 py-2 font-bold text-xs border-b-2 transition-all cursor-pointer ${
              activeTab === 'manager' ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-800'
            }`}
          >
            Active Profiles ({profiles.length})
          </button>
          {isAdmin && (
            <button
              onClick={() => setActiveTab('builder')}
              className={`px-4 py-2 font-bold text-xs border-b-2 transition-all cursor-pointer ${
                activeTab === 'builder' ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              Profile Builder
            </button>
          )}
          {isAdmin && (
            <button
              onClick={() => setActiveTab('notifications')}
              className={`px-4 py-2 font-bold text-xs border-b-2 transition-all cursor-pointer flex items-center gap-1.5 ${
                activeTab === 'notifications' ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              <Bell className="w-3.5 h-3.5" />
              <span>Notifications</span>
            </button>
          )}
        </div>

        {activeTab === 'ai' && (
          <div className="space-y-6 pt-2">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-7 bg-slate-900 text-white p-5 rounded-2xl border border-slate-800 space-y-4">
                <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                  <div className="flex items-center gap-2">
                    <Server className="w-4 h-4 text-emerald-400" />
                    <h3 className="text-sm font-extrabold text-white tracking-wide uppercase">Ollama Configuration</h3>
                  </div>
                  <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded font-mono font-bold flex items-center gap-1">
                    <Lock className="w-3 h-3" /> Local-only, no cloud fallback
                  </span>
                </div>

                <div className="space-y-3 text-xs">
                  <p className="text-slate-400">
                    This app talks to Ollama only -- there's no cloud AI fallback, so nothing in this section
                    is user-editable from the browser. Set <code className="text-amber-300">OLLAMA_URL</code> and{' '}
                    <code className="text-amber-300">OLLAMA_MODEL</code> as server environment variables to change these.
                  </p>

                  <div>
                    <label className="block font-bold text-slate-300 mb-1">Ollama Host URL</label>
                    <div className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 font-mono text-emerald-300 font-semibold">
                      {aiConfig?.ollama_url ?? '…'}
                    </div>
                  </div>

                  <div>
                    <label className="block font-bold text-slate-300 mb-1">Model</label>
                    <div className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 font-mono text-blue-300 font-semibold">
                      {aiConfig?.ollama_model ?? '…'}
                    </div>
                  </div>

                  <div className="flex items-center gap-3 pt-2">
                    <button
                      onClick={handleTestOllama}
                      disabled={testingOllama}
                      className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-bold text-xs rounded-lg transition-all flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                    >
                      {testingOllama ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5 text-amber-400" />}
                      Test Ollama Connection
                    </button>
                    <span className={`text-xs font-bold ${ollamaAvailable ? 'text-emerald-400' : 'text-slate-500'}`}>
                      {ollamaAvailable ? '● Online' : '○ Offline'}
                    </span>
                  </div>

                  {ollamaTestResult && (
                    <div
                      className={`p-3 rounded-lg border text-xs font-mono space-y-1 ${
                        ollamaTestResult.available
                          ? 'bg-emerald-950/80 border-emerald-800 text-emerald-300'
                          : 'bg-rose-950/80 border-rose-800 text-rose-300'
                      }`}
                    >
                      <div className="font-bold flex items-center gap-2">
                        {ollamaTestResult.available ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
                        {ollamaTestResult.status}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="lg:col-span-5 bg-slate-50 p-5 rounded-2xl border border-slate-200 space-y-3">
                <h3 className="text-xs font-extrabold text-slate-800 uppercase tracking-wider flex items-center gap-2">
                  <Terminal className="w-4 h-4 text-blue-600" />
                  How to run Ollama locally
                </h3>
                <ol className="space-y-3 text-xs text-slate-700 list-decimal list-inside font-medium">
                  <li className="bg-white p-2.5 rounded-lg border border-slate-200">
                    <strong className="text-slate-900 block font-bold mb-0.5">1. Install Ollama</strong>
                    Download from <a href="https://ollama.com" target="_blank" rel="noreferrer" className="text-blue-600 underline">ollama.com</a>.
                  </li>
                  <li className="bg-white p-2.5 rounded-lg border border-slate-200">
                    <strong className="text-slate-900 block font-bold mb-0.5">2. Pull the model this app expects</strong>
                    <pre className="bg-slate-900 text-emerald-400 p-2 rounded mt-1 text-[11px] font-mono">
                      ollama pull {aiConfig?.ollama_model ?? 'llama3.1:8b-instruct-q4_K_M'}
                    </pre>
                  </li>
                  <li className="bg-white p-2.5 rounded-lg border border-slate-200">
                    <strong className="text-slate-900 block font-bold mb-0.5">3. Set the backend's env vars</strong>
                    <code className="bg-slate-100 px-1 py-0.5 rounded font-mono">OLLAMA_URL</code>,{' '}
                    <code className="bg-slate-100 px-1 py-0.5 rounded font-mono">OLLAMA_MODEL</code> -- see <code className="bg-slate-100 px-1 py-0.5 rounded font-mono">.env.example</code>.
                  </li>
                </ol>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'manager' && (
          <div className="space-y-4 pt-2">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {profiles.map((p) => (
                <div key={p.name} className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-xs text-slate-900">{p.name}</span>
                    <span className="bg-blue-100 text-blue-700 text-[10px] font-bold px-2 py-0.5 rounded border border-blue-200 uppercase">
                      {p.type}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono bg-white p-2.5 rounded border border-slate-200 text-slate-700 break-all">
                    {p.pattern}
                  </div>
                  <div className="flex items-center gap-4 text-[10px] text-slate-500 font-medium flex-wrap">
                    <span>TS: <strong className="text-slate-800">{p.timestamp_field}</strong></span>
                    <span>LVL: <strong className="text-slate-800">{p.level_field}</strong></span>
                    <span>MSG: <strong className="text-slate-800">{p.message_field}</strong></span>
                    <span>Min match: <strong className="text-slate-800">{(p.min_match_ratio * 100).toFixed(0)}%</strong></span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'builder' && isAdmin && (
          <div className="space-y-6 pt-2">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-3 bg-slate-50 p-5 rounded-xl border border-slate-200">
                <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2">Profile Attributes</h3>

                <div>
                  <label className="block text-[11px] font-bold text-slate-600 mb-1">Profile Name</label>
                  <input type="text" value={pName} onChange={(e) => setPName(e.target.value)} className="w-full text-xs bg-white border border-slate-300 rounded p-2 text-slate-900 font-medium" />
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-slate-600 mb-1">Profile Type</label>
                  <select value={pType} onChange={(e) => setPType(e.target.value as ProfileType)} className="w-full text-xs bg-white border border-slate-300 rounded p-2 text-slate-900 font-medium">
                    <option value="regex">Regex Named Groups</option>
                    <option value="json">Structured JSON</option>
                    <option value="delimited">Delimited (CSV/TSV)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-slate-600 mb-1">
                    {pType === 'delimited' ? 'Delimiter character' : 'Pattern / RegEx'}
                  </label>
                  <textarea rows={3} value={pPattern} onChange={(e) => setPPattern(e.target.value)} className="w-full text-xs bg-white border border-slate-300 rounded p-2 font-mono text-slate-900" />
                </div>

                <div className="grid grid-cols-2 gap-2 pt-1">
                  <div>
                    <label className="block text-[10px] font-bold text-slate-500">TS Field</label>
                    <input type="text" value={pTsField} onChange={(e) => setPTsField(e.target.value)} className="w-full text-xs bg-white border border-slate-300 rounded p-1.5 font-mono" />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-slate-500">LVL Field</label>
                    <input type="text" value={pLvlField} onChange={(e) => setPLvlField(e.target.value)} className="w-full text-xs bg-white border border-slate-300 rounded p-1.5 font-mono" />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-slate-500">MSG Field</label>
                    <input type="text" value={pMsgField} onChange={(e) => setPMsgField(e.target.value)} className="w-full text-xs bg-white border border-slate-300 rounded p-1.5 font-mono" />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-slate-500">Component Field</label>
                    <input type="text" value={pComponentField} onChange={(e) => setPComponentField(e.target.value)} className="w-full text-xs bg-white border border-slate-300 rounded p-1.5 font-mono" />
                  </div>
                </div>
              </div>

              <div className="space-y-4 bg-slate-900 p-5 rounded-xl text-white">
                <h3 className="text-xs font-bold text-amber-400 uppercase tracking-wider">Live Pattern Sandbox (client-side preview only)</h3>

                <div>
                  <label className="block text-[11px] font-bold text-slate-300 mb-1">Test Log Sample Line</label>
                  <textarea rows={2} value={testLine} onChange={(e) => setTestLine(e.target.value)} className="w-full text-xs bg-slate-950 border border-slate-800 rounded p-2 font-mono text-slate-200" />
                </div>

                <div className="flex items-center gap-3">
                  <button onClick={handleTestRegex} className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs rounded transition-all cursor-pointer shadow-2xs">
                    <Play className="w-3.5 h-3.5" />
                    <span>Run Test Match</span>
                  </button>
                  <button onClick={handleSaveProfile} className="flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs rounded transition-all cursor-pointer shadow-2xs">
                    <Save className="w-3.5 h-3.5" />
                    <span>Save New Profile</span>
                  </button>
                </div>

                {testError && <div className="p-3 bg-rose-950/80 border border-rose-800 rounded text-xs text-rose-300">{testError}</div>}

                {testResult && (
                  <div className="space-y-1">
                    <div className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider">Extracted Groups</div>
                    <pre className="bg-slate-950 p-3 rounded border border-slate-800 text-[11px] font-mono text-emerald-300 max-h-40 overflow-y-auto">
                      {JSON.stringify(testResult, null, 2)}
                    </pre>
                  </div>
                )}

                {saveStatus && <div className="p-2.5 bg-emerald-950 border border-emerald-800 rounded text-xs text-emerald-400 font-semibold">{saveStatus}</div>}
                {saveError && <div className="p-2.5 bg-rose-950 border border-rose-800 rounded text-xs text-rose-300 font-semibold">{saveError}</div>}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'notifications' && isAdmin && (
          <div className="space-y-6 pt-2">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-7 bg-slate-900 text-white p-5 rounded-2xl border border-slate-800 space-y-4">
                <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                  <div className="flex items-center gap-2">
                    <Mail className="w-4 h-4 text-blue-400" />
                    <h3 className="text-sm font-extrabold text-white tracking-wide uppercase">SMTP Configuration</h3>
                  </div>
                  <span className="text-[10px] bg-blue-500/10 text-blue-400 border border-blue-500/30 px-2 py-0.5 rounded font-mono font-bold flex items-center gap-1">
                    <Bell className="w-3 h-3" /> Alert Notifications
                  </span>
                </div>

                <div className="space-y-3 text-xs">
                  <p className="text-slate-400">
                    Configure the SMTP server used to send alert notifications. Alert rules dispatch emails when
                    log events match their criteria. Leave host as <code className="text-amber-300">localhost</code> to simulate sends without a real server.
                  </p>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block font-bold text-slate-300 mb-1">SMTP Host</label>
                      <input
                        type="text"
                        value={smtpForm.smtp_host}
                        onChange={(e) => setSmtpForm({ ...smtpForm, smtp_host: e.target.value })}
                        className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 font-mono text-slate-200 text-xs"
                        placeholder="localhost"
                      />
                    </div>
                    <div>
                      <label className="block font-bold text-slate-300 mb-1">SMTP Port</label>
                      <input
                        type="number"
                        value={smtpForm.smtp_port}
                        onChange={(e) => setSmtpForm({ ...smtpForm, smtp_port: parseInt(e.target.value) || 587 })}
                        className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 font-mono text-slate-200 text-xs"
                        placeholder="587"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block font-bold text-slate-300 mb-1">SMTP Username</label>
                    <input
                      type="text"
                      value={smtpForm.smtp_user}
                      onChange={(e) => setSmtpForm({ ...smtpForm, smtp_user: e.target.value })}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 font-mono text-slate-200 text-xs"
                      placeholder="Empty for no auth"
                    />
                  </div>

                  <div>
                    <label className="block font-bold text-slate-300 mb-1">
                      SMTP Password {smtpConfig?.smtp_password_set && <span className="text-emerald-400">(set)</span>}
                    </label>
                    <input
                      type="password"
                      value={smtpForm.smtp_password}
                      onChange={(e) => setSmtpForm({ ...smtpForm, smtp_password: e.target.value })}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 font-mono text-slate-200 text-xs"
                      placeholder={smtpConfig?.smtp_password_set ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : 'Leave empty to keep current'}
                    />
                  </div>

                  <div>
                    <label className="block font-bold text-slate-300 mb-1">Default Recipient Email</label>
                    <input
                      type="email"
                      value={smtpForm.alert_email_to}
                      onChange={(e) => setSmtpForm({ ...smtpForm, alert_email_to: e.target.value })}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 font-mono text-emerald-300 text-xs"
                      placeholder="admin@example.com"
                    />
                    <p className="text-[10px] text-slate-500 mt-1">Used when a rule has no specific recipients</p>
                  </div>

                  <div className="flex items-center gap-3 pt-2">
                    <button
                      onClick={handleSaveSmtp}
                      disabled={smtpSaving}
                      className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs rounded-lg transition-all flex items-center gap-1.5 cursor-pointer disabled:opacity-50 shadow-2xs"
                    >
                      {smtpSaving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                      Save SMTP Settings
                    </button>
                  </div>

                  {smtpStatus && (
                    <div className="p-3 rounded-lg border text-xs font-mono bg-emerald-950/80 border-emerald-800 text-emerald-300">
                      <div className="font-bold flex items-center gap-2">
                        <CheckCircle2 className="w-4 h-4" />
                        {smtpStatus}
                      </div>
                    </div>
                  )}
                  {smtpError && (
                    <div className="p-3 rounded-lg border text-xs font-mono bg-rose-950/80 border-rose-800 text-rose-300">
                      <div className="font-bold flex items-center gap-2">
                        <AlertTriangle className="w-4 h-4" />
                        {smtpError}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="lg:col-span-5 bg-slate-50 p-5 rounded-2xl border border-slate-200 space-y-3">
                <h3 className="text-xs font-extrabold text-slate-800 uppercase tracking-wider flex items-center gap-2">
                  <Bell className="w-4 h-4 text-blue-600" />
                  How Alert Notifications Work
                </h3>
                <ol className="space-y-3 text-xs text-slate-700 list-decimal list-inside font-medium">
                  <li className="bg-white p-2.5 rounded-lg border border-slate-200">
                    <strong className="text-slate-900 block font-bold mb-0.5">1. Create Alert Rules</strong>
                    Go to the <strong className="text-blue-600">Alerts</strong> page and create rules with level thresholds, source filters, and delivery mode (immediate or digest).
                  </li>
                  <li className="bg-white p-2.5 rounded-lg border border-slate-200">
                    <strong className="text-slate-900 block font-bold mb-0.5">2. Ingest Logs</strong>
                    Upload log files or load samples. Each batch is automatically evaluated against all enabled rules.
                  </li>
                  <li className="bg-white p-2.5 rounded-lg border border-slate-200">
                    <strong className="text-slate-900 block font-bold mb-0.5">3. Emails Dispatched</strong>
                    Matching events trigger email notifications to the rule's recipients (or the default address above). Deduplication prevents alert storms.
                  </li>
                </ol>
                <div className="bg-white p-2.5 rounded-lg border border-slate-200 text-[11px] text-slate-500">
                  <strong className="text-slate-800">Tip:</strong> Set SMTP host to <code className="text-blue-600">localhost</code> to test without a real mail server — sends are simulated and logged.
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
