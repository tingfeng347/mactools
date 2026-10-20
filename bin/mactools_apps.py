"""Native macOS apps, inspected and removed by the vendored Mole engine.

The list is cheap: no leftovers, Spotlight sizing or Homebrew commands. Each
uninstall builds a new identity-bound plan and keeps personal data by default.
"""
import hashlib
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from mactools_mole import run_engine, unpack_bundle
from mactools_runtime import text

APP_ID = re.compile(r'app-[0-9a-f]{64}\Z')


def _environment():
    env = dict(os.environ)
    # Do not inherit test bypasses, shell initialization hooks, or Mole state.
    for key in list(env):
        if (key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS') or
                key.startswith(('BASH_FUNC_', 'MOLE_'))):
            env.pop(key, None)
    env.update(HOME=str(Path.home().resolve()), HOMEBREW_NO_AUTO_UPDATE='1',
               HOMEBREW_NO_ENV_HINTS='1', HOMEBREW_NO_COLOR='1',
               MACTOOLS_APPS_PYTHON=sys.executable)
    return env


def _safe_text(value):
    return ''.join(char for char in str(value) if char.isprintable())


@dataclass(frozen=True)
class App:
    id: str
    name: str
    version: str
    path: str
    bundle_id: str
    cask_name: str = ''


def _app(path, bundle_id='', name=''):
    absolute = os.path.abspath(path)
    if '|' in absolute or any(ord(char) < 32 or ord(char) == 127 for char in absolute):
        raise ValueError('Application path contains unsupported control characters or |.')
    location = Path(absolute)
    if location.is_symlink() or not location.is_dir() or location.suffix.lower() != '.app':
        raise ValueError('Application is missing, changed, or is a symbolic link.')
    metadata = {}
    for plist in (location / 'Contents/Info.plist', location / 'Info.plist'):
        try:
            with plist.open('rb') as stream:
                metadata = plistlib.load(stream)
            if isinstance(metadata, dict):
                break
            metadata = {}
        except (OSError, plistlib.InvalidFileException, ValueError, TypeError):
            metadata = {}
    version = metadata.get('CFBundleShortVersionString') or metadata.get('CFBundleVersion') or ''
    bundle_id = metadata.get('CFBundleIdentifier') or bundle_id or 'unknown'
    return App('app-' + hashlib.sha256(os.fsencode(absolute)).hexdigest(),
               _safe_text(name or location.stem), _safe_text(version), absolute,
               _safe_text(bundle_id))


def installed_apps(timeout=15):
    """List eligible apps using Mole's discovery policy, without scanning data."""
    with tempfile.TemporaryDirectory(prefix='mactools-app-list-') as temporary:
        folder = Path(temporary)
        unpack_bundle(folder / 'engine')
        output = folder / 'apps.bin'
        output.touch(mode=0o600)
        status = run_engine(['/bin/bash', str(folder / 'engine/mactools-apps.sh'),
                             'list', str(output)], _environment(), timeout)
        if status:
            raise ValueError(text('无法读取本机应用列表。', 'Could not read installed applications.'))
        records = output.read_bytes().split(b'\0')
        if records[-1:] == [b'']:
            records.pop()
        if len(records) % 3:
            raise ValueError('Incomplete application inventory.')
        found = {}
        for index in range(0, len(records), 3):
            path, bundle, name = (os.fsdecode(item) for item in records[index:index + 3])
            try:
                app = _app(path, bundle, name)
            except ValueError:
                continue  # Apps may disappear while an inventory is being read.
            found[app.id] = app
        return sorted(found.values(), key=lambda app: (app.name.casefold(), app.path))


def resolve_apps(ids, timeout=15):
    """Resolve only emitted IDs; no arbitrary path from a search query is used."""
    ids = list(dict.fromkeys(ids))
    if not ids or any(not APP_ID.fullmatch(value) for value in ids):
        raise ValueError('Invalid application selection.')
    found = {app.id: app for app in installed_apps(timeout)}
    if any(value not in found for value in ids):
        raise ValueError(text('所选应用已变化，请刷新后重新选择。',
                              'Selected application changed; refresh and select it again.'))
    return [found[value] for value in ids]


def _brew_capture(brew, arguments, timeout):
    process = subprocess.Popen([brew] + list(arguments), env=_environment(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    previous_term = None
    if threading.current_thread() is threading.main_thread():
        previous_term = signal.getsignal(signal.SIGTERM)

        def terminate(signum, frame):
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, terminate)
    try:
        try:
            output, error = process.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
        if process.returncode:
            raise ValueError(text('无法验证 Homebrew 应用来源：', 'Cannot verify Homebrew app ownership: ') + brew)
        return output
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)


def _brew_candidates():
    candidates = [shutil.which('brew'), '/opt/homebrew/bin/brew', '/usr/local/bin/brew']
    return sorted({str(Path(path).resolve()) for path in candidates if path and os.access(path, os.X_OK)})


def _cask_owners(selected, timeout):
    """Bind each installed artifact to its owning brew prefix and full token.

    Mole's upstream detector scans both Caskrooms but invokes PATH brew. That
    is insufficient on dual-prefix Macs. Ask every installed prefix and refuse
    ambiguous ownership rather than uninstalling a same-name cask elsewhere.
    """
    brews = _brew_candidates()
    owners = {}
    by_name = {Path(app.path).name.casefold() for app in selected}
    for brew in brews:
        payload = json.loads(_brew_capture(brew, ['info', '--json=v2', '--installed', '--cask'], timeout))
        records = payload.get('casks') if isinstance(payload, dict) else None
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError('Invalid installed Homebrew cask inventory.')
        for record in records:
            token = record.get('full_token') or record.get('token')
            if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9@+_.-]*(?:/[A-Za-z0-9][A-Za-z0-9@+_.-]*){0,2}', token):
                raise ValueError('Invalid installed cask identity.')
            artifact_names = set()
            artifacts = record.get('artifacts', [])
            if not isinstance(artifacts, list):
                raise ValueError('Invalid Homebrew cask artifacts.')
            for artifact in artifacts:
                if not isinstance(artifact, dict) or not isinstance(artifact.get('app'), list):
                    continue
                for item in artifact['app']:
                    value = item.get('target') if isinstance(item, dict) else item
                    if isinstance(value, str):
                        artifact_names.add(Path(value).name.casefold())
            if not artifact_names.intersection(by_name):
                continue
            # `list <cask>` renders installed artifact targets, including the
            # saved appdir and target rename. JSON app basenames alone cannot
            # prove ownership and are only a cheap prefilter.
            installed = re.sub(r'\x1b\[[0-9;]*m', '',
                               _brew_capture(brew, ['list', '--cask', token], timeout)).splitlines()
            for app in selected:
                displays = (app.path, '~/' + os.path.relpath(app.path, str(Path.home())))
                if not any(line.startswith(display + ' (') and line.endswith(')')
                           for line in installed for display in displays):
                    continue
                identity = (brew, token)
                if app.path in owners and owners[app.path] != identity:
                    raise ValueError(text('应用被多个 Homebrew 来源声明，请先解决来源冲突：',
                                          'Multiple Homebrew sources claim this app; resolve ownership first: ') + app.path)
                owners[app.path] = identity
    return owners


class AppUninstallPlan:
    """A private snapshot of exact app bundles and chosen Mole leftovers."""
    def __init__(self, apps, timeout=60, with_data=False):
        self.apps = list({app.id: app for app in apps}.values())
        if not self.apps:
            raise ValueError('No applications selected.')
        for app in self.apps:
            if _app(app.path).id != app.id:
                raise ValueError('Invalid application identity.')
        self.timeout = timeout
        self.with_data = bool(with_data)
        self.reviewed = False
        self.cask_names = set()
        self.cask_identities = set()
        self._owners = {}
        self._owner_executables = {}
        self._temporary = tempfile.TemporaryDirectory(prefix='mactools-uninstall-')
        self.path = Path(self._temporary.name)
        self._unpacked = False
        for name in ('request.bin', 'details.bin', 'approved.bin', 'casks.bin', 'owners.bin'):
            (self.path / name).touch(mode=0o600)
        (self.path / 'request.bin').write_bytes(b''.join(os.fsencode(app.path) + b'\0' for app in self.apps))

    @staticmethod
    def _executable_identity(path):
        info = os.stat(path)
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def close(self):
        self._temporary.cleanup()

    def _run(self, mode, timeout=None):
        if not self._unpacked:
            unpack_bundle(self.path / 'engine')
            self._unpacked = True
        argv = ['/bin/bash', str(self.path / 'engine/mactools-apps.sh'), mode]
        if mode == 'preview':
            argv += [str(self.path / name) for name in ('request.bin', 'details.bin', 'approved.bin', 'casks.bin')]
            argv += ['1' if self.with_data else '0', str(self.path / 'owners.bin')]
        else:
            argv += [str(self.path / name) for name in ('details.bin', 'approved.bin', 'owners.bin')]
        status = run_engine(argv, _environment(), timeout or self.timeout, interactive=mode == 'apply')
        if status:
            self.reviewed = False
            raise ValueError(text(f'应用卸载{ "预览" if mode == "preview" else "执行" }未完成（状态 {status}）。',
                                  f'Application uninstall {mode} incomplete (status {status}).'))
        return status

    def preview(self):
        self.reviewed = False
        self.cask_names = set()
        self.cask_identities = set()
        self._owners = _cask_owners(self.apps, self.timeout)
        self.cask_identities = set(self._owners.values())
        self._owner_executables = {brew: self._executable_identity(brew)
                                   for brew, _ in self.cask_identities}
        (self.path / 'owners.bin').write_bytes(b''.join(
            os.fsencode(value) + b'\0' for path, owner in self._owners.items()
            for value in (path,) + owner))
        self._run('preview')
        details = (self.path / 'details.bin').read_bytes()
        if not details or not details.endswith(b'\0') or details.count(b'\0') != len(self.apps):
            raise ValueError('Incomplete application uninstall preview.')
        self.cask_names = {os.fsdecode(value) for value in (self.path / 'casks.bin').read_bytes().split(b'\0') if value}
        self.reviewed = True

    def apply(self, timeout=600):
        if not self.reviewed:
            raise ValueError('Preview the application uninstall plan before applying it.')
        # A plan is one-shot even if the process fails after doing partial work.
        try:
            if any(self._executable_identity(brew) != identity
                   for brew, identity in self._owner_executables.items()):
                raise ValueError('Homebrew executable changed after preview; select again.')
            if _cask_owners(self.apps, self.timeout) != self._owners:
                raise ValueError('Homebrew application ownership changed after preview; select again.')
            return self._run('apply', timeout)
        finally:
            self.reviewed = False


def preview_detail(app_id, timeout=15):
    apps = resolve_apps([app_id], timeout)
    app = apps[0]
    print(f'{app.name} · App (Mole)', flush=True)
    print(text('已安装：', 'Installed: ') + (app.version or '—'), flush=True)
    print(text('位置：', 'Path: ') + app.path, flush=True)
    print('Bundle ID: ' + app.bundle_id, flush=True)
    plan = AppUninstallPlan(apps, timeout)
    try:
        plan.preview()
    finally:
        plan.close()
