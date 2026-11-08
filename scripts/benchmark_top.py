#!/usr/bin/env python3
"""Measure the real curses monitor and its descendants in an isolated PTY.

python3 scripts/benchmark_top.py --seconds 60 --output /tmp/mato-benchmark.json
Use --seconds 3600 for the full duration target. Does not modify user settings.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from mactools_top_native import MacCollector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--no-macmon', action='store_true')
    parser.add_argument('--term', default='xterm-256color')
    args = parser.parse_args()
    if args.seconds < 2:
        parser.error('--seconds must be at least 2')
    collector = MacCollector()
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 44, 160, 0, 0))
    process = None
    with tempfile.TemporaryDirectory(prefix='mato-benchmark-') as directory:
        try:
            command = [str(ROOT / 'bin/mato'), '--theme', 'nord']
            if args.no_macmon:
                command.append('--no-macmon')
            env = dict(os.environ, TERM=args.term, MACTOOLS_LANG='en',
                       XDG_CONFIG_HOME=directory, LC_ALL='en_US.UTF-8')
            process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                       env=env, start_new_session=True)

            def drain(until):
                data = bytearray()
                while time.monotonic() < until:
                    if select.select([master], [], [], min(0.025, max(0, until - time.monotonic())))[0]:
                        data.extend(os.read(master, 65536))
                        if len(data) > 131072:
                            del data[:-65536]
                return data

            drain(time.monotonic() + 2)  # Exclude startup/import work, include steady UI redraws.
            start = time.monotonic()
            cpus, low, high, first, last, children = [], float('inf'), 0, None, None, set()
            collector.sample()
            for index in range(args.seconds):
                drain(start + index + 1)
                if process.poll() is not None:
                    raise RuntimeError(f'monitor exited early: {process.returncode}')
                sample = collector.sample()
                members = {process.pid}
                while True:
                    expanded = members | {p.pid for p in sample['processes'] if p.ppid in members}
                    if expanded == members:
                        break
                    members = expanded
                rows = [p for p in sample['processes'] if p.pid in members]
                rss = sum(p.rss for p in rows) / 1024**2
                cpu = sum(p.cpu for p in rows)
                low, high, last = min(low, rss), max(high, rss), rss
                first = rss if first is None else first
                cpus.append(cpu)
                children.update(members - {process.pid})
            # Query a distinct modal title to measure actual keyboard-to-output time.
            latencies = []
            for _ in range(5):
                drain(time.monotonic() + 0.05)
                sent = time.monotonic()
                os.write(master, b'?')
                output = bytearray()
                while b'Keyboard shortcuts' not in output and time.monotonic() - sent < 2:
                    if select.select([master], [], [], 0.005)[0]:
                        output.extend(os.read(master, 65536))
                if b'Keyboard shortcuts' not in output:
                    raise RuntimeError('keyboard response marker not received')
                latencies.append((time.monotonic() - sent) * 1000)
                os.write(master, b'\x1b')
                drain(time.monotonic() + 0.1)
            result = dict(duration_seconds=args.seconds, dimensions='160x44',
                          rss_mib_min=low, rss_mib_max=high, rss_mib_first=first, rss_mib_last=last,
                          cpu_one_core_percent_average=sum(cpus) / len(cpus),
                          keyboard_ms=latencies, keyboard_ms_max=max(latencies),
                          included_child_pids=sorted(children), python=sys.version.split()[0],
                          machine=os.uname().machine, term=args.term, scope='main process plus descendants; real curses UI')
            payload = json.dumps(result, indent=2)
            print(payload)
            if args.output:
                args.output.write_text(payload + '\n')
        finally:
            if process:
                if process.poll() is None:
                    os.write(master, b'\x1bq')
                    deadline = time.monotonic() + 5
                    while process.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.02)[0]:
                            os.read(master, 65536)
                    if process.poll() is None:
                        process.kill()
                process.wait(timeout=3)
            os.close(master)
            os.close(slave)
            collector.close()


if __name__ == '__main__':
    main()
