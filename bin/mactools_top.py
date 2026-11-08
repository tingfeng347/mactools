"""ma top: bounded sampling, process filtering and CLI. UI imports are lazy."""

import argparse
from collections import deque
from dataclasses import asdict
import json
import math
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time

from mactools_runtime import config_dir, save_json, text
from mactools_top_native import MacCollector


THEMES = ('terminal', 'dark', 'light', 'nord', 'gruvbox')


def memory_size(value):
    if value is None or value == '':
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(B|[KMGT](?:i?B)?)?\s*', str(value), re.I)
    if not match:
        raise ValueError(text('内存格式示例：500MiB、1GiB。', 'Memory examples: 500MiB, 1GiB.'))
    number, unit = match.groups()
    exponent = 'BKMGT'.index((unit or 'B')[0].upper())
    amount = float(number) * 1024 ** exponent
    if not math.isfinite(amount) or amount > 2**64 - 1:
        raise ValueError(text('内存数值过大。', 'Memory value is too large.'))
    return int(amount)


def size_text(value):
    if value is None:
        return '--'
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(value) < 1024 or unit == 'TiB':
            return f'{value:.0f}{unit}' if unit == 'B' else f'{value:.1f}{unit}'
        value /= 1024


class ProcessView:
    def __init__(self, search='', minimum=None, maximum=None, user='', sort='cpu'):
        self.search, self.minimum, self.maximum, self.user = search, minimum, maximum, user
        self.sort, self.descending, self.frozen = sort, True, False
        self.selected, self.order = None, []
        self.rows = []

    def update(self, processes):
        query, user = self.search.casefold(), self.user.casefold()
        rows = [p for p in processes
                if (not query or query in (str(p.pid) + ' ' + p.name + ' ' + p.command).casefold())
                and (self.minimum is None or p.rss >= self.minimum)
                and (self.maximum is None or p.rss <= self.maximum)
                and (not user or user == p.user.casefold() or user == str(p.uid))]
        if self.frozen:
            positions = {identity: index for index, identity in enumerate(self.order)}
            rows = sorted((p for p in rows if p.identity in positions),
                          key=lambda p: positions[p.identity])
        else:
            key = {'cpu': lambda p: p.cpu, 'memory': lambda p: p.rss,
                   'pid': lambda p: p.pid, 'name': lambda p: p.name.casefold()}[self.sort]
            rows.sort(key=lambda p: (key(p), p.pid), reverse=self.descending)
            self.order = [p.identity for p in rows]
        self.rows = rows
        identities = {p.identity for p in rows}
        if self.selected not in identities:
            self.selected = rows[0].identity if rows else None
        return rows

    def move(self, delta):
        if self.rows:
            index = next((i for i, p in enumerate(self.rows) if p.identity == self.selected), 0)
            self.selected = self.rows[max(0, min(len(self.rows) - 1, index + delta))].identity

    def selection(self):
        return next((p for p in self.rows if p.identity == self.selected), None)

    def sorting(self, field):
        self.descending = not self.descending if self.sort == field else True
        self.sort, self.frozen = field, False


def load_settings():
    try:
        settings = json.loads((config_dir() / 'top.json').read_text())
        if not isinstance(settings, dict):
            return {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        print(text('监控设置读取失败，使用默认设置：', 'Monitor settings unavailable: ') + str(error), file=sys.stderr)
        return {}
    result = {}
    if settings.get('theme') in THEMES:
        result['theme'] = settings['theme']
    interval = settings.get('interval')
    if isinstance(interval, (int, float)) and math.isfinite(interval) and 0.5 <= interval <= 60:
        result['interval'] = interval
    if settings.get('page') in ('overview', 'processes'):
        result['page'] = settings['page']
    return result


class GpuStream:
    """Optional single macmon child. No unbounded queue or stderr buffering."""

    def __init__(self, stop, interval):
        self.stop, self.interval = stop, interval
        self.process, self.latest, self.updated = None, None, 0.0
        self.error, self.thread = '', None
        self.executable = shutil.which('macmon')
        if self.executable and os.uname().machine == 'arm64':
            self.thread = threading.Thread(target=self.run, name='mato-gpu', daemon=True)
            self.thread.start()

    def run(self):
        try:
            self.process = subprocess.Popen(
                [self.executable, 'pipe', '-i', str(int(self.interval * 1000))],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            buffer = bytearray()
            while not self.stop.is_set() and self.process.poll() is None:
                ready, _, _ = select.select([self.process.stdout], [], [], 0.25)
                if not ready:
                    continue
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > 1024 * 1024:
                    raise ValueError('macmon sample exceeds 1 MiB')
                while b'\n' in buffer:
                    line, _, buffer = buffer.partition(b'\n')
                    parsed = self.parse(json.loads(line))
                    self.updated = time.monotonic()
                    self.latest = parsed
            if not self.stop.is_set():
                self.error = text('macmon 已退出', 'macmon exited')
        except (OSError, ValueError, TypeError) as error:
            self.error = 'macmon: ' + str(error)
        finally:
            self.terminate()

    @staticmethod
    def parse(value):
        def number(value, minimum=0, maximum=float('inf')):
            return value if isinstance(value, (int, float)) and math.isfinite(value) and minimum <= value <= maximum else None
        if not isinstance(value, dict):
            raise ValueError('macmon expected a JSON object')
        utilization = number(value.get('gpu_active_ratio'), maximum=1)
        temp = value.get('temp') or {}
        if not isinstance(temp, dict):
            raise ValueError('macmon temp must be an object')
        return dict(frequency=number(value.get('gpu_freq_mhz')),
                    power=number(value.get('gpu_power')),
                    temperature=number(temp.get('gpu_temp_avg'), minimum=0.1, maximum=150),
                    utilization=utilization * 100 if utilization is not None else None)

    def enrich(self, gpu):
        result = dict(gpu or dict(name='GPU', utilization=None, frequency=None,
                                  power=None, temperature=None, source='Unavailable'))
        if self.latest and time.monotonic() - self.updated < max(5, self.interval * 3):
            for key, value in self.latest.items():
                if value is not None:
                    result[key] = value
            result['source'] = 'IOKit + macmon'
        result['note'] = self.error
        return result

    def terminate(self):
        process = self.process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            if process.stdout:
                process.stdout.close()

    def close(self):
        if self.thread:
            self.thread.join(timeout=2)


class Sampler:
    def __init__(self, interval=1, gpu=True):
        self.interval, self.use_gpu = interval, gpu
        self.stop, self.changed = threading.Event(), threading.Event()
        self.wake = threading.Event()
        self.control = threading.Lock()
        self.resample = False
        self.latest, self.error = None, ''
        self.search_commands = False
        self.request, self.details = None, None
        self.history = {name: deque(maxlen=120) for name in ('cpu', 'gpu', 'memory')}
        self.io_history = {'network': {}, 'disks': {}}
        self.thread = threading.Thread(target=self.run, name='mato-sampler', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def run(self):
        collector, stream = None, None
        try:
            collector = MacCollector()
            stream = GpuStream(self.stop, self.interval) if self.use_gpu else None
            next_sample = 0
            while not self.stop.is_set():
                self.wake.clear()
                with self.control:
                    resample, self.resample = self.resample, False
                if resample or time.monotonic() >= next_sample:
                    started = time.monotonic()
                    snapshot = collector.sample(self.search_commands)
                    if stream:
                        snapshot['gpu'] = stream.enrich(snapshot['gpu'])
                    self.latest = snapshot
                    self.error = ''
                    self.changed.set()
                    next_sample = started + self.interval
                with self.control:
                    request, self.request = self.request, None
                if request:
                    try:
                        self.details = (request, collector.command(request, refresh=True), '')
                    except OSError as error:
                        self.details = (request, '', str(error))
                    self.changed.set()
                self.wake.wait(max(0.01, next_sample - time.monotonic()))
        except Exception as error:
            # Surface worker failures in the UI instead of losing the thread silently.
            self.error = type(error).__name__ + ': ' + str(error)
            self.changed.set()
        finally:
            if stream:
                self.stop.set()
                stream.close()
            if collector:
                collector.close()

    def set_interval(self, interval):
        with self.control:
            self.interval = interval
            self.resample = True
        self.wake.set()

    def set_search(self, enabled):
        with self.control:
            self.search_commands = enabled
            self.resample = True
        self.wake.set()

    def request_detail(self, identity):
        with self.control:
            self.request, self.details = identity, None
        self.wake.set()

    def record(self, snapshot):
        """UI-owned histories: worker never mutates a deque during drawing."""
        self.history['cpu'].append(snapshot['cpu'])
        self.history['gpu'].append((snapshot['gpu'] or {}).get('utilization'))
        memory = snapshot['memory']
        self.history['memory'].append(100 * memory['used'] / memory['total'] if memory else None)
        for group in self.io_history:
            self.io_history[group] = {name: self.io_history[group].get(name, (deque(maxlen=120), deque(maxlen=120)))
                                      for name in snapshot[group]}
            for name, item in snapshot[group].items():
                self.io_history[group][name][0].append(item['read'])
                self.io_history[group][name][1].append(item['write'])

    def close(self):
        self.stop.set()
        self.wake.set()
        self.thread.join(timeout=4)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ma top / mato', description=text(
        '低开销实时监控 CPU、GPU、内存、磁盘、网络与进程。',
        'Lightweight live CPU, GPU, memory, disk, network and process monitor.'))
    parser.add_argument('--sort', choices=('cpu', 'memory', 'pid', 'name'), default='cpu')
    parser.add_argument('--search', default='', help=text('搜索进程名称、PID、命令', 'Search name, PID, command'))
    parser.add_argument('--min-memory', help='500MiB / 1GiB')
    parser.add_argument('--max-memory', help='500MiB / 1GiB')
    parser.add_argument('--user', default='')
    parser.add_argument('--theme', choices=THEMES)
    parser.add_argument('--interval', type=float, help=text('采样秒数，0.5–60，默认 1', 'Sample seconds, 0.5–60, default 1'))
    parser.add_argument('--interface', help=text('网卡，如 en0、utun4', 'Interface, e.g. en0, utun4'))
    parser.add_argument('--disk', help=text('物理磁盘，如 disk0', 'Physical disk, e.g. disk0'))
    parser.add_argument('--no-macmon', action='store_true', help=text('仅使用原生 GPU 利用率', 'Use native GPU utilization only'))
    parser.add_argument('--json', action='store_true', help=text('逐行输出采样 JSON', 'Stream JSON samples'))
    parser.add_argument('--samples', type=int, default=0, help=text('JSON 采样数，0 为持续', 'JSON sample count; 0 is continuous'))
    args = parser.parse_args(argv)
    settings = load_settings()
    args.theme = args.theme or settings.get('theme', 'terminal')
    args.interval = args.interval if args.interval is not None else settings.get('interval', 1)
    if not math.isfinite(args.interval) or not 0.5 <= args.interval <= 60:
        parser.error(text('--interval 必须在 0.5–60 秒之间', '--interval must be between 0.5 and 60 seconds'))
    if args.samples < 0 or (args.samples and not args.json):
        parser.error('--samples requires --json and a nonnegative count')
    minimum, maximum = memory_size(args.min_memory), memory_size(args.max_memory)
    if minimum is not None and maximum is not None and minimum > maximum:
        parser.error(text('内存下限不能超过上限', 'Minimum memory exceeds maximum'))
    if sys.platform != 'darwin':
        raise RuntimeError(text('ma top 仅支持 macOS。', 'ma top supports macOS only.'))
    if not args.json and (not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get('TERM') == 'dumb'):
        raise ValueError(text('请在交互终端运行 mato；脚本可用 --json --samples 2。',
                              'Run mato in a terminal; use --json --samples 2 in scripts.'))
    view = ProcessView(args.search, minimum, maximum, args.user, args.sort)
    sampler = Sampler(args.interval, not args.no_macmon)
    sampler.search_commands = bool(args.search)
    sampler.start()
    try:
        if args.json:
            count, previous = 0, None
            while not args.samples or count < args.samples:
                sampler.changed.wait(0.25)
                sampler.changed.clear()
                if sampler.error:
                    raise RuntimeError(sampler.error)
                snapshot = sampler.latest
                if snapshot is None or snapshot['time'] == previous:
                    continue
                previous = snapshot['time']
                result = dict(snapshot)
                result['processes'] = [asdict(p) for p in view.update(snapshot['processes'])]
                print(json.dumps(result, ensure_ascii=False), flush=True)
                count += 1
        else:
            from mactools_top_ui import run
            state = run(sampler, view, args, settings.get('page', 'processes'))
            save_json(config_dir() / 'top.json', state)
    finally:
        sampler.close()
    return 0
