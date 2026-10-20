"""Extended Mole scopes use disposable homes; admin and owner commands are fake."""
import contextlib
import io
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import mactools_mole as mole


@unittest.skipUnless(sys.platform == 'darwin', 'Mole requires BSD/macOS tools')
class ExtendedEngine(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ma-extended-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.fakebin = self.home / 'bin'
        self.fakebin.mkdir()
        self.engine = self.root / 'engine'
        shutil.copytree(ROOT / 'third_party/mole', self.engine)
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        XDG_CACHE_HOME=str(self.home / '.cache'),
                        NO_COLOR='1', MOLE_TEST_NO_AUTH='1',
                        PATH=str(self.fakebin) + os.pathsep + os.environ['PATH'])
        self.output = ''
        # Any accidental privilege acquisition fails and leaves evidence.
        self.fake('sudo', 'printf "%s\\n" "$*" >> "$HOME/sudo-called"; exit 91')

    def file(self, name, content=b'x' * 4096, days=0):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if days:
            old = time.time() - days * 86400
            os.utime(path, (old, old))
        return path

    def fake(self, name, body):
        path = self.fakebin / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)
        return path

    def launch(self, argv, env, timeout, interactive=False):
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        self.output += result.stdout + result.stderr
        return result.returncode

    @contextlib.contextmanager
    def plan(self, name):
        def unpack(destination):
            shutil.copytree(self.engine, destination)
        with patch.dict(os.environ, self.env), patch.object(mole, 'run_engine', side_effect=self.launch), patch.object(mole, 'unpack_bundle', side_effect=unpack):
            plan = mole.MolePlan(name, 40)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    yield plan
            finally:
                plan.close()

    def test_trash_and_recent_items_are_explicit_separate_scopes(self):
        trash = self.file('.Trash/recoverable.txt')
        recent = self.file('Library/Preferences/com.apple.recentitems.plist')
        original = self.file('Documents/original.txt')
        with self.plan('recent-items') as plan:
            plan.preview()
            self.assertTrue(recent.exists())
            plan.apply(30)
        # The pinned upstream calls this scope but independently protects
        # macOS preferences and shared-file-list databases at its final sink.
        self.assertTrue(recent.exists(), self.output)
        self.assertTrue(trash.exists())
        with self.plan('trash') as plan:
            plan.preview()
            fresh = self.file('.Trash/new-after-preview.txt')
            plan.apply(30)
        self.assertFalse(trash.exists(), self.output)
        self.assertTrue(fresh.exists(), self.output)
        self.assertTrue(original.exists())
        self.assertFalse((self.home / 'sudo-called').exists())

    def test_saved_states_use_upstream_age_guard(self):
        old = self.file('Library/Saved Application State/com.example.old.savedState/state')
        young = self.file('Library/Saved Application State/com.example.new.savedState/state')
        timestamp = time.time() - 45 * 86400
        os.utime(old.parent, (timestamp, timestamp))
        with self.plan('saved-state') as plan:
            plan.preview()
            self.assertTrue(old.exists())
            plan.apply(30)
        self.assertFalse(old.exists(), self.output)
        self.assertTrue(young.exists(), self.output)

    def test_finder_metadata_honors_upstream_mandatory_protection(self):
        ds = self.file('Documents/.DS_Store')
        document = self.file('Documents/keep.txt')
        with self.plan('finder-metadata') as plan:
            plan.preview()
            fresh = self.file('Desktop/.DS_Store')
            plan.apply(30)
        self.assertTrue(ds.exists(), self.output)
        self.assertIn('Finder metadata is protected', self.output)
        self.assertTrue(document.exists())
        self.assertTrue(fresh.exists())

    def test_mail_downloads_respect_age_and_mail_activity(self):
        self.fake('pgrep', 'exit 1')
        old = self.file('Library/Mail Downloads/old.pdf', b'x' * 6 * 1024 * 1024, days=45)
        new = self.file('Library/Mail Downloads/new.pdf', b'x' * 6 * 1024 * 1024)
        with self.plan('mail-downloads') as plan:
            plan.preview()
            self.assertTrue(old.exists())
            plan.apply(30)
        self.assertFalse(old.exists(), self.output)
        self.assertTrue(new.exists())

    def test_dedicated_actions_keep_whitelist(self):
        trash = self.file('.Trash/keep')
        self.file('.config/mactools/mole/whitelist', (str(trash) + '\n').encode())
        with self.plan('trash') as plan:
            plan.preview()
            plan.apply(30)
        self.assertTrue(trash.exists())

    def test_review_scopes_do_not_acquire_admin_or_delete(self):
        marker = self.file('Documents/keep')
        self.fake('tmutil', 'printf "called %s\\n" "$*" >> "$HOME/tmutil-called"; exit 0')
        for name in ('local-snapshots', 'system-updates', 'large-files', 'disk-verify'):
            with self.subTest(name=name), self.plan(name) as plan:
                plan.preview()
                plan.apply(30)
        self.assertTrue(marker.exists())
        self.assertIn('Review only', self.output)
        self.assertFalse((self.home / 'sudo-called').exists())
        calls = self.home / 'tmutil-called'
        if calls.exists():
            self.assertNotIn('delete', calls.read_text())

    def test_applied_plan_is_one_shot(self):
        self.file('.Trash/once')
        with self.plan('trash') as plan:
            plan.preview()
            plan.apply(30)
            with self.assertRaisesRegex(ValueError, 'Preview'):
                plan.apply(30)

    def test_noop_periodic_never_acquires_admin(self):
        self.fake('periodic', 'printf "unexpected" >> "$HOME/periodic-called"')
        daily = self.file('daily.out')
        self.env['MOLE_PERIODIC_LOG'] = str(daily)
        with self.plan('periodic-maintenance') as plan:
            plan.preview()
            self.assertEqual(plan.ledger.read_bytes(), b'')
            plan.apply(30)
        self.assertFalse((self.home / 'sudo-called').exists())
        self.assertFalse((self.home / 'periodic-called').exists())
    def test_sudo_preview_uses_unprivileged_probes_and_apply_is_identity_bound(self):
        old = self.file('Library/Caches/fixture/old.cache', days=45)
        # Exercise the real bridge sinks with exact temporary fixture targets.
        bridge = self.engine / 'mactools-bridge.sh'
        script = bridge.read_text().replace('system-cache) clean_deep_system ;;',
            'system-cache) safe_sudo_remove "$HOME/Library/Caches/fixture/old.cache" ;;')
        bridge.write_text(script)
        with self.plan('system-cache') as plan:
            plan.preview()
            self.assertTrue(old.exists())
            self.assertFalse((self.home / 'sudo-called').exists())
            replacement = self.file('Library/Caches/fixture/new.cache')
            os.replace(replacement, old)
            with self.assertRaises(ValueError):
                plan.apply(30)
            self.assertFalse(plan.reviewed)
        self.assertTrue(old.exists())
        self.assertFalse((self.home / 'sudo-called').exists())

    def test_every_new_scope_previews_in_empty_home(self):
        names = ('incomplete-downloads', 'old-app-versions', 'orphan-app-data',
                 'orphan-services', 'system-cache', 'group-cache', 'silicon-cache',
                 'device-firmware', 'failed-backups', 'mobile-cache', 'toolbox-cache',
                 'nix-garbage', 'periodic-maintenance')
        for name in names:
            with self.subTest(name=name), self.plan(name) as plan:
                plan.preview()
        self.assertFalse((self.home / 'sudo-called').exists())

    def test_admin_sink_only_deletes_reviewed_identity(self):
        outside = self.root / 'system-cache'
        outside.mkdir()
        old = outside / 'old.cache'
        old.write_bytes(b'x' * 4096)
        bridge = self.engine / 'mactools-bridge.sh'
        script = bridge.read_text().replace('system-cache:/Library/*|', f'system-cache:{outside}/*|system-cache:/Library/*|')
        script = script.replace('system-cache) clean_deep_system ;;', '''system-cache)
            _mole_privileged_path_has_mutable_ancestor() { return 1; }
            for target in "$MA_FIXTURE_OUTSIDE"/*; do
                safe_sudo_remove "$target" || true
            done ;;''')
        bridge.write_text(script)
        self.env.pop('MOLE_TEST_NO_AUTH')
        self.env['MA_FIXTURE_OUTSIDE'] = str(outside)
        self.fake('sudo', '''printf '%s\\n' "$*" >> "$HOME/sudo-called"
[[ "$*" == '-n -v' ]] && exit 0
[[ "$1" == -n ]] || exit 90
shift
exec "$@"''')
        with self.plan('system-cache') as plan:
            plan.preview()
            self.assertFalse((self.home / 'sudo-called').exists())
            fresh = outside / 'late.cache'
            fresh.write_bytes(b'y' * 4096)
            plan.apply(30)
        self.assertFalse(old.exists(), self.output)
        self.assertTrue(fresh.exists(), self.output)
        self.assertIn('-n rm -rf', (self.home / 'sudo-called').read_text())

    def test_simulator_batch_is_split_into_only_reviewed_uuids(self):
        first = 'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA'
        late = 'BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB'
        self.file('Library/Developer/CoreSimulator/Devices/' + first + '/data')
        bridge = self.engine / 'mactools-bridge.sh'
        script = bridge.read_text().replace('mobile-cache) clean_dev_mobile', '''mobile-cache)
            ma_upstream__run_simctl() {
                if [[ "$2" == list ]]; then cat "$HOME/sim-list";
                else printf '%s\\n' "$*" >> "$HOME/sim-calls"; fi
            }
            if [[ "$mode" == preview ]]; then
                ma_record P "$HOME/Library/Developer/CoreSimulator/Devices/AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA" "$HOME/Library/Developer/CoreSimulator/Devices/AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
            else _run_simctl 10 delete unavailable; fi
            return $? ;;
        unused-mobile-cache) clean_dev_mobile''')
        bridge.write_text(script)
        with self.plan('mobile-cache') as plan:
            plan.preview()
            self.file('Library/Developer/CoreSimulator/Devices/' + late + '/data')
            self.file('sim-list', f'device ({first}) (Shutdown) (unavailable, runtime missing)\ndevice ({late}) (Shutdown) (unavailable, runtime missing)\n'.encode())
            plan.apply(30)
        calls = (self.home / 'sim-calls').read_text()
        self.assertIn('delete ' + first, calls)
        self.assertNotIn(late, calls)
        self.assertNotIn('delete unavailable', calls)

    def test_nix_only_passes_reviewed_dead_paths_to_native_delete(self):
        store = self.root / 'nix/store'
        old = store / ('a' * 32 + '-old')
        old.mkdir(parents=True)
        late = store / ('b' * 32 + '-late')
        bridge = self.engine / 'mactools-bridge.sh'
        bridge.write_text(bridge.read_text().replace('/nix/store/', str(store) + '/'))
        self.fake('nix-store', '''printf '%s\\n' "$*" >> "$HOME/nix-calls"
if [[ "$*" == '--gc --print-dead' ]]; then cat "$HOME/nix-dead";
elif [[ "$1" == --delete ]]; then /bin/rm -rf -- "$2";
else exit 90; fi''')
        self.file('nix-dead', (str(old) + '\n').encode())
        with self.plan('nix-garbage') as plan:
            plan.preview()
            self.assertTrue(old.exists())
            late.mkdir()
            self.file('nix-dead', (str(old) + '\n' + str(late) + '\n').encode())
            plan.apply(30)
        self.assertFalse(old.exists(), self.output)
        self.assertTrue(late.exists(), self.output)
        calls = (self.home / 'nix-calls').read_text().splitlines()
        self.assertNotIn('--gc', calls)
        self.assertIn('--delete ' + str(old), calls)
        self.assertNotIn('--delete ' + str(late), calls)


@unittest.skipUnless(hasattr(os, 'tcsetpgrp'), 'requires POSIX controlling terminals')
class EngineTerminal(unittest.TestCase):
    def test_child_exit_before_foreground_transfer_returns_exit_status(self):
        class Completed:
            pid = 2147483647
            returncode = 7
            def poll(self):
                return self.returncode
            def wait(self, timeout=None):
                return self.returncode
        with patch.object(mole.subprocess, 'Popen', return_value=Completed()), patch.object(mole.os, 'open', return_value=91), patch.object(mole.os, 'close'), patch.object(mole.os, 'tcgetpgrp', return_value=os.getpgrp()), patch.object(mole.os, 'tcsetpgrp', side_effect=ProcessLookupError):
            self.assertEqual(mole.run_engine(['fixture'], {}, 1, interactive=True), 7)

    def test_confirmed_owner_can_read_tty_and_foreground_is_restored(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                pid, terminal = pty.fork()
                if pid == 0:
                    try:
                        if redirected:
                            null = os.open(os.devnull, os.O_RDONLY)
                            os.dup2(null, 0)
                            os.close(null)
                        command = ['bash', '-c', 'printf "FAKE_ADMIN_READY\\n" > /dev/tty; read -r answer < /dev/tty; test "$answer" = fixture-secret']
                        status = mole.run_engine(command, dict(os.environ), 5, interactive=True)
                        for _ in range(5):
                            if mole.run_engine(['/usr/bin/true'], dict(os.environ), 1, interactive=True):
                                os._exit(4)
                        try:
                            mole.run_engine(['/bin/sleep', '10'], dict(os.environ), 0.05, interactive=True)
                            os._exit(5)
                        except subprocess.TimeoutExpired:
                            pass
                        tty = os.open('/dev/tty', os.O_RDWR)
                        restored = os.tcgetpgrp(tty) == os.getpgrp()
                        os.close(tty)
                        os._exit(0 if status == 0 and restored else 2)
                    except BaseException:
                        os._exit(3)
                captured = b''
                sent = False
                status = None
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if select.select([terminal], [], [], 0.1)[0]:
                            try:
                                captured += os.read(terminal, 8192)
                            except OSError:
                                pass
                        if b'FAKE_ADMIN_READY' in captured and not sent:
                            os.write(terminal, b'fixture-secret\n')
                            sent = True
                        result, value = os.waitpid(pid, os.WNOHANG)
                        if result:
                            status = value
                            break
                    self.assertTrue(sent, captured)
                    self.assertIsNotNone(status, captured)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, captured)
                finally:
                    if status is None:
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                    os.close(terminal)


if __name__ == '__main__':
    unittest.main()
