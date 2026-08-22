// Chart/SVG color anchors -- mirror the brand palette defined in index.css
// exactly. Recharts needs literal color values (SVG fill/stroke), so these
// can't reference Tailwind classes or CSS custom properties directly. Keep
// this in sync with the `@theme` block in index.css if the palette changes.
export const BRAND = {
  blue600: '#052460',
  blue500: '#036fd0',
  blue400: '#00aeef',
  slate900: '#15171a',
  slate700: '#2a2f34',
  slate500: '#8892a1',
  slate400: '#b2bbc2',
  slate200: '#d8dadd',
  slate100: '#e7e7e7',
  emerald600: '#45a21f',
  emerald500: '#54c029',
  rose600: '#e51f1f',
  rose500: '#ff2f2f',
  amber500: '#ff8800',
} as const;

export const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: BRAND.rose600,
  ERROR: BRAND.rose500,
  WARN: BRAND.amber500,
  INFO: BRAND.blue600,
  DEBUG: BRAND.slate500,
  UNKNOWN: BRAND.slate400,
};

export const SOURCE_BAR_COLORS = [BRAND.blue600, BRAND.blue500, BRAND.blue400, BRAND.slate500, BRAND.slate400];
