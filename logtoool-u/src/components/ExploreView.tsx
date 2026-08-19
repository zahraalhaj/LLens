import React, { useState, useEffect } from 'react';
import { Search, Filter, Sparkles, ChevronLeft, ChevronRight, Eye, AlertCircle, FileCode, CheckCircle, ArrowUpRight } from 'lucide-react';
import { LogEvent, LogLevel, AIExplanation } from '../types';
import { api, ApiError } from '../api';

interface ExploreViewProps {
  sources: string[];
  components: string[];
  onRefreshStats: () => void;
}

export const ExploreView: React.FC<ExploreViewProps> = ({ sources, components, onRefreshStats }) => {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [page, setPage] = useState<number>(1);
  const [pageSize] = useState<number>(50);
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  // Filters
  const [selectedLevel, setSelectedLevel] = useState<string>('ALL');
  const [selectedSource, setSelectedSource] = useState<string>('ALL');
  const [selectedComponent, setSelectedComponent] = useState<string>('ALL');
  const [searchTerm, setSearchTerm] = useState<string>('');

  // Selected event inspection
  const [selectedEvent, setSelectedEvent] = useState<LogEvent | null>(null);
  const [contextEvents, setContextEvents] = useState<LogEvent[]>([]);
  const [isLoadingContext, setIsLoadingContext] = useState<boolean>(false);

  // AI Explain
  const [aiExplanation, setAiExplanation] = useState<AIExplanation | null>(null);
  const [isExplaining, setIsExplaining] = useState<boolean>(false);
  const [explainError, setExplainError] = useState<string | null>(null);

  const fetchEvents = async () => {
    try {
      const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
      if (selectedLevel !== 'ALL') params.set('level', selectedLevel);
      if (selectedSource !== 'ALL') params.set('source_system', selectedSource);
      if (selectedComponent !== 'ALL') params.set('component', selectedComponent);
      if (searchTerm) params.set('search_term', searchTerm);

      const data = await api.get<{ events: LogEvent[]; total: number }>(`/api/logs/events?${params}`);
      setEvents(data.events || []);
      setTotalCount(data.total || 0);
    } catch (err) {
      console.error('Failed to fetch events:', err);
    }
  };

  useEffect(() => {
    fetchEvents();
  }, [page, selectedLevel, selectedSource, selectedComponent, searchTerm]);

  const handleSelectEvent = async (evt: LogEvent) => {
    setSelectedEvent(evt);
    setAiExplanation(null);
    setExplainError(null);
    setIsLoadingContext(true);

    try {
      const data = await api.get<{ event: LogEvent; context: LogEvent[] }>(`/api/logs/context/${evt.event_id}`);
      setContextEvents(data.context || []);
    } catch (err) {
      console.error('Failed to load context:', err);
    } finally {
      setIsLoadingContext(false);
    }
  };

  const handleExplain = async () => {
    if (!selectedEvent) return;
    setIsExplaining(true);
    setExplainError(null);

    try {
      const data = await api.post<{ explanation: AIExplanation }>('/api/ai/explain', {
        event_id: selectedEvent.event_id,
      });
      setAiExplanation(data.explanation);
    } catch (err) {
      setExplainError(
        err instanceof ApiError
          ? err.status === 503
            ? 'Ollama is unavailable right now -- try again once it\'s running.'
            : err.detail
          : 'Failed to explain event'
      );
    } finally {
      setIsExplaining(false);
    }
  };

  const getLevelBadge = (lvl: LogLevel) => {
    switch (lvl) {
      case 'CRITICAL':
        return 'bg-red-100 text-red-800 border-red-200 font-extrabold';
      case 'ERROR':
        return 'bg-rose-100 text-rose-700 border-rose-200 font-bold';
      case 'WARN':
        return 'bg-amber-100 text-amber-800 border-amber-200 font-bold';
      case 'INFO':
        return 'bg-blue-100 text-blue-700 border-blue-200 font-semibold';
      case 'DEBUG':
        return 'bg-slate-100 text-slate-600 border-slate-200 font-medium';
      default:
        return 'bg-slate-100 text-slate-600 border-slate-200';
    }
  };

  return (
    <div className="space-y-6">
      {/* Search & Filter Bar */}
      <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-slate-500" />
            <h2 className="text-sm font-bold text-slate-900">Log Event Explorer & Server-Side Search</h2>
          </div>
          <div className="text-xs text-slate-500 font-medium">
            Showing <strong className="text-slate-900">{events.length}</strong> of <strong className="text-slate-900">{totalCount.toLocaleString()}</strong> matching records
          </div>
        </div>

        {/* Severity Level Filter Buttons */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <span className="text-xs font-bold text-slate-500 mr-1">Severity:</span>
          {['ALL', 'CRITICAL', 'ERROR', 'WARN', 'INFO', 'DEBUG'].map((lvl) => (
            <button
              key={lvl}
              onClick={() => { setSelectedLevel(lvl); setPage(1); }}
              className={`px-3 py-1 rounded-md text-xs font-bold transition-all cursor-pointer ${
                selectedLevel === lvl
                  ? 'bg-slate-900 text-white shadow-2xs'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200 border border-slate-200'
              }`}
            >
              {lvl}
            </button>
          ))}
        </div>

        {/* Dropdowns & Keyword Search */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-100">
          <div>
            <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Source System</label>
            <select
              value={selectedSource}
              onChange={(e) => { setSelectedSource(e.target.value); setPage(1); }}
              className="w-full text-xs bg-slate-50 border border-slate-300 rounded-lg p-2 font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="ALL">All Sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Component / Logger</label>
            <select
              value={selectedComponent}
              onChange={(e) => { setSelectedComponent(e.target.value); setPage(1); }}
              className="w-full text-xs bg-slate-50 border border-slate-300 rounded-lg p-2 font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="ALL">All Components</option>
              {components.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Search Keywords</label>
            <div className="relative">
              <input
                type="text"
                placeholder="Search error, exception, IP address..."
                value={searchTerm}
                onChange={(e) => { setSearchTerm(e.target.value); setPage(1); }}
                className="w-full text-xs bg-slate-50 border border-slate-300 rounded-lg pl-8 pr-3 py-2 font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2.5" />
            </div>
          </div>
        </div>
      </div>

      {/* Main Table + Inspector Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Log Events Table */}
        <div className={`space-y-4 transition-all ${selectedEvent ? 'lg:col-span-7' : 'lg:col-span-12'}`}>
          <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-100 text-slate-600 font-bold uppercase tracking-wider text-[10px] border-b border-slate-200">
                  <tr>
                    <th className="px-3.5 py-2.5">Line</th>
                    <th className="px-3.5 py-2.5">Timestamp (UTC)</th>
                    <th className="px-3.5 py-2.5">Level</th>
                    <th className="px-3.5 py-2.5">Source System</th>
                    <th className="px-3.5 py-2.5">Component</th>
                    <th className="px-3.5 py-2.5">Log Message</th>
                    <th className="px-3.5 py-2.5 text-right">Inspect</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 text-slate-800 font-medium">
                  {events.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-slate-500 italic">
                        No log events matching specified filters. Try selecting "ALL" severity or clearing search terms.
                      </td>
                    </tr>
                  ) : (
                    events.map((evt) => {
                      const isSelected = selectedEvent?.event_id === evt.event_id;
                      return (
                        <tr
                          key={evt.event_id}
                          onClick={() => handleSelectEvent(evt)}
                          className={`hover:bg-blue-50/60 cursor-pointer transition-colors ${
                            isSelected ? 'bg-blue-50/90 font-semibold' : ''
                          }`}
                        >
                          <td className="px-3.5 py-2.5 font-mono text-[11px] text-slate-400">{evt.line_no}</td>
                          <td className="px-3.5 py-2.5 font-mono text-[11px] text-slate-600 whitespace-nowrap">
                            {evt.ts_utc ? evt.ts_utc.substring(0, 19).replace('T', ' ') : evt.ts_raw}
                          </td>
                          <td className="px-3.5 py-2.5">
                            <span className={`inline-block px-2 py-0.5 rounded text-[10px] border ${getLevelBadge(evt.level)}`}>
                              {evt.level}
                            </span>
                          </td>
                          <td className="px-3.5 py-2.5 font-semibold text-slate-800 truncate max-w-[120px]">
                            {evt.source_system}
                          </td>
                          <td className="px-3.5 py-2.5 text-slate-600 font-mono text-[11px] truncate max-w-[120px]">
                            {evt.component}
                          </td>
                          <td className="px-3.5 py-2.5 text-slate-900 truncate max-w-[280px]">
                            {evt.message}
                          </td>
                          <td className="px-3.5 py-2.5 text-right">
                            <button className="text-blue-600 hover:text-blue-800 p-1">
                              <Eye className="w-4 h-4" />
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination Controls */}
            <div className="px-4 py-3 bg-slate-50 border-t border-slate-200 flex items-center justify-between text-xs text-slate-600">
              <div>
                Page <strong className="text-slate-900">{page}</strong> of <strong className="text-slate-900">{totalPages}</strong>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-2.5 py-1 bg-white hover:bg-slate-100 disabled:opacity-40 border border-slate-200 rounded font-bold text-slate-700 transition-all cursor-pointer"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-2.5 py-1 bg-white hover:bg-slate-100 disabled:opacity-40 border border-slate-200 rounded font-bold text-slate-700 transition-all cursor-pointer"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Event Inspector & AI Explainer Panel */}
        {selectedEvent && (
          <div className="lg:col-span-5 space-y-4">
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 space-y-4 sticky top-20 max-h-[85vh] overflow-y-auto">
              <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                <div className="flex items-center gap-2">
                  <FileCode className="w-5 h-5 text-blue-600" />
                  <h3 className="font-bold text-sm text-slate-900">Log Event Inspector</h3>
                </div>
                <button
                  onClick={() => setSelectedEvent(null)}
                  className="text-xs font-bold text-slate-400 hover:text-slate-600 p-1"
                >
                  ✕ Close
                </button>
              </div>

              {/* Header Badge & Level */}
              <div className="bg-slate-50 p-3 rounded-lg border border-slate-200 space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className={`px-2 py-0.5 rounded text-[10px] border ${getLevelBadge(selectedEvent.level)}`}>
                    {selectedEvent.level}
                  </span>
                  <span className="text-[11px] font-mono text-slate-500">
                    Line {selectedEvent.line_no} ({selectedEvent.file_name})
                  </span>
                </div>
                <div className="text-xs font-bold text-slate-900 break-words font-mono">
                  {selectedEvent.message}
                </div>
              </div>

              {/* AI Root Cause Explainer Action */}
              <div className="bg-slate-900 rounded-xl p-4 text-white shadow-md space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center font-bold text-xs">
                      🤖
                    </div>
                    <span className="text-xs font-bold tracking-wide">AI Root Cause Assistant</span>
                  </div>
                  <span className="text-[10px] text-amber-400 font-semibold border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 rounded-full">
                    Ollama
                  </span>
                </div>

                <p className="text-[11px] text-slate-300 leading-normal">
                  Perform automated deep context analysis across surrounding log lines to pinpoint root causes and remediation steps.
                </p>

                <button
                  onClick={handleExplain}
                  disabled={isExplaining}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs rounded-lg transition-all cursor-pointer shadow-sm"
                >
                  <Sparkles className="w-4 h-4 text-amber-300" />
                  <span>{isExplaining ? 'Analyzing with Ollama...' : 'Explain Root Cause with AI'}</span>
                </button>

                {explainError && (
                  <div className="text-[11px] font-semibold text-rose-300 bg-rose-950/40 border border-rose-800 rounded-lg px-2.5 py-1.5">
                    {explainError}
                  </div>
                )}

                {aiExplanation && (
                  <div className="pt-3 border-t border-slate-800 space-y-2.5 text-xs">
                    <div className="bg-slate-800/80 p-2.5 rounded-lg border border-slate-700">
                      <div className="text-[10px] font-bold text-amber-400 uppercase tracking-wider mb-1">Probable Cause</div>
                      <div className="font-semibold text-slate-100">{aiExplanation.probable_cause}</div>
                    </div>

                    <div className="space-y-1">
                      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Technical Explanation</div>
                      <div className="text-slate-300 text-[11px] leading-relaxed bg-slate-950 p-2.5 rounded border border-slate-800">
                        {aiExplanation.explanation}
                      </div>
                    </div>

                    <div className="space-y-1">
                      <div className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider">Suggested Next Steps</div>
                      <ul className="space-y-1">
                        {aiExplanation.suggested_next_steps.map((step, idx) => (
                          <li key={idx} className="flex items-start gap-1.5 text-[11px] text-slate-200 bg-slate-800/40 p-1.5 rounded">
                            <span className="text-emerald-400 font-bold">•</span>
                            <span>{step}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                )}
              </div>

              {/* Raw JSON Attributes */}
              <div>
                <div className="text-xs font-bold text-slate-700 mb-1">Parsed JSON Fields</div>
                <pre className="bg-slate-950 text-slate-200 text-[11px] p-3 rounded-lg overflow-x-auto font-mono max-h-40">
                  {JSON.stringify(selectedEvent, null, 2)}
                </pre>
              </div>

              {/* Surrounding Raw Log Context */}
              <div>
                <div className="text-xs font-bold text-slate-700 mb-1">
                  Surrounding Raw Log Context (±5 Lines)
                </div>
                <div className="bg-slate-950 rounded-lg p-3 font-mono text-[11px] text-slate-300 space-y-1.5 max-h-48 overflow-y-auto border border-slate-800">
                  {contextEvents.map((ctx) => {
                    const isTarget = ctx.event_id === selectedEvent.event_id;
                    return (
                      <div
                        key={ctx.event_id}
                        className={`p-1.5 rounded text-[10px] leading-normal ${
                          isTarget ? 'bg-blue-900/60 text-blue-200 border-l-2 border-blue-400 font-bold' : 'hover:bg-slate-900'
                        }`}
                      >
                        <span className="text-slate-500 mr-2">Line {ctx.line_no}</span>
                        <span>{ctx.raw}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
