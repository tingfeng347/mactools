"""Explicit, previewed access to Mole's system optimization tasks."""
import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mactools_mole import run_engine, unpack_bundle
from mactools_runtime import confirm, text
from mactools_packages import cell, clip_cell, selector_widths


def optimize_info():
    # Keep order and IDs aligned with the pinned upstream optimization catalog.
    entries = [
        ('cache-refresh', 'Finder 缓存刷新', 'Finder cache refresh', False, '刷新 QuickLook 与图标服务缓存', 'Refresh QuickLook and icon-service caches'),
        ('saved-state-cleanup', '旧应用恢复状态', 'Old application states', False, '删除超过 30 天的窗口恢复状态', 'Remove window restoration state older than 30 days'),
        ('fix-broken-configs', '修复损坏配置', 'Repair broken preferences', False, '删除损坏的第三方偏好设置；应用会重置该配置', 'Remove corrupt third-party preferences; applications reset those settings'),
        ('network-optimization', 'DNS 缓存刷新', 'DNS cache refresh', True, '向 mDNSResponder 发送 HUP；VPN 活动或状态不明时跳过', 'Send HUP to mDNSResponder; skip active or unknown VPN state'),
        ('sqlite-vacuum', '数据库压缩', 'Database optimization', False, '压缩 Mail、Safari、Messages 数据库；应用运行时跳过', 'Vacuum Mail, Safari and Messages databases; skip running applications'),
        ('prevent-network-dsstore', 'Finder 文件写入设置', 'Finder metadata preferences', False, '持久设置：停止在网络盘与 USB 盘写入 .DS_Store', 'Persist preferences to stop .DS_Store writes on network and USB volumes'),
        ('legacy-overrides-audit', '恢复旧优化设置', 'Restore legacy overrides', False, '移除旧工具设置的 App Nap 与磁盘镜像验证覆盖', 'Remove legacy App Nap and disk-image verification overrides'),
        ('network-stack-optimize', '网络路由修复', 'Network stack refresh', True, '故障时刷新路由和 ARP；可能短暂影响连接；VPN 时跳过', 'Refresh routing and ARP on detected faults; may interrupt connections; skip VPN'),
        ('disk-permissions-repair', '用户权限修复', 'User permission repair', True, '检测异常后重置当前用户主目录权限', 'Reset current user home permissions only when faults are detected'),
        ('spotlight-index-optimize', 'Spotlight 索引优化', 'Spotlight index optimization', True, '接通电源且搜索持续缓慢时重建索引；后台可能持续数小时', 'Rebuild on AC power after repeatedly slow searches; background work may take hours'),
        ('spotlight-orphan-rules-cleanup', 'Spotlight 失效规则', 'Orphan Spotlight rules', False, '移除已卸载应用的搜索设置规则', 'Remove search-preference rules for uninstalled applications'),
        ('periodic-maintenance', '系统定期维护', 'Periodic maintenance', True, '仅系统支持且日志超过 7 天时运行 daily/weekly/monthly', 'Run daily/weekly/monthly only when supported and logs are stale for 7 days'),
        ('shared-file-list-repair', 'Finder 列表修复', 'Finder shared-list repair', False, '修复损坏的收藏和共享列表；遵循受保护路径规则', 'Repair corrupt favorites and shared lists; preserve protected paths'),
        ('disk-verify', '磁盘文件系统检查', 'Disk filesystem verification', False, '仅 MOLE_ENABLE_DISK_VERIFY=1 时检查；不修复磁盘', 'Requires MOLE_ENABLE_DISK_VERIFY=1; verify only, no disk repair'),
        ('login-items-audit', '登录项检查', 'Login item audit', False, '仅检查，建议在系统设置手动移除失效项；可能查询现有管理员授权', 'Audit only; remove broken items manually in System Settings; may use existing admin authorization'),
        ('quarantine-cleanup', '下载记录清理', 'Download history cleanup', False, '清空 Gatekeeper 下载跟踪记录；不修改文件隔离标记', 'Clear Gatekeeper download tracking history; preserve file quarantine flags'),
        ('launch-agents-cleanup', '启动代理检查', 'Launch agent audit', False, '仅报告可执行文件缺失的用户代理；不卸载服务', 'Report user agents with missing executables; do not unload services'),
        ('notification-cleanup', '旧通知清理', 'Old notification cleanup', False, '数据库超过 50 MiB 时删除 30 天前通知并重启 NotificationCenter', 'Above 50 MiB, remove notifications older than 30 days and restart NotificationCenter'),
        ('coreduet-cleanup', '旧使用记录清理', 'Old usage history cleanup', False, '数据库超过 100 MiB 时删除 90 天前的使用记录', 'Above 100 MiB, remove usage records older than 90 days'),
    ]
    return {key: {'title': text(zh, en), 'admin': admin, 'effect': text(effect_zh, effect_en)}
            for key, zh, en, admin, effect_zh, effect_en in entries}


def describe(name):
    info = optimize_info()[name]
    permission = text('按需管理员权限', 'Administrator access when needed') if info['admin'] else text('用户权限', 'User permissions')
    return f"{name} · {info['title']}\n{permission}\n{info['effect']}"


class OptimizePlan:
    def __init__(self, names, timeout=60):
        if not names or any(name not in optimize_info() for name in names):
            raise ValueError('Unknown or empty optimization selection')
        self.names = list(dict.fromkeys(names))
        self.timeout = timeout
        self._temporary = tempfile.TemporaryDirectory(prefix='mactools-optimize-')
        self.path = Path(self._temporary.name)
        self.reviewed = False
        self._unpacked = False

    def close(self):
        self._temporary.cleanup()

    def _run(self, mode):
        if not self._unpacked:
            unpack_bundle(self.path / 'engine')
            self._unpacked = True
        env = dict(os.environ, HOME=str(Path.home().resolve()),
                   MOLE_DRY_RUN='1' if mode == 'preview' else '0',
                   MOLE_OPTIMIZE_SUDO_AVAILABLE='false')
        for key in list(env):
            if key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'MOLE_TEST_MODE') or key.startswith('BASH_FUNC_'):
                env.pop(key, None)
        argv = ['/bin/bash', str(self.path / 'engine/mactools-optimize.sh'), mode,
                str(self.path / 'approved.bin'), *self.names]
        if mode == 'apply':
            status = run_engine(argv, env, self.timeout * len(self.names), interactive=True)
        else:
            status = run_engine(argv, env, self.timeout * len(self.names))
        if status:
            raise ValueError(text(f'优化未完成（状态 {status}），请检查上方任务结果。',
                                  f'Optimization incomplete (status {status}); review task outcomes above.'))

    def preview(self):
        self.reviewed = False
        (self.path / 'approved.bin').write_bytes(b'')
        (self.path / 'approved.bin').chmod(0o600)
        for name in self.names:
            print(describe(name), flush=True)
        print(text('\nMole 优化预览（不会更改系统）：', '\nMole optimization preview (no system changes):'), flush=True)
        self._run('preview')
        self.reviewed = True

    def apply(self):
        if not self.reviewed:
            raise ValueError('Preview the optimization plan before applying it.')
        try:
            self._run('apply')
        finally:
            self.reviewed = False


def select_tasks():
    info = optimize_info()
    if not shutil.which('fzf'):
        raise ValueError(text('请指定任务名，或使用 --list 查看任务。', 'Specify task names, or use --list to inspect tasks.'))
    name_width, _ = selector_widths('install')
    scope_width = 20
    colored = not os.environ.get('NO_COLOR') and os.environ.get('TERM') != 'dumb'
    rows = []
    for name, data in info.items():
        kind = 'system' if data['admin'] else 'user'
        permission = text('管理员（按需）', 'Admin when needed') if data['admin'] else text('用户权限', 'User permissions')
        kind_cell = cell(kind, 8)
        if colored:
            kind_cell = '\033[38;5;75m' + kind_cell + '\033[0m'
        visible = kind_cell + '\t' + cell(clip_cell(data['title'], name_width), name_width) + '\t' + cell(clip_cell(permission, scope_width), scope_width, right=True)
        rows.append('\t'.join((name, data['title'], kind, permission, visible)))
    headings = cell(text('类型', 'Type'), 8) + ' ' + cell(text('名称', 'Name'), name_width, center=True) + ' ' + cell(text('权限', 'Permissions'), scope_width, right=True)
    preview = shlex.join([sys.executable, str(Path(__file__).resolve()), '--detail']) + ' {1}'
    env = dict(os.environ, SHELL='/bin/sh')
    env.pop('FZF_DEFAULT_OPTS', None)
    env.pop('FZF_DEFAULT_OPTS_FILE', None)
    query, exact = '', False
    while True:
        argv = ['fzf', '--ansi', '--multi', '--layout=reverse', '--border', '--height=95%',
            '--delimiter=\t', '--with-nth=5..', '--nth=2', '--tabstop=1', '--info=hidden', '--tiebreak=index',
            '--print-query', '--expect=ctrl-e,ctrl-r', '--query=' + query,
            '--preview=' + preview, '--preview-window=right:42%:wrap:<50(down:35%:wrap)',
            '--prompt=' + text('搜索 › ', 'Search › '),
            '--bind=tab:toggle+down,shift-tab:toggle+up,ctrl-a:select-all,ctrl-/:toggle-preview',
            '--header=' + text('Tab 多选  Enter 预览  Ctrl+R 刷新  Ctrl+E 匹配  Ctrl+/ 详情  Esc 退出', 'Tab select  Enter preview  Ctrl+R refresh  Ctrl+E match  Ctrl+/ details  Esc exit') + '\n\n' + headings]
        if exact:
            argv.append('--exact')
        result = subprocess.run(argv, input='\n'.join(rows) + '\n', text=True, stdout=subprocess.PIPE, env=env)
        if result.returncode in (1, 130):
            return []
        if result.returncode:
            raise ValueError(f'fzf failed (exit {result.returncode})')
        lines = result.stdout.splitlines()
        query = lines[0] if lines else ''
        key = lines[1] if len(lines) > 1 else ''
        if key == 'ctrl-e':
            exact = not exact
            continue
        if key == 'ctrl-r':
            continue
        chosen = [row.split('\t', 1)[0] for row in lines[2:]]
        if any(name not in info for name in chosen):
            raise ValueError('Unknown optimization selection')
        return chosen


def positive_timeout(value):
    try:
        value = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('expected 0 < seconds <= 600')
    if not 0 < value <= 600:
        raise argparse.ArgumentTypeError('expected 0 < seconds <= 600')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ma optimize', description=text('选择并预览 Mole 系统优化任务。', 'Select and preview Mole system optimization tasks.'))
    parser.add_argument('tasks', nargs='*', choices=None, metavar='TASK')
    parser.add_argument('--list', action='store_true', help=text('列出任务、权限与影响', 'List tasks, permissions and effects'))
    parser.add_argument('--dry-run', action='store_true', help=text('仅预览', 'Preview only'))
    parser.add_argument('--yes', '-y', action='store_true', help=text('明确指定任务后，预览并执行', 'Preview and apply explicitly named tasks'))
    parser.add_argument('--timeout', type=positive_timeout, default=60, metavar='SECONDS')
    parser.add_argument('--detail', choices=tuple(optimize_info()), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.detail:
        print(describe(args.detail))
        return 0
    unknown = [name for name in args.tasks if name not in optimize_info()]
    if unknown:
        parser.error('unknown task: ' + ', '.join(unknown))
    if args.list:
        for name in args.tasks or optimize_info():
            print(describe(name) + '\n')
        return 0
    if args.yes and not args.tasks:
        parser.error(text('--yes 必须明确指定任务名。', '--yes requires explicit task names.'))
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        names = args.tasks or (select_tasks() if interactive and not args.dry_run else list(optimize_info()))
        if not names:
            return 0
        plan = OptimizePlan(names, args.timeout)
        try:
            plan.preview()
            if args.dry_run or (not interactive and not args.yes):
                return 0
            if confirm(text('执行以上已选择的优化任务？', 'Apply the selected optimization tasks above?'), args.yes):
                plan.apply()
            return 0
        finally:
            plan.close()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(text('错误：', 'Error: ') + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
