"""Pi's interactive installer and confirmations work with redirected stdio."""
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'


class TerminalTests(unittest.TestCase):
    def run_in_terminal(self, code, answer, env):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        # A real PTY fixture is opened under /dev/tty by the child. This works
        # even on macOS background hosts that cannot acquire controlling TTYs.
        # Its descriptors remain real terminals when inherited by shell clients.
        setup = f"""import builtins
_original_open = builtins.open
def _fixture_tty(path, *args, **kwargs):
    return _original_open({os.ttyname(slave)!r} if path == '/dev/tty' else path, *args, **kwargs)
builtins.open = _fixture_tty
"""
        proc = subprocess.Popen([sys.executable, '-c', setup + code],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(env, PYTHONPATH=str(BIN)))
        try:
            os.write(master, answer)
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, stderr.decode())
            return stdout.decode()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_pi_uninstaller_receives_real_terminal_with_redirected_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'verified'
            # Only a local fixture runs; no client is downloaded or removed.
            installer = b'''#!/bin/sh
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] || exit 9
read choice
[ "$choice" = U ] || exit 10
printf verified > "$PROBE_RESULT"
'''
            code = f'''import mactools_ai as ai
ai.fetch = lambda *args, **kwargs: {installer!r}
ai.execute([['@pi-uninstaller', ai.TOOLS['pi']['url'], 'sh']], 1)
'''
            self.run_in_terminal(code, b'U\n', dict(os.environ, PROBE_RESULT=str(marker)))
            self.assertEqual(marker.read_text(), 'verified')

    def test_confirmation_reads_terminal_when_stdin_is_redirected(self):
        code = '''from mactools_runtime import confirm
assert confirm('Proceed?'), 'terminal confirmation was not accepted'
'''
        self.run_in_terminal(code, b'y\n', os.environ)

    def test_pi_uninstaller_without_terminal_fails_before_download(self):
        code = '''import mactools_ai as ai
ai.fetch = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('unexpected download'))
try:
    ai.execute([['@pi-uninstaller', ai.TOOLS['pi']['url'], 'sh']], 1)
except RuntimeError as error:
    assert 'TTY' in str(error)
else:
    raise AssertionError('missing terminal was not rejected')
'''
        result = subprocess.run([sys.executable, '-c', code], start_new_session=True,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                env=dict(os.environ, PYTHONPATH=str(BIN)), timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == '__main__':
    unittest.main()
