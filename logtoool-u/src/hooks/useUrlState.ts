import { useCallback, useEffect, useState } from 'react';

function readParam(key: string, defaultValue: string): string {
  const params = new URLSearchParams(window.location.search);
  return params.get(key) ?? defaultValue;
}

function writeParam(key: string, value: string, defaultValue: string) {
  const params = new URLSearchParams(window.location.search);
  if (value === defaultValue || value === '') {
    params.delete(key); // keep the URL clean -- don't write out default/empty values
  } else {
    params.set(key, value);
  }
  const query = params.toString();
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
}

/**
 * Syncs a single piece of state to a URL search param, so the current
 * value survives a reload and can be shared by pasting the link. Uses
 * history.replaceState (not pushState) so every keystroke/filter tweak
 * doesn't spam the browser's back-button history -- that's reserved for
 * coarser navigation (see App.tsx's tab switching).
 */
export function useUrlState(key: string, defaultValue: string): [string, (value: string) => void] {
  const [value, setValueState] = useState<string>(() => readParam(key, defaultValue));

  useEffect(() => {
    const onPopState = () => setValueState(readParam(key, defaultValue));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [key, defaultValue]);

  const setValue = useCallback(
    (next: string) => {
      setValueState(next);
      writeParam(key, next, defaultValue);
    },
    [key, defaultValue]
  );

  return [value, setValue];
}
