"""Mole's vendored GPL cleanup engine, behind ma's preview and confirmation."""
import hashlib
import os
import signal
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

from mactools_runtime import text

BUNDLE_SHA256 = '23d2d5f0bacdf960a4a2f77950c4528290681ac67e339613ca9ff9d227eddf9c'


def mole_info():
    entries = [
        ('user-cache', 'cache', '用户缓存与日志', 'User caches and logs', '用户 Library 缓存', 'User Library caches'),
        ('app-cache', 'cache', '应用与沙盒缓存', 'Application caches', '应用和容器缓存', 'App / container caches'),
        ('browser-cache', 'browser', '浏览器缓存', 'Browser caches', '网页和图形缓存', 'Web / graphics caches'),
        ('cloud-cache', 'cloud', '云盘缓存', 'Cloud storage caches', '本地可再生缓存', 'Rebuildable local caches'),
        ('office-cache', 'office', '办公软件缓存', 'Office caches', 'Office / WPS 缓存', 'Office / WPS caches'),
        ('app-tools', 'app', '常用软件缓存', 'Utility app caches', '通信、设计和影音缓存', 'Chat / design / media caches'),
        ('vm-cache', 'vm', '虚拟化缓存', 'Virtualization caches', '缓存，保留虚拟磁盘', 'Caches, preserve VM disks'),
        ('app-logs', 'logs', '应用日志', 'Application logs', 'Application Support 日志', 'Application Support logs'),
        ('rust-cache', 'rust', 'Rust 下载缓存', 'Rust download cache', '保留源码与工具链', 'Preserve sources / toolchains'),
        ('jvm-cache', 'jvm', 'Gradle 构建缓存', 'Gradle build caches', '构建缓存与日志', 'Build caches / logs'),
        ('frontend-cache', 'web', '前端构建缓存', 'Frontend build caches', 'Vite / Turbo / Webpack', 'Vite / Turbo / Webpack'),
        ('ruby-cache', 'ruby', 'Ruby / Perl 缓存', 'Ruby / Perl caches', '下载与构建缓存', 'Download / build caches'),
        ('cloud-cli-cache', 'cli', '云工具缓存', 'Cloud CLI caches', 'GitHub / 云 CLI 缓存', 'GitHub / cloud CLI caches'),
        ('ai-cache', 'ai', 'AI 工具旧版本与缓存', 'AI tool caches and old versions', '保留当前版本与会话', 'Preserve active versions / sessions'),
        ('language-cache', 'lang', '其他语言与工具缓存', 'Other language tool caches', 'Python / mise / PHP / Hex', 'Python / mise / PHP / Hex'),
        ('build-tools-cache', 'build', '构建与命令行工具缓存', 'Build and CLI tool caches', 'Docker / CI / Shell / API', 'Docker / CI / Shell / API'),
        ('editor-cache', 'editor', '编辑器缓存与日志', 'Editor caches and logs', 'VS Code / JetBrains', 'VS Code / JetBrains'),
        ('xcode-extra', 'xcode', 'Xcode 额外缓存', 'Additional Xcode caches', '文档缓存与测试设备', 'Docs / XCTest devices'),
        ('project-cache', 'build', '项目构建缓存', 'Project build caches', '可再生缓存，保护 Git 文件', 'Rebuildable caches, Git guarded'),
        ('project-artifacts', 'build', '闲置项目依赖与产物', 'Inactive project artifacts', '闲置至少 7 天', 'Inactive at least 7 days'),
        ('installers', 'file', '下载的安装包', 'Downloaded installers', 'DMG / PKG / 安装压缩包', 'DMG / PKG / installer archives'),
        ('trash', 'trash', '清空废纸篓', 'Empty Trash', '仅清理已预览项目', 'Only reviewed Trash items'),
        ('recent-items', 'review', '检查最近使用记录', 'Review recent items history', '保留受保护的 macOS 记录与原文件', 'Preserve protected macOS history / originals'),
        ('mail-downloads', 'mail', '旧邮件下载附件', 'Old Mail downloads', 'Mail 退出后清理旧下载副本', 'Old download copies while Mail is closed'),
        ('incomplete-downloads', 'file', '闲置的未完成下载', 'Idle incomplete downloads', '保留正在使用的下载', 'Preserve active downloads'),
        ('saved-state', 'state', '应用保存状态', 'Saved application state', '关闭应用后的窗口恢复状态', 'Window restoration state for closed apps'),
        ('old-app-versions', 'app', '应用旧版本', 'Old application versions', '签名校验后保留当前版本', 'Signature guards preserve active versions'),
        ('orphan-app-data', 'app', '卸载后的应用残留', 'Orphaned application data', '保留已安装应用及用户资料', 'Protect installed apps and user documents'),
        ('orphan-services', 'system', '失效的系统服务注册', 'Orphaned system registrations', '需要时请求管理员权限', 'Administrator access when required'),
        ('system-cache', 'system', '系统旧缓存与日志', 'Old system caches and logs', '按保留期限清理；需要管理员权限', 'Age guarded; requires administrator access'),
        ('finder-metadata', 'review', '检查 Finder 元数据', 'Review Finder metadata', 'Mole 安全规则保留 .DS_Store', 'Mole safety rules preserve .DS_Store'),
        ('group-cache', 'cache', '共享容器缓存', 'Group container caches', '保留容器中的应用资料', 'Preserve application data in containers'),
        ('silicon-cache', 'cache', 'Apple Silicon 缓存', 'Apple Silicon caches', 'Rosetta 与 Metal 可再生缓存', 'Rebuildable Rosetta / Metal caches'),
        ('device-firmware', 'file', '设备固件下载缓存', 'Downloaded device firmware', '旧 iPhone / iPad 固件', 'Old iPhone / iPad firmware'),
        ('failed-backups', 'backup', '失败的 Time Machine 备份', 'Failed Time Machine backups', '仅过期且非活动的未完成备份', 'Only stale inactive incomplete backups'),
        ('mobile-cache', 'xcode', '移动开发与模拟器', 'Mobile development and simulators', '无效模拟器、旧设备支持与缓存', 'Unavailable simulators / old device support'),
        ('toolbox-cache', 'editor', 'JetBrains Toolbox 旧版本', 'Old JetBrains Toolbox versions', '保留当前使用版本', 'Preserve active versions'),
        ('nix-garbage', 'nix', 'Nix 无引用存储', 'Unreferenced Nix store paths', '仅删除预览时无引用的路径', 'Delete only reviewed dead store paths'),
        ('local-snapshots', 'review', '检查本地快照', 'Review local snapshots', '仅检查，交由 Time Machine 管理', 'Review only; managed by Time Machine'),
        ('system-updates', 'review', '检查系统更新暂存', 'Review OS update staging', '仅检查，交由系统更新管理', 'Review only; managed by Software Update'),
        ('large-files', 'review', '检查大文件', 'Review large files', '仅列出建议，不自动删除', 'Suggestions only, no automatic deletion'),
        ('periodic-maintenance', 'system', '系统定期维护', 'Periodic system maintenance', '系统支持时运行日常维护', 'Run periodic tasks when supported'),
        ('disk-verify', 'review', '检查磁盘文件系统', 'Verify disk filesystem', '需显式启用 MOLE_ENABLE_DISK_VERIFY=1', 'Opt in with MOLE_ENABLE_DISK_VERIFY=1'),
    ]
    description = text(
        '复用 Mole 清理源码及白名单、运行状态和路径保护。执行时重新检查已预览目标；系统项目在确认后按需请求管理员权限。标记为检查的项目遵循 Mole 的保留策略。',
        'Uses Mole source with whitelist, activity and path guards. Apply rechecks reviewed targets; system actions request administrator access after confirmation when required. Review actions follow Mole preservation policies.')
    return {name: (kind, text(zh, en), text(scope_zh, scope_en), description)
            for name, kind, zh, en, scope_zh, scope_en in entries}


MOLE_DEV_ACTIONS = frozenset(('rust-cache', 'jvm-cache', 'frontend-cache', 'ruby-cache',
                              'cloud-cli-cache', 'ai-cache', 'editor-cache',
                              'xcode-extra', 'project-cache', 'project-artifacts',
                              'language-cache', 'build-tools-cache', 'mobile-cache',
                              'toolbox-cache', 'nix-garbage'))


def unpack_bundle(destination):
    bundle = Path(__file__).with_name('mactools_mole.zip')
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != BUNDLE_SHA256:
        raise ValueError('Mole source bundle is incomplete or changed; reinstall mactools.')
    with zipfile.ZipFile(bundle) as archive:
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or item.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError('Unsafe path in Mole bundle')
        archive.extractall(destination)


def run_engine(argv, env, timeout, interactive=False):
    # A timed-out shell may have scanners and owner commands running. Terminate
    # its whole session and reap it before returning control to ma.
    if interactive and threading.current_thread() is not threading.main_thread():
        raise ValueError('Interactive Mole actions must run on the main thread.')
    # Confirmed actions retain their controlling terminal (including nested
    # Homebrew/sudo prompts), but own a process group for bounded cancellation.
    # os.setpgrp is the Python 3.9-compatible equivalent of process_group=0.
    process = subprocess.Popen(argv, env=env,
                               stdin=None if interactive else subprocess.DEVNULL,
                               start_new_session=not interactive,
                               preexec_fn=os.setpgrp if interactive else None)
    terminal_fd = foreground_group = previous_ttou = None
    previous_term = None
    if threading.current_thread() is threading.main_thread():
        previous_term = signal.getsignal(signal.SIGTERM)

        def terminate(signum, frame):
            raise SystemExit(128 + signum)

        # fzf terminates the old preview when the cursor moves. Convert its
        # SIGTERM to normal unwinding so the scanner's separate group stops.
        signal.signal(signal.SIGTERM, terminate)
    try:
        if interactive:
            try:
                terminal_fd = os.open('/dev/tty', os.O_RDWR)
            except OSError:
                # Noninteractive callers may still run actions whose owners
                # need no credentials. They receive no terminal handoff.
                if os.isatty(0):
                    raise
        if terminal_fd is not None:
            foreground_group = os.tcgetpgrp(terminal_fd)
            if foreground_group != os.getpgrp():
                raise ValueError('Run interactive Mole actions in the foreground.')
            previous_ttou = signal.getsignal(signal.SIGTTOU)
            signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            try:
                os.tcsetpgrp(terminal_fd, process.pid)
            except OSError:
                # A no-op owner command can exit before its group receives
                # the foreground. Its exit status remains authoritative.
                if process.poll() is not None:
                    return process.returncode
                raise
            # A fast child can read before the foreground transfer and receive
            # SIGTTIN. Resume it only after its group owns the terminal.
            try:
                os.killpg(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        return process.wait(timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        try:
            if terminal_fd is not None:
                try:
                    if foreground_group is not None and os.tcgetpgrp(terminal_fd) == process.pid:
                        os.tcsetpgrp(terminal_fd, foreground_group)
                finally:
                    os.close(terminal_fd)
        finally:
            if previous_ttou is not None:
                signal.signal(signal.SIGTTOU, previous_ttou)
            if previous_term is not None:
                signal.signal(signal.SIGTERM, previous_term)


class MolePlan:
    def __init__(self, name, timeout):
        if name not in mole_info():
            raise ValueError('Unknown Mole cleanup action')
        self.name, self.timeout = name, timeout
        self._temporary = tempfile.TemporaryDirectory(prefix='mactools-clean-')
        self.path = Path(self._temporary.name)
        self.argv = ['/bin/bash', str(self.path / 'engine/mactools-bridge.sh'), name]
        self.ledger = self.path / 'approved.bin'
        self.ledger.touch(mode=0o600)
        self.reviewed = False
        self._unpacked = False

    def close(self):
        self._temporary.cleanup()

    def _run(self, mode):
        if not self._unpacked:
            unpack_bundle(self.path / 'engine')
            self._unpacked = True
        env = dict(os.environ, HOME=str(Path.home().resolve()), MOLE_DRY_RUN='1' if mode == 'preview' else '0',
                   COREPACK_ENABLE_NETWORK='0', COREPACK_ENABLE_AUTO_PIN='0',
                   GOTOOLCHAIN='local', UV_OFFLINE='1', UV_PYTHON_DOWNLOADS='never',
                   PIP_DISABLE_PIP_VERSION_CHECK='1', HOMEBREW_NO_AUTO_UPDATE='1')
        # Parent shell hooks and function imports must not alter the engine.
        for key in list(env):
            if key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'MOLE_TEST_MODE') or key.startswith('BASH_FUNC_'):
                env.pop(key, None)
        arguments = (self.argv + [mode, str(self.ledger)], env,
                     self.timeout if mode == 'preview' else 600)
        if mode == 'apply' and self.name in ('system-cache', 'orphan-services',
                                            'mobile-cache', 'old-app-versions',
                                            'periodic-maintenance'):
            status = run_engine(*arguments, interactive=True)
        else:
            status = run_engine(*arguments)
        if status:
            raise ValueError(text(f'{self.name} 清理未完成（状态 {status}）。',
                                  f'{self.name} cleanup incomplete (status {status}).'))
        return status

    def preview(self):
        self.reviewed = False
        self.ledger.write_bytes(b'')
        print(text('Mole 清理预览：', 'Mole cleanup preview: ') + self.name, flush=True)
        self._run('preview')
        self.reviewed = True

    def apply(self, timeout):
        if not self.reviewed:
            raise ValueError('Preview the Mole cleanup plan before applying it.')
        try:
            return self._run('apply')
        finally:
            self.reviewed = False
