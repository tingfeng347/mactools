import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { listen } from '@tauri-apps/api/event';
import { Eraser, Play, RotateCcw, X } from 'lucide-react';
import '@xterm/xterm/css/xterm.css';
import { api, newId } from '../lib/api';
import { useApp } from '../state';

const QUICK = [
  { label: 'ma --help', command: 'ma --help' },
  { label: 'maci', command: 'maci' },
  { label: 'macr', command: 'macr' },
  { label: 'macc', command: 'macc' },
  { label: 'maco', command: 'maco' },
  { label: 'ai status', command: 'ma ai status --all' },
];

type TerminalStatus = 'starting' | 'ready' | 'exited' | 'error';

function Session({
  sessionKey,
  writeRef,
  clearRef,
  onStatus,
}: {
  sessionKey: number;
  writeRef: React.MutableRefObject<((text: string) => void) | null>;
  clearRef: React.MutableRefObject<(() => void) | null>;
  onStatus: (status: TerminalStatus, detail?: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const idRef = useRef(newId());

  useEffect(() => {
    if (!host.current) return;
    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: 'bar',
      fontSize: 12.5,
      lineHeight: 1.28,
      fontFamily: '"SF Mono", "JetBrains Mono", Menlo, Monaco, monospace',
      scrollback: 8000,
      allowProposedApi: true,
      macOptionIsMeta: true,
      theme: {
        background: '#07080c',
        foreground: '#d6deee',
        cursor: '#8f7dff',
        cursorAccent: '#07080c',
        selectionBackground: 'rgba(139,123,255,0.32)',
        black: '#1a2030',
        red: '#ff7b93',
        green: '#5fe0b0',
        yellow: '#ffd479',
        blue: '#7aa2ff',
        magenta: '#c49bff',
        cyan: '#67e8f9',
        white: '#e6ecfb',
        brightBlack: '#4b556b',
        brightRed: '#ff9eb0',
        brightGreen: '#8ff0cd',
        brightYellow: '#ffe4a5',
        brightBlue: '#9dbaff',
        brightMagenta: '#dcc2ff',
        brightCyan: '#9beef7',
        brightWhite: '#ffffff',
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current);
    termRef.current = term;
    const id = idRef.current;
    let started = false;

    const start = () => {
      try {
        fit.fit();
      } catch {
        /* not laid out yet */
      }
      if (started) return;
      started = true;
      onStatus('starting');
      api
        .ptyStart(id, Math.max(20, term.cols), Math.max(5, term.rows))
        .then(() => {
          onStatus('ready');
          term.focus();
        })
        .catch((error) => {
          onStatus('error', String(error));
          term.writeln(`\x1b[31m${String(error)}\x1b[0m`);
        });
    };
    const timer = window.setTimeout(start, 80);

    const input = term.onData((data) => {
      void api.ptyWrite(id, data).catch(() => undefined);
    });
    writeRef.current = (text: string) => {
      void api.ptyWrite(id, `${text}\r`).catch(() => undefined);
      term.focus();
    };
    clearRef.current = () => term.clear();

    const resize = new ResizeObserver(() => {
      try {
        fit.fit();
        void api.ptyResize(id, term.cols, term.rows).catch(() => undefined);
      } catch {
        /* ignore transient layout errors */
      }
    });
    resize.observe(host.current);

    let disposed = false;
    const disposers: Array<() => void> = [];
    void listen<{ id: string; data: string }>('pty-output', (event) => {
      if (event.payload.id === id) term.write(event.payload.data);
    }).then((fn) => (disposed ? fn() : disposers.push(fn)));
    void listen<{ id: string; code: number | null }>('pty-exit', (event) => {
      if (event.payload.id !== id) return;
      const code = event.payload.code;
      onStatus('exited');
      term.writeln(`\r\n\x1b[2m—— 会话已结束${code === null ? '' : ` · 退出码 ${code}`} ——\x1b[0m`);
    }).then((fn) => (disposed ? fn() : disposers.push(fn)));

    return () => {
      disposed = true;
      window.clearTimeout(timer);
      input.dispose();
      resize.disconnect();
      disposers.forEach((fn) => fn());
      void api.ptyKill(id).catch(() => undefined);
      writeRef.current = null;
      clearRef.current = null;
      term.dispose();
      termRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);

  return <div className="terminal-host" ref={host} onClick={() => termRef.current?.focus()} />;
}

export function TerminalPanel({
  variant,
  onClose,
}: {
  variant: 'dock' | 'page';
  onClose?: () => void;
}) {
  const { terminalCommand } = useApp();
  const [sessionKey, setSessionKey] = useState(0);
  const [status, setStatus] = useState<TerminalStatus>('starting');
  const [detail, setDetail] = useState('');
  const lastNonce = useRef('');
  const writeRef = useRef<((text: string) => void) | null>(null);
  const clearRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!terminalCommand || terminalCommand.nonce === lastNonce.current) return;
    lastNonce.current = terminalCommand.nonce;
    let attempt = 0;
    const timer = window.setInterval(() => {
      attempt += 1;
      if (status === 'ready' && writeRef.current) {
        writeRef.current(terminalCommand.text);
        window.clearInterval(timer);
      } else if (attempt > 60) {
        window.clearInterval(timer);
      }
    }, 120);
    return () => window.clearInterval(timer);
  }, [terminalCommand, status]);

  return (
    <section className={`terminal-panel ${variant}`}>
      <div className="terminal-head">
        <div className="terminal-title">
          <span className={`status-dot ${status === 'ready' ? 'ok' : status === 'error' ? 'bad' : 'warn'}`} />
          <strong>mactools terminal</strong>
          <span className="terminal-state">
            {status === 'starting' && '正在启动…'}
            {status === 'ready' && '已连接 · 完整 TUI 可用'}
            {status === 'exited' && '会话已结束'}
            {status === 'error' && `启动失败：${detail}`}
          </span>
        </div>
        <div className="terminal-actions">
          <button onClick={() => clearRef.current?.()} title="清屏">
            <Eraser size={14} />
          </button>
          <button onClick={() => setSessionKey((key) => key + 1)} title="重启会话">
            <RotateCcw size={14} />
          </button>
          {onClose && (
            <button onClick={onClose} title="关闭终端">
              <X size={15} />
            </button>
          )}
        </div>
      </div>
      <div className="terminal-quick">
        {QUICK.map((item) => (
          <button key={item.command} onClick={() => writeRef.current?.(item.command)} title={`运行 ${item.command}`}>
            <Play size={10} />
            {item.label}
          </button>
        ))}
      </div>
      <div className="terminal-stage">
        <Session
          sessionKey={sessionKey}
          writeRef={writeRef}
          clearRef={clearRef}
          onStatus={(next, message) => {
            setStatus(next);
            setDetail(message ?? '');
          }}
        />
      </div>
    </section>
  );
}
