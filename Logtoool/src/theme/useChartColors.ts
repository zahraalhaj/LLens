import { useEffect, useState } from 'react';
import { useTheme } from './ThemeContext';

function readVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

interface ChartColors {
  surface: string;
  surfaceBorder: string;
  textSecondary: string;
  sevCritical: string;
  sevError: string;
  sevWarn: string;
  sevInfo: string;
  sevDebug: string;
  brand: string;
  success: string;
  warning: string;
  error: string;
}

function fetchColors(): ChartColors {
  return {
    surface: readVar('--color-surface') || '#fff',
    surfaceBorder: readVar('--color-surface-border') || '#E7E7E7',
    textSecondary: readVar('--color-text-secondary') || '#949799',
    sevCritical: readVar('--color-sev-critical') || '#FF2F2F',
    sevError: readVar('--color-sev-error') || '#FF8800',
    sevWarn: readVar('--color-sev-warn') || '#F8E71C',
    sevInfo: readVar('--color-sev-info') || '#2398C9',
    sevDebug: readVar('--color-sev-debug') || '#8892A1',
    brand: readVar('--color-brand') || '#052460',
    success: readVar('--color-success') || '#54C029',
    warning: readVar('--color-warning') || '#FF8800',
    error: readVar('--color-error') || '#FF2F2F',
  };
}

export function useChartColors(): ChartColors {
  const { theme } = useTheme();
  const [colors, setColors] = useState<ChartColors>(fetchColors);

  useEffect(() => {
    // Re-read after a short delay so the CSS class has been applied
    const id = requestAnimationFrame(() => setColors(fetchColors()));
    return () => cancelAnimationFrame(id);
  }, [theme]);

  return colors;
}
