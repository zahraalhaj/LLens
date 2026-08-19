import React, { useState } from 'react';
import { Upload, AlertTriangle, ArrowRight, Zap, FolderOpen, Sparkles, Save, Loader2, FolderInput, CheckCircle2, XCircle } from 'lucide-react';
import { ParserProfile, IngestionSummary, LogEvent } from '../types';
import { api, ApiError } from '../api';

interface UploadViewProps {
  profiles: ParserProfile[];
  onIngestSuccess: () => void;
  isAdmin: boolean;
}

interface GeneratedProfileState {
  batchId: string;
  fileName: string;
  profile: ParserProfile | null;
  status: string;
  loading: boolean;
  saved: boolean;
  error: string | null;
}

interface DirectoryIngestResult {
  ingested: IngestionSummary[];
  errors: string[];
  skipped_unrecognized_extension: string[];
}

export const UploadView: React.FC<UploadViewProps> = ({ profiles, onIngestSuccess, isAdmin }) => {
  const [selectedProfile, setSelectedProfile] = useState<string>('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [progress, setProgress] = useState<number>(0);
  const [recentSummaries, setRecentSummaries] = useState<IngestionSummary[]>([]);
  const [dragActive, setDragActive] = useState<boolean>(false);
  const [genState, setGenState] = useState<GeneratedProfileState | null>(null);

  const [directoryPath, setDirectoryPath] = useState<string>('');
  const [directoryRecursive, setDirectoryRecursive] = useState<boolean>(true);
  const [isIngestingDirectory, setIsIngestingDirectory] = useState<boolean>(false);
  const [directoryResult, setDirectoryResult] = useState<DirectoryIngestResult | null>(null);
  const [directoryError, setDirectoryError] = useState<string | null>(null);


  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) setSelectedFiles(Array.from(e.target.files));
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true);
    else if (e.type === 'dragleave') setDragActive(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.[0]) setSelectedFiles(Array.from(e.dataTransfer.files));
  };

  const processFiles = async () => {
    if (selectedFiles.length === 0) return;
    setIsProcessing(true);
    setProgress(10);
    setGenState(null);

    const summaries: IngestionSummary[] = [];

    for (let i = 0; i < selectedFiles.length; i++) {
      const file = selectedFiles[i];
      try {
        const form = new FormData();
        form.append('file', file);
        const qs = selectedProfile ? `?profile_name=${encodeURIComponent(selectedProfile)}` : '';
        const summary = await api.postForm<IngestionSummary>(`/api/logs/upload${qs}`, form);
        summaries.push(summary);

        // Step 7: if the backend had to fall back to a weak-match profile
        // (surfaced via `warnings`), offer to generate a real one with AI
        // instead of leaving the user stuck with a poor match forever.
        if (summary.warnings.length > 0) {
          setGenState({
            batchId: summary.batch_id,
            fileName: summary.file_name,
            profile: null,
            status: '',
            loading: false,
            saved: false,
            error: null,
          });
        }
      } catch (err) {
        console.error(`Failed to upload file ${file.name}:`, err);
      }
      setProgress(Math.round(((i + 1) / selectedFiles.length) * 100));
    }

    setRecentSummaries(summaries);
    setIsProcessing(false);
    setSelectedFiles([]);
    onIngestSuccess();
  };

  const handleLoadSample = async () => {
    setIsProcessing(true);
    setProgress(30);
    try {
      await api.post('/api/logs/ingest-sample');
      setProgress(100);
      onIngestSuccess();
    } catch (err) {
      console.error('Failed to load sample logs:', err);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleIngestDirectory = async () => {
    if (!directoryPath.trim()) return;
    setIsIngestingDirectory(true);
    setDirectoryError(null);
    setDirectoryResult(null);
    try {
      const result = await api.post<DirectoryIngestResult>('/api/logs/ingest-directory', {
        path: directoryPath.trim(),
        recursive: directoryRecursive,
      });
      setDirectoryResult(result);
      if (result.ingested.length > 0) onIngestSuccess();
    } catch (err) {
      setDirectoryError(err instanceof ApiError ? err.detail : 'Failed to ingest from directory');
    } finally {
      setIsIngestingDirectory(false);
    }
  };

  const handleGenerateProfile = async () => {
    if (!genState) return;
    setGenState({ ...genState, loading: true, error: null });
    try {
      const events = await api.get<{ events: LogEvent[] }>(
        `/api/logs/events?batch_id=${genState.batchId}&pageSize=50`
      );
      const sampleLines = events.events.map((e) => e.raw);
      const result = await api.post<{ profile: ParserProfile; status: string }>('/api/profiles/generate', {
        sample_lines: sampleLines,
        suggested_name: `${genState.fileName} (AI-generated)`,
      });
      setGenState({ ...genState, loading: false, profile: result.profile, status: result.status });
    } catch (err) {
      setGenState({
        ...genState,
        loading: false,
        error: err instanceof ApiError ? err.detail : 'Profile generation failed',
      });
    }
  };

  const handleSaveGeneratedProfile = async () => {
    if (!genState?.profile) return;
    try {
      await api.post('/api/profiles', genState.profile);
      setGenState({ ...genState, saved: true });
    } catch (err) {
      setGenState({
        ...genState,
        error: err instanceof ApiError ? err.detail : 'Failed to save profile',
      });
    }
  };

  const totalLines = recentSummaries.reduce((acc, s) => acc + s.total_lines, 0);
  const totalGrouped = recentSummaries.reduce((acc, s) => acc + s.grouped_events_count, 0);
  const totalParsed = recentSummaries.reduce((acc, s) => acc + s.parsed_events_count, 0);
  const avgMatchRatio =
    recentSummaries.length > 0
      ? (recentSummaries.reduce((acc, s) => acc + s.match_ratio, 0) / recentSummaries.length) * 100
      : 0;

  return (
    <div className="space-y-6">
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs">
        <h2 className="text-xl font-bold text-slate-900 mb-1">Upload & Ingest Log Files</h2>
        <p className="text-xs text-slate-500 mb-6">
          Upload log files or load the bundled sample to run automated format detection, multiline
          grouping, and structured attribute extraction.
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-4">
            <div
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              className={`border-2 border-dashed rounded-xl p-8 text-center transition-all cursor-pointer ${
                dragActive ? 'border-blue-500 bg-blue-50/50' : 'border-slate-300 hover:border-slate-400 bg-slate-50/50'
              }`}
            >
              <div className="w-12 h-12 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center mx-auto mb-3">
                <Upload className="w-6 h-6" />
              </div>
              <h3 className="text-sm font-bold text-slate-800 mb-1">
                Drag & drop log file(s) here, or click to browse
              </h3>
              <p className="text-xs text-slate-500 mb-4">
                Supported formats: <span className="font-mono font-medium text-slate-700">.log, .txt, .json, .jsonl, .csv, .tsv</span>
              </p>

              <label className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-lg shadow-2xs transition-all cursor-pointer">
                <FolderOpen className="w-4 h-4" />
                <span>Select Log File(s)</span>
                <input
                  type="file"
                  multiple
                  accept=".log,.txt,.json,.jsonl,.csv,.tsv"
                  onChange={handleFileChange}
                  className="hidden"
                />
              </label>
            </div>

            {selectedFiles.length > 0 && (
              <div className="bg-slate-50 p-4 rounded-lg border border-slate-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-bold text-slate-800">Selected Files ({selectedFiles.length}):</div>
                  <button
                    onClick={() => setSelectedFiles([])}
                    className="text-[11px] text-rose-600 font-semibold hover:underline cursor-pointer"
                  >
                    Clear Selection
                  </button>
                </div>
                <div className="max-h-32 overflow-y-auto space-y-1">
                  {selectedFiles.map((f, idx) => (
                    <div key={idx} className="flex items-center justify-between text-xs bg-white px-3 py-1.5 rounded border border-slate-200">
                      <span className="font-medium text-slate-700 truncate max-w-md">{f.name}</span>
                      <span className="text-[10px] text-slate-400 font-mono font-medium">{(f.size / 1024).toFixed(1)} KB</span>
                    </div>
                  ))}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-200">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-slate-600">Parser Profile:</span>
                    <select
                      value={selectedProfile}
                      onChange={(e) => setSelectedProfile(e.target.value)}
                      className="text-xs font-medium bg-white border border-slate-300 rounded-md px-2.5 py-1 text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">Auto-Detect Best Match</option>
                      {profiles.map((p) => (
                        <option key={p.name} value={p.name}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <button
                    onClick={processFiles}
                    disabled={isProcessing}
                    className="flex items-center gap-2 px-5 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-xs font-bold rounded-lg shadow-sm transition-all cursor-pointer"
                  >
                    <Zap className="w-4 h-4" />
                    <span>{isProcessing ? 'Ingesting...' : 'Process & Ingest Files'}</span>
                  </button>
                </div>
              </div>
            )}

            {isProcessing && (
              <div className="space-y-1">
                <div className="flex justify-between text-xs font-bold text-slate-600">
                  <span>Ingesting and parsing log records...</span>
                  <span>{progress}%</span>
                </div>
                <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                  <div className="bg-blue-600 h-2 rounded-full transition-all duration-300" style={{ width: `${progress}%` }}></div>
                </div>
              </div>
            )}

            {genState && (
              <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-3">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                  <div className="text-xs text-amber-900">
                    <strong>{genState.fileName}</strong> didn't match any known format well, so it was parsed
                    with a best-effort fallback profile. You can generate a custom profile for this format with AI.
                  </div>
                </div>

                {!genState.profile && (
                  <button
                    onClick={handleGenerateProfile}
                    disabled={genState.loading}
                    className="flex items-center gap-2 px-4 py-2 bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white text-xs font-bold rounded-lg transition-all cursor-pointer"
                  >
                    {genState.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                    <span>{genState.loading ? 'Generating with Ollama…' : 'Generate profile with AI'}</span>
                  </button>
                )}

                {genState.profile && !genState.saved && (
                  <div className="space-y-2">
                    <div className="text-xs font-semibold text-emerald-700">{genState.status}</div>
                    <pre className="text-[10px] bg-white border border-slate-200 rounded-lg p-3 overflow-x-auto max-h-48">
                      {JSON.stringify(genState.profile, null, 2)}
                    </pre>
                    <button
                      onClick={handleSaveGeneratedProfile}
                      className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold rounded-lg transition-all cursor-pointer"
                    >
                      <Save className="w-4 h-4" />
                      <span>Save profile for future uploads</span>
                    </button>
                  </div>
                )}

                {genState.saved && (
                  <div className="text-xs font-semibold text-emerald-700">
                    Profile saved. Future uploads of this format will auto-match.
                  </div>
                )}

                {genState.error && <div className="text-xs font-semibold text-rose-700">{genState.error}</div>}
              </div>
            )}
          </div>

          <div className="bg-slate-50 p-5 rounded-xl border border-slate-200 flex flex-col justify-between">
            <div>
              <div className="flex items-center gap-2 text-xs font-bold text-slate-800 uppercase tracking-wide mb-3">
                <Zap className="w-4 h-4 text-amber-500" />
                <span>Bundled Sample Logs</span>
              </div>
              <p className="text-xs text-slate-600 mb-4 leading-relaxed">
                Try the ingestion pipeline immediately with a bundled sample log.
              </p>
              <button
                onClick={handleLoadSample}
                className="w-full flex items-center justify-between p-3 bg-white hover:bg-blue-50 border border-slate-200 hover:border-blue-300 rounded-lg text-left transition-all cursor-pointer group"
              >
                <div className="text-xs font-bold text-slate-800 group-hover:text-blue-600">Load sample logs</div>
                <ArrowRight className="w-4 h-4 text-slate-400 group-hover:text-blue-600" />
              </button>
            </div>

            <div className="mt-6 pt-4 border-t border-slate-200 text-[11px] text-slate-500 leading-normal">
              <strong>Tip:</strong> Multiline stack traces are automatically coalesced into single logical events.
            </div>
          </div>
        </div>
      </div>

      {isAdmin && (
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-2xs space-y-4">
          <div className="flex items-center gap-2">
            <FolderInput className="w-5 h-5 text-blue-600" />
            <h3 className="text-base font-bold text-slate-900">Ingest From a Server Directory</h3>
          </div>
          <p className="text-xs text-slate-500">
            Point this at a directory the server can read -- every recognized log file inside is detected
            and ingested automatically, no browser upload needed. Admin-only, since this lets the app read
            server-local paths rather than only what you've selected in the browser.
          </p>

          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-64">
              <label className="block text-[11px] font-bold text-slate-600 mb-1">Directory path</label>
              <input
                type="text"
                value={directoryPath}
                onChange={(e) => setDirectoryPath(e.target.value)}
                placeholder="/var/log/myapp or C:\\logs\\myapp"
                className="w-full text-xs font-mono bg-white border border-slate-300 rounded-md px-3 py-2 text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 pb-2">
              <input
                type="checkbox"
                checked={directoryRecursive}
                onChange={(e) => setDirectoryRecursive(e.target.checked)}
                className="rounded border-slate-300"
              />
              Include subdirectories
            </label>
            <button
              onClick={handleIngestDirectory}
              disabled={isIngestingDirectory || !directoryPath.trim()}
              className="flex items-center gap-2 px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-bold rounded-lg shadow-sm transition-all cursor-pointer"
            >
              {isIngestingDirectory ? <Loader2 className="w-4 h-4 animate-spin" /> : <FolderInput className="w-4 h-4" />}
              <span>{isIngestingDirectory ? 'Scanning & ingesting…' : 'Ingest directory'}</span>
            </button>
          </div>

          {directoryError && (
            <div className="flex items-center gap-2 text-xs font-semibold text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3.5 py-2.5">
              <XCircle className="w-4 h-4 shrink-0" />
              {directoryError}
            </div>
          )}

          {directoryResult && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3.5 py-2.5">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                Ingested {directoryResult.ingested.length} file(s)
                {directoryResult.skipped_unrecognized_extension.length > 0 &&
                  ` -- skipped ${directoryResult.skipped_unrecognized_extension.length} file(s) with an unrecognized extension`}
                {directoryResult.errors.length > 0 && ` -- ${directoryResult.errors.length} error(s)`}
              </div>
              {directoryResult.errors.length > 0 && (
                <div className="text-[11px] font-mono bg-rose-50 border border-rose-200 rounded-lg p-3 space-y-1 max-h-32 overflow-y-auto">
                  {directoryResult.errors.map((e, i) => (
                    <div key={i} className="text-rose-700">{e}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {recentSummaries.length > 0 && (
        <div className="space-y-4">
          <h3 className="text-base font-bold text-slate-900">Batch Ingestion Metrics</h3>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
              <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Total Lines Read</div>
              <div className="text-2xl font-extrabold text-slate-900">{totalLines.toLocaleString()}</div>
            </div>
            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
              <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Grouped Events</div>
              <div className="text-2xl font-extrabold text-slate-900">{totalGrouped.toLocaleString()}</div>
            </div>
            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
              <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Parsed Events</div>
              <div className="text-2xl font-extrabold text-blue-600">{totalParsed.toLocaleString()}</div>
            </div>
            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
              <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">Avg Match Ratio</div>
              <div className="text-2xl font-extrabold text-emerald-600">{avgMatchRatio.toFixed(1)}%</div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
            <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700">
              Ingested File Batch Summary
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-100 text-slate-600 font-bold uppercase tracking-wider text-[10px] border-b border-slate-200">
                  <tr>
                    <th className="px-4 py-2.5">Batch ID</th>
                    <th className="px-4 py-2.5">File Name</th>
                    <th className="px-4 py-2.5">Total Lines</th>
                    <th className="px-4 py-2.5">Grouped Events</th>
                    <th className="px-4 py-2.5">Parsed Events</th>
                    <th className="px-4 py-2.5">Matched Profile</th>
                    <th className="px-4 py-2.5">Match Ratio</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 text-slate-800 font-medium">
                  {recentSummaries.map((s) => (
                    <tr key={s.batch_id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 font-mono text-slate-500">{s.batch_id.substring(0, 8)}</td>
                      <td className="px-4 py-3 font-bold text-slate-900">{s.file_name}</td>
                      <td className="px-4 py-3">{s.total_lines}</td>
                      <td className="px-4 py-3">{s.grouped_events_count}</td>
                      <td className="px-4 py-3 font-bold text-blue-600">{s.parsed_events_count}</td>
                      <td className="px-4 py-3 font-medium text-slate-600">{s.matched_profile}</td>
                      <td className="px-4 py-3 font-bold text-emerald-600">{(s.match_ratio * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
