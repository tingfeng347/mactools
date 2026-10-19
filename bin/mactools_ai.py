"""Manage AI clients through their official distribution channels."""

import argparse
import json
import os
import re
import shutil
import shlex
import unicodedata
import subprocess
import sys
import tempfile
import tarfile
import urllib.parse
from dataclasses import dataclass
from contextlib import ExitStack
from pathlib import Path

from mactools_packages import cell, clean, selector_widths

from mactools_runtime import brew_env, command, confirm, fetch, run, text, within

TOOLS = {
    "codex": {
        "package": "@openai/codex",
        "url": "https://chatgpt.com/codex/install.sh",
        "shell": "sh",
    },
    "claude": {
        "package": "@anthropic-ai/claude-code",
        "url": "https://claude.ai/install.sh",
        "shell": "bash",
    },
    "opencode": {
        "package": "opencode-ai",
        "url": "https://opencode.ai/install",
        "shell": "bash",
    },
    "qodercn": {
        "package": "@qodercn-ai/qoderclicn",
        "url": "https://static.qoder.com.cn/qoder-cli-cn/install.sh",
        "shell": "bash",
    },
    "gemini": {
        "package": "@google/gemini-cli",
        "url": "https://github.com/google-gemini/gemini-cli",
        "install": "npm",
    },
    "qwen": {
        "package": "@qwen-code/qwen-code",
        "url": "https://github.com/QwenLM/qwen-code",
        "install": "npm",
    },
    "zcode": {
        "package": "",
        "url": "https://github.com/zai-org/ZCode.git",
        "install": "source",
    },
    "pi": {
        "package": "@earendil-works/pi-coding-agent",
        "url": "https://pi.dev/install.sh",
        "shell": "sh",
    },
}

BREW_PREFIXES = (Path('/opt/homebrew'), Path('/usr/local'))


@dataclass
class Installation:
    path: str = ""
    method: str = "missing"
    package: str = ""
    target: str = ""
    prefix: str = ""
    manager: str = ""
    aliases: tuple = ()


def zcode_root():
    return Path.home() / '.local/share/mactools/zcode'


def candidate_paths(name):
    home = Path.home()
    executable_names = ('qoderclicn', 'qodercn') if name == 'qodercn' else (name,)
    primary = next((shutil.which(value) for value in executable_names if shutil.which(value)), None)
    if primary and not (name == 'qodercn' and Path(primary).resolve() == home / '.qoder-cn/entry/qodercn'):
        yield primary
    directories = [Path(value or '.') for value in os.environ.get('PATH', '').split(os.pathsep)]
    directories += [home / '.local/bin', home / '.opencode/bin',
                    home / '.npm-global/bin', home / '.npm/bin', home / '.bun/bin',
                    home / '.local/share/pnpm', home / '.volta/bin']
    # A Rosetta installation may be hidden behind the active ARM Homebrew.
    prefixes = list(BREW_PREFIXES)
    if os.environ.get('HOMEBREW_PREFIX'):
        prefixes.append(Path(os.environ['HOMEBREW_PREFIX']).expanduser())
    directories += [prefix / 'bin' for prefix in prefixes]
    directories.append(Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share'))) / 'pnpm')
    for variable in ['CODEX_INSTALL_DIR', 'PNPM_HOME', 'BUN_INSTALL_BIN']:
        if os.environ.get(variable):
            directories.append(Path(os.environ[variable]).expanduser())
    for variable in ['NPM_CONFIG_PREFIX', 'npm_config_prefix', 'BUN_INSTALL']:
        if os.environ.get(variable):
            directories.append(Path(os.environ[variable]).expanduser() / 'bin')
    nvm = Path(os.environ.get('NVM_DIR', str(home / '.nvm'))).expanduser()
    directories += sorted(nvm.glob('versions/node/*/bin'))
    if name == 'pi':
        directories.append(Path(os.environ.get('PI_CODING_AGENT_DIR', str(home / '.pi/agent'))) / 'bin')
    if name == 'qodercn':
        directories.append(home / '.qoder-cn/bin/qoderclicn')
    for directory in directories:
        for executable in executable_names:
            launcher = directory / executable
            # This dispatcher also opens the IDE; manage its underlying CLI only.
            if name == 'qodercn' and launcher.resolve() == home / '.qoder-cn/entry/qodercn':
                continue
            if launcher.is_file() and os.access(launcher, os.X_OK):
                yield str(launcher.absolute())


def detect_all(name):
    """Discover distinct executable targets; PATH order determines the active one."""
    installations = {}
    for candidate in candidate_paths(name):
        path = str(Path(candidate).absolute())
        target = str(Path(path).resolve())
        if target in installations:
            found = installations[target]
            if path not in found.aliases:
                found.aliases += (path,)
            continue
        found = classify(name, path)
        found.target = target
        found.aliases = (path,)
        installations[target] = found
    return list(installations.values())


def detect(name):
    return next(iter(detect_all(name)), Installation())


def classify(name, found):
    home = Path.home()
    native = home / ('.opencode/bin' if name == 'opencode' else '.local/bin') / name
    native_paths = [native.resolve()]
    if name == 'codex' and os.environ.get('CODEX_INSTALL_DIR'):
        native_paths.append((Path(os.environ['CODEX_INSTALL_DIR']).expanduser() / name).resolve())
    pi_root = Path(os.environ.get('PI_CODING_AGENT_DIR', str(home / '.pi/agent'))).expanduser().resolve()
    path = Path(found).resolve()
    value = str(path)
    if name == 'zcode' and path == zcode_root() / 'launcher':
        return Installation(found, 'source')
    if name == 'qodercn' and within(path, home / '.qoder-cn/bin/qoderclicn'):
        return Installation(found, 'native')
    # Homebrew formulas can contain node_modules. Attribute the enclosing keg
    # first, and retain its prefix rather than using whichever brew is on PATH.
    for marker, method in [('Cellar', 'brew'), ('Caskroom', 'brew-cask')]:
        if marker in path.parts:
            index = path.parts.index(marker)
            if len(path.parts) < index + 4:
                continue
            package = path.parts[index + 1]
            if not re.fullmatch(r'[a-z0-9][a-z0-9@+_.-]*', package):
                continue
            prefix = Path(*path.parts[:index])
            return Installation(found, method, package, prefix=str(prefix),
                                manager=str(prefix / 'bin/brew'))
    # Pi's managed installation contains node_modules but updates through pi itself.
    if name == "pi" and (
        within(path, pi_root / "bin") or within(path, pi_root / "install")
    ):
        return Installation(found, "native")
    if "/node_modules/" in value and TOOLS[name]["package"]:
        bun_global = os.environ.get('BUN_INSTALL_GLOBAL_DIR')
        is_bun = '/install/global/node_modules/' in value or (bun_global and within(path, Path(bun_global).expanduser().resolve()))
        method = "pnpm" if "/.pnpm/" in value else "bun" if is_bun else "npm"
        package = TOOLS[name]["package"]
        if name == "pi" and "/node_modules/@mariozechner/pi-coding-agent/" in value:
            package = "@mariozechner/pi-coding-agent"
        prefix = ''
        if method == 'npm' and '/lib/node_modules/' in value:
            prefix = value.split('/lib/node_modules/', 1)[0] or '/'
        elif method == 'pnpm' and '/global/' in value:
            prefix = value.split('/node_modules/', 1)[0]
            # pnpm adds a numeric layout version under configured globalDir.
            if Path(prefix).name.isdigit():
                prefix = str(Path(prefix).parent)
        elif method == 'bun':
            prefix = value.split('/node_modules/', 1)[0]
        managers = [Path(found).parent / method]
        if prefix and method == 'npm':
            managers.insert(0, Path(prefix) / 'bin/npm')
        elif prefix and method == 'bun':
            managers.insert(0, Path(prefix).parent.parent / 'bin/bun')
        manager_path = next((str(item) for item in managers if item.is_file() and os.access(item, os.X_OK)),
                            shutil.which(method) or '')
        return Installation(found, method, package, prefix=prefix, manager=manager_path)
    native_roots = [
        home / ".local/share/codex",
        home / ".local/share/claude",
        home / ".opencode/bin",
        Path(os.environ.get("CODEX_HOME", str(home / ".codex")))
        / "packages/standalone",
    ]
    if name in {'codex', 'claude', 'opencode', 'pi'} and (path in native_paths or any(within(path, root.expanduser().resolve()) for root in native_roots)):
        return Installation(found, "native")
    return Installation(found, "unmanaged")


def version(installation):
    if not installation.path:
        return "-", False
    try:
        result = run(
            [installation.path, "--version"], timeout=5, capture_output=True, text=True
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return "version check failed/timed out", True
    output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout).strip().splitlines()
    value = "".join(c for c in (output[0] if output else "-") if c.isprintable())[:100]
    return value, result.returncode != 0 or not output


def latest(name, timeout):
    package = urllib.parse.quote(TOOLS[name]["package"], safe="@")
    if name == 'zcode':
        data = json.loads(fetch('https://raw.githubusercontent.com/zai-org/ZCode/main/package.json', timeout=timeout))
    else:
        data = json.loads(
            fetch(f"https://registry.npmjs.org/{package}/latest", timeout=timeout)
        )
    value = data.get("version", "") if isinstance(data, dict) else None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", value):
        raise ValueError("invalid published version metadata")
    return value


def manager_command(existing, arguments):
    """Operate on the selected global installation, including a hidden npm prefix."""
    method = existing.method
    if existing.target and not existing.prefix:
        raise ValueError(text(
            f"无法确认全局安装目录，请使用原安装工具处理：{existing.path}",
            f"Cannot identify the global installation directory; use the original manager: {existing.path}",
        ))
    executable = existing.manager or method
    if existing.prefix and method == "npm":
        return [executable, *arguments, "--prefix", existing.prefix]
    if existing.prefix and method == "pnpm":
        return [executable, *arguments, "--global-dir", existing.prefix,
                "--global-bin-dir", str(Path(existing.path).parent)]
    if existing.prefix and method == "bun":
        return ["env", f"BUN_INSTALL_GLOBAL_DIR={existing.prefix}",
                f"BUN_INSTALL_BIN={Path(existing.path).parent}", executable, *arguments]
    return [executable, *arguments]


def homebrew_manager(existing):
    """Never silently redirect an Intel keg operation to an ARM brew (or vice versa)."""
    if not existing.manager or not Path(existing.manager).is_file() or not os.access(existing.manager, os.X_OK):
        raise ValueError(text(
            f'未找到此安装所属的 Homebrew，请使用原安装工具处理：{existing.path}',
            f'Homebrew for this installation was not found; use its original manager: {existing.path}',
        ))
    return existing.manager


def plan(name, existing, action, yes=False):
    method = existing.method
    package = existing.package
    if action == "uninstall":
        if name == 'zcode' and method == 'source':
            if Path(existing.path).resolve() != zcode_root() / 'launcher':
                raise ValueError('Unrecognized ZCode source installation')
            return [['@remove', existing.path], ['@remove-tree', str(zcode_root())]]
        if name == 'qodercn' and method == 'native':
            launcher = Path(existing.path)
            runtime = Path.home() / '.qoder-cn/bin/qoderclicn'
            if not within(launcher.resolve(), runtime) or launcher.name not in {'qoderclicn', 'qodercn'}:
                raise ValueError('Unrecognized Qoder CN native installation')
            steps = [['@remove', str(launcher)]]
            steps.append(['@remove-tree', str(runtime)])
            return steps
        if method in {"npm", "pnpm", "bun"}:
            verb = "uninstall" if method == "npm" else "remove"
            return [manager_command(existing, [verb, "--global", package])]
        if method in {"brew", "brew-cask"}:
            return [
                [
                    homebrew_manager(existing),
                    "uninstall",
                    "--cask" if method == "brew-cask" else "--formula",
                    package,
                ]
            ]
        if method in {"native", "unmanaged"} and name == "opencode":
            home = Path.home()
            launcher = Path(existing.path).expanduser()
            official_launchers = {
                home / ".local/bin/opencode",
                home / ".opencode/bin/opencode",
            }
            if launcher not in official_launchers:
                raise ValueError(
                    f"opencode: {text('无法确认这是官方安装路径，拒绝自动删除', 'unrecognized OpenCode path; refusing automatic removal')}: {launcher}"
                )
            return [
                [existing.path, "uninstall", "--force", "--keep-config", "--keep-data"],
                ["@remove", str(launcher)],
            ]
        if method == "unmanaged":
            raise ValueError(
                f"{name}: {text('无法确定安装来源，拒绝自动删除', 'cannot determine installation source; refusing automatic removal')}: {existing.path}"
            )
        if method == "native" and name == "pi":
            if not os.path.exists("/dev/tty"):
                raise ValueError("Pi 官方卸载器需要交互式终端（TTY）")
            return [["@pi-uninstaller", TOOLS[name]["url"], TOOLS[name]["shell"]]]
        if method == "native" and name == "opencode":
            return [
                [existing.path, "uninstall", "--force", "--keep-config", "--keep-data"]
            ]
        if method == "native" and name == "claude":
            home = Path.home()
            launcher = Path(existing.path).expanduser()
            share = home / ".local/share/claude"
            if launcher.name != "claude" or not (
                within(launcher, home / ".local/bin") or within(launcher, share)
            ):
                raise ValueError(
                    f"claude: refusing to remove unexpected native path: {launcher}"
                )
            steps = [["@remove", str(launcher)]]
            if within(launcher.resolve(), share.resolve()):
                steps.append(["@remove-tree", str(share)])
            return steps
        if method == "native" and name == "codex":
            home = Path.home()
            code_home = Path(
                os.environ.get("CODEX_HOME", str(home / ".codex"))
            ).expanduser()
            launcher = Path(existing.path).expanduser()
            install_dir = Path(
                os.environ.get("CODEX_INSTALL_DIR", str(home / ".local/bin"))
            ).expanduser()
            runtime = code_home / "packages/standalone"
            legacy_runtime = home / ".local/share/codex"
            if within(Path(existing.path).resolve(), legacy_runtime.resolve()):
                runtime = legacy_runtime
            if launcher.name != "codex" or not (
                within(launcher, install_dir)
                or within(launcher, home / '.local/bin')
                or within(launcher, code_home / "packages/standalone")
                or within(launcher, legacy_runtime)
            ):
                raise ValueError(
                    f"codex: refusing to remove unexpected native path: {launcher}"
                )
            steps = [["@remove", str(launcher)]]
            if within(launcher.resolve(), runtime.resolve()):
                steps.append(["@remove-tree", str(runtime)])
            return steps
        raise ValueError(
            f"{name}: {text('暂不支持此安装方式的卸载', 'uninstall is not supported for this installation method')}: {existing.path}"
        )
    if (
        name == "pi"
        and method in {"npm", "pnpm", "bun"}
        and package == "@mariozechner/pi-coding-agent"
    ):
        # Official migration may need one update to 0.73.1 before changing scope.
        return [[existing.path, "update"], [existing.path, "update"]]
    if method in {"npm", "pnpm", "bun"}:
        verbs = {
            "npm": ["install", "--global"],
            "pnpm": ["add", "--global"],
            "bun": ["install", "--global"],
        }
        flags = ["--ignore-scripts"] if name == "pi" and method == "npm" else []
        return [manager_command(existing, [*verbs[method], *flags, package + "@latest"])]
    if method in {"brew", "brew-cask"}:
        return [
            [homebrew_manager(existing), "upgrade", "--cask" if method == "brew-cask" else "--formula", package]
        ]
    if method == "unmanaged":
        raise ValueError(
            f"{name}: {text('无法确定安装来源，请使用原安装工具更新', 'unrecognized installation; update with its original manager')}: {existing.path}"
        )
    if name == 'zcode' and method in {'missing', 'source'}:
        return [['@zcode-source', TOOLS[name]['url']]]
    if method == 'missing' and TOOLS[name].get('install') == 'npm':
        return [['npm', 'install', '--global', TOOLS[name]['package'] + '@latest']]
    if name == 'qodercn' and method == 'native' and action == 'update':
        return [[existing.path, 'update']]
    if method == "native" and action == "update" and name == "pi":
        return [[existing.path, "update"]]
    if method == "native" and action == "update" and name != "codex":
        return (
            [[existing.path, "update"]]
            if name == "claude"
            else [[existing.path, "upgrade", "--method", "curl"]]
        )
    if name == 'zcode':
        raise ValueError(text('ZCode 安装来源未知，请使用原安装方式管理。',
                              'Unknown ZCode installation; use its original installer.'))
    # The @installer marker is rendered as a download + a separate execution,
    # with no shell interpolation of remote contents or user-supplied arguments.
    return [
        [
            "@installer",
            TOOLS[name]["url"],
            TOOLS[name]["shell"],
            *(["latest"] if name == "claude" else []),
        ]
    ]


def display_plan(steps):
    for step in steps:
        if step[0] == '@zcode-source':
            print(text('从智谱官方源码构建 ZCode CLI（需要 Git、Node.js 24.14+、pnpm 10.33.2；构建可能耗时较长）。',
                       'Build official ZCode CLI from source (Git, Node.js 24.14+, pnpm 10.33.2 required; this may take some time).'))
            command(['git', 'clone', '--depth=1', step[1], '<temporary-source>'])
            command(['pnpm', 'install', '--frozen-lockfile'])
            command(['pnpm', 'build:zcode', '--base-url', 'https://github.com/zai-org/ZCode/'])
        elif step[0] in {"@installer", "@pi-uninstaller"}:
            command(["curl", "-fL", step[1], "-o", "<installer-file>"])
            command([step[2], "<installer-file>", *step[3:]])
            if step[0] == "@pi-uninstaller":
                print(
                    text(
                        "安装器中按 U 选择卸载；Pi 配置和会话会保留。",
                        "Press U in the installer to uninstall; Pi settings and sessions are kept.",
                    ),
                    flush=True,
                )
        elif step[0] in {"@remove", "@remove-tree"}:
            command(["rm", "-f" if step[0] == "@remove" else "-rf", "--", step[1]])
        else:
            command(step)


def build_zcode():
    """Build in isolation, then publish only the runtime into our owned directory."""
    for executable in ('git', 'node', 'pnpm', 'tar'):
        if not shutil.which(executable):
            raise ValueError(f'ZCode source build requires {executable}')
    node = run(['node', '--version'], capture_output=True, text=True)
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', node.stdout)
    if node.returncode or not match or tuple(map(int, match.groups())) < (24, 14, 0):
        raise ValueError('ZCode source build requires Node.js 24.14.0 or newer')
    launcher = Path.home() / '.local/bin/zcode'
    root = zcode_root()
    if (launcher.exists() or launcher.is_symlink()) and launcher.resolve() != root / 'launcher':
        raise ValueError(f'Refusing to overwrite an existing ZCode launcher: {launcher}')
    with tempfile.TemporaryDirectory(prefix='ma-zcode-build-') as directory:
        source = Path(directory) / 'source'
        commands = [
            (['git', 'clone', '--depth=1', TOOLS['zcode']['url'], str(source)], None),
            (['pnpm', 'install', '--frozen-lockfile'], source),
            (['pnpm', 'build:zcode', '--base-url', 'https://github.com/zai-org/ZCode/'], source),
        ]
        for arguments, cwd in commands:
            if arguments[:2] == ['pnpm', 'install']:
                expected = json.loads((source / 'package.json').read_text()).get('packageManager', '')
                actual = run(['pnpm', '--version'], capture_output=True, text=True)
                if actual.returncode or expected != 'pnpm@' + actual.stdout.strip():
                    raise ValueError(f'ZCode requires {expected}; activate it before building')
            result = run(arguments, cwd=cwd, timeout=3600)
            if result.returncode:
                raise RuntimeError('ZCode source build failed; existing runtime kept')
        metadata = json.loads((source / 'dist/zcode/latest.json').read_text())
        version_name, tarball = metadata['version'], metadata['tarball']
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?', version_name) or Path(tarball).name != tarball:
            raise ValueError('Invalid ZCode build metadata')
        archive = source / 'dist/zcode/releases' / version_name / tarball
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='runtime-', dir=root) as stage:
            # Validate archive paths and links before extracting, also on Python 3.9.
            with tarfile.open(archive) as bundle:
                for member in bundle.getmembers():
                    target = Path(stage) / member.name
                    if not within(target.resolve(), Path(stage).resolve()) or member.isdev() or member.isfifo():
                        raise ValueError('Unsafe ZCode runtime archive')
                    if member.issym() or member.islnk():
                        link = target.parent / member.linkname if member.issym() else Path(stage) / member.linkname
                        if not within(link.resolve(), Path(stage).resolve()):
                            raise ValueError('Unsafe ZCode runtime link')
            result = run(['tar', '-xzf', str(archive), '-C', stage], timeout=120)
            if result.returncode or not (Path(stage) / 'zcode/bin/zcode.mjs').is_file():
                raise RuntimeError('Incomplete ZCode runtime')
            # Keep the previous runtime until the next one has been fully staged.
            release = root / (Path(stage).name + '-ready')
            shutil.move(str(Path(stage) / 'zcode'), str(release))
        script = root / 'launcher'
        script.write_text('#!/bin/sh\nexec node ' + shlex.quote(str(root / 'current/bin/zcode.mjs')) + ' "$@"\n')
        script.chmod(0o755)
        pending = root / 'current.new'
        if pending.is_symlink():
            pending.unlink()
        pending.symlink_to(release.name)
        pending.replace(root / 'current')
        launcher.parent.mkdir(parents=True, exist_ok=True)
        if not launcher.is_symlink():
            launcher.symlink_to(script)
        for old in root.glob('runtime-*-ready'):
            if old != release and old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)


def execute(steps, timeout):
    for step in steps:
        if step[0] == '@zcode-source':
            build_zcode()
            continue
        if step[0] in {"@installer", "@pi-uninstaller"}:
            with ExitStack() as stack:
                terminal_io = {}
                if step[0] == "@pi-uninstaller":
                    try:
                        # Buffered update streams require seeking; terminals
                        # must use raw I/O. Keep this fd open for the installer.
                        tty = stack.enter_context(open("/dev/tty", "r+b", buffering=0))
                        if not os.isatty(tty.fileno()):
                            raise OSError("not a terminal")
                    except OSError as error:
                        raise RuntimeError(text(
                            "Pi 官方卸载器需要交互式终端（TTY）",
                            "Pi's official uninstaller needs an interactive TTY",
                        )) from error
                    terminal_io = {"stdin": tty, "stdout": tty, "stderr": tty}
                data = fetch(step[1], timeout=timeout, limit=2 * 1024 * 1024)
                if not data.startswith(b"#!") or b"\x00" in data:
                    raise ValueError("official installer returned unexpected content")
                directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="ma-ai-"))
                script = Path(directory) / "install.sh"
                script.write_bytes(data)
                env = dict(os.environ)
                if "chatgpt.com/codex/" in step[1]:
                    # Ignore an inherited prerelease selector for a latest install.
                    env["CODEX_RELEASE"] = "latest"
                result = run(
                    [step[2], str(script), *step[3:]], timeout=600, env=env, **terminal_io
                )
        elif step[0] in {"@remove", "@remove-tree"}:
            target = Path(step[1]).expanduser()
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                if step[0] == "@remove-tree":
                    shutil.rmtree(target)
                else:
                    raise RuntimeError(
                        f"refusing to remove a directory as a launcher: {target}"
                    )
            result = subprocess.CompletedProcess(step, 0)
        else:
            result = run(step, timeout=600, **({"env": brew_env()} if len(step) > 1 and Path(step[0]).name == "brew" else {}))
        if result.returncode:
            raise RuntimeError(f"installer/update failed (exit {result.returncode})")


def selector_cell(value, width, **alignment):
    value = clean(value)
    result, used = '', 0
    for char in value:
        size = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if used + size > width:
            result = result[:-1] + '…'
            break
        result += char
        used += size
    return cell(result, width, **alignment)


def preview_tool(name):
    if name not in TOOLS:
        raise ValueError('Unknown AI client')
    print(name + '\n')
    print(text('官方安装源', 'Official installation source'))
    print(TOOLS[name]['url'])
    if name == 'zcode':
        print('\n' + text('源码构建：Git、Node.js 24.14+、pnpm（版本以仓库为准）。',
                           'Source build: Git, Node.js 24.14+, repository-pinned pnpm.'))
    found = detect_all(name)
    if not found:
        print('\n' + text('未安装', 'Not installed'))
    for item in found:
        print('\n' + text('安装来源：', 'Source: ') + item.method)
        print(text('路径：', 'Path: ') + clean(item.path))
    print('\n' + text('卸载保留配置、凭据与会话。',
                       'Removal preserves settings, credentials and sessions.'))


def choose(action):
    fzf = shutil.which("fzf")
    if not fzf:
        raise ValueError(text('未找到 fzf，请指定工具名或 --all',
                              'fzf unavailable; specify tool names or --all'))
    installations = {name: detect_all(name) for name in TOOLS}
    candidates = [name for name, found in installations.items() if action == 'install' or found]
    if not candidates:
        print(text('尚未安装 AI 工具；请运行 ma ai install。',
                   'No clients installed; run ma ai install.'))
        return []
    # Use the same preview allocation as maci, reserving status/source columns.
    package_name_width, _ = selector_widths('install')
    name_width = min(24, max(12, package_name_width - 8))
    state_width, source_width = 16, 14
    headings = (cell(text('名称', 'Name'), name_width, center=True) + ' ' +
                cell(text('状态', 'Status'), state_width, center=True) + ' ' +
                cell(text('安装来源', 'Source'), source_width, right=True))
    rows, choices = [], {}
    for name in candidates:
        found = installations[name]
        entries = found if action == 'uninstall' else [None]
        for item in entries:
            state = text('已安装', 'Installed') if found else text('未安装', 'Not installed')
            if item is None and len(found) > 1:
                state += f' ({len(found)})'
            methods = item.method if item else '/'.join(dict.fromkeys(i.method for i in found)) or '-'
            visible = (selector_cell(name, name_width) + ' ' +
                       selector_cell(state, state_width, center=True) + ' ' +
                       selector_cell(methods, source_width, right=True))
            # Metadata stays hidden; retain exact row identity for duplicate installs.
            row = '\t'.join([name, str(len(rows)), clean(item.path) if item else '', visible])
            rows.append(row)
            choices[row] = (name, item) if item else name
    header = text(
        'Tab 多选  Enter 确认  Ctrl+/ 详情  Esc 退出\nCtrl+A 全选  Ctrl+D 清除',
        'Tab select  Enter confirm  Ctrl+/ details  Esc exit\nCtrl+A select all  Ctrl+D clear',
    ) + '\n\n' + headings
    preview = shlex.join([sys.executable, str(Path(__file__).resolve()), '--preview']) + ' {1}'
    env = dict(os.environ)
    env.pop('FZF_DEFAULT_OPTS', None)
    env.pop('FZF_DEFAULT_OPTS_FILE', None)
    env['SHELL'] = '/bin/sh'
    selected = run(
        [fzf, '--multi', '--layout=reverse', '--border', '--height=95%',
         '--info=hidden', '--delimiter=\t', '--nth=1', '--with-nth=4..', '--tabstop=1',
         '--prompt=' + text('搜索 › ', 'Search › '),
         '--preview=' + preview, '--preview-window=right:42%:wrap:<50(down:35%:wrap)',
         '--bind=tab:toggle+down,shift-tab:toggle+up,ctrl-a:select-all,ctrl-d:deselect-all,ctrl-/:toggle-preview',
         '--header=' + header],
        input='\n'.join(rows) + '\n', text=True, stdout=subprocess.PIPE,
        timeout=None, env=env,
    )
    if selected.returncode in {1, 130}:
        return []
    if selected.returncode:
        raise RuntimeError(f'fzf failed (exit {selected.returncode})')
    return [choices[line] for line in dict.fromkeys(selected.stdout.splitlines()) if line in choices]


def tool_name(value):
    if value == 'qoderclicn':
        value = 'qodercn'
    if value not in TOOLS:
        raise argparse.ArgumentTypeError(
            f"unknown tool: {value}; choose from {', '.join(TOOLS)}"
        )
    return value


SEMVER = re.compile(
    r"(?<![\w.])v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?![\w.+-])"
)


def compare_versions(current, published):
    """SemVer precedence: numeric components, prereleases, ignored build metadata."""
    left, right = SEMVER.search(current), SEMVER.fullmatch(published)
    if not left or not right:
        return None
    for match in (left, right):
        if any(part.isdigit() and len(part) > 1 and part[0] == '0'
               for part in (match[4] or '').split('.')):
            return None
    a, b = tuple(map(int, left.groups()[:3])), tuple(map(int, right.groups()[:3]))
    if a != b:
        return (a > b) - (a < b)
    a, b = left[4], right[4]
    if a == b:
        return 0
    if not a or not b:
        return 1 if not a else -1
    a, b = a.split('.'), b.split('.')
    for x, y in zip(a, b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return (int(x) > int(y)) - (int(x) < int(y))
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return (x > y) - (x < y)
    return (len(a) > len(b)) - (len(a) < len(b))


def check_updates(names, timeout):
    print(text("工具       当前版本                  上游最新       状态 / 来源 / 路径",
               "Tool       Installed version         Upstream       State / source / path"))
    failed = False
    for name in names:
        installations = detect_all(name)
        if not installations:
            print(f"{name:10} -                         -              " + text("未安装", "Not installed"))
            continue
        try:
            published = latest(name, timeout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(text(f"{name}: 检查失败（{type(error).__name__}）",
                       f"{name}: check failed ({type(error).__name__})"))
            failed = True
            continue
        for found in installations:
            current, bad = version(found)
            comparison = None if bad else compare_versions(current, published)
            if comparison is None:
                state = text("无法比较", "Cannot compare")
                failed = True
            elif comparison < 0:
                state = text("可更新", "Update available")
            elif comparison == 0:
                state = text("已是最新", "Up to date")
            else:
                state = text("本机版本较新", "Local version newer")
            print(f"{name:10} {current:25} {published:14} {state} | {found.method} | {found.path}")
    print(text("ZCode 最新版本来自官方源码，其余来自 npm；Homebrew 等渠道的版本可能不同。",
               "Latest uses official source metadata for ZCode and npm for other clients; Homebrew availability may differ."))
    return int(failed)


def removal_targets(selections, all_sources=False, path=None):
    targets = []
    for selection in selections:
        if isinstance(selection, tuple):
            targets.append(selection)
            continue
        found = detect_all(selection)
        if path:
            requested = str(Path(path).expanduser().absolute())
            found = [item for item in found if requested in (item.aliases or (item.path,))]
            if not found:
                raise ValueError(text(f"{selection}: 未找到指定安装路径 {requested}",
                                      f"{selection}: installation path not found: {requested}"))
        if len(found) > 1 and not all_sources:
            paths = '\n'.join(f'  {item.method}: {item.path}' for item in found)
            raise ValueError(text(
                f"{selection} 有多处安装，请运行 ma ai remove 选择，或指定 --path：\n{paths}",
                f"{selection} has multiple installations; select with ma ai remove or --path:\n{paths}",
            ))
        if not found:
            print(text(f"跳过 {selection}：未安装", f"SKIP {selection}: not installed"))
        targets.extend((selection, item) for item in found)
    return targets


def refreshed_status(name, action, selected):
    """Re-detect after executing, so installer cancellation cannot look successful."""
    remaining = detect_all(name)
    selected_paths = set(selected.aliases or (selected.path,))
    same_source = [item for item in remaining
                   if selected_paths.intersection(item.aliases or (item.path,))
                   or (selected.target and item.target == selected.target)]
    if action == 'uninstall':
        if same_source:
            print(text(f"失败 {name}：所选安装仍存在（{selected.path}），未完成卸载。",
                       f"FAIL {name}: selected installation still exists ({selected.path}); removal incomplete."))
        elif remaining:
            print(text(f"正常 {name}：所选安装已卸载，但还有 {len(remaining)} 处安装。",
                       f"OK {name}: selected installation removed; {len(remaining)} other installation(s) remain."))
        else:
            print(text(f"正常 {name}：已卸载，复查为未安装；设置和数据已保留。",
                       f"OK {name}: verified not installed; settings and data kept."))
        for found in remaining:
            print(text("  仍安装：", "  Remaining: ") + f"{found.method} | {found.path}")
        return not same_source
    expected = same_source if selected.path else remaining
    if not expected:
        print(text(f"失败 {name}：操作后未检测到目标安装。",
                   f"FAIL {name}: target installation not found after operation."))
        return False
    found = expected[0]
    current, bad = version(found)
    state = text("失败", "FAIL") if bad else text("正常", "OK")
    print(f"{state} {name}: {current} ({found.method} | {found.path})", flush=True)
    if len(remaining) > 1:
        print(text(f"  检测到 {len(remaining)} 处安装：", f"  {len(remaining)} installations detected:"))
        for item in remaining:
            print(f"  {item.method} | {item.path}")
    if not bad:
        print(text("登录/配置入口：", "Sign-in/configuration: ") + name)
        if shutil.which(name) != found.path:
            print(text(f"请将 {Path(found.path).parent} 添加到 PATH。",
                       f"PATH: add {Path(found.path).parent} to your shell PATH"))
    return not bad


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ma ai",
        description=text(
            "管理 Codex、Claude Code、OpenCode、Pi、Qoder CN、Gemini、Qwen 和 ZCode CLI。",
            "Manage Codex, Claude Code, OpenCode, Pi, Qoder CN, Gemini, Qwen and ZCode CLI.",
        ),
    )
    sub = parser.add_subparsers(dest="action")
    for action in ["install", "update", "remove", "status", "doctor"]:
        item = sub.add_parser(action, aliases=["uninstall"] if action == "remove" else [])
        # Validate each supplied value; choices + nargs="*" rejects [] on older Python.
        item.add_argument(
            "tools", nargs="*", type=tool_name, metavar="{" + ",".join(TOOLS) + "}"
        )
        item.add_argument("--all", action="store_true")
        item.add_argument("--timeout", type=int, default=15)
        if action in {"install", "update", "remove"}:
            item.add_argument("--dry-run", action="store_true")
            item.add_argument("--yes", "-y", action="store_true")
        if action == "status":
            item.add_argument(
                "--latest",
                action="store_true",
                help="query latest upstream versions",
            )
        if action == "update":
            item.add_argument("--check", action="store_true", help=text(
                "检查当前版本和上游最新版本，不执行更新",
                "Compare installed versions with upstream releases without updating",
            ))
        if action == "remove":
            item.add_argument("--path", help=text(
                "只卸载指定路径的安装，需要指定一个工具名",
                "Remove this exact installation path; specify one tool name",
            ))
        if action == "doctor":
            item.add_argument("--offline", action="store_true")
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments or ["install"])
    if args.action == "remove":
        args.action = "uninstall"
    if not args.action:
        parser.print_help()
        return 0
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.all and args.tools:
        parser.error("use tool names or --all, not both")
    if getattr(args, "path", None) and (args.all or len(args.tools) != 1):
        parser.error("--path requires exactly one tool name")
    names = args.tools or list(TOOLS)
    if args.action == "update" and args.check:
        return check_updates(names, args.timeout)
    if (
        args.action in {"install", "update", "uninstall"}
        and not args.tools
        and not args.all
    ):
        names = choose(args.action)
    failed = False
    if args.action in {"status", "doctor"}:
        for name in names:
            installations = detect_all(name)
            if len(installations) > 1:
                print(text(f'{name}: 检测到 {len(installations)} 处安装',
                           f'{name}: {len(installations)} installations detected'))
            published = ''
            if args.action == 'status' and args.latest:
                try:
                    published = f" {'source-latest' if name == 'zcode' else 'npm-latest'}={latest(name, args.timeout)}"
                except (OSError, ValueError) as error:
                    published = f' latest-query-failed={type(error).__name__}'
                    failed = True
            active = (shutil.which('qoderclicn') or shutil.which(name)) if name == 'qodercn' else shutil.which(name)
            active_target = str(Path(active).resolve()) if active else ''
            for found in installations or [Installation()]:
                current, bad_version = version(found)
                failed |= bad_version
                state = text('已安装', 'Installed') if found.path else text('未安装', 'Not installed')
                marker = text(' [当前]', ' [Active]') if active_target and active_target == found.target else ''
                print(f'{name:10} {state:14} {current:28} {found.method:12} {found.path}{marker}{published}', flush=True)
        if args.action == "doctor":
            for dependency in ["curl", "bash", "tar", "unzip"]:
                ok = bool(shutil.which(dependency))
                state = text("正常" if ok else "失败", "OK" if ok else "FAIL")
                print(f"{state} {text('依赖', 'dependency')}: {dependency}", flush=True)
                failed |= not ok
            if not args.offline:
                for name in names:
                    try:
                        fetch(
                            TOOLS[name]["url"],
                            timeout=args.timeout,
                            limit=2 * 1024 * 1024,
                        )
                        print(text(f"正常 {name} 官方下载地址", f"OK {name} official download endpoint"), flush=True)
                    except (OSError, ValueError) as error:
                        print(
                            text(
                                f"失败 {name} 下载地址：{type(error).__name__}",
                                f"FAIL {name} download endpoint: {type(error).__name__}",
                            ),
                            flush=True,
                        )
                        failed = True
        return int(failed)
    operations = []
    if args.action == 'uninstall':
        targets = removal_targets(names, all_sources=args.all, path=args.path)
    else:
        targets = [(name, detect(name)) for name in names]
    for name, found in targets:
        if args.action == 'update' and not found.path:
            print(text(f'跳过 {name}：未安装', f'SKIP {name}: not installed'))
            continue
        steps = plan(name, found, args.action, args.yes if args.action == 'uninstall' else False)
        action_name = {'install': text('安装', 'install'), 'update': text('更新', 'update'),
                       'uninstall': text('卸载', 'uninstall')}[args.action]
        print(f'{name}: {found.method} → {action_name}' + (f' ({found.path})' if found.path else ''), flush=True)
        display_plan(steps)
        operations.append((name, found, steps))
    if not operations or args.dry_run:
        return 0
    if not confirm(
        text(
            "执行以上卸载操作？配置、凭据和会话将保留。",
            "Uninstall the selected tools? Settings, credentials and sessions will be kept.",
        )
        if args.action == "uninstall"
        else text("执行以上安装/更新操作？", "Apply these installations/updates?"),
        args.yes,
    ):
        return 0
    for name, found, steps in operations:
        try:
            execute(steps, args.timeout)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            print(text(f'失败 {name}：{error}', f'FAIL {name}: {error}'), flush=True)
            refreshed_status(name, args.action, found)
            return 1
        if not refreshed_status(name, args.action, found):
            failed = True
    return int(failed)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == '--preview':
            preview_tool(sys.argv[2])
        else:
            sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(text(f"错误：{error}", f"Error: {error}"), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
