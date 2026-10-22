import { useMemo, useState } from 'react';
import {
  Check,
  CloudDownload,
  Loader2,
  PackageSearch,
  Search,
  Sparkles,
  TerminalSquare,
} from 'lucide-react';
import { api } from '../lib/api';
import { RunOutput } from '../components/RunOutput';
import { Section } from '../components/ui';
import type { PackageEntry, RunRecord } from '../lib/types';
import { useApp } from '../state';

const SUGGESTIONS = ['firefox', 'iterm2', 'visual-studio-code', 'raycast', 'stats', 'rectangle'];

type Source = 'auto' | 'formula' | 'cask';

function keyOf(entry: PackageEntry) {
  return `${entry.source}:${entry.name}`;
}

function signature(rows: PackageEntry[]) {
  return rows.map(keyOf).sort().join('|');
}

export function Install() {
  const { env, refreshEnv, run, confirmAction, toast, sendTerminal } = useApp();
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<Source>('auto');
  const [results, setResults] = useState<PackageEntry[]>([]);
  const [searched, setSearched] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [searching, setSearching] = useState(false);
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [previewed, setPreviewed] = useState('');
  const [busy, setBusy] = useState<'preview' | 'install' | null>(null);

  const normalized = query.trim();
  const selectedRows = useMemo(
    () => results.filter((entry) => picked.has(keyOf(entry))),
    [results, picked],
  );
  const selection = signature(selectedRows);
  const command = selectedRows.length
    ? `ma install ${selectedRows.map((entry) => `--pick ${keyOf(entry)}`).join(' ')}`
    : 'ma install <software>';

  const search = async (value = normalized) => {
    const text = value.trim();
    if (!text) {
      toast('请输入软件名称', 'error');
      return;
    }
    setQuery(value);
    setSearching(true);
    try {
      const list = await api.searchPackages(
        'install',
        text,
        source === 'auto' ? undefined : source,
      );
      setResults(list);
      setSearched(text);
      setPreviewed('');
      const exact = list.filter(
        (entry) => entry.name === text || entry.name.endsWith(`/${text}`),
      );
      setPicked(new Set((exact.length ? exact.slice(0, 1) : []).map(keyOf)));
      if (list.length === 0) toast('没有找到匹配的软件，试试更短的关键词', 'info');
    } catch (error) {
      setResults([]);
      setSearched('');
      toast(`搜索失败：${String(error)}`, 'error');
    } finally {
      setSearching(false);
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
      toast('请先选择要安装的软件', 'info');
      return;
    }
    setBusy('preview');
    const record = await run(
      [
        'install',
        '--dry-run',
        ...selectedRows.flatMap((entry) => ['--pick', keyOf(entry)]),
      ],
      {
        label: `预览安装 ${selectedRows.map((entry) => entry.name).join('、')}`,
        group: 'install',
      },
    );
    setLastRun(record);
    setBusy(null);
    if (record.status === 'success') setPreviewed(selection);
    else toast('预览失败，请查看输出', 'error');
  };

  const install = async () => {
    if (selectedRows.length === 0) {
      toast('请先选择要安装的软件', 'info');
      return;
    }
    if (previewed !== selection) {
      toast('请先预览安装计划，确认来源与版本', 'info');
      await preview();
      return;
    }
    const ok = await confirmAction({
      title: `安装所选 ${selectedRows.length} 项？`,
      description: '将通过 Homebrew 安装所选软件。下载、依赖与权限提示会实时显示在命令中心。',
      details: [
        ...selectedRows.map(
          (entry) => `${entry.source === 'cask' ? 'Cask' : 'Formula'} · ${entry.name}`,
        ),
        command,
        env?.brewPath ? `Homebrew：${env.brewPath}` : 'Homebrew 未就绪',
      ],
      confirmLabel: '开始安装',
    });
    if (!ok) return;
    setBusy('install');
    const record = await run(
      [
        'install',
        '--yes',
        ...selectedRows.flatMap((entry) => ['--pick', keyOf(entry)]),
      ],
      {
        label: `安装 ${selectedRows.map((entry) => entry.name).join('、')}`,
        group: 'install',
      },
    );
    setLastRun(record);
    setBusy(null);
    if (record.status === 'success') {
      toast(`已安装 ${selectedRows.length} 项软件`, 'success');
      setPicked(new Set());
      setPreviewed('');
      setResults([]);
      setSearched('');
      void refreshEnv();
    } else {
      toast('安装未完成，请查看输出', 'error');
    }
  };

  return (
    <div className="page install-page">
      <section className="search-hero card">
        <div className="search-hero-head">
          <div className="eyebrow">
            <PackageSearch size={14} />
            Homebrew 软件库
          </div>
          <h2>安装你需要的工具</h2>
          <p>搜索 Formula 与 Cask，选择确切结果后先预览、再安装。可一次选择多项。</p>
        </div>
        <div className="search-row">
          <div className="search-field">
            <Search size={17} />
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPreviewed('');
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void search();
              }}
              placeholder="搜索软件，例如 firefox、wget、iterm2"
              spellCheck={false}
              autoFocus
            />
            {normalized && <kbd>↵</kbd>}
          </div>
          <div className="segmented source">
            {(['auto', 'formula', 'cask'] as const).map((value) => (
              <button
                key={value}
                className={source === value ? 'active' : ''}
                onClick={() => {
                  setSource(value);
                  setResults([]);
                  setSearched('');
                  setPicked(new Set());
                  setPreviewed('');
                }}
              >
                {value === 'auto' ? '自动' : value === 'formula' ? 'Formula' : 'Cask'}
              </button>
            ))}
          </div>
        </div>
        <div className="search-actions">
          <button className="btn ghost" onClick={() => void search()} disabled={searching || busy !== null}>
            {searching ? <Loader2 size={15} className="spin" /> : <Search size={15} />}
            搜索
          </button>
          <button
            className="btn ghost"
            onClick={() => void preview()}
            disabled={busy !== null || selectedRows.length === 0}
          >
            {busy === 'preview' ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />}
            预览安装计划
          </button>
          <button
            className="btn primary"
            onClick={() => void install()}
            disabled={busy !== null || selectedRows.length === 0}
          >
            {busy === 'install' ? <Loader2 size={15} className="spin" /> : <CloudDownload size={15} />}
            {selection && previewed === selection ? '执行安装' : '预览并安装'}
          </button>
        </div>
        <div className="suggestions">
          <span>常用：</span>
          {SUGGESTIONS.map((item) => (
            <button key={item} onClick={() => void search(item)}>
              {item}
            </button>
          ))}
        </div>
      </section>

      <Section
        title="搜索结果"
        desc={
          searched
            ? `“${searched}” · ${results.length} 个结果 · 已选 ${selectedRows.length} 项`
            : '输入关键词搜索，结果来自 Homebrew 的实时目录'
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
            </div>
          ) : undefined
        }
      >
        {results.length === 0 && (
          <div className="placeholder-panel result-empty">
            {searching ? <Loader2 size={26} className="spin" /> : <PackageSearch size={26} />}
            <p>{searching ? '正在查询 Homebrew…' : '搜索后在这里选择要安装的软件。'}</p>
          </div>
        )}
        {results.length > 0 && (
          <div className="result-list">
            {results.map((entry) => {
              const key = keyOf(entry);
              const active = picked.has(key);
              return (
                <button
                  key={key}
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
                  <span className="result-state">
                    {entry.current && entry.current !== '-' ? `已安装 ${entry.current}` : '未安装'}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      <div className="two-col">
        <Section title="安装说明" desc="mactools 不会悄悄修改系统">
          <ul className="check-list">
            <li>
              <strong>确切的软件身份</strong>
              <p>点击结果选中具体的 Formula 或 Cask，安装时不会再经过模糊匹配。</p>
            </li>
            <li>
              <strong>先预览，再安装</strong>
              <p>预览会展示将要执行的 Homebrew 计划，不会写入任何内容。</p>
            </li>
            <li>
              <strong>使用官方 Homebrew</strong>
              <p>安装、卸载与更新都交由 Homebrew 执行，mactools 只负责编排与校验。</p>
            </li>
          </ul>
          <div className="inline-command">
            <code>{command}</code>
            <button className="btn subtle sm" onClick={() => sendTerminal('maci')}>
              <TerminalSquare size={13} />
              打开交互安装
            </button>
          </div>
        </Section>

        <Section
          title="执行结果"
          desc={
            lastRun
              ? `${lastRun.label} · ${lastRun.status === 'running' ? '运行中' : lastRun.status === 'success' ? '已完成' : '未完成'}`
              : '预览或安装后显示实时输出'
          }
        >
          {lastRun ? (
            <RunOutput run={lastRun} />
          ) : (
            <div className="placeholder-panel">
              <PackageSearch size={26} />
              <p>搜索软件并预览，输出会显示在这里。</p>
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
