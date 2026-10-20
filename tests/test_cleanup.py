"""Developer cleanup uses fake tools and disposable cache directories only."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import mactools_cleanup as cleanup
import mactools_system as system


class DeveloperCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.cache = self.home / 'cache'
        self.cache.mkdir()
        (self.cache / 'artifact').write_bytes(b'123456')
        (self.cache / '_cacache').mkdir()
        (self.cache / '_cacache/entry').write_bytes(b'123456')
        patcher = patch.object(Path, 'home', return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.commands = []

    def query(self, argv, **kwargs):
        self.commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, str(self.cache) + '\n', '')

    def test_static_menu_contains_developer_and_deep_cleanup_without_queries(self):
        with patch.object(system, 'run', side_effect=AssertionError('menu started a process')), patch.object(cleanup, 'run', side_effect=AssertionError('menu started a cache query')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--generate']), 0)
        names = output.getvalue().splitlines()
        self.assertIn('autoremove', names)
        self.assertIn('brew-cache', names)
        self.assertTrue(set(cleanup.cache_info()).issubset(names))

    def test_dev_filter_and_scope_flags(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--dev', '--generate']), 0)
        self.assertEqual(output.getvalue().splitlines(), list(cleanup.cache_info()))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            system.main(['clean', '--dev', '--cask'])

    def test_native_cache_previews_never_run_mutating_commands(self):
        for name in cleanup.cache_info():
            if name == 'xcode-derived':
                continue
            self.commands = []
            with self.subTest(name=name), patch.object(cleanup.shutil, 'which', return_value='/stub/tool'), patch.object(cleanup, 'run', side_effect=self.query), patch.object(system, 'brew_path', side_effect=AssertionError('developer action queried brew')), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(system.main(['clean', name, '--dry-run']), 0)
            self.assertEqual(len(self.commands), 1)
            self.assertEqual((self.cache / 'artifact').read_bytes(), b'123456')
            self.assertIn(str(self.cache), output.getvalue())
            self.assertIn('6.0 B' if name == 'npm-cache' else '12.0 B', output.getvalue())

    def test_detail_preview_and_list_are_read_only(self):
        for args in (['--preview-action', 'npm-cache'], ['npm-cache', '--list']):
            self.commands = []
            with patch.object(cleanup.shutil, 'which', return_value='/stub/npm'), patch.object(cleanup, 'run', side_effect=self.query), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(system.main(['clean', *args]), 0)
            self.assertEqual(self.commands, [['/stub/npm', 'config', 'get', 'cache']])

    def test_each_native_command_runs_only_after_confirmation(self):
        expected = {
            'pip-cache': ['--cache-dir', str(self.cache), 'cache', 'purge'],
            'npm-cache': ['cache', 'clean', '--force', '--cache', str(self.cache)],
            'pnpm-store': ['store', 'prune'],
            'uv-cache': ['cache', 'prune', '--cache-dir', str(self.cache)],
            'go-cache': ['clean', '-cache'],
            'ccache': ['--dir', str(self.cache), '--clear'],
        }
        for name, arguments in expected.items():
            for confirmed in (False, True):
                self.commands = []
                def fake_run(argv, **kwargs):
                    if argv == ['/stub/tool', *arguments]:
                        self.assertTrue(confirmed)
                        self.assertEqual(kwargs['env']['GOTOOLCHAIN'], 'local')
                        self.assertEqual(kwargs['env']['COREPACK_ENABLE_NETWORK'], '0')
                        self.assertEqual(kwargs['cwd'], str(self.home))
                        if name == 'go-cache':
                            self.assertEqual(kwargs['env']['GOCACHE'], str(self.cache))
                    return self.query(argv, **kwargs)
                with self.subTest(name=name, confirmed=confirmed), patch.object(cleanup.shutil, 'which', return_value='/stub/tool'), patch.object(cleanup, 'run', side_effect=fake_run), patch.object(system, 'confirm', return_value=confirmed) as confirm, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(system.main(['clean', name]), 0)
                self.assertEqual(self.commands.count(['/stub/tool', *arguments]), int(confirmed))
                confirm.assert_called_once()

    def test_changed_directory_after_confirmation_prevents_apply(self):
        paths = iter((str(self.cache), str(self.home / 'other-cache')))
        def changed(argv, **kwargs):
            self.commands.append(argv)
            return subprocess.CompletedProcess(argv, 0, next(paths) + '\n', '')
        with patch.object(cleanup.shutil, 'which', return_value='/stub/npm'), patch.object(cleanup, 'run', side_effect=changed), contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, '配置已变化|configuration changed'):
            system.main(['clean', 'npm-cache', '--yes'])
        self.assertEqual(len(self.commands), 2)
        self.assertTrue(all(argv[1:3] == ['config', 'get'] for argv in self.commands))

    def test_missing_optional_tool_does_not_block_available_tool(self):
        with patch.object(cleanup.shutil, 'which', side_effect=lambda tool: '/stub/npm' if tool == 'npm' else None), patch.object(cleanup, 'run', side_effect=self.query), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', 'npm-cache', '--dry-run']), 0)
            self.assertIsNone(cleanup.development_plan('ccache', 1))
        self.assertIn('SKIP ccache', output.getvalue())
        self.assertEqual(len(self.commands), 1)

    def test_invalid_paths_and_query_failure_block_execution(self):
        for value in ('/', str(self.home), str(self.home.parent), 'relative', '', '/cache\n/other'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cleanup.cache_path(value)
        with patch.object(cleanup.shutil, 'which', return_value='/stub/npm'), patch.object(cleanup, 'run', return_value=subprocess.CompletedProcess('npm', 1, '', 'lookup failed')), contextlib.redirect_stdout(io.StringIO()), patch.object(system, 'confirm', side_effect=AssertionError('confirmation after failed lookup')), self.assertRaisesRegex(ValueError, 'lookup failed'):
            system.main(['clean', 'npm-cache', '--yes'])

    def test_size_scan_does_not_follow_symlinks_and_reports_partial_results(self):
        outside = self.home / 'outside'
        outside.mkdir()
        (outside / 'large').write_bytes(b'0' * 100)
        (self.cache / 'link').symlink_to(outside, target_is_directory=True)
        self.assertEqual(cleanup.cache_size(self.cache), (12, True))
        self.assertFalse(cleanup.cache_size(self.cache, max_entries=0)[1])

    def test_xcode_preview_preserves_data_and_apply_preserves_archives_and_symlink_targets(self):
        derived = self.home / 'Library/Developer/Xcode/DerivedData'
        build = derived / 'Example/Build'
        build.mkdir(parents=True)
        (build / 'object').write_text('compiled')
        archive = derived.parent / 'Archives/release'
        archive.parent.mkdir()
        archive.write_text('archive')
        (derived / 'external').symlink_to(self.cache, target_is_directory=True)
        with contextlib.redirect_stdout(io.StringIO()):
            system.main(['clean', 'xcode-derived', '--dry-run'])
        self.assertTrue((build / 'object').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            system.main(['clean', 'xcode-derived', '--yes'])
        self.assertEqual(list(derived.iterdir()), [])
        self.assertEqual(archive.read_text(), 'archive')
        self.assertEqual((self.cache / 'artifact').read_bytes(), b'123456')

    def test_xcode_rejects_symlink_in_every_cache_path_component(self):
        for relative in ('Library', 'Library/Developer', 'Library/Developer/Xcode', 'Library/Developer/Xcode/DerivedData'):
            target = self.home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(self.cache, target_is_directory=True)
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                cleanup.clear_xcode()
            target.unlink()
        self.assertEqual((self.cache / 'artifact').read_bytes(), b'123456')

    def test_deep_homebrew_cleanup_uses_native_preview_before_apply(self):
        def brew(argv, **kwargs):
            self.commands.append(argv)
            return subprocess.CompletedProcess(argv, 0, '', '')
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=brew), contextlib.redirect_stdout(io.StringIO()):
            system.main(['clean', 'brew-cache', '--yes'])
        self.assertEqual(self.commands, [
            ['/stub/brew', 'cleanup', '--dry-run', '--prune=all', '--scrub'],
            ['/stub/brew', 'cleanup', '--prune=all', '--scrub'],
        ])

    def test_deep_cleanup_does_not_repeat_scoped_cleanup_or_query_inventory(self):
        with patch.object(system, 'run', side_effect=AssertionError('redundant inventory query')):
            actions = system.cleanup_actions('/stub/brew', type('Args', (), {'formula': False, 'cask': False})(),
                                             ['formula-cache', 'cask-cache', 'brew-cache'])
        self.assertEqual(actions, [('brew-cache', ['/stub/brew', 'cleanup', '--prune=all', '--scrub'])])

    def test_xcode_directory_replaced_by_symlink_after_validation_is_rejected(self):
        derived = self.home / 'Library/Developer/Xcode/DerivedData'
        derived.mkdir(parents=True)
        original_open = os.open
        def raced_open(path, *args, **kwargs):
            if path == 'DerivedData':
                derived.rmdir()
                derived.symlink_to(self.cache, target_is_directory=True)
            return original_open(path, *args, **kwargs)
        with patch.object(cleanup.os, 'open', side_effect=raced_open), self.assertRaises(OSError):
            cleanup.clear_xcode()
        self.assertEqual((self.cache / 'artifact').read_bytes(), b'123456')


if __name__ == '__main__':
    unittest.main()
