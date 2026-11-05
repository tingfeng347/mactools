"""Shared subprocess lifecycle tests using isolated temporary files."""
import subprocess
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from mactools_runtime import run


class ProcessLifetimeTests(unittest.TestCase):
    def test_input_capture_and_checked_failure_keep_subprocess_contract(self):
        result = run([sys.executable, '-c', 'import sys; print(sys.stdin.read().upper())'],
                     input='hello', capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, 'HELLO\n')
        self.assertEqual(result.stderr, '')
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            run([sys.executable, '-c', 'import sys; print("failed"); sys.exit(7)'],
                capture_output=True, text=True, check=True)
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(raised.exception.stdout, 'failed\n')

    def test_timeout_kills_resistant_descendants_holding_output_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'orphan-ran'
            child = ('import signal, time; from pathlib import Path; '
                     'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                     f'time.sleep(0.8); Path({str(marker)!r}).touch()')
            parent = f'import subprocess, sys; subprocess.Popen([sys.executable, "-c", {child!r}])'
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                run([sys.executable, '-c', parent], capture_output=True, timeout=0.2)
            self.assertLess(time.monotonic() - started, 3)
            time.sleep(0.9)
            self.assertFalse(marker.exists())

    def test_interrupt_stops_children(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / 'ready'
            marker = Path(directory) / 'orphan-ran'
            script = Path(directory) / 'task.sh'
            script.write_text(f'touch "{ready}"; (sleep 0.8; touch "{marker}") & wait\n')
            code = ('from mactools_runtime import run\n'
                    'try:\n'
                    f'    run(["/bin/sh", {str(script)!r}], timeout=5)\n'
                    'except KeyboardInterrupt:\n'
                    '    pass\n')
            process = subprocess.Popen([sys.executable, '-c', code],
                                       env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'bin')))
            try:
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGINT)
                self.assertEqual(process.wait(timeout=3), 0)
                time.sleep(0.9)
                self.assertFalse(marker.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    def test_timeout_stops_descendants_before_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'orphan-ran'
            script = root / 'task.sh'
            script.write_text(
                '#!/bin/sh\n'
                f'(sleep 0.5; printf ran > "{marker}") &\n'
                'sleep 30\n'
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                run(['/bin/sh', str(script)], timeout=0.05)
            time.sleep(0.7)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
