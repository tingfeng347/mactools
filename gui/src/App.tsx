import { useEffect, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { CommandConsole } from './components/CommandConsole';
import { ConfirmDialog } from './components/ConfirmDialog';
import { TerminalPanel } from './components/TerminalPanel';
import { Toasts } from './components/Toasts';
import { Overview } from './pages/Overview';
import { Install } from './pages/Install';
import { Uninstall } from './pages/Uninstall';
import { Update } from './pages/Update';
import { Clean } from './pages/Clean';
import { Optimize } from './pages/Optimize';
import { Mirror } from './pages/Mirror';
import { AiTools } from './pages/AiTools';
import { Doctor } from './pages/Doctor';
import { useApp } from './state';
import { cn } from './lib/format';
import type { PageId } from './lib/types';

function Shell() {
  const [page, setPage] = useState<PageId>('overview');
  const {
    consoleOpen,
    terminalOpen,
    setTerminalOpen,
    setConsoleOpen,
    sendTerminal,
  } = useApp();

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.metaKey && event.key.toLowerCase() === 'j') {
        event.preventDefault();
        setTerminalOpen(!terminalOpen);
      }
      if (event.metaKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setConsoleOpen(!consoleOpen);
      }
      if (event.key === 'Escape' && terminalOpen) setTerminalOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [terminalOpen, consoleOpen, setTerminalOpen, setConsoleOpen]);

  const content = (() => {
    switch (page) {
      case 'install':
        return <Install />;
      case 'uninstall':
        return <Uninstall />;
      case 'update':
        return <Update />;
      case 'clean':
        return <Clean />;
      case 'optimize':
        return <Optimize />;
      case 'mirror':
        return <Mirror />;
      case 'ai':
        return <AiTools />;
      case 'doctor':
        return <Doctor />;
      case 'terminal':
        return (
          <div className="page terminal-page">
            <div className="terminal-page-head">
              <div>
                <span className="eyebrow">完整 TUI · 真实 PTY</span>
                <h2>所有 mactools 交互都在这里</h2>
                <p>
                  嵌入终端运行的是同一个 <code>ma</code> 命令。maci、macr、macc、maco 的搜索、
                  多选、预览与确认流程与 Terminal.app 完全一致。
                </p>
              </div>
              <div className="terminal-tips">
                <span>⌘J 打开 / 收起</span>
                <span>⌘K 命令中心</span>
              </div>
            </div>
            <TerminalPanel variant="page" />
          </div>
        );
      default:
        return <Overview onNavigate={setPage} />;
    }
  })();

  return (
    <div className={cn('app', consoleOpen && 'console-open', terminalOpen && page !== 'terminal' && 'drawer-open')}>
      <div className="ambient">
        <span className="blob one" />
        <span className="blob two" />
        <span className="blob three" />
        <span className="grain" />
      </div>

      <Sidebar page={page} onNavigate={setPage} />

      <main className="main">
        <TopBar page={page} onOpenConsole={() => setConsoleOpen(true)} />
        <div className="content" key={page}>
          {content}
        </div>
      </main>

      <CommandConsole />

      {terminalOpen && page !== 'terminal' && (
        <>
          <div className="drawer-backdrop" onClick={() => setTerminalOpen(false)} />
          <div className="terminal-drawer">
            <TerminalPanel variant="dock" onClose={() => setTerminalOpen(false)} />
          </div>
        </>
      )}

      <ConfirmDialog />
      <Toasts />

      <button className="quick-terminal" onClick={() => sendTerminal('')} title="聚焦交互终端">
        <span>⌘J</span>
      </button>
    </div>
  );
}

export default function App() {
  return <Shell />;
}
