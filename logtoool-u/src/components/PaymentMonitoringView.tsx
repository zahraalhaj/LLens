import React, { useState } from 'react';
import { Activity, KeyRound, CreditCard, ShieldQuestion, Smartphone, type LucideIcon } from 'lucide-react';
import { VPlusMonitoringView } from './VPlusMonitoringView';
import { OtpProcessorView } from './OtpProcessorView';
import { DebitPortalView } from './DebitPortalView';
import { CardinalView } from './CardinalView';
import { VFlexView } from './VFlexView';

type PaymentTab = 'vplus' | 'otp-processor' | 'debit-portal' | 'cardinal' | 'vflex';

const TABS: { id: PaymentTab; label: string; icon: LucideIcon }[] = [
  { id: 'vplus', label: 'V+ Monitoring', icon: Activity },
  { id: 'otp-processor', label: 'OTP Processor', icon: KeyRound },
  { id: 'debit-portal', label: 'Debit Portal', icon: CreditCard },
  { id: 'cardinal', label: 'Cardinal', icon: ShieldQuestion },
  { id: 'vflex', label: 'VFlex', icon: Smartphone },
];

export const PaymentMonitoringView: React.FC = () => {
  const [tab, setTab] = useState<PaymentTab>('vplus');

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

      {tab === 'vplus' && <VPlusMonitoringView />}
      {tab === 'otp-processor' && <OtpProcessorView />}
      {tab === 'debit-portal' && <DebitPortalView />}
      {tab === 'cardinal' && <CardinalView />}
      {tab === 'vflex' && <VFlexView />}
    </div>
  );
};
