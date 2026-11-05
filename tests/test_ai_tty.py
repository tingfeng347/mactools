"""Pi's interactive installer and confirmations work with redirected stdio."""
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import time
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

    def test_foreground_timeout_kills_children_and_restores_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'orphan-ran'
            pid, master = pty.fork()
            if pid == 0:
                try:
                    os.execl(sys.executable, sys.executable, '-c', f'''import os, subprocess, sys
sys.path.insert(0, {str(BIN)!r})
from mactools_runtime import run
tty = open('/dev/tty', 'r+b', buffering=0)
try:
    run(['/bin/sh', '-c', '(sleep 0.5; touch "$1") & sleep 30', '_', {str(marker)!r}], timeout=0.05,
        stdin=tty, stdout=tty, stderr=tty)
except subprocess.TimeoutExpired:
    pass
print(os.tcgetpgrp(tty.fileno()) == os.getpgrp())
''')
                finally:
                    os._exit(1)
            try:
                os.set_blocking(master, False)
                output = b''
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        output += os.read(master, 4096)
                    except BlockingIOError:
                        pass
                    except OSError:
                        break
                    done, status = os.waitpid(pid, os.WNOHANG)
                    if done:
                        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                        break
                    time.sleep(0.02)
                else:
                    self.fail('terminal subprocess did not exit')
                self.assertIn(b'True', output)
                time.sleep(0.7)
                self.assertFalse(marker.exists())
            finally:
                os.close(master)
                try:
                    pending = os.waitpid(pid, os.WNOHANG) == (0, 0)
                except ChildProcessError:
                    pending = False
                if pending:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)


if __name__ == '__main__':
    unittest.main()
