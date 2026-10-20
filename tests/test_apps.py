"""Native-app Mole adapter tests. All targets live in a disposable HOME.

The harness disables real Homebrew, process/service changes and administrator
access; the Trash sink only renames fixtures under that temporary HOME.
"""
import os
import json
import plistlib
import subprocess
import signal
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import mactools_apps as apps


class AppIdentity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ma-app-id-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.owner_patch = patch.object(apps, '_cask_owners', return_value={})
        self.owner_mock = self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)

    def app(self, name='测试 App', bundle='net.mactools.fixture'):
        path = self.home / 'Applications' / (name + '.app')
        (path / 'Contents').mkdir(parents=True)
        with (path / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': bundle, 'CFBundleShortVersionString': '1.2.3'}, stream)
        return path

    def test_names_and_versions_and_stable_safe_ids(self):
        path = self.app()
        app = apps._app(str(path))
        self.assertEqual(app.name, '测试 App')
        self.assertEqual(app.version, '1.2.3')
        self.assertEqual(app.bundle_id, 'net.mactools.fixture')
        self.assertRegex(app.id, r'^app-[0-9a-f]{64}$')
        self.assertEqual(app.id, apps._app(str(path)).id)

    def test_arbitrary_and_stale_ids_are_rejected(self):
        app = apps._app(str(self.app()))
        with patch.object(apps, 'installed_apps', return_value=[app]):
            self.assertEqual(apps.resolve_apps([app.id]), [app])
            for value in ('$(touch /tmp/no)', 'app-' + 'f' * 64):
                with self.assertRaises(ValueError):
                    apps.resolve_apps([value])

    def test_rejects_symlink_and_protocol_delimiters(self):
        path = self.app()
        symlink = path.with_name('Alias.app')
        symlink.symlink_to(path)
        with self.assertRaises(ValueError):
            apps._app(str(symlink))
        with self.assertRaises(ValueError):
            apps._app(str(path.with_name('bad|app.app')))

    def test_non_dictionary_plist_is_tolerated(self):
        path = self.app()
        with (path / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump(['not', 'an', 'application', 'dictionary'], stream)
        self.assertEqual(apps._app(str(path)).version, '')

    def test_environment_drops_shell_and_mole_bypasses(self):
        with patch.dict(os.environ, {'BASH_ENV': '/untrusted', 'MOLE_TEST_MODE': '1', 'MOLE_DELETE_MODE': 'permanent'}):
            env = apps._environment()
        self.assertNotIn('BASH_ENV', env)
        self.assertNotIn('MOLE_TEST_MODE', env)
        self.assertNotIn('MOLE_DELETE_MODE', env)

    def test_failed_preview_and_one_shot_apply(self):
        app = apps._app(str(self.app()))
        plan = apps.AppUninstallPlan([app], 5)
        self.addCleanup(plan.close)
        with self.assertRaisesRegex(ValueError, 'Preview'):
            plan.apply(5)
        with patch.object(plan, '_run', side_effect=ValueError('scan failed')):
            with self.assertRaises(ValueError):
                plan.preview()
        self.assertFalse(plan.reviewed)
        plan.reviewed = True
        with patch.object(plan, '_run', return_value=0):
            plan.apply(5)
        self.assertFalse(plan.reviewed)


@unittest.skipUnless(sys.platform == 'darwin' and os.geteuid() != 0, 'Mole uses macOS tools and an unprivileged account')
class Engine(AppIdentity):
    def setUp(self):
        super().setUp()
        self.output = ''
        self.env = dict(os.environ, HOME=str(self.home), NO_COLOR='1', MACTOOLS_LANG='en')
        self.env_patch = patch.dict(os.environ, self.env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.unpack_patch = patch.object(apps, 'unpack_bundle', side_effect=self.unpack)
        self.unpack_patch.start()
        self.addCleanup(self.unpack_patch.stop)
        self.run_patch = patch.object(apps, 'run_engine', side_effect=self.launch)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def unpack(self, destination):
        destination.mkdir(parents=True)
        script = destination / 'mactools-apps.sh'
        # No runtime test hook exists in production. Override only in this
        # private fixture wrapper after loading the real upstream functions.
        script.write_text('''#!/bin/bash
source "$MA_APPS_REAL_BRIDGE"
uninstall_print_app_search_dirs() { printf '%s\\n' "$HOME/Applications"; }
uninstall_live_bundle_has_other_install() { _MOLE_UNINSTALL_LIVE_SIBLING_FINGERPRINT=""; return 1; }
get_diagnostic_report_paths_for_app() { :; }
find_app_system_files() { :; }
discover_login_item_helper_bundle_ids() { :; }
stop_launch_services() { touch "$HOME/teardown"; }
unregister_app_bundle() { :; }
remove_login_item() { :; }
force_kill_app() { touch "$HOME/quit"; }
ensure_sudo_session() { echo 'NO REAL ADMIN' >&2; return 1; }
_mole_move_to_trash() {
    [[ "$1" == "$HOME/"* && ! -L "$1" ]] || return 1
    mkdir -p "$HOME/.Trash"
    /bin/mv "$1" "$HOME/.Trash/$(basename "$1").fixture"
}
_mole_move_to_trash_batch() {
    local p
    _MOLE_TRASH_BATCH_MOVED_PATHS=()
    for p in "$@"; do
        _mole_move_to_trash "$p" || return 1
        _MOLE_TRASH_BATCH_MOVED_PATHS+=("$p")
    done
}
ma_apps_main "$@"
''')

    def launch(self, argv, env, timeout, interactive=False):
        env = dict(env, MA_APPS_REAL_BRIDGE=str(ROOT / 'third_party/mole/mactools-apps.sh'),
                   MOLE_TEST_NO_AUTH='1')
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        self.output += result.stdout + result.stderr
        return result.returncode

    def file(self, relative):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
        return path

    def plan(self, path, with_data=False):
        plan = apps.AppUninstallPlan([apps._app(str(path))], 30, with_data=with_data)
        self.addCleanup(plan.close)
        return plan

    def test_inventory_is_fast_without_brew_or_residual_scan(self):
        path = self.app()
        app = apps.installed_apps(15)
        self.assertEqual(len(app), 1, self.output)
        self.assertEqual(app[0].path, str(path))
        self.assertFalse((self.home / 'quit').exists())
        self.assertFalse((self.home / 'teardown').exists())

    def test_preview_and_apply_only_reviewed_cache_default_keeps_personal_data(self):
        path = self.app()
        cache = self.file('Library/Caches/net.mactools.fixture/payload')
        preferences = self.file('Library/Preferences/net.mactools.fixture.plist')
        plan = self.plan(path)
        plan.preview()
        self.assertTrue(path.exists(), self.output)
        self.assertTrue(cache.exists(), self.output)
        self.assertFalse((self.home / 'quit').exists(), self.output)
        self.assertFalse((self.home / 'teardown').exists(), self.output)
        new = self.file('Library/Logs/net.mactools.fixture/new.log')
        plan.apply(30)
        self.assertFalse(path.exists(), self.output)
        self.assertFalse(cache.exists(), self.output)
        self.assertTrue(new.exists(), self.output)
        self.assertTrue(preferences.exists(), self.output)
        self.assertTrue((self.home / '.Trash').exists(), self.output)
        self.assertFalse((self.home / '.config/mole').exists(), self.output)

    def test_replaced_app_fails_before_teardown(self):
        path = self.app()
        plan = self.plan(path)
        plan.preview()
        path.rename(path.with_name('old.app'))
        path = self.app()
        with self.assertRaises(ValueError):
            plan.apply(30)
        self.assertTrue(path.exists(), self.output)
        self.assertFalse((self.home / 'teardown').exists(), self.output)

    def test_replaced_leftover_is_kept_and_no_app_teardown_occurs(self):
        path = self.app()
        cache = self.file('Library/Caches/net.mactools.fixture/payload')
        plan = self.plan(path)
        plan.preview()
        cache.parent.rename(cache.parent.with_name('moved'))
        cache = self.file('Library/Caches/net.mactools.fixture/fresh')
        with self.assertRaises(ValueError):
            plan.apply(30)
        self.assertTrue(path.exists(), self.output)
        self.assertTrue(cache.exists(), self.output)
        self.assertFalse((self.home / 'teardown').exists(), self.output)

    def test_new_file_inside_reviewed_directory_prevents_removal(self):
        path = self.app()
        cache = self.file('Library/Caches/net.mactools.fixture/payload')
        plan = self.plan(path)
        plan.preview()
        new = self.file('Library/Caches/net.mactools.fixture/new-data')
        with self.assertRaises(ValueError):
            plan.apply(30)
        self.assertTrue(path.exists(), self.output)
        self.assertTrue(cache.exists(), self.output)
        self.assertTrue(new.exists(), self.output)
        self.assertFalse((self.home / 'teardown').exists(), self.output)

    def test_with_data_includes_reviewed_preferences(self):
        path = self.app()
        preferences = self.file('Library/Preferences/net.mactools.fixture.plist')
        plan = self.plan(path, with_data=True)
        plan.preview()
        self.assertTrue(preferences.exists())
        plan.apply(30)
        self.assertFalse(preferences.exists(), self.output)

    def test_system_app_is_protected(self):
        path = self.app(bundle='com.apple.finder')
        self.assertEqual(apps.installed_apps(15), [], self.output)
        plan = self.plan(path)
        with self.assertRaises(ValueError):
            plan.preview()
        self.assertTrue(path.exists())

    def test_official_vendor_uninstaller_is_required(self):
        path = self.app('Fixture Security', 'com.crowdstrike.fixture')
        plan = self.plan(path)
        with self.assertRaises(ValueError):
            plan.preview()
        self.assertIn('official CrowdStrike uninstaller', self.output)
        self.assertTrue(path.exists())
        self.assertFalse((self.home / 'teardown').exists())

    def test_cask_identity_is_exported_and_admin_denial_does_not_teardown(self):
        path = self.app()
        plan = self.plan(path)
        original_unpack = self.unpack

        def cask_unpack(destination):
            original_unpack(destination)
            script = destination / 'mactools-apps.sh'
            content = script.read_text()
            script.write_text(content)

        self.owner_mock.return_value = {str(path): ('/bin/echo', 'fixture/tap/fixture-cask')}
        with patch.object(apps, 'unpack_bundle', side_effect=cask_unpack):
            plan.preview()
        self.assertEqual(plan.cask_names, {'fixture/tap/fixture-cask'}, self.output)
        self.assertEqual(plan.cask_identities, {('/bin/echo', 'fixture/tap/fixture-cask')})
        self.assertIn('/bin/echo uninstall --cask fixture/tap/fixture-cask', self.output)
        with self.assertRaises(ValueError):
            plan.apply(30)
        self.assertTrue(path.exists())
        self.assertFalse((self.home / 'teardown').exists())

    def test_cask_apply_invokes_exact_prefix_and_full_token(self):
        path = self.app()
        brew = self.home / 'bin' / 'owning-brew'
        brew.parent.mkdir()
        brew.write_text('''#!/bin/bash
set -eu
case "$1" in
    uninstall)
        [[ "$*" == 'uninstall --cask owner/tap/fixture' ]] || exit 4
        printf '%s\\n' "$@" > "$HOME/cask-command"
        mkdir -p "$HOME/.Trash"
        /bin/mv "$HOME/Applications/测试 App.app" "$HOME/.Trash/cask-fixture"
        ;;
    list)
        [[ ! -e "$HOME/Applications/测试 App.app" ]] || echo owner/tap/fixture
        ;;
    *) exit 5 ;;
esac
''')
        brew.chmod(0o755)
        self.owner_mock.return_value = {str(path): (str(brew), 'owner/tap/fixture')}
        original_unpack = self.unpack

        def authorized_unpack(destination):
            original_unpack(destination)
            wrapper = destination / 'mactools-apps.sh'
            wrapper.write_text(wrapper.read_text().replace(
                "ensure_sudo_session() { echo 'NO REAL ADMIN' >&2; return 1; }",
                'ensure_sudo_session() { return 0; }'))

        plan = self.plan(path)
        with patch.object(apps, 'unpack_bundle', side_effect=authorized_unpack):
            plan.preview()
        plan.apply(30)
        self.assertFalse(path.exists(), self.output)
        self.assertEqual((self.home / 'cask-command').read_text().splitlines(),
                         ['uninstall', '--cask', 'owner/tap/fixture'])


class QueryLifetime(unittest.TestCase):
    def test_brew_ownership_timeout_stops_background_worker(self):
        with tempfile.TemporaryDirectory(prefix='ma-owner-timeout-') as folder:
            marker = Path(folder) / 'late'
            with self.assertRaises(subprocess.TimeoutExpired):
                apps._brew_capture('/bin/bash', ['-c', '(sleep 0.3; touch "$1") & wait', '_', str(marker)], 0.03)
            time.sleep(0.4)
            self.assertFalse(marker.exists())

    def test_fzf_preview_cancellation_stops_background_ownership_query(self):
        with tempfile.TemporaryDirectory(prefix='ma-owner-cancel-') as folder:
            ready, marker = Path(folder) / 'ready', Path(folder) / 'late'
            code = """import sys
sys.path.insert(0, sys.argv[1])
import mactools_apps as apps
apps._brew_capture('/bin/bash', ['-c', 'touch "$1"; (sleep 0.5; touch "$2") & wait', '_', sys.argv[2], sys.argv[3]], 10)
"""
            process = subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'bin'), str(ready), str(marker)])
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 143)
                time.sleep(0.6)
                self.assertFalse(marker.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()


class CaskOwnership(unittest.TestCase):
    def app(self):
        return apps.App('app-' + 'a' * 64, 'Fixture', '1', '/Applications/Fixture.app', 'net.fixture')

    def inventory(self, token):
        return json.dumps({'casks': [{'full_token': token, 'token': 'fixture',
                                     'artifacts': [{'app': ['Fixture.app']}]}]})

    def test_uses_exact_artifact_owner_and_full_tap_identity(self):
        def capture(brew, arguments, timeout):
            if arguments[0] == 'info':
                return self.inventory('first/tap/fixture' if brew == '/first/brew' else 'second/tap/fixture')
            return ('/Applications/Fixture.app (3 files)\n' if brew == '/first/brew'
                    else '/elsewhere/Fixture.app (3 files)\n')
        with patch.object(apps, '_brew_candidates', return_value=['/first/brew', '/second/brew']), \
                patch.object(apps, '_brew_capture', side_effect=capture) as queried:
            owners = apps._cask_owners([self.app()], 10)
        self.assertEqual(owners, {'/Applications/Fixture.app': ('/first/brew', 'first/tap/fixture')})
        self.assertIn(('/second/brew', ['list', '--cask', 'second/tap/fixture'], 10),
                      [call.args for call in queried.call_args_list])

    def test_ambiguous_two_prefix_ownership_is_refused(self):
        def capture(brew, arguments, timeout):
            return self.inventory('fixture') if arguments[0] == 'info' else '/Applications/Fixture.app (3 files)\n'
        with patch.object(apps, '_brew_candidates', return_value=['/first/brew', '/second/brew']), \
                patch.object(apps, '_brew_capture', side_effect=capture):
            with self.assertRaises(ValueError):
                apps._cask_owners([self.app()], 10)

    def test_missing_brew_supports_native_apps(self):
        with patch.object(apps, '_brew_candidates', return_value=[]), \
                patch.object(apps, '_brew_capture', side_effect=AssertionError('queried brew')):
            self.assertEqual(apps._cask_owners([self.app()], 10), {})


if __name__ == '__main__':
    unittest.main()
