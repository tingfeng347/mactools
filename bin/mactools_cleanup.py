"""Read-only cache discovery and explicit developer-cache cleanup plans."""

import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from mactools_runtime import command, run, text


def cache_info():
    # Keep menu construction independent of tool startup and directory scans.
    return {
        'pip-cache': ('pip', text('pip 下载缓存', 'pip downloads'), text('下载与 wheel', 'Downloads / wheels'),
                      text('清理 pip 的 HTTP 和 wheel 缓存，保留虚拟环境及已安装的软件包。',
                           'Clear pip HTTP and wheel caches, preserving virtual environments and installed packages.')),
        'npm-cache': ('npm', text('npm 下载缓存', 'npm downloads'), text('包下载缓存', 'Package downloads'),
                      text('清理 npm 包下载缓存，保留 node_modules 和全局安装的工具。',
                           'Clear npm package downloads, preserving node_modules and globally installed tools.')),
        'pnpm-store': ('pnpm', text('pnpm 无引用缓存', 'pnpm unused store'), text('无引用的包', 'Unreferenced packages'),
                       text('使用 pnpm store prune 清理无引用的包，保留项目依赖目录。',
                            'Use pnpm store prune for unreferenced packages, preserving project dependency directories.')),
        'uv-cache': ('uv', text('uv 无引用缓存', 'uv unused cache'), text('无引用的缓存', 'Unreferenced cache'),
                     text('使用 uv cache prune 清理无引用缓存和缓存环境，保留项目虚拟环境。',
                          'Use uv cache prune for dangling cache entries and cached environments, preserving project virtual environments.')),
        'go-cache': ('go', text('Go 构建缓存', 'Go build cache'), text('编译缓存', 'Compiled objects'),
                     text('清理 Go 编译缓存，保留模块下载、源代码和安装的命令。',
                          'Clear the Go build cache, preserving downloaded modules, source code and installed commands.')),
        'ccache': ('ccache', text('C/C++ 编译缓存', 'C/C++ build cache'), text('编译缓存', 'Compiled objects'),
                   text('清理 ccache 编译结果，保留配置及项目文件。',
                        'Clear ccache compilation results, preserving configuration and project files.')),
        'xcode-derived': ('xcode', text('Xcode 构建缓存', 'Xcode build cache'), 'DerivedData',
                          text('仅清理默认 DerivedData 中的构建数据；保留 Archives、设备数据和项目源文件。请先停止 Xcode 构建。',
                               'Clear build data in the default DerivedData only; preserve Archives, device data and project sources. Stop Xcode builds first.')),
    }


def cache_path(value):
    path = Path(value.strip())
    if not value.strip() or '\n' in value.strip() or not path.is_absolute():
        raise ValueError(text('缓存工具未返回有效的绝对路径。', 'Cache tool did not return a valid absolute path.'))
    path = path.resolve()
    home = Path.home().resolve()
    if path == home or path in home.parents:
        raise ValueError(text('拒绝清理用户目录或其上级目录。', 'Refusing to clean the home directory or its ancestors.'))
    return path


def xcode_path():
    home = Path.home().absolute()
    path = home / 'Library/Developer/Xcode/DerivedData'
    if home.is_symlink():
        raise ValueError('Xcode home must not be a symbolic link')
    current = home
    for component in path.relative_to(home).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(text('拒绝清理包含符号链接的 Xcode 缓存路径。', 'Refusing an Xcode cache path containing symbolic links.'))
    return path


def cache_size(path, seconds=0.5, max_entries=20000):
    """Bounded, non-following size estimate; never block menu startup."""
    total, count, complete = 0, 0, True
    deadline = time.monotonic() + seconds
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    count += 1
                    if count > max_entries or time.monotonic() > deadline:
                        return total, False
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        total += info.st_size
        except OSError:
            complete = False
    return total, complete


def format_size(size):
    value = float(size)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024


@dataclass
class CachePlan:
    name: str
    path: Path
    argv: list
    env: dict
    cwd: str

    @property
    def contents(self):
        # npm's configured cache root can also contain npx environments and
        # user-managed tools. `cache clean` targets _cacache, not that root.
        return self.path / '_cacache' if self.name == 'npm-cache' else self.path

    def preview(self):
        print(text('缓存目录：', 'Cache directory: ') + str(self.contents))
        if self.contents.exists():
            size, complete = cache_size(self.contents)
            prefix = '' if complete else text('至少 ', 'at least ')
            print(text('当前缓存大小：', 'Current cache size: ') + prefix + format_size(size))
        else:
            print(text('缓存目录不存在，无需清理。', 'Cache directory does not exist; nothing to clean.'))
        print(text('大小为当前缓存总量，并非预计回收量；下次构建或安装可能重新生成缓存。',
                   'Size is the current cache total, not estimated savings; future builds or installs may recreate it.'))
        if self.argv:
            print(text('确认后执行：', 'After confirmation:'))
            command(self.argv)
        else:
            print(text('执行计划：清空 DerivedData 内容，保留目录。', 'Plan: empty DerivedData, preserving the directory.'))

    def apply(self, timeout):
        # Re-query after confirmation; a changed configuration cannot silently
        # redirect an approved plan to another cache directory.
        current = development_plan(self.name, timeout)
        if current is None:
            return 0
        if current.path != self.path or current.argv != self.argv:
            raise ValueError(text('缓存配置已变化，请重新预览。', 'Cache configuration changed; preview again.'))
        if not self.contents.exists():
            return 0
        if self.name == 'xcode-derived':
            clear_xcode()
            return 0
        command(self.argv)
        return run(self.argv, env=self.env, cwd=self.cwd, timeout=600).returncode


def development_plan(name, timeout):
    if name == 'xcode-derived':
        path = xcode_path()
        if not path.exists():
            print('SKIP ' + name + text('：未找到构建缓存。', ': no build cache found.'))
            return None
        return CachePlan(name, path, [], {}, str(Path.home()))
    tool = {'pip-cache': 'pip3', 'npm-cache': 'npm', 'pnpm-store': 'pnpm',
            'uv-cache': 'uv', 'go-cache': 'go', 'ccache': 'ccache'}[name]
    executable = shutil.which(tool)
    prefix = [executable] if executable else []
    if name == 'pip-cache' and not prefix:
        prefix = [sys.executable, '-m', 'pip']
    if not prefix:
        print('SKIP ' + name + text('：工具未安装。', ': tool not installed.'))
        return None
    env = dict(os.environ, GOTOOLCHAIN='local', COREPACK_ENABLE_NETWORK='0',
               COREPACK_ENABLE_AUTO_PIN='0', PIP_DISABLE_PIP_VERSION_CHECK='1',
               npm_config_manage_package_manager_versions='false',
               UV_OFFLINE='1', UV_PYTHON_DOWNLOADS='never')
    cwd = str(Path.home())
    query = {'pip-cache': ['cache', 'dir'], 'npm-cache': ['config', 'get', 'cache'],
             'pnpm-store': ['store', 'path'], 'uv-cache': ['cache', 'dir'],
             'go-cache': ['env', 'GOCACHE'], 'ccache': ['--get-config', 'cache_dir']}[name]
    result = run(prefix + query, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        if name == 'pip-cache' and not executable and 'No module named pip' in result.stderr:
            print('SKIP pip-cache' + text('：工具未安装。', ': tool not installed.'))
            return None
        raise ValueError(name + ': ' + (result.stderr.strip() or 'cache lookup failed'))
    path = cache_path(result.stdout)
    argv = {
        'pip-cache': ['--cache-dir', str(path), 'cache', 'purge'],
        'npm-cache': ['cache', 'clean', '--force', '--cache', str(path)],
        'pnpm-store': ['store', 'prune'],
        'uv-cache': ['cache', 'prune', '--cache-dir', str(path)],
        'go-cache': ['clean', '-cache'],
        'ccache': ['--dir', str(path), '--clear'],
    }[name]
    if name == 'go-cache':
        env['GOCACHE'] = str(path)
    return CachePlan(name, path, prefix + argv, env, cwd)


def clear_xcode():
    """Walk from home using directory descriptors; never traverse symlinks.

    Keep each directory open while clearing it so replacements cannot redirect
    deletion outside DerivedData, including on the supported Python 3.9.
    """
    path = xcode_path()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(Path.home(), flags)
    try:
        for component in path.relative_to(Path.home()).parts:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child

        def empty(directory):
            with os.scandir(directory) as entries:
                for entry in entries:
                    info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(entry.name, flags, dir_fd=directory)
                        try:
                            empty(child)
                        finally:
                            os.close(child)
                        os.rmdir(entry.name, dir_fd=directory)
                    else:
                        os.unlink(entry.name, dir_fd=directory)
        empty(fd)
    finally:
        os.close(fd)
