import { useEffect, useRef, useState } from 'react';
import {
  FolderOpen,
  Loader2,
  PackageMinus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { api } from '../lib/api';
import { cn, formatBytes } from '../lib/format';
import type { AppEntry, RunRecord } from '../lib/types';
import { useApp } from '../state';
import { AppIcon, Empty, Section } from '../components/ui';
import { RunOutput } from '../components/RunOutput';

type DataMode = 'keep' | 'clean';

export function Uninstall() {
  const { run, confirmAction, toast } = useApp();
  const [apps, setApps] = useState<AppEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [icons, setIcons] = useState<Record<string, string | null>>({});
  const [mode, setMode] = useState<DataMode>('keep');
  const [previewRun, setPreviewRun] = useState<RunRecord | null>(null);
  const [previewTarget, setPreviewTarget] = useState('');
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [busy, setBusy] = useState<'preview' | 'remove' | null>(null);
  const requested = useRef(new Set<string>());

  const load = async () => {
    setLoading(true);
    try {
      const list = await api.scanApps();
      setApps(list);
      setSelected((current) => current ?? list[0]?.path ?? null);
      void loadIcons(list);
    } catch (error) {
      toast(`扫描应用失败：${String(error)}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const loadIcons = async (list: AppEntry[]) => {
    const queue = list.filter((app) => !requested.current.has(app.path)).map((app) => app.path);
    queue.forEach((path) => requested.current.add(path));
    const workers = Array.from({ length: 5 }, async () => {
      while (queue.length > 0) {
        const path = queue.shift();
        if (!path) return;
        try {
          const icon = await api.appIcon(path);
          setIcons((current) => ({ ...current, [path]: icon }));
        } catch {
          setIcons((current) => ({ ...current, [path]: null }));
        }
      }
    });
    await Promise.all(workers);
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visible = apps.filter((app) => app.name.toLowerCase().includes(filter.trim().toLowerCase()));
  const current = apps.find((app) => app.path === selected) ?? null;
  const totalSize = apps.reduce((sum, app) => sum + app.size, 0);

  const preview = async () => {
    if (!current) return;
    setBusy('preview');
    const record = await run(
      ['remove', '--apps', '--dry-run', '--pick', `app:${current.id}`],
      {
        label: `预览卸载 ${current.name}`,
        group: 'uninstall',
      },
    );
    setPreviewRun(record);
    setPreviewTarget(current.path);
    setBusy(null);
  };

  const remove = async () => {
    if (!current) return;
    if (previewTarget !== current.path) {
      toast('请先扫描残留并查看预览', 'info');
      await preview();
      return;
    }
    const output = previewRun?.lines.map((line) => line.text).join('\n') ?? '';
    const dataCount = (output.match(/保留个人数据：/g) ?? []).length;
    const leftoverCount = (output.match(/残留/g) ?? []).length;
    const ok = await confirmAction({
      title: `卸载 ${current.name}？`,
      description:
        mode === 'clean'
          ? '应用会移到废纸篓，预览中列出的个人数据与残留也会一并清理。此操作不可撤销。'
          : '应用会移到废纸篓，预览中列出的个人数据与残留将保留，方便以后恢复或重装。',
      details: [
        `应用：${current.path}`,
        mode === 'clean' ? '数据策略：清理预览中的全部残留' : '数据策略：保留个人数据与残留',
        `预览识别：${dataCount} 项个人数据 · ${leftoverCount} 处残留描述`,
        `执行：ma remove --apps --pick app:${current.id.slice(0, 18)}… --yes ${mode === 'clean' ? '--clean-leftovers' : '--keep-leftovers'}`,
      ],
      confirmLabel: mode === 'clean' ? '卸载并清理' : '仅卸载应用',
      danger: true,
    });
    if (!ok) return;
    setBusy('remove');
    const record = await run(
      [
        'remove',
        '--apps',
        '--yes',
        '--pick',
        `app:${current.id}`,
        mode === 'clean' ? '--clean-leftovers' : '--keep-leftovers',
      ],
      { label: `卸载 ${current.name}`, group: 'uninstall' },
    );
    setLastRun(record);
    setBusy(null);
    if (record.status === 'success') {
      toast(`${current.name} 已卸载`, 'success');
      setPreviewTarget('');
      setPreviewRun(null);
      setSelected(null);
      await load();
    } else {
      toast('卸载未完成，请查看输出', 'error');
    }
  };

  return (
    <div className="page uninstall-page">
      <Section
        title="已安装应用"
        desc={
          loading
            ? '正在扫描 /Applications 与 ~/Applications…'
            : `发现 ${apps.length} 个应用 · 合计 ${formatBytes(totalSize)}`
        }
        aside={
          <button className="btn subtle sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={13} className={loading ? 'spin' : ''} />
            重新扫描
          </button>
        }
      >
        <div className="uninstall-grid">
          <div className="app-browser">
            <div className="app-search">
              <Search size={15} />
              <input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="筛选应用"
                spellCheck={false}
              />
            </div>
            <div className="app-list">
              {loading &&
                Array.from({ length: 7 }).map((_, index) => (
                  <div key={index} className="app-row skeleton-row">
                    <div className="skeleton icon" />
                    <div className="skeleton-lines">
                      <div className="skeleton line" />
                      <div className="skeleton line short" />
                    </div>
                  </div>
                ))}
              {!loading && visible.length === 0 && <Empty title="没有匹配的应用" desc="试试其他关键词。" />}
              {!loading &&
                visible.map((app) => (
                  <button
                    key={app.path}
                    className={cn('app-row', selected === app.path && 'active')}
                    onClick={() => {
                      setSelected(app.path);
                      setPreviewTarget('');
                      setPreviewRun(null);
                    }}
                  >
                    <AppIcon name={app.name} icon={icons[app.path]} size={38} radius={10} />
                    <span className="app-row-text">
                      <strong>{app.name}</strong>
                      <em>
                        {app.version ? `v${app.version}` : '版本未知'}
                        {app.size > 0 && ` · ${formatBytes(app.size)}`}
                      </em>
                    </span>
                    {selected === app.path && <span className="row-indicator" />}
                  </button>
                ))}
            </div>
          </div>

          <div className="app-detail">
            {!current && (
              <Empty title="选择一个应用" desc="左侧列出的是本机 /Applications 中的应用。" />
            )}
            {current && (
              <>
                <div className="detail-head">
                  <AppIcon name={current.name} icon={icons[current.path]} size={60} radius={16} />
                  <div className="detail-title">
                    <h3>{current.name}</h3>
                    <p>
                      {current.version ? `版本 ${current.version}` : '版本未知'}
                      {current.size > 0 && ` · ${formatBytes(current.size)}`}
                    </p>
                    <div className="detail-tags">
                      <span>{current.bundleId || '无 Bundle ID'}</span>
                      {current.system && <span className="tag warn">系统应用</span>}
                    </div>
                  </div>
                </div>

                <div className="detail-path">
                  <span>{current.path}</span>
                  <button onClick={() => void api.revealPath(current.path)} title="在 Finder 中显示">
                    <FolderOpen size={14} />
                  </button>
                </div>

                <div className="mode-cards">
                  <button className={cn('mode-card', mode === 'keep' && 'active')} onClick={() => setMode('keep')}>
                    <span className="mode-icon green">
                      <ShieldCheck size={17} />
                    </span>
                    <span>
                      <strong>保留个人数据</strong>
                      <em>卸载应用，保留配置、容器与缓存</em>
                    </span>
                    <i className="radio" />
                  </button>
                  <button className={cn('mode-card danger', mode === 'clean' && 'active')} onClick={() => setMode('clean')}>
                    <span className="mode-icon rose">
                      <Trash2 size={17} />
                    </span>
                    <span>
                      <strong>同时清理残留</strong>
                      <em>清理预览中列出的个人数据与残留</em>
                    </span>
                    <i className="radio" />
                  </button>
                </div>

                <div className="detail-actions">
                  <button className="btn ghost" onClick={preview} disabled={busy !== null}>
                    {busy === 'preview' ? <Loader2 size={15} className="spin" /> : <Search size={15} />}
                    {previewTarget === current.path ? '重新扫描残留' : '扫描残留'}
                  </button>
                  <button className="btn danger" onClick={remove} disabled={busy !== null}>
                    {busy === 'remove' ? <Loader2 size={15} className="spin" /> : <PackageMinus size={15} />}
                    卸载应用
                  </button>
                </div>

                {previewRun && previewTarget === current.path && (
                  <RunOutput run={previewRun} title="残留预览" compact />
                )}
                {lastRun && <RunOutput run={lastRun} title="卸载结果" compact />}
              </>
            )}
          </div>
        </div>
      </Section>
    </div>
  );
}
