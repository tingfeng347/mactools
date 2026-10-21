"""Real process tests against isolated brew/fzf stand-ins; never use host brew."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import mactools_packages as packages

REAL_FZF = shutil.which("fzf")


BREW = r'''
import json, os, signal, subprocess, sys, time
args = sys.argv[1:]
with open(os.environ["BREW_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "auto": os.getenv("HOMEBREW_NO_AUTO_UPDATE")}) + "\n")
if args[:2] == ["info", "--json=v2"]:
    time.sleep(float(os.environ.get("INVENTORY_DELAY", "0")))
if args[:2] == ["info", "--json=v2"] and os.environ.get("BREW_INFO_JSON"):
    print(os.environ["BREW_INFO_JSON"])
    sys.exit(0)
if os.environ.get("FAIL_COMMAND") == args[0]:
    print("stub brew failure", file=sys.stderr)
    sys.exit(7)
if os.environ.get("SLOW_CHILD_COMMAND") == args[0]:
    subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)"])
    time.sleep(10)
if os.environ.get("SLOW_COMMAND") == args[0]:
    time.sleep(10)
if args[:2] == ["list", "--versions"]:
    for name in args[3:]:
        print(name, "3.0")
elif args[0] == "formulae":
    print("fzf\ngit\nowner/tools/tapped\n")
elif args[0] == "casks":
    print("firefox\ngit-client\n")
elif args[:2] == ["info", "--json=v2"]:
    print(json.dumps({"formulae": [{"name": "git", "full_name": "git", "installed": [{"version": "2.0"}, {"version": "2.1"}]}], "casks": [{"token": "firefox", "full_token": os.environ.get("CASK_FULL_TOKEN", "firefox"), "installed": "120.0"}]}))
elif args[0] == "outdated":
    print(json.dumps({"formulae": [{"name": "git", "installed_versions": ["2.1"], "current_version": "2.2"}], "casks": [{"name": "firefox", "installed_versions": ["120.0"], "current_version": "121.0"}]}))
elif args[0] == "info":
    print("Package metadata")
'''
FZF = r'''
import json, os, sys
with open(os.environ["FZF_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
rows = sys.stdin.read().splitlines()
step_path = os.environ["FZF_STEP_FILE"]
try:
    step = int(open(step_path).read())
except FileNotFoundError:
    step = 0
open(step_path, "w").write(str(step + 1))
keys = json.loads(os.environ.get("FZF_KEYS", "[]"))
key = keys[step] if step < len(keys) else ""
query = next(arg[8:] for arg in sys.argv if arg.startswith("--query="))
print(query)
print(key)
if not key:
    wanted = os.environ.get("FZF_SELECT", "git,firefox").split(",")
    for row in rows:
        if row.split("\t")[1] in wanted:
            print(row)
sys.exit(int(os.environ.get("FZF_STATUS", "0")))
'''


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.brew = self.root / "brew"
        self.fzf = self.root / "fzf"
        for path, code in ((self.brew, BREW), (self.fzf, FZF)):
            path.write_text("#!" + sys.executable + "\n" + code)
            path.chmod(0o755)
        self.env = {"PATH": str(self.root), "BREW_LOG": str(self.root / "brew.log"), "FZF_LOG": str(self.root / "fzf.log"), "FZF_STEP_FILE": str(self.root / "step"), "XDG_CACHE_HOME": str(self.root / "cache"), "MACTOOLS_LANG": "en", "HOMEBREW_NO_AUTO_UPDATE": "0"}
        self.patch_env = patch.dict(os.environ, self.env)
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        self.brew_patch = patch.object(packages, "brew_path", return_value=str(self.brew))
        self.brew_patch.start()
        self.addCleanup(self.brew_patch.stop)
        self.native_patch = patch.object(packages, "native_rows", return_value=[])
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)

    def test_native_apps_are_first_yellow_with_white_names(self):
        app = packages.Row('app', 'app-' + 'a' * 64, '1.2', display_name='My Editor')
        with patch.object(packages, 'native_rows', return_value=[app]):
            code, output = self.invoke('remove', '--list')
        self.assertEqual(code, 0)
        self.assertTrue(output.startswith('app\tMy Editor\t1.2\tapp-'))
        with patch.dict(os.environ, NO_COLOR='', TERM='xterm-256color'):
            fields = app.selector_line('remove').split('\t')
        self.assertIn('\033[38;5;220mapp', fields[4])
        self.assertIn('\033[37mMy Editor', fields[5])
        self.assertNotIn('My Editor', app.line())

    def test_native_only_does_not_require_homebrew(self):
        with patch.object(packages, 'brew_path', side_effect=ValueError('absent')):
            self.assertEqual(self.invoke('remove', '--apps', '--list')[0], 0)
            self.assertEqual(self.invoke('remove', '--list')[0], 0)
            with self.assertRaises(ValueError):
                self.invoke('remove', '--cask', '--list')
        self.assertEqual(self.calls(), [])

    def test_native_flag_validation(self):
        for args in [('install', '--apps'), ('update', '--app-data'),
                     ('remove', '--formula', '--app-data')]:
            with self.assertRaises(ValueError):
                self.invoke(*args)

    def test_native_selection_searches_name_and_keeps_header(self):
        self.invoke('remove', '--dry-run')
        args = json.loads((self.root / 'fzf.log').read_text().splitlines()[0])
        self.assertIn('--nth=2', args)
        self.assertIn('--no-sort', args)
        header = next(arg for arg in args if arg.startswith('--header='))
        self.assertNotIn('Ctrl+A', header)

    def test_native_preview_confirmation_and_duplicate_cask(self):
        app = packages.Row('app', 'app-' + 'b' * 64, '1', display_name='Firefox')
        for dry_run, accepted in ((True, True), (False, False), (False, True)):
            with self.subTest(dry_run=dry_run, accepted=accepted):
                with patch('mactools_apps.resolve_apps', return_value=['resolved']) as resolve, \
                     patch('mactools_apps.AppUninstallPlan') as plan_factory, \
                     patch.object(packages, 'confirm', return_value=accepted):
                    native_plan = plan_factory.return_value
                    native_plan.cask_identities = {(str(self.brew), 'firefox')}
                    native_plan.apply.return_value = 0
                    opts = packages.parser().parse_args(['remove', '--app-data', '--scan-timeout', '240'] + (['--dry-run'] if dry_run else []))
                    queries = packages.Queries(str(self.brew), 15)
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = packages.execute_selection(str(self.brew), queries, opts,
                            [app, packages.Row('cask', 'homebrew/cask/firefox', '120')])
                    self.assertEqual(status, 0)
                    resolve.assert_called_once_with([app.name], 15)
                    plan_factory.assert_called_once_with(['resolved'], 15, with_data=True, scan_timeout=240)
                    native_plan.preview.assert_called_once_with()
                    native_plan.close.assert_called_once_with()
                    self.assertEqual(native_plan.apply.called, not dry_run and accepted)
        self.assertEqual(self.mutations(), [])

    def test_leftover_choice_is_explicit_and_remove_only(self):
        for args in (('install', '--clean-leftovers'), ('update', '--keep-leftovers')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.invoke(*args)
        with self.assertRaises(SystemExit):
            packages.parser().parse_args(['remove', '--clean-leftovers', '--keep-leftovers'])
        with self.assertRaises(ValueError):
            self.invoke('remove', '--app-data', '--keep-leftovers')

    def test_interactive_native_uninstall_asks_about_leftovers(self):
        app = packages.Row('app', 'app-' + 'f' * 64, '1', display_name='Editor')
        for answer, expected in ((False, False), (True, True)):
            with self.subTest(answer=answer):
                with patch('mactools_apps.resolve_apps', return_value=['resolved']), \
                     patch('mactools_apps.AppUninstallPlan') as factory, \
                     patch.object(packages, 'confirm', side_effect=[answer, True]) as confirm:
                    factory.return_value.cask_identities = set()
                    factory.return_value.apply.return_value = 0
                    opts = packages.parser().parse_args(['remove'])
                    queries = packages.Queries(str(self.brew), 15)
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = packages.execute_selection(str(self.brew), queries, opts, [app])
                    self.assertEqual(status, 0)
                    self.assertEqual(confirm.call_count, 2)
                    self.assertIn('leftover', confirm.call_args_list[0].args[0].lower())
                    factory.return_value.apply.assert_called_once_with(600, clean_leftovers=expected)

    def test_native_uninstall_flags_choose_leftover_handling(self):
        app = packages.Row('app', 'app-' + 'g' * 64, '1', display_name='Editor')
        for args, expected in (
            (['remove', '--yes'], False),
            (['remove', '--clean-leftovers', '--yes'], True),
            (['remove', '--keep-leftovers', '--yes'], False),
            (['remove', '--app-data', '--yes'], True),
        ):
            with self.subTest(args=args):
                with patch('mactools_apps.resolve_apps', return_value=['resolved']), \
                     patch('mactools_apps.AppUninstallPlan') as factory:
                    factory.return_value.cask_identities = set()
                    factory.return_value.apply.return_value = 0
                    opts = packages.parser().parse_args(args)
                    queries = packages.Queries(str(self.brew), 15)
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = packages.execute_selection(str(self.brew), queries, opts, [app])
                    self.assertEqual(status, 0)
                    factory.return_value.apply.assert_called_once_with(600, clean_leftovers=expected)

    def test_successful_mutations_patch_selector_snapshots(self):
        cases = (
            ('install', packages.Row('formula', 'git', '1'), '3.0'),
            ('update', packages.Row('formula', 'git', '1', '2.2'), '2.2'),
        )
        for action, row, current in cases:
            with self.subTest(action=action):
                snapshot = self.write_snapshot(action, [row])
                self.assertEqual(self.invoke(action, '--yes')[0], 0)
                payload = json.loads(snapshot.read_text())
                git = next(line for line in payload['rows'] if line.startswith('formula\tgit\t'))
                self.assertEqual(packages.Row.parse(git).current, current)
        snapshot = self.write_snapshot('remove', [packages.Row('formula', 'git', '1')])
        self.assertEqual(self.invoke('remove', '--yes')[0], 0)
        payload = json.loads(snapshot.read_text())
        self.assertFalse(any(line.startswith('formula\tgit\t') for line in payload['rows']))

    def test_uninstall_patch_clears_install_and_update_snapshots(self):
        install_snapshot = self.write_snapshot('install', [packages.Row('formula', 'git', '1')])
        update_snapshot = self.write_snapshot('update', [packages.Row('formula', 'git', '1', '2')])
        remove_snapshot = self.write_snapshot('remove', [packages.Row('formula', 'git', '1')])
        self.assertEqual(self.invoke('remove', '--yes')[0], 0)
        install_rows = [packages.Row.parse(line)
                        for line in json.loads(install_snapshot.read_text())['rows']]
        self.assertEqual([(row.name, row.current) for row in install_rows], [('git', '-')])
        self.assertEqual(json.loads(update_snapshot.read_text())['rows'], [])
        remove_rows = [packages.Row.parse(line)
                       for line in json.loads(remove_snapshot.read_text())['rows']]
        self.assertEqual([row.name for row in remove_rows], ['firefox'])

    def test_uninstall_patch_drops_only_removed_app_from_snapshot(self):
        app = packages.Row('app', 'app-' + 'b' * 64, '1.0', display_name='Editor')
        other = packages.Row('app', 'app-' + 'c' * 64, '2.0', display_name='Other')
        snapshot = self.write_snapshot('remove', [packages.Row('formula', 'git', '1')],
                                       apps=[app, other])
        with patch('mactools_apps.resolve_apps', return_value=['resolved']), \
             patch('mactools_apps.AppUninstallPlan') as factory:
            factory.return_value.cask_identities = set()
            factory.return_value.apply.return_value = 0
            opts = packages.parser().parse_args(['remove', '--yes'])
            queries = packages.Queries(str(self.brew), 15)
            with contextlib.redirect_stdout(io.StringIO()):
                status = packages.execute_selection(str(self.brew), queries, opts, [app])
        self.assertEqual(status, 0)
        payload = json.loads(snapshot.read_text())
        self.assertTrue(any(line.startswith('formula\tgit\t') for line in payload['rows']))
        names = [packages.Row.parse(entry['row']).name for entry in payload['apps']]
        self.assertNotIn(app.name, names)
        self.assertIn(other.name, names)

    def test_install_patch_updates_install_and_remove_snapshots(self):
        install_snapshot = self.write_snapshot('install', [packages.Row('formula', 'git', '-')])
        remove_snapshot = self.write_snapshot('remove', [])
        self.assertEqual(self.invoke('install', '--yes')[0], 0)
        install_rows = [packages.Row.parse(line)
                        for line in json.loads(install_snapshot.read_text())['rows']]
        git = next(row for row in install_rows if row.source == 'formula' and row.name == 'git')
        self.assertEqual(git.current, '3.0')
        firefox = next(row for row in install_rows if row.source == 'cask' and row.name == 'firefox')
        self.assertEqual(firefox.current, '120.0')
        remove_rows = [packages.Row.parse(line)
                       for line in json.loads(remove_snapshot.read_text())['rows']]
        self.assertEqual([(row.name, row.current) for row in remove_rows], [('git', '3.0')])

    def test_app_uninstall_patch_drops_owning_cask_row(self):
        cask = packages.Row('cask', 'homebrew/cask/cherry-studio', '2.0.8')
        app = packages.Row('app', 'app-' + 'd' * 64, '2.0.8', display_name='Cherry Studio')
        snapshot = self.write_snapshot('remove', [cask])
        with patch('mactools_apps.resolve_apps', return_value=['resolved']), \
             patch('mactools_apps.AppUninstallPlan') as factory:
            factory.return_value.cask_identities = {(str(self.brew), 'cherry-studio')}
            factory.return_value.apply.return_value = 0
            opts = packages.parser().parse_args(['remove', '--keep-leftovers', '--yes'])
            queries = packages.Queries(str(self.brew), 15)
            with contextlib.redirect_stdout(io.StringIO()):
                status = packages.execute_selection(str(self.brew), queries, opts, [app])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(snapshot.read_text())['rows'], [])
        self.assertEqual(self.mutations(), [])

    def test_expired_selector_snapshot_is_not_patched_or_queried(self):
        snapshot = self.write_snapshot('remove', [packages.Row('formula', 'git', '1')])
        payload = json.loads(snapshot.read_text())
        payload['time'] = 0
        snapshot.write_text(json.dumps(payload))
        before = snapshot.read_bytes()
        queries = packages.Queries(str(self.brew), 2)
        packages.patch_selector_cache(queries, 'install', [packages.Row('formula', 'git', '-')])
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertEqual(self.calls(), [])

    def test_unpatchable_selector_snapshot_falls_back_to_removal(self):
        snapshot = self.write_snapshot('remove', [packages.Row('formula', 'git', '1')])
        snapshot.write_text('{not json')
        queries = packages.Queries(str(self.brew), 2)
        packages.patch_selector_cache(queries, 'remove', [])
        self.assertFalse(snapshot.exists())

    def test_dry_run_keeps_selector_snapshot(self):
        for action in ('install', 'remove', 'update'):
            with self.subTest(action=action):
                snapshot = self.write_snapshot(action, [packages.Row('formula', 'git', '1')])
                self.assertEqual(self.invoke(action, '--dry-run', '--yes')[0], 0)
                self.assertTrue(snapshot.exists())

    def test_cask_dedup_requires_same_homebrew_and_tap(self):
        self.assertEqual(packages.cask_identity(str(self.brew), 'firefox'),
                         packages.cask_identity(str(self.brew), 'homebrew/cask/firefox'))
        self.assertNotEqual(packages.cask_identity(str(self.brew), 'firefox'),
                            packages.cask_identity(str(self.brew), 'owner/tap/firefox'))
        self.assertNotEqual(packages.cask_identity(str(self.brew), 'firefox'),
                            packages.cask_identity(str(self.root / 'other-brew'), 'firefox'))

    def test_native_app_hides_only_its_exact_cask_row(self):
        app = packages.Row('app', 'app-' + 'c' * 64, '4.0.10', display_name='CC Switch',
                           app_path='/Applications/CC Switch.app')
        item = {'token': 'cc-switch', 'full_token': 'homebrew/cask/cc-switch',
                'installed': '4.0.10', 'bundle_short_version': '4.0.10',
                'artifacts': [{'app': ['CC Switch.app'], 'target': '/Applications/CC Switch.app'}]}
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        with patch.object(packages, 'native_rows', return_value=[app]), \
             patch.object(queries, 'capture', return_value=json.dumps({'formulae': [], 'casks': [item]})):
            result = list(packages.rows(queries, opts))
        self.assertEqual([row.source for row in result], ['app'])
        # The hidden cask token is remembered for the next listing, so the
        # merged app row stays searchable without another brew inventory.
        self.assertEqual(packages.app_alias_map(),
                         {packages.app_path_key(app.app_path): 'cc-switch'})

    def test_same_basename_in_another_app_directory_keeps_both_rows(self):
        app = packages.Row('app', 'app-' + 'd' * 64, '4.0.10', display_name='CC Switch',
                           app_path='/Users/alice/Applications/CC Switch.app')
        item = {'token': 'cc-switch', 'full_token': 'homebrew/cask/cc-switch',
                'installed': '4.0.10',
                'artifacts': [{'app': ['CC Switch.app'], 'target': '/Applications/CC Switch.app'}]}
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        with patch.object(packages, 'native_rows', return_value=[app]), \
             patch.object(queries, 'capture', return_value=json.dumps({'formulae': [], 'casks': [item]})):
            result = list(packages.rows(queries, opts))
        self.assertEqual([row.source for row in result], ['app', 'cask'])

    def test_ambiguous_cask_ownership_keeps_the_app_and_every_cask(self):
        app = packages.Row('app', 'app-' + 'e' * 64, '1', display_name='Editor',
                           app_path='/Applications/Editor.app')
        casks = [
            {'token': 'editor', 'full_token': 'homebrew/cask/editor', 'installed': '1',
             'artifacts': [{'app': ['Editor.app'], 'target': '/Applications/Editor.app'}]},
            {'token': 'editor', 'full_token': 'owner/tap/editor', 'installed': '1',
             'artifacts': [{'app': ['Editor.app'], 'target': '/Applications/Editor.app'}]},
        ]
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        with patch.object(packages, 'native_rows', return_value=[app]), \
             patch.object(queries, 'capture', return_value=json.dumps({'formulae': [], 'casks': casks})):
            result = list(packages.rows(queries, opts))
        self.assertEqual([row.source for row in result], ['app', 'cask', 'cask'])

    def test_legacy_cached_duplicate_keeps_native_app_until_live_refresh(self):
        app = packages.Row('app', 'app-' + 'f' * 64, '4.0.10', display_name='CC Switch')
        cask = packages.Row('cask', 'homebrew/cask/cc-switch', '4.0.10')
        self.write_snapshot('remove', [cask], apps=[app])
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        cached = packages.cached_selector(queries, opts)
        self.assertEqual(cached, [app])
        # A legacy snapshot hides the cask without losing its search token.
        self.assertEqual(cached[0].search_alias, 'cc-switch')

    def test_selector_alias_roundtrip_and_native_lookup(self):
        app = packages.Row('app', 'app-' + 'a' * 64, '1', display_name='CC Switch',
                           app_path=str(self.root / 'Applications/CC Switch.app'),
                           search_alias='cc-switch')
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        packages.save_selector(queries, opts, [app])
        self.assertEqual(packages.cached_selector(queries, opts), [app])
        packages.save_app_alias_map({packages.app_path_key(app.app_path): 'cc-switch'})
        self.assertEqual(packages.app_alias_map(),
                         {packages.app_path_key(app.app_path): 'cc-switch'})
        installed = Mock(id=app.name, version='1', path=app.app_path)
        installed.name = 'CC Switch'
        self.native_patch.stop()
        with patch('mactools_apps.installed_apps', return_value=[installed]):
            self.assertEqual(packages.native_rows(opts), [app])

    def test_selector_alias_is_displayed_dim_and_never_leaks_into_records(self):
        app = packages.Row('app', 'app-' + 'a' * 64, '4.0.10', display_name='CC Switch',
                           search_alias='cc-switch')
        with patch.dict(os.environ, NO_COLOR='', TERM='xterm-256color'):
            colored = app.selector_line('remove', (60, 18))
        self.assertIn('\033[38;5;243m · cc-switch\033[0m', colored)
        self.assertIn('CC Switch · cc-switch', packages.without_color(colored))
        self.assertNotIn('cc-switch', app.line())
        # A token that cannot fit is hidden rather than clipped: clipping would
        # display a query that fzf can no longer match.
        self.assertNotIn('cc-switch',
                         packages.without_color(app.selector_line('remove', (20, 18))))

    def test_malformed_cached_alias_is_ignored(self):
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        row = packages.Row('app', 'app-' + 'a' * 64, '1', display_name='Editor')
        snapshot = self.write_snapshot('remove', [], apps=[row])
        payload = json.loads(snapshot.read_text())
        for alias in ('bad token', '..', 3):
            payload['apps'][0]['alias'] = alias
            snapshot.write_text(json.dumps(payload))
            self.assertIsNone(packages.cached_selector(queries, opts))

    def test_native_failure_stops_remaining_brew_operations(self):
        opts = packages.parser().parse_args(['remove', '--yes'])
        queries = packages.Queries(str(self.brew), 15)
        app = packages.Row('app', 'app-' + 'c' * 64, '1', display_name='Editor')
        with patch('mactools_apps.resolve_apps', return_value=['resolved']), \
             patch('mactools_apps.AppUninstallPlan') as factory:
            factory.return_value.cask_identities = set()
            factory.return_value.apply.return_value = 4
            with contextlib.redirect_stdout(io.StringIO()):
                code = packages.execute_selection(str(self.brew), queries, opts,
                    [app, packages.Row('formula', 'git', '2')])
            self.assertEqual(code, 4)
            factory.return_value.close.assert_called_once_with()
        self.assertEqual(self.mutations(), [])

    def test_native_snapshot_roundtrip_and_stale_selection(self):
        app = packages.Row('app', 'app-' + 'd' * 64, '1', display_name='Old Editor')
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        packages.save_selector(queries, opts, [app])
        self.assertEqual(packages.cached_selector(queries, opts), [app])
        opts.include_apps = False
        self.assertEqual(packages.cached_selector(queries, opts), [])
        # A cached application disappearing during refresh must never reach apply.
        self.cached_fzf()
        with patch.dict(os.environ, CACHED_SELECT=app.name, INVENTORY_DELAY='0.1'):
            self.assertEqual(self.invoke('remove', '--yes')[0], 0)
        self.assertEqual(self.mutations(), [])

    def test_malformed_native_cache_is_ignored(self):
        opts = packages.parser().parse_args(['remove'])
        opts.include_apps, opts.sources = True, ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 15)
        for entry in (None, {'row': 'bad', 'display_name': 'Editor'},
                      {'row': 'cask\teditor\t1\t-', 'display_name': 'Editor'}):
            snapshot = self.write_snapshot('remove', [])
            payload = json.loads(snapshot.read_text())
            payload['apps'] = [entry]
            snapshot.write_text(json.dumps(payload))
            self.assertIsNone(packages.cached_selector(queries, opts))

    @unittest.skipUnless(REAL_FZF, 'host fzf unavailable')
    def test_real_fzf_searches_native_display_name_and_preserves_app_group(self):
        app = packages.Row('app', 'app-' + 'e' * 64, '1', display_name='Firefox')
        cask = packages.Row('cask', 'firefox', '120')
        data = '\n'.join(row.selector_line('remove') for row in (app, cask)) + '\n'
        env = dict(os.environ)
        env.pop('FZF_DEFAULT_OPTS', None)
        env.pop('FZF_DEFAULT_OPTS_FILE', None)
        result = subprocess.run([REAL_FZF, '--ansi', '--delimiter=\t', '--nth=2',
                                 '--with-nth=5..', '--no-sort', '--sync', '--filter=firefox'],
                                input=data, capture_output=True, text=True, env=env, check=True)
        self.assertEqual([packages.Row.parse(line).source for line in result.stdout.splitlines()], ['app', 'cask'])

    @unittest.skipUnless(REAL_FZF, 'host fzf unavailable')
    def test_real_fzf_matches_hidden_cask_alias_on_the_app_row(self):
        app = packages.Row('app', 'app-' + 'a' * 64, '4.0.10',
                           display_name='CC Switch', search_alias='cc-switch')
        data = app.selector_line('remove') + '\n'
        env = dict(os.environ)
        env.pop('FZF_DEFAULT_OPTS', None)
        env.pop('FZF_DEFAULT_OPTS_FILE', None)
        for query in ('cc-switch', 'ccswitch', 'CC Switch'):
            with self.subTest(query=query):
                result = subprocess.run([REAL_FZF, '--ansi', '--delimiter=\t', '--nth=2',
                                         '--with-nth=5..', '--no-sort', '--sync',
                                         '--filter=' + query],
                                        input=data, capture_output=True, text=True, env=env)
                self.assertEqual([packages.Row.parse(line).source
                                  for line in result.stdout.splitlines()], ['app'])

    def calls(self):
        path = self.root / "brew.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = packages.main(list(args))
        return code, output.getvalue()

    def mutations(self):
        return [call["args"] for call in self.calls() if call["args"][0] in ("install", "uninstall", "upgrade", "update")]

    def write_snapshot(self, action, result, sources=None, apps=None):
        snapshot = self.root / ('cache/mactools/selector-' + action + '.json')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        payload = {'brew': str(self.brew), 'time': time.time(),
            'sources': sources or ['formula', 'cask'], 'rows': [row.line() for row in result]}
        if apps is not None:
            payload['apps'] = [{'row': row.line(), 'display_name': row.display_name} for row in apps]
        snapshot.write_text(json.dumps(payload))
        return snapshot

    def cached_fzf(self, reload=False):
        # Implement only the fzf transport contract. The actual refresh worker,
        # atomic snapshot helper, inventory parsing and command plans all run.
        self.fzf.write_text('#!' + sys.executable + '\n' + r"""import os,sys,time,json,subprocess
if sys.argv[1:] == ['--help']:
    print('--id-nth')
    raise SystemExit(0)
start=time.monotonic()
first=sys.stdin.readline()
if os.environ.get('FIRST_ROW_RESULT'):
    open(os.environ['FIRST_ROW_RESULT'], 'w').write(str(time.monotonic()-start) if first else 'empty')
rows=([first.rstrip('\n')] if first else [])+sys.stdin.read().splitlines()
if os.environ.get('SIMULATE_RELOAD'):
    bind=next(arg for arg in sys.argv if arg.startswith('--bind=load:'))
    command=bind.split('reload-sync:', 1)[1]
    fresh=subprocess.check_output(command, shell=True, text=True, timeout=10).splitlines()
    open(os.environ['RELOAD_RESULT'], 'w').write(json.dumps({'cached': rows, 'fresh': fresh}))
    rows=fresh
if os.environ.get('CANCEL_CACHED'):
    raise SystemExit(130)
print('')
print('')
for row in rows:
    if row.split('\t')[1] == os.environ.get('CACHED_SELECT', 'git'):
        print(row)
""")

    def test_cached_selector_rows_arrive_before_slow_brew_inventory(self):
        self.cached_fzf()
        result = self.root / 'first-row'
        for action in ('install', 'remove', 'update'):
            self.write_snapshot(action, [packages.Row('formula', 'git', '2.0', '2.2')])
            with self.subTest(action=action), patch.dict(os.environ,
                INVENTORY_DELAY='0.6', FIRST_ROW_RESULT=str(result), CANCEL_CACHED='1'
            ):
                code, _ = self.invoke(action)
                self.assertEqual(code, 0)
                self.assertNotEqual(result.read_text(), 'empty')
                self.assertLess(float(result.read_text()), 0.25, 'cached rows waited for brew inventory')

    def test_background_refresh_replaces_old_rows_and_keeps_cache_readonly_in_dry_run(self):
        snapshot = self.write_snapshot('update', [packages.Row('formula', 'ghost', '1', '2')])
        before = snapshot.read_bytes()
        result = self.root / 'reload.json'
        self.cached_fzf(reload=True)
        with patch.dict(os.environ, SIMULATE_RELOAD='1', RELOAD_RESULT=str(result)):
            code, output = self.invoke('update', '--dry-run')
        self.assertEqual(code, 0)
        recorded = json.loads(result.read_text())
        self.assertTrue(any('ghost' in row for row in recorded['cached']))
        self.assertFalse(any('ghost' in row for row in recorded['fresh']))
        self.assertTrue(any('git' in row for row in recorded['fresh']))
        self.assertIn('upgrade --formula git', output)
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertEqual(self.mutations(), [])

    def test_empty_background_snapshot_does_not_create_a_blank_selectable_row(self):
        self.write_snapshot('remove', [packages.Row('formula', 'git', '1')])
        result = self.root / 'reload.json'
        self.cached_fzf()
        with patch.dict(os.environ, SIMULATE_RELOAD='1', RELOAD_RESULT=str(result), BREW_INFO_JSON='{}'):
            code, _ = self.invoke('remove', '--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(result.read_text())['fresh'], [])
        self.assertEqual(self.mutations(), [])

    def test_early_cached_selection_rechecks_removed_or_self_updated_apps(self):
        self.cached_fzf()
        for action, info in (
            ('remove', {'casks': []}),
            ('update', {'casks': [{'token': 'firefox', 'installed': '120.0', 'bundle_short_version': '122.0'}]}),
            ('install', {'casks': []}),
        ):
            name = 'ghost' if action == 'install' else 'firefox'
            self.write_snapshot(action, [packages.Row('cask', name, '120.0', '121.0')])
            with self.subTest(action=action), patch.dict(os.environ,
                INVENTORY_DELAY='0.3', CACHED_SELECT=name, BREW_INFO_JSON=json.dumps(info)
            ):
                code, output = self.invoke(action, '--yes')
                self.assertEqual(code, 0)
                self.assertNotIn('Plan:', output)
                self.assertEqual(self.mutations(), [])

    def test_early_accept_reuses_refresh_and_prints_current_version(self):
        self.cached_fzf()
        self.write_snapshot('remove', [packages.Row('formula', 'git', '0.1')])
        with patch.dict(os.environ, INVENTORY_DELAY='0.3'):
            code, output = self.invoke('remove', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('2.0, 2.1', output)
        inventory_calls = [call for call in self.calls() if call['args'][:2] == ['info', '--json=v2']]
        self.assertEqual(len(inventory_calls), 1)
        self.assertEqual(self.mutations(), [])

    def test_failed_refresh_cannot_execute_cached_update(self):
        self.cached_fzf()
        self.write_snapshot('update', [packages.Row('formula', 'git', '1', '2')])
        with patch.dict(os.environ, FAIL_COMMAND='outdated'):
            code, _ = self.invoke('update', '--yes')
        self.assertEqual(code, 7)
        self.assertEqual(self.mutations(), [])

    def test_invalid_or_mismatched_selector_snapshot_is_ignored(self):
        opts = packages.parser().parse_intermixed_args(['remove'])
        opts.sources = ['formula', 'cask']
        queries = packages.Queries(str(self.brew), 2)
        for change in ({'time': 0}, {'brew': '/different/brew'}, {'sources': ['formula']},
                       {'rows': ['formula\t--zap\t1\t-']}, {'rows': ['formula\tgit\t-\t-']},
                       {'rows': None}, {'time': float('nan')}):
            snapshot = self.write_snapshot('remove', [packages.Row('formula', 'git', '1')])
            payload = json.loads(snapshot.read_text())
            payload.update(change)
            snapshot.write_text(json.dumps(payload))
            with self.subTest(change=change):
                self.assertIsNone(packages.cached_selector(queries, opts))

    def test_source_specific_snapshots_do_not_evict_combined_snapshot(self):
        opts = packages.parser().parse_intermixed_args(['remove'])
        queries = packages.Queries(str(self.brew), 2)
        opts.sources = ['formula', 'cask']
        combined = [packages.Row('formula', 'git', '1'), packages.Row('cask', 'firefox', '120')]
        packages.save_selector(queries, opts, combined)
        opts.sources = ['formula']
        packages.save_selector(queries, opts, combined[:1])
        self.assertEqual(packages.cached_selector(queries, opts), combined[:1])
        opts.sources = ['formula', 'cask']
        self.assertEqual(packages.cached_selector(queries, opts), combined)

    def test_filtered_selector_can_reuse_combined_snapshot(self):
        opts = packages.parser().parse_intermixed_args(['remove'])
        queries = packages.Queries(str(self.brew), 2)
        result = [packages.Row('formula', 'git', '1'), packages.Row('cask', 'firefox', '120')]
        self.write_snapshot('remove', result)
        for source in ('formula', 'cask'):
            opts.sources = [source]
            with self.subTest(source=source):
                self.assertEqual(packages.cached_selector(queries, opts), [r for r in result if r.source == source])

    def test_cancel_finished_query_reaps_before_signalling_its_group(self):
        process = subprocess.Popen([sys.executable, '-c', 'pass'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            process.stdout.read()
            process.stderr.read()
            # Leave the exited child unreaped, as happens when fzf cancellation
            # races the inventory worker. Darwin rejects killpg on this group.
            time.sleep(0.1)
            self.assertIsNone(process.returncode)
            packages.Queries.stop(process)
            self.assertEqual(process.poll(), 0)
        finally:
            process.communicate(timeout=2)

    def test_cancel_query_handles_exit_between_poll_and_group_signal(self):
        process = Mock(pid=12345, _mactools_detached=True)
        process.poll.side_effect = [None, 0, 0, 0]
        with patch.object(packages.os, 'killpg', side_effect=PermissionError):
            packages.Queries.stop(process)
        process.poll.assert_called()

    def test_cancel_live_query_preserves_permission_errors(self):
        process = Mock(pid=12345, _mactools_detached=True)
        process.poll.return_value = None
        with patch.object(packages.os, 'killpg', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                packages.Queries.stop(process)

    def test_cancel_finished_wrapper_still_kills_its_children(self):
        code = ('import subprocess,sys; '
                'subprocess.Popen([sys.executable,"-c",'
                '"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                'print(\\\"ready\\\",flush=True);time.sleep(10)"])')
        process = subprocess.Popen([sys.executable, '-c', code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            process.wait(timeout=2)
            packages.Queries.stop(process)
            process.communicate(timeout=2)
        finally:
            try:
                os.killpg(process.pid, packages.signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            process.communicate(timeout=2)

    def test_terminated_preview_reaps_its_brew_query(self):
        import signal
        pid_path = self.root / 'preview-brew.pid'
        self.brew.write_text('#!' + sys.executable + '\n' +
            'import os,time\nfrom pathlib import Path\n' +
            'Path(os.environ["PREVIEW_BREW_PID"]).write_text(str(os.getpid()))\n' +
            'time.sleep(10)\n')
        env = dict(os.environ, PREVIEW_BREW_PID=str(pid_path))
        process = subprocess.Popen([sys.executable, str(Path(packages.__file__).resolve()),
            '--preview', str(self.brew), '15', 'formula', 'git'], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        brew_pid = None
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    brew_pid = int(pid_path.read_text())
                    break
                except (FileNotFoundError, ValueError):
                    time.sleep(0.02)
            self.assertIsNotNone(brew_pid, 'fixture brew did not start')
            process.terminate()
            process.communicate(timeout=2)
            with self.assertRaises(ProcessLookupError, msg='preview left a brew query running'):
                os.kill(brew_pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if brew_pid:
                try:
                    os.killpg(brew_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_fzf_forced_preview_cancellation_includes_brew_process_group(self):
        import signal
        pid_path = self.root / 'fzf-preview-brew.pid'
        self.brew.write_text('#!' + sys.executable + '\n' +
            'import os,time\nfrom pathlib import Path\n' +
            'Path(os.environ["PREVIEW_BREW_PID"]).write_text(str(os.getpid()))\n' +
            'time.sleep(10)\n')
        process = subprocess.Popen([sys.executable, str(Path(packages.__file__).resolve()),
            '--preview', str(self.brew), '15', 'formula', 'git'],
            env=dict(os.environ, PREVIEW_BREW_PID=str(pid_path), FZF_PREVIEW_COLUMNS='80'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        brew_pid = None
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    brew_pid = int(pid_path.read_text())
                    break
                except (FileNotFoundError, ValueError):
                    time.sleep(0.02)
            self.assertIsNotNone(brew_pid, 'fixture brew did not start')
            # fzf forcibly kills the whole preview group. The query must be in
            # that group, because SIGKILL cannot run Python cleanup handlers.
            self.assertEqual(os.getpgid(brew_pid), process.pid)
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=2)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            if brew_pid:
                try:
                    os.killpg(brew_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_install_list_filters_name_not_source_or_version_and_never_caches(self):
        code, output = self.invoke("install", "--list", "--exact", "git")
        self.assertEqual(code, 0)
        self.assertIn("formula\tgit\t2.0, 2.1\t-", output)
        self.assertIn("cask\tgit-client\t-\t-", output)
        self.assertNotIn("firefox", output)
        self.assertFalse((self.root / "cache").exists())
        self.assertEqual(self.mutations(), [])
        self.assertTrue(all(call["auto"] == "1" for call in self.calls()))
        _, output = self.invoke("install", "--list", "formula")
        self.assertEqual(output, "")

    def test_sources_combine_and_brew_alias_selects_formulae(self):
        _, output = self.invoke("install", "--list", "--brew")
        self.assertNotIn("cask\t", output)
        self.assertNotIn(["casks"], [call["args"] for call in self.calls()])
        self.assertIn(['info', '--json=v2', '--installed', '--formula'], [call['args'] for call in self.calls()])
        _, output = self.invoke("remove", "--list", "--formula", "--cask")
        self.assertIn("formula\tgit", output)
        self.assertIn("cask\tfirefox", output)

    def test_update_versions_and_no_implicit_index_refresh(self):
        code, output = self.invoke("update", "--list")
        self.assertEqual(code, 0)
        self.assertIn("formula\tgit\t2.1\t2.2", output)
        self.assertIn("cask\tfirefox\t120.0\t121.0", output)
        self.assertEqual(self.mutations(), [])

    def test_remove_dry_run_preserves_installed_versions_and_data(self):
        code, output = self.invoke("remove", "--dry-run", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("uninstall --formula git", output)
        self.assertIn("uninstall --cask firefox", output)
        self.assertIn("2.0, 2.1", output)
        self.assertNotIn("zap", output)
        self.assertEqual(self.mutations(), [])

    def test_install_dry_run_does_not_create_cache(self):
        self.assertEqual(self.invoke("install", "--dry-run", "--yes")[0], 0)
        self.assertEqual(self.mutations(), [])
        self.assertFalse((self.root / "cache").exists())

    def test_confirmation_decline_prints_plan_without_mutation(self):
        with patch.object(packages, "confirm", return_value=False) as confirm:
            code, output = self.invoke("remove")
        self.assertEqual(code, 0)
        self.assertIn("Plan:", output)
        confirm.assert_called_once()
        self.assertEqual(self.mutations(), [])

    def test_yes_keeps_native_prompts_no_native_yes_flag(self):
        self.assertEqual(self.invoke("update", "--yes")[0], 0)
        self.assertEqual(self.mutations(), [["upgrade", "--formula", "git"], ["upgrade", "--cask", "firefox"]])

    def test_native_failure_stops_remaining_sources_and_preserves_exit(self):
        with patch.dict(os.environ, {"FAIL_COMMAND": "uninstall"}):
            self.assertEqual(self.invoke("remove", "--yes")[0], 7)
        self.assertEqual(self.mutations(), [["uninstall", "--formula", "git"]])

    def test_list_query_failure_is_nonzero(self):
        with patch.dict(os.environ, {"FAIL_COMMAND": "outdated"}):
            self.assertEqual(self.invoke("update", "--list")[0], 7)

    def test_bounded_query_terminates_and_propagates_timeout(self):
        with patch.dict(os.environ, {"SLOW_COMMAND": "outdated"}):
            started = time.monotonic()
            self.assertEqual(self.invoke("update", "--list", "--timeout", "0.05")[0], 124)
            self.assertLess(time.monotonic() - started, 2)

    def test_cache_hits_and_explicit_refresh(self):
        queries = packages.Queries(str(self.brew), 2)
        self.addCleanup(queries.close)
        packages.catalog(queries, "formula")
        packages.catalog(queries, "formula")
        self.assertEqual(len(self.calls()), 1)
        packages.catalog(queries, "formula", refresh=True)
        self.assertEqual(len(self.calls()), 2)
        with patch.dict(queries.env, {"FAIL_COMMAND": "formulae"}):
            with self.assertRaises(packages.BrewError):
                packages.catalog(queries, "formula", refresh=True)
        self.assertEqual(packages.catalog(queries, "formula"), ["fzf", "git", "owner/tools/tapped"])

    def test_ctrl_e_and_r_keep_query_and_refresh_names(self):
        with patch.dict(os.environ, {"FZF_KEYS": '["ctrl-e", "ctrl-r"]'}):
            code, output = self.invoke("install", "--yes", "--dry-run", "--formula", "git")
        self.assertEqual(code, 0)
        invocations = [json.loads(line) for line in (self.root / "fzf.log").read_text().splitlines()]
        self.assertEqual(len(invocations), 3)
        self.assertNotIn("--exact", invocations[0])
        self.assertIn("--exact", invocations[1])
        for args in invocations:
            self.assertIn("--nth=2", args)
            self.assertIn("--multi", args)
            self.assertIn("--query=git", args)
        self.assertEqual(self.mutations(), [])

    def test_esc_cancels_without_mutation(self):
        with patch.dict(os.environ, {"FZF_STATUS": "130"}):
            self.assertEqual(self.invoke("remove", "--yes")[0], 0)
        self.assertEqual(self.mutations(), [])

    def test_explicit_index_requires_action_and_non_preview(self):
        for args in (("install",), ("update", "--list"), ("update", "--dry-run")):
            with self.assertRaises(ValueError):
                self.invoke(*args, "--refresh-index")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.invoke("update", "--refresh-index", "--yes")[0], 0)
        self.assertEqual(self.mutations()[0], ["update"])

    def test_preview_uses_bounded_info_for_correct_source(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(packages.preview([str(self.brew), "2", "cask", "firefox"]), 0)
        self.assertIn("Installed", output.getvalue())
        self.assertIn("120.0", output.getvalue())
        self.assertEqual(self.calls()[-1]["args"], ["info", "--json=v2", "--cask", "firefox"])

    def test_chinese_selector_has_separate_help_and_aligned_columns(self):
        with patch.dict(os.environ, {"MACTOOLS_LANG": "zh"}):
            self.invoke("install", "--dry-run")
        args = json.loads((self.root / "fzf.log").read_text().splitlines()[0])
        header = next(arg for arg in args if arg.startswith("--header="))
        self.assertNotIn('Ctrl+A', header)
        self.assertTrue(any('ctrl-a:select-all' in arg for arg in args if arg.startswith('--bind=')))
        self.assertNotIn("Fuzzy", header)
        self.assertNotIn("新版本", header)
        self.assertNotIn("安装软件", header)
        self.assertNotIn("模糊匹配", header)
        self.assertIn("--info=hidden", args)
        self.assertGreaterEqual(header.count("\n"), 2)
        self.assertIn("--with-nth=5..", args)
        self.assertIn("--preview-window=right:42%:wrap:<50(down:35%:wrap)", args)
        for terminal_width in (160, 200):
            widths = packages.selector_widths("install", terminal_width)
            self.assertGreater(widths[0], 32)
            short = packages.without_color(packages.Row("formula", "fish", "4.9.3").selector_line("install", widths)).split("\t")[4:]
            long = packages.without_color(packages.Row("cask", "font-big-shoulders-stencil-display-sc").selector_line("install", widths)).split("\t")[4:]
            self.assertEqual(len(short[1]), len(long[1]))
            self.assertTrue(short[1].startswith("fish"))
            self.assertTrue(long[1].startswith("font-big-shoulders-stencil-display-sc"))
            self.assertTrue(short[2].endswith("4.9.3 ✓"))
            self.assertTrue(short[2].startswith(" "))

    def test_source_colors_and_english_types_preserve_alignment(self):
        with patch.dict(os.environ, MACTOOLS_LANG='zh', NO_COLOR='', TERM='xterm-256color'):
            for source, name, color, label in (
                ('formula', 'fish', '38;5;75', 'formula'),
                ('formula', 'homebrew/core/fish', '38;5;75', 'formula'),
                ('cask', 'homebrew/cask/firefox', '38;5;75', 'cask'),
                ('formula', 'owner/tools/fish', '38;5;141', 'formula'),
                ('cask', 'owner/apps/firefox', '38;5;141', 'cask'),
            ):
                row = packages.Row(source, name).selector_line('install', (32, 18))
                fields = row.split('\t')
                self.assertEqual(fields[1], name)
                self.assertIn('\033[' + color + 'm', fields[4])
                self.assertEqual(packages.without_color(fields[4]).strip(), label)
                plain = packages.without_color(row).split('\t')
                self.assertEqual(len(plain[4]), 8)
                self.assertEqual(len(plain[5]), 32)
            with patch.dict(os.environ, NO_COLOR='1'):
                self.assertNotIn('\033[', packages.Row('formula', 'fish').selector_line('install'))

    def test_white_names_and_green_installed_checks_keep_version_columns_aligned(self):
        for action in ('install', 'remove', 'update'):
            for no_color in ('', '1'):
                with self.subTest(action=action, no_color=no_color), patch.dict(
                    os.environ, NO_COLOR=no_color, TERM='xterm-256color'
                ):
                    installed = packages.Row('formula', 'owner/tools/fish', '4.9.3', '5.0.0')
                    missing = packages.Row('formula', 'fish')
                    colored = installed.selector_line(action, (32, 18)).split('\t')
                    present = packages.without_color(installed.selector_line(action, (32, 18))).split('\t')
                    absent = packages.without_color(missing.selector_line(action, (32, 18))).split('\t')
                    self.assertTrue(present[6].endswith('4.9.3 ✓'))
                    self.assertNotIn('✓', absent[6])
                    self.assertEqual(len(present[6]), len(absent[6]))
                    if not no_color:
                        self.assertIn('\033[37m', colored[5])
                        self.assertNotIn('38;5;141', colored[5])
                        self.assertIn('\033[38;5;78m✓', colored[6])
                    else:
                        self.assertNotIn('\033[', '\t'.join(colored))
                    if action == 'update':
                        self.assertEqual(len(present[7]), len(absent[7]))
                    self.assertEqual(packages.Row.parse('\t'.join(colored)), installed)

    def test_long_installed_versions_leave_room_for_check(self):
        row = packages.Row('formula', 'cmake', '4.4.0, 4.4.2, 4.4.3, 4.4.4')
        line = row.selector_line('install', (32, 18))
        current = packages.without_color(line).split('\t')[6]
        self.assertEqual(len(current), 20)
        self.assertTrue(current.endswith('… ✓'))
        self.assertEqual(packages.Row.parse(line), row)

    def cask_fixture(self, actual='4.0.10', target='4.0.15'):
        installed = {'token': 'cc-switch', 'full_token': 'owner/apps/cc-switch',
                     'installed': '3.19.2', 'bundle_short_version': actual,
                     'bundle_version': '40010', 'version': target, 'tap': 'owner/apps'}
        outdated = {'name': 'cc-switch', 'installed_versions': ['3.19.2'], 'current_version': target}
        return installed, outdated

    def test_cask_inventory_uses_actual_bundle_version_for_install_and_remove(self):
        item, _ = self.cask_fixture()
        queries = packages.Queries(str(self.brew), 2)
        for action in ('install', 'remove'):
            with patch.object(queries, 'capture', return_value=json.dumps({'casks': [item]})):
                self.assertEqual(packages.json_inventory(queries, action, ['cask']),
                                 [packages.Row('cask', 'owner/apps/cc-switch', '4.0.10')])

    def test_cask_updates_join_actual_versions_and_preserve_tap(self):
        item, outdated = self.cask_fixture()
        queries = packages.Queries(str(self.brew), 2)
        with patch.object(queries, 'capture', side_effect=[json.dumps({'casks': [outdated]}), json.dumps({'casks': [item]})]):
            self.assertEqual(packages.json_inventory(queries, 'update', ['cask']),
                             [packages.Row('cask', 'owner/apps/cc-switch', '4.0.10', '4.0.15')])

    def test_self_updated_casks_are_not_offered_older_or_equal_brew_versions(self):
        queries = packages.Queries(str(self.brew), 2)
        for actual in ('4.0.15', '4.0.15.0', '4.0.16'):
            item, outdated = self.cask_fixture(actual=actual)
            with self.subTest(actual=actual), patch.object(queries, 'capture', side_effect=[
                json.dumps({'casks': [outdated]}), json.dumps({'casks': [item]})
            ]):
                self.assertEqual(packages.json_inventory(queries, 'update', ['cask']), [])

    def test_cask_version_fallback_and_build_comparisons_are_conservative(self):
        item, _ = self.cask_fixture()
        item.pop('bundle_short_version')
        self.assertEqual(packages.cask_installed_version(item), '3.19.2')
        self.assertFalse(packages.cask_already_current(item, '4.0.15'))
        item.pop('bundle_version')
        self.assertEqual(packages.cask_installed_version(item), '3.19.2')
        item['bundle_short_version'] = '4.0.16-beta'
        self.assertFalse(packages.cask_already_current(item, '4.0.15'))
        item['bundle_short_version'] = '4.0.15'
        self.assertFalse(packages.cask_already_current(item, '4.0.15,123'))
        item['bundle_version'] = '124'
        self.assertTrue(packages.cask_already_current(item, '4.0.15,123'))
        item['bundle_version'] = '122'
        self.assertFalse(packages.cask_already_current(item, '4.0.15,123'))
        item['installed'] = None
        self.assertEqual(packages.cask_installed_version(item), '-')
        self.assertFalse(packages.cask_already_current(item, '4.0.15'))

    def test_cask_preview_separates_actual_version_record_and_brew_channel(self):
        item, _ = self.cask_fixture()
        for lang in ('zh', 'en'):
            with patch.dict(os.environ, MACTOOLS_LANG=lang), patch.object(
                packages.Queries, 'capture', return_value=json.dumps({'casks': [item]})
            ), contextlib.redirect_stdout(io.StringIO()) as output:
                packages.preview([str(self.brew), '2', 'cask', 'owner/apps/cc-switch'])
            preview = output.getvalue()
            for value in ('4.0.10', '3.19.2', '4.0.15', 'owner/apps'):
                self.assertIn(value, preview)
            self.assertIn('Homebrew installation record' if lang == 'en' else 'Homebrew 安装记录', preview)
            self.assertIn('Homebrew available version' if lang == 'en' else 'Homebrew 可用版本', preview)

    def test_chinese_preview_uses_localized_metadata_labels(self):
        with patch.dict(os.environ, {"MACTOOLS_LANG": "zh"}), contextlib.redirect_stdout(io.StringIO()) as output:
            packages.preview([str(self.brew), "2", "formula", "git"])
        self.assertIn("已安装", output.getvalue())
        self.assertIn("命令行工具", output.getvalue())
        self.assertNotIn("Installed", output.getvalue())

    def test_malformed_cache_is_ignored_and_refreshed(self):
        queries = packages.Queries(str(self.brew), 2)
        self.addCleanup(queries.close)
        cache = packages.cache_dir() / "packages-formula.json"
        cache.parent.mkdir(parents=True)
        payloads = [[], None, {}, {"brew": str(self.brew), "names": "fzf", "time": 1}, {"brew": str(self.brew), "names": [], "time": "bad"}, {"brew": str(self.brew), "names": ["../bad"], "time": 1}]
        for payload in payloads:
            with self.subTest(payload=payload):
                cache.write_text(json.dumps(payload))
                with patch.object(queries, "capture", return_value="fzf\n"):
                    self.assertEqual(packages.catalog(queries, "formula", writable=False), ["fzf"])
        cache.write_text("[]")
        opts = packages.parser().parse_intermixed_args(["install", "--formula", "--dry-run"])
        opts.sources = ["formula"]
        with patch.object(queries, "capture", side_effect=lambda *args: '{"formulae": [], "casks": []}' if args[0] == "info" else "fzf\n"):
            self.assertEqual(list(packages.rows(queries, opts)), [packages.Row("formula", "fzf")])

    def test_malformed_inventory_has_clear_validation_error(self):
        queries = packages.Queries(str(self.brew), 2)
        self.addCleanup(queries.close)
        for payload in ([], None, {"formulae": None}, {"formulae": ["bad"]}, {"casks": [None]}):
            with self.subTest(payload=payload), patch.object(queries, "capture", return_value=json.dumps(payload)):
                with self.assertRaisesRegex(ValueError, "Invalid Homebrew JSON inventory"):
                    packages.json_inventory(queries, "remove", ["formula", "cask"])

    def test_tapped_cask_update_keeps_installed_identity(self):
        with patch.dict(os.environ, {"CASK_FULL_TOKEN": "owner/apps/firefox", "FZF_SELECT": "owner/apps/firefox"}):
            code, output = self.invoke("update", "--cask", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("owner/apps/firefox", output)
        self.assertEqual(self.mutations(), [["upgrade", "--cask", "owner/apps/firefox"]])
        arguments = [call['args'] for call in self.calls()]
        self.assertIn(['outdated', '--json=v2', '--cask'], arguments)
        self.assertIn(['info', '--json=v2', '--installed', '--cask'], arguments)

    def test_ui_can_select_installed_row_without_waiting_for_catalog(self):
        self.fzf.write_text("#!" + sys.executable + "\nimport sys\nrow=sys.stdin.readline().strip()\nprint('\\n\\n'+row)\n")
        with patch.dict(os.environ, {"SLOW_COMMAND": "formulae"}):
            started = time.monotonic()
            code, output = self.invoke("install", "--formula", "--dry-run", "--timeout", "5")
        self.assertEqual(code, 0)
        self.assertIn("install --formula git", output)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(self.mutations(), [])

    def test_timeout_also_stops_children_holding_query_pipes(self):
        with patch.dict(os.environ, {"SLOW_CHILD_COMMAND": "outdated"}):
            started = time.monotonic()
            self.assertEqual(self.invoke("update", "--list", "--timeout", "0.15")[0], 124)
            self.assertLess(time.monotonic() - started, 2)

    @unittest.skipUnless(REAL_FZF, "host fzf unavailable")
    def test_real_fzf_searches_only_package_name_and_exact_toggle(self):
        rows = "\n".join(row.selector_line("update") for row in (
            packages.Row("formula", "git", "1", "2"), packages.Row("formula", "g-i-t", "1", "2"),
            packages.Row("formula", "other", "git", "git"))) + "\n"
        env = dict(os.environ)
        env.pop("FZF_DEFAULT_OPTS", None)
        env.pop("FZF_DEFAULT_OPTS_FILE", None)
        argv = [REAL_FZF, "--ansi", "--multi", "--delimiter=\t", "--nth=2", "--with-nth=5..", "--tabstop=1", "--filter=git"]
        fuzzy = subprocess.run(argv, input=rows, text=True, capture_output=True, env=env, check=True)
        exact = subprocess.run(argv + ["--exact"], input=rows, text=True, capture_output=True, env=env, check=True)
        self.assertEqual({line.split("\t")[1] for line in fuzzy.stdout.splitlines()}, {"git", "g-i-t"})
        self.assertEqual({line.split("\t")[1] for line in exact.stdout.splitlines()}, {"git"})
        self.assertEqual(packages.Row.parse(exact.stdout.strip()), packages.Row("formula", "git", "1", "2"))

    @unittest.skipUnless(REAL_FZF, 'host fzf unavailable')
    def test_real_fzf_colored_rows_keep_selection_metadata(self):
        with patch.dict(os.environ, NO_COLOR='', TERM='xterm-256color'):
            rows = [packages.Row('formula', 'fish'), packages.Row('formula', 'owner/tools/fish')]
            data = '\n'.join(row.selector_line('install') for row in rows) + '\n'
            env = dict(os.environ)
            env.pop('FZF_DEFAULT_OPTS', None)
            env.pop('FZF_DEFAULT_OPTS_FILE', None)
            result = subprocess.run([REAL_FZF, '--ansi', '--delimiter=\t', '--nth=2',
                                     '--with-nth=5..', '--filter=fish'], input=data, text=True,
                                    capture_output=True, check=True, env=env)
            self.assertEqual({packages.Row.parse(line) for line in result.stdout.splitlines()}, set(rows))
            self.assertEqual({packages.without_color(line) for line in result.stdout.splitlines()},
                             {packages.without_color(row.selector_line('install')) for row in rows})

    def test_selection_and_timeout_validation(self):
        for value in ("nan", "inf", "0", "-1"):
            with self.assertRaises(ValueError):
                self.invoke("remove", "--list", "--timeout", value)
            with self.assertRaises(ValueError):
                self.invoke("remove", "--list", "--scan-timeout", value)
        with self.assertRaises(ValueError):
            self.invoke("install", "--list", "--scan-timeout", "240")
        for name in ("--zap", "../user", "hello\nworld", "$(touch /tmp/unsafe)"):
            with self.assertRaises(ValueError):
                packages.package_name(name)
        self.assertTrue(packages.matches("visual-studio-code", "vsc"))
        self.assertFalse(packages.matches("visual-studio-code", "vsc", exact=True))


if __name__ == "__main__":
    unittest.main()
