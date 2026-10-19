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
        self.assertIn("Package metadata", output.getvalue())
        self.assertEqual(self.calls()[-1]["args"], ["info", "--cask", "firefox"])

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
        rows = "formula\tgit\t1\t2\nformula\tg-i-t\t1\t2\nformula\tother\tgit\tgit\n"
        env = dict(os.environ)
        env.pop("FZF_DEFAULT_OPTS", None)
        env.pop("FZF_DEFAULT_OPTS_FILE", None)
        argv = [REAL_FZF, "--multi", "--delimiter=\t", "--nth=2", "--with-nth=1,2,3,4", "--filter=git"]
        fuzzy = subprocess.run(argv, input=rows, text=True, capture_output=True, env=env, check=True)
        exact = subprocess.run(argv + ["--exact"], input=rows, text=True, capture_output=True, env=env, check=True)
        self.assertEqual({line.split("\t")[1] for line in fuzzy.stdout.splitlines()}, {"git", "g-i-t"})
        self.assertEqual({line.split("\t")[1] for line in exact.stdout.splitlines()}, {"git"})

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
