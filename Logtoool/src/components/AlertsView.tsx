import React, { useState, useEffect } from 'react';
import { BellRing, Send, Mail, CheckCircle2, XCircle, Info, History, Filter } from 'lucide-react';
import { AlertRule, AlertDispatchHistory } from '../types';
import { api, ApiError } from '../api';

export const AlertsView: React.FC = () => {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [history, setHistory] = useState<AlertDispatchHistory | null>(null);
  const [isSending, setIsSending] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<{ success: boolean; status: string } | null>(null);

  const fetchRules = async () => {
    try {
      const data = await api.get<AlertRule[]>('/api/alerts/rules');
      setRules(data);
    } catch (err) {
      console.error('Failed to fetch alert rules:', err);
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

  const describeFilters = (rule: AlertRule) => {
    const parts: string[] = [];
    if (rule.source_system_filter) parts.push(`source contains "${rule.source_system_filter}"`);
    if (rule.component_filter) parts.push(`component contains "${rule.component_filter}"`);
    if (rule.message_contains) parts.push(`message contains "${rule.message_contains}"`);
    return parts.length ? parts.join(', ') : 'no additional filters';
  };

  return (
    <div className="space-y-6">
      <div className="bg-surface p-6 rounded-xl border border-surface-border shadow-2xs space-y-4">
        <h2 className="text-xl font-bold text-text flex items-center gap-2">
          <BellRing className="w-5 h-5 text-brand" />
          Alert Rules
        </h2>

        <div className="flex items-start gap-2 bg-brand/[0.04] border border-brand/15 rounded-lg px-3.5 py-2.5 text-xs text-brand">
          <Info className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            Rules are now configurable server-side (custom rules, filters, per-rule dedup windows, and
            recipients). Creating/editing rules from this page is coming next -- for now this shows the live
            rule configuration and dispatch history.
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {rules.map((rule) => (
            <div key={rule.rule_id} className="bg-surface-alt p-4 rounded-xl border border-surface-border space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-bold text-xs text-text">{rule.name}</span>
                <span className={`font-bold text-[11px] ${rule.enabled ? 'text-success' : 'text-text-muted'}`}>
                  {rule.enabled ? '✓ Active' : '○ Disabled'}
                </span>
              </div>
              <div className="text-xs text-text-secondary leading-relaxed">
                Fires on <strong>{rule.min_level}+</strong> events, <strong>{rule.mode === 'immediate' ? 'one email per event' : 'one digest per batch'}</strong>.
                Suppresses repeats for {rule.dedup_window_minutes}m.
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
                <Filter className="w-3 h-3" />
                {describeFilters(rule)}
              </div>
              {rule.recipients && (
                <div className="text-[11px] text-text-muted">→ {rule.recipients}</div>
              )}
            </div>
          ))}
          {rules.length === 0 && <div className="text-xs text-text-muted italic">Loading rules…</div>}
        </div>

        <div className="bg-sidebar p-5 rounded-xl text-white space-y-3">
          <div className="flex items-center gap-2">
            <Mail className="w-4 h-4 text-brand-hover" />
            <span className="font-bold text-xs tracking-wide">Test Email Dispatch</span>
          </div>

          <p className="text-xs text-white/70">
            Sends a test alert through the same email path real alerts use. If SMTP isn't configured
            (see Settings), this logs locally instead of failing silently -- check the response below.
          </p>

          <div className="flex items-center gap-3 flex-wrap">
            <button
              onClick={handleTestAlert}
              disabled={isSending}
              className="flex items-center gap-2 px-4 py-2 bg-brand hover:bg-brand-hover disabled:opacity-50 text-white font-bold text-xs rounded-lg transition-all cursor-pointer shadow-md shadow-brand/20"
            >
              <Send className="w-3.5 h-3.5" />
              <span>{isSending ? 'Dispatching...' : 'Send test alert'}</span>
            </button>

            {testResult && (
              <div
                className={`flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded border ${
                  testResult.success
                    ? 'text-success bg-success/10 border-success/20'
                    : 'text-warning bg-warning/10 border-warning/20'
                }`}
              >
                {testResult.success ? <CheckCircle2 className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                <span>{testResult.status}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="bg-surface rounded-xl border border-surface-border shadow-2xs overflow-hidden">
        <div className="px-5 py-3 border-b border-surface-border bg-surface-alt font-bold text-xs text-text flex items-center gap-2">
          <History className="w-4 h-4 text-text-muted" />
          Dispatch History
        </div>
        {!history || history.entries.length === 0 ? (
          <div className="px-5 py-6 text-center text-xs text-text-muted italic">No alerts dispatched yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-alt text-text-secondary font-bold uppercase tracking-wider text-[10px] border-b border-surface-border">
                <tr>
                  <th className="px-4 py-2.5">Time</th>
                  <th className="px-4 py-2.5">Rule</th>
                  <th className="px-4 py-2.5">Recipient</th>
                  <th className="px-4 py-2.5">Events</th>
                  <th className="px-4 py-2.5">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border text-text font-medium">
                {history.entries.map((e) => (
                  <tr key={e.dispatch_id} className="hover:bg-surface-alt table-row-brand">
                    <td className="px-4 py-3 font-mono text-[11px] text-text-muted">{e.triggered_at?.slice(0, 19).replace('T', ' ')}</td>
                    <td className="px-4 py-3 font-bold">{e.rule_name}</td>
                    <td className="px-4 py-3 text-text-secondary">{e.recipient}</td>
                    <td className="px-4 py-3">{e.event_count}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 font-bold ${e.success ? 'text-success' : 'text-error'}`}>
                        {e.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                        {e.success ? 'Sent' : 'Failed'}
                      </span>
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
