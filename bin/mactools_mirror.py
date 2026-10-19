"""Bounded bottle sampling and transactional, user-owned Homebrew mirror config.

References: https://docs.brew.sh/Manpage
https://mirrors.tuna.tsinghua.edu.cn/help/homebrew-bottles/
https://mirrors.ustc.edu.cn/help/homebrew-bottles.html
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import platform
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mactools_runtime import atomic_write, config_dir, confirm, fetch, run, save_json, state_dir, text

OFFICIAL_API = 'https://formulae.brew.sh/api'
OFFICIAL_BOTTLES = 'https://ghcr.io/v2/homebrew/core'
MIRRORS = {
    'official': OFFICIAL_BOTTLES,
    'tuna': 'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles',
    'ustc': 'https://mirrors.ustc.edu.cn/homebrew-bottles',
}
SAMPLE_LIMIT = 8 * 1024 * 1024


def normalize(url):
    value = urllib.parse.urlsplit(url)
    if value.scheme not in ('http', 'https') or not value.hostname or value.username or value.password or value.query or value.fragment or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('Mirror must be an HTTP(S) URL without credentials, whitespace, query or fragment')
    return url.rstrip('/')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def positive(value):
    number = float(value)
    if not 0 < number <= 120:
        raise argparse.ArgumentTypeError('expected 0 < seconds <= 120')
    return number


def bounded_count(value):
    number = int(value)
    if not 1 <= number <= 32:
        raise argparse.ArgumentTypeError('expected an integer from 1 to 32')
    return number


def bottle_fetch(url, timeout):
    # Homebrew uses this anonymous bearer for public GHCR bottles. Never forward
    # a user's registry credentials to mirrors.
    headers = {'Authorization': 'Bearer QQ=='} if urllib.parse.urlsplit(url).hostname == 'ghcr.io' else {}
    return fetch(url, timeout=timeout, limit=SAMPLE_LIMIT, headers=headers)


def reference(timeout):
    """Trust only the official API hash, then verify the entire small bottle."""
    data = json.loads(fetch(OFFICIAL_API + '/formula/hello.json', timeout=timeout, limit=1024 * 1024))
    try:
        files = data['bottle']['stable']['files']
        if not isinstance(files, dict):
            raise TypeError('bottle files must be an object')
    except (KeyError, TypeError) as exc:
        raise ValueError('Invalid official Homebrew reference metadata') from exc
    arm = platform.machine() in ('arm64', 'aarch64')
    candidates = [(tag, entry) for tag, entry in files.items() if isinstance(tag, str) and 'linux' not in tag and tag.startswith('arm64_') == arm]
    if not candidates:
        raise ValueError('No macOS reference bottle found for this architecture')
    tag, entry = candidates[0]
    if not isinstance(entry, dict) or not isinstance(entry.get('sha256'), str) or not isinstance(entry.get('url'), str):
        raise ValueError('Invalid official Homebrew bottle metadata')
    checksum = entry['sha256']
    url = entry['url']
    if not re.fullmatch(r'[a-f0-9]{64}', checksum) or not url.startswith(OFFICIAL_BOTTLES + '/hello/blobs/sha256:'):
        raise ValueError('Unexpected official reference bottle metadata')
    payload = bottle_fetch(url, timeout)
    if not payload or digest(payload) != checksum:
        raise ValueError('Official reference bottle checksum mismatch')
    try:
        version = data['versions']['stable']
        if not isinstance(version, str):
            raise TypeError('version must be a string')
    except (KeyError, TypeError) as exc:
        raise ValueError('Invalid official Homebrew version metadata') from exc
    revision = data.get('revision', 0)
    rebuild = data['bottle']['stable'].get('rebuild', 0)
    # Legacy mirror naming follows Homebrew Bottle::Filename.
    version = version + (f'_{revision}' if revision else '')
    filename = f'hello-{version}.{tag}.bottle' + (f'.{rebuild}' if rebuild else '') + '.tar.gz'
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', filename):
        raise ValueError('Unexpected bottle filename')
    return {'url': url, 'filename': filename, 'sha256': checksum, 'bytes': len(payload)}


def measure(name, url, sample, timeout):
    started = time.monotonic()
    try:
        target = sample['url'] if url == OFFICIAL_BOTTLES else url + '/' + sample['filename']
        payload = bottle_fetch(target, timeout)
        elapsed = max(time.monotonic() - started, 0.000001)
        if digest(payload) != sample['sha256']:
            raise ValueError('reference bottle SHA-256 mismatch')
        return {'name': name, 'url': url, 'ok': True, 'bytes': len(payload), 'seconds': round(elapsed, 6), 'bytes_per_second': round(len(payload) / elapsed), 'sha256': sample['sha256']}
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return {'name': name, 'url': url, 'ok': False, 'seconds': round(time.monotonic() - started, 6), 'error': str(exc)}


def benchmark(args):
    candidates = []
    for item in args.mirror or MIRRORS:
        url = normalize(MIRRORS.get(item, item))
        if url not in [u for _, u in candidates]:
            candidates.append((item, url))
    if len(candidates) > args.limit:
        candidates = candidates[:args.limit]
    sample = reference(args.timeout)
    result = []
    with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
        futures = [pool.submit(measure, name, url, sample, args.timeout) for name, url in candidates]
        for future in as_completed(futures):
            result.append(future.result())
    return sorted(result, key=lambda item: (not item['ok'], -item.get('bytes_per_second', 0), item['name']))


def render(url):
    if url == OFFICIAL_BOTTLES:
        bottle, api = OFFICIAL_BOTTLES, OFFICIAL_API
    else:
        bottle, api = url, url + '/api'
    # Explicit values also override an inherited mirror. No Git remotes or RC
    # files are changed, and no code from the remote endpoint is executed.
    return ('# Generated by mactools; source this file in bash/zsh.\n'
            'unset HOMEBREW_ARTIFACT_DOMAIN HOMEBREW_ARTIFACT_DOMAIN_NO_FALLBACK\n'
            f'export HOMEBREW_BOTTLE_DOMAIN={shlex.quote(bottle)}\n'
            f'export HOMEBREW_API_DOMAIN={shlex.quote(api)}\n').encode()


def safe_file(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f'Refusing non-regular file: {path}')


def validate_fragment(path):
    result = run(['/bin/sh', '-n', str(path)], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise ValueError('Mirror shell fragment failed syntax validation')


def transaction(args, operation):
    """Lock across reading old bytes, writing manifest, applying and validation."""
    target = Path(args.config_dir).expanduser().absolute() / 'mirrors.sh'
    root = Path(args.state_dir).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / 'mirror.lock'
    safe_file(lock)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        safe_file(target)
        return operation(target, root / 'mirror-backup.json')


def apply(args, url):
    payload = render(url)
    if args.dry_run:
        return {'action': 'preview', 'fragment': payload.decode(), 'path': str(Path(args.config_dir) / 'mirrors.sh')}
    if not confirm(text('写入 mactools 镜像片段并备份当前配置？', 'Write the mactools mirror fragment and back up current config?'), args.yes):
        return {'action': 'cancelled'}

    def commit(target, manifest):
        safe_file(manifest)
        original = target.read_bytes() if target.exists() else None
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
        old_manifest = manifest.read_bytes() if manifest.exists() else None
        backup = {'version': 1, 'path': str(target), 'original': None if original is None else base64.b64encode(original).decode(), 'original_sha256': None if original is None else digest(original), 'mode': mode, 'applied_sha256': digest(payload)}
        # Write recoverable backup before changing the fragment.
        save_json(manifest, backup)
        try:
            atomic_write(target, payload)
            validate_fragment(target)
        except BaseException:
            if original is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write(target, original, mode)
            if old_manifest is None:
                manifest.unlink(missing_ok=True)
            else:
                atomic_write(manifest, old_manifest)
            raise
        return {'action': 'applied', 'url': url, 'path': str(target), 'backup': str(manifest), 'source': 'source ' + shlex.quote(str(target))}

    return transaction(args, commit)


def restore(args):
    # Dry runs must not create even a state directory/lock.
    def recover(target, manifest):
        safe_file(target)
        safe_file(manifest)
        data = json.loads(manifest.read_bytes())
        required = {'version', 'path', 'original', 'original_sha256', 'mode', 'applied_sha256'}
        if not isinstance(data, dict) or not required.issubset(data):
            raise ValueError('Invalid mirror backup manifest schema')
        if data['original'] is not None and not isinstance(data['original'], str):
            raise ValueError('Invalid mirror backup original data')
        if not isinstance(data['applied_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', data['applied_sha256']):
            raise ValueError('Invalid mirror backup applied checksum')
        if data.get('version') != 1 or data.get('path') != str(target):
            raise ValueError('Backup belongs to a different config path')
        original = None if data['original'] is None else base64.b64decode(data['original'], validate=True)
        if (None if original is None else digest(original)) != data['original_sha256']:
            raise ValueError('Backup checksum mismatch')
        current = target.read_bytes() if target.exists() else None
        if not args.force and (current is None or digest(current) != data['applied_sha256']):
            raise ValueError('Mirror fragment changed since apply; refusing restore (review it, then use --force)')
        mode = data['mode']
        if not isinstance(mode, int) or mode & ~0o777:
            raise ValueError('Invalid backup permissions')
        if args.dry_run:
            return {'action': 'restore-preview', 'path': str(target), 'remove_fragment': original is None}
        if not confirm(text('恢复上次 mactools 镜像配置？', 'Restore the previous mactools mirror configuration?'), args.yes):
            return {'action': 'cancelled'}
        if original is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write(target, original, mode)
        manifest.unlink()
        return {'action': 'restored', 'path': str(target), 'source': ('unset HOMEBREW_BOTTLE_DOMAIN HOMEBREW_API_DOMAIN' if original is None else 'source ' + shlex.quote(str(target)))}

    if args.dry_run:
        return recover(Path(args.config_dir).expanduser().absolute() / 'mirrors.sh', Path(args.state_dir).expanduser().absolute() / 'mirror-backup.json')
    return transaction(args, recover)


def main(argv=None):
    parser = argparse.ArgumentParser(description=text('Homebrew 镜像测速、自动选择与精确恢复', 'Test, select and restore Homebrew bottle mirrors'))
    parser.add_argument('action', nargs='?', choices=('test', 'auto', 'restore'), default=None)
    parser.add_argument('--mirror', action='append', help='official / tuna / ustc / bottle base URL; repeatable')
    parser.add_argument('--timeout', type=positive, default=10)
    parser.add_argument('--limit', type=bounded_count, default=8, help='maximum candidate mirrors (1-32)')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--yes', '-y', action='store_true')
    parser.add_argument('--force', action='store_true', help='restore even when the managed fragment was edited')
    parser.add_argument('--config-dir', default=str(config_dir()))
    parser.add_argument('--state-dir', default=str(state_dir()))
    args = parser.parse_args(argv)
    if args.action is None:
        args.action = 'test' if args.json else 'auto'
    if args.action == 'restore':
        output = restore(args)
        code = 0
    else:
        results = benchmark(args)
        output = {'results': results}
        code = 0 if any(row['ok'] for row in results) else 1
        if args.action == 'auto':
            if code:
                raise ValueError('No verified mirrors available; configuration unchanged')
            # API schema validation prevents applying a bottle-only or broken base.
            winner = results[0]['url']
            api = OFFICIAL_API if winner == OFFICIAL_BOTTLES else winner + '/api'
            metadata = json.loads(fetch(api + '/formula/hello.json', timeout=args.timeout, limit=1024 * 1024))
            try:
                files = metadata['bottle']['stable']['files']
                matching_hash = any(entry.get('sha256') == results[0]['sha256'] for entry in files.values())
                valid = metadata.get('name') == 'hello' and matching_hash
            except (KeyError, TypeError, AttributeError):
                valid = False
            if not valid:
                raise ValueError('Selected mirror API validation failed; configuration unchanged')
            output.update(apply(args, winner))
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for row in output.get('results', []):
            print(f"{'OK' if row['ok'] else 'FAIL'} {row['name']} " + (f"{row['bytes_per_second'] / 1024:.1f} KiB/s ({row['bytes']} bytes, SHA-256 verified)" if row['ok'] else row['error']))
        if 'action' in output:
            print(output['action'] + ': ' + output.get('path', ''))
        if 'fragment' in output:
            print(output['fragment'], end='')
        if 'source' in output:
            print(text('在当前 bash/zsh 中执行以生效：', 'Run in your current bash/zsh to activate:') + '\n' + output['source'])
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
