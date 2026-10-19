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
from unittest.mock import patch

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
if args[0] == "formulae":
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

    def write_snapshot(self, action, result, sources=None):
        snapshot = self.root / ('cache/mactools/selector-' + action + '.json')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps({'brew': str(self.brew), 'time': time.time(),
            'sources': sources or ['formula', 'cask'], 'rows': [row.line() for row in result]}))
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
        for name in ("--zap", "../user", "hello\nworld", "$(touch /tmp/unsafe)"):
            with self.assertRaises(ValueError):
                packages.package_name(name)
        self.assertTrue(packages.matches("visual-studio-code", "vsc"))
        self.assertFalse(packages.matches("visual-studio-code", "vsc", exact=True))


if __name__ == "__main__":
    unittest.main()
