"""Homebrew formula/cask selectors. Queries are bounded; actions keep native prompts."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tempfile
import unicodedata

from mactools_runtime import atomic_write, brew_env, brew_path, cache_dir, command, confirm, save_json, text

CACHE_SECONDS = 3600
SELECTOR_CACHE_SECONDS = 24 * 3600
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@+_.-]*(?:/[A-Za-z0-9][A-Za-z0-9@+_.-]*){0,2}$")


class BrewError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code if code > 0 else 1


def clean(value):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))


def without_color(value):
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def cell(value, width, right=False, center=False):
    used = sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in value)
    padding = " " * max(0, width - used)
    if center:
        left = len(padding) // 2
        return padding[:left] + value + padding[left:]
    return padding + value if right else value + padding


def clip_cell(value, width):
    if sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in value) <= width:
        return value
    result, used = '', 0
    for char in value:
        size = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if used + size > width - 1:
            break
        result += char
        used += size
    return result + '…'


def selector_widths(action, terminal_width=None):
    columns = terminal_width or shutil.get_terminal_size((120, 24)).columns
    inner = max(40, columns - 4)
    preview = int(inner * 0.42)
    # Match fzf's right preview and its narrow-window fallback below.
    available = inner - preview - 4 if preview >= 50 else inner - 4
    version_columns = 2 if action == "update" else 1
    version_width = 18
    name_width = max(20, available - 8 - 2 - version_columns * version_width - (version_columns + 1))
    return name_width, version_width


def package_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(text("Homebrew 返回了无效的软件名", "Homebrew returned an invalid package name"))
    return value


@dataclass(frozen=True)
class Row:
    source: str
    name: str
    current: str = "-"
    target: str = "-"
    display_name: str = ""

    def line(self):
        return "\t".join((self.source, self.name, clean(self.current), clean(self.target)))

    def selector_line(self, action, widths=None):
        # Keep canonical fields hidden for preview commands and selection validation.
        source = self.source
        current = clean(self.current) if self.current != "-" else text("未安装", "Not installed")
        name_width, version_width = widths or selector_widths(action)
        official = '/' not in self.name or self.name.startswith('homebrew/')
        kind = cell(source, 8)
        name = cell(clean(self.display_name or self.name), name_width)
        colored = not os.environ.get('NO_COLOR') and os.environ.get('TERM') != 'dumb'
        installed = self.current != '-'
        check = '✓' if installed else ' '
        if colored:
            color = '38;5;220' if source == 'app' else ('38;5;75' if official else '38;5;141')
            kind = f'\033[{color}m{kind}\033[0m'
            name = f'\033[37m{name}\033[0m'
            if installed:
                check = f'\033[38;5;78m{check}\033[0m'
        columns = [kind, name, cell(clip_cell(current, version_width), version_width, right=True) + ' ' + check]
        if action == "update":
            columns.append(cell(clean(self.target), version_width, right=True))
        return self.line() + "\t" + "\t".join(columns)

    @classmethod
    def parse(cls, line):
        parts = line.split("\t")
        if len(parts) < 4 or parts[0] not in ("formula", "cask", "app"):
            raise ValueError("Invalid package selection")
        if parts[0] == 'app':
            if not re.fullmatch(r'app-[a-f0-9]{64}', parts[1]):
                raise ValueError('Invalid application selection')
            return cls(*parts[:4])
        return cls(parts[0], package_name(parts[1]), parts[2], parts[3])


class Queries:
    """Own query processes so leaving fzf also stops outstanding brew requests."""
    def __init__(self, brew, timeout, detached=True):
        self.brew, self.timeout = brew, timeout
        self.detached = detached
        self.env = brew_env()
        self.env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
        self.lock = threading.Lock()
        self.processes = set()
        self.cancelled = False

    def capture(self, *args):
        with self.lock:
            if self.cancelled:
                raise BrewError("Query cancelled")
            process = subprocess.Popen([self.brew, *args], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, env=self.env,
                                       start_new_session=self.detached)
            process._mactools_detached = self.detached
            self.processes.add(process)
        try:
            try:
                output, errors = process.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.stop(process)
                process.communicate()
                raise BrewError(text("Homebrew 查询超时", "Homebrew query timed out"), 124)
            except BaseException:
                # Preview cancellation must reap its detached brew process too.
                self.stop(process)
                process.communicate()
                raise
            if process.returncode:
                raise BrewError(clean(errors.strip()) or "Homebrew query failed", process.returncode)
            return output
        finally:
            with self.lock:
                self.processes.discard(process)

    @staticmethod
    def stop(process):
        if not getattr(process, '_mactools_detached', True):
            # A preview query shares fzf's group so forced preview cancellation
            # kills it too. Never signal the caller's entire process group here.
            try:
                process.terminate()
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            except ProcessLookupError:
                pass
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.3)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            pass
        finally:
            # A wrapper may exit before children that still hold stdout/stderr.
            # Kill the remaining group before communicate() drains those pipes.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self):
        with self.lock:
            self.cancelled = True
            processes = list(self.processes)
        for process in processes:
            self.stop(process)


def versions(value):
    if isinstance(value, list):
        return ", ".join(clean(v.get("version", "-")) if isinstance(v, dict) else clean(v) for v in value) or "-"
    return clean(value) if value else "-"


def cask_installed_version(item):
    recorded = versions(item.get('installed'))
    if recorded == '-':
        return recorded
    # The build counter is not necessarily a user-facing application version.
    return versions(item.get('bundle_short_version') or item.get('installed'))


def numeric_version_compare(left, right):
    """Compare only unambiguous numeric versions; avoid guessing vendor formats."""
    if not all(isinstance(value, str) and re.fullmatch(r'\d+(?:\.\d+)*', value) for value in (left, right)):
        return None
    a, b = [int(part) for part in left.split('.')], [int(part) for part in right.split('.')]
    width = max(len(a), len(b))
    a += [0] * (width - len(a))
    b += [0] * (width - len(b))
    return (a > b) - (a < b)


def cask_already_current(item, target):
    # CFBundleVersion alone is often an internal build counter, not a release.
    actual = item.get('bundle_short_version')
    if versions(item.get('installed')) == '-' or not actual:
        return False
    parts = target.split(',')
    comparison = numeric_version_compare(actual, parts[0])
    if comparison is None or comparison < 0:
        return False
    if comparison > 0:
        return True
    if len(parts) == 1:
        return True
    if len(parts) == 2:
        build = numeric_version_compare(item.get('bundle_version'), parts[1])
        return build is not None and build >= 0
    return False


def inventory_items(data, key):
    if not isinstance(data, dict):
        raise ValueError("Invalid Homebrew JSON inventory")
    items = data.get(key, [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Invalid Homebrew JSON inventory: " + key)
    return items


def cached_catalog(path, brew):
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict) or payload.get("brew") != brew:
            return None
        names, timestamp = payload["names"], payload["time"]
        if not isinstance(names, list) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            return None
        return [package_name(name) for name in names], timestamp
    except (OSError, ValueError, TypeError, KeyError):
        return None


def json_inventory(queries, action, sources):
    arguments = ['outdated', '--json=v2'] if action == 'update' else ['info', '--json=v2', '--installed']
    if len(sources) == 1:
        arguments.append('--' + sources[0])
    raw = queries.capture(*arguments)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid Homebrew JSON inventory")
    cask_details = {}
    if action == "update" and "cask" in sources and data.get("casks"):
        # Outdated JSON emits cask short tokens. Keep the installed tap identity
        # so an identically named cask in another tap cannot be upgraded instead.
        inventory = json.loads(queries.capture("info", "--json=v2", "--installed", "--cask"))
        for item in inventory_items(inventory, "casks"):
            token = item.get("token")
            if token:
                token = package_name(token)
                cask_details[token] = item
                cask_details[package_name(item.get("full_token") or token)] = item
    result = []
    for source, key in (("formula", "formulae"), ("cask", "casks")):
        if source not in sources:
            continue
        for item in inventory_items(data, key):
            if source == "formula":
                name = item.get("full_name") or item.get("name")
            else:
                name = item.get("full_token") or item.get("token") or item.get("name")
                details = cask_details.get(package_name(name), item)
                name = details.get("full_token") or name
            current = versions(item.get("installed_versions") if action == "update" else item.get("installed"))
            target = clean(item.get("current_version") or "-") if action == "update" else "-"
            if source == 'cask':
                if action == 'update':
                    if cask_already_current(details, target):
                        continue
                    if versions(details.get('installed')) != '-':
                        current = cask_installed_version(details)
                else:
                    current = cask_installed_version(item)
            result.append(Row(source, package_name(name), current, target))
    return result


def catalog(queries, source, refresh=False, writable=True):
    """Cache names only: installation state is always queried live, never cached."""
    path = cache_dir() / ("packages-" + source + ".json")
    payload = cached_catalog(path, queries.brew)
    cached = payload[0] if payload is not None else None
    if payload is not None and not refresh and time.time() - payload[1] < CACHE_SECONDS:
        return cached
    try:
        names = sorted(set(package_name(n) for n in queries.capture("formulae" if source == "formula" else "casks").splitlines() if n.strip()))
    except (BrewError, ValueError):
        if cached is not None and not refresh:
            print(text("警告：目录刷新失败，使用已有缓存。", "Warning: catalog refresh failed; using cached names."), file=sys.stderr)
            return cached
        raise
    if writable:
        save_json(path, {"brew": queries.brew, "time": time.time(), "names": names})
    return names


def native_rows(opts):
    if not getattr(opts, 'include_apps', False):
        return []
    from mactools_apps import installed_apps
    return [Row('app', app.id, app.version or text('未知', 'Unknown'), display_name=app.name)
            for app in installed_apps(opts.timeout)]


def rows(queries, opts):
    # Native applications appear before any slower Homebrew inventory query.
    yield from native_rows(opts)
    if not opts.sources:
        return
    installed = json_inventory(queries, opts.action, opts.sources)
    if opts.action != "install":
        yield from sorted(installed, key=lambda row: (row.source, row.name))
        return
    known = {(row.source, row.name): row for row in installed}
    # Installed rows arrive before potentially slow catalog enumeration.
    yield from sorted(installed, key=lambda row: (row.source, row.name))
    with ThreadPoolExecutor(max_workers=len(opts.sources)) as pool:
        futures = {pool.submit(catalog, queries, source, opts.refresh, not (opts.list or opts.dry_run)): source for source in opts.sources}
        for future in as_completed(futures):
            source = futures[future]
            for name in future.result():
                if (source, name) not in known:
                    known[(source, name)] = Row(source, name)
                    yield known[(source, name)]


def matches(name, query, exact=False):
    name = name.casefold()
    for word in query.casefold().split():
        if exact:
            if word not in name:
                return False
        else:
            chars = iter(name)
            if not all(any(char == candidate for candidate in chars) for char in word):
                return False
    return True


def selector_cache_path(action, sources=None):
    suffix = '' if sources is None or sources == ['formula', 'cask'] else '-' + ('-'.join(sources) or 'apps')
    return cache_dir() / ('selector-' + action + suffix + '.json')


def cached_selector(queries, opts):
    paths = dict.fromkeys([selector_cache_path(opts.action, opts.sources), selector_cache_path(opts.action)])
    for path in paths:
        try:
            payload = json.loads(path.read_text())
            stamp, sources = payload['time'], payload['sources']
            if (payload.get('brew') != queries.brew
                    or sources not in ([], ['formula'], ['cask'], ['formula', 'cask'])
                    or not set(opts.sources).issubset(sources)
                    or not isinstance(stamp, (int, float)) or not math.isfinite(stamp)
                    or not 0 <= time.time() - stamp < SELECTOR_CACHE_SECONDS):
                continue
            result = []
            if getattr(opts, 'include_apps', False):
                for entry in payload.get('apps', []):
                    if not isinstance(entry, dict) or not isinstance(entry.get('display_name'), str):
                        raise ValueError('Invalid cached application')
                    row = Row.parse(entry['row'])
                    if row.source != 'app' or row.line() != entry['row']:
                        raise ValueError('Invalid cached application')
                    result.append(Row(row.source, row.name, row.current, row.target,
                                      clean(entry['display_name'])))
            for line in payload['rows']:
                row = Row.parse(line)
                if row.source not in sources or row.line() != line:
                    raise ValueError('Invalid cached row')
                if opts.action != 'install' and row.current == '-':
                    raise ValueError('Invalid cached installation state')
                if row.source in opts.sources:
                    result.append(row)
            return result
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def save_selector(queries, opts, result):
    if opts.list or opts.dry_run or queries.cancelled:
        return
    try:
        save_json(selector_cache_path(opts.action, opts.sources), {
            'brew': queries.brew, 'sources': opts.sources, 'time': time.time(),
            'rows': [row.line() for row in result if row.source != 'app'],
            'apps': [{'row': row.line(), 'display_name': row.display_name}
                     for row in result if row.source == 'app'],
        })
    except OSError:
        # A cache is optional; read-only Homebrew operations still work without it.
        pass


def wait_snapshot(path, timeout):
    """fzf reload helper: wait for our parent's atomic snapshot, never run brew."""
    path = Path(path)
    deadline = time.monotonic() + timeout
    while path.parent.is_dir() and time.monotonic() < deadline:
        try:
            with path.open() as stream:
                shutil.copyfileobj(stream, sys.stdout)
            return 0
        except FileNotFoundError:
            time.sleep(0.02)
    return 1


def fzf_identity_options(fzf, env):
    # New fzf versions can preserve selections when refreshed version text changes.
    try:
        result = subprocess.run([fzf, '--help'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=1, env=env)
        return ['--id-nth=1,2'] if result.returncode == 0 and '--id-nth' in result.stdout else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def select(queries, opts):
    fzf = shutil.which('fzf')
    if not fzf:
        raise ValueError(text('未找到 fzf，请先运行 brew install fzf', 'fzf not found. Run brew install fzf first'))
    query, exact = ' '.join(opts.query), opts.exact
    while True:
        widths = selector_widths(opts.action)
        name_width, version_width = widths
        preview = shlex.join([sys.executable, str(Path(__file__).resolve()), '--preview', queries.brew, str(opts.timeout)]) + ' {1} {2}'
        headings = cell(text('类型', 'Type'), 8) + ' ' + cell(text('名称', 'Name'), name_width, center=True) + ' '
        headings += cell(text('已安装版本', 'Installed version'), version_width, right=True) + '  '
        if opts.action == 'update':
            headings += ' ' + cell(text('可更新至', 'Available version'), version_width, right=True)
        header = text('Tab 多选  Enter 确认  Ctrl+R 刷新  Ctrl+E 匹配  Ctrl+/ 详情  Esc 退出', 'Tab select  Enter confirm  Ctrl+R refresh  Ctrl+E match  Ctrl+/ details  Esc exit') + '\n\n' + headings
        argv = [fzf, '--ansi', '--multi', '--delimiter=\t', '--nth=2', '--with-nth=5..', '--tabstop=1',
                '--print-query', '--expect=ctrl-e,ctrl-r', '--query=' + query,
                '--height=95%', '--layout=reverse', '--border', '--tiebreak=index', '--info=hidden',
                '--preview=' + preview, '--preview-window=right:42%:wrap:<50(down:35%:wrap)',
                '--prompt=' + text('搜索 › ', 'Search › '),
                '--bind=tab:toggle+down,shift-tab:toggle+up,ctrl-a:select-all,alt-j:last,alt-k:first,ctrl-/:toggle-preview',
                '--header=' + header]
        if exact:
            argv.append('--exact')
        if getattr(opts, 'include_apps', False):
            # Preserve the app group before casks/formulae, including searches.
            argv.append('--no-sort')
        env = dict(os.environ)
        env.pop('FZF_DEFAULT_OPTS', None)
        env.pop('FZF_DEFAULT_OPTS_FILE', None)
        env['SHELL'] = '/bin/sh'
        cached = cached_selector(queries, opts)
        temporary = tempfile.TemporaryDirectory(prefix='ma-selector-')
        ready = Path(temporary.name) / 'rows'
        if cached is not None:
            reload = shlex.join([sys.executable, str(Path(__file__).resolve()), '--wait-snapshot', str(ready), str(opts.timeout * 3 + 5)])
            argv += ['--bind=load:unbind(load)+reload-sync:' + reload, *fzf_identity_options(fzf, env)]
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        errors, emitted, allowed = [], {}, set()
        completed = threading.Event()

        def render(row):
            line = row.selector_line(opts.action, widths)
            allowed.add(without_color(line))
            return line

        def produce():
            try:
                fresh_lines = []
                if cached is not None:
                    for index, row in enumerate(cached):
                        process.stdin.write(render(row) + '\n')
                        if index % 128 == 0:
                            process.stdin.flush()
                    process.stdin.close()
                fresh = []
                for index, row in enumerate(rows(queries, opts)):
                    fresh.append(row)
                    line = render(row)
                    if cached is not None:
                        fresh_lines.append(line + '\n')
                    emitted[(row.source, row.name)] = row
                    if cached is None:
                        process.stdin.write(line + '\n')
                        if index % 128 == 0:
                            process.stdin.flush()
                if not queries.cancelled:
                    if cached is not None:
                        atomic_write(ready, ''.join(fresh_lines).encode())
                    save_selector(queries, opts, fresh)
                    completed.set()
            except BrokenPipeError:
                pass
            except Exception as error:
                if not queries.cancelled:
                    errors.append(error)
                    print(str(error), file=sys.stderr)
                    if cached is not None:
                        atomic_write(ready, ''.join(row.selector_line(opts.action, widths) + '\n' for row in cached).encode())
            finally:
                try:
                    if not process.stdin.closed:
                        process.stdin.close()
                except BrokenPipeError:
                    pass

        worker = threading.Thread(target=produce, daemon=True)
        worker.start()
        try:
            output = process.stdout.read()
            process.stdout.close()
            status = process.wait()
            fields = output.splitlines()
            accepted = status == 0 and len(fields) > 2 and fields[1] == ''
            if cached is not None and accepted and not completed.is_set():
                print(text('正在核对所选软件的最新状态…', 'Checking the selected packages against current state…'), file=sys.stderr)
                # Reuse the refresh already in flight instead of cancelling and
                # launching the same expensive inventory queries a second time.
                worker.join(timeout=opts.timeout * 3 + 5)
        finally:
            queries.close()
            worker.join(timeout=opts.timeout + 1)
            if process.poll() is None:
                process.terminate()
                process.wait()
            temporary.cleanup()
        if status in (1, 130):
            if errors and status != 130:
                raise errors[0]
            return []
        if status:
            raise BrewError('fzf failed', status)
        fields = output.splitlines()
        query = fields[0] if fields else ''
        key = fields[1] if len(fields) > 1 else ''
        if key in ('ctrl-e', 'ctrl-r'):
            if key == 'ctrl-e':
                exact = not exact
            else:
                opts.refresh = True
            queries = Queries(queries.brew, opts.timeout)
            continue
        if errors:
            raise errors[0]
        selected = []
        for line in fields[2:]:
            row = Row.parse(line)
            if without_color(line) not in allowed:
                raise ValueError('Unknown package selection')
            actual = emitted.get((row.source, row.name), row)
            if actual not in selected:
                selected.append(actual)
        if cached is not None and selected:
            if not completed.is_set():
                # Enter may beat the refresh. Cache never authorizes a mutation.
                fresh_queries = Queries(queries.brew, opts.timeout)
                try:
                    fresh = list(rows(fresh_queries, opts))
                    emitted = {(row.source, row.name): row for row in fresh}
                    save_selector(fresh_queries, opts, fresh)
                finally:
                    fresh_queries.close()
            verified = []
            for row in selected:
                actual = emitted.get((row.source, row.name))
                if actual is not None:
                    verified.append(actual)
                else:
                    print(text('跳过状态已变化的软件：', 'Skip package whose state changed: ') + row.name)
            return verified
        return selected


def plan(brew, action, selected):
    operation = {"install": "install", "remove": "uninstall", "update": "upgrade"}[action]
    return [[brew, operation, "--" + source, *sorted({row.name for row in selected if row.source == source})]
            for source in ("formula", "cask") if any(row.source == source for row in selected)]


def parser():
    result = argparse.ArgumentParser(prog="ma", description=text("软件多选安装、卸载、更新", "Select packages and applications to install, remove or update"))
    result.add_argument("action", choices=("install", "remove", "update"))
    result.add_argument("--formula", "--brew", action="store_true", help=text("选择 formula，可与 --cask 组合", "Select formulae; combine with --cask"))
    result.add_argument("--cask", action="store_true", help=text("选择 cask", "Select casks"))
    result.add_argument("--apps", action="store_true", help=text("仅 remove：只列本机应用及其残留，可与 --cask 组合", "remove only: native applications; combine with --cask if needed"))
    result.add_argument("--app-data", action="store_true", help=text("仅 remove：同时移除应用个人数据，预览后确认", "remove only: include personal app data after preview and confirmation"))
    result.add_argument("--exact", "-e", action="store_true", help=text("按名称精确匹配（子串）", "Exact name matching (substring)"))
    result.add_argument("--refresh", "-r", action="store_true", help=text("重新查询目录；不会运行 brew update", "Reload names; does not run brew update"))
    result.add_argument("--refresh-index", action="store_true", help=text("仅 update：明确执行 brew update", "update only: explicitly run brew update"))
    result.add_argument("--list", action="store_true", help=text("仅列出；不写入工具缓存", "List only; do not write tool cache"))
    result.add_argument("--dry-run", action="store_true", help=text("选择后只打印计划，不修改软件或工具缓存", "Select and print plan without changing packages or tool cache"))
    result.add_argument("--yes", "-y", action="store_true", help=text("只跳过 mactools 确认", "Skip only mactools confirmation"))
    result.add_argument("--timeout", type=float, default=15, metavar="SECONDS", help=text("每个只读查询的超时（默认 15 秒）", "Timeout per read-only query (default: 15 seconds)"))
    result.add_argument("query", nargs="*", help=text("软件名称查询", "Package name query"))
    return result


def main(argv):
    opts = parser().parse_intermixed_args(argv)
    if not math.isfinite(opts.timeout) or opts.timeout <= 0:
        raise ValueError("--timeout must be a positive finite number")
    if opts.refresh_index and (opts.action != "update" or opts.list or opts.dry_run):
        raise ValueError(text("--refresh-index 仅用于 update，不能与 --list 或 --dry-run 组合", "--refresh-index requires update and cannot be combined with --list or --dry-run"))
    if (opts.apps or opts.app_data) and opts.action != 'remove':
        raise ValueError(text('--apps 和 --app-data 仅用于 remove', '--apps and --app-data require remove'))
    explicit_sources = [source for source, enabled in (("formula", opts.formula), ("cask", opts.cask)) if enabled]
    opts.include_apps = opts.action == 'remove' and (opts.apps or not explicit_sources)
    if opts.app_data and not opts.include_apps:
        raise ValueError(text('--app-data 需要选择应用卸载', '--app-data requires application removal'))
    opts.sources = explicit_sources or ([] if opts.apps else ['formula', 'cask'])
    brew = ''
    if opts.sources:
        try:
            brew = brew_path()
        except ValueError:
            if not opts.include_apps or explicit_sources:
                raise
            opts.sources = []
            print(text('未找到 Homebrew，仅列出本机应用。', 'Homebrew unavailable; showing native applications only.'), file=sys.stderr)
    queries = Queries(brew, opts.timeout)
    try:
        if opts.refresh_index:
            command([brew, "update"])
            if not confirm(text("刷新 Homebrew 索引？", "Refresh Homebrew indexes?"), opts.yes):
                return 0
            code = subprocess.run([brew, "update"], env=queries.env).returncode
            if code:
                return code
        if opts.list:
            query = " ".join(opts.query)
            for row in rows(queries, opts):
                if matches(row.display_name or row.name, query, opts.exact):
                    print('\t'.join((row.source, row.display_name, row.current, row.name))
                          if row.source == 'app' else row.line())
            return 0
        selected = select(queries, opts)
        return execute_selection(brew, queries, opts, selected)
    except BrewError as error:
        print(str(error), file=sys.stderr)
        return error.code
    finally:
        queries.close()


def cask_identity(brew, token):
    # Official qualified/unqualified tokens denote the same cask. A third-party
    # tap or a different Homebrew executable must never be merged by basename.
    prefix = 'homebrew/cask/'
    return (os.path.realpath(brew), token[len(prefix):] if token.startswith(prefix) else token)


def execute_selection(brew, queries, opts, selected):
    native = [row for row in selected if row.source == 'app']
    app_plan = None
    try:
        if native:
            from mactools_apps import AppUninstallPlan, resolve_apps
            apps = resolve_apps([row.name for row in native], opts.timeout)
            app_plan = AppUninstallPlan(apps, opts.timeout, with_data=opts.app_data)
            app_plan.preview()
        # Mole's app uninstaller removes the cask registration itself. If both
        # views of one app were selected, run that uninstall just once.
        handled = {cask_identity(owner, token) for owner, token in app_plan.cask_identities} if app_plan else set()
        remaining = [row for row in selected if row.source != 'app' and not
                     (row.source == 'cask' and cask_identity(brew, row.name) in handled)]
        commands = plan(brew, opts.action, remaining)
        if not commands and app_plan is None:
            return 0
        print(text('执行计划：', 'Plan:'))
        for row in selected:
            print('\t'.join((row.source, row.display_name or row.name, row.current, row.target)))
        for argv in commands:
            command(argv)
        prompt = text('卸载所选应用及预览中的残留，并执行以上计划？',
                      'Uninstall selected apps and reviewed leftovers, and execute this plan?') if app_plan else text('执行上述计划？', 'Execute this plan?')
        if opts.dry_run or not confirm(prompt, opts.yes):
            return 0
        if app_plan:
            code = app_plan.apply(max(600, opts.timeout))
            if code:
                return code
        for argv in commands:
            code = subprocess.run(argv, env=queries.env).returncode
            if code:
                return code
        return 0
    finally:
        if app_plan:
            app_plan.close()


def preview(argv):
    brew, timeout, source, name = argv
    if source == 'app':
        from mactools_apps import preview_detail
        return preview_detail(name, float(timeout))
    if source not in ("formula", "cask"):
        raise ValueError("Invalid source")
    queries = Queries(brew, float(timeout), detached='FZF_PREVIEW_COLUMNS' not in os.environ)
    try:
        data = json.loads(queries.capture("info", "--json=v2", "--" + source, package_name(name)))
        items = inventory_items(data, "formulae" if source == "formula" else "casks")
        if not items:
            raise ValueError(text("未找到软件详情", "Package details unavailable"))
        item = items[0]
        kind = text("命令行工具", "Command-line tool") if source == "formula" else text("桌面应用", "Desktop app")
        print(clean(name) + " · " + kind + "\n")
        installed = cask_installed_version(item) if source == "cask" else versions(item.get("installed"))
        latest = (item.get("versions") or {}).get("stable") if source == "formula" else item.get("version")
        print(text("已安装：", "Installed: ") + (installed if installed != "-" else text("未安装", "Not installed")))
        if source == 'cask' and versions(item.get('installed')) != '-':
            print(text('Homebrew 安装记录：', 'Homebrew installation record: ') + versions(item.get('installed')))
        if latest:
            label = text('Homebrew 可用版本：', 'Homebrew available version: ') if source == 'cask' else text('最新版本：', 'Latest version: ')
            print(label + clean(latest))
        if source == 'cask' and item.get('tap'):
            print(text('来源：', 'Source: ') + clean(item['tap']))
        if item.get("desc"):
            print("\n" + text("简介（上游原文）", "Description") + "\n" + clean(item["desc"]))
        for key, label in (("homepage", text("主页", "Homepage")), ("license", text("许可证", "License"))):
            if item.get(key):
                print("\n" + label + "\n" + clean(item[key]))
        if source == "formula" and item.get("dependencies"):
            print("\n" + text("依赖", "Dependencies") + "\n" + ", ".join(clean(value) for value in item["dependencies"]))
        return 0
    finally:
        queries.close()


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ['--wait-snapshot']:
            status = wait_snapshot(sys.argv[2], float(sys.argv[3]))
        elif sys.argv[1:2] == ['--preview']:
            def cancel_preview(signum, frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, cancel_preview)
            signal.signal(signal.SIGHUP, cancel_preview)
            status = preview(sys.argv[2:])
        else:
            status = main(sys.argv[1:])
    except (OSError, ValueError, KeyboardInterrupt, BrewError) as error:
        print(str(error), file=sys.stderr)
        status = 130 if isinstance(error, KeyboardInterrupt) else 1
    sys.exit(status)
