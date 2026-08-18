import React, { useState } from 'react';
import { MessageSquareCode, Sparkles, Send, Database, AlertTriangle } from 'lucide-react';
import { ChatMessage } from '../types';
import { api, ApiError } from '../api';

interface ChatViewProps {
  ollamaAvailable: boolean;
}

export const ChatView: React.FC<ChatViewProps> = ({ ollamaAvailable }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'm-1',
      sender: 'assistant',
      text: 'Ask me questions about your ingested logs in plain English -- I\'ll generate SQL, run it against your data (read-only), and summarize the results.',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ]);

  const [inputQuery, setInputQuery] = useState<string>('');
  const [isQuerying, setIsQuerying] = useState<boolean>(false);

  const samplePrompts = [
    'Show me all critical events from the last upload',
    'What components have the most errors?',
    'List the 10 most recent WARN events',
  ];

  const handleSendQuery = async (queryText?: string) => {
    const q = queryText || inputQuery;
    if (!q.trim()) return;

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      sender: 'user',
      text: q,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputQuery('');
    setIsQuerying(true);

    try {
      const data = await api.post<{ sql: string | null; results: Record<string, any>[] | null; summary: string | null; status: string }>(
        '/api/ai/chat',
        { question: q }
      );
      const assistantMsg: ChatMessage = {
        id: `a-${Date.now()}`,
        sender: 'assistant',
        text: data.summary || data.status,
        sql_query: data.sql || undefined,
        results: data.results || undefined,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      const detail =
        err instanceof ApiError
          ? err.status === 503
            ? 'Ollama is unavailable right now -- try again once it\'s running.'
            : err.detail
          : 'Something went wrong answering that.';
      setMessages((prev) => [
        ...prev,
        { id: `e-${Date.now()}`, sender: 'assistant', text: detail, timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) },
      ]);
    } finally {
      setIsQuerying(false);
    }
  };

  const resultColumns = (results: Record<string, any>[]) => {
    const cols = new Set<string>();
    results.slice(0, 10).forEach((r) => Object.keys(r).forEach((k) => cols.add(k)));
    return Array.from(cols).slice(0, 6);
  };

  return (
    <div className="space-y-6">
      <div className="bg-surface p-6 rounded-2xl border border-surface-border shadow-2xs space-y-4 card-brand-glow">
        <h2 className="text-xl font-bold text-text flex items-center gap-2">
          <MessageSquareCode className="w-5 h-5 text-brand" />
          Chat With Your Logs
        </h2>
        <p className="text-xs text-text-muted">
          Ask questions in plain English. A local Ollama model generates SQL, it's validated and run
          read-only against your data, and the results are summarized -- nothing leaves your network.
        </p>

        {!ollamaAvailable && (
          <div className="flex items-center gap-2 bg-warning-light border border-warning/30 rounded-lg px-3.5 py-2.5 text-xs text-warning">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            Ollama is currently offline -- questions will fail until it's running. Check Settings.
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-surface-border">
          <span className="text-xs font-bold text-text-muted mr-1 flex items-center gap-1">
            <Sparkles className="w-3.5 h-3.5 text-warning" /> Suggested Queries:
          </span>
          {samplePrompts.map((prompt, idx) => (
            <button
              key={idx}
              onClick={() => handleSendQuery(prompt)}
              className="text-xs bg-surface-alt hover:bg-brand/[0.04] text-text-secondary hover:text-brand font-medium px-3 py-1 rounded-full border border-surface-border hover:border-brand/30 transition-all cursor-pointer"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`p-5 rounded-2xl border transition-all ${
              msg.sender === 'user'
                ? 'bg-brand text-white border-brand-hover ml-12 shadow-sm'
                : 'bg-surface text-text border-surface-border mr-12 shadow-2xs space-y-3'
            }`}
          >
            <div className="flex items-center justify-between border-b pb-2 mb-2 border-opacity-20 border-surface-border">
              <span className="font-extrabold text-xs tracking-wide flex items-center gap-1.5">
                {msg.sender === 'user' ? '🧑‍💻 You' : '🤖 AI Log Assistant'}
              </span>
              <span className="text-[10px] opacity-70 font-mono">{msg.timestamp}</span>
            </div>

            <p className="text-xs font-medium leading-relaxed">{msg.text}</p>

            {msg.sql_query && (
              <div className="space-y-1 pt-2">
                <div className="text-[10px] font-bold text-text-muted uppercase tracking-wider flex items-center gap-1">
                  <Database className="w-3 h-3 text-brand" /> Generated SQL (read-only)
                </div>
                <pre className="bg-sidebar text-aquamarine font-mono text-[11px] p-3 rounded-lg overflow-x-auto border border-sidebar-border">
                  {msg.sql_query}
                </pre>
              </div>
            )}

            {msg.results && msg.results.length > 0 && (
              <div className="space-y-1.5 pt-2">
                <div className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                  Matching Rows ({msg.results.length})
                </div>
                <div className="bg-surface-alt rounded-lg border border-surface-border overflow-x-auto max-h-48">
                  <table className="w-full text-left text-[11px]">
                    <thead className="bg-surface-alt text-text-secondary font-bold uppercase text-[9px] border-b border-surface-border">
                      <tr>
                        {resultColumns(msg.results).map((col) => (
                          <th key={col} className="p-2">{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-surface-border text-text font-medium">
                      {msg.results.slice(0, 10).map((r, i) => (
                        <tr key={i} className="table-row-brand">
                          {resultColumns(msg.results!).map((col) => (
                            <td key={col} className="p-2 truncate max-w-xs">{String(r[col] ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        ))}

        {isQuerying && (
          <div className="p-4 bg-surface-alt rounded-2xl border border-surface-border text-xs text-text-secondary flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-brand animate-spin" />
            <span>Generating SQL and querying your logs…</span>
          </div>
        )}
      </div>

      <div className="bg-surface p-3 rounded-2xl border border-surface-border shadow-sm flex items-center gap-3">
        <input
          type="text"
          value={inputQuery}
          onChange={(e) => setInputQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSendQuery()}
          placeholder="Ask anything about your logs (e.g. 'Show database locks in the last 24 hours')..."
          className="flex-1 text-xs bg-surface-alt border border-surface-border rounded-lg px-4 py-2.5 font-medium text-text focus:outline-none input-brand"
        />
        <button
          onClick={() => handleSendQuery()}
          disabled={isQuerying || !inputQuery.trim()}
          className="px-5 py-2.5 bg-brand hover:bg-brand-hover disabled:opacity-50 text-white font-bold text-xs rounded-lg transition-all cursor-pointer flex items-center gap-1.5 shadow-md shadow-brand/20"
        >
          <Send className="w-3.5 h-3.5" />
          <span>Ask</span>
        </button>
      </div>
    </div>
  );
};
