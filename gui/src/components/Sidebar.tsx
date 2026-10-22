import {
  Bot,
  Gauge,
  LayoutDashboard,
  PackageMinus,
  PackagePlus,
  Radio,
  RefreshCw,
  Sparkles,
  Stethoscope,
  TerminalSquare,
} from 'lucide-react';
import { BrandMark } from './BrandMark';
import { PAGES } from '../lib/navigation';
import { cn } from '../lib/format';
import type { PageId } from '../lib/types';
import { useApp } from '../state';

const NAV: Array<{ id: PageId; icon: typeof LayoutDashboard; label: string }> = [
  { id: 'overview', icon: LayoutDashboard, label: '概览' },
  { id: 'install', icon: PackagePlus, label: '安装' },
  { id: 'uninstall', icon: PackageMinus, label: '卸载' },
  { id: 'update', icon: RefreshCw, label: '更新' },
  { id: 'clean', icon: Sparkles, label: '清理' },
  { id: 'optimize', icon: Gauge, label: '优化' },
  { id: 'mirror', icon: Radio, label: '镜像' },
  { id: 'ai', icon: Bot, label: 'AI 工具' },
  { id: 'doctor', icon: Stethoscope, label: '检查' },
  { id: 'terminal', icon: TerminalSquare, label: '终端' },
];

export function Sidebar({
  page,
  onNavigate,
}: {
  page: PageId;
  onNavigate: (page: PageId) => void;
}) {
  const { env, terminalOpen, setTerminalOpen } = useApp();
  const brewReady = Boolean(env?.brewPath);
  const cliReady = Boolean(env?.cliPath);

  return (
    <aside className="sidebar">
      <div className="sidebar-drag" data-tauri-drag-region />
      <button className="brand" onClick={() => onNavigate('overview')} title="mactools 概览">
        <BrandMark size={38} />
        <span className="brand-text">
          <strong>mactools</strong>
          <em>macOS toolbox</em>
        </span>
      </button>

      <nav className="nav">
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              className={cn('nav-item', page === item.id && 'active')}
              onClick={() => onNavigate(item.id)}
            >
              <span className="nav-icon">
                <Icon size={17} strokeWidth={1.9} />
              </span>
              <span>{item.label}</span>
              {page === item.id && <i className="nav-dot" />}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <button
          className={cn('mini-terminal', terminalOpen && 'active')}
          onClick={() => setTerminalOpen(!terminalOpen)}
          title="打开交互终端"
        >
          <TerminalSquare size={16} />
          <span>{terminalOpen ? '收起终端' : '交互终端'}</span>
          <kbd>⌘J</kbd>
        </button>
        <div className="status-stack">
          <div className="status-line">
            <span className={cn('status-dot', env?.cliOutdated ? 'warn' : cliReady ? 'ok' : 'bad')} />
            mactools {env?.cliVersion || (cliReady ? '' : '未安装')}
            {env?.cliOutdated && (
              <span className="status-note" title="运行 brew upgrade tingfeng347/tap/mactools 升级">
                需升级
              </span>
            )}
          </div>
          <div className="status-line">
            <span className={cn('status-dot', brewReady ? 'ok' : 'warn')} />
            {brewReady ? 'Homebrew 就绪' : '缺少 Homebrew'}
          </div>
        </div>
        <p className="sidebar-version">{PAGES[page].title} · v{env?.appVersion || '—'}</p>
      </div>
    </aside>
  );
}
