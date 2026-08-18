import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

export type ThemeId = 'default' | 'observatory';

interface ThemeContextValue {
  theme: ThemeId;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({ theme: 'default', toggle: () => {} });

const STORAGE_KEY = 'llens-theme';

function getInitialTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'observatory' || stored === 'default') return stored;
  } catch {}
  return 'default';
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<ThemeId>(getInitialTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'observatory') {
      root.classList.add('theme-observatory');
    } else {
      root.classList.remove('theme-observatory');
    }
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {}
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((prev) => (prev === 'default' ? 'observatory' : 'default'));
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
