"""End-to-end CLI and isolated install/uninstall lifecycle tests."""
import os
import pty
import re
import select
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CLI(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='ma-cli-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = dict(os.environ, XDG_CONFIG_HOME=str(self.root / 'config'),
                        XDG_CACHE_HOME=str(self.root / 'cache'), MACTOOLS_LANG='zh')

    def invoke(self, *args, executable=None, input=None):
        return subprocess.run([str(executable or ROOT / 'bin/ma'), *args], env=self.env,
                              capture_output=True, text=True, input=input, timeout=20)

    def test_help_unknown_and_version(self):
        self.assertIn('maci', self.invoke('--help').stdout)
        self.assertIn('mactools', self.invoke('--version').stdout)
        result = self.invoke('no-such-command')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)

    def test_help_colors_only_in_supported_terminals(self):
        plain = self.invoke('--help').stdout
        self.assertNotIn('\033[', plain)
        for lang in ('zh', 'en'):
            for no_color, term, expected in (('', 'xterm-256color', True),
                                             ('1', 'xterm-256color', False),
                                             ('', 'dumb', False)):
                with self.subTest(lang=lang, no_color=no_color, term=term):
                    master, slave = pty.openpty()
                    try:
                        env = dict(self.env, MACTOOLS_LANG=lang, TERM=term, NO_COLOR=no_color)
                        process = subprocess.Popen([str(ROOT / 'bin/ma')], env=env, stdout=slave,
                                                   stderr=subprocess.PIPE)
                        process.communicate(timeout=20)
                        data = bytearray()
                        while select.select([master], [], [], 0.2)[0]:
                            try:
                                chunk = os.read(master, 65536)
                            except OSError:
                                break
                            if not chunk:
                                break
                            data.extend(chunk)
                        output = data.decode().replace('\r\n', '\n')
                        self.assertEqual(process.returncode, 0)
                        self.assertEqual('\033[' in output, expected)
                        plain_env = dict(env)
                        comparison = subprocess.check_output([str(ROOT / 'bin/ma')], env=plain_env, text=True)
                        self.assertEqual(re.sub(r'\033\[[0-9;]*m', '', output), comparison)
                    finally:
                        os.close(master)
                        if slave is not None:
                            os.close(slave)

    def test_language_persists_for_all_entries(self):
        self.assertEqual(self.invoke('setup', 'en').returncode, 0)
        self.env.pop('MACTOOLS_LANG')
        self.assertIn('Usage:', self.invoke('--help').stdout)
        result = self.invoke('--help', executable=ROOT / 'bin/maci')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.invoke('setup', 'de').returncode, 1)
        self.assertEqual((self.root / 'config/mactools/language').read_text(), 'en\n')

    def test_all_shortcuts_match_dispatcher_help(self):
        for alias, action in {'maci':'install', 'macr':'remove', 'macu':'update',
                              'macd':'doctor', 'macc':'clean', 'macm':'mirror', 'maca':'ai'}.items():
            with self.subTest(alias=alias):
                full = self.invoke(action, '--help')
                short = self.invoke('--help', executable=ROOT / 'bin' / alias)
                self.assertEqual(full.returncode, 0, full.stderr)
                self.assertEqual(short.returncode, 0, short.stderr)
                self.assertEqual(full.stdout, short.stdout)

    def test_symlink_launcher_from_unrelated_cwd(self):
        link = self.root / 'renamed-ma'
        link.symlink_to(ROOT / 'bin/ma')
        result = self.invoke('--version', executable=link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mactools', result.stdout)
        shortcut = self.root / 'install-alias'
        shortcut.symlink_to(ROOT / 'bin/maci')
        self.assertEqual(self.invoke('--help', executable=shortcut).stdout,
                         self.invoke('install', '--help').stdout)

    def test_completion_parses_without_mutation(self):
        for shell in ('bash', 'zsh', 'fish'):
            result = self.invoke('completion', shell)
            self.assertEqual(result.returncode, 0)
            self.assertIn('maci', result.stdout)
            self.assertIn('refresh-index', result.stdout)
            self.assertIn('config-dir', result.stdout)
            self.assertIn('latest', result.stdout)
            self.assertIn('dev', result.stdout)
            if shell in ('bash', 'zsh'):
                parsed = subprocess.run([shell, '-n'], input=result.stdout,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
        self.assertFalse((self.root / 'config').exists())


@unittest.skipUnless(os.uname().sysname == 'Darwin', 'installer targets macOS')
class Install(unittest.TestCase):
    setUp = CLI.setUp
    invoke = CLI.invoke

    def script(self, name, *args):
        return subprocess.run(['/bin/bash', str(ROOT / name), *args], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def test_lifecycle_custom_path_preserves_unrelated_files(self):
        target = self.root / 'a directory/bin'
        result = self.script('install.sh', '--bin-dir', str(target), '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists())
        result = self.script('install.sh', '--bin-dir', str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.access(target / 'maci', os.X_OK))
        self.assertEqual(self.invoke('--version', executable=target / 'ma').returncode, 0)
        clean = self.invoke('clean', '--dev', '--generate', executable=target / 'ma')
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIn('xcode-derived', clean.stdout)
        (target / 'unrelated').write_text('preserve')
        marker = self.root / 'config/mactools/language'
        marker.parent.mkdir(parents=True)
        marker.write_text('en\n')
        self.assertEqual(self.script('install.sh', '--bin-dir', str(target)).returncode, 0)
        preview = self.script('uninstall.sh', '--bin-dir', str(target), '--dry-run')
        self.assertEqual(preview.returncode, 0)
        self.assertTrue((target / 'ma').exists())
        result = self.script('uninstall.sh', '--bin-dir', str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((target / 'ma').exists())
        self.assertFalse((target / 'mactools_cleanup.py').exists())
        self.assertEqual((target / 'unrelated').read_text(), 'preserve')
        self.assertEqual(marker.read_text(), 'en\n')

    def test_installer_refuses_directory_collision_before_changing_any_file(self):
        target = self.root / 'bin'
        target.mkdir()
        (target / 'mactools_ai.py').mkdir()
        (target / 'ma').write_text('old version')
        result = self.script('install.sh', '--bin-dir', str(target))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / 'ma').read_text(), 'old version')
        self.assertFalse((target / 'maci').exists())

    def test_bad_payload_never_overwrites_existing_install(self):
        import shutil
        source = self.root / 'source'
        shutil.copytree(ROOT / 'bin', source / 'bin')
        shutil.copyfile(ROOT / 'install.sh', source / 'install.sh')
        (source / 'bin/mactools_ai.py').write_text('not valid python !')
        target = self.root / 'existing'
        target.mkdir()
        (target / 'ma').write_text('old')
        result = subprocess.run(['/bin/bash', str(source / 'install.sh'), '--bin-dir', str(target)],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / 'ma').read_text(), 'old')
        self.assertEqual(list(target.iterdir()), [target / 'ma'])

    def test_mid_deployment_failure_restores_files_and_symlinks(self):
        target = self.root / 'rollback-bin'
        target.mkdir()
        original = self.root / 'original-launcher'
        original.write_text('original executable')
        (target / 'maci').symlink_to(original)
        (target / 'macr').write_text('old remove command')
        injected = self.root / 'failure-injection'
        injected.mkdir()
        # Fail once after two replacements. Rollback itself uses real os.replace.
        (injected / 'sitecustomize.py').write_text('''import os
from pathlib import Path
_replace = os.replace
_failed = False
def replace(source, destination, *args, **kwargs):
    global _failed
    source, destination = Path(source), Path(destination)
    if (not _failed and destination.name == 'macu'
            and destination.parent == Path(os.environ['MACTOOLS_TEST_TARGET'])
            and source.parent.name.startswith('.mactools-stage-')):
        _failed = True
        raise OSError('injected deployment failure')
    return _replace(source, destination, *args, **kwargs)
os.replace = replace
''')
        self.env.update(PYTHONPATH=str(injected), MACTOOLS_TEST_TARGET=str(target))
        result = self.script('install.sh', '--bin-dir', str(target))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('injected deployment failure', result.stderr)
        self.assertTrue((target / 'maci').is_symlink())
        self.assertEqual((target / 'maci').readlink(), original)
        self.assertEqual((target / 'macr').read_text(), 'old remove command')
        self.assertEqual({p.name for p in target.iterdir()}, {'maci', 'macr'})

    def test_uninstall_cache_only_removes_mactools_child(self):
        cache = self.root / 'cache/mactools'
        cache.mkdir(parents=True)
        (cache / 'catalog').write_text('data')
        (cache.parent / 'other').write_text('keep')
        result = self.script('uninstall.sh', '--bin-dir', str(self.root / 'not-installed'), '--purge-cache')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(cache.exists())
        self.assertTrue((cache.parent / 'other').exists())


if __name__ == '__main__':
    unittest.main()
