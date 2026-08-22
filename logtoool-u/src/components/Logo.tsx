import React from 'react';

// Log lines flowing into a bracket -- LLens' mark. Built as inline SVG using
// the brand tokens directly (blue-400 accent, slate-900/white bars) rather
// than a raster asset, so it stays crisp at any size and in both the dark
// sidebar and light navbar without needing separate exports.
export const LogoMark: React.FC<{ className?: string; barColor?: string; accentColor?: string }> = ({
  className,
  barColor = '#15171A',
  accentColor = '#00AEEF',
}) => (
  <svg viewBox="0 0 40 32" className={className} fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="0" y="4" width="4" height="4" rx="1.5" fill={barColor} />
    <rect x="7" y="4" width="16" height="4" rx="1.5" fill={barColor} />
    <rect x="3" y="12" width="4" height="4" rx="1.5" fill={barColor} opacity="0.7" />
    <rect x="10" y="12" width="13" height="4" rx="1.5" fill={barColor} opacity="0.7" />
    <rect x="0" y="20" width="4" height="4" rx="1.5" fill={barColor} opacity="0.85" />
    <rect x="7" y="20" width="16" height="4" rx="1.5" fill={barColor} opacity="0.85" />
    <rect x="3" y="28" width="4" height="0" rx="1.5" fill={barColor} />
    <path
      d="M18 2 L26 2 C27.1 2 28 2.9 28 4 L28 12 L34 16 L28 20 L28 28 C28 29.1 27.1 30 26 30 L18 30"
      stroke={accentColor}
      strokeWidth="4"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
  </svg>
);

interface LogoProps {
  variant?: 'dark' | 'light';
  className?: string;
  markClassName?: string;
}

// Full lockup: mark + "LLens" wordmark. `variant="dark"` is for placement on
// dark surfaces (sidebar); `variant="light"` for white surfaces (navbar,
// login).
export const Logo: React.FC<LogoProps> = ({ variant = 'light', className = '', markClassName = 'w-8 h-6' }) => {
  const textColor = variant === 'dark' ? 'text-white' : 'text-slate-900';
  const barColor = variant === 'dark' ? '#FFFFFF' : '#15171A';

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <LogoMark className={markClassName} barColor={barColor} accentColor="#00AEEF" />
      <span className={`font-extrabold tracking-tight text-lg ${textColor}`}>
        LL<span className="lowercase">ens</span>
      </span>
    </div>
  );
};
