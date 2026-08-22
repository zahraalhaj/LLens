import React, { useEffect, useState } from 'react';
import {
  ServerCog, Plus, Trash2, Zap, RefreshCw, CheckCircle2, XCircle,
  Clock, KeyRound, Lock, ChevronDown, ChevronUp, ShieldCheck, PauseCircle, PlayCircle,
} from 'lucide-react';
import { api, ApiError } from '../api';
import { MachineAuthType, PollResult, RemoteMachine } from '../types';
import { useConfirm } from './ConfirmDialog';

const emptyForm = {
  label: '',
  host: '',
  port: 22,
  username: '',
  auth_type: 'password' as MachineAuthType,
  secret: '',
  remote_directory: '',
  recursive: true,
  poll_interval_minutes: 15,
};

export const ControlCenterView: React.FC = () => {
  const confirm = useConfirm();
  const [machines, setMachines] = useState<RemoteMachine[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [busyMachineId, setBusyMachineId] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<Record<string, { type: 'test' | 'poll'; success: boolean; message: string; detail?: PollResult }>>({});
  const [expandedMachineId, setExpandedMachineId] = useState<string | null>(null);

  const loadMachines = async () => {
    try {
      const data = await api.get<RemoteMachine[]>('/api/machines');
      setMachines(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load machines');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMachines();
    const interval = setInterval(loadMachines, 30_000); // background scheduler polls independently -- keep status fresh
    return () => clearInterval(interval);
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    setCreating(true);
    try {
      await api.post('/api/machines', form);
      setForm(emptyForm);
      setShowForm(false);
      await loadMachines();
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.detail : 'Failed to create machine');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (m: RemoteMachine) => {
    const confirmed = await confirm({
      title: 'Remove machine?',
      message: `Remove '${m.label}'? Its ingested logs stay in LLens -- only the connection is removed.`,
      confirmLabel: 'Remove',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      await api.delete(`/api/machines/${m.machine_id}`);
      await loadMachines();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to delete machine');
    }
  };

  const handleToggleEnabled = async (m: RemoteMachine) => {
    try {
      await api.put(`/api/machines/${m.machine_id}`, { enabled: !m.enabled });
      await loadMachines();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to update machine');
    }
  };

  const handleTestConnection = async (m: RemoteMachine) => {
    setBusyMachineId(m.machine_id);
    try {
      const result = await api.post<{ success: boolean; message: string }>(`/api/machines/${m.machine_id}/test-connection`);
      setActionResult((prev) => ({ ...prev, [m.machine_id]: { type: 'test', success: result.success, message: result.message } }));
      if (result.success) await loadMachines(); // fingerprint may have just been recorded
    } catch (err) {
      setActionResult((prev) => ({
        ...prev,
        [m.machine_id]: { type: 'test', success: false, message: err instanceof ApiError ? err.detail : 'Connection test failed' },
      }));
    } finally {
      setBusyMachineId(null);
    }
  };

  const handlePollNow = async (m: RemoteMachine) => {
    setBusyMachineId(m.machine_id);
    try {
      const result = await api.post<PollResult>(`/api/machines/${m.machine_id}/poll-now`);
      const success = result.errors.length === 0;
      setActionResult((prev) => ({
        ...prev,
        [m.machine_id]: {
          type: 'poll',
          success,
          message: `${result.files_ingested} file(s) ingested, ${result.files_unchanged} unchanged, ${result.total_events_ingested} event(s)${result.errors.length ? `, ${result.errors.length} error(s)` : ''}`,
          detail: result,
        },
      }));
      await loadMachines();
    } catch (err) {
      setActionResult((prev) => ({
        ...prev,
        [m.machine_id]: { type: 'poll', success: false, message: err instanceof ApiError ? err.detail : 'Poll failed' },
      }));
    } finally {
      setBusyMachineId(null);
    }
  };

  const timeSince = (iso: string | null) => {
    if (!iso) return 'Never';
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <ServerCog className="w-5 h-5 text-blue-600" />
            Control Center
          </h2>
          <p className="text-sm text-slate-500">
            Register remote machines LLens should pull logs from over SSH. Polled automatically on each
            machine's own interval, or trigger a pull manually below.
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg shadow-sm transition-all cursor-pointer"
        >
          <Plus className="w-4 h-4" />
          Add machine
        </button>
      </div>

      {error && (
        <div className="text-sm font-medium text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-4 py-2.5">
          {error}
        </div>
      )}

      {showForm && (
        <form onSubmit={handleCreate} className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          <div className="flex items-center gap-2 text-xs font-bold text-slate-700 uppercase tracking-wide">
            <Lock className="w-3.5 h-3.5 text-slate-400" />
            Credentials are encrypted at rest and never shown again after saving
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Label</label>
              <input
                type="text" required placeholder="prod-db-01" value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Host</label>
              <input
                type="text" required placeholder="10.0.0.5 or db01.internal" value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Port</label>
              <input
                type="number" required value={form.port}
                onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) || 22 })}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Username</label>
              <input
                type="text" required placeholder="loguser" value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Auth type</label>
              <select
                value={form.auth_type}
                onChange={(e) => setForm({ ...form, auth_type: e.target.value as MachineAuthType, secret: '' })}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="password">Password</option>
                <option value="key">SSH private key</option>
              </select>
            </div>
            <div className="md:col-span-1">
              <label className="block text-[11px] font-bold text-slate-600 mb-1">
                {form.auth_type === 'password' ? 'Password' : 'Private key (PEM)'}
              </label>
              {form.auth_type === 'password' ? (
                <input
                  type="password" required value={form.secret}
                  onChange={(e) => setForm({ ...form, secret: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              ) : (
                <textarea
                  required rows={1} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" value={form.secret}
                  onChange={(e) => setForm({ ...form, secret: e.target.value })}
                  className="w-full px-3 py-2 text-xs font-mono border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-2">
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Remote log directory</label>
              <input
                type="text" required placeholder="/var/log/myapp" value={form.remote_directory}
                onChange={(e) => setForm({ ...form, remote_directory: e.target.value })}
                className="w-full px-3 py-2 text-sm font-mono border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Poll every (minutes)</label>
              <input
                type="number" required min={1} value={form.poll_interval_minutes}
                onChange={(e) => setForm({ ...form, poll_interval_minutes: parseInt(e.target.value) || 15 })}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>

          <label className="flex items-center gap-2 text-xs font-semibold text-slate-600">
            <input
              type="checkbox" checked={form.recursive}
              onChange={(e) => setForm({ ...form, recursive: e.target.checked })}
              className="rounded border-slate-300"
            />
            Include subdirectories
          </label>

          {createError && (
            <div className="text-xs font-medium text-rose-700 bg-rose-50 border border-rose-200 rounded-md px-3 py-2">
              {createError}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="submit" disabled={creating}
              className="px-4 py-2 text-xs font-bold bg-blue-600 hover:bg-blue-500 disabled:bg-blue-300 text-white rounded-md transition-all cursor-pointer"
            >
              {creating ? 'Adding…' : 'Add machine'}
            </button>
            <button
              type="button" onClick={() => { setShowForm(false); setForm(emptyForm); setCreateError(null); }}
              className="px-4 py-2 text-xs font-bold text-slate-500 hover:text-slate-700 cursor-pointer"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-sm text-slate-400">Loading machines…</div>
      ) : machines.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-sm text-slate-400">
          No remote machines registered yet.
        </div>
      ) : (
        <div className="space-y-3">
          {machines.map((m) => {
            const result = actionResult[m.machine_id];
            const isExpanded = expandedMachineId === m.machine_id;
            const isBusy = busyMachineId === m.machine_id;
            return (
              <div key={m.machine_id} className="bg-white border border-slate-200 rounded-xl shadow-2xs overflow-hidden">
                <div className="p-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div
                      className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                        !m.enabled ? 'bg-slate-300' : m.last_status === 'success' ? 'bg-emerald-500' : m.last_status === 'error' ? 'bg-rose-500' : 'bg-slate-300'
                      }`}
                      title={!m.enabled ? 'Disabled' : m.last_status || 'Never polled'}
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-sm text-slate-900 truncate">{m.label}</span>
                        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 flex items-center gap-1">
                          {m.auth_type === 'key' ? <KeyRound className="w-2.5 h-2.5" /> : <Lock className="w-2.5 h-2.5" />}
                          {m.auth_type}
                        </span>
                        {m.host_key_fingerprint && (
                          <span title={`Host key pinned: ${m.host_key_fingerprint}`} className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600 border border-emerald-200 flex items-center gap-1">
                            <ShieldCheck className="w-2.5 h-2.5" /> pinned
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-slate-500 font-mono truncate">
                        {m.username}@{m.host}:{m.port} → {m.remote_directory}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-4 text-xs text-slate-500">
                    <div className="flex items-center gap-1.5">
                      <Clock className="w-3.5 h-3.5" />
                      <span>Every {m.poll_interval_minutes}m · last: {timeSince(m.last_polled_at)}</span>
                    </div>

                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={() => handleTestConnection(m)} disabled={isBusy} title="Test connection"
                        className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 disabled:opacity-40 cursor-pointer"
                      >
                        <Zap className={`w-4 h-4 ${isBusy ? 'animate-pulse' : ''}`} />
                      </button>
                      <button
                        onClick={() => handlePollNow(m)} disabled={isBusy} title="Pull now"
                        className="p-1.5 rounded-md text-blue-600 hover:bg-blue-50 disabled:opacity-40 cursor-pointer"
                      >
                        <RefreshCw className={`w-4 h-4 ${isBusy ? 'animate-spin' : ''}`} />
                      </button>
                      <button
                        onClick={() => handleToggleEnabled(m)} title={m.enabled ? 'Pause polling' : 'Resume polling'}
                        className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 cursor-pointer"
                      >
                        {m.enabled ? <PauseCircle className="w-4 h-4" /> : <PlayCircle className="w-4 h-4" />}
                      </button>
                      <button
                        onClick={() => handleDelete(m)} title="Remove"
                        className="p-1.5 rounded-md text-rose-500 hover:bg-rose-50 cursor-pointer"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setExpandedMachineId(isExpanded ? null : m.machine_id)}
                        className="p-1.5 rounded-md text-slate-400 hover:bg-slate-100 cursor-pointer"
                      >
                        {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>
                </div>

                {result && (
                  <div className={`mx-4 mb-3 flex items-start gap-2 text-xs font-medium rounded-lg px-3 py-2 ${result.success ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-rose-50 text-rose-700 border border-rose-200'}`}>
                    {result.success ? <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" /> : <XCircle className="w-4 h-4 shrink-0 mt-0.5" />}
                    <div>
                      <div>{result.message}</div>
                      {result.detail && result.detail.errors.length > 0 && (
                        <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
                          {result.detail.errors.map((e, i) => <li key={i}>{e}</li>)}
                        </ul>
                      )}
                    </div>
                  </div>
                )}

                {isExpanded && (
                  <div className="mx-4 mb-4 p-3 bg-slate-50 rounded-lg border border-slate-200 text-xs text-slate-600 grid grid-cols-2 gap-2">
                    <div><span className="font-bold text-slate-500">Recursive:</span> {m.recursive ? 'Yes' : 'No'}</div>
                    <div><span className="font-bold text-slate-500">Status:</span> {m.enabled ? 'Enabled' : 'Disabled'}</div>
                    <div><span className="font-bold text-slate-500">Last files ingested:</span> {m.last_files_ingested ?? '—'}</div>
                    <div><span className="font-bold text-slate-500">Registered:</span> {m.created_at?.slice(0, 10)}</div>
                    {m.last_error && (
                      <div className="col-span-2">
                        <span className="font-bold text-rose-500">Last error:</span> <span className="font-mono">{m.last_error}</span>
                      </div>
                    )}
                    {m.host_key_fingerprint && (
                      <div className="col-span-2 truncate">
                        <span className="font-bold text-slate-500">Host key fingerprint:</span> <span className="font-mono">{m.host_key_fingerprint}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
