import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { AlertTriangle, HelpCircle } from 'lucide-react';

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn>(() => Promise.resolve(false));

export function useConfirm(): ConfirmFn {
  return useContext(ConfirmContext);
}

interface PendingConfirm {
  options: ConfirmOptions;
  resolve: (result: boolean) => void;
}

export const ConfirmProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => setPending({ options, resolve }));
  }, []);

  const settle = (result: boolean) => {
    pending?.resolve(result);
    setPending(null);
  };

  useEffect(() => {
    if (!pending) return;
    confirmButtonRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') settle(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-[2px]" onClick={() => settle(false)} />
          <div
            role="alertdialog"
            aria-modal="true"
            className="relative bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-sm p-5"
          >
            <div className="flex items-start gap-3">
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${
                  pending.options.destructive ? 'bg-rose-50' : 'bg-blue-50'
                }`}
              >
                {pending.options.destructive ? (
                  <AlertTriangle className="w-5 h-5 text-rose-600" />
                ) : (
                  <HelpCircle className="w-5 h-5 text-blue-600" />
                )}
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-bold text-slate-900">{pending.options.title || 'Please confirm'}</h2>
                <p className="text-xs text-slate-600 mt-1 leading-relaxed">{pending.options.message}</p>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 mt-5">
              <button
                onClick={() => settle(false)}
                className="px-3.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-md transition-colors cursor-pointer"
              >
                {pending.options.cancelLabel || 'Cancel'}
              </button>
              <button
                ref={confirmButtonRef}
                onClick={() => settle(true)}
                className={`px-3.5 py-1.5 text-xs font-semibold text-white rounded-md transition-colors cursor-pointer ${
                  pending.options.destructive ? 'bg-rose-600 hover:bg-rose-700' : 'bg-blue-600 hover:bg-blue-500'
                }`}
              >
                {pending.options.confirmLabel || (pending.options.destructive ? 'Delete' : 'Confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
};
