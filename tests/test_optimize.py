"""Optimization runs only against disposable homes and fake owner commands."""
import contextlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import mactools_optimize as optimize


class Interface(unittest.TestCase):
    def test_list_and_details_do_not_start_engine(self):
        with patch.object(optimize, 'unpack_bundle', side_effect=AssertionError('engine started')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(optimize.main(['--list']), 0)
            self.assertEqual(optimize.main(['--detail', 'notification-cleanup']), 0)
        self.assertEqual(len(optimize.optimize_info()), 19)
        self.assertIn('NotificationCenter', output.getvalue())

    def test_yes_requires_explicit_tasks(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            optimize.main(['--yes'])

    def test_nonterminal_default_only_previews(self):
        plan = unittest.mock.MagicMock()
        with patch.object(optimize, 'OptimizePlan', return_value=plan), patch.object(sys.stdin, 'isatty', return_value=False):
            self.assertEqual(optimize.main([]), 0)
        plan.preview.assert_called_once()
        plan.apply.assert_not_called()

    def test_selector_uses_aligned_static_details_and_hidden_select_all(self):
        result = subprocess.CompletedProcess([], 0, '\n\ncache-refresh\tignored\n')
        with patch.object(optimize.shutil, 'which', return_value='/fake/fzf'), patch.object(optimize.subprocess, 'run', return_value=result) as launch:
            self.assertEqual(optimize.select_tasks(), ['cache-refresh'])
        argv = launch.call_args.args[0]
        self.assertIn('--with-nth=5..', argv)
        self.assertIn('--nth=2', argv)
        self.assertIn('--info=hidden', argv)
        self.assertTrue(any('ctrl-a:select-all' in value for value in argv))
        header = next(value for value in argv if value.startswith('--header='))
        self.assertNotIn('Ctrl+A', header)
        preview = next(value for value in argv if value.startswith('--preview='))
        self.assertIn('--detail', preview)


@unittest.skipUnless(sys.platform == 'darwin', 'Mole uses macOS tools')
class Engine(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ma-optimize-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.fakebin = self.home / 'bin'
        self.fakebin.mkdir()
        self.engine = self.root / 'source'
        shutil.copytree(ROOT / 'third_party/mole', self.engine)
        self.env = dict(os.environ, HOME=str(self.home), NO_COLOR='1',
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        PATH=str(self.fakebin) + os.pathsep + os.environ['PATH'])
        self.output = ''
        self.fake('sudo', 'printf "%s\\n" "$*" >> "$HOME/unexpected-sudo"; exit 91')
        self.fake('pgrep', 'exit 1')
        self.fake('qlmanage', 'printf "%s\\n" "$*" >> "$HOME/qlmanage-calls"')
        self.fake('killall', 'printf "%s\\n" "$*" >> "$HOME/killall-calls"')
        self.fake('defaults', '[[ "$1" == read ]] && exit 1; printf "%s\\n" "$*" >> "$HOME/defaults-writes"')
        self.fake('mdutil', 'echo "Indexing disabled."')
        self.fake('scutil', 'exit 0')
        self.fake('route', '[[ "$*" == "-n get default" ]] && echo "interface: en0"; exit 0')
        self.fake('dscacheutil', 'exit 0')
        self.fake('osascript', 'cat > /dev/null; exit 0')
        self.fake('sfltool', 'exit 0')

    def fake(self, name, body):
        path = self.fakebin / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)
        return path

    def file(self, name, data=b'x'):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    @contextlib.contextmanager
    def plan(self, names):
        def unpack(destination):
            shutil.copytree(self.engine, destination)
        def launch(argv, env, timeout, interactive=False):
            result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=timeout)
            self.output += result.stdout + result.stderr
            return result.returncode
        with patch.dict(os.environ, self.env), patch.object(optimize, 'unpack_bundle', side_effect=unpack), patch.object(optimize, 'run_engine', side_effect=launch):
            plan = optimize.OptimizePlan(names, 30)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    yield plan
            finally:
                plan.close()

    def test_all_catalog_tasks_preview_without_mutating_services_preferences_or_data(self):
        cache = self.file('Library/Caches/com.apple.iconservices/cache')
        saved = self.file('Library/Saved Application State/com.example.app.savedState/state')
        old = time.time() - 45 * 86400
        os.utime(saved.parent, (old, old))
        with self.plan(list(optimize.optimize_info())) as plan:
            plan.preview()
        self.assertTrue(cache.exists(), self.output)
        self.assertTrue(saved.exists(), self.output)
        for name in ('unexpected-sudo', 'qlmanage-calls', 'killall-calls', 'defaults-writes'):
            self.assertFalse((self.home / name).exists(), name + '\n' + self.output)
        self.assertIn('Task outcomes:', self.output)

    def test_only_selected_task_runs_and_requires_preview_once(self):
        with self.plan(['prevent-network-dsstore']) as plan:
            with self.assertRaisesRegex(ValueError, 'Preview'):
                plan.apply()
            plan.preview()
            self.assertFalse((self.home / 'defaults-writes').exists())
            plan.apply()
            with self.assertRaisesRegex(ValueError, 'Preview'):
                plan.apply()
        writes = (self.home / 'defaults-writes').read_text()
        self.assertIn('DSDontWriteNetworkStores', writes)
        self.assertIn('DSDontWriteUSBStores', writes)
        self.assertFalse((self.home / 'qlmanage-calls').exists())
        self.assertFalse((self.home / 'unexpected-sudo').exists())

    def test_saved_state_identity_binding_rejects_late_added_target(self):
        old = self.file('Library/Saved Application State/com.example.old.savedState/state')
        timestamp = time.time() - 45 * 86400
        os.utime(old.parent, (timestamp, timestamp))
        with self.plan(['saved-state-cleanup']) as plan:
            plan.preview()
            late = self.file('Library/Saved Application State/com.example.late.savedState/state')
            os.utime(late.parent, (timestamp, timestamp))
            # Upstream reports the unreviewed late target as failed; known
            # reviewed targets still receive their final identity checks.
            with self.assertRaises(ValueError):
                plan.apply()
        self.assertFalse(old.exists(), self.output)
        self.assertTrue(late.exists(), self.output)

    def test_database_preview_is_readonly_and_replaced_database_not_modified(self):
        database = self.file('Library/Safari/History.db', b'SQLite format 3\0' + b'x' * 4096)
        self.fake('file', 'echo "SQLite 3.x database"')
        self.fake('sqlite3', '''printf '%s\\n' "$*" >> "$HOME/sql-calls"
[[ "$1" == -readonly ]] && shift
case "$2" in
 'PRAGMA page_count; PRAGMA freelist_count;') printf '100\\n20\\n' ;;
 'PRAGMA integrity_check;') echo ok ;;
 'VACUUM;') printf vacuum >> "$1" ;;
 *) exit 90 ;;
esac''')
        with self.plan(['sqlite-vacuum']) as plan:
            plan.preview()
            self.assertTrue(all(line.startswith('-readonly ') for line in (self.home / 'sql-calls').read_text().splitlines()))
            replacement = self.file('replacement', b'SQLite format 3\0replacement')
            os.replace(replacement, database)
            with self.assertRaises(ValueError):
                plan.apply()
        self.assertNotIn(b'vacuum', database.read_bytes())

    def test_lazy_admin_is_selected_and_noop_or_vpn_never_authenticates(self):
        self.env['MOLE_ASSUME_VPN_ACTIVE'] = '1'
        with self.plan(['network-optimization', 'network-stack-optimize']) as plan:
            plan.preview()
            plan.apply()
        self.assertFalse((self.home / 'unexpected-sudo').exists())
        self.env['MOLE_ASSUME_VPN_ACTIVE'] = '0'
        self.fake('sudo', '''printf '%s\\n' "$*" >> "$HOME/sudo-calls"
[[ "$*" == '-n -v' ]] && exit 0
[[ "$*" == '-n dscacheutil -flushcache' || "$*" == '-n killall -HUP mDNSResponder' ]] && exit 0
exit 90''')
        with self.plan(['network-optimization']) as plan:
            plan.preview()
            self.assertFalse((self.home / 'sudo-calls').exists())
            plan.apply()
        calls = (self.home / 'sudo-calls').read_text()
        self.assertIn('-n -v', calls)
        self.assertIn('-n dscacheutil -flushcache', calls)
        self.assertIn('-n killall -HUP mDNSResponder', calls)

    def test_preview_sqlite_wal_probe_never_creates_original_sidecar(self):
        database = self.file('Library/Safari/History.db', b'')
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE fixture(value)')
        connection.executemany('INSERT INTO fixture VALUES (?)', [('x' * 2000,)] * 50)
        connection.commit()
        connection.execute('DELETE FROM fixture')
        connection.commit()
        shm = Path(str(database) + '-shm')
        shm.unlink()
        before = {p: p.read_bytes() for p in (database, Path(str(database) + '-wal'))}
        with self.plan(['sqlite-vacuum']) as plan:
            plan.preview()
        self.assertFalse(shm.exists(), self.output)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_login_audit_preview_never_executes_external_sudo_fallback(self):
        # Exercise upstream's wrapped fallback boundary without requiring a
        # real app inventory or changing login items on the host.
        bridge = self.engine / 'mactools-optimize.sh'
        bridge.write_text(bridge.read_text().replace('execute_optimization "$MA_ACTION"', '''
        if [[ "$MA_ACTION" == login_items_audit ]]; then
            if run_with_timeout 3 sudo -n true; then exit 92; fi
            optimize_task_start
            optimize_task_result "$MOLE_OPTIMIZE_OUTCOME_UNAVAILABLE"
            optimize_task_finish "$MA_ACTION"
        else execute_optimization "$MA_ACTION"; fi'''))
        with self.plan(['login-items-audit']) as plan:
            plan.preview()
        self.assertFalse((self.home / 'unexpected-sudo').exists())

    def test_whitelisted_task_and_disk_verify_remain_disabled(self):
        self.file('.config/mactools/mole/whitelist_optimize', b'prevent_network_dsstore\n')
        with self.plan(['prevent-network-dsstore', 'disk-verify']) as plan:
            plan.preview()
            plan.apply()
        self.assertFalse((self.home / 'defaults-writes').exists())
        self.assertIn('skipped', self.output.lower())


if __name__ == '__main__':
    unittest.main()
