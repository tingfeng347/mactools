import { useMemo, useState } from 'react';
import { Check, Gauge, Loader2, ShieldAlert, Sparkles, Square, TerminalSquare } from 'lucide-react';
import { OPTIMIZE_TASKS, type OptimizeTask } from '../data/catalog';
import { cn } from '../lib/format';
import type { RunRecord } from '../lib/types';
import { useApp } from '../state';
import { RunOutput } from '../components/RunOutput';
import { Section } from '../components/ui';

export function Optimize() {
  const { run, confirmAction, toast, sendTerminal } = useApp();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null);

  const selectedTasks = useMemo(
    () => OPTIMIZE_TASKS.filter((task) => selected.has(task.id)),
    [selected],
  );
  const adminCount = selectedTasks.filter((task) => task.admin).length;

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const preview = async () => {
    const tasks = selectedTasks.length ? selectedTasks : OPTIMIZE_TASKS;
    setBusy('preview');
    const record = await run(['optimize', ...tasks.map((task) => task.id), '--dry-run'], {
      label: selectedTasks.length ? '预览优化任务' : '预览全部优化任务',
      group: 'optimize',
    });
    setLastRun(record);
    setBusy(null);
  };

  const apply = async () => {
    if (selectedTasks.length === 0) {
      toast('请先选择要执行的优化任务', 'info');
      return;
    }
    const ok = await confirmAction({
      title: `执行 ${selectedTasks.length} 个优化任务？`,
      description:
        adminCount > 0
          ? `其中 ${adminCount} 个任务可能需要管理员授权。命令会在嵌入终端中执行，便于输入密码与查看实时进度。`
          : '任务会刷新系统缓存、状态或数据库。执行前建议先预览。',
      details: [
        `任务：${selectedTasks.map((task) => task.title).join('、')}`,
        `执行：ma optimize ${selectedTasks.map((task) => task.id).join(' ')} --yes`,
        '部分任务会按系统状态自动跳过，不会强制修改不适用的项目。',
      ],
      confirmLabel: '在终端中执行',
      danger: true,
    });
    if (!ok) return;
    setBusy('apply');
    sendTerminal(`ma optimize ${selectedTasks.map((task) => task.id).join(' ')} --yes`);
    setBusy(null);
    toast('已发送到交互终端', 'success');
  };

  return (
    <div className="page optimize-page">
      <div className="toolbar-row">
        <div className="toolbar-title">
          <span className="eyebrow">
            <Gauge size={14} />
            {selectedTasks.length} / {OPTIMIZE_TASKS.length} 已选择
          </span>
          <h2>刷新系统缓存与服务</h2>
        </div>
        <div className="toolbar-actions">
          <button
            className="btn subtle sm"
            onClick={() =>
              setSelected((current) =>
                current.size === OPTIMIZE_TASKS.length ? new Set() : new Set(OPTIMIZE_TASKS.map((task) => task.id)),
              )
            }
          >
            {selected.size === OPTIMIZE_TASKS.length ? <Check size={13} /> : <Square size={13} />}
            {selected.size === OPTIMIZE_TASKS.length ? '取消全选' : '全选'}
          </button>
          <button className="btn ghost sm" onClick={preview} disabled={busy !== null}>
            {busy === 'preview' ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
            {selectedTasks.length ? '预览所选' : '预览全部'}
          </button>
          <button className="btn primary sm" onClick={apply} disabled={busy !== null || selected.size === 0}>
            {busy === 'apply' ? <Loader2 size={13} className="spin" /> : <TerminalSquare size={13} />}
            执行所选
          </button>
        </div>
      </div>

      <div className="optimize-grid">
        {OPTIMIZE_TASKS.map((task: OptimizeTask) => {
          const active = selected.has(task.id);
          return (
            <button
              key={task.id}
              className={cn('optimize-card', active && 'active')}
              onClick={() => toggle(task.id)}
            >
              <span className={cn('optimize-check', active && 'on')}>
                {active && <Check size={12} />}
              </span>
              <span className="optimize-text">
                <strong>
                  {task.title}
                  {task.admin && (
                    <em className="admin-badge">
                      <ShieldAlert size={10} />
                      管理员
                    </em>
                  )}
                </strong>
                <span>{task.note}</span>
              </span>
              <code>{task.id}</code>
            </button>
          );
        })}
      </div>

      <div className="two-col">
        <Section title="预览输出" desc={lastRun ? lastRun.label : '选择任务后预览，查看实际会做什么'}>
          {lastRun ? (
            <RunOutput run={lastRun} />
          ) : (
            <div className="placeholder-panel">
              <Gauge size={26} />
              <p>未选择任务时，「预览全部」只读展示所有任务的计划。</p>
            </div>
          )}
        </Section>
        <Section title="执行方式" desc="复杂任务交给真实的交互终端">
          <ul className="check-list">
            <li>
              <strong>管理员任务</strong>
              <p>涉及 DNS、Spotlight、权限的任务会在终端中请求授权，密码不会经过 GUI。</p>
            </li>
            <li>
              <strong>按状态跳过</strong>
              <p>VPN 活动、应用运行中或系统不支持时，任务会自动跳过而不是强行执行。</p>
            </li>
            <li>
              <strong>完整 TUI</strong>
              <p>需要逐项选择时，可以直接运行 maco，体验与终端完全一致。</p>
            </li>
          </ul>
          <div className="inline-command">
            <code>maco</code>
            <button className="btn subtle sm" onClick={() => sendTerminal('maco')}>
              <TerminalSquare size={13} />
              打开交互优化
            </button>
          </div>
        </Section>
      </div>
    </div>
  );
}
