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
import mactools_system as system


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.commands = []

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

    def test_clean_list_is_read_only(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system.main(['clean', '--list', '--cask']), 0)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.commands[0][0], ['/stub/brew', 'list', '--cask', '--full-name', '-1'])
        self.assertNotIn('autoremove', output.getvalue())

    def test_clean_preview_never_mutates(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(system.main(['clean', '--dry-run', '--yes', '--formula']), 0)
        previews = [a for a, _ in self.commands if a[1] != 'list']
        self.assertEqual(len(previews), 2)
        self.assertTrue(all('--dry-run' in a for a in previews))
        self.assertFalse(any('--zap' in a or 'sudo' in a for a, _ in self.commands))

    def test_cleanup_disambiguates_formula_and_cask_tokens(self):
        with patch.object(system, 'brew_path', return_value='/stub/brew'), patch.object(system, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()):
            system.main(['clean', '--dry-run', '--yes'])
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
