import React, { useEffect, useState } from 'react';
import {
  BellRing, Send, Mail, CheckCircle2, XCircle, Info, History, Filter, ExternalLink,
  Users, Plus, Pencil, Trash2, Power, X,
} from 'lucide-react';
import { AlertRule, AlertDispatchHistory, DrillThroughTarget, NotificationGroup } from '../types';
import { api, ApiError } from '../api';
import { useConfirm } from './ConfirmDialog';

interface AlertsViewProps {
  onInvestigate: (target: DrillThroughTarget) => void;
  isAdmin: boolean;
}

// Same order backend/alerts/rule_manager.py's SEVERITY_ORDER uses.
const SEVERITY_ORDER = ['DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL'];

const severitiesFrom = (minLevel: string): string[] => {
  const idx = SEVERITY_ORDER.indexOf(minLevel);
  return idx === -1 ? SEVERITY_ORDER : SEVERITY_ORDER.slice(idx);
};

const emptyRuleForm = {
  name: '',
  min_level: 'ERROR',
  mode: 'immediate' as 'immediate' | 'digest',
  source_system_filter: '',
  component_filter: '',
  message_contains: '',
  dedup_window_minutes: 60,
  notification_group_id: '',
  recipients: '',
};

// -- Notification groups panel (admin-only) ---------------------------------

const NotificationGroupsPanel: React.FC<{ groups: NotificationGroup[]; onChanged: () => void }> = ({ groups, onChanged }) => {
  const confirm = useConfirm();
  const [editingId, setEditingId] = useState<string | null>(null); // null while not editing; '' means "creating new"
  const [name, setName] = useState('');
  const [emails, setEmails] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startCreate = () => {
    setEditingId('');
    setName('');
    setEmails('');
    setError(null);
  };

  const startEdit = (g: NotificationGroup) => {
    setEditingId(g.group_id);
    setName(g.name);
    setEmails(g.emails);
    setError(null);
  };

  const cancel = () => {
    setEditingId(null);
    setError(null);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      if (editingId) {
        await api.put(`/api/alerts/groups/${editingId}`, { name, emails });
      } else {
        await api.post('/api/alerts/groups', { name, emails });
      }
      setEditingId(null);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to save notification group');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (g: NotificationGroup) => {
    const confirmed = await confirm({
      title: 'Delete notification group?',
      message: `Delete "${g.name}"? Any rule assigned to it will fall back to its own recipients field (or the server default) instead.`,
      confirmLabel: 'Delete',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      await api.delete(`/api/alerts/groups/${g.group_id}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to delete notification group');
    }
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-800 flex items-center justify-between">
        <span className="flex items-center gap-2">
          <Users className="w-4 h-4 text-slate-500" />
          Notification Groups
        </span>
        {editingId === null && (
          <button
            onClick={startCreate}
            className="flex items-center gap-1.5 text-[11px] font-bold text-blue-600 hover:text-blue-500 cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
            New Group
          </button>
        )}
      </div>

      {editingId !== null && (
        <form onSubmit={handleSave} className="p-4 border-b border-slate-100 bg-slate-50 space-y-2.5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
            <input
              type="text"
              placeholder="Group name (e.g. Payments Team)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <input
              type="text"
              placeholder="Emails, comma-separated"
              value={emails}
              onChange={(e) => setEmails(e.target.value)}
              required
              className="px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          {error && <div className="text-[11px] font-medium text-rose-700">{error}</div>}
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={saving}
              className="px-3 py-1.5 text-xs font-bold bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-md cursor-pointer"
            >
              {saving ? 'Saving…' : editingId ? 'Save changes' : 'Create group'}
            </button>
            <button type="button" onClick={cancel} className="px-3 py-1.5 text-xs font-bold text-slate-500 hover:text-slate-700 cursor-pointer">
              Cancel
            </button>
          </div>
        </form>
      )}

      {groups.length === 0 ? (
        <div className="px-5 py-6 text-center text-xs text-slate-400 italic">
          No notification groups yet -- rules fall back to their own recipients field, or the server default.
        </div>
      ) : (
        <div className="divide-y divide-slate-100">
          {groups.map((g) => (
            <div key={g.group_id} className="px-5 py-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-bold text-slate-800">{g.name}</div>
                <div className="text-[11px] text-slate-500 truncate">{g.emails}</div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button onClick={() => startEdit(g)} title="Edit" className="p-1.5 rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-100 cursor-pointer">
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <button onClick={() => handleDelete(g)} title="Delete" className="p-1.5 rounded-md text-rose-400 hover:text-rose-600 hover:bg-rose-50 cursor-pointer">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// -- Rule create/edit form (admin-only) --------------------------------------

const RuleForm: React.FC<{
  editingRule: AlertRule | null;
  groups: NotificationGroup[];
  onSaved: () => void;
  onCancel: () => void;
}> = ({ editingRule, groups, onSaved, onCancel }) => {
  const [form, setForm] = useState(emptyRuleForm);
  const [windows, setWindows] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (editingRule) {
      setForm({
        name: editingRule.name,
        min_level: editingRule.min_level,
        mode: editingRule.mode,
        source_system_filter: editingRule.source_system_filter || '',
        component_filter: editingRule.component_filter || '',
        message_contains: editingRule.message_contains || '',
        dedup_window_minutes: editingRule.dedup_window_minutes,
        notification_group_id: editingRule.notification_group_id || '',
        recipients: editingRule.recipients || '',
      });
      const base: Record<string, number> = {};
      severitiesFrom(editingRule.min_level).forEach((s) => {
        base[s] = editingRule.dedup_windows?.[s] ?? editingRule.dedup_window_minutes;
      });
      setWindows(base);
    } else {
      setForm(emptyRuleForm);
      const base: Record<string, number> = {};
      severitiesFrom(emptyRuleForm.min_level).forEach((s) => (base[s] = emptyRuleForm.dedup_window_minutes));
      setWindows(base);
    }
  }, [editingRule]);

  const updateMinLevel = (level: string) => {
    setForm((f) => ({ ...f, min_level: level }));
    setWindows((prev) => {
      const next: Record<string, number> = {};
      severitiesFrom(level).forEach((s) => (next[s] = prev[s] ?? form.dedup_window_minutes));
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    const payload = {
      name: form.name,
      min_level: form.min_level,
      mode: form.mode,
      source_system_filter: form.source_system_filter || null,
      component_filter: form.component_filter || null,
      message_contains: form.message_contains || null,
      dedup_window_minutes: form.dedup_window_minutes,
      notification_group_id: form.notification_group_id || null,
      recipients: form.notification_group_id ? null : form.recipients || null,
      dedup_windows: windows,
    };
    try {
      if (editingRule) {
        await api.put(`/api/alerts/rules/${editingRule.rule_id}`, payload);
      } else {
        await api.post('/api/alerts/rules', payload);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to save rule');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="bg-slate-50 border border-slate-200 rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-slate-800">{editingRule ? `Edit "${editingRule.name}"` : 'New alert rule'}</h3>
        <button type="button" onClick={onCancel} className="text-slate-400 hover:text-slate-600 cursor-pointer">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Name</label>
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            className="w-full px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Fires on (min. severity)</label>
          <select
            value={form.min_level}
            onChange={(e) => updateMinLevel(e.target.value)}
            className="w-full px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {SEVERITY_ORDER.map((s) => (
              <option key={s} value={s}>{s}+</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Mode</label>
          <select
            value={form.mode}
            onChange={(e) => setForm({ ...form, mode: e.target.value as 'immediate' | 'digest' })}
            className="w-full px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="immediate">Immediate -- one email per event</option>
            <option value="digest">Digest -- one summary per batch</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <input
          type="text"
          placeholder="Source system contains (optional)"
          value={form.source_system_filter}
          onChange={(e) => setForm({ ...form, source_system_filter: e.target.value })}
          className="px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <input
          type="text"
          placeholder="Component contains (optional)"
          value={form.component_filter}
          onChange={(e) => setForm({ ...form, component_filter: e.target.value })}
          className="px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <input
          type="text"
          placeholder="Message contains (optional)"
          value={form.message_contains}
          onChange={(e) => setForm({ ...form, message_contains: e.target.value })}
          className="px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Notify group</label>
          <select
            value={form.notification_group_id}
            onChange={(e) => setForm({ ...form, notification_group_id: e.target.value })}
            className="w-full px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Use recipients field below / server default</option>
            {groups.map((g) => (
              <option key={g.group_id} value={g.group_id}>{g.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">
            Recipients {form.notification_group_id && <span className="normal-case text-slate-400">(ignored while a group is selected)</span>}
          </label>
          <input
            type="text"
            placeholder="Emails, comma-separated"
            value={form.recipients}
            onChange={(e) => setForm({ ...form, recipients: e.target.value })}
            disabled={!!form.notification_group_id}
            className="w-full px-3 py-2 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-slate-100 disabled:text-slate-400"
          />
        </div>
      </div>

      <div>
        <label className="block text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">
          Suppress repeats for, by severity of the triggering event (minutes)
        </label>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
          {severitiesFrom(form.min_level).map((s) => (
            <div key={s}>
              <div className="text-[10px] font-bold text-slate-500 mb-0.5">{s}</div>
              <input
                type="number"
                min={0}
                value={windows[s] ?? 0}
                onChange={(e) => setWindows((w) => ({ ...w, [s]: Number(e.target.value) }))}
                className="w-full px-2 py-1.5 text-xs border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          ))}
        </div>
        <p className="text-[10px] text-slate-400 mt-1">0 means never suppress -- notify every time that severity fires.</p>
      </div>

      {error && <div className="text-xs font-medium text-rose-700 bg-rose-50 border border-rose-200 rounded-md px-3 py-2">{error}</div>}

      <button
        type="submit"
        disabled={saving}
        className="px-4 py-2 text-xs font-bold bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-md cursor-pointer"
      >
        {saving ? 'Saving…' : editingRule ? 'Save changes' : 'Create rule'}
      </button>
    </form>
  );
};

// -- Main view ----------------------------------------------------------------

export const AlertsView: React.FC<AlertsViewProps> = ({ onInvestigate, isAdmin }) => {
  const confirm = useConfirm();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [groups, setGroups] = useState<NotificationGroup[]>([]);
  const [history, setHistory] = useState<AlertDispatchHistory | null>(null);
  const [isSending, setIsSending] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<{ success: boolean; status: string } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);

  const fetchRules = async () => {
    try {
      const data = await api.get<AlertRule[]>('/api/alerts/rules');
      setRules(data);
    } catch (err) {
      console.error('Failed to fetch alert rules:', err);
    }
  };

  const fetchGroups = async () => {
    try {
      const data = await api.get<NotificationGroup[]>('/api/alerts/groups');
      setGroups(data);
    } catch (err) {
      console.error('Failed to fetch notification groups:', err);
    }
  };

  const fetchHistory = async () => {
    try {
      const data = await api.get<AlertDispatchHistory>('/api/alerts/history?page=1&page_size=20');
      setHistory(data);
    } catch (err) {
      console.error('Failed to fetch alert history:', err);
    }
  };

  useEffect(() => {
    fetchRules();
    fetchGroups();
    fetchHistory();
  }, []);

  const handleTestAlert = async () => {
    setIsSending(true);
    setTestResult(null);
    try {
      const data = await api.post<{ success: boolean; status: string }>('/api/alerts/test', {});
      setTestResult(data);
      fetchHistory();
    } catch (err) {
      setTestResult({ success: false, status: err instanceof ApiError ? err.detail : 'Failed to send test alert' });
    } finally {
      setIsSending(false);
    }
  };

  const handleToggleEnabled = async (rule: AlertRule) => {
    try {
      await api.put(`/api/alerts/rules/${rule.rule_id}`, { enabled: !rule.enabled });
      fetchRules();
    } catch (err) {
      console.error('Failed to toggle rule:', err);
    }
  };

  const handleDeleteRule = async (rule: AlertRule) => {
    const confirmed = await confirm({
      title: 'Delete alert rule?',
      message: `Permanently delete "${rule.name}"? This cannot be undone.`,
      confirmLabel: 'Delete',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      await api.delete(`/api/alerts/rules/${rule.rule_id}`);
      fetchRules();
    } catch (err) {
      console.error('Failed to delete rule:', err);
    }
  };

  const openCreateForm = () => {
    setEditingRule(null);
    setFormOpen(true);
  };

  const openEditForm = (rule: AlertRule) => {
    setEditingRule(rule);
    setFormOpen(true);
  };

  const closeForm = () => setFormOpen(false);

  const handleFormSaved = () => {
    setFormOpen(false);
    fetchRules();
  };

  const groupById = Object.fromEntries(groups.map((g) => [g.group_id, g]));

  const describeFilters = (rule: AlertRule) => {
    const parts: string[] = [];
    if (rule.source_system_filter) parts.push(`source contains "${rule.source_system_filter}"`);
    if (rule.component_filter) parts.push(`component contains "${rule.component_filter}"`);
    if (rule.message_contains) parts.push(`message contains "${rule.message_contains}"`);
    return parts.length ? parts.join(', ') : 'no additional filters';
  };

  return (
    <div className="space-y-6">
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <BellRing className="w-5 h-5 text-blue-600" />
            Alert Rules
          </h2>
          {isAdmin && !formOpen && (
            <button
              onClick={openCreateForm}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold bg-blue-600 hover:bg-blue-500 text-white rounded-md cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              New Rule
            </button>
          )}
        </div>

        {!isAdmin && (
          <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3.5 py-2.5 text-xs text-blue-900">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            <span>Ask an admin to configure alert rules and notification groups -- this view shows the live configuration and dispatch history.</span>
          </div>
        )}

        {isAdmin && formOpen && (
          <RuleForm editingRule={editingRule} groups={groups} onSaved={handleFormSaved} onCancel={closeForm} />
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {rules.map((rule) => (
            <div key={rule.rule_id} className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-bold text-xs text-slate-900">{rule.name}</span>
                <div className="flex items-center gap-2">
                  <span className={`font-bold text-[11px] ${rule.enabled ? 'text-emerald-600' : 'text-slate-400'}`}>
                    {rule.enabled ? '✓ Active' : '○ Disabled'}
                  </span>
                  {isAdmin && (
                    <div className="flex items-center gap-0.5">
                      <button onClick={() => handleToggleEnabled(rule)} title={rule.enabled ? 'Disable' : 'Enable'} className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-200 cursor-pointer">
                        <Power className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => openEditForm(rule)} title="Edit" className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-200 cursor-pointer">
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleDeleteRule(rule)} title="Delete" className="p-1 rounded text-rose-400 hover:text-rose-600 hover:bg-rose-50 cursor-pointer">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              </div>
              <div className="text-xs text-slate-600 leading-relaxed">
                Fires on <strong>{rule.min_level}+</strong> events, <strong>{rule.mode === 'immediate' ? 'one email per event' : 'one digest per batch'}</strong>.
                Suppresses repeats for {rule.dedup_window_minutes}m
                {rule.dedup_windows && Object.keys(rule.dedup_windows).length > 0 && ' (varies by severity)'}.
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
                <Filter className="w-3 h-3" />
                {describeFilters(rule)}
              </div>
              {rule.notification_group_id ? (
                <div className="text-[11px] text-slate-500">
                  → group: <span className="font-semibold">{groupById[rule.notification_group_id]?.name || 'unknown group'}</span>
                </div>
              ) : (
                rule.recipients && <div className="text-[11px] text-slate-500">→ {rule.recipients}</div>
              )}
            </div>
          ))}
          {rules.length === 0 && <div className="text-xs text-slate-400 italic">Loading rules…</div>}
        </div>

        <div className="bg-slate-900 p-5 rounded-xl text-white space-y-3">
          <div className="flex items-center gap-2">
            <Mail className="w-4 h-4 text-blue-400" />
            <span className="font-bold text-xs tracking-wide">Test Email Dispatch</span>
          </div>

          <p className="text-xs text-slate-300">
            Sends a test alert through the same email path real alerts use. If SMTP isn't configured
            (see Settings), this logs locally instead of failing silently -- check the response below.
          </p>

          <div className="flex items-center gap-3 flex-wrap">
            <button
              onClick={handleTestAlert}
              disabled={isSending}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold text-xs rounded-lg transition-all cursor-pointer shadow-2xs"
            >
              <Send className="w-3.5 h-3.5" />
              <span>{isSending ? 'Dispatching...' : 'Send test alert'}</span>
            </button>

            {testResult && (
              <div
                className={`flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded border ${
                  testResult.success
                    ? 'text-emerald-400 bg-emerald-950/80 border-emerald-800'
                    : 'text-amber-300 bg-amber-950/40 border-amber-800'
                }`}
              >
                {testResult.success ? <CheckCircle2 className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                <span>{testResult.status}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {isAdmin && <NotificationGroupsPanel groups={groups} onChanged={fetchGroups} />}

      <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-800 flex items-center gap-2">
          <History className="w-4 h-4 text-slate-500" />
          Dispatch History
        </div>
        {!history || history.entries.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-slate-400 italic">No alerts dispatched yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-100 text-slate-600 font-bold uppercase tracking-wider text-[10px] border-b border-slate-200">
                <tr>
                  <th className="px-4 py-2.5">Time</th>
                  <th className="px-4 py-2.5">Rule</th>
                  <th className="px-4 py-2.5">Recipient</th>
                  <th className="px-4 py-2.5">Events</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Investigation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 text-slate-800 font-medium">
                {history.entries.map((e) => (
                  <tr key={e.dispatch_id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-mono text-[11px] text-slate-500">{e.triggered_at?.slice(0, 19).replace('T', ' ')}</td>
                    <td className="px-4 py-3 font-bold">{e.rule_name}</td>
                    <td className="px-4 py-3 text-slate-600">{e.recipient}</td>
                    <td className="px-4 py-3">{e.event_count}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 font-bold ${e.success ? 'text-emerald-600' : 'text-rose-600'}`}>
                        {e.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                        {e.success ? 'Sent' : 'Failed'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {e.correlation_field && e.correlation_value ? (
                        <button
                          onClick={() => onInvestigate({ kind: e.correlation_field as any, value: e.correlation_value as string })}
                          className="flex items-center gap-1 text-[11px] font-bold text-blue-600 hover:text-blue-500 cursor-pointer"
                        >
                          <ExternalLink className="w-3 h-3" />
                          View
                        </button>
                      ) : (
                        <span className="text-[11px] text-slate-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
