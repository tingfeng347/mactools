import { useMemo, useState } from 'react';
import {
  Check,
  Code2,
  Eye,
  HardDrive,
  Loader2,
  Package,
  Sparkles,
  Square,
  TerminalSquare,
  Trash2,
} from 'lucide-react';
import { CLEAN_ITEMS, type CleanItem } from '../data/catalog';
import { cn } from '../lib/format';
import type { RunRecord } from '../lib/types';
import { useApp } from '../state';
import { RunOutput } from '../components/RunOutput';
import { Section } from '../components/ui';

const GROUPS = ['全部', 'Homebrew', '开发缓存', '系统缓存'] as const;
type Group = (typeof GROUPS)[number];

function GroupIcon({ group }: { group: CleanItem['group'] }) {
  if (group === 'Homebrew') return <Package size={17} />;
  if (group === '开发缓存') return <Code2 size={17} />;
  return <HardDrive size={17} />;
}

export function Clean() {
  const { run, confirmAction, toast, sendTerminal } = useApp();
  const [group, setGroup] = useState<Group>('全部');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [busy, setBusy] = useState<'preview' | 'clean' | null>(null);
  const [progress, setProgress] = useState('');

  const visible = useMemo(
    () => CLEAN_ITEMS.filter((item) => group === '全部' || item.group === group),
    [group],
  );
  const selectedItems = CLEAN_ITEMS.filter((item) => selected.has(item.id));

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleVisible = () => {
    const allSelected = visible.every((item) => selected.has(item.id));
    setSelected((current) => {
      const next = new Set(current);
      visible.forEach((item) => (allSelected ? next.delete(item.id) : next.add(item.id)));
      return next;
    });
  };

  const previewOne = async (item: CleanItem) => {
    setBusy('preview');
    setProgress(`正在预览 ${item.title}`);
    const record = await run(['clean', '--preview-action', item.id], {
      label: `预览清理 ${item.title}`,
      group: 'clean',
    });
    setLastRun(record);
    setBusy(null);
    setProgress('');
  };

  const previewSelected = async () => {
    if (selectedItems.length === 0) {
      toast('请先选择要预览的清理项目', 'info');
      return;
    }
    setBusy('preview');
    let last: RunRecord | null = null;
    for (const [index, item] of selectedItems.entries()) {
      setProgress(`预览 ${index + 1}/${selectedItems.length} · ${item.title}`);
      last = await run(['clean', '--preview-action', item.id], {
        label: `预览清理 ${item.title}`,
        group: 'clean',
        background: index !== selectedItems.length - 1,
      });
    }
    setLastRun(last);
    setBusy(null);
    setProgress('');
    toast('所选项目预览完成', 'success');
  };

  const clean = async () => {
    if (selectedItems.length === 0) {
      toast('请先选择要清理的项目', 'info');
      return;
    }
    const ids = selectedItems.map((item) => item.id);
    const ok = await confirmAction({
      title: `清理 ${selectedItems.length} 个项目？`,
      description:
        '清理命令会重新核对当前状态，只处理仍然存在的缓存与旧版本。建议先预览大小与受影响范围。',
      details: [
        `项目：${selectedItems.map((item) => item.title).join('、')}`,
        `执行：ma clean --exact ${ids.join(' ')} --yes`,
        '缓存会在后续使用中重新生成；删除的内容不会进入废纸篓。',
      ],
      confirmLabel: '开始清理',
      danger: true,
    });
    if (!ok) return;
    setBusy('clean');
    setProgress('正在清理所选项目…');
    const record = await run(['clean', '--exact', ...ids, '--yes'], {
      label: `清理 ${selectedItems.length} 个项目`,
      group: 'clean',
    });
    setLastRun(record);
    setBusy(null);
    setProgress('');
    if (record.status === 'success') toast('清理完成', 'success');
    else toast('清理未完成，请查看输出', 'error');
  };

  return (
    <div className="page clean-page">
      <div className="toolbar-row">
        <div className="segmented">
          {GROUPS.map((item) => (
            <button key={item} className={group === item ? 'active' : ''} onClick={() => setGroup(item)}>
              {item}
            </button>
          ))}
        </div>
        <div className="toolbar-actions">
          <button className="btn subtle sm" onClick={toggleVisible}>
            {visible.every((item) => selected.has(item.id)) ? <Check size={13} /> : <Square size={13} />}
            {visible.every((item) => selected.has(item.id)) ? '取消全选' : '全选当前'}
          </button>
          <button className="btn ghost sm" onClick={previewSelected} disabled={busy !== null}>
            {busy === 'preview' ? <Loader2 size={13} className="spin" /> : <Eye size={13} />}
            预览所选
          </button>
          <button className="btn primary sm" onClick={clean} disabled={busy !== null || selected.size === 0}>
            {busy === 'clean' ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
            清理所选 {selected.size > 0 && `(${selected.size})`}
          </button>
        </div>
      </div>

      {progress && (
        <div className="progress-banner">
          <Loader2 size={14} className="spin" />
          {progress}
        </div>
      )}

      <div className="clean-grid">
        {visible.map((item) => {
          const active = selected.has(item.id);
          return (
            <div
              key={item.id}
              className={cn('clean-card', active && 'active', item.tone)}
              onClick={() => toggle(item.id)}
              role="checkbox"
              aria-checked={active}
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  toggle(item.id);
                }
              }}
            >
              <div className="clean-card-top">
                <span className="clean-icon">
                  <GroupIcon group={item.group} />
                </span>
                <span className="clean-check">{active ? <Check size={13} /> : null}</span>
              </div>
              <strong>{item.title}</strong>
              <p>{item.note}</p>
              <div className="clean-card-foot">
                <span>{item.group}</span>
                <button
                  className="preview-link"
                  onClick={(event) => {
                    event.stopPropagation();
                    void previewOne(item);
                  }}
                  disabled={busy !== null}
                >
                  <Eye size={12} />
                  预览
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="two-col">
        <Section title="执行输出" desc={lastRun ? lastRun.label : '预览或清理后显示输出'}>
          {lastRun ? (
            <RunOutput run={lastRun} />
          ) : (
            <div className="placeholder-panel">
              <Trash2 size={26} />
              <p>点击任一卡片的「预览」查看可回收空间，再选择是否清理。</p>
            </div>
          )}
        </Section>
        <Section title="安全边界" desc="清理同样遵循 mactools 的白名单与路径保护">
          <ul className="check-list">
            <li>
              <strong>只处理可再生数据</strong>
              <p>不会删除项目源码、虚拟环境、已安装软件与用户文档。</p>
            </li>
            <li>
              <strong>执行前重新核对</strong>
              <p>清理时会再次检查目标状态，已变化的内容会被跳过。</p>
            </li>
            <li>
              <strong>需要交互时</strong>
              <p>系统级项目可能需要管理员授权，可在终端中完成。</p>
            </li>
          </ul>
          <div className="inline-command">
            <code>{selected.size ? `ma clean --exact ${selectedItems.map((item) => item.id).join(' ')}` : 'macc'}</code>
            <button
              className="btn subtle sm"
              onClick={() =>
                sendTerminal(selected.size ? `ma clean --exact ${selectedItems.map((item) => item.id).join(' ')}` : 'macc')
              }
            >
              <TerminalSquare size={13} />
              在终端运行
            </button>
          </div>
        </Section>
      </div>
    </div>
  );
}
