"""Read-only diagnostics and native Homebrew cleanup plans."""
import argparse
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from mactools_runtime import brew_env, brew_path, cache_dir, command, confirm, fetch, run, text
from mactools_packages import cell, clip_cell, selector_widths, without_color


def positive(value):
    number = float(value)
    if not 0 < number <= 600:
        raise argparse.ArgumentTypeError('expected 0 < seconds <= 600')
    return number


def doctor(args):
    failed = False

    def report(status, label, started=None, detail=''):
        nonlocal failed
        failed |= status == 'FAIL'
        elapsed = 0 if started is None else (time.monotonic() - started) * 1000
        print(f'{status:4} {label:28} {elapsed:8.0f}ms {detail}')

    def probe(label, argv, warn=False):
        started = time.monotonic()
        try:
            result = run(argv, timeout=args.timeout, env=brew_env(), capture_output=True, text=True)
            status = 'OK' if result.returncode == 0 else ('WARN' if warn else 'FAIL')
            report(status, label, started, (result.stdout or result.stderr).strip()[:300].replace('\n', '; '))
            return result if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            report('FAIL', label, started, str(exc))
            return None

    print(text('mactools 环境诊断（状态 / 检查 / 耗时 / 说明）', 'mactools diagnostics (status / check / duration / detail)'))
    for name in ('python3', 'fzf', 'curl'):
        found = shutil.which(name)
        report('OK' if found else ('FAIL' if name == 'python3' else 'SKIP'), 'dependency: ' + name, detail=found or text('缺失', 'missing'))
    try:
        brew = brew_path()
    except ValueError as exc:
        report('FAIL', 'dependency: brew', detail=str(exc))
        return 1
    report('OK', 'dependency: brew', detail=brew)
    machine = platform.machine()
    report('OK' if platform.system() == 'Darwin' else 'WARN', 'platform / architecture', detail=f'{platform.system()} / {machine}')
    prefix = probe('Homebrew prefix', [brew, '--prefix'])
    if prefix:
        expected = '/opt/homebrew' if machine == 'arm64' else '/usr/local'
        if prefix.stdout.strip() != expected:
            report('WARN', 'Homebrew prefix / CPU', detail=f'{prefix.stdout.strip()} (usual: {expected}; Rosetta/custom prefixes may differ)')
    if shutil.which('xcode-select'):
        probe('Xcode Command Line Tools', ['xcode-select', '-p'])
    else:
        report('SKIP', 'Xcode Command Line Tools', detail='xcode-select unavailable')
    probe('Homebrew doctor', [brew, 'doctor'], warn=True)
    for scope in selected_scopes(args):
        probe(f'Homebrew list: {scope}', [brew, 'list', '--' + scope])
    cache = cache_dir()
    files = list(cache.glob('*.json')) if cache.exists() else []
    if files:
        age = max(0, int(time.time() - min(p.stat().st_mtime for p in files)))
        report('WARN' if age > 3600 else 'OK', 'mactools catalog cache', detail=f'age={age}s')
    else:
        report('WARN', 'mactools catalog cache', detail=text('尚未建立缓存', 'cache not built'))
    probe('Homebrew cache path', [brew, '--cache'])
    if args.offline:
        report('SKIP', 'Remote connectivity', detail='offline')
    else:
        for label, url in [('Homebrew API', os.environ.get('HOMEBREW_API_DOMAIN', 'https://formulae.brew.sh/api') + '/formula/hello.json'), ('Homebrew registry', 'https://ghcr.io/v2/')]:
            started = time.monotonic()
            try:
                # A small real request tests TLS/DNS/connectivity without package writes.
                fetch(url, timeout=args.timeout, limit=1024, partial=True)
                report('OK', label, started)
            except Exception as exc:
                # Registry authentication challenges still establish connectivity.
                if getattr(exc, 'code', None) == 401 and 'registry' in label:
                    report('OK', label, started, 'registry authentication challenge')
                else:
                    report('FAIL', label, started, str(exc))
    return int(failed)


def selected_scopes(args):
    return [name for name in ('formula', 'cask') if getattr(args, name)] or ['formula', 'cask']


def cleanup_choices(args):
    names = [scope + '-cache' for scope in selected_scopes(args)]
    if 'formula' in selected_scopes(args):
        names.append('autoremove')
    return [name for name in names if not args.query or all(
        (q.lower() == name.lower()) if args.exact else (q.lower() in name.lower())
        for q in args.query)]


def cleanup_actions(brew, args, choices):
    # cleanup has no --formula/--cask selector: pass the installed names explicitly.
    actions = []
    for scope in selected_scopes(args):
        if scope + '-cache' not in choices:
            continue
        result = run([brew, 'list', '--' + scope, '--full-name', '-1'], env=brew_env(), capture_output=True, text=True, timeout=args.timeout)
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'brew list failed')
        names = result.stdout.splitlines()
        if names:
            if any(name.startswith('-') or not name.strip() or any(c.isspace() for c in name) for name in names):
                raise ValueError('invalid package name returned by brew list')
            # cleanup resolves both formulae and casks for an unqualified token.
            # Fully qualify core names to preserve the requested source.
            tap = 'homebrew/core/' if scope == 'formula' else 'homebrew/cask/'
            names = [name if '/' in name else tap + name for name in names]
            actions.append((scope + '-cache', [brew, 'cleanup', '--', *names]))
    if 'autoremove' in choices:
        actions.append(('autoremove', [brew, 'autoremove']))
    return actions


def cleanup_info(name):
    return {
        'formula-cache': ('formula', text('Formula 缓存', 'Formula cache'),
                          text('缓存与旧版本', 'Cache / old versions'),
                          text('清理已安装 Formula 的旧版本和下载缓存。',
                               'Clean old versions and downloads for installed formulae.')),
        'cask-cache': ('cask', text('Cask 缓存', 'Cask cache'),
                       text('缓存与旧版本', 'Cache / old versions'),
                       text('清理已安装 Cask 的旧版本和下载缓存。',
                            'Clean old versions and downloads for installed casks.')),
        'autoremove': ('formula', text('无用依赖', 'Unused dependencies'),
                       text('未使用的依赖', 'Unused dependencies'),
                       text('移除 Homebrew 判定已不再需要的 Formula 依赖。',
                            'Remove formula dependencies Homebrew considers no longer needed.')),
    }[name]


def cleanup_rows(choices):
    name_width, _ = selector_widths('install')
    scope_width = 20
    rows = []
    colored = not os.environ.get('NO_COLOR') and os.environ.get('TERM') != 'dumb'
    for name in choices:
        kind, title, scope, _ = cleanup_info(name)
        kind_cell = cell(kind, 8)
        if colored:
            kind_cell = '\033[38;5;75m' + kind_cell + '\033[0m'
        visible = (kind_cell + '\t' + cell(clip_cell(title, name_width), name_width) + '\t' +
                   cell(clip_cell(scope, scope_width), scope_width, right=True))
        rows.append('\t'.join((name, kind, title, scope, visible)))
    headings = (cell(text('类型', 'Type'), 8) + ' ' +
                cell(text('名称', 'Name'), name_width, center=True) + ' ' +
                cell(text('清理范围', 'Scope'), scope_width, right=True))
    return rows, headings


def choose_cleanup(args, choices):
    query, exact = '', args.exact
    preview = shlex.join([sys.executable, str(Path(__file__).resolve()), 'clean',
                          '--timeout', str(args.timeout), '--preview-action']) + ' {1}'
    env = brew_env()
    env.pop('FZF_DEFAULT_OPTS', None)
    env.pop('FZF_DEFAULT_OPTS_FILE', None)
    env['SHELL'] = '/bin/sh'
    while True:
        rows, headings = cleanup_rows(choices)
        header = text('Tab 多选  Enter 确认  Ctrl+R 刷新  Ctrl+E 匹配  Ctrl+/ 详情  Esc 退出',
                      'Tab select  Enter confirm  Ctrl+R refresh  Ctrl+E match  Ctrl+/ details  Esc exit')
        argv = ['fzf', '--ansi', '--multi', '--delimiter=\t', '--nth=2',
                '--with-nth=5..', '--tabstop=1', '--print-query', '--expect=ctrl-e,ctrl-r',
                '--query=' + query, '--height=95%', '--layout=reverse', '--border',
                '--tiebreak=index', '--info=hidden', '--preview=' + preview,
                '--preview-window=right:42%:wrap:<50(down:35%:wrap)',
                '--prompt=' + text('搜索 › ', 'Search › '),
                '--bind=tab:toggle+down,shift-tab:toggle+up,ctrl-a:select-all,alt-j:last,alt-k:first,ctrl-/:toggle-preview',
                '--header=' + header + '\n\n' + headings]
        if exact:
            argv.append('--exact')
        result = run(argv, input='\n'.join(rows) + '\n', text=True,
                     stdout=subprocess.PIPE, env=env, timeout=86400)
        if result.returncode in (1, 130):
            return [], 0
        if result.returncode:
            return [], result.returncode
        fields = result.stdout.splitlines()
        query = fields[0] if fields else ''
        key = fields[1] if len(fields) > 1 else ''
        if key in ('ctrl-e', 'ctrl-r'):
            if key == 'ctrl-e':
                exact = not exact
            continue
        allowed = {without_color(row): row.split('\t', 1)[0] for row in rows}
        selected = []
        for row in fields[2:]:
            if without_color(row) not in allowed:
                raise ValueError('invalid cleanup selection')
            selected.append(allowed[without_color(row)])
        return [name for name in choices if name in selected], 0


def preview_cleanup(args):
    name = args.preview_action
    kind, title, _, description = cleanup_info(name)
    print(f'{title} · {kind}\n\n{description}\n')
    print(text('Homebrew 预览 · 回车核对，确认后执行',
               'Homebrew preview · Enter to review, then confirm'), flush=True)
    actions = cleanup_actions(brew_path(), args, [name])
    if not actions:
        print(text('没有可清理的已安装软件。', 'No installed packages to clean.'))
        return 0
    for _, argv in actions:
        result = run(argv[:2] + ['--dry-run'] + argv[2:], timeout=args.timeout,
                     env=brew_env(), capture_output=True, text=True)
        skipped, lines = 0, []
        for output in (result.stdout, result.stderr):
            for line in (output or '').splitlines():
                if (result.returncode == 0 and
                        re.match(r'^Warning: Skipping .+: most recent version .+ not installed\s*$', without_color(line))):
                    skipped += 1
                else:
                    lines.append(line)
        if skipped:
            print(text(f'{skipped} 项尚未安装最新版，Homebrew 已跳过对应旧版本清理。',
                       f'Homebrew skipped old-version cleanup for {skipped} packages without the latest version installed.'))
        for line in lines:
            print(line)
        if not result.stdout.strip() and not result.stderr.strip():
            print(text('当前没有可清理的项目。', 'Nothing to clean right now.'))
        if result.returncode:
            return result.returncode
    return 0


def clean(args):
    choices = cleanup_choices(args)
    if args.generate:
        for name in choices:
            print(name)
        return 0
    if not choices:
        print(text('没有匹配的清理项目。', 'No matching cleanup actions.'))
        return 0
    brew = brew_path()
    if not args.list and not args.query and not args.yes and shutil.which('fzf'):
        choices, status = choose_cleanup(args, choices)
        if status or not choices:
            return status
    # Build plans from current inventory only after selection. No cached names
    # can authorize cleanup, and unused scopes never need a Homebrew query.
    matches = cleanup_actions(brew, args, choices)
    if args.list:
        for name, argv in matches:
            print(name + '\t' + shlex.join(argv))
        return 0
    if not matches:
        print(text('没有匹配的清理项目。', 'No matching cleanup actions.'))
        return 0
    for _, argv in matches:
        preview = argv[:2] + ['--dry-run'] + argv[2:]
        command(preview)
        result = run(preview, timeout=args.timeout, env=brew_env())
        if result.returncode:
            return result.returncode
    if args.dry_run:
        return 0
    if not matches or not confirm(text('执行以上 Homebrew 清理计划？', 'Apply the Homebrew cleanup plan above?'), args.yes):
        return 0
    for _, argv in matches:
        command(argv)
        result = run(argv, timeout=600, env=brew_env())
        if result.returncode:
            return result.returncode
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=text('macOS 环境诊断与安全清理', 'macOS diagnostics and safe cleanup'))
    subs = parser.add_subparsers(dest='action', required=True)
    for action in ('doctor', 'clean'):
        sub = subs.add_parser(action)
        sub.add_argument('--formula', '--brew', action='store_true')
        sub.add_argument('--cask', action='store_true')
        sub.add_argument('--timeout', type=positive, default=15)
        if action == 'doctor':
            sub.add_argument('--offline', action='store_true')
        else:
            sub.add_argument('--list', action='store_true')
            sub.add_argument('--dry-run', action='store_true')
            sub.add_argument('--yes', '-y', action='store_true')
            sub.add_argument('-e', '--exact', action='store_true')
            sub.add_argument('--generate', action='store_true', help=argparse.SUPPRESS)
            sub.add_argument('--preview-action', choices=('formula-cache', 'cask-cache', 'autoremove'), help=argparse.SUPPRESS)
            sub.add_argument('query', nargs='*')
    args = parser.parse_args(argv)
    if args.action == 'clean' and args.preview_action:
        args.query = [args.preview_action]
        return preview_cleanup(args)
    return doctor(args) if args.action == 'doctor' else clean(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
