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

BUNDLE_SHA256 = '2412d78b0a89cfc78b7dc6f7ba6605ee410952fbf7eb5f0e801628d25b415c91'


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
    ]
    description = text(
        '复用 Mole 清理源码及白名单、运行状态和路径保护。预览列出当前候选；执行时重新检查，只处理已预览且身份未变化的目标。不会请求管理员权限。',
        'Uses Mole cleanup source with whitelist, activity and path guards. Preview lists current candidates; apply rechecks them and only processes reviewed, unchanged targets. No administrator access is requested.')
    return {name: (kind, text(zh, en), text(scope_zh, scope_en), description)
            for name, kind, zh, en, scope_zh, scope_en in entries}


MOLE_DEV_ACTIONS = frozenset(('rust-cache', 'jvm-cache', 'frontend-cache', 'ruby-cache',
                              'cloud-cli-cache', 'ai-cache', 'editor-cache',
                              'xcode-extra', 'project-cache', 'project-artifacts',
                              'language-cache', 'build-tools-cache'))


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


def run_engine(argv, env, timeout):
    # A timed-out shell may have scanners and owner commands running. Terminate
    # its whole session and reap it before returning control to ma.
    process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    previous_term = None
    if threading.current_thread() is threading.main_thread():
        previous_term = signal.getsignal(signal.SIGTERM)

        def terminate(signum, frame):
            raise SystemExit(128 + signum)

        # fzf terminates the old preview when the cursor moves. Convert its
        # SIGTERM to normal unwinding so the scanner's separate group stops.
        signal.signal(signal.SIGTERM, terminate)
    try:
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
        status = run_engine(self.argv + [mode, str(self.ledger)], env,
                            self.timeout if mode == 'preview' else 600)
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
        return self._run('apply')
