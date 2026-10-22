import type { PageId } from './types';

export const PAGES: Record<PageId, { title: string; sub: string }> = {
  overview: { title: '概览', sub: '系统状态与快捷操作' },
  install: { title: '安装软件', sub: '搜索并安装 Formula / Cask' },
  uninstall: { title: '卸载应用', sub: '预览应用残留，再决定是否清理' },
  update: { title: '更新软件', sub: '检查并升级所有可更新项目' },
  clean: { title: '空间清理', sub: '按项目预览，只清理你选择的内容' },
  optimize: { title: '系统优化', sub: '刷新缓存与服务，检查系统健康' },
  mirror: { title: '镜像源', sub: '测速、切换与恢复 Homebrew 镜像' },
  ai: { title: 'AI 工具', sub: '统一管理终端 AI 编程工具' },
  doctor: { title: '环境检查', sub: '诊断 Homebrew、缓存与连通性' },
  terminal: { title: '终端', sub: '运行完整的 mactools 交互命令' },
};
