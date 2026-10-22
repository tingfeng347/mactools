export type CleanItem = {
  id: string;
  title: string;
  note: string;
  group: 'Homebrew' | '开发缓存' | '系统缓存';
  tone: 'blue' | 'violet' | 'cyan' | 'amber' | 'rose' | 'green';
};

export const CLEAN_ITEMS: CleanItem[] = [
  { id: 'formula-cache', title: 'Formula 下载缓存', note: '清理 Homebrew 安装包下载缓存', group: 'Homebrew', tone: 'amber' },
  { id: 'cask-cache', title: 'Cask 安装包缓存', note: '清理已下载的 cask 安装镜像', group: 'Homebrew', tone: 'amber' },
  { id: 'autoremove', title: '无用依赖', note: '移除不再被依赖的 formula', group: 'Homebrew', tone: 'amber' },
  { id: 'brew-cache', title: 'Homebrew 完整缓存', note: '下载缓存与旧版本清理', group: 'Homebrew', tone: 'amber' },

  { id: 'pip-cache', title: 'pip 下载缓存', note: '保留虚拟环境与已安装包', group: '开发缓存', tone: 'violet' },
  { id: 'npm-cache', title: 'npm 下载缓存', note: '保留 node_modules 与全局工具', group: '开发缓存', tone: 'violet' },
  { id: 'pnpm-store', title: 'pnpm 无引用包', note: '仅清理未被引用的存储条目', group: '开发缓存', tone: 'violet' },
  { id: 'uv-cache', title: 'uv 无引用缓存', note: '保留项目虚拟环境', group: '开发缓存', tone: 'violet' },
  { id: 'go-cache', title: 'Go 构建缓存', note: '保留模块下载与源码', group: '开发缓存', tone: 'violet' },
  { id: 'ccache', title: 'C/C++ 编译缓存', note: '清理 ccache 编译结果', group: '开发缓存', tone: 'violet' },
  { id: 'rust-cache', title: 'Rust 下载缓存', note: '保留源码与工具链', group: '开发缓存', tone: 'violet' },
  { id: 'jvm-cache', title: 'Gradle 构建缓存', note: '构建缓存与日志', group: '开发缓存', tone: 'violet' },
  { id: 'frontend-cache', title: '前端构建缓存', note: 'Vite / Turbo / Webpack', group: '开发缓存', tone: 'violet' },
  { id: 'ruby-cache', title: 'Ruby / Perl 缓存', note: '下载与构建缓存', group: '开发缓存', tone: 'violet' },
  { id: 'language-cache', title: '其他语言缓存', note: 'Python / mise / PHP / Hex', group: '开发缓存', tone: 'violet' },
  { id: 'build-tools-cache', title: '构建工具缓存', note: 'Docker / CI / Shell / API', group: '开发缓存', tone: 'violet' },
  { id: 'ai-cache', title: 'AI 工具旧版本', note: '保留当前版本与会话', group: '开发缓存', tone: 'violet' },
  { id: 'cloud-cli-cache', title: '云工具缓存', note: 'GitHub / 云 CLI 缓存', group: '开发缓存', tone: 'violet' },
  { id: 'xcode-derived', title: 'Xcode 构建缓存', note: '仅 DerivedData，保留 Archives', group: '开发缓存', tone: 'violet' },
  { id: 'xcode-extra', title: 'Xcode 额外缓存', note: '文档缓存与测试设备', group: '开发缓存', tone: 'violet' },
  { id: 'editor-cache', title: '编辑器缓存', note: 'VS Code / JetBrains', group: '开发缓存', tone: 'violet' },
  { id: 'project-cache', title: '项目构建缓存', note: '可再生缓存，保护 Git 文件', group: '开发缓存', tone: 'violet' },

  { id: 'user-cache', title: '用户缓存与日志', note: '清理 ~/Library 中的可再生数据', group: '系统缓存', tone: 'cyan' },
  { id: 'app-cache', title: '应用沙盒缓存', note: '应用与容器缓存', group: '系统缓存', tone: 'cyan' },
  { id: 'browser-cache', title: '浏览器缓存', note: '网页与图形缓存', group: '系统缓存', tone: 'cyan' },
  { id: 'cloud-cache', title: '云盘缓存', note: '本地可再生缓存', group: '系统缓存', tone: 'cyan' },
  { id: 'office-cache', title: '办公软件缓存', note: 'Office / WPS 缓存', group: '系统缓存', tone: 'cyan' },
  { id: 'app-tools', title: '常用软件缓存', note: '通信、设计和影音缓存', group: '系统缓存', tone: 'cyan' },
  { id: 'vm-cache', title: '虚拟化缓存', note: '保留虚拟磁盘', group: '系统缓存', tone: 'cyan' },
  { id: 'app-logs', title: '应用日志', note: 'Application Support 日志', group: '系统缓存', tone: 'cyan' },
];

export type OptimizeTask = {
  id: string;
  title: string;
  note: string;
  admin: boolean;
};

export const OPTIMIZE_TASKS: OptimizeTask[] = [
  { id: 'cache-refresh', title: 'Finder 缓存刷新', note: '刷新 QuickLook 与图标服务缓存', admin: false },
  { id: 'saved-state-cleanup', title: '旧应用恢复状态', note: '删除超过 30 天的窗口恢复状态', admin: false },
  { id: 'fix-broken-configs', title: '修复损坏配置', note: '删除损坏的第三方偏好设置', admin: false },
  { id: 'network-optimization', title: 'DNS 缓存刷新', note: '向 mDNSResponder 发送 HUP', admin: true },
  { id: 'sqlite-vacuum', title: '数据库压缩', note: '压缩 Mail、Safari、Messages 数据库', admin: false },
  { id: 'prevent-network-dsstore', title: 'Finder 文件写入设置', note: '停止在网络盘与 USB 盘写入 .DS_Store', admin: false },
  { id: 'legacy-overrides-audit', title: '恢复旧优化设置', note: '移除旧工具设置的覆盖项', admin: false },
  { id: 'network-stack-optimize', title: '网络路由修复', note: '故障时刷新路由与 ARP', admin: true },
  { id: 'disk-permissions-repair', title: '用户权限修复', note: '异常时重置当前用户主目录权限', admin: true },
  { id: 'spotlight-index-optimize', title: 'Spotlight 索引优化', note: '搜索持续缓慢时重建索引', admin: true },
  { id: 'spotlight-orphan-rules-cleanup', title: 'Spotlight 失效规则', note: '移除已卸载应用的搜索规则', admin: false },
  { id: 'periodic-maintenance', title: '系统定期维护', note: '运行 daily / weekly / monthly', admin: true },
  { id: 'shared-file-list-repair', title: 'Finder 列表修复', note: '修复损坏的收藏和共享列表', admin: false },
  { id: 'disk-verify', title: '磁盘文件系统检查', note: '仅检查，不修复磁盘', admin: false },
  { id: 'login-items-audit', title: '登录项检查', note: '检查失效的登录项', admin: false },
  { id: 'quarantine-cleanup', title: '下载记录清理', note: '清空 Gatekeeper 下载跟踪', admin: false },
  { id: 'launch-agents-cleanup', title: '启动代理检查', note: '报告可执行文件缺失的代理', admin: false },
  { id: 'notification-cleanup', title: '旧通知清理', note: '数据库过大时清理 30 天前通知', admin: false },
  { id: 'coreduet-cleanup', title: '旧使用记录清理', note: '数据库过大时清理 90 天前记录', admin: false },
];

export type AiTool = {
  id: string;
  name: string;
  note: string;
  color: string;
};

export const AI_TOOLS: AiTool[] = [
  { id: 'codex', name: 'Codex', note: 'OpenAI 终端编程代理', color: '#7dd3fc' },
  { id: 'claude', name: 'Claude Code', note: 'Anthropic 编程代理', color: '#f0b48f' },
  { id: 'opencode', name: 'OpenCode', note: '开源终端 AI 代理', color: '#a5b4fc' },
  { id: 'qodercn', name: 'Qoder CN', note: '通义 Qoder 命令行', color: '#86efac' },
  { id: 'gemini', name: 'Gemini CLI', note: 'Google Gemini 终端工具', color: '#93c5fd' },
  { id: 'qwen', name: 'Qwen Code', note: '通义千问编程 CLI', color: '#c4b5fd' },
  { id: 'zcode', name: 'ZCode', note: '智谱 ZCode 编程工具', color: '#67e8f9' },
  { id: 'pi', name: 'Pi', note: '轻量 AI 编程助手', color: '#fda4af' },
];

export type MirrorInfo = {
  id: string;
  name: string;
  note: string;
};

export const MIRRORS: MirrorInfo[] = [
  { id: 'official', name: '官方源', note: 'Homebrew 默认线路' },
  { id: 'tuna', name: '清华 TUNA', note: '国内教育网镜像' },
  { id: 'ustc', name: '中科大 USTC', note: '国内教育网镜像' },
];
