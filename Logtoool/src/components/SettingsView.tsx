import React, { useState, useEffect } from 'react';
import { Settings, CheckCircle2, AlertTriangle, Play, Save, Cpu, Terminal, RefreshCw, Server, Zap, Lock } from 'lucide-react';
import { ParserProfile, ProfileType } from '../types';
import { api, ApiError } from '../api';

interface SettingsViewProps {
  profiles: ParserProfile[];
  onRefreshProfiles: () => void;
  ollamaAvailable: boolean;
  isAdmin: boolean;
}

export const SettingsView: React.FC<SettingsViewProps> = ({ profiles, onRefreshProfiles, ollamaAvailable, isAdmin }) => {
  const [activeTab, setActiveTab] = useState<'manager' | 'builder' | 'ai'>('ai');

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

  useEffect(() => {
    api
      .get<{ ollama_url: string; ollama_model: string }>('/api/ai/config')
      .then(setAiConfig)
      .catch((err) => console.error('Failed to load AI config:', err));
  }, []);

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

  return (
    <div className="space-y-6">
      <div className="bg-surface p-6 rounded-2xl border border-surface-border shadow-2xs space-y-4 card-brand-glow">
        <h2 className="text-xl font-bold text-text flex items-center gap-2">
          <Settings className="w-5 h-5 text-brand" />
          Settings
        </h2>
        <p className="text-xs text-text-muted">Local Ollama AI configuration and parser profile management.</p>

        <div className="flex items-center gap-2 border-b border-surface-border pt-2">
          <button
            onClick={() => setActiveTab('ai')}
            className={`px-4 py-2 font-bold text-xs border-b-2 transition-all cursor-pointer flex items-center gap-1.5 ${
              activeTab === 'ai' ? 'border-brand text-brand' : 'border-transparent text-text-muted hover:text-text'
            }`}
          >
            <Cpu className="w-3.5 h-3.5 text-brand" />
            <span>Ollama & AI</span>
          </button>
          <button
            onClick={() => setActiveTab('manager')}
            className={`px-4 py-2 font-bold text-xs border-b-2 transition-all cursor-pointer ${
              activeTab === 'manager' ? 'border-brand text-brand' : 'border-transparent text-text-muted hover:text-text'
            }`}
          >
            Active Profiles ({profiles.length})
          </button>
          {isAdmin && (
            <button
              onClick={() => setActiveTab('builder')}
              className={`px-4 py-2 font-bold text-xs border-b-2 transition-all cursor-pointer ${
                activeTab === 'builder' ? 'border-brand text-brand' : 'border-transparent text-text-muted hover:text-text'
              }`}
            >
              Profile Builder
            </button>
          )}
        </div>

        {activeTab === 'ai' && (
          <div className="space-y-6 pt-2">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-7 bg-sidebar text-white p-5 rounded-2xl border border-sidebar-border space-y-4">
                <div className="flex items-center justify-between border-b border-sidebar-border pb-3">
                  <div className="flex items-center gap-2">
                    <Server className="w-4 h-4 text-success" />
                    <h3 className="text-sm font-extrabold text-white tracking-wide uppercase">Ollama Configuration</h3>
                  </div>
                  <span className="text-[10px] bg-success/10 text-success border border-success/30 px-2 py-0.5 rounded font-mono font-bold flex items-center gap-1">
                    <Lock className="w-3 h-3" /> Local-only, no cloud fallback
                  </span>
                </div>

                <div className="space-y-3 text-xs">
                  <p className="text-white/50">
                    This app talks to Ollama only -- there's no cloud AI fallback, so nothing in this section
                    is user-editable from the browser. Set <code className="text-warning">OLLAMA_URL</code> and{' '}
                    <code className="text-warning">OLLAMA_MODEL</code> as server environment variables to change these.
                  </p>

                  <div>
                    <label className="block font-bold text-white/80 mb-1">Ollama Host URL</label>
                    <div className="w-full bg-sidebar border border-sidebar-border rounded-lg p-2.5 font-mono text-aquamarine font-semibold">
                      {aiConfig?.ollama_url ?? '…'}
                    </div>
                  </div>

                  <div>
                    <label className="block font-bold text-white/80 mb-1">Model</label>
                    <div className="w-full bg-sidebar border border-sidebar-border rounded-lg p-2.5 font-mono text-visionary font-semibold">
                      {aiConfig?.ollama_model ?? '…'}
                    </div>
                  </div>

                  <div className="flex items-center gap-3 pt-2">
                    <button
                      onClick={handleTestOllama}
                      disabled={testingOllama}
                      className="px-4 py-2 bg-white/10 hover:bg-white/15 border border-sidebar-border text-white/80 font-bold text-xs rounded-lg transition-all flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                    >
                      {testingOllama ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5 text-warning" />}
                      Test Ollama Connection
                    </button>
                    <span className={`text-xs font-bold ${ollamaAvailable ? 'text-success' : 'text-text-muted'}`}>
                      {ollamaAvailable ? '● Online' : '○ Offline'}
                    </span>
                  </div>

                  {ollamaTestResult && (
                    <div
                      className={`p-3 rounded-lg border text-xs font-mono space-y-1 ${
                        ollamaTestResult.available
                          ? 'bg-success/10 border-success/30 text-success'
                          : 'bg-error/10 border-error/30 text-error'
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

              <div className="lg:col-span-5 bg-surface-alt p-5 rounded-2xl border border-surface-border space-y-3">
                <h3 className="text-xs font-extrabold text-text uppercase tracking-wider flex items-center gap-2">
                  <Terminal className="w-4 h-4 text-brand" />
                  How to run Ollama locally
                </h3>
                <ol className="space-y-3 text-xs text-text-secondary list-decimal list-inside font-medium">
                  <li className="bg-surface p-2.5 rounded-lg border border-surface-border">
                    <strong className="text-text block font-bold mb-0.5">1. Install Ollama</strong>
                    Download from <a href="https://ollama.com" target="_blank" rel="noreferrer" className="text-brand underline">ollama.com</a>.
                  </li>
                  <li className="bg-surface p-2.5 rounded-lg border border-surface-border">
                    <strong className="text-text block font-bold mb-0.5">2. Pull the model this app expects</strong>
                    <pre className="bg-sidebar text-aquamarine p-2 rounded mt-1 text-[11px] font-mono">
                      ollama pull {aiConfig?.ollama_model ?? 'llama3.1:8b-instruct-q4_K_M'}
                    </pre>
                  </li>
                  <li className="bg-surface p-2.5 rounded-lg border border-surface-border">
                    <strong className="text-text block font-bold mb-0.5">3. Set the backend's env vars</strong>
                    <code className="bg-surface-alt px-1 py-0.5 rounded font-mono">OLLAMA_URL</code>,{' '}
                    <code className="bg-surface-alt px-1 py-0.5 rounded font-mono">OLLAMA_MODEL</code> -- see <code className="bg-surface-alt px-1 py-0.5 rounded font-mono">.env.example</code>.
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
                <div key={p.name} className="bg-surface-alt p-4 rounded-xl border border-surface-border space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-xs text-text">{p.name}</span>
                    <span className="bg-brand/[0.08] text-brand text-[10px] font-bold px-2 py-0.5 rounded border border-brand/15 uppercase">
                      {p.type}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono bg-surface p-2.5 rounded border border-surface-border text-text-secondary break-all">
                    {p.pattern}
                  </div>
                  <div className="flex items-center gap-4 text-[10px] text-text-muted font-medium flex-wrap">
                    <span>TS: <strong className="text-text">{p.timestamp_field}</strong></span>
                    <span>LVL: <strong className="text-text">{p.level_field}</strong></span>
                    <span>MSG: <strong className="text-text">{p.message_field}</strong></span>
                    <span>Min match: <strong className="text-text">{(p.min_match_ratio * 100).toFixed(0)}%</strong></span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'builder' && isAdmin && (
          <div className="space-y-6 pt-2">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-3 bg-surface-alt p-5 rounded-xl border border-surface-border">
                <h3 className="text-xs font-bold text-text uppercase tracking-wider mb-2">Profile Attributes</h3>

                <div>
                  <label className="block text-[11px] font-bold text-text-secondary mb-1">Profile Name</label>
                  <input type="text" value={pName} onChange={(e) => setPName(e.target.value)} className="w-full text-xs bg-surface border border-surface-border rounded p-2 text-text font-medium input-brand" />
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-text-secondary mb-1">Profile Type</label>
                  <select value={pType} onChange={(e) => setPType(e.target.value as ProfileType)} className="w-full text-xs bg-surface border border-surface-border rounded p-2 text-text font-medium input-brand">
                    <option value="regex">Regex Named Groups</option>
                    <option value="json">Structured JSON</option>
                    <option value="delimited">Delimited (CSV/TSV)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-text-secondary mb-1">
                    {pType === 'delimited' ? 'Delimiter character' : 'Pattern / RegEx'}
                  </label>
                  <textarea rows={3} value={pPattern} onChange={(e) => setPPattern(e.target.value)} className="w-full text-xs bg-surface border border-surface-border rounded p-2 font-mono text-text input-brand" />
                </div>

                <div className="grid grid-cols-2 gap-2 pt-1">
                  <div>
                    <label className="block text-[10px] font-bold text-text-muted">TS Field</label>
                    <input type="text" value={pTsField} onChange={(e) => setPTsField(e.target.value)} className="w-full text-xs bg-surface border border-surface-border rounded p-1.5 font-mono input-brand" />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-text-muted">LVL Field</label>
                    <input type="text" value={pLvlField} onChange={(e) => setPLvlField(e.target.value)} className="w-full text-xs bg-surface border border-surface-border rounded p-1.5 font-mono input-brand" />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-text-muted">MSG Field</label>
                    <input type="text" value={pMsgField} onChange={(e) => setPMsgField(e.target.value)} className="w-full text-xs bg-surface border border-surface-border rounded p-1.5 font-mono input-brand" />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-text-muted">Component Field</label>
                    <input type="text" value={pComponentField} onChange={(e) => setPComponentField(e.target.value)} className="w-full text-xs bg-surface border border-surface-border rounded p-1.5 font-mono input-brand" />
                  </div>
                </div>
              </div>

              <div className="space-y-4 bg-sidebar p-5 rounded-xl text-white">
                <h3 className="text-xs font-bold text-warning uppercase tracking-wider">Live Pattern Sandbox (client-side preview only)</h3>

                <div>
                  <label className="block text-[11px] font-bold text-white/80 mb-1">Test Log Sample Line</label>
                  <textarea rows={2} value={testLine} onChange={(e) => setTestLine(e.target.value)} className="w-full text-xs bg-sidebar border border-sidebar-border rounded p-2 font-mono text-white/80" />
                </div>

                <div className="flex items-center gap-3">
                  <button onClick={handleTestRegex} className="flex items-center gap-1.5 px-4 py-2 bg-brand hover:bg-brand-hover text-white font-bold text-xs rounded-lg transition-all cursor-pointer shadow-md shadow-brand/20">
                    <Play className="w-3.5 h-3.5" />
                    <span>Run Test Match</span>
                  </button>
                  <button onClick={handleSaveProfile} className="flex items-center gap-1.5 px-4 py-2 bg-success hover:bg-success/90 text-white font-bold text-xs rounded-lg transition-all cursor-pointer shadow-md shadow-success/20">
                    <Save className="w-3.5 h-3.5" />
                    <span>Save New Profile</span>
                  </button>
                </div>

                {testError && <div className="p-3 bg-error/10 border border-error/30 rounded text-xs text-error">{testError}</div>}

                {testResult && (
                  <div className="space-y-1">
                    <div className="text-[10px] font-bold text-success uppercase tracking-wider">Extracted Groups</div>
                    <pre className="bg-sidebar p-3 rounded border border-sidebar-border text-[11px] font-mono text-aquamarine max-h-40 overflow-y-auto">
                      {JSON.stringify(testResult, null, 2)}
                    </pre>
                  </div>
                )}

                {saveStatus && <div className="p-2.5 bg-success-light border border-success/30 rounded text-xs text-success font-semibold">{saveStatus}</div>}
                {saveError && <div className="p-2.5 bg-error-light border border-error/30 rounded text-xs text-error font-semibold">{saveError}</div>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
