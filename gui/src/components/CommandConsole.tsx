import { useEffect, useRef } from 'react';
import { Check, ChevronDown, CircleX, Copy, Loader2, Terminal, X } from 'lucide-react';
import { cn, duration, formatTime } from '../lib/format';
import { useApp } from '../state';

export function CommandConsole() {
  const { activeRun, consoleOpen, setConsoleOpen, toast } = useApp();
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (consoleOpen && scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight;
    }
  }, [activeRun?.lines.length, consoleOpen]);

  if (!consoleOpen) return null;

  const copy = async () => {
    if (!activeRun) return;
    const text = activeRun.lines.map((line) => line.text).join('\n');
    try {
      await navigator.clipboard.writeText(text);
      toast('命令输出已复制', 'success');
    } catch {
      toast('复制失败', 'error');
    }
  };

  return (
    <section className="console">
      <div className="console-head">
        <div className="console-run">
          {activeRun?.status === 'running' && <Loader2 size={14} className="spin" />}
          {activeRun?.status === 'success' && <Check size={14} className="text-ok" />}
          {activeRun?.status === 'error' && <CircleX size={14} className="text-bad" />}
          <Terminal size={14} />
          <strong>{activeRun?.label ?? '命令中心'}</strong>
          {activeRun && (
            <span className="console-meta">
              {formatTime(activeRun.startedAt)} · {duration(activeRun)}
              {activeRun.code !== null && activeRun.code !== 0 && ` · 退出码 ${activeRun.code}`}
            </span>
          )}
        </div>
        <div className="console-actions">
          <button onClick={copy} title="复制输出">
            <Copy size={14} />
          </button>
          <button onClick={() => setConsoleOpen(false)} title="收起">
            <ChevronDown size={15} />
          </button>
          <button onClick={() => setConsoleOpen(false)} title="关闭">
            <X size={15} />
          </button>
        </div>
      </div>
      <div className="console-body" ref={scroller}>
        {!activeRun && <div className="console-empty">运行命令后，实时输出会显示在这里。</div>}
        {activeRun?.lines.map((line, index) => (
          <div
            key={`${line.at}-${index}`}
            className={cn(
              'console-line',
              line.stream === 'stderr' && 'stderr',
              line.stream === 'meta' && 'meta',
            )}
          >
            <span className="console-gutter">{line.stream === 'stderr' ? '!' : line.stream === 'meta' ? '$' : '›'}</span>
            <span>{line.text || '\u00a0'}</span>
          </div>
        ))}
        {activeRun?.status === 'running' && <div className="console-cursor">▍</div>}
      </div>
    </section>
  );
}
