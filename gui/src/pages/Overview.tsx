import {
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  CircleX,
  Download,
  Gauge,
  HardDrive,
  Package,
  PackageMinus,
  Sparkles,
  TerminalSquare,
} from 'lucide-react';
import { api } from '../lib/api';
import { cn, formatBytes, relativeTime } from '../lib/format';
import type { PageId } from '../lib/types';
import { useApp } from '../state';
import { BrandMark } from '../components/BrandMark';
import { Section } from '../components/ui';

const QUICK: Array<{
  id: PageId;
  icon: typeof Sparkles;
  title: string;
  desc: string;
  tone: string;
}> = [
  { id: 'install', icon: Download, title: '安装软件', desc: 'Formula / Cask 搜索安装', tone: 'violet' },
  { id: 'uninstall', icon: PackageMinus, title: '卸载应用', desc: '预览残留后再决定', tone: 'rose' },
  { id: 'clean', icon: Sparkles, title: '空间清理', desc: '缓存与旧版本清理', tone: 'cyan' },
  { id: 'optimize', icon: Gauge, title: '系统优化', desc: '刷新缓存与服务', tone: 'amber' },
];

export function Overview({ onNavigate }: { onNavigate: (page: PageId) => void }) {
  const { env, envError, runs, toast, sendTerminal } = useApp();
  const used = env ? Math.max(0, env.diskTotal - env.diskFree) : 0;
  const usedPct = env && env.diskTotal ? Math.min(100, Math.round((used / env.diskTotal) * 100)) : 0;
  const circumference = 2 * Math.PI * 42;
  const brewReady = Boolean(env?.brewPath);
  const cliReady = Boolean(env?.cliPath);

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制到剪贴板', 'success');
    } catch {
      toast('复制失败', 'error');
    }
  };

  return (
    <div className="page overview">
      <section className="hero">
        <div className="hero-glow" />
        <div className="hero-main">
          <div className="hero-kicker">
            <span className="live-dot" />
            {cliReady ? 'mactools 已就绪' : '需要安装 mactools'}
          </div>
          <h2>
            让 Mac 保持
            <span className="gradient-text">轻盈、干净、可控</span>
          </h2>
          <p>
            统一管理 Homebrew 软件、本机应用、空间清理、系统优化与 AI 工具。
            每一步操作都先预览，再确认。
          </p>
          <div className="hero-actions">
            <button className="btn primary" onClick={() => onNavigate('clean')}>
              <Sparkles size={15} />
              开始清理
            </button>
            <button className="btn ghost" onClick={() => onNavigate('uninstall')}>
              <PackageMinus size={15} />
              卸载应用
            </button>
            <button className="btn subtle" onClick={() => sendTerminal('ma --help')}>
              <TerminalSquare size={15} />
              打开终端
            </button>
          </div>
        </div>
        <div className="hero-mark">
          <BrandMark size={112} />
          <div className="hero-mark-ring" />
        </div>
      </section>

      {!cliReady && (
        <section className="banner danger">
          <div className="banner-icon">
            <CircleX size={18} />
          </div>
          <div className="banner-body">
            <strong>未找到 mactools 命令行工具</strong>
            <p>{envError || 'GUI 依赖已安装的 ma 命令。先通过 Homebrew 安装，然后重新打开应用。'}</p>
            <code>brew install tingfeng347/tap/mactools</code>
          </div>
          <button className="btn ghost sm" onClick={() => copy('brew install tingfeng347/tap/mactools')}>
            复制命令
          </button>
        </section>
      )}

      {cliReady && !brewReady && (
        <section className="banner warn">
          <div className="banner-icon">
            <Package size={18} />
          </div>
          <div className="banner-body">
            <strong>未检测到 Homebrew</strong>
            <p>软件安装、更新与镜像功能需要 Homebrew。mactools 不会自动修改你的系统。</p>
          </div>
          <button className="btn ghost sm" onClick={() => api.openUrl('https://brew.sh/')}>
            查看安装方法
          </button>
        </section>
      )}

      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-icon violet">
            <Package size={17} />
          </div>
          <div>
            <span>Homebrew 软件</span>
            <strong>{env ? env.formulaCount + env.caskCount : '—'}</strong>
            <em>{env ? `${env.formulaCount} Formula · ${env.caskCount} Cask` : '正在读取'}</em>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon cyan">
            <HardDrive size={17} />
          </div>
          <div>
            <span>可用空间</span>
            <strong>{env ? formatBytes(env.diskFree) : '—'}</strong>
            <em>{env ? `共 ${formatBytes(env.diskTotal)}` : '正在读取'}</em>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon green">
            <TerminalSquare size={17} />
          </div>
          <div>
            <span>mactools</span>
            <strong>{env?.cliVersion ? `v${env.cliVersion}` : '—'}</strong>
            <em>{env?.cliPath ? env.cliPath.replace(env.home, '~') : '未检测到'}</em>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon amber">
            <Bot size={17} />
          </div>
          <div>
            <span>AI 工具</span>
            <strong>8</strong>
            <em>Codex · Claude · Gemini …</em>
          </div>
        </div>
      </div>

      <div className="overview-columns">
        <Section title="快捷操作" desc="常用流程，一步直达" className="quick-section">
          <div className="quick-grid">
            {QUICK.map((item) => {
              const Icon = item.icon;
              return (
                <button key={item.id} className={`quick-card ${item.tone}`} onClick={() => onNavigate(item.id)}>
                  <span className="quick-icon">
                    <Icon size={19} />
                  </span>
                  <span className="quick-text">
                    <strong>{item.title}</strong>
                    <em>{item.desc}</em>
                  </span>
                  <ChevronRight size={16} className="quick-arrow" />
                </button>
              );
            })}
          </div>
          <div className="activity">
            <div className="activity-head">
              <span>最近活动</span>
              <button onClick={() => onNavigate('terminal')}>
                打开终端 <ArrowRight size={12} />
              </button>
            </div>
            {runs.length === 0 && <div className="activity-empty">还没有执行记录。从上方选择一项开始。</div>}
            {[...runs]
              .reverse()
              .slice(0, 5)
              .map((item) => (
                <div key={item.id} className="activity-row">
                  <span className={cn('activity-dot', item.status)} />
                  <div className="activity-label">
                    <strong>{item.label}</strong>
                    <em>ma {item.args.join(' ')}</em>
                  </div>
                  <span className="activity-time">{relativeTime(item.startedAt)}</span>
                  {item.status === 'success' && <Check size={13} className="text-ok" />}
                  {item.status === 'error' && <CircleX size={13} className="text-bad" />}
                </div>
              ))}
          </div>
        </Section>

        <div className="overview-side">
          <Section title="磁盘占用" desc="系统数据卷">
            <div className="disk-card">
              <div className="disk-ring">
                <svg viewBox="0 0 100 100">
                  <circle className="ring-track" cx="50" cy="50" r="42" />
                  <circle
                    className="ring-value"
                    cx="50"
                    cy="50"
                    r="42"
                    strokeDasharray={`${(usedPct / 100) * circumference} ${circumference}`}
                  />
                </svg>
                <div className="disk-center">
                  <strong>{usedPct}%</strong>
                  <span>已使用</span>
                </div>
              </div>
              <div className="disk-legend">
                <div>
                  <span className="legend-dot used" />
                  已使用
                  <em>{formatBytes(used)}</em>
                </div>
                <div>
                  <span className="legend-dot free" />
                  可用
                  <em>{formatBytes(env?.diskFree ?? 0)}</em>
                </div>
              </div>
            </div>
          </Section>

          <Section title="系统信息">
            <div className="info-list">
              <div>
                <span>macOS</span>
                <strong>{env?.macos || '—'}</strong>
              </div>
              <div>
                <span>架构</span>
                <strong>{env?.arch === 'arm64' ? 'Apple Silicon' : env?.arch === 'x86_64' ? 'Intel' : env?.arch || '—'}</strong>
              </div>
              <div>
                <span>主机</span>
                <strong>{env?.hostname || '—'}</strong>
              </div>
              <div>
                <span>Homebrew</span>
                <strong className={brewReady ? 'text-ok' : 'text-warn'}>
                  {brewReady ? env?.brewVersion || '已安装' : '未安装'}
                </strong>
              </div>
              <div>
                <span>Python</span>
                <strong>{env?.pythonPath ? '已就绪' : '未检测到'}</strong>
              </div>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
