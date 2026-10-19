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
import unicodedata

from mactools_runtime import brew_env, brew_path, cache_dir, command, confirm, save_json, text

CACHE_SECONDS = 3600
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@+_.-]*(?:/[A-Za-z0-9][A-Za-z0-9@+_.-]*){0,2}$")


class BrewError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code if code > 0 else 1


def clean(value):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))


def cell(value, width, right=False, center=False):
    used = sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in value)
    padding = " " * max(0, width - used)
    if center:
        left = len(padding) // 2
        return padding[:left] + value + padding[left:]
    return padding + value if right else value + padding


def selector_widths(action, terminal_width=None):
    columns = terminal_width or shutil.get_terminal_size((120, 24)).columns
    inner = max(40, columns - 4)
    preview = int(inner * 0.42)
    # Match fzf's right preview and its narrow-window fallback below.
    available = inner - preview - 4 if preview >= 50 else inner - 4
    version_columns = 2 if action == "update" else 1
    version_width = 18
    name_width = max(20, available - 6 - version_columns * version_width - (version_columns + 1))
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

    def line(self):
        return "\t".join((self.source, self.name, clean(self.current), clean(self.target)))

    def selector_line(self, action, widths=None):
        # Keep canonical fields hidden for preview commands and selection validation.
        source = text("工具", "CLI") if self.source == "formula" else text("应用", "App")
        current = clean(self.current) if self.current != "-" else text("未安装", "Not installed")
        name_width, version_width = widths or selector_widths(action)
        columns = [cell(source, 6), cell(self.name, name_width, center=True), cell(current, version_width, right=True)]
        if action == "update":
            columns.append(cell(clean(self.target), version_width, right=True))
        return self.line() + "\t" + "\t".join(columns)

    @classmethod
    def parse(cls, line):
        parts = line.split("\t")
        if len(parts) < 4 or parts[0] not in ("formula", "cask"):
            raise ValueError("Invalid package selection")
        return cls(parts[0], package_name(parts[1]), parts[2], parts[3])


class Queries:
    """Own query processes so leaving fzf also stops outstanding brew requests."""
    def __init__(self, brew, timeout):
        self.brew, self.timeout = brew, timeout
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
                                       start_new_session=True)
            self.processes.add(process)
        try:
            try:
                output, errors = process.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.stop(process)
                process.communicate()
                raise BrewError(text("Homebrew 查询超时", "Homebrew query timed out"), 124)
            if process.returncode:
                raise BrewError(clean(errors.strip()) or "Homebrew query failed", process.returncode)
            return output
        finally:
            with self.lock:
                self.processes.discard(process)

    @staticmethod
    def stop(process):
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
    raw = queries.capture("outdated", "--json=v2") if action == "update" else queries.capture("info", "--json=v2", "--installed")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid Homebrew JSON inventory")
    cask_names = {}
    if action == "update" and "cask" in sources and data.get("casks"):
        # Outdated JSON emits cask short tokens. Keep the installed tap identity
        # so an identically named cask in another tap cannot be upgraded instead.
        inventory = json.loads(queries.capture("info", "--json=v2", "--installed"))
        for item in inventory_items(inventory, "casks"):
            token = item.get("token")
            if token:
                token = package_name(token)
                cask_names[token] = item.get("full_token") or token
    result = []
    for source, key in (("formula", "formulae"), ("cask", "casks")):
        if source not in sources:
            continue
        for item in inventory_items(data, key):
            if source == "formula":
                name = item.get("full_name") or item.get("name")
            else:
                name = item.get("full_token") or item.get("token") or item.get("name")
                name = cask_names.get(package_name(name), name)
            current = versions(item.get("installed_versions") if action == "update" else item.get("installed"))
            target = clean(item.get("current_version") or "-") if action == "update" else "-"
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


def rows(queries, opts):
    installed = json_inventory(queries, opts.action, opts.sources)
    if opts.action != "install":
        yield from sorted(installed, key=lambda row: (row.source, row.name))
        return
    known = {(row.source, row.name): row for row in installed}
    # Installed rows arrive before potentially slow catalog enumeration.
    yield from sorted(installed, key=lambda row: (row.source, row.name))
    # Emit even expired cached names first; refreshes prepare this and future runs.
    if not opts.refresh:
        for source in opts.sources:
            payload = cached_catalog(cache_dir() / ("packages-" + source + ".json"), queries.brew)
            if payload is None:
                continue
            names = payload[0]
            for name in names:
                if (source, name) not in known:
                    row = Row(source, name)
                    known[(source, name)] = row
                    yield row
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


def select(queries, opts):
    fzf = shutil.which("fzf")
    if not fzf:
        raise ValueError(text("未找到 fzf，请先运行 brew install fzf", "fzf not found. Run brew install fzf first"))
    query, exact = " ".join(opts.query), opts.exact
    while True:
        widths = selector_widths(opts.action)
        name_width, version_width = widths
        preview = shlex.join([sys.executable, str(Path(__file__).resolve()), "--preview", queries.brew, str(opts.timeout)]) + " {1} {2}"
        headings = cell(text("类型", "Type"), 6) + " " + cell(text("名称", "Name"), name_width, center=True) + " "
        installed_heading = text("已安装版本", "Installed version")
        headings += cell(installed_heading, version_width, right=True)
        if opts.action == "update":
            headings += " " + cell(text("可更新至", "Available version"), version_width, right=True)
        header = text("Tab 多选  Enter 确认  Ctrl+R 刷新  Ctrl+E 匹配  Ctrl+/ 详情  Esc 退出", "Tab select  Enter confirm  Ctrl+R refresh  Ctrl+E match  Ctrl+/ details  Esc exit") + "\n\n" + headings
        argv = [fzf, "--multi", "--delimiter=\t", "--nth=2", "--with-nth=5..", "--tabstop=1",
                "--print-query", "--expect=ctrl-e,ctrl-r", "--query=" + query,
                "--height=95%", "--layout=reverse", "--border", "--tiebreak=index", "--info=hidden",
                "--preview=" + preview, "--preview-window=right:42%:wrap:<50(down:35%:wrap)",
                "--prompt=" + text("搜索 › ", "Search › "),
                "--bind=tab:toggle+down,shift-tab:toggle+up,alt-j:last,alt-k:first,ctrl-/:toggle-preview",
                "--header=" + header]
        if exact:
            argv.append("--exact")
        env = dict(os.environ)
        # User defaults can alter delimiters/output and compromise selection parsing.
        env.pop("FZF_DEFAULT_OPTS", None)
        env.pop("FZF_DEFAULT_OPTS_FILE", None)
        env["SHELL"] = "/bin/sh"
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        errors, emitted = [], {}

        def produce():
            try:
                for row in rows(queries, opts):
                    emitted[(row.source, row.name)] = row
                    process.stdin.write(row.selector_line(opts.action, widths) + "\n")
                    process.stdin.flush()
            except BrokenPipeError:
                pass
            except Exception as error:
                if not queries.cancelled:
                    errors.append(error)
                    print(str(error), file=sys.stderr)
            finally:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass

        worker = threading.Thread(target=produce, daemon=True)
        worker.start()
        try:
            output = process.stdout.read()
            process.stdout.close()
            status = process.wait()
        finally:
            queries.close()
            worker.join(timeout=opts.timeout + 1)
            if process.poll() is None:
                process.terminate()
                process.wait()
        # Cancellation never applies a partially loaded selection.
        if status in (1, 130):
            if errors and status != 130:
                raise errors[0]
            return []
        if status:
            raise BrewError("fzf failed", status)
        fields = output.splitlines()
        query = fields[0] if fields else ""
        key = fields[1] if len(fields) > 1 else ""
        if key in ("ctrl-e", "ctrl-r"):
            if key == "ctrl-e":
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
            actual = emitted.get((row.source, row.name))
            if actual is None or actual.selector_line(opts.action, widths) != line:
                raise ValueError("Unknown package selection")
            if actual not in selected:
                selected.append(actual)
        return selected


def plan(brew, action, selected):
    operation = {"install": "install", "remove": "uninstall", "update": "upgrade"}[action]
    return [[brew, operation, "--" + source, *sorted({row.name for row in selected if row.source == source})]
            for source in ("formula", "cask") if any(row.source == source for row in selected)]


def parser():
    result = argparse.ArgumentParser(prog="ma", description=text("Homebrew 多选安装、卸载、更新", "Select Homebrew packages to install, remove or update"))
    result.add_argument("action", choices=("install", "remove", "update"))
    result.add_argument("--formula", "--brew", action="store_true", help=text("选择 formula，可与 --cask 组合", "Select formulae; combine with --cask"))
    result.add_argument("--cask", action="store_true", help=text("选择 cask", "Select casks"))
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
    opts.sources = [source for source, enabled in (("formula", opts.formula), ("cask", opts.cask)) if enabled] or ["formula", "cask"]
    brew = brew_path()
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
                if matches(row.name, query, opts.exact):
                    print(row.line())
            return 0
        selected = select(queries, opts)
        commands = plan(brew, opts.action, selected)
        if not commands:
            return 0
        print(text("执行计划：", "Plan:"))
        for row in selected:
            print(row.line())
        for argv in commands:
            command(argv)
        if opts.dry_run or not confirm(text("执行上述计划？", "Execute this plan?"), opts.yes):
            return 0
        for argv in commands:
            code = subprocess.run(argv, env=queries.env).returncode
            if code:
                return code
        return 0
    except BrewError as error:
        print(str(error), file=sys.stderr)
        return error.code
    finally:
        queries.close()


def preview(argv):
    brew, timeout, source, name = argv
    if source not in ("formula", "cask"):
        raise ValueError("Invalid source")
    queries = Queries(brew, float(timeout))
    try:
        data = json.loads(queries.capture("info", "--json=v2", "--" + source, package_name(name)))
        items = inventory_items(data, "formulae" if source == "formula" else "casks")
        if not items:
            raise ValueError(text("未找到软件详情", "Package details unavailable"))
        item = items[0]
        kind = text("命令行工具", "Command-line tool") if source == "formula" else text("桌面应用", "Desktop app")
        print(clean(name) + " · " + kind + "\n")
        installed = versions(item.get("installed"))
        latest = (item.get("versions") or {}).get("stable") if source == "formula" else item.get("version")
        print(text("已安装：", "Installed: ") + (installed if installed != "-" else text("未安装", "Not installed")))
        if latest:
            print(text("最新版本：", "Latest version: ") + clean(latest))
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
        status = preview(sys.argv[2:]) if sys.argv[1:2] == ["--preview"] else main(sys.argv[1:])
    except (OSError, ValueError, KeyboardInterrupt, BrewError) as error:
        print(str(error), file=sys.stderr)
        status = 130 if isinstance(error, KeyboardInterrupt) else 1
    sys.exit(status)
