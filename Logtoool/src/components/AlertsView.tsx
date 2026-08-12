import React, { useState, useEffect } from 'react';
import { BellRing, Send, Mail, CheckCircle2, XCircle, Info } from 'lucide-react';
import { AlertRuleInfo } from '../types';
import { api, ApiError } from '../api';

export const AlertsView: React.FC = () => {
  const [rules, setRules] = useState<AlertRuleInfo[]>([]);
  const [isSending, setIsSending] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<{ success: boolean; status: string } | null>(null);

  const fetchRules = async () => {
    try {
      const data = await api.get<{ rules: AlertRuleInfo[] }>('/api/alerts/rules');
      setRules(data.rules || []);
    } catch (err) {
      console.error('Failed to fetch alert rules:', err);
    }
  };

  useEffect(() => {
    fetchRules();
  }, []);

  const handleTestAlert = async () => {
    setIsSending(true);
    setTestResult(null);
    try {
      const data = await api.post<{ success: boolean; status: string }>('/api/alerts/test', {});
      setTestResult(data);
    } catch (err) {
      setTestResult({ success: false, status: err instanceof ApiError ? err.detail : 'Failed to send test alert' });
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs space-y-4">
        <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <BellRing className="w-5 h-5 text-blue-600" />
          Alert Rules
        </h2>

        <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3.5 py-2.5 text-xs text-blue-900">
          <Info className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            These two rules are currently fixed (evaluated automatically on every upload). Per-rule editing,
            custom rules, and a dispatch history are planned but not built yet -- this page shows exactly what
            the system actually does today, nothing more.
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {rules.map((rule) => (
            <div key={rule.name} className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-bold text-xs text-slate-900">{rule.name}</span>
                <span className="text-emerald-600 font-bold text-[11px]">✓ Active</span>
              </div>
              <div className="text-xs text-slate-600 leading-relaxed">{rule.description}</div>
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
    </div>
  );
};
