"""Read-only diagnostics and native Homebrew cleanup plans."""
import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from mactools_runtime import brew_env, brew_path, cache_dir, command, confirm, fetch, run, text


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
            report('WARN' if warn else 'FAIL', label, started, str(exc))
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


def cleanup_actions(brew, args):
    # cleanup has no --formula/--cask selector: pass the installed names explicitly.
    actions = []
    for scope in selected_scopes(args):
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
    if 'formula' in selected_scopes(args):
        actions.append(('autoremove', [brew, 'autoremove']))
    return actions


def clean(args):
    brew = brew_path()
    actions = cleanup_actions(brew, args)
    matches = [(name, argv) for name, argv in actions if not args.query or all((q.lower() == name.lower()) if args.exact else (q.lower() in name.lower()) for q in args.query)]
    if args.generate:
        for name, _ in matches:
            print(name)
        return 0
    if args.list:
        for name, argv in matches:
            print(name + '\t' + shlex.join(argv))
        return 0
    if not matches:
        print(text('没有匹配的清理项目。', 'No matching cleanup actions.'))
        return 0
    if not args.query and not args.yes and shutil.which('fzf'):
        # Preview is a fixed, shell-quoted invocation; selected strings are validated below.
        preview = shlex.join([sys.executable, str(Path(__file__).resolve()), 'clean', '--preview-action']) + ' {1}'
        reload_argv = [sys.executable, str(Path(__file__).resolve()), 'clean', '--generate']
        reload_argv += ['--' + scope for scope in selected_scopes(args)]
        reload_command = shlex.join(reload_argv)
        choices = '\n'.join(name for name, _ in matches) + '\n'
        result = run(['fzf', '--multi', '--header', text('Tab 多选 | Enter 预览并确认', 'Tab: multi-select | Enter: review and confirm'), '--preview', preview, '--bind', 'ctrl-r:reload(' + reload_command + '),ctrl-e:toggle-exact', *(['--exact'] if args.exact else [])], input=choices, text=True, stdout=subprocess.PIPE, env=brew_env(), timeout=86400)
        if result.returncode in (1, 130):
            return 0
        if result.returncode:
            return result.returncode
        chosen = result.stdout.splitlines()
        if any(name not in dict(matches) for name in chosen):
            raise ValueError('invalid cleanup selection')
        matches = [(name, argv) for name, argv in matches if name in chosen]
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
        args.dry_run = args.yes = True
    return doctor(args) if args.action == 'doctor' else clean(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
