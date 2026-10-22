export function BrandMark({ size = 40, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 256 256"
      role="img"
      aria-label="mactools"
    >
      <defs>
        <linearGradient id="brand-tile" x1="24" y1="16" x2="232" y2="240" gradientUnits="userSpaceOnUse">
          <stop stopColor="#1d2b44" />
          <stop offset="1" stopColor="#0d1522" />
        </linearGradient>
        <linearGradient id="brand-m" x1="57" y1="85" x2="159" y2="171" gradientUnits="userSpaceOnUse">
          <stop stopColor="#8ec5ff" />
          <stop offset="1" stopColor="#6f8cff" />
        </linearGradient>
      </defs>
      <rect x="4" y="4" width="248" height="248" rx="56" fill="url(#brand-tile)" stroke="rgba(154,183,255,0.22)" strokeWidth="2" />
      <path
        d="M57 171V85L108 137L159 85V171"
        fill="none"
        stroke="url(#brand-m)"
        strokeWidth="17"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <path d="M184 171H212" fill="none" stroke="#62dec3" strokeWidth="17" strokeLinecap="round" />
    </svg>
  );
}
