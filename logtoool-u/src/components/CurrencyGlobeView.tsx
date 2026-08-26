import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Globe, { type GlobeMethods } from 'react-globe.gl';
import { Globe2, RefreshCw, Info, Coins, DollarSign } from 'lucide-react';
import { api, ApiError } from '../api';
import { CurrencyGeoPoint, CurrencyMapSummary } from '../types';
import { DateRangeFilter, DateRangeValue, defaultRange, toIsoRange } from './DateRangeFilter';

const PALETTE = ['#036FD0', '#54C029', '#FF8800', '#CC1F1F', '#8A4FE0', '#04ADA4', '#FF4FA0', '#F2C744', '#3C4B72', '#00AEEF'];

// Point/ring radius scale -- sqrt so a 10x count difference reads as a
// clearly-bigger-but-not-absurd pin, not a globe-swallowing blob.
const scaleRadius = (count: number, maxCount: number) => 0.35 + 1.65 * Math.sqrt(count / Math.max(1, maxCount));

const GLOBE_IMAGE_URL = '//unpkg.com/three-globe/example/img/earth-night.jpg';
const BUMP_IMAGE_URL = '//unpkg.com/three-globe/example/img/earth-topology.png';
const BACKGROUND_IMAGE_URL = '//unpkg.com/three-globe/example/img/night-sky.png';

interface RenderPoint extends CurrencyGeoPoint {
  color: string;
}

function useContainerSize<T extends HTMLElement>() {
  const [size, setSize] = useState({ width: 0, height: 0 });
  // A callback ref, not useRef -- the container div this measures doesn't
  // exist on first mount (it's behind the loading-state conditional
  // render), so a plain useRef + useEffect([]) would fire once against a
  // still-null ref and never re-measure once the div actually appears.
  // The callback ref re-fires whenever the node itself changes.
  const [node, setNode] = useState<T | null>(null);
  const ref = useCallback((el: T | null) => setNode(el), []);

  useEffect(() => {
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        const { width, height } = entry.contentRect;
        setSize({ width, height });
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);

  return { ref, size };
}

export const CurrencyGlobeView: React.FC = () => {
  const [report, setReport] = useState<CurrencyMapSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DateRangeValue>(defaultRange());
  const [hovered, setHovered] = useState<RenderPoint | null>(null);
  const [selectedCurrency, setSelectedCurrency] = useState<string | null>(null);

  const globeRef = useRef<GlobeMethods | undefined>(undefined);
  const { ref: containerRef, size } = useContainerSize<HTMLDivElement>();

  const fetchReport = async (r: DateRangeValue) => {
    setLoading(true);
    setError(null);
    setSelectedCurrency(null);
    try {
      const { dateFrom, dateTo } = toIsoRange(r);
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      const data = await api.get<CurrencyMapSummary>(`/api/currency-map/summary?${params}`);
      setReport(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to load currency map data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReport(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  // Auto-rotate on load; a user drag (OrbitControls) naturally takes over
  // and the browser leaves rotation paused wherever they let go -- no need
  // to fight the interaction to keep it "eye-catching" on its own.
  useEffect(() => {
    const globe = globeRef.current;
    if (!globe) return;
    const controls = globe.controls();
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.6;
    controls.enableDamping = true;
  }, [report]);

  const points: RenderPoint[] = useMemo(() => {
    const raw = report?.points || [];
    return raw.map((p, i) => ({ ...p, color: PALETTE[i % PALETTE.length] }));
  }, [report]);

  const maxCount = useMemo(() => points.reduce((m, p) => Math.max(m, p.count), 1), [points]);

  // Clicking a currency in the side list spins the globe to face it --
  // clicking the already-selected one toggles back out to the overview and
  // lets auto-rotate resume, same as if nothing were selected.
  const handleSelectCurrency = useCallback(
    (point: RenderPoint) => {
      const globe = globeRef.current;
      if (selectedCurrency === point.currency) {
        setSelectedCurrency(null);
        if (globe) {
          globe.controls().autoRotate = true;
          globe.pointOfView({ altitude: 2.2 }, 1000);
        }
        return;
      }
      setSelectedCurrency(point.currency);
      if (globe) {
        globe.controls().autoRotate = false;
        globe.pointOfView({ lat: point.lat, lng: point.lng, altitude: 1.5 }, 1200);
      }
    },
    [selectedCurrency]
  );

  const header = (
    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white border border-slate-200 p-5 rounded-xl shadow-2xs">
      <div>
        <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <Globe2 className="w-5 h-5 text-blue-600" />
          Global Currency Map
        </h1>
        <p className="text-xs text-slate-500 mt-1">
          Transaction currency distribution by country, combined across Cardinal, VFlex, Debit Portal, OTP Processor, and AFS/Netcetera.
        </p>
      </div>
      <div className="flex items-end gap-3">
        <DateRangeFilter value={range} onChange={setRange} />
        <button
          onClick={() => fetchReport(range)}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 mb-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-bold transition-all cursor-pointer"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>
    </div>
  );

  if (loading && !report) {
    return (
      <div className="space-y-6">
        {header}
        <div className="flex flex-col items-center justify-center h-96 text-slate-400">
          <Globe2 className="w-10 h-10 animate-pulse text-blue-500 mb-3" />
          <p className="text-sm font-medium">Charting global currency activity…</p>
        </div>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="space-y-6">
        {header}
        <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-800">
          <p className="font-semibold text-sm">Failed to load currency map data</p>
          <p className="text-xs text-rose-600 mt-1">{error}</p>
          <button
            onClick={() => fetchReport(range)}
            className="mt-4 px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded text-xs font-semibold cursor-pointer"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (report.status !== 'ok') {
    return (
      <div className="space-y-6">
        {header}
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-8 text-center">
          <Info className="w-8 h-8 text-slate-400 mx-auto mb-2" />
          <p className="text-sm font-medium text-slate-500">{report.message || 'No currency activity found in this window.'}</p>
        </div>
      </div>
    );
  }

  const unmappedEntries = Object.entries(report.unmapped_currencies || {});

  return (
    <div className="space-y-6">
      {header}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Transactions Mapped</span>
            <DollarSign className="w-4 h-4 text-blue-600" />
          </div>
          <div className="text-2xl font-extrabold text-slate-900">{report.total_transactions}</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Distinct Currencies</span>
            <Coins className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-2xl font-extrabold text-slate-900">{report.distinct_currencies}</div>
        </div>
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-2xs">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-bold uppercase tracking-wider">Countries Plotted</span>
            <Globe2 className="w-4 h-4 text-amber-600" />
          </div>
          <div className="text-2xl font-extrabold text-slate-900">{points.length}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div
          ref={containerRef}
          className="lg:col-span-3 bg-[#020611] rounded-xl border border-slate-200 shadow-2xs overflow-hidden relative"
          style={{ height: 560 }}
        >
          {size.width > 0 && (
            <Globe
              ref={globeRef as any}
              width={size.width}
              height={size.height}
              globeImageUrl={GLOBE_IMAGE_URL}
              bumpImageUrl={BUMP_IMAGE_URL}
              backgroundImageUrl={BACKGROUND_IMAGE_URL}
              showAtmosphere
              atmosphereColor="#3C8CFF"
              atmosphereAltitude={0.22}
              pointsData={points}
              pointLat="lat"
              pointLng="lng"
              pointColor={(p: any) => ((p as RenderPoint).currency === selectedCurrency ? '#FFD23C' : (p as RenderPoint).color)}
              pointAltitude={(p: any) => ((p as RenderPoint).currency === selectedCurrency ? 0.025 : 0.01)}
              pointRadius={(p: any) => {
                const point = p as RenderPoint;
                const base = scaleRadius(point.count, maxCount) * 0.45;
                return point.currency === selectedCurrency ? base * 1.5 : base;
              }}
              pointLabel={(p: any) => {
                const point = p as RenderPoint;
                return `<div style="font-family: inherit; font-size: 12px; background: #15171A; color: #fff; padding: 6px 10px; border-radius: 6px;">
                  <b>${point.currency}</b> — ${point.country}<br/>${point.count.toLocaleString()} transaction${point.count === 1 ? '' : 's'}
                </div>`;
              }}
              onPointHover={(p: any) => setHovered((p as RenderPoint) || null)}
              onPointClick={(p: any) => handleSelectCurrency(p as RenderPoint)}
              ringsData={points}
              ringLat="lat"
              ringLng="lng"
              ringColor={(p: any) => {
                const isSelected = (p as RenderPoint).currency === selectedCurrency;
                return (t: number) => (isSelected ? `rgba(255, 210, 60, ${1 - t})` : `rgba(54, 162, 255, ${1 - t})`);
              }}
              ringMaxRadius={(p: any) => {
                const point = p as RenderPoint;
                const base = scaleRadius(point.count, maxCount) * 3.5;
                return point.currency === selectedCurrency ? base * 1.5 : base;
              }}
              ringPropagationSpeed={2}
              ringRepeatPeriod={(p: any) => {
                const point = p as RenderPoint;
                const period = 1800 - 1200 * (point.count / maxCount);
                return point.currency === selectedCurrency ? period * 0.55 : period;
              }}
              labelsData={points}
              labelLat="lat"
              labelLng="lng"
              labelText={(p: any) => (p as RenderPoint).currency}
              labelColor={() => 'rgba(255, 255, 255, 0.85)'}
              labelSize={(p: any) => 0.55 + 0.35 * Math.sqrt((p as RenderPoint).count / maxCount)}
              labelDotRadius={0}
              labelAltitude={0.012}
              labelResolution={2}
            />
          )}

          {hovered && (
            <div className="absolute bottom-4 left-4 bg-black/70 backdrop-blur-sm text-white text-xs rounded-lg px-3 py-2 pointer-events-none">
              <span className="font-bold">{hovered.currency}</span> — {hovered.country}
              <div className="text-white/70">{hovered.count.toLocaleString()} transaction{hovered.count === 1 ? '' : 's'}</div>
            </div>
          )}

          <div className="absolute top-3 right-3 text-[10px] text-white/50 bg-black/40 rounded px-2 py-1">
            Drag to rotate · scroll to zoom
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden flex flex-col">
          <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 font-bold text-xs text-slate-700 flex items-center gap-2">
            <Coins className="w-4 h-4 text-slate-500" />
            Currencies by Volume
          </div>
          <div className="overflow-y-auto max-h-[500px] divide-y divide-slate-100">
            {points.map((p) => {
              const isSelected = p.currency === selectedCurrency;
              return (
                <button
                  key={p.currency}
                  data-currency={p.currency}
                  onClick={() => handleSelectCurrency(p)}
                  className={`w-full px-4 py-2.5 flex items-center justify-between text-xs text-left transition-colors cursor-pointer ${
                    isSelected ? 'bg-amber-50' : 'hover:bg-slate-50'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ backgroundColor: isSelected ? '#FFD23C' : p.color }}
                    />
                    <div className="min-w-0">
                      <div className={`font-bold ${isSelected ? 'text-amber-700' : 'text-slate-800'}`}>{p.currency}</div>
                      <div className="text-slate-400 truncate">{p.country}</div>
                    </div>
                  </div>
                  <div className={`font-bold shrink-0 ml-2 ${isSelected ? 'text-amber-700' : 'text-slate-700'}`}>
                    {p.count.toLocaleString()}
                  </div>
                </button>
              );
            })}
          </div>

          {unmappedEntries.length > 0 && (
            <div className="px-4 py-3 border-t border-slate-200 bg-slate-50">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5">
                Not yet mapped to a country
              </div>
              <div className="space-y-1">
                {unmappedEntries.map(([code, count]) => (
                  <div key={code} className="flex justify-between text-xs text-slate-500">
                    <span className="font-mono">{code}</span>
                    <span>{count.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
