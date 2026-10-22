import { useMemo, useState } from 'react';
import {
  ArrowUpCircle,
  Check,
  Loader2,
  RefreshCw,
  RotateCw,
  Sparkles,
  TerminalSquare,
} from 'lucide-react';
import { api } from '../lib/api';
import { RunOutput } from '../components/RunOutput';
import { Section } from '../components/ui';
import type { PackageEntry, RunRecord } from '../lib/types';
import { useApp } from '../state';

function keyOf(entry: PackageEntry) {
  return `${entry.source}:${entry.name}`;
}

function signature(rows: PackageEntry[]) {
  return rows.map(keyOf).sort().join('|');
}

export function Update() {
  const { refreshEnv, run, confirmAction, toast, sendTerminal } = useApp();
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [results, setResults] = useState<PackageEntry[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [checked, setChecked] = useState(false);
  const [previewed, setPreviewed] = useState('');
  const [busy, setBusy] = useState<'check' | 'preview' | 'update' | 'index' | null>(null);

  const selectedRows = useMemo(
    () => results.filter((entry) => picked.has(keyOf(entry))),
    [results, picked],
  );
  const selection = signature(selectedRows);

  const check = async () => {
    setBusy('check');
    try {
      const list = await api.searchPackages('update', '');
      setResults(list);
      setPicked(new Set(list.map(keyOf)));
      setPreviewed('');
      setChecked(true);
      toast(
        list.length ? `发现 ${list.length} 个可更新软件` : '当前没有可更新的软件',
        list.length ? 'success' : 'info',
      );
    } catch (error) {
      toast(`检查失败：${String(error)}`, 'error');
    } finally {
      setBusy(null);
    }
  };

  const toggle = (entry: PackageEntry) => {
    const key = keyOf(entry);
    setPreviewed('');
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const preview = async () => {
    if (selectedRows.length === 0) {
      toast('请先选择要更新的软件', 'info');
      return;
    }
    setBusy('preview');
    const record = await run(
      ['update', '--dry-run', ...selectedRows.flatMap((entry) => ['--pick', keyOf(entry)])],
      { label: '预览软件更新', group: 'update' },
    );
    setLastRun(record);
    setBusy(null);
    if (record.status === 'success') setPreviewed(selection);
    else toast('预览失败，请查看输出', 'error');
  };

  const updateSelected = async () => {
    if (selectedRows.length === 0) {
      toast('请先选择要更新的软件', 'info');
      return;
    }
    if (previewed !== selection) {
      toast('请先预览更新计划', 'info');
      await preview();
      return;
    }
    const ok = await confirmAction({
      title: `更新所选 ${selectedRows.length} 项？`,
      description: 'Homebrew 将升级所选 Formula 与 Cask。部分应用可能要求关闭后继续。',
      details: [
        ...selectedRows.map((entry) => `${entry.name} · ${entry.current} → ${entry.target}`),
        `执行：ma update --yes ${selectedRows.map((entry) => `--pick ${keyOf(entry)}`).join(' ')}`,
      ],
      confirmLabel: '开始更新',
    });
    if (!ok) return;
    setBusy('update');
    const record = await run(
      ['update', '--yes', ...selectedRows.flatMap((entry) => ['--pick', keyOf(entry)])],
      { label: `更新 ${selectedRows.length} 项软件`, group: 'update' },
    );
    setLastRun(record);
    setBusy(null);
    if (record.status === 'success') {
      toast('更新流程已完成', 'success');
      setPreviewed('');
      void refreshEnv();
      await check();
    } else {
      toast('更新未完成，请查看输出', 'error');
    }
  };

  const updateAll = async () => {
    const ok = await confirmAction({
      title: '更新所有可升级软件？',
      description: 'Homebrew 将升级所有已安装且可更新的 Formula 与 Cask。部分应用可能要求关闭后继续。',
      details: ['执行：ma update --all --yes', '建议先运行「检查更新」查看计划'],
      confirmLabel: '更新全部',
    });
    if (!ok) return;
    setBusy('update');
    const record = await run(['update', '--all', '--yes'], {
      label: '更新所有软件',
      group: 'update',
    });
    setLastRun(record);
    setBusy(null);
    if (record.status === 'success') {
      toast('更新流程已完成', 'success');
      setResults([]);
      setPicked(new Set());
      setChecked(false);
      setPreviewed('');
      void refreshEnv();
    } else {
      toast('更新未完成，请查看输出', 'error');
    }
  };

  const refreshIndex = async () => {
    const ok = await confirmAction({
      title: '刷新 Homebrew 索引？',
      description: '会显式运行 brew update 获取最新的软件目录。通常需要网络，耗时取决于网络状况。',
      details: ['执行：ma update --refresh-index --yes'],
      confirmLabel: '刷新索引',
    });
    if (!ok) return;
    setBusy('index');
    const record = await run(['update', '--refresh-index', '--yes'], {
      label: '刷新 Homebrew 索引',
      group: 'update',
    });
    setLastRun(record);
    setBusy(null);
  };

  return (
    <div className="page update-page">
      <div className="action-grid">
        <button className="action-card" onClick={() => void check()} disabled={busy !== null}>
          <span className="action-icon violet">
            {busy === 'check' ? <Loader2 size={20} className="spin" /> : <RefreshCw size={20} />}
          </span>
          <strong>检查更新</strong>
          <p>列出可升级的 Formula 与 Cask，不修改任何内容。</p>
          <code>ma update --list-json</code>
        </button>
        <button
          className="action-card"
          onClick={() => void preview()}
          disabled={busy !== null || selectedRows.length === 0}
        >
          <span className="action-icon cyan">
            {busy === 'preview' ? <Loader2 size={20} className="spin" /> : <Sparkles size={20} />}
          </span>
          <strong>预览所选</strong>
          <p>查看所选软件将执行的具体升级计划。</p>
          <code>ma update --dry-run --pick …</code>
        </button>
        <button className="action-card" onClick={() => void updateAll()} disabled={busy !== null}>
          <span className="action-icon green">
            {busy === 'update' ? <Loader2 size={20} className="spin" /> : <ArrowUpCircle size={20} />}
          </span>
          <strong>全部更新</strong>
          <p>升级所有可更新的软件，执行前会再次确认。</p>
          <code>ma update --all --yes</code>
        </button>
        <button className="action-card" onClick={() => void refreshIndex()} disabled={busy !== null}>
          <span className="action-icon violet">
            {busy === 'index' ? <Loader2 size={20} className="spin" /> : <RotateCw size={20} />}
          </span>
          <strong>刷新索引</strong>
          <p>运行 brew update，获取最新的软件目录信息。</p>
          <code>ma update --refresh-index</code>
        </button>
      </div>

      <Section
        title="可更新软件"
        desc={
          checked
            ? `${results.length} 个可更新软件 · 已选 ${selectedRows.length} 项`
            : '点击「检查更新」获取实时列表'
        }
        aside={
          results.length > 0 ? (
            <div className="result-tools">
              <button className="btn subtle sm" onClick={() => setPicked(new Set(results.map(keyOf)))}>
                全选
              </button>
              <button className="btn subtle sm" onClick={() => setPicked(new Set())}>
                清空
              </button>
              <button
                className="btn primary sm"
                onClick={() => void updateSelected()}
                disabled={busy !== null || selectedRows.length === 0}
              >
                更新所选
              </button>
            </div>
          ) : undefined
        }
      >
        {!checked && (
          <div className="placeholder-panel result-empty">
            <RefreshCw size={26} />
            <p>还没有检查更新。检查只会读取版本信息。</p>
          </div>
        )}
        {checked && results.length === 0 && (
          <div className="placeholder-panel result-empty">
            <Check size={26} />
            <p>所有软件都是最新版本。</p>
          </div>
        )}
        {results.length > 0 && (
          <div className="result-list">
            {results.map((entry) => {
              const active = picked.has(keyOf(entry));
              return (
                <button
                  key={keyOf(entry)}
                  className={`result-row${active ? ' active' : ''}`}
                  onClick={() => toggle(entry)}
                >
                  <span className="result-check">{active && <Check size={12} />}</span>
                  <span className={`source-badge ${entry.source}`}>
                    {entry.source === 'cask' ? 'Cask' : 'Formula'}
                  </span>
                  <span className="result-name">
                    <strong>{entry.displayName || entry.name}</strong>
                    {entry.displayName && entry.displayName !== entry.name && <em>{entry.name}</em>}
                  </span>
                  <span className="result-version">
                    <em>{entry.current}</em>
                    <span>→</span>
                    <strong>{entry.target}</strong>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      <div className="two-col">
        <Section title="更新策略" desc="mactools 只编排 Homebrew 更新">
          <ul className="check-list">
            <li>
              <strong>先检查，后执行</strong>
              <p>检查阶段只读，不会改动已安装软件或工具缓存。</p>
            </li>
            <li>
              <strong>精确到每个来源</strong>
              <p>所选更新会带上来源与完整名称，避免同名 Formula 或 Cask 被误升级。</p>
            </li>
            <li>
              <strong>需要密码时</strong>
              <p>部分 Cask 升级要求交互确认，可在嵌入终端中完成。</p>
            </li>
          </ul>
          <div className="inline-command">
            <code>macu</code>
            <button className="btn subtle sm" onClick={() => sendTerminal('macu')}>
              <TerminalSquare size={13} />
              打开交互更新
            </button>
          </div>
        </Section>

        <Section title="执行结果" desc={lastRun ? lastRun.label : '预览或更新后显示输出'}>
          {lastRun ? (
            <RunOutput run={lastRun} />
          ) : (
            <div className="placeholder-panel">
              <RefreshCw size={26} />
              <p>点击左侧操作开始。</p>
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
