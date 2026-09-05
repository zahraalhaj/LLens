import React from 'react';
import { ShieldCheck } from 'lucide-react';

/**
 * Marks a surface whose sensitive values are masked at render time
 * (src/utils/maskSensitive.ts).
 *
 * This exists so masked output can't be misread as missing or corrupt data:
 * an analyst seeing `CIF=***21` needs to know the tool hid it, not that the
 * log was truncated or the parser failed. The tooltip says where the real
 * value still lives, since ingestion keeps it byte-for-byte.
 */
export const MaskedBadge: React.FC<{ className?: string }> = ({ className = '' }) => (
  <span
    title="Card numbers, OTPs, mobiles, emails, account/CIF numbers and secrets are masked for display. The stored log keeps the original value."
    className={`inline-flex items-center gap-1 text-[9px] font-bold uppercase tracking-wider text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-1.5 py-0.5 ${className}`}
  >
    <ShieldCheck className="w-2.5 h-2.5" />
    Masked
  </span>
);
