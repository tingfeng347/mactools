import { useState } from 'react';
import {
  ArrowDownToLine,
  Check,
  Gauge,
  Globe2,
  Loader2,
  Radio,
  RotateCcw,
  TerminalSquare,
  X,
} from 'lucide-react';
import { MIRRORS } from '../data/catalog';
import { cn } from '../lib/format';
import type { RunRecord } from '../lib/types';
import { useApp } from '../state';
import { RunOutput } from '../components/RunOutput';
import { Section } from '../components/ui';

type MirrorResult = {
  name: string;
  url: string;
  ok: boolean;
  bytes?: number;
  seconds?: number;
  bytes_per_second?: number;
  error?: string;
};

type MirrorPayload = {
  results?: MirrorResult[];
  action?: string;
  path?: string;
  fragment?: string;
  source?: string;
};

function parsePayload(record: RunRecord | null): MirrorPayload | null {
  if (!record) return null;
  const text = record.lines
    .filter((line) => line.stream !== 'meta')
    .map((line) => line.text)
    .join('\n');
  const start = text.indexOf('{');
  if (start < 0) return null;
  try {
    return JSON.parse(text.slice(start)) as MirrorPayload;
  } catch {
    return null;
  }
}

function speed(value?: number): string {
  if (!value) return '—';
  if (value > 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB/s`;
  return `${(value / 1024).toFixed(0)} KB/s`;
}

export function Mirror() {
  const { run, confirmAction, toast, sendTerminal } = useApp();
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [payload, setPayload] = useState<MirrorPayload | null>(null);
  const [busy, setBusy] = useState<'test' | 'auto' | 'restore' | null>(null);

  const test = async () => {
    setBusy('test');
    const record = await run(['mirror', 'test', '--json'], { label: 'Homebrew 镜像测速', group: 'mirror' });
    setLastRun(record);
    setPayload(parsePayload(record));
    setBusy(null);
    if (record.status === 'success') toast('镜像测速完成', 'success');
  };

  const auto = async () => {
    const ok = await confirmAction({
      title: '自动选择并切换镜像？',
      description: '会依次测速所有候选源，验证 API 与 bottle 哈希后写入 Homebrew 镜像配置。原配置会保留备份。',
      details: ['执行：ma mirror auto --yes', '验证失败时不会修改当前配置'],
      confirmLabel: '测速并切换',
    });
    if (!ok) return;
    setBusy('auto');
    const record = await run(['mirror', 'auto', '--json', '--yes'], {
      label: '自动切换 Homebrew 镜像',
      group: 'mirror',
    });
    setLastRun(record);
    setPayload(parsePayload(record));
    setBusy(null);
    if (record.status === 'success') toast('镜像配置已更新', 'success');
    else toast('镜像切换失败，配置未修改', 'error');
  };

  const restore = async () => {
    const ok = await confirmAction({
      title: '恢复 Homebrew 官方源？',
      description: '会移除 mactools 管理的镜像片段，恢复 Homebrew 默认配置。',
      details: ['执行：ma mirror restore --yes'],
      confirmLabel: '恢复官方源',
      danger: true,
    });
    if (!ok) return;
    setBusy('restore');
    const record = await run(['mirror', 'restore', '--json', '--yes'], {
      label: '恢复 Homebrew 官方源',
      group: 'mirror',
    });
    setLastRun(record);
    setPayload(parsePayload(record));
    setBusy(null);
  };

  const results = payload?.results ?? [];

  return (
    <div className="page mirror-page">
      <div className="mirror-hero card">
        <div>
          <span className="eyebrow">
            <Radio size={14} />
            Homebrew bottles
          </span>
          <h2>选择更快的下载线路</h2>
          <p>对官方、清华与中科大镜像进行字节级测速与哈希校验，只应用验证通过的源。</p>
        </div>
        <div className="mirror-actions">
          <button className="btn ghost" onClick={test} disabled={busy !== null}>
            {busy === 'test' ? <Loader2 size={15} className="spin" /> : <Gauge size={15} />}
            测速
          </button>
          <button className="btn primary" onClick={auto} disabled={busy !== null}>
            {busy === 'auto' ? <Loader2 size={15} className="spin" /> : <ArrowDownToLine size={15} />}
            自动选择
          </button>
          <button className="btn subtle" onClick={restore} disabled={busy !== null}>
            {busy === 'restore' ? <Loader2 size={15} className="spin" /> : <RotateCcw size={15} />}
            恢复官方
          </button>
        </div>
      </div>

      <div className="mirror-grid">
        {MIRRORS.map((mirror) => {
          const result = results.find((row) => row.name === mirror.id);
          return (
            <div key={mirror.id} className={cn('mirror-card', result?.ok && 'ok', result && !result.ok && 'bad')}>
              <div className="mirror-card-head">
                <span className="mirror-icon">
                  <Globe2 size={18} />
                </span>
                <span className={cn('mirror-status', result?.ok ? 'ok' : result ? 'bad' : '')}>
                  {result ? (result.ok ? <Check size={11} /> : <X size={11} />) : null}
                  {result ? (result.ok ? '可用' : '失败') : '未测试'}
                </span>
              </div>
              <strong>{mirror.name}</strong>
              <p>{mirror.note}</p>
              <div className="mirror-metrics">
                <div>
                  <span>速度</span>
                  <em>{speed(result?.bytes_per_second)}</em>
                </div>
                <div>
                  <span>耗时</span>
                  <em>{result?.seconds ? `${Math.round(result.seconds * 1000)} ms` : '—'}</em>
                </div>
              </div>
              <code>{result?.url || mirror.id}</code>
            </div>
          );
        })}
      </div>

      <div className="two-col">
        <Section title="执行输出" desc={lastRun ? lastRun.label : '测速或切换后显示结果'}>
          {lastRun ? (
            <RunOutput run={lastRun} />
          ) : (
            <div className="placeholder-panel">
              <Radio size={26} />
              <p>点击「测速」比较各镜像的实际下载速度。</p>
            </div>
          )}
        </Section>
        <Section title="生效方式" desc="只修改 Homebrew 用户配置">
          <ul className="check-list">
            <li>
              <strong>事务式写入</strong>
              <p>切换前保存备份，验证 API 元数据与 SHA-256 后才应用。</p>
            </li>
            <li>
              <strong>当前终端生效</strong>
              <p>若输出包含 source 命令，在终端执行即可让当前会话立即生效。</p>
            </li>
            <li>
              <strong>随时恢复</strong>
              <p>「恢复官方」只移除 mactools 管理的片段，不触碰其他 Homebrew 配置。</p>
            </li>
          </ul>
          {payload?.source && <pre className="source-snippet">{payload.source}</pre>}
          <div className="inline-command">
            <code>macm</code>
            <button className="btn subtle sm" onClick={() => sendTerminal('macm')}>
              <TerminalSquare size={13} />
              打开交互测速
            </button>
          </div>
        </Section>
      </div>
    </div>
  );
}
