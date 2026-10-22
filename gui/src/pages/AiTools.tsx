import { useEffect, useMemo, useState } from 'react';
import {
  ArrowUpCircle,
  Bot,
  Check,
  Download,
  Loader2,
  RefreshCw,
  TerminalSquare,
  Trash2,
} from 'lucide-react';
import { AI_TOOLS, type AiTool } from '../data/catalog';
import { cn } from '../lib/format';
import type { RunRecord } from '../lib/types';
import { useApp } from '../state';
import { RunOutput } from '../components/RunOutput';
import { Section } from '../components/ui';

type ToolState = {
  installed: boolean;
  version: string;
  method: string;
  path: string;
  count: number;
};

function parseStatus(record: RunRecord): Record<string, ToolState> {
  const states: Record<string, ToolState> = {};
  const counts: Record<string, number> = {};
  const lines = record.lines.filter((line) => line.stream === 'stdout').map((line) => line.text);
  for (const line of lines) {
    const multi = line.match(/^(\w+):\s*检测到\s*(\d+)\s*处安装/);
    if (multi) counts[multi[1]] = Number(multi[2]);
  }
  for (const line of lines) {
    const parts = line.trim().split(/\s{2,}/);
    if (parts.length < 2) continue;
    const [name, state, version, method, path] = parts;
    if (!AI_TOOLS.some((tool) => tool.id === name)) continue;
    const installed = state.includes('已安装');
    if (!installed && states[name]) continue;
    states[name] = {
      installed,
      version: version && version !== '-' ? version : '',
      method: method && method !== 'missing' ? method : '',
      path: path ?? '',
      count: counts[name] ?? (installed ? 1 : 0),
    };
  }
  return states;
}

export function AiTools() {
  const { env, run, confirmAction, toast, sendTerminal } = useApp();
  const [states, setStates] = useState<Record<string, ToolState>>({});
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async (notify = false) => {
    setBusy('status');
    const record = await run(['ai', 'status', '--all'], {
      label: '检查 AI 工具状态',
      group: 'ai',
      background: true,
    });
    setLastRun(record);
    setStates(parseStatus(record));
    setLoaded(true);
    setBusy(null);
    if (notify) toast('AI 工具状态已刷新', 'success');
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const installedCount = useMemo(() => Object.values(states).filter((state) => state.installed).length, [states]);

  const action = async (tool: AiTool, kind: 'install' | 'update' | 'remove') => {
    const current = states[tool.id];
    if (!env?.cliPath) {
      toast('未找到 mactools，无法执行', 'error');
      return;
    }
    if (kind === 'remove') {
      const ok = await confirmAction({
        title: `卸载 ${tool.name}？`,
        description: '会移除检测到的安装，但保留配置与登录数据。存在多处安装时可能需要指定路径。',
        details: [
          `执行：ma ai remove ${tool.id} --yes`,
          current?.path ? `安装路径：${current.path}` : '',
        ].filter(Boolean),
        confirmLabel: '卸载',
        danger: true,
      });
      if (!ok) return;
    }
    setBusy(`${kind}:${tool.id}`);
    const record = await run(['ai', kind, tool.id, '--yes'], {
      label: `${kind === 'install' ? '安装' : kind === 'update' ? '更新' : '卸载'} ${tool.name}`,
      group: 'ai',
    });
    setLastRun(record);
    setBusy(null);
    if (record.status === 'success') {
      toast(`${tool.name} ${kind === 'remove' ? '已卸载' : kind === 'install' ? '安装完成' : '更新完成'}`, 'success');
      await refresh();
    } else {
      toast(`${tool.name} 操作未完成，可以尝试嵌入终端`, 'error');
    }
  };

  const checkAll = async () => {
    setBusy('check');
    const record = await run(['ai', 'update', '--all', '--check'], {
      label: '检查 AI 工具更新',
      group: 'ai',
    });
    setLastRun(record);
    setBusy(null);
  };

  return (
    <div className="page ai-page">
      <div className="toolbar-row">
        <div className="toolbar-title">
          <span className="eyebrow">
            <Bot size={14} />
            {loaded ? `${installedCount} / ${AI_TOOLS.length} 已安装` : '正在检测安装状态'}
          </span>
          <h2>终端里的 AI 编程搭档</h2>
        </div>
        <div className="toolbar-actions">
          <button className="btn ghost sm" onClick={() => void refresh(true)} disabled={busy !== null}>
            {busy === 'status' ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
            刷新状态
          </button>
          <button className="btn subtle sm" onClick={checkAll} disabled={busy !== null}>
            {busy === 'check' ? <Loader2 size={13} className="spin" /> : <ArrowUpCircle size={13} />}
            检查更新
          </button>
          <button className="btn primary sm" onClick={() => sendTerminal('ma ai status --all')}>
            <TerminalSquare size={13} />
            打开交互管理
          </button>
        </div>
      </div>

      <div className="ai-grid">
        {AI_TOOLS.map((tool) => {
          const state = states[tool.id];
          const installed = Boolean(state?.installed);
          return (
            <div key={tool.id} className={cn('ai-card', installed && 'installed')}>
              <div className="ai-card-head">
                <span className="ai-logo" style={{ background: `linear-gradient(140deg, ${tool.color}33, ${tool.color}14)`, color: tool.color }}>
                  {tool.name.slice(0, 1)}
                </span>
                <div className="ai-title">
                  <strong>{tool.name}</strong>
                  <span>{tool.note}</span>
                </div>
                <span className={cn('ai-state', installed ? 'on' : '')}>
                  {installed ? <Check size={11} /> : null}
                  {loaded ? (installed ? '已安装' : '未安装') : '检测中'}
                </span>
              </div>

              <div className="ai-meta">
                <div>
                  <span>版本</span>
                  <em>{state?.version || '—'}</em>
                </div>
                <div>
                  <span>来源</span>
                  <em>{state?.method || '—'}</em>
                </div>
                {state && state.count > 1 && (
                  <div>
                    <span>安装</span>
                    <em>{state.count} 处</em>
                  </div>
                )}
              </div>
              <code className="ai-path">{state?.path || '尚未检测到安装路径'}</code>

              <div className="ai-actions">
                {!installed ? (
                  <button
                    className="btn primary sm"
                    onClick={() => action(tool, 'install')}
                    disabled={busy !== null}
                  >
                    {busy === `install:${tool.id}` ? <Loader2 size={12} className="spin" /> : <Download size={12} />}
                    安装
                  </button>
                ) : (
                  <button
                    className="btn ghost sm"
                    onClick={() => action(tool, 'update')}
                    disabled={busy !== null}
                  >
                    {busy === `update:${tool.id}` ? <Loader2 size={12} className="spin" /> : <ArrowUpCircle size={12} />}
                    更新
                  </button>
                )}
                {installed && (
                  <button
                    className="icon-button sm danger"
                    onClick={() => action(tool, 'remove')}
                    disabled={busy !== null}
                    title={`卸载 ${tool.name}`}
                  >
                    {busy === `remove:${tool.id}` ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="two-col">
        <Section title="执行输出" desc={lastRun ? lastRun.label : '状态与操作输出'}>
          {lastRun ? (
            <RunOutput run={lastRun} compact />
          ) : (
            <div className="placeholder-panel">
              <Bot size={26} />
              <p>正在读取 AI 工具状态…</p>
            </div>
          )}
        </Section>
        <Section title="关于安装策略" desc="每个工具都使用各自官方推荐的安装方式">
          <ul className="check-list">
            <li>
              <strong>多来源识别</strong>
              <p>同时识别 npm、native 与 Homebrew Cask 安装，并标注当前生效的路径。</p>
            </li>
            <li>
              <strong>不覆盖配置</strong>
              <p>卸载时保留登录、设置与会话数据；更新只处理对应安装来源。</p>
            </li>
            <li>
              <strong>检查上游版本</strong>
              <p>ZCode 使用官方源码元数据，其余工具使用 npm 元数据；Homebrew 版本可能不同。</p>
            </li>
          </ul>
        </Section>
      </div>
    </div>
  );
}
