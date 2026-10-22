import { invoke } from '@tauri-apps/api/core';
import type { AppEntry, EnvInfo, PackageEntry } from './types';

export const api = {
  environment: () => invoke<EnvInfo>('environment'),
  scanApps: () => invoke<AppEntry[]>('scan_apps'),
  searchPackages: (action: 'install' | 'remove' | 'update', query: string, source?: 'formula' | 'cask') =>
    invoke<PackageEntry[]>('search_packages', { action, query, source: source ?? null }),
  appIcon: (path: string) => invoke<string | null>('app_icon', { path }),
  revealPath: (path: string) => invoke<void>('reveal_path', { path }),
  openUrl: (url: string) => invoke<void>('open_url', { url }),
  saveText: (path: string, content: string) => invoke<void>('save_text', { path, content }),
  run: (runId: string, args: string[]) => invoke<{ code: number | null }>('run_mactools', { runId, args }),
  ptyStart: (id: string, cols: number, rows: number) => invoke<void>('pty_start', { id, cols, rows }),
  ptyWrite: (id: string, data: string) => invoke<void>('pty_write', { id, data }),
  ptyResize: (id: string, cols: number, rows: number) => invoke<void>('pty_resize', { id, cols, rows }),
  ptyKill: (id: string) => invoke<void>('pty_kill', { id }),
};

export function newId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
