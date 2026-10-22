"""Homebrew formula/cask selectors. Queries are bounded; actions keep native prompts."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
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


def display_width(value):
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in value)


def cell(value, width, right=False, center=False):
    padding = " " * max(0, width - display_width(value))
    if center:
        left = len(padding) // 2
        return padding[:left] + value + padding[left:]
    return padding + value if right else value + padding


def clip_cell(value, width):
    if display_width(value) <= width:
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


def app_path_key(value):
    """Normalize a local app bundle path for exact cask ownership matching.

    Relative artifact names are deliberately rejected: without an absolute
    target Homebrew's appdir may differ from the app we are looking at.
    """
    if not isinstance(value, str) or not value:
        return ''
    try:
        expanded = os.path.expanduser(value)
        if not os.path.isabs(expanded):
            return ''
        return os.path.normcase(os.path.realpath(expanded))
    except (OSError, ValueError):
        return ''


def cask_app_paths(item):
    """Return only app targets proven by the installed-cask inventory."""
    targets = set()
    artifacts = item.get('artifacts')
    if not isinstance(artifacts, list):
        return targets
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get('app'), list):
            continue
        candidates = [artifact.get('target')]
        for entry in artifact['app']:
            if isinstance(entry, str):
                candidates.append(entry)
            elif isinstance(entry, dict):
                candidates.append(entry.get('target'))
        for value in candidates:
            key = app_path_key(value)
            if key:
                targets.add(key)
    return targets


def app_alias_path():
    return cache_dir() / 'app-casks.json'


def app_alias_map():
    """Read cached cask tokens keyed by the exact installed app bundle path.

    The token is a search hint only. A selected app row is still resolved and
    revalidated against the live inventory before anything is removed.
    """
    try:
        payload = json.loads(app_alias_path().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    aliases = {}
    for path, token in payload.items():
        key = app_path_key(path)
        if key and isinstance(token, str) and NAME.fullmatch(token):
            aliases[key] = token
    return aliases


def save_app_alias_map(aliases):
    """Replace the alias hint file atomically; failures never block a listing."""
    try:
        if aliases == app_alias_map():
            return
        save_selector_payload(app_alias_path(), dict(sorted(aliases.items())))
    except OSError:
        pass


@dataclass(frozen=True)
class Row:
    source: str
    name: str
    current: str = "-"
    target: str = "-"
    display_name: str = ""
    # Local-only hint used to hide a cask row when its exact app bundle is
    # already shown. It is deliberately absent from selector/cache records.
    app_path: str = field(default="", compare=False, repr=False)
    # Local-only search hint: the owning cask token of a managed app. It keeps
    # the merged app row searchable without bringing the duplicate cask back.
    search_alias: str = field(default="", compare=False, repr=False)

    def line(self):
        return "\t".join((self.source, self.name, clean(self.current), clean(self.target)))

    def selector_line(self, action, widths=None):
        # Keep canonical fields hidden for preview commands and selection validation.
        source = self.source
        current = clean(self.current) if self.current != "-" else text("未安装", "Not installed")
        name_width, version_width = widths or selector_widths(action)
        official = '/' not in self.name or self.name.startswith('homebrew/')
        kind = cell(source, 8)
        label = clean(self.display_name or self.name)
        alias = clean(self.search_alias)
        suffix = ''
        # Only show a complete token: fzf matches this field, so a clipped
        # alias would look searchable while failing its own query.
        if (alias and alias.casefold() != label.casefold()
                and display_width(alias) <= name_width - display_width(label) - 3):
            suffix = ' · ' + alias
        colored = not os.environ.get('NO_COLOR') and os.environ.get('TERM') != 'dumb'
        installed = self.current != '-'
        check = '✓' if installed else ' '
        if suffix:
            padding = ' ' * max(0, name_width - display_width(label + suffix))
            if colored:
                name = f'\033[37m{label}\033[38;5;243m{suffix}\033[0m{padding}'
            else:
                name = label + suffix + padding
        else:
            name = cell(label, name_width)
            if colored:
                name = f'\033[37m{name}\033[0m'
        if colored:
            color = '38;5;220' if source == 'app' else ('38;5;75' if official else '38;5;141')
            kind = f'\033[{color}m{kind}\033[0m'
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
        def signal_group(sig):
            # Darwin reports EPERM for groups containing only unreaped exited
            # processes. Reap our child first, but still signal the group: a
            # finished brew wrapper can leave live children holding its pipes.
            process.poll()
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                # The child can exit between poll() and killpg(). Do not hide
                # a genuine permission error while our query is still alive.
                if process.poll() is None:
                    raise

        try:
            signal_group(signal.SIGTERM)
            process.wait(timeout=0.3)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            pass
        finally:
            # A wrapper may exit before children that still hold stdout/stderr.
            # Kill the remaining group before communicate() drains those pipes.
            signal_group(signal.SIGKILL)

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


def json_inventory(queries, action, sources, app_owners=None):
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
            name = package_name(name)
            if source == 'cask' and app_owners is not None:
                identity = cask_identity(queries.brew, name)
                for path in cask_app_paths(item):
                    app_owners.setdefault(path, set()).add(identity)
            result.append(Row(source, name, current, target))
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
    aliases = app_alias_map()
    rows = []
    for app in installed_apps(opts.timeout):
        path = app_path_key(app.path)
        rows.append(Row('app', app.id, app.version or text('未知', 'Unknown'),
                        display_name=app.name, app_path=app.path,
                        search_alias=aliases.get(path, '')))
    return rows


def rows(queries, opts):
    # Native applications appear before any slower Homebrew inventory query.
    native = list(native_rows(opts))
    yield from native
    if not opts.sources:
        return
    app_owners = {}
    installed = json_inventory(queries, opts.action, opts.sources, app_owners)
    if native and app_owners:
        # Prefer the native row for a Homebrew-managed app: it carries the
        # Mole leftover selection and removes the cask registration itself.
        # Only an exact absolute artifact target can suppress the cask row;
        # ambiguous ownership keeps both rows and is rejected at preview.
        present = {app_path_key(row.app_path) for row in native} - {''}
        owned = {identity for path, identities in app_owners.items()
                 if path in present and len(identities) == 1
                 for identity in identities}
        if owned:
            installed = [row for row in installed if not
                         (row.source == 'cask'
                          and cask_identity(queries.brew, row.name) in owned)]
    # Remember the token of every managed app so the next maci/macr run can
    # keep the merged row searchable without another Homebrew inventory.
    readonly = opts.list or opts.dry_run or getattr(opts, 'list_json', False)
    if opts.action != 'update' and 'cask' in opts.sources and not readonly:
        save_app_alias_map({path: next(iter(identities))[1]
                            for path, identities in app_owners.items()
                            if len(identities) == 1})
    if opts.action != "install":
        yield from sorted(installed, key=lambda row: (row.source, row.name))
        return
    known = {(row.source, row.name): row for row in installed}
    # Installed rows arrive before potentially slow catalog enumeration.
    yield from sorted(installed, key=lambda row: (row.source, row.name))
    with ThreadPoolExecutor(max_workers=len(opts.sources)) as pool:
        futures = {pool.submit(catalog, queries, source, opts.refresh,
                               not (opts.list or opts.dry_run or getattr(opts, 'list_json', False))): source
                   for source in opts.sources}
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


def selector_name_key(value):
    return ''.join(char for char in str(value).casefold() if char.isalnum())


def drop_legacy_duplicate_casks(result, action):
    """Keep old snapshots visually clean until the live refresh replaces them.

    Snapshot rows carry no artifact targets. A unique normalized app/cask name
    pair is a display-only hint here; selection is still revalidated against
    the live inventory before any mutation.
    """
    if action != 'remove':
        return result
    apps, casks = {}, {}
    for row in result:
        key = (selector_name_key(row.display_name or row.name)
               if row.source == 'app'
               else selector_name_key(row.name.rsplit('/', 1)[-1]))
        if key:
            (apps if row.source == 'app' else casks).setdefault(key, []).append(row)
    drop = {}
    for key, found in casks.items():
        owners = apps.get(key, ())
        if len(found) == 1 and len(owners) == 1:
            drop[found[0]] = owners[0]
    if not drop:
        return list(result)
    # The hidden cask token stays searchable as the app row's alias, so a
    # legacy snapshot keeps the same query behavior as the refreshed one.
    hints = {app.name: cask.name.rsplit('/', 1)[-1] for cask, app in drop.items()}
    return [replace(row, search_alias=hints[row.name])
            if row.source == 'app' and row.name in hints and not row.search_alias else row
            for row in result if row not in drop]


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
                    alias = entry.get('alias', '')
                    if not isinstance(alias, str) or (alias and not NAME.fullmatch(alias)):
                        raise ValueError('Invalid cached application alias')
                    result.append(Row(row.source, row.name, row.current, row.target,
                                      display_name=clean(entry['display_name']),
                                      search_alias=alias))
            for line in payload['rows']:
                row = Row.parse(line)
                if row.source not in sources or row.line() != line:
                    raise ValueError('Invalid cached row')
                if opts.action != 'install' and row.current == '-':
                    raise ValueError('Invalid cached installation state')
                if row.source in opts.sources:
                    result.append(row)
            return drop_legacy_duplicate_casks(result, opts.action)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def save_selector_payload(path, payload):
    """Write selector snapshots compactly; these files are read on every start."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, json.dumps(payload, ensure_ascii=False,
                                  separators=(',', ':')).encode())


def app_selector_entry(row):
    entry = {'row': row.line(), 'display_name': row.display_name}
    if row.search_alias:
        entry['alias'] = row.search_alias
    return entry


def save_selector(queries, opts, result):
    if opts.list or opts.dry_run or getattr(opts, 'list_json', False) or queries.cancelled:
        return
    try:
        aliases = (app_alias_map()
                   if 'cask' in opts.sources and opts.action != 'update' else None)
        apps = []
        for row in result:
            if row.source != 'app':
                continue
            if aliases is not None and row.app_path:
                row = replace(row, search_alias=aliases.get(app_path_key(row.app_path), ''))
            apps.append(app_selector_entry(row))
        save_selector_payload(selector_cache_path(opts.action, opts.sources), {
            'brew': queries.brew, 'sources': opts.sources, 'time': time.time(),
            'rows': [row.line() for row in result if row.source != 'app'],
            'apps': apps,
        })
    except OSError:
        # A cache is optional; read-only Homebrew operations still work without it.
        pass


def invalidate_selector_cache():
    """Drop cached selector rows that cannot be patched safely."""
    for path in cache_dir().glob('selector-*.json'):
        try:
            path.unlink()
        except OSError:
            pass


SELECTOR_NAME = re.compile(r'^selector-(install|remove|update)(?:-[a-z-]+)?\.json$')


def selector_cache_action(path):
    match = SELECTOR_NAME.fullmatch(path.name)
    return match.group(1) if match else None


def installed_selection_versions(queries, selected):
    """Read only the versions of packages just installed, never a full inventory."""
    wanted = {}
    for row in selected:
        if row.source in ('formula', 'cask'):
            wanted.setdefault(row.source, []).append(row.name)
    result = {}
    for source, flag in (('formula', '--formula'), ('cask', '--cask')):
        names = wanted.get(source)
        if not names:
            continue
        output = queries.capture('list', '--versions', flag, *names)
        parsed = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            version = clean(parts[-1])
            parsed[parts[0]] = version
            parsed[parts[0].rsplit('/', 1)[-1]] = version
        for name in names:
            version = parsed.get(name) or parsed.get(name.rsplit('/', 1)[-1])
            if not version:
                raise ValueError(text('无法确认安装后的版本', 'Could not confirm the installed version'))
            result[(source, name)] = version
    return result


def patch_selector_cache(queries, action, selected, removed_casks=()):
    """Patch selector snapshots in place after a confirmed mutation.

    Rebuilding a selector is dominated by Homebrew inventory and catalog
    queries, so the next maci/macr run should keep reading its snapshot. Only
    rows touched by this mutation change; unrelated rows and the original
    snapshot time stay intact. A cache that cannot be patched is dropped so the
    next run falls back to a live read instead of showing stale state.
    """
    paths = [path for path in cache_dir().glob('selector-*.json')
             if selector_cache_action(path)]
    if not paths:
        return

    now = time.time()
    brew_keys = {}
    cask_keys = {}
    app_keys = set()
    for row in selected:
        if row.source == 'app':
            app_keys.add(row.name)
        elif row.source == 'cask':
            cask_keys[cask_identity(queries.brew, row.name)] = row
        else:
            brew_keys[(row.source, row.name)] = row
    removed_cask_ids = {cask_identity(brew, token) for brew, token in removed_casks}
    versions = None

    for path in paths:
        cache_action = selector_cache_action(path)
        try:
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict):
                raise ValueError('Invalid selector cache')
            if payload.get('brew') != queries.brew:
                continue
            stamp = payload.get('time')
            if (not isinstance(stamp, (int, float)) or not math.isfinite(stamp)
                    or not 0 <= now - stamp < SELECTOR_CACHE_SECONDS):
                # An expired snapshot is rebuilt live on the next start anyway.
                continue
            rows, apps, sources = payload.get('rows'), payload.get('apps', []), payload.get('sources')
            if (not isinstance(rows, list) or not isinstance(apps, list)
                    or not isinstance(sources, list)):
                raise ValueError('Invalid selector cache')
            if action == 'install' and versions is None:
                # One short local query, never a full inventory scan.
                versions = installed_selection_versions(queries, selected)
            changed = False
            updated_rows = []
            present = set()
            for line in rows:
                row = Row.parse(line)
                if row.line() != line:
                    raise ValueError('Invalid selector cache row')
                found = (cask_keys.get(cask_identity(queries.brew, row.name))
                         if row.source == 'cask' else brew_keys.get((row.source, row.name)))
                removed = action == 'remove' and found is not None
                if (row.source == 'cask'
                        and cask_identity(queries.brew, row.name) in removed_cask_ids):
                    removed = True
                if removed:
                    changed = True
                    if cache_action == 'install':
                        # Installed again after removal: keep the row, clear state.
                        row = Row(row.source, row.name, '-', row.target)
                        updated_rows.append(row.line())
                        present.add((row.source, row.name))
                    # Remove/update selectors must not keep the package at all.
                    continue
                if found is not None:
                    new_row = None
                    if action == 'update' and found.target != '-':
                        new_row = Row(row.source, row.name, found.target, row.target)
                    elif action == 'install':
                        version = versions.get((found.source, found.name))
                        if version:
                            new_row = Row(row.source, row.name, version, row.target)
                    if new_row is not None and new_row.line() != line:
                        row, changed = new_row, True
                updated_rows.append(row.line())
                present.add((row.source, row.name))
            if action == 'install' and cache_action == 'remove':
                # The remove selector must also know about packages installed now.
                for key, version in versions.items():
                    if key in present or key[0] not in sources:
                        continue
                    updated_rows.append(Row(key[0], key[1], version, '-').line())
                    changed = True
            updated_apps = []
            for entry in apps:
                if (not isinstance(entry, dict) or not isinstance(entry.get('row'), str)
                        or not isinstance(entry.get('display_name'), str)):
                    raise ValueError('Invalid selector cache application')
                row = Row.parse(entry['row'])
                if row.source != 'app' or row.line() != entry['row']:
                    raise ValueError('Invalid selector cache application')
                if action == 'remove' and row.name in app_keys:
                    changed = True
                    continue
                updated_apps.append(entry)
            if not changed:
                continue
            payload['rows'] = updated_rows
            if 'apps' in payload:
                payload['apps'] = updated_apps
            save_selector_payload(path, payload)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, BrewError):
            try:
                path.unlink()
            except OSError:
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


def select_explicit(queries, opts):
    """Resolve GUI-supplied identities without opening fzf.

    The desktop app first calls `--list` to show candidates, then passes the
    exact source/name pair back. Nothing is matched by shell-expanded text.
    """
    available = list(rows(queries, opts))
    if opts.select_all:
        if not available:
            print(text('没有可更新的软件。', 'Nothing to update.'))
            return []
        return available
    index = {(row.source, row.name): row for row in available}
    selected = []
    for value in opts.pick:
        source, separator, name = value.partition(':')
        if not separator or source not in ('formula', 'cask', 'app') or not name:
            raise ValueError('Invalid --pick selection; expected SOURCE:NAME')
        row = index.get((source, name))
        if row is None:
            raise ValueError(text('所选软件已变化，请重新搜索：', 'Selection changed; search again: ') + value)
        if row not in selected:
            selected.append(row)
    if not selected:
        raise ValueError(text('没有匹配的软件。', 'No matching package.'))
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
    leftovers = result.add_mutually_exclusive_group()
    leftovers.add_argument("--clean-leftovers", action="store_true", help=text("仅 remove：确认后同时清理预览中的残留", "remove only: clean previewed leftovers after confirmation"))
    leftovers.add_argument("--keep-leftovers", action="store_true", help=text("仅 remove：只卸载应用，保留预览中的残留", "remove only: uninstall apps and keep previewed leftovers"))
    result.add_argument("--exact", "-e", action="store_true", help=text("按名称精确匹配（子串）", "Exact name matching (substring)"))
    result.add_argument("--refresh", "-r", action="store_true", help=text("重新查询目录；不会运行 brew update", "Reload names; does not run brew update"))
    result.add_argument("--refresh-index", action="store_true", help=text("仅 update：明确执行 brew update", "update only: explicitly run brew update"))
    result.add_argument("--list", action="store_true", help=text("仅列出；不写入工具缓存", "List only; do not write tool cache"))
    # Stable machine-readable inventory for the desktop app. It is deliberately
    # read-only: no selector snapshot, catalog cache or alias map is written.
    result.add_argument("--list-json", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--dry-run", action="store_true", help=text("选择后只打印计划，不修改软件或工具缓存", "Select and print plan without changing packages or tool cache"))
    result.add_argument("--yes", "-y", action="store_true", help=text("只跳过 mactools 确认", "Skip only mactools confirmation"))
    result.add_argument("--timeout", type=float, default=15, metavar="SECONDS", help=text("每个只读查询的超时（默认 15 秒）", "Timeout per read-only query (default: 15 seconds)"))
    result.add_argument("--scan-timeout", type=float, default=120, metavar="SECONDS", help=text("仅 remove：每个应用残留扫描的超时（默认 120 秒）", "remove only: leftover scan timeout per app (default: 120 seconds)"))
    # Machine-facing selection used by the desktop app. These bypass fzf and
    # never appear in terminal help: --pick names one exact SOURCE:NAME row and
    # --all is limited to update so a GUI cannot mass-install or mass-remove.
    result.add_argument("--pick", action="append", default=[], metavar="SOURCE:NAME", help=argparse.SUPPRESS)
    result.add_argument("--all", dest="select_all", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("query", nargs="*", help=text("软件名称查询", "Package name query"))
    return result


def main(argv):
    opts = parser().parse_intermixed_args(argv)
    if not math.isfinite(opts.timeout) or opts.timeout <= 0:
        raise ValueError("--timeout must be a positive finite number")
    if not math.isfinite(opts.scan_timeout) or opts.scan_timeout <= 0:
        raise ValueError("--scan-timeout must be a positive finite number")
    if opts.scan_timeout != 120 and opts.action != 'remove':
        raise ValueError(text('--scan-timeout 仅用于 remove', '--scan-timeout requires remove'))
    if opts.pick and opts.select_all:
        raise ValueError('--pick and --all are mutually exclusive')
    if opts.select_all and opts.action != 'update':
        raise ValueError('--all is only available for update')
    if (opts.pick or opts.select_all) and opts.refresh_index:
        raise ValueError('--pick/--all cannot be combined with --refresh-index')
    if opts.list_json and (opts.list or opts.dry_run or opts.refresh_index
                           or opts.pick or opts.select_all or opts.yes):
        raise ValueError('--list-json cannot be combined with other output or execution flags')
    if opts.refresh_index and (opts.action != "update" or opts.list or opts.dry_run or opts.list_json):
        raise ValueError(text("--refresh-index 仅用于 update，不能与 --list 或 --dry-run 组合", "--refresh-index requires update and cannot be combined with --list or --dry-run"))
    if (opts.apps or opts.app_data) and opts.action != 'remove':
        raise ValueError(text('--apps 和 --app-data 仅用于 remove', '--apps and --app-data require remove'))
    if (opts.clean_leftovers or opts.keep_leftovers) and opts.action != 'remove':
        raise ValueError(text('--clean-leftovers 和 --keep-leftovers 仅用于 remove',
                              '--clean-leftovers and --keep-leftovers require remove'))
    if opts.app_data and opts.keep_leftovers:
        raise ValueError(text('--app-data 与 --keep-leftovers 不能同时使用',
                              '--app-data cannot be combined with --keep-leftovers'))
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
            invalidate_selector_cache()
        if opts.list or opts.list_json:
            query = " ".join(opts.query)
            found = [row for row in rows(queries, opts)
                     if matches(row.display_name or row.name, query, opts.exact)]
            if opts.list_json:
                print(json.dumps([{
                    'source': row.source,
                    'name': row.name,
                    'displayName': row.display_name or row.name,
                    'current': row.current,
                    'target': row.target,
                    'appPath': row.app_path or None,
                } for row in found], ensure_ascii=False, separators=(',', ':')))
            else:
                for row in found:
                    print('\t'.join((row.source, row.display_name, row.current, row.name))
                          if row.source == 'app' else row.line())
            return 0
        selected = select_explicit(queries, opts) if (opts.pick or opts.select_all) else select(queries, opts)
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
    mutated = False
    patched = False
    try:
        if native:
            from mactools_apps import AppUninstallPlan, resolve_apps
            apps = resolve_apps([row.name for row in native], opts.timeout)
            app_plan = AppUninstallPlan(apps, opts.timeout, with_data=opts.app_data,
                                       scan_timeout=opts.scan_timeout)
            app_plan.preview()
        # Mole's app uninstaller removes the cask registration itself. If both
        # views of one app were selected, run that uninstall just once.
        handled = {cask_identity(owner, token) for owner, token in app_plan.cask_identities} if app_plan else set()
        remaining = [row for row in selected if row.source != 'app' and not
                     (row.source == 'cask' and cask_identity(brew, row.name) in handled)]
        commands = plan(brew, opts.action, remaining)
        if not commands and app_plan is None:
            return 0
        clean_leftovers = True
        if app_plan:
            if opts.keep_leftovers:
                clean_leftovers = False
            elif opts.clean_leftovers or opts.app_data:
                clean_leftovers = True
            elif opts.dry_run:
                clean_leftovers = None
            elif opts.yes:
                # --yes skips confirmation, not the destructive data choice.
                # Without an explicit opt-in, leaving leftovers is the safe path.
                clean_leftovers = False
            else:
                clean_leftovers = confirm(text('是否同时清理预览中的残留？',
                                               'Also clean the previewed leftovers?'), False)
        print(text('执行计划：', 'Plan:'))
        for row in selected:
            print('\t'.join((row.source, row.display_name or row.name, row.current, row.target)))
        if app_plan:
            if clean_leftovers is None:
                print(text('残留：执行前选择清理或保留', 'Leftovers: choose clean or keep before execution'))
            elif clean_leftovers:
                print(text('残留：清理', 'Leftovers: clean'))
            elif opts.yes and not opts.keep_leftovers:
                print(text('残留：保留（使用 --clean-leftovers 可清理）',
                           'Leftovers: keep (use --clean-leftovers to clean them)'))
            else:
                print(text('残留：保留', 'Leftovers: keep'))
        for argv in commands:
            command(argv)
        if app_plan:
            prompt = (text('卸载所选应用并清理预览中的残留，执行以上计划？',
                           'Uninstall selected apps and clean previewed leftovers, and execute this plan?')
                      if clean_leftovers else
                      text('卸载所选应用并保留预览中的残留，执行以上计划？',
                           'Uninstall selected apps while keeping previewed leftovers, and execute this plan?'))
        else:
            prompt = text('执行上述计划？', 'Execute this plan?')
        if opts.dry_run or not confirm(prompt, opts.yes):
            return 0
        mutated = True
        if app_plan:
            code = app_plan.apply(max(600, opts.timeout), clean_leftovers=bool(clean_leftovers))
            if code:
                return code
        for argv in commands:
            code = subprocess.run(argv, env=queries.env).returncode
            if code:
                return code
        patch_queries = None
        try:
            removed_casks = (getattr(app_plan, 'cask_identities', ())
                             if app_plan else ())
            # select() closes its query owner; patching needs a live one for the
            # single local "brew list --versions" call.
            patch_queries = Queries(brew, opts.timeout)
            patch_selector_cache(patch_queries, opts.action, selected, removed_casks)
            patched = True
        except (BrewError, OSError, ValueError, TypeError, KeyError):
            pass
        finally:
            if patch_queries is not None:
                patch_queries.close()
        return 0
    finally:
        # A partial or unpatched mutation must never leave a stale snapshot.
        if mutated and not patched:
            invalidate_selector_cache()
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
