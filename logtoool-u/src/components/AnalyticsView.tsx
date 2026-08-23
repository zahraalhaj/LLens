import React, { useEffect, useState } from 'react';
import { LayoutDashboard, Network, Workflow, SearchCode, ShieldCheck, GitFork, type LucideIcon } from 'lucide-react';
import { ServiceOverviewView } from './ServiceOverviewView';
import { DependencyHealthView } from './DependencyHealthView';
import { QueueMessagingView } from './QueueMessagingView';
import { InvestigationView } from './InvestigationView';
import { SecurityQualityView } from './SecurityQualityView';
import { CorrelationExplorerView } from './CorrelationExplorerView';
import { DrillThroughTarget } from '../types';

type AnalyticsTab = 'overview' | 'dependencies' | 'queues' | 'investigation' | 'correlation' | 'security';

const TABS: { id: AnalyticsTab; label: string; icon: LucideIcon }[] = [
  { id: 'overview', label: 'Service Overview', icon: LayoutDashboard },
  { id: 'dependencies', label: 'Dependency Health', icon: Network },
  { id: 'queues', label: 'Queue & Messaging', icon: Workflow },
  { id: 'investigation', label: 'Investigation', icon: SearchCode },
  { id: 'correlation', label: 'Correlation Explorer', icon: GitFork },
  { id: 'security', label: 'Security / Data Quality', icon: ShieldCheck },
];

interface AnalyticsViewProps {
  // Lets a view OUTSIDE this component (e.g. AlertsView, which lives at the
  // top-level tab bar in App.tsx, not nested under Analytics) request a
  // drill-through into Investigation -- e.g. "View Investigation" on an
  // alert dispatch row. Consumed once via onConsumeExternalTarget, same
  // one-shot pattern InvestigationView's own pendingTarget already uses.
  externalTarget?: DrillThroughTarget | null;
  onConsumeExternalTarget?: () => void;
}

// Every chart in this dashboard is built on the trusted analytical model
// (backend/analysis/pipeline.py's AnalysisBundle -- normalized events +
// correlated flows + Phases 4-9), never raw log rows. "Drill through" means
// jumping to the Investigation tab pre-loaded with a specific flow_id --
// every chart element that carries a flow_id/tracker calls onInvestigate()
// with it, which is how every chart stays traceable back to a concrete
// analytical record instead of being a dead-end visualization.
export const AnalyticsView: React.FC<AnalyticsViewProps> = ({ externalTarget, onConsumeExternalTarget }) => {
  const [tab, setTab] = useState<AnalyticsTab>('overview');
  const [pendingTarget, setPendingTarget] = useState<DrillThroughTarget | null>(null);

  const onInvestigate = (target: DrillThroughTarget) => {
    setPendingTarget(target);
    setTab('investigation');
  };

  useEffect(() => {
    if (!externalTarget) return;
    onInvestigate(externalTarget);
    onConsumeExternalTarget?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalTarget]);

  return (
    <div className="space-y-6">
      <div className="bg-white border border-slate-200 rounded-xl shadow-2xs p-1.5 flex flex-wrap gap-1">
        {TABS.map((t) => {
          const Icon = t.icon;
          const isActive = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-bold transition-colors cursor-pointer ${
                isActive ? 'bg-blue-600 text-white shadow-2xs' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              <Icon className={`w-4 h-4 ${isActive ? 'text-white' : 'text-slate-400'}`} />
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === 'overview' && <ServiceOverviewView onInvestigate={onInvestigate} />}
      {tab === 'dependencies' && <DependencyHealthView onInvestigate={onInvestigate} />}
      {tab === 'queues' && <QueueMessagingView onInvestigate={onInvestigate} />}
      {tab === 'investigation' && (
        <InvestigationView pendingTarget={pendingTarget} onConsumePendingTarget={() => setPendingTarget(null)} />
      )}
      {tab === 'correlation' && <CorrelationExplorerView onInvestigate={onInvestigate} />}
      {tab === 'security' && <SecurityQualityView onInvestigate={onInvestigate} />}
    </div>
  );
};
