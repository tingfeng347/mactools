import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { listen } from '@tauri-apps/api/event';
import { api, newId } from './lib/api';
import type {
  ConfirmOptions,
  EnvInfo,
  LogLine,
  RunRecord,
  StreamEvent,
} from './lib/types';

type Toast = {
  id: string;
  message: string;
  tone: 'success' | 'error' | 'info';
};

type TerminalCommand = {
  nonce: string;
  text: string;
};

type AppContextValue = {
  env: EnvInfo | null;
  envError: string;
  refreshEnv: () => Promise<void>;
  runs: RunRecord[];
  activeRun: RunRecord | null;
  consoleOpen: boolean;
  setConsoleOpen: (open: boolean) => void;
  run: (args: string[], meta: { label: string; group: string; background?: boolean }) => Promise<RunRecord>;
  confirmAction: (options: ConfirmOptions) => Promise<boolean>;
  confirmState: { options: ConfirmOptions; resolve: (value: boolean) => void } | null;
  toasts: Toast[];
  toast: (message: string, tone?: Toast['tone']) => void;
  dismissToast: (id: string) => void;
  terminalOpen: boolean;
  setTerminalOpen: (open: boolean) => void;
  terminalCommand: TerminalCommand | null;
  sendTerminal: (text: string) => void;
};

const AppContext = createContext<AppContextValue | null>(null);

const MAX_RUNS = 40;

export function AppProvider({ children }: { children: ReactNode }) {
  const [env, setEnv] = useState<EnvInfo | null>(null);
  const [envError, setEnvError] = useState('');
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [confirmState, setConfirmState] = useState<{
    options: ConfirmOptions;
    resolve: (value: boolean) => void;
  } | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalCommand, setTerminalCommand] = useState<TerminalCommand | null>(null);
  const runsRef = useRef<RunRecord[]>([]);

  const commitRuns = useCallback((updater: (current: RunRecord[]) => RunRecord[]) => {
    const next = updater(runsRef.current);
    runsRef.current = next;
    setRuns(next);
  }, []);

  const refreshEnv = useCallback(async () => {
    try {
      const info = await api.environment();
      setEnv(info);
      setEnvError(info.cliPath ? '' : '未找到 mactools 命令行工具');
    } catch (error) {
      setEnvError(String(error));
    }
  }, []);

  useEffect(() => {
    void refreshEnv();
  }, [refreshEnv]);

  useEffect(() => {
    let mounted = true;
    let unlisten: (() => void) | undefined;
    void listen<StreamEvent>('cmd-output', (event) => {
      const payload = event.payload;
      commitRuns((current) =>
        current.map((record) =>
          record.id === payload.runId
            ? {
                ...record,
                lines: [
                  ...record.lines,
                  { stream: payload.stream, text: payload.line, at: Date.now() },
                ].slice(-4000),
              }
            : record,
        ),
      );
    }).then((dispose) => {
      if (mounted) unlisten = dispose;
      else dispose();
    });
    return () => {
      mounted = false;
      unlisten?.();
    };
  }, [commitRuns]);

  const toast = useCallback((message: string, tone: Toast['tone'] = 'info') => {
    const id = newId();
    setToasts((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 4600);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const run = useCallback(
    async (args: string[], meta: { label: string; group: string; background?: boolean }) => {
      const record: RunRecord = {
        id: newId(),
        label: meta.label,
        args,
        group: meta.group,
        status: 'running',
        code: null,
        startedAt: Date.now(),
        lines: [{ stream: 'meta', text: `$ ma ${args.join(' ')}`, at: Date.now() }],
      };
      commitRuns((current) => [...current, record].slice(-MAX_RUNS));
      setActiveRunId(record.id);
      if (!meta.background) setConsoleOpen(true);
      try {
        const result = await api.run(record.id, args);
        const code = result.code ?? 0;
        const status = code === 0 ? 'success' : 'error';
        commitRuns((current) =>
          current.map((item) =>
            item.id === record.id
              ? {
                  ...item,
                  status,
                  code,
                  finishedAt: Date.now(),
                  lines: [
                    ...item.lines,
                    {
                      stream: code === 0 ? 'meta' : 'stderr',
                      text: code === 0 ? '完成' : `命令退出码 ${code}`,
                      at: Date.now(),
                    },
                  ],
                }
              : item,
          ),
        );
      } catch (error) {
        commitRuns((current) =>
          current.map((item) =>
            item.id === record.id
              ? {
                  ...item,
                  status: 'error',
                  code: -1,
                  finishedAt: Date.now(),
                  lines: [
                    ...item.lines,
                    { stream: 'stderr', text: String(error), at: Date.now() },
                  ],
                }
              : item,
          ),
        );
      }
      return runsRef.current.find((item) => item.id === record.id) ?? record;
    },
    [commitRuns],
  );

  const confirmAction = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setConfirmState({ options, resolve });
      }),
    [],
  );

  const sendTerminal = useCallback((text: string) => {
    setTerminalOpen(true);
    setTerminalCommand({ nonce: newId(), text });
  }, []);

  const activeRun = useMemo(
    () => runs.find((item) => item.id === activeRunId) ?? runs[runs.length - 1] ?? null,
    [runs, activeRunId],
  );

  const value: AppContextValue = {
    env,
    envError,
    refreshEnv,
    runs,
    activeRun,
    consoleOpen,
    setConsoleOpen,
    run,
    confirmAction,
    confirmState,
    toasts,
    toast,
    dismissToast,
    terminalOpen,
    setTerminalOpen,
    terminalCommand,
    sendTerminal,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}

export function outputText(run: RunRecord | null | undefined): string {
  if (!run) return '';
  return run.lines
    .filter((line) => line.stream !== 'meta')
    .map((line) => line.text)
    .join('\n');
}

export function isDangerousLine(line: LogLine): boolean {
  return /失败|错误|error|failed|FAIL|拒绝|cannot|EACCES/i.test(line.text);
}
