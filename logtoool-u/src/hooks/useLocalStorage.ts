import { useCallback, useState } from 'react';

function readStorage<T>(key: string, defaultValue: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : defaultValue;
  } catch {
    return defaultValue; // private-mode/quota errors, or corrupt stored JSON
  }
}

/**
 * Simple state hook synced to localStorage, keyed by `key`. For
 * lightweight per-browser preferences (e.g. Explore's saved searches)
 * that don't need a backend table or to be shared across devices.
 */
export function useLocalStorage<T>(key: string, defaultValue: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValueState] = useState<T>(() => readStorage(key, defaultValue));

  const setValue = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValueState((prev) => {
        const resolved = typeof next === 'function' ? (next as (prev: T) => T)(prev) : next;
        try {
          window.localStorage.setItem(key, JSON.stringify(resolved));
        } catch {
          // Storage unavailable -- state still updates in-memory, it just
          // won't persist across reloads.
        }
        return resolved;
      });
    },
    [key]
  );

  return [value, setValue];
}
