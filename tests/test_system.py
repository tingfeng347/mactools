import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import mactools_system as system


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.commands = []
        # Existing Homebrew tests must not discover tools or caches on the host.
        patcher = patch.object(system, 'development_plan', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        # These tests cover Homebrew; Mole integration has its own isolated HOME.
        patcher = patch.object(system, 'mole_info', return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_run(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        output = 'hello\n' if argv[1:2] == ['list'] else '/opt/homebrew\n'
        return subprocess.CompletedProcess(argv, 0, output, '')

    def test_offline_doctor_never_fetches_or_mutates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(system, 'cache_dir', return_value=Path(tmp)), patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), patch.object(system, 'fetch', side_effect=AssertionError('network in offline mode')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['doctor', '--offline']), 0)
        self.assertIn('SKIP Remote connectivity', output.getvalue())
        self.assertIn('Homebrew doctor', output.getvalue())
        self.assertTrue(all(kw['timeout'] <= 15 for _, kw in self.commands))
        self.assertFalse(any(c in argv for argv, _ in self.commands for c in ('install', 'update', 'upgrade', 'cleanup')))

    def test_missing_brew_fails(self):
        with patch.object(system, 'brew_path', side_effect=ValueError('missing')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(system.main(['doctor', '--offline']), 1)

    def test_timed_out_doctor_fails_and_continues(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=subprocess.TimeoutExpired('stub', 1)), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['doctor', '--offline', '--timeout', '0.01']), 1)
        self.assertIn('FAIL', output.getvalue())
        self.assertIn('SKIP Remote connectivity', output.getvalue())

    def test_only_brew_doctor_timeout_is_failure(self):
        def run(argv, **kwargs):
            if argv[1:2] == ['doctor']:
                raise subprocess.TimeoutExpired(argv, 0.01)
            return self.fake_run(argv, **kwargs)
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['doctor', '--offline']), 1)
        self.assertIn('FAIL Homebrew doctor', output.getvalue())

    def test_clean_list_is_read_only(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--list', '--cask']), 0)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.commands[0][0], ['/stub/brew', 'list', '--cask', '--full-name', '-1'])
        self.assertNotIn('autoremove', output.getvalue())

    def test_clean_menu_generation_needs_no_inventory(self):
        with patch.object(system, 'brew_path', side_effect=AssertionError('menu queried brew')), patch.object(system, 'run', side_effect=AssertionError('menu queried inventory')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--generate', '--cask']), 0)
        self.assertEqual(output.getvalue().splitlines(), ['cask-cache'])

    def test_autoremove_plan_needs_no_package_lists(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=AssertionError('unrelated inventory')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', 'autoremove', '--list']), 0)
        self.assertIn('autoremove\t/stub/brew autoremove', output.getvalue())

    def test_clean_preview_only_queries_selected_scope(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(system.main(['clean', '--preview-action', 'cask-cache']), 0)
        self.assertEqual([argv for argv, _ in self.commands], [
            ['/stub/brew', 'list', '--cask', '--full-name', '-1'],
            ['/stub/brew', 'cleanup', '--dry-run', '--', 'homebrew/cask/hello'],
        ])

    def test_clean_queries_inventory_after_selection_and_cancel_queries_nothing(self):
        for cancelled in (False, True):
            self.commands = []
            menu_opened = False

            def run(argv, **kwargs):
                nonlocal menu_opened
                if argv[0] == 'fzf':
                    self.assertFalse(self.commands, 'inventory blocked menu startup')
                    menu_opened = True
                    row = next(row for row in kwargs['input'].splitlines() if row.startswith('cask-cache\t'))
                    return subprocess.CompletedProcess(argv, 130 if cancelled else 0, '\n\n' + row + '\n', '')
                self.assertTrue(menu_opened)
                return self.fake_run(argv, **kwargs)

            with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system.shutil, 'which', return_value='/stub/fzf'), patch.object(system, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(system.main(['clean', '--dry-run']), 0)
            inventories = [argv for argv, _ in self.commands if argv[1] == 'list']
            self.assertEqual(inventories, [] if cancelled else [['/stub/brew', 'list', '--cask', '--full-name', '-1']])

    def test_clean_selector_matches_package_layout_and_ignores_global_options(self):
        menus = []

        def run(argv, **kwargs):
            if argv[0] == 'fzf':
                menus.append(argv)
                self.assertNotIn('FZF_DEFAULT_OPTS', kwargs['env'])
                self.assertNotIn('FZF_DEFAULT_OPTS_FILE', kwargs['env'])
                self.assertEqual(kwargs['env']['SHELL'], '/bin/sh')
                for option in ('--layout=reverse', '--border', '--height=95%', '--info=hidden',
                               '--preview-window=right:42%:wrap:<50(down:35%:wrap)'):
                    self.assertIn(option, argv)
                self.assertIn('ctrl-a:select-all', next(a for a in argv if a.startswith('--bind=')))
                header = next(a for a in argv if a.startswith('--header='))
                self.assertNotIn('Ctrl+A', header)
                if len(menus) == 1:
                    return subprocess.CompletedProcess(argv, 0, 'cache\nctrl-e\n', '')
                self.assertIn('--exact', argv)
                self.assertIn('--query=cache', argv)
                if len(menus) == 2:
                    return subprocess.CompletedProcess(argv, 0, 'cache\nctrl-r\n', '')
                return subprocess.CompletedProcess(argv, 130, '', '')
            raise AssertionError('cancelled menu queried inventory')

        with patch.dict(os.environ, {'FZF_DEFAULT_OPTS': '--layout=default', 'FZF_DEFAULT_OPTS_FILE': '/stub/options'}), patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system.shutil, 'which', return_value='/stub/fzf'), patch.object(system, 'run', side_effect=run):
            self.assertEqual(system.main(['clean']), 0)
        self.assertEqual(len(menus), 3)

    def test_clean_selector_rejects_unknown_row(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system.shutil, 'which', return_value='/stub/fzf'), patch.object(system, 'run', return_value=subprocess.CompletedProcess('fzf', 0, '\n\nforged-action\n', '')):
            with self.assertRaisesRegex(ValueError, 'invalid cleanup selection'):
                system.main(['clean'])

    def test_clean_preview_groups_skipped_warnings_but_preserves_other_output(self):
        def run(argv, **kwargs):
            if argv[1] == 'list':
                return self.fake_run(argv, **kwargs)
            self.assertIn('--dry-run', argv)
            self.assertTrue(kwargs['capture_output'])
            return subprocess.CompletedProcess(argv, 0, 'Would remove: /cache/hello (1MB)\n',
                'Warning: Skipping hello: most recent version 2.0 not installed\n'
                'Warning: Skipping other: most recent version 3.0 not installed\n'
                'Warning: another diagnostic\n')

        with patch.dict(os.environ, {'MACTOOLS_LANG': 'zh'}), patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--preview-action', 'formula-cache']), 0)
        self.assertIn('2 项尚未安装最新版', output.getvalue())
        self.assertNotIn('Warning: Skipping', output.getvalue())
        self.assertIn('Would remove: /cache/hello (1MB)', output.getvalue())
        self.assertIn('Warning: another diagnostic', output.getvalue())
        self.assertNotIn('> /stub/brew', output.getvalue())

    def test_failed_clean_detail_preserves_all_diagnostics(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', return_value=subprocess.CompletedProcess('brew', 2, '', 'Warning: Skipping hello: most recent version 2.0 not installed\nError: unavailable\n')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--preview-action', 'autoremove']), 2)
        self.assertIn('Error: unavailable', output.getvalue())
        self.assertIn('Warning: Skipping', output.getvalue())

    def test_clean_preview_never_mutates(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(system.main(['clean', '--dry-run', '--yes', '--formula']), 0)
        previews = [a for a, _ in self.commands if a[1] != 'list']
        self.assertEqual(len(previews), 2)
        self.assertTrue(all('--dry-run' in a for a in previews))
        self.assertFalse(any('--zap' in a or 'sudo' in a for a, _ in self.commands))

    @unittest.skipUnless(shutil.which('fzf'), 'host fzf unavailable')
    def test_clean_selector_bindings_work_with_real_fzf(self):
        def run(argv, **kwargs):
            if argv[0] == 'fzf':
                # Filter mode parses the real selector options without opening
                # a terminal or executing the preview/reload commands.
                result = subprocess.run([*argv, '--filter=' + system.cleanup_info('formula-cache')[1]], **kwargs)
                # --filter omits the expect-key line that interactive fzf emits.
                query, _, rows = result.stdout.partition('\n')
                result.stdout = query + '\n\n' + rows
                return result
            return self.fake_run(argv, **kwargs)
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(system.main(['clean', '--dry-run']), 0)
        previews = [argv for argv, _ in self.commands if argv[1] != 'list']
        self.assertEqual(previews, [['/stub/brew', 'cleanup', '--dry-run', '--', 'homebrew/core/hello']])

    def test_cleanup_disambiguates_formula_and_cask_tokens(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()):
            system.main(['clean', '--dry-run', '--yes', '--formula', '--cask'])
        previews = [a for a, _ in self.commands if a[1] == 'cleanup']
        self.assertEqual(previews[0][-1], 'homebrew/core/hello')
        self.assertEqual(previews[1][-1], 'homebrew/cask/hello')

    def test_failed_preview_prevents_execution(self):
        def failed(argv, **kwargs):
            self.commands.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, int('--dry-run' in argv), 'hello\n', '')
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=failed), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(system.main(['clean', '--yes']), 1)
        self.assertTrue(all('--dry-run' in a for a, _ in self.commands if a[1] != 'list'))

    def test_clean_confirmation_decline_and_apply(self):
        for confirmed in (False, True):
            self.commands = []
            with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), patch.object(system, 'confirm', return_value=confirmed), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(system.main(['clean', 'autoremove']), 0)
            applied = [a for a, _ in self.commands if a[1] == 'autoremove' and '--dry-run' not in a]
            self.assertEqual(len(applied), int(confirmed))

    def test_fake_brew_executable_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'log'
            brew = Path(tmp) / 'brew'
            brew.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$STUB_LOG"\ncase "$1" in list) echo hello;; esac\n')
            brew.chmod(0o755)
            with patch.object(system, 'brew_path', return_value=str(brew)), patch.dict(os.environ, {'STUB_LOG': str(log)}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(system.main(['clean', '--formula', '--dry-run', '--yes']), 0)
            lines = log.read_text().splitlines()
            self.assertEqual(lines, ['list --formula --full-name -1', 'cleanup --dry-run -- homebrew/core/hello', 'autoremove --dry-run'])


if __name__ == '__main__':
    unittest.main()
