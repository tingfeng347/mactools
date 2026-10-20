"""Mole integration: real vendored engine, disposable HOME and fake owners."""
import contextlib
import hashlib
import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import mactools_mole as mole
import mactools_system as system


class Bundle(unittest.TestCase):
    def test_bundle_is_exact_source_and_includes_license(self):
        bundle = ROOT / 'bin/mactools_mole.zip'
        self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), mole.BUNDLE_SHA256)
        with zipfile.ZipFile(bundle) as archive:
            paths = {p.relative_to(ROOT / 'third_party/mole').as_posix(): p
                     for p in (ROOT / 'third_party/mole').rglob('*') if p.is_file()}
            self.assertEqual(set(archive.namelist()), set(paths))
            for name, path in paths.items():
                self.assertEqual(archive.read(name), path.read_bytes(), name)
            self.assertIn(b'GNU GENERAL PUBLIC LICENSE', archive.read('LICENSE'))

    def test_menu_and_list_never_start_engine_or_query_inventory(self):
        with patch.object(mole, 'run_engine', side_effect=AssertionError('started engine')), patch.object(system, 'brew_path', side_effect=AssertionError('queried brew')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--generate', '--dev']), 0)
            self.assertEqual(system.main(['clean', '--exact', 'browser-cache', '--list']), 0)
        self.assertIn('ai-cache', output.getvalue())
        self.assertIn('ma clean --exact browser-cache', output.getvalue())

    def test_incomplete_or_modified_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mole, 'BUNDLE_SHA256', 'bad'):
            with self.assertRaisesRegex(ValueError, 'incomplete or changed'):
                mole.unpack_bundle(Path(tmp))

    def test_timeout_stops_child_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'late'
            with self.assertRaises(subprocess.TimeoutExpired):
                mole.run_engine(['/bin/bash', '-c', '(sleep 0.5; touch "$1") & wait', '_', str(marker)], os.environ.copy(), 0.05)
            time.sleep(0.6)
            self.assertFalse(marker.exists())

    def test_fzf_cancelling_preview_stops_scanner_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready, marker = Path(tmp) / 'ready', Path(tmp) / 'late'
            code = '''import sys
sys.path.insert(0, sys.argv[1])
import mactools_mole as mole
import os
mole.run_engine(['/bin/bash', '-c', 'touch "$1"; (sleep 0.5; touch "$2") & wait', '_', sys.argv[2], sys.argv[3]], os.environ.copy(), 10)
'''
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


@unittest.skipUnless(sys.platform == 'darwin', 'Mole uses BSD/macOS tools')
class Engine(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ma-mole-fixture-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'),
                        XDG_CACHE_HOME=str(self.home / '.cache'), NO_COLOR='1',
                        MACTOOLS_LANG='en', MOLE_TEST_NO_AUTH='1')
        self.output = ''

    def file(self, name, content=b'x' * 4096):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def launch(self, argv, env, timeout):
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        self.output += result.stdout + result.stderr
        return result.returncode

    @contextlib.contextmanager
    def plan(self, name):
        with patch.dict(os.environ, self.env), patch.object(mole, 'run_engine', side_effect=self.launch):
            plan = mole.MolePlan(name, 30)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    yield plan
            finally:
                plan.close()

    def test_preview_is_read_only_and_apply_cleans_only_reviewed_cache(self):
        cache = self.file('.cache/vite/old')
        protected = self.file('Documents/document.txt')
        with self.plan('frontend-cache') as plan:
            plan.preview()
            self.assertTrue(cache.exists())
            fresh = self.file('.cache/webpack/new')
            plan.apply(30)
            self.assertFalse(cache.exists(), self.output)
            self.assertTrue(fresh.exists(), self.output)
        self.assertTrue(protected.exists())
        self.assertFalse((self.home / '.config/mole').exists())
        self.assertFalse((self.home / 'Library/Logs/mole').exists())

    def test_replaced_path_and_symlink_target_are_preserved(self):
        cache = self.file('.cache/vite/old')
        outside = self.file('Documents/important')
        with self.plan('frontend-cache') as plan:
            plan.preview()
            cache.rename(cache.with_name('moved'))
            cache.symlink_to(outside)
            plan.apply(30)
        self.assertTrue(cache.is_symlink())
        self.assertEqual(outside.read_bytes(), b'x' * 4096)

    def test_whitelist_and_user_data_are_preserved(self):
        cache = self.file('Library/Caches/demo/payload')
        trash = self.file('.Trash/recoverable')
        recent = self.file('Library/Preferences/com.apple.recentitems.plist')
        self.file('.config/mactools/mole/whitelist', str(cache.parent).encode() + b'\n')
        with self.plan('user-cache') as plan:
            plan.preview()
            plan.apply(30)
        for path in (cache, trash, recent):
            self.assertTrue(path.exists(), self.output)

    def test_owner_command_is_previewed_then_applied_with_record(self):
        self.file('.cargo/registry/cache/crates.io/package.crate')
        self.file('.cargo/registry/src/crates.io/source.rs')
        with self.plan('rust-cache') as plan:
            plan.preview()
            self.assertTrue((self.home / '.cargo/registry/cache/crates.io/package.crate').exists())
            plan.apply(30)
        self.assertTrue((self.home / '.cargo/registry/src/crates.io/source.rs').exists())
        self.assertFalse((self.home / '.cargo/registry/cache/crates.io/package.crate').exists(), self.output)
        logs = self.home / 'Library/Logs/mactools/mole/operations.log'
        self.assertIn('REMOVED', logs.read_text())

    def test_installer_preview_and_apply_preserve_ordinary_archives(self):
        installer = self.file('Downloads/setup.dmg')
        archive = self.home / 'Downloads/documents.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('report.txt', 'keep')
        with self.plan('installers') as plan:
            plan.preview()
            self.assertTrue(installer.exists())
            plan.apply(30)
        self.assertFalse(installer.exists(), self.output)
        self.assertTrue(archive.exists())

    def test_apply_requires_successful_preview(self):
        with self.plan('frontend-cache') as plan:
            with self.assertRaisesRegex(ValueError, 'Preview'):
                plan.apply(30)

    def test_failed_preview_never_authorizes_apply(self):
        with self.plan('frontend-cache') as plan, patch.object(mole, 'run_engine', return_value=124):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                plan.preview()
            with self.assertRaisesRegex(ValueError, 'Preview'):
                plan.apply(30)

    def test_replaced_regular_file_is_not_authorized(self):
        cache = self.file('.cache/vite/old')
        with self.plan('frontend-cache') as plan:
            plan.preview()
            replacement = self.file('.cache/vite/replacement', b'new cache')
            os.replace(replacement, cache)
            plan.apply(30)
        self.assertEqual(cache.read_bytes(), b'new cache', self.output)

    def test_inactive_project_dependencies_are_previewed_before_removal(self):
        project = self.home / 'Projects/demo'
        self.file('Projects/demo/package.json', b'{"name":"demo"}')
        dependency = self.file('Projects/demo/node_modules/example/index.js')
        source = self.file('Projects/demo/src/main.js', b'keep source')
        self.file('.config/mactools/mole/purge_paths', str(project).encode() + b'\n')
        old = time.time() - 20 * 86400
        for path in sorted(project.rglob('*'), reverse=True):
            os.utime(path, (old, old))
        os.utime(project, (old, old))
        with self.plan('project-artifacts') as plan:
            plan.preview()
            self.assertTrue(dependency.exists())
            plan.apply(30)
        self.assertFalse(dependency.exists(), self.output)
        self.assertEqual(source.read_bytes(), b'keep source')

    def test_category_smoke_previews_never_delete(self):
        marker = self.file('Documents/keep')
        for name in mole.mole_info():
            with self.subTest(name=name), self.plan(name) as plan:
                plan.preview()
                self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
