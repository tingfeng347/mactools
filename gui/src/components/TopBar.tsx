import { Command, HardDrive, TerminalSquare } from 'lucide-react';
import { formatBytes } from '../lib/format';
import { PAGES } from '../lib/navigation';
import type { PageId } from '../lib/types';
import { useApp } from '../state';

export function TopBar({
  page,
  onOpenConsole,
}: {
  page: PageId;
  onOpenConsole: () => void;
}) {
  const { env, setTerminalOpen, terminalOpen } = useApp();
  const free = env?.diskFree ?? 0;
  const total = env?.diskTotal ?? 0;
  const pct = total ? Math.round((free / total) * 100) : 0;

  return (
    <header className="topbar" data-tauri-drag-region>
      <div className="topbar-title">
        <h1>{PAGES[page].title}</h1>
        <p>{PAGES[page].sub}</p>
      </div>
      <div className="topbar-actions">
        <button className="pill clickable" onClick={onOpenConsole} title="打开命令中心">
          <Command size={13} />
          命令中心
        </button>
        <div className="pill" title={`${formatBytes(free)} 可用 / ${formatBytes(total)}`}>
          <HardDrive size={13} />
          可用 {formatBytes(free)}
          <span className="pill-muted">{pct}%</span>
        </div>
        <button
          className={`icon-button ${terminalOpen ? 'active' : ''}`}
          onClick={() => setTerminalOpen(!terminalOpen)}
          title="交互终端 (⌘J)"
        >
          <TerminalSquare size={17} />
        </button>
      </div>
    </header>
  );
}
