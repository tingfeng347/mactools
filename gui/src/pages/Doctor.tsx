import { useState } from 'react';
import {
  Check,
  Clipboard,
  Cpu,
  Globe2,
  HardDrive,
  Loader2,
  RefreshCw,
  Stethoscope,
  TerminalSquare,
  X,
} from 'lucide-react';
import { RunOutput } from '../components/RunOutput';
import { Section } from '../components/ui';
import type { RunRecord } from '../lib/types';
import { useApp } from '../state';

export function Doctor() {
  const { env, envError, refreshEnv, run, toast, sendTerminal } = useApp();
  const [lastRun, setLastRun] = useState<RunRecord | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async () => {
    setBusy(true);
    const record = await run(['doctor'], { label: '环境与连通性检查', group: 'doctor' });
    setLastRun(record);
    setBusy(false);
    if (record.status === 'success') toast('检查完成', 'success');
    else toast('检查发现需要处理的问题', 'error');
  };

  const copy = async () => {
    const text = lastRun?.lines.map((line) => line.text).join('\n') ?? '';
    if (!text) {
      toast('还没有可复制的输出', 'info');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      toast('诊断输出已复制', 'success');
    } catch {
      toast('复制失败', 'error');
    }
  };

  const checks = [
    {
      icon: HardDrive,
      title: 'Homebrew',
      state: env?.brewPath ? 'ok' : 'bad',
      detail: env?.brewPath ? env.brewPath : '未安装 Homebrew',
    },
    {
      icon: Cpu,
      title: 'Python',
      state: env?.pythonPath ? 'ok' : 'bad',
      detail: env?.pythonPath || '未检测到 python3',
    },
    {
      icon: Stethoscope,
      title: 'mactools',
      state: env?.cliPath ? 'ok' : 'bad',
      detail: env?.cliPath || envError || '未找到 ma 命令',
    },
    {
      icon: Globe2,
      title: '网络',
      state: 'unknown',
      detail: '运行检查以测试 Homebrew 与镜像连通性',
    },
  ];

  return (
    <div className="page doctor-page">
      <div className="doctor-hero card">
        <div className="doctor-illustration">
          <Stethoscope size={34} />
        </div>
        <div className="doctor-copy">
          <span className="eyebrow">macd</span>
          <h2>给环境做一次完整体检</h2>
          <p>检查 Homebrew、Python、缓存占用与连通性。全程只读，不会修改系统。</p>
          <div className="hero-actions">
            <button className="btn primary" onClick={check} disabled={busy}>
              {busy ? <Loader2 size={15} className="spin" /> : <Stethoscope size={15} />}
              运行检查
            </button>
            <button className="btn ghost" onClick={() => void refreshEnv()}>
              <RefreshCw size={15} />
              重新检测环境
            </button>
            <button className="btn subtle" onClick={copy}>
              <Clipboard size={15} />
              复制结果
            </button>
          </div>
        </div>
      </div>

      <div className="check-grid">
        {checks.map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.title} className="check-card">
              <span className="check-card-icon">
                <Icon size={18} />
              </span>
              <div>
                <strong>{item.title}</strong>
                <p title={item.detail}>{item.detail}</p>
              </div>
              <span className={`check-badge ${item.state}`}>
                {item.state === 'ok' ? <Check size={11} /> : item.state === 'bad' ? <X size={11} /> : '?'}
                {item.state === 'ok' ? '正常' : item.state === 'bad' ? '异常' : '待检查'}
              </span>
            </div>
          );
        })}
      </div>

      <div className="two-col">
        <Section title="诊断输出" desc={lastRun ? lastRun.label : '运行检查后显示详细结果'}>
          {lastRun ? (
            <RunOutput run={lastRun} />
          ) : (
            <div className="placeholder-panel">
              <Stethoscope size={26} />
              <p>点击「运行检查」验证环境与连通性。</p>
            </div>
          )}
        </Section>
        <Section title="检查覆盖范围">
          <ul className="check-list">
            <li>
              <strong>环境依赖</strong>
              <p>Homebrew 路径与版本、Python 3、mactools 自身位置与版本。</p>
            </li>
            <li>
              <strong>缓存与磁盘</strong>
              <p>缓存目录状态、磁盘余量、可清理项目与权限问题。</p>
            </li>
            <li>
              <strong>网络连通性</strong>
              <p>GitHub、Homebrew API 与配置镜像的可达性与响应时间。</p>
            </li>
          </ul>
          <div className="inline-command">
            <code>macd</code>
            <button className="btn subtle sm" onClick={() => sendTerminal('macd')}>
              <TerminalSquare size={13} />
              打开交互检查
            </button>
          </div>
        </Section>
      </div>
    </div>
  );
}
