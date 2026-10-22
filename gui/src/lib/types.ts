export type EnvInfo = {
  appVersion: string;
  cliPath: string;
  cliVersion: string;
  cliOutdated: boolean;
  brewPath: string;
  brewVersion: string;
  pythonPath: string;
  macos: string;
  arch: string;
  hostname: string;
  user: string;
  home: string;
  diskTotal: number;
  diskFree: number;
  formulaCount: number;
  caskCount: number;
};

export type AppEntry = {
  id: string;
  name: string;
  path: string;
  bundleId: string;
  version: string;
  size: number;
  system: boolean;
};

export type PackageSource = 'formula' | 'cask' | 'app';

export type PackageEntry = {
  source: PackageSource;
  name: string;
  displayName: string;
  current: string;
  target: string;
  appPath: string | null;
};

export type StreamEvent = {
  runId: string;
  stream: 'stdout' | 'stderr';
  line: string;
};

export type LogLine = {
  stream: 'stdout' | 'stderr' | 'meta';
  text: string;
  at: number;
};

export type RunRecord = {
  id: string;
  label: string;
  args: string[];
  group: string;
  status: 'running' | 'success' | 'error';
  code: number | null;
  startedAt: number;
  finishedAt?: number;
  lines: LogLine[];
};

export type ConfirmOptions = {
  title: string;
  description: string;
  details?: string[];
  confirmLabel?: string;
  danger?: boolean;
};

export type PageId =
  | 'overview'
  | 'install'
  | 'uninstall'
  | 'update'
  | 'clean'
  | 'optimize'
  | 'mirror'
  | 'ai'
  | 'doctor'
  | 'terminal';
