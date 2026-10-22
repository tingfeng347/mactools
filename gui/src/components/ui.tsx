import type { ReactNode } from 'react';
import { Inbox } from 'lucide-react';
import { appInitials, cn } from '../lib/format';

export function Section({
  title,
  desc,
  aside,
  children,
  className,
}: {
  title?: string;
  desc?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('card section', className)}>
      {(title || aside) && (
        <div className="section-head">
          <div>
            {title && <h2>{title}</h2>}
            {desc && <p>{desc}</p>}
          </div>
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented">
      {options.map((option) => (
        <button
          key={option.value}
          className={value === option.value ? 'active' : ''}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Inbox size={20} />
      </div>
      <strong>{title}</strong>
      {desc && <p>{desc}</p>}
    </div>
  );
}

const GRADIENTS = [
  ['#5b7cfa', '#8b5cf6'],
  ['#22d3ee', '#3b82f6'],
  ['#34d399', '#14b8a6'],
  ['#f59e0b', '#ef4444'],
  ['#ec4899', '#8b5cf6'],
  ['#60a5fa', '#22d3ee'],
];

function gradientFor(name: string): string {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) hash = (hash * 31 + name.charCodeAt(index)) >>> 0;
  const [from, to] = GRADIENTS[hash % GRADIENTS.length];
  return `linear-gradient(140deg, ${from}, ${to})`;
}

export function AppIcon({
  name,
  icon,
  size = 44,
  radius = 12,
}: {
  name: string;
  icon?: string | null;
  size?: number;
  radius?: number;
}) {
  if (icon) {
    return (
      <img
        className="app-icon"
        src={icon}
        width={size}
        height={size}
        alt=""
        style={{ borderRadius: radius }}
        draggable={false}
      />
    );
  }
  return (
    <div
      className="app-icon fallback"
      style={{ width: size, height: size, borderRadius: radius, background: gradientFor(name) }}
    >
      <span style={{ fontSize: size * 0.34 }}>{appInitials(name)}</span>
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}
