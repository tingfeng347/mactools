import { useMemo } from 'react';
import { Check, CircleX, Loader2, Maximize2 } from 'lucide-react';
import { cn, duration } from '../lib/format';
import type { RunRecord } from '../lib/types';
import { useApp } from '../state';

export function RunOutput({
  run,
  title = '执行输出',
  compact = false,
}: {
  run: RunRecord | null;
  title?: string;
  compact?: boolean;
}) {
  const { setConsoleOpen } = useApp();
  const lines = useMemo(() => {
    if (!run) return [];
    return run.lines.filter((line) => line.stream !== 'meta').slice(compact ? -14 : -60);
  }, [run, compact]);

  if (!run) return null;

  return (
    <div className={cn('run-output', compact && 'compact')}>
      <div className="run-output-head">
        <div className="run-output-title">
          {run.status === 'running' ? (
            <Loader2 size={14} className="spin" />
          ) : run.status === 'success' ? (
            <Check size={14} className="text-ok" />
          ) : (
            <CircleX size={14} className="text-bad" />
          )}
          <span>{title}</span>
          <em>{duration(run)}</em>
        </div>
        <button onClick={() => setConsoleOpen(true)} title="在命令中心展开">
          <Maximize2 size={13} />
        </button>
      </div>
      <div className="run-output-body">
        {lines.length === 0 && run.status === 'running' && <span className="muted">正在等待输出…</span>}
        {lines.map((line, index) => (
          <div key={`${line.at}-${index}`} className={cn('run-line', line.stream === 'stderr' && 'stderr')}>
            {line.text || '\u00a0'}
          </div>
        ))}
      </div>
    </div>
  );
}
