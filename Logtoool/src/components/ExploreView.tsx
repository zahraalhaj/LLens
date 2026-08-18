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
        return 'bg-error/10 text-error border border-error/20 font-extrabold';
      case 'ERROR':
        return 'bg-warning/10 text-warning border border-warning/20 font-bold';
      case 'WARN':
        return 'bg-notice/20 text-warning border border-warning/20 font-bold';
      case 'INFO':
        return 'bg-brand/[0.08] text-brand border border-brand/15 font-semibold';
      case 'DEBUG':
        return 'bg-surface-alt text-text-muted border border-surface-border font-medium';
      default:
        return 'bg-surface-alt text-text-muted border border-surface-border';
    }
  };

  return (
    <div className="space-y-6">
      {/* Search & Filter Bar */}
      <div className="bg-surface p-5 rounded-2xl border border-surface-border card-brand-glow space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-text-muted" />
            <h2 className="text-sm font-bold text-text">Log Event Explorer & Server-Side Search</h2>
          </div>
          <div className="text-xs text-text-muted font-medium">
            Showing <strong className="text-text">{events.length}</strong> of <strong className="text-text">{totalCount.toLocaleString()}</strong> matching records
          </div>
        </div>

        {/* Severity Level Filter Buttons */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <span className="text-xs font-bold text-text-muted mr-1">Severity:</span>
          {['ALL', 'CRITICAL', 'ERROR', 'WARN', 'INFO', 'DEBUG'].map((lvl) => (
            <button
              key={lvl}
              onClick={() => { setSelectedLevel(lvl); setPage(1); }}
              className={`px-3 py-1 rounded-md text-xs font-bold transition-all cursor-pointer ${
                selectedLevel === lvl
                  ? 'bg-brand text-white shadow-2xs'
                  : 'bg-surface-alt text-text-secondary hover:bg-surface-alt border border-surface-border'
              }`}
            >
              {lvl}
            </button>
          ))}
        </div>

        {/* Dropdowns & Keyword Search */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-surface-border">
          <div>
            <label className="block text-[11px] font-bold text-text-muted uppercase tracking-wider mb-1">Source System</label>
            <select
              value={selectedSource}
              onChange={(e) => { setSelectedSource(e.target.value); setPage(1); }}
              className="w-full text-xs bg-surface-alt border border-surface-border rounded-lg p-2 font-medium text-text input-brand"
            >
              <option value="ALL">All Sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-[11px] font-bold text-text-muted uppercase tracking-wider mb-1">Component / Logger</label>
            <select
              value={selectedComponent}
              onChange={(e) => { setSelectedComponent(e.target.value); setPage(1); }}
              className="w-full text-xs bg-surface-alt border border-surface-border rounded-lg p-2 font-medium text-text input-brand"
            >
              <option value="ALL">All Components</option>
              {components.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-[11px] font-bold text-text-muted uppercase tracking-wider mb-1">Search Keywords</label>
            <div className="relative">
              <input
                type="text"
                placeholder="Search error, exception, IP address..."
                value={searchTerm}
                onChange={(e) => { setSearchTerm(e.target.value); setPage(1); }}
                className="w-full text-xs bg-surface-alt border border-surface-border rounded-lg pl-8 pr-3 py-2 font-medium text-text input-brand"
              />
              <Search className="w-3.5 h-3.5 text-text-muted absolute left-2.5 top-2.5" />
            </div>
          </div>
        </div>
      </div>

      {/* Main Table + Inspector Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Log Events Table */}
        <div className={`space-y-4 transition-all ${selectedEvent ? 'lg:col-span-7' : 'lg:col-span-12'}`}>
          <div className="bg-surface rounded-2xl border border-surface-border card-brand-glow overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface-alt text-text-secondary font-bold uppercase tracking-wider text-[10px] border-b border-surface-border">
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
                <tbody className="divide-y divide-surface-border text-text font-medium">
                  {events.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-text-muted italic">
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
                          className={`hover:bg-brand/[0.04] cursor-pointer transition-colors ${
                            isSelected ? 'bg-brand/[0.06] font-semibold' : ''
                          }`}
                        >
                          <td className="px-3.5 py-2.5 font-mono text-[11px] text-text-muted">{evt.line_no}</td>
                          <td className="px-3.5 py-2.5 font-mono text-[11px] text-text-secondary whitespace-nowrap">
                            {evt.ts_utc ? evt.ts_utc.substring(0, 19).replace('T', ' ') : evt.ts_raw}
                          </td>
                          <td className="px-3.5 py-2.5">
                            <span className={`inline-block px-2 py-0.5 rounded text-[10px] border ${getLevelBadge(evt.level)}`}>
                              {evt.level}
                            </span>
                          </td>
                          <td className="px-3.5 py-2.5 font-semibold text-text truncate max-w-[120px]">
                            {evt.source_system}
                          </td>
                          <td className="px-3.5 py-2.5 text-text-secondary font-mono text-[11px] truncate max-w-[120px]">
                            {evt.component}
                          </td>
                          <td className="px-3.5 py-2.5 text-text truncate max-w-[280px]">
                            {evt.message}
                          </td>
                          <td className="px-3.5 py-2.5 text-right">
                            <button className="text-brand hover:text-brand-hover p-1">
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
            <div className="px-4 py-3 bg-surface-alt border-t border-surface-border flex items-center justify-between text-xs text-text-secondary">
              <div>
                Page <strong className="text-text">{page}</strong> of <strong className="text-text">{totalPages}</strong>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-2.5 py-1 bg-surface hover:bg-surface-alt disabled:opacity-40 border border-surface-border rounded font-bold text-text transition-all cursor-pointer"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-2.5 py-1 bg-surface hover:bg-surface-alt disabled:opacity-40 border border-surface-border rounded font-bold text-text transition-all cursor-pointer"
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
            <div className="bg-surface rounded-2xl border border-surface-border card-brand-glow p-5 space-y-4 sticky top-20 max-h-[85vh] overflow-y-auto">
              <div className="flex items-center justify-between border-b border-surface-border pb-3">
                <div className="flex items-center gap-2">
                  <FileCode className="w-5 h-5 text-brand" />
                  <h3 className="font-bold text-sm text-text">Log Event Inspector</h3>
                </div>
                <button
                  onClick={() => setSelectedEvent(null)}
                  className="text-xs font-bold text-text-muted hover:text-text-secondary p-1"
                >
                  ✕ Close
                </button>
              </div>

              {/* Header Badge & Level */}
              <div className="bg-surface-alt p-3 rounded-lg border border-surface-border space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className={`px-2 py-0.5 rounded text-[10px] border ${getLevelBadge(selectedEvent.level)}`}>
                    {selectedEvent.level}
                  </span>
                  <span className="text-[11px] font-mono text-text-muted">
                    Line {selectedEvent.line_no} ({selectedEvent.file_name})
                  </span>
                </div>
                <div className="text-xs font-bold text-text break-words font-mono">
                  {selectedEvent.message}
                </div>
              </div>

              {/* AI Root Cause Explainer Action */}
              <div className="bg-sidebar rounded-2xl p-4 text-white shadow-md space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 bg-brand rounded-full flex items-center justify-center font-bold text-xs">
                      🤖
                    </div>
                    <span className="text-xs font-bold tracking-wide">AI Root Cause Assistant</span>
                  </div>
                  <span className="text-[10px] text-brand-pressed font-semibold border border-brand-pressed/30 bg-brand-pressed/10 px-2 py-0.5 rounded-full">
                    Ollama
                  </span>
                </div>

                <p className="text-[11px] text-white/70 leading-normal">
                  Perform automated deep context analysis across surrounding log lines to pinpoint root causes and remediation steps.
                </p>

                <button
                  onClick={handleExplain}
                  disabled={isExplaining}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-brand hover:bg-brand-hover text-white font-bold text-xs rounded-lg transition-all cursor-pointer"
                >
                  <Sparkles className="w-4 h-4 text-brand-pressed" />
                  <span>{isExplaining ? 'Analyzing with Ollama...' : 'Explain Root Cause with AI'}</span>
                </button>

                {explainError && (
                  <div className="text-[11px] font-semibold text-error bg-error/20 border border-error/30 rounded-lg px-2.5 py-1.5">
                    {explainError}
                  </div>
                )}

                {aiExplanation && (
                  <div className="pt-3 border-t border-white/10 space-y-2.5 text-xs">
                    <div className="bg-white/10 p-2.5 rounded-lg border border-white/10">
                      <div className="text-[10px] font-bold text-warning uppercase tracking-wider mb-1">Probable Cause</div>
                      <div className="font-semibold text-white">{aiExplanation.probable_cause}</div>
                    </div>

                    <div className="space-y-1">
                      <div className="text-[10px] font-bold text-white/60 uppercase tracking-wider">Technical Explanation</div>
                      <div className="text-white/70 text-[11px] leading-relaxed bg-sidebar p-2.5 rounded border border-white/10">
                        {aiExplanation.explanation}
                      </div>
                    </div>

                    <div className="space-y-1">
                      <div className="text-[10px] font-bold text-success uppercase tracking-wider">Suggested Next Steps</div>
                      <ul className="space-y-1">
                        {aiExplanation.suggested_next_steps.map((step, idx) => (
                          <li key={idx} className="flex items-start gap-1.5 text-[11px] text-white/80 bg-white/5 p-1.5 rounded">
                            <span className="text-success font-bold">•</span>
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
                <div className="text-xs font-bold text-text mb-1">Parsed JSON Fields</div>
                <pre className="bg-sidebar text-white/80 text-[11px] p-3 rounded-lg overflow-x-auto font-mono max-h-40">
                  {JSON.stringify(selectedEvent, null, 2)}
                </pre>
              </div>

              {/* Surrounding Raw Log Context */}
              <div>
                <div className="text-xs font-bold text-text mb-1">
                  Surrounding Raw Log Context (±5 Lines)
                </div>
                <div className="bg-sidebar rounded-lg p-3 font-mono text-[11px] text-white/70 space-y-1.5 max-h-48 overflow-y-auto border border-white/10">
                  {contextEvents.map((ctx) => {
                    const isTarget = ctx.event_id === selectedEvent.event_id;
                    return (
                      <div
                        key={ctx.event_id}
                        className={`p-1.5 rounded text-[10px] leading-normal ${
                          isTarget ? 'bg-brand/20 text-brand-pressed border-l-2 border-brand-pressed font-bold' : 'hover:bg-white/5'
                        }`}
                      >
                        <span className="text-white/50 mr-2">Line {ctx.line_no}</span>
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
