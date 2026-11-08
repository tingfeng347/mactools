"""Monitor behavior, actual macOS samples and signals to disposable children only."""

from dataclasses import replace
import json
import fcntl
import os
import pty
from pathlib import Path
import signal
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from mactools_top import GpuStream, ProcessView, Sampler, load_settings, memory_size
from mactools_top_native import MacCollector, Process, signal_process


def process(pid, name, rss, cpu=0, user='me', started=(100, 1), command=''):
    return Process(pid, started, 1, 501, user, name, rss, cpu, 2, 2, command)


class ProcessFiltering(unittest.TestCase):
    def test_combines_case_insensitive_command_search_user_and_inclusive_memory_range(self):
        rows = [process(10, 'Helper', 512 * 1024**2, command='/Applications/Chrome Helper'),
                process(11, 'Chrome', 1024**3),
                process(12, 'Chrome', 1024**3 + 1),
                process(13, 'Chrome', 600 * 1024**2, user='other'),
                process(14, 'Editor', 600 * 1024**2)]
        view = ProcessView('CHROME', memory_size('512MiB'), memory_size('1GiB'), 'ME', 'memory')
        self.assertEqual([p.pid for p in view.update(rows)], [11, 10])

    def test_sort_and_refresh_keep_identity_not_row_number(self):
        rows = [process(10, 'first', 100, 10), process(11, 'second', 200, 5)]
        view = ProcessView()
        view.update(rows)
        view.move(1)
        view.sorting('memory')
        self.assertEqual([p.pid for p in view.update(rows)], [11, 10])
        self.assertEqual(view.selection().pid, 11)
        view.sorting('memory')
        self.assertEqual([p.pid for p in view.update(rows)], [10, 11])
        self.assertEqual(view.selection().pid, 11)

    def test_reused_pid_does_not_preserve_selection(self):
        view = ProcessView()
        view.update([process(10, 'old', 100), process(11, 'other', 200)])
        view.selected = (10, (100, 1))
        view.update([process(10, 'replacement', 100, started=(101, 0)), process(11, 'other', 200, 10)])
        self.assertEqual(view.selection().pid, 11)

    def test_hold_order_updates_values_and_waits_for_new_processes(self):
        view = ProcessView(sort='memory')
        view.update([process(10, 'a', 200), process(11, 'b', 100)])
        view.frozen = True
        updated = [process(11, 'b', 500), process(10, 'a', 300), process(12, 'c', 1000)]
        self.assertEqual([(p.pid, p.rss) for p in view.update(updated)], [(10, 300), (11, 500)])
        view.frozen = False
        self.assertEqual([p.pid for p in view.update(updated)], [12, 11, 10])

    def test_size_validation_and_empty_results(self):
        self.assertEqual(memory_size('1.5 GiB'), 1610612736)
        self.assertEqual(memory_size('500m'), 524288000)
        for invalid in ('-1', 'nan', '2cats', 'inf', '9' * 400):
            with self.assertRaises(ValueError):
                memory_size(invalid)
        self.assertEqual(ProcessView('missing').update([process(10, 'a', 1)]), [])


class SamplingRules(unittest.TestCase):
    def test_long_command_wrapping_does_not_retain_overlapping_suffixes(self):
        from mactools_top_ui import command_lines, fit
        command = 'x' * 200000
        tracemalloc.start()
        try:
            for index in range(500):
                self.assertEqual(fit(command[index * 80:], 80), 'x' * 80)
            lines = command_lines(command, 80)
            self.assertEqual(''.join(lines), command)
            self.assertLess(tracemalloc.get_traced_memory()[1], 2 * 1024**2)
        finally:
            tracemalloc.stop()

    def test_first_sample_new_interface_and_counter_reset_are_unknown(self):
        values = {'en0': (1000, 2000), 'new': (1, 2)}
        self.assertIsNone(MacCollector.rates(values, {}, None)['en0']['read'])
        rates = MacCollector.rates(values, {'en0': (1200, 1000)}, 2)
        self.assertIsNone(rates['en0']['read'])
        self.assertEqual(rates['en0']['write'], 500)
        self.assertIsNone(rates['new']['write'])

    def test_histories_remain_bounded_and_removed_interfaces_are_reclaimed(self):
        sampler = Sampler(gpu=False)
        snapshot = dict(cpu=10, gpu={'utilization': 20}, memory={'used': 1, 'total': 2},
                        network={'en0': {'read': 1, 'write': 2}}, disks={})
        for _ in range(500):
            sampler.record(snapshot)
        self.assertEqual(len(sampler.history['cpu']), 120)
        self.assertEqual(len(sampler.io_history['network']['en0'][0]), 120)
        snapshot['network'] = {}
        sampler.record(snapshot)
        self.assertEqual(sampler.io_history['network'], {})

    def test_gpu_unavailable_is_not_zero_and_invalid_numbers_are_rejected(self):
        missing = GpuStream.parse({'temp': {}})
        self.assertIsNone(missing['utilization'])
        value = GpuStream.parse({'gpu_active_ratio': 0.25, 'gpu_freq_mhz': 450,
                                 'gpu_power': float('nan'), 'temp': {'gpu_temp_avg': 0}})
        self.assertEqual(value['utilization'], 25)
        self.assertEqual(value['frequency'], 450)
        self.assertIsNone(value['power'])
        self.assertIsNone(value['temperature'])

    def test_gpu_child_stream_is_bounded_and_reaped_on_exit_or_bad_json(self):
        import threading
        for malformed in (False, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as directory:
                executable = Path(directory) / 'macmon'
                output = 'bad json' if malformed else '{"gpu_active_ratio":0.25,"gpu_power":1.5}'
                executable.write_text('#!' + sys.executable + '\nimport time\nwhile True:\n'
                                      ' print(' + repr(output) + ', flush=True)\n time.sleep(.01)\n')
                executable.chmod(0o755)
                stop = threading.Event()
                with patch('mactools_top.shutil.which', return_value=str(executable)), \
                        patch('mactools_top.os.uname', return_value=SimpleNamespace(machine='arm64')):
                    stream = GpuStream(stop, .5)
                try:
                    deadline = time.monotonic() + 2
                    while not stream.latest and not stream.error and time.monotonic() < deadline:
                        time.sleep(.01)
                    if malformed:
                        self.assertIn('macmon', stream.error)
                    else:
                        self.assertEqual(stream.enrich(None)['utilization'], 25)
                finally:
                    stop.set()
                    stream.close()
                self.assertIsNotNone(stream.process.poll())
                self.assertFalse(stream.thread.is_alive())

    def test_bad_settings_cannot_inject_interval_or_theme(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, XDG_CONFIG_HOME=directory):
            path = Path(directory) / 'mactools/top.json'
            path.parent.mkdir()
            path.write_text('{"interval": NaN, "theme": "nonsense", "page": "processes"}')
            self.assertEqual(load_settings(), {'page': 'processes'})


class Terminal:
    def __init__(self, *args, columns=160, rows=44, term='xterm-256color', language='en'):
        self.directory = tempfile.TemporaryDirectory(prefix='mato-terminal-')
        self.master, self.slave = pty.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
        self.before = termios.tcgetattr(self.slave)
        env = dict(os.environ, TERM=term, XDG_CONFIG_HOME=self.directory.name,
                   MACTOOLS_LANG=language, LC_ALL='en_US.UTF-8')
        self.process = subprocess.Popen([str(ROOT / 'bin/mato'), '--no-macmon', '--interval', '0.5', *args],
                                        stdin=self.slave, stdout=self.slave, stderr=self.slave, env=env)
        self.buffer = bytearray()
        self.closed = False

    def send(self, value):
        self.buffer.clear()
        os.write(self.master, value.encode())

    def until(self, value, timeout=5):
        marker = value.encode()
        deadline = time.monotonic() + timeout
        while marker not in self.buffer and time.monotonic() < deadline:
            if select.select([self.master], [], [], 0.05)[0]:
                self.buffer.extend(os.read(self.master, 65536))
            if self.process.poll() is not None:
                break
        if marker not in self.buffer:
            raise AssertionError(f'{value!r} not displayed; tail={self.buffer[-2000:]!r}')

    def wait(self, timeout=5):
        # A real terminal consumes output continuously. Drain while waiting so
        # curses.endwin() cannot block on the PTY's small output buffer.
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            if select.select([self.master], [], [], 0.02)[0]:
                self.buffer.extend(os.read(self.master, 65536))
        return self.process.wait(timeout=max(0.1, deadline - time.monotonic()))

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            os.write(self.master, b'\x1bq')
            try:
                self.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.wait(timeout=3)
        os.close(self.master)
        os.close(self.slave)
        self.directory.cleanup()


@unittest.skipUnless(sys.platform == 'darwin', 'real curses/macOS integration')
class TerminalMonitor(unittest.TestCase):
    def test_kitty_refresh_preserves_unicode_borders_charts_and_numeric_values(self):
        terminal = Terminal(term='xterm-kitty')
        self.addCleanup(terminal.close)
        terminal.until('Processes')
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            if select.select([terminal.master], [], [], .02)[0]:
                terminal.buffer.extend(os.read(terminal.master, 65536))
        output = bytes(terminal.buffer)
        output.decode('utf-8', errors='strict')
        self.assertIn('─'.encode(), output)
        self.assertIn('━'.encode(), output)
        self.assertNotIn(b'\x01\x1b[', output)  # Truncated U+2501 before REP repeats a number.

    def test_header_interval_keyboard_mouse_edit_and_persistence(self):
        terminal = self.terminal(columns=80, rows=24, term='xterm-kitty')
        terminal.send('+')  # Same direction as the displayed milliseconds.
        time.sleep(.1)
        terminal.send('i')
        terminal.until('Refresh interval')
        terminal.until('600')
        terminal.send('\x15nan\n')
        terminal.until('Interval must be')
        terminal.send('\x151250\n')
        time.sleep(.1)
        # At 80 columns, the plus button is in columns 76..78, row zero.
        terminal.send('\x1b[M' + chr(32) + chr(33 + 77) + chr(33))
        time.sleep(.1)
        terminal.send('i')
        terminal.until('Refresh interval')
        terminal.until('1350')
        terminal.send('\x1b')
        time.sleep(.05)
        terminal.send('-')
        time.sleep(.1)
        terminal.send('q')
        self.assertEqual(terminal.wait(), 0)
        settings = json.loads((Path(terminal.directory.name) / 'mactools/top.json').read_text())
        self.assertEqual(settings['interval'], 1.25)

    def terminal(self, *args, **kwargs):
        terminal = Terminal(*args, **kwargs)
        self.addCleanup(terminal.close)
        terminal.until('Processes')
        return terminal

    def test_search_filter_theme_and_terminal_restore(self):
        terminal = self.terminal()
        terminal.send('/')
        terminal.until('Search processes')
        terminal.send('no-such-mato-process\n')
        terminal.until('No matches')
        terminal.send('\x1b')
        time.sleep(0.1)
        terminal.send('f')
        terminal.until('Filter processes')
        terminal.send('999GiB\n')
        terminal.until('No matches')
        terminal.send('t')
        terminal.until('Theme')
        terminal.send('\x1bOB\n')
        time.sleep(0.1)
        terminal.send('q')
        self.assertEqual(terminal.wait(timeout=4), 0, terminal.buffer.decode(errors='replace'))
        restored = termios.tcgetattr(terminal.slave)
        # PENDIN is the kernel's transient queued-input status, not an input mode.
        restored[3] &= ~getattr(termios, 'PENDIN', 0)
        terminal.before[3] &= ~getattr(termios, 'PENDIN', 0)
        self.assertEqual(restored, terminal.before)
        settings = json.loads((Path(terminal.directory.name) / 'mactools/top.json').read_text())
        self.assertEqual(settings['theme'], 'dark')
        self.assertNotIn('search', settings)

    def test_narrow_pages_and_resize_while_modal_open(self):
        terminal = self.terminal(columns=80, rows=24)
        terminal.send('\t')
        terminal.until('Network')
        terminal.send('?')
        terminal.until('Keyboard shortcuts')
        fcntl.ioctl(terminal.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 18, 60, 0, 0))
        os.kill(terminal.process.pid, signal.SIGWINCH)
        time.sleep(0.1)
        terminal.send('\x1bq')
        self.assertEqual(terminal.wait(timeout=4), 0)

    def test_cancel_then_confirm_signal_on_test_child(self):
        for key, sig in (('k', signal.SIGTERM), ('K', signal.SIGKILL)):
            with self.subTest(signal=sig):
                child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
                def cleanup(child=child):
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=3)
                self.addCleanup(cleanup)
                terminal = self.terminal('--search', str(child.pid), '--sort', 'pid')
                # Ascending PID puts our older disposable child before the monitor.
                terminal.send('p\x1bOH')
                time.sleep(0.1)
                terminal.send(key)
                terminal.until('SIGTERM' if key == 'k' else 'SIGKILL')
                terminal.send('n')
                time.sleep(0.1)
                self.assertIsNone(child.poll())
                terminal.send(key)
                terminal.until('SIGTERM' if key == 'k' else 'SIGKILL')
                terminal.send('y')
                # Keep consuming terminal output while the UI handles the key.
                deadline = time.monotonic() + 4
                while child.poll() is None and time.monotonic() < deadline:
                    if select.select([terminal.master], [], [], .02)[0]:
                        terminal.buffer.extend(os.read(terminal.master, 65536))
                self.assertEqual(child.wait(timeout=4), -sig)
                terminal.close()


@unittest.skipUnless(sys.platform == 'darwin', 'native macOS integration')
class NativeMonitor(unittest.TestCase):
    def setUp(self):
        self.collector = MacCollector()
        self.addCleanup(self.collector.close)

    def child(self, code='import time; time.sleep(30)'):
        child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
        self.addCleanup(cleanup)
        return child

    def test_live_sample_has_plausible_self_memory_volumes_and_rates(self):
        first = self.collector.sample()
        time.sleep(0.1)
        second = self.collector.sample()
        self.assertEqual(first['errors'], [])
        self.assertEqual(second['errors'], [])
        self.assertIsNone(first['cpu'])
        self.assertTrue(0 <= second['cpu'] <= 100)
        row = next(p for p in second['processes'] if p.pid == os.getpid())
        self.assertGreater(row.rss, 1024**2)
        self.assertEqual(row.uid, os.getuid())
        memory = second['memory']
        self.assertGreater(memory['total'], 1024**3)
        self.assertEqual(memory['used'] + memory['available'], memory['total'])
        self.assertTrue(any(v['mount'] == '/' for v in first['volumes']))
        self.assertTrue(second['network'])
        # Virtual macOS CI disks may expose no IOBlockStorageDriver counters.
        self.assertIsInstance(second['disks'], dict)

    def test_cpu_time_conversion_measures_one_busy_core(self):
        child = self.child('while True: pass')
        time.sleep(0.1)
        self.collector.sample()
        time.sleep(0.3)
        sample = self.collector.sample()
        row = next(p for p in sample['processes'] if p.pid == child.pid)
        self.assertGreater(row.cpu, 25)
        self.assertLess(row.cpu, 120)

    def test_full_command_is_available_on_demand(self):
        child = self.child('import time; marker="mato-command-marker"; time.sleep(30)')
        time.sleep(0.05)
        identity = self.collector.identity(child.pid)
        self.assertIn('mato-command-marker', self.collector.command(identity))

    def test_exec_refreshes_detail_and_search_cache_expires(self):
        for refresh in (True, False):
            child = self.child('import os,time; time.sleep(.3); os.execl("/bin/sleep", "sleep", "30")')
            time.sleep(0.05)
            identity = self.collector.identity(child.pid)
            self.assertIn('os.execl', self.collector.command(identity))
            time.sleep(0.4)
            self.assertEqual(self.collector.identity(child.pid), identity)
            if refresh:
                self.assertEqual(self.collector.command(identity, refresh=True), 'sleep 30')
            else:
                with patch('mactools_top_native.time.monotonic', return_value=time.monotonic() + 6):
                    self.assertEqual(self.collector.command(identity), 'sleep 30')

    def test_long_interval_does_not_delay_details_or_interval_changes(self):
        sampler = Sampler(60, gpu=False).start()
        self.addCleanup(sampler.close)
        deadline = time.monotonic() + 3
        while sampler.latest is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(sampler.latest, sampler.error)
        identity = self.collector.identity(os.getpid())
        sampler.request_detail(identity)
        deadline = time.monotonic() + 1
        while sampler.details is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(sampler.details)
        self.assertEqual(sampler.details[0], identity)
        old_time = sampler.latest['time']
        sampler.set_interval(.5)
        deadline = time.monotonic() + 1
        while sampler.latest['time'] == old_time and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertNotEqual(sampler.latest['time'], old_time)

    def test_term_and_force_kill_only_test_created_children(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            child = self.child()
            row = next(p for p in self.collector.sample()['processes'] if p.pid == child.pid)
            signal_process(row, sig, self.collector)
            self.assertEqual(child.wait(timeout=3), -sig)

    def test_stale_identity_never_signals_a_live_replacement(self):
        child = self.child()
        row = next(p for p in self.collector.sample()['processes'] if p.pid == child.pid)
        stale = replace(row, started=(row.started[0] - 1, row.started[1]))
        with self.assertRaises(ProcessLookupError):
            signal_process(stale, signal.SIGTERM, self.collector)
        self.assertIsNone(child.poll())

    def test_protected_pids_and_permission_denial_are_reported(self):
        for pid in (0, 1, os.getpid()):
            with self.assertRaises(PermissionError):
                signal_process(process(pid, 'protected', 0), signal.SIGTERM, self.collector)
        child = self.child()
        row = next(p for p in self.collector.sample()['processes'] if p.pid == child.pid)
        with patch('mactools_top_native.os.kill', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(PermissionError, 'denied'):
                signal_process(row, signal.SIGTERM, self.collector)
        self.assertIsNone(child.poll())

    def test_json_cli_filters_and_non_terminal_explains_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, XDG_CONFIG_HOME=directory, MACTOOLS_LANG='zh')
            result = subprocess.run([str(ROOT / 'bin/mato'), '--json', '--samples', '2',
                                     '--interval', '0.5', '--min-memory', '1GiB', '--no-macmon'],
                                    capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            samples = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(samples), 2)
            self.assertTrue(all(p['rss'] >= 1024**3 for s in samples for p in s['processes']))
            self.assertFalse((Path(directory) / 'mactools/top.json').exists())
            plain = subprocess.run([str(ROOT / 'bin/mato')], capture_output=True, text=True, timeout=5, env=env)
            self.assertNotEqual(plain.returncode, 0)
            self.assertIn('--json', plain.stderr)


if __name__ == '__main__':
    unittest.main()
