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
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs space-y-4">
        <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <MessageSquareCode className="w-5 h-5 text-blue-600" />
          Chat With Your Logs
        </h2>
        <p className="text-xs text-slate-500">
          Ask questions in plain English. A local Ollama model generates SQL, it's validated and run
          read-only against your data, and the results are summarized -- nothing leaves your network.
        </p>

        {!ollamaAvailable && (
          <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3.5 py-2.5 text-xs text-amber-800">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            Ollama is currently offline -- questions will fail until it's running. Check Settings.
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-100">
          <span className="text-xs font-bold text-slate-500 mr-1 flex items-center gap-1">
            <Sparkles className="w-3.5 h-3.5 text-amber-500" /> Suggested Queries:
          </span>
          {samplePrompts.map((prompt, idx) => (
            <button
              key={idx}
              onClick={() => handleSendQuery(prompt)}
              className="text-xs bg-slate-100 hover:bg-blue-50 text-slate-700 hover:text-blue-700 font-medium px-3 py-1 rounded-full border border-slate-200 hover:border-blue-300 transition-all cursor-pointer"
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
            className={`p-5 rounded-xl border transition-all ${
              msg.sender === 'user'
                ? 'bg-blue-600 text-white border-blue-700 ml-12 shadow-sm'
                : 'bg-white text-slate-800 border-slate-200 mr-12 shadow-2xs space-y-3'
            }`}
          >
            <div className="flex items-center justify-between border-b pb-2 mb-2 border-opacity-20 border-slate-400">
              <span className="font-extrabold text-xs tracking-wide flex items-center gap-1.5">
                {msg.sender === 'user' ? '🧑‍💻 You' : '🤖 AI Log Assistant'}
              </span>
              <span className="text-[10px] opacity-70 font-mono">{msg.timestamp}</span>
            </div>

            <p className="text-xs font-medium leading-relaxed">{msg.text}</p>

            {msg.sql_query && (
              <div className="space-y-1 pt-2">
                <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1">
                  <Database className="w-3 h-3 text-blue-600" /> Generated SQL (read-only)
                </div>
                <pre className="bg-slate-950 text-emerald-400 font-mono text-[11px] p-3 rounded-lg overflow-x-auto border border-slate-800">
                  {msg.sql_query}
                </pre>
              </div>
            )}

            {msg.results && msg.results.length > 0 && (
              <div className="space-y-1.5 pt-2">
                <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                  Matching Rows ({msg.results.length})
                </div>
                <div className="bg-slate-50 rounded-lg border border-slate-200 overflow-x-auto max-h-48">
                  <table className="w-full text-left text-[11px]">
                    <thead className="bg-slate-100 text-slate-600 font-bold uppercase text-[9px] border-b border-slate-200">
                      <tr>
                        {resultColumns(msg.results).map((col) => (
                          <th key={col} className="p-2">{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 text-slate-800 font-medium">
                      {msg.results.slice(0, 10).map((r, i) => (
                        <tr key={i}>
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
          <div className="p-4 bg-slate-50 rounded-xl border border-slate-200 text-xs text-slate-600 flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-amber-500 animate-spin" />
            <span>Generating SQL and querying your logs…</span>
          </div>
        )}
      </div>

      <div className="bg-white p-3 rounded-xl border border-slate-200 shadow-sm flex items-center gap-3">
        <input
          type="text"
          value={inputQuery}
          onChange={(e) => setInputQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSendQuery()}
          placeholder="Ask anything about your logs (e.g. 'Show database locks in the last 24 hours')..."
          className="flex-1 text-xs bg-slate-50 border border-slate-300 rounded-lg px-4 py-2.5 font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={() => handleSendQuery()}
          disabled={isQuerying || !inputQuery.trim()}
          className="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold text-xs rounded-lg transition-all cursor-pointer flex items-center gap-1.5 shadow-2xs"
        >
          <Send className="w-3.5 h-3.5" />
          <span>Ask</span>
        </button>
      </div>
    </div>
  );
};
