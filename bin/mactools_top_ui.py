"""Event-driven curses view: only changed lines reach the terminal."""

import curses
from functools import lru_cache
import locale
import math
import os
import signal
import time
import unicodedata

from mactools_runtime import text
from mactools_top import THEMES, memory_size, size_text
from mactools_top_native import signal_process


def clean(value):
    value = str(value)
    if value.isascii() and value.isprintable():
        return value
    return ''.join(' ' if unicodedata.category(ch).startswith('C') else ch for ch in value)


@lru_cache(maxsize=1024)
def width(value):
    if value.isascii():
        return len(value)
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in value)


def fit(value, columns, pad=False):
    value = str(value)
    if len(value) <= 512:
        return fit_short(value, columns, pad)
    return fit_visible(value, columns, pad)


@lru_cache(maxsize=1024)
def fit_short(value, columns, pad=False):
    return fit_visible(value, columns, pad)


def fit_visible(value, columns, pad=False):
    columns = max(0, columns)
    if value.isascii():
        result = clean(value[:columns])
        return result.ljust(columns) if pad else result
    result, used = [], 0
    for ch in value:
        if unicodedata.category(ch).startswith('C'):
            ch = ' '
        count = width(ch)
        if used + count > columns:
            break
        result.append(ch)
        used += count
    return ''.join(result) + (' ' * max(0, columns - used) if pad else '')


def percent(value):
    return '--' if value is None else f'{value:.0f}%'


def chart(values, columns, maximum=100):
    points = list(values)[-max(0, columns):]
    levels = '▁▂▃▄▅▆▇█'
    return ''.join(' ' if value is None else levels[min(7, max(0, round(value / max(maximum, 1) * 7)))]
                   for value in points).rjust(max(0, columns))


def command_lines(value, columns):
    """Wrap a command once without retaining every overlapping suffix."""
    lines, current, used = [], [], 0
    columns = max(1, columns)
    if value.isascii():
        value = clean(value)
        return [value[index:index + columns] for index in range(0, len(value), columns)] or ['']
    for ch in value:
        if unicodedata.category(ch).startswith('C'):
            ch = ' '
        count = width(ch)
        if used + count > columns and current:
            lines.append(''.join(current))
            current, used = [], 0
        current.append(ch)
        used += count
    lines.append(''.join(current))
    return lines


class Screen:
    def __init__(self, window, theme):
        self.window, self.previous, self.size = window, {}, None
        self.theme = None
        self.colors = {}
        self.set_theme(theme)

    def set_theme(self, theme):
        self.theme, self.previous = theme, {}
        palettes = {
            'terminal': (-1, -1, 6, 2, 3, 1, 4),
            'dark': (252, 234, 81, 114, 222, 203, 24),
            'light': (234, 255, 25, 28, 94, 160, 153),
            'nord': (254, 235, 110, 150, 222, 174, 60),
            'gruvbox': (223, 235, 109, 142, 214, 167, 58),
        }
        fg, bg, accent, good, warning, danger, selection = palettes[theme]
        category_colors = {
            'terminal': (114, 183, 222, 75, 80),
            'dark': (114, 183, 222, 75, 80),
            'light': (28, 90, 94, 25, 30),
            'nord': (150, 182, 222, 110, 116),
            'gruvbox': (142, 175, 214, 109, 108),
        }
        self.base = 0
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                fg, bg = 7, 0
            colors = [('text', fg, bg), ('accent', accent, bg), ('good', good, bg),
                      ('warning', warning, bg), ('danger', danger, bg),
                      ('selected', fg if theme != 'light' else 234, selection),
                      ('muted', 248 if theme != 'light' else 240, bg)]
            colors.extend((name, color, bg) for name, color in zip(
                ('cpu', 'gpu', 'memory', 'disk', 'network'), category_colors[theme]))
            for index, (name, foreground, background) in enumerate(colors, 1):
                if curses.COLORS < 256:
                    foreground = foreground % 8 if foreground >= 0 else foreground
                    background = background % 8 if background >= 0 else background
                try:
                    curses.init_pair(index, foreground, background)
                    self.colors[name] = curses.color_pair(index)
                except curses.error:
                    self.colors[name] = 0
            self.base = self.colors['text']
        self.window.bkgd(' ', self.base)
        self.window.erase()

    def begin(self):
        self.height, self.columns = self.window.getmaxyx()
        if self.size != (self.height, self.columns):
            self.previous = {}
            self.window.erase()
            self.size = (self.height, self.columns)
        self.lines = {row: [] for row in range(self.height)}

    def put(self, y, x, value, color='text', bold=False, limit=None):
        if not 0 <= y < self.height or x >= self.columns or x < 0:
            return
        value = fit(value, min(self.columns - x - 1, limit if limit is not None else self.columns))
        attr = self.colors.get(color, 0) | (curses.A_BOLD if bold else 0)
        self.lines[y].append((x, value, attr))

    def clear_row(self, y):
        if y in self.lines:
            self.lines[y] = []

    def box(self, y, x, height, columns, title, color='accent'):
        if height < 3 or columns < 5:
            return
        self.put(y, x, '┌' + '─' * (columns - 2) + '┐', 'muted')
        self.put(y, x + 2, ' ' + title + ' ', color, True, columns - 5)
        for row in range(y + 1, y + height - 1):
            self.put(row, x, '│', 'muted')
            self.put(row, x + columns - 1, '│', 'muted')
        self.put(y + height - 1, x, '└' + '─' * (columns - 2) + '┘', 'muted')

    def bar(self, y, x, columns, value, color='good'):
        if columns < 1:
            return
        amount = round(columns * max(0, min(100, value or 0)) / 100)
        self.put(y, x, '━' * amount, color)
        self.put(y, x + amount, '─' * (columns - amount), 'muted')

    def flush(self, cursor=None):
        for row, line in self.lines.items():
            if self.previous.get(row) == line:
                continue
            self.window.move(row, 0)
            self.window.clrtoeol()
            for x, value, attr in line:
                try:
                    self.window.addstr(row, x, value, attr)
                except curses.error:
                    # A resize may have occurred after begin(); next pass rebuilds geometry.
                    if self.window.getmaxyx() == self.size and row != self.height - 1:
                        raise
        self.previous = self.lines
        try:
            curses.curs_set(1 if cursor else 0)
            if cursor:
                self.window.move(*cursor)
        except curses.error:
            pass
        self.window.noutrefresh()
        curses.doupdate()


class Monitor:
    def __init__(self, window, sampler, view, args, page):
        self.window, self.sampler, self.view, self.args = window, sampler, view, args
        self.screen, self.page = Screen(window, args.theme), page
        self.snapshot, self.last_sample = None, None
        self.net, self.disk, self.volume = args.interface, args.disk, None
        self.modal, self.input, self.fields = None, '', []
        self.field = 0
        self.message, self.message_until = '', 0
        self.offset, self.table_area = 0, None
        self.interval_controls = {}
        self.selected_modal, self.pending_signal = None, None
        self.detail_offset = 0
        self.detail_layout = None
        self.detail_lines = []
        self.theme_index = THEMES.index(args.theme)
        self.quitting, self.dirty = False, True

    def notice(self, value):
        self.message, self.message_until = value, time.monotonic() + 5

    def accept_snapshot(self):
        snapshot = self.sampler.latest
        if snapshot is None or snapshot['time'] == self.last_sample:
            return
        self.snapshot, self.last_sample = snapshot, snapshot['time']
        self.sampler.record(snapshot)
        self.view.update(snapshot['processes'])
        networks = snapshot['network']
        if self.net is None and networks:
            # One concrete interface, never sum VPN and underlying traffic.
            physical = {k: v for k, v in networks.items() if k.startswith('en')}
            self.net = max(physical or networks, key=lambda k: (networks[k]['received'] + networks[k]['sent']))
        if self.disk is None and snapshot['disks']:
            self.disk = sorted(snapshot['disks'])[0]
        if self.volume is None and snapshot['volumes']:
            self.volume = snapshot['volumes'][0]['mount']
        self.dirty = True

    def cpu_panel(self, y, x, h, w):
        s, snap = self.screen, self.snapshot
        s.box(y, x, h, w, 'CPU · ' + snap['brand'], 'cpu')
        s.put(y + 1, x + 2, percent(snap['cpu']), 'cpu', True)
        s.bar(y + 1, x + 10, max(1, w - 45), snap['cpu'], 'cpu')
        s.put(y + 1, x + w - 31, 'Load ' + ' '.join(f'{v:.2f}' for v in snap['load']), 'muted')
        cores = snap['cores']
        if h >= 6:
            columns = max(1, min(len(cores), (w - 4) // 24))
            cell = (w - 4) // columns
            for index, value in enumerate(cores[:columns * max(1, h - 5)]):
                cy, cx = y + 2 + index // columns, x + 2 + (index % columns) * cell
                s.put(cy, cx, f'C{index:<2} {percent(value):>4}', 'text')
                s.bar(cy, cx + 10, max(1, cell - 12), value, 'cpu')
        s.put(y + h - 2, x + 2, chart(self.sampler.history['cpu'], w - 4), 'cpu')

    def gpu_panel(self, y, x, h, w):
        gpu = self.snapshot['gpu'] or {}
        s = self.screen
        s.box(y, x, h, w, 'GPU · ' + gpu.get('name', 'GPU'), 'gpu')
        s.put(y + 1, x + 2, percent(gpu.get('utilization')), 'gpu', True)
        if w >= 75:
            s.bar(y + 1, x + 10, w - 62, gpu.get('utilization'), 'gpu')
        frequency = '--' if gpu.get('frequency') is None else f"{gpu['frequency']:.0f}MHz"
        power = '--' if gpu.get('power') is None else f"{gpu['power']:.2f}W"
        temp = '--' if gpu.get('temperature') is None else f"{gpu['temperature']:.0f}°C"
        s.put(y + 1, x + max(10, w - 48), f'{frequency}   {power}   {temp}', 'muted')
        if h >= 5:
            s.put(y + 2, x + 2, chart(self.sampler.history['gpu'], w - 4), 'gpu')
        note = gpu.get('note') or text('频率/功耗/温度：已安装 macmon 时补充',
                                      'Frequency/power/temperature: optional macmon')
        s.put(y + h - 2, x + 2, note, 'muted', limit=w - 4)

    def memory_panel(self, y, x, h, w):
        s, mem = self.screen, self.snapshot['memory']
        s.box(y, x, h, w, text('内存 · RAM', 'Memory · RAM'), 'memory')
        if not mem:
            s.put(y + 1, x + 2, text('不可用', 'Unavailable'), 'warning')
            return
        used = 100 * mem['used'] / mem['total']
        s.put(y + 1, x + 2, f"{size_text(mem['used'])} / {size_text(mem['total'])}  {used:.0f}%", 'memory', True, w - 4)
        s.bar(y + 2, x + 2, w - 4, used, 'memory')
        if h >= 7:
            s.put(y + 3, x + 2, text('可用 ', 'Available ') + size_text(mem['available']), limit=w - 4)
            s.put(y + 4, x + 2, text('压缩 ', 'Compressed ') + size_text(mem['compressed']), limit=w - 4)
            s.put(y + 5, x + 2, f"Swap {size_text(mem['swap_used'])} / {size_text(mem['swap_total'])}", 'muted', limit=w - 4)
        s.put(y + h - 2, x + 2, chart(self.sampler.history['memory'], w - 4), 'memory')

    def disk_panel(self, y, x, h, w):
        s = self.screen
        s.box(y, x, h, w, text('磁盘 ', 'Disk ') + (self.disk or '--') + ' · d / v', 'disk')
        volumes = self.snapshot['volumes']
        volume = next((v for v in volumes if v['mount'] == self.volume), None)
        if volume:
            label = 'Data' if volume['mount'] == '/System/Volumes/Data' else volume['mount']
            s.put(y + 1, x + 2, f"{label}  {size_text(volume['used'])}/{size_text(volume['total'])}", limit=w - 4)
            s.bar(y + 2, x + 2, w - 4, volume['used'] / max(1, volume['total']) * 100, 'disk')
        else:
            s.put(y + 1, x + 2, text('容量不可用', 'Capacity unavailable'), 'warning')
        rates = self.snapshot['disks'].get(self.disk, {})
        if h >= 6:
            s.put(y + 3, x + 2, text('读 ', 'R ') + size_text(rates.get('read')) + '/s   ' +
                  text('写 ', 'W ') + size_text(rates.get('write')) + '/s', limit=w - 4)
        histories = self.sampler.io_history['disks'].get(self.disk)
        if histories:
            maximum = max([1] + [v for history in histories for v in history if v is not None])
            s.put(y + h - 2, x + 2, chart(histories[0], (w - 5) // 2, maximum) + ' ' +
                  chart(histories[1], (w - 5) // 2, maximum), 'disk')

    def net_panel(self, y, x, h, w):
        s = self.screen
        s.box(y, x, h, w, text('网络 ', 'Network ') + (self.net or '--') + ' · n', 'network')
        item = self.snapshot['network'].get(self.net)
        if item is None:
            s.put(y + 1, x + 2, text('网卡不可用；n 切换', 'Unavailable; n to switch'), 'warning', limit=w - 4)
            return
        s.put(y + 1, x + 2, '↓ ' + size_text(item['read']) + '/s   ↑ ' + size_text(item['write']) + '/s', 'network', True, w - 4)
        if h >= 6:
            s.put(y + 2, x + 2, text('累计 ↓ ', 'Total ↓ ') + size_text(item['received']), 'muted', limit=w - 4)
            s.put(y + 3, x + 2, '     ↑ ' + size_text(item['sent']), 'muted', limit=w - 4)
        histories = self.sampler.io_history['network'].get(self.net)
        if histories:
            maximum = max([1] + [v for history in histories for v in history if v is not None])
            s.put(y + h - 2, x + 2, chart(histories[0], w - 4, maximum), 'network')

    def process_panel(self, y, x, h, w):
        s, view = self.screen, self.view
        title = text('进程', 'Processes') + f" · {len(view.rows)}/{len(self.snapshot['processes'])}"
        title += ' · ' + view.sort + (' ↓' if view.descending else ' ↑')
        if view.frozen:
            title += text(' · 固定顺序', ' · order held')
        s.box(y, x, h, w, title)
        filters = []
        if view.search:
            filters.append('/ ' + view.search)
        if view.minimum is not None:
            filters.append('RSS ≥ ' + size_text(view.minimum))
        if view.maximum is not None:
            filters.append('RSS ≤ ' + size_text(view.maximum))
        if view.user:
            filters.append('user: ' + view.user)
        s.put(y + 1, x + 2, ' · '.join(filters) if filters else text('/ 搜索名称、PID、命令   f 内存/用户过滤',
                                                                  '/ search name, PID, command   f memory/user'),
              'accent' if filters else 'muted', limit=w - 4)
        content = w - 4
        extended = content >= 73
        name_width = max(8, content - (47 if extended else 30))
        header = f"{'PID':>7}  {fit(text('名称', 'Name'), name_width, True)} {'CPU%':>6} {'RSS':>10}"
        if extended:
            header += f" {'Threads':>7} {'User':<9}"
        s.put(y + 2, x + 2, header, 'muted', True, content)
        visible = max(0, h - 4)
        index = next((i for i, p in enumerate(view.rows) if p.identity == view.selected), 0)
        self.offset = max(0, min(self.offset, len(view.rows) - visible))
        if index < self.offset:
            self.offset = index
        if index >= self.offset + visible:
            self.offset = max(0, index - visible + 1)
        self.table_area = (y + 3, x, visible, w)
        if not view.rows:
            s.put(y + 4, x + 2, text('没有匹配进程；Esc 清除过滤', 'No matches; Esc clears filters'), 'warning', limit=content)
        for offset, process in enumerate(view.rows[self.offset:self.offset + visible]):
            row = f'{process.pid:>7}  {fit(process.name, name_width, True)} {process.cpu:>6.1f} {size_text(process.rss):>10}'
            if extended:
                row += f' {process.threads:>7} {fit(process.user, 9)}'
            selected = process.identity == view.selected
            s.put(y + 3 + offset, x + 2, fit(row, content, True), 'selected' if selected else 'text', selected, content)
            if not selected:
                cpu_x = x + 2 + 9 + name_width + 1
                if process.cpu >= 20:
                    s.put(y + 3 + offset, cpu_x, f'{process.cpu:>6.1f}', 'cpu', limit=6)
                if process.rss >= 512 * 1024**2:
                    s.put(y + 3 + offset, cpu_x + 7, f'{size_text(process.rss):>10}', 'memory', limit=10)

    def draw(self):
        s = self.screen
        s.begin()
        h, w = s.height, s.columns - 1
        s.put(0, 1, 'ma top', 'accent', True)
        milliseconds = f'{round(self.sampler.interval * 1000)} ms'
        control_width = len(milliseconds) + 8
        control_x = max(1, w - control_width)
        clock_x = control_x - 11
        s.put(0, 10, s.theme, 'muted', limit=max(0, clock_x - 12))
        if clock_x >= 20:
            s.put(0, clock_x, time.strftime('%H:%M:%S'), 'muted')
        s.put(0, control_x, '[-]', 'accent', True)
        s.put(0, control_x + 4, milliseconds, 'text', True)
        s.put(0, control_x + control_width - 3, '[+]', 'accent', True)
        self.interval_controls = {
            'minus': (control_x, control_x + 3),
            'value': (control_x + 4, control_x + 4 + len(milliseconds)),
            'plus': (control_x + control_width - 3, control_x + control_width),
        }
        self.table_area = None
        if w < 60 or h < 18:
            s.put(3, 2, text('窗口至少需要 60 列 × 18 行', 'Minimum terminal: 60 columns × 18 rows'), 'warning')
            s.put(5, 2, text('放大窗口；q 退出', 'Enlarge the window; q quits'))
        elif not self.snapshot:
            s.put(3, 2, text('正在读取系统指标…', 'Reading system metrics…'), 'accent')
        elif w >= 110 and h >= 36:
            self.cpu_panel(2, 0, 8, w)
            self.gpu_panel(10, 0, 5, w)
            lower = h - 18
            left = max(34, min(46, w // 3))
            mh, dh = max(6, lower // 3), max(6, lower // 3)
            self.memory_panel(15, 0, mh, left)
            self.disk_panel(15 + mh, 0, dh, left)
            self.net_panel(15 + mh + dh, 0, lower - mh - dh, left)
            self.process_panel(15, left + 1, lower, w - left - 1)
        elif self.page == 'processes':
            memory = self.snapshot['memory']
            summary = 'CPU ' + percent(self.snapshot['cpu']) + '  GPU ' + percent((self.snapshot['gpu'] or {}).get('utilization'))
            if memory:
                summary += '  RAM ' + size_text(memory['used']) + '/' + size_text(memory['total'])
            s.put(2, 1, summary, 'accent')
            self.process_panel(4, 0, h - 7, w)
        else:
            if h < 28:
                memory = self.snapshot['memory'] or {}
                gpu = self.snapshot['gpu'] or {}
                net = self.snapshot['network'].get(self.net, {})
                disk = self.snapshot['disks'].get(self.disk, {})
                volume = next((v for v in self.snapshot['volumes'] if v['mount'] == self.volume), {})
                summaries = [
                    ('CPU · ' + self.snapshot['brand'], percent(self.snapshot['cpu'])),
                    ('GPU · ' + gpu.get('name', 'GPU'), percent(gpu.get('utilization'))),
                    (text('内存', 'Memory'), size_text(memory.get('used')) + ' / ' + size_text(memory.get('total'))),
                    (text('磁盘 ', 'Disk ') + (self.disk or '--'), size_text(volume.get('used')) + '/' + size_text(volume.get('total')) +
                     '  R ' + size_text(disk.get('read')) + '/s W ' + size_text(disk.get('write')) + '/s'),
                    (text('网络 ', 'Network ') + (self.net or '--'), '↓ ' + size_text(net.get('read')) + '/s ↑ ' + size_text(net.get('write')) + '/s')]
                for index, (title, value) in enumerate(summaries):
                    s.put(2 + index * 2, 2, title, 'accent', True)
                    s.put(3 + index * 2, 4, value, limit=w - 6)
                s.put(13, 2, text('n 网卡 · d 磁盘 · v 容量卷 · Tab 进程', 'n interface · d disk · v volume · Tab processes'), 'muted')
            elif h >= 32:
                self.cpu_panel(2, 0, 7, w)
                self.gpu_panel(9, 0, 5, w)
                mh = max(6, (h - 17) // 2)
                self.memory_panel(14, 0, mh, w // 2)
                self.disk_panel(14, w // 2 + 1, mh, w - w // 2 - 1)
                self.net_panel(14 + mh, 0, h - 17 - mh, w)
            else:
                self.cpu_panel(2, 0, 5, w)
                self.gpu_panel(7, 0, 4, w)
                self.memory_panel(11, 0, 5, w // 2)
                self.disk_panel(11, w // 2 + 1, 5, w - w // 2 - 1)
                if h >= 22:
                    self.net_panel(16, 0, h - 19, w)
        status = self.sampler.error
        if not status and self.snapshot and self.snapshot['errors']:
            status = '; '.join(self.snapshot['errors'])
        if self.message and time.monotonic() < self.message_until:
            status = self.message
        if not status and self.snapshot:
            stale = time.monotonic() - self.snapshot['time']
            status = (text('数据已过期 ', 'Stale data ') + f'{stale:.0f}s') if stale > max(5, self.sampler.interval * 3) else (
                text('采集 ', 'Sample ') + f"{self.snapshot['sample_ms']:.1f}ms · RSS · " +
                text('Tab 切换概览/进程', 'Tab overview/processes'))
        s.put(h - 3, 1, status or '', 'warning' if self.sampler.error else 'muted')
        s.put(h - 2, 1, text('/ 搜索  f 过滤  m 内存  c CPU  k 结束  t 主题  ? 帮助  q 退出',
                            '/ search  f filter  m memory  c CPU  k end  t theme  ? help  q quit'), 'accent')
        cursor = self.draw_modal() if self.modal else None
        s.flush(cursor)

    def modal_rows(self, title, height=12):
        s = self.screen
        h, w = min(height, s.height - 2), min(86, s.columns - 5)
        y, x = max(1, (s.height - h) // 2), max(1, (s.columns - w) // 2)
        for row in range(y, y + h):
            s.clear_row(row)
        s.box(y, x, h, w, title)
        return y, x, h, w

    def draw_modal(self):
        s = self.screen
        if self.modal == 'interval':
            y, x, h, w = self.modal_rows(text('刷新间隔', 'Refresh interval'), 9)
            value = fit(self.input, w - 16)
            s.put(y + 2, x + 3, text('毫秒：', 'ms: ') + value, 'accent')
            s.put(y + 4, x + 3, text('范围 500–60000 ms；默认 1000 ms', 'Range 500–60000 ms; default 1000 ms'), 'muted', limit=w - 6)
            s.put(y + 6, x + 3, text('Enter 应用 · Esc 取消', 'Enter applies · Esc cancels'), 'muted')
            return (y + 2, min(x + w - 2, x + 3 + width(text('毫秒：', 'ms: ')) + width(value)))
        if self.modal == 'search':
            y, x, h, w = self.modal_rows(text('搜索进程', 'Search processes'), 6)
            value = fit(self.input, w - 8)
            s.put(y + 2, x + 3, '/ ' + value, 'accent')
            s.put(y + 4, x + 3, text('Enter 应用 · Esc 取消', 'Enter applies · Esc cancels'), 'muted')
            return (y + 2, min(x + w - 2, x + 5 + width(value)))
        if self.modal == 'filter':
            y, x, h, w = self.modal_rows(text('过滤进程 · 内存为 RSS', 'Filter processes · memory is RSS'), 11)
            labels = [text('内存下限', 'Min memory'), text('内存上限', 'Max memory'), text('用户 / UID', 'User / UID')]
            for index, label in enumerate(labels):
                s.put(y + 2 + index * 2, x + 3, fit(label, 17, True) + self.fields[index],
                      'selected' if index == self.field else 'text', limit=w - 6)
            s.put(y + h - 3, x + 3, text('500MiB / 1GiB；留空不限', '500MiB / 1GiB; blank means any'), 'muted')
            s.put(y + h - 2, x + 3, text('Tab 切换 · Enter 应用 · Esc 取消', 'Tab next · Enter apply · Esc cancel'), 'muted')
            return (y + 2 + self.field * 2, min(x + w - 2, x + 20 + width(self.fields[self.field])))
        if self.modal == 'theme':
            y, x, h, w = self.modal_rows(text('主题 · 即时预览', 'Theme · live preview'), 11)
            for index, theme in enumerate(THEMES):
                s.put(y + 2 + index, x + 3, ('› ' if index == self.theme_index else '  ') + theme,
                      'selected' if index == self.theme_index else 'text', True)
            s.put(y + h - 2, x + 3, text('↑↓ 预览 · Enter 保存 · Esc 取消', '↑↓ preview · Enter keep · Esc cancel'), 'muted')
        elif self.modal == 'confirm':
            p = self.selected_modal
            force = self.pending_signal == signal.SIGKILL
            y, x, h, w = self.modal_rows(text('强制结束进程' if force else '结束进程',
                                             'Force kill process' if force else 'End process'), 10)
            s.put(y + 2, x + 3, f'{p.name} · PID {p.pid} · {p.user}', 'danger', True, w - 6)
            command = self.detail_command(p)
            s.put(y + 3, x + 3, command, 'muted', limit=w - 6)
            s.put(y + 5, x + 3, 'SIGKILL (9)' if force else 'SIGTERM (15)', 'warning')
            s.put(y + 7, x + 3, text('y 确认 · 其他按键取消', 'y confirms · any other key cancels'), 'accent', True)
        elif self.modal == 'detail':
            p = self.selected_modal
            y, x, h, w = self.modal_rows(text('进程详情', 'Process details'), 15)
            s.put(y + 2, x + 3, f'{p.name} · PID {p.pid} · PPID {p.ppid}', 'accent', True, w - 6)
            s.put(y + 3, x + 3, f'{p.user} · CPU {p.cpu:.1f}% · RSS {size_text(p.rss)} · Threads {p.threads}', limit=w - 6)
            s.put(y + 4, x + 3, text('启动 ', 'Started ') + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.started[0])), 'muted')
            command = self.detail_command(p)
            layout = (p.identity, w, command)
            if self.detail_layout != layout:
                self.detail_layout = layout
                self.detail_lines = command_lines(command, w - 6)
            visible = max(1, h - 8)
            self.detail_offset = min(self.detail_offset, max(0, len(self.detail_lines) - visible))
            for index, line in enumerate(self.detail_lines[self.detail_offset:self.detail_offset + visible]):
                s.put(y + 6 + index, x + 3, line)
            s.put(y + h - 2, x + 3, text('↑↓ 滚动命令 · Esc 关闭', '↑↓ scroll command · Esc closes'), 'accent')
        elif self.modal == 'help':
            y, x, h, w = self.modal_rows(text('快捷键', 'Keyboard shortcuts'), 18)
            lines = [text('↑↓ / j l 选择；PgUp/PgDn 翻页；Enter 详情', '↑↓ / j l select; PgUp/PgDn page; Enter details'),
                     text('/ 搜索名称、PID、完整命令（权限允许时）', '/ searches name, PID, full command when permitted'),
                     text('f 内存上下限/用户；Esc 清除过滤', 'f memory range/user; Esc clears filters'),
                     text('m 内存排序；c CPU；p PID；a 名称；重复按反转', 'm memory; c CPU; p PID; a name; repeat reverses'),
                     text('Space 固定排列，数值继续更新', 'Space holds row order; values keep updating'),
                     text('k 请求结束；K 强制结束；都需确认', 'k terminate; K force kill; both need confirmation'),
                     text('n 网卡；d 物理磁盘；v 容量卷', 'n interface; d physical disk; v capacity volume'),
                     text('Tab 概览/进程；t 主题；i 设置刷新间隔', 'Tab overview/processes; t theme; i sets interval'),
                     text('右上角 -/+ 或键盘 -/+ 调整毫秒数', 'Top-right -/+ or keys -/+ change milliseconds'),
                     text('RSS 为驻留内存；进程 CPU 可超过 100%', 'RSS is resident memory; process CPU can exceed 100%'),
                     text('系统已用 = 总量 - 空闲页 - 非活跃页', 'System used = total - free pages - inactive pages'),
                     text('首个速率样本/不可用值显示 --', 'First rate sample / unavailable values show --'),
                     text('Esc 关闭；q 退出', 'Esc closes; q quits')]
            for index, line in enumerate(lines[:h - 3]):
                s.put(y + 2 + index, x + 3, line, 'text', limit=w - 6)
        return None

    def detail_command(self, process):
        detail = self.sampler.details
        if detail and detail[0] == process.identity:
            return detail[1] or detail[2] or text('命令不可读取（权限限制）', 'Command unavailable (permissions)')
        return process.command or text('正在读取完整命令…', 'Reading full command…')

    def start_process_modal(self, kind):
        process = self.view.selection()
        if process:
            self.selected_modal, self.modal = process, kind
            self.detail_offset = 0
            self.sampler.request_detail(process.identity)

    def apply_filter(self):
        try:
            minimum, maximum = memory_size(self.fields[0]), memory_size(self.fields[1])
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(text('内存下限不能超过上限', 'Minimum exceeds maximum'))
            self.view.minimum, self.view.maximum, self.view.user = minimum, maximum, self.fields[2].strip()
            self.view.frozen, self.modal = False, None
        except ValueError as error:
            self.notice(str(error))

    def edit_key(self, key, value):
        if key in (curses.KEY_BACKSPACE, '\b', '\x7f'):
            return value[:-1]
        if key == '\x15':
            return ''
        if isinstance(key, str) and key.isprintable() and len(value) < 256:
            return value + key
        return value

    def modal_key(self, key):
        if self.modal == 'confirm':
            if key == 'y':
                try:
                    signal_process(self.selected_modal, self.pending_signal)
                    self.notice(text('已发送信号，等待进程退出', 'Signal sent; waiting for process exit'))
                except OSError as error:
                    self.notice(text('结束失败：', 'Could not end process: ') + str(error))
            self.modal = None
        elif key == '\x1b':
            if self.modal == 'theme':
                self.screen.set_theme(self.original_theme)
            self.modal = None
            self.detail_layout, self.detail_lines = None, []
        elif self.modal == 'interval':
            if key in ('\n', '\r', curses.KEY_ENTER):
                try:
                    milliseconds = float(self.input)
                    if not math.isfinite(milliseconds) or not 500 <= milliseconds <= 60000:
                        raise ValueError
                    self.sampler.set_interval(round(milliseconds) / 1000)
                    self.modal = None
                except ValueError:
                    self.notice(text('刷新间隔需为 500–60000 毫秒', 'Interval must be 500–60000 milliseconds'))
            else:
                self.input = self.edit_key(key, self.input)
        elif self.modal == 'search':
            if key in ('\n', '\r', curses.KEY_ENTER):
                self.view.search, self.modal, self.view.frozen = self.input, None, False
                self.sampler.set_search(bool(self.input))
            else:
                self.input = self.edit_key(key, self.input)
        elif self.modal == 'filter':
            if key in ('\t', curses.KEY_DOWN):
                self.field = (self.field + 1) % 3
            elif key in (curses.KEY_BTAB, curses.KEY_UP):
                self.field = (self.field - 1) % 3
            elif key in ('\n', '\r', curses.KEY_ENTER):
                self.apply_filter()
            else:
                self.fields[self.field] = self.edit_key(key, self.fields[self.field])
        elif self.modal == 'theme':
            if key in (curses.KEY_UP, curses.KEY_DOWN, 'j', 'l'):
                self.theme_index = (self.theme_index + (1 if key in (curses.KEY_DOWN, 'j') else -1)) % len(THEMES)
                self.screen.set_theme(THEMES[self.theme_index])
            elif key in ('\n', '\r', curses.KEY_ENTER):
                self.modal = None
        elif self.modal == 'detail':
            if key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                self.detail_offset += 1 if key == curses.KEY_DOWN else 5
            elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                self.detail_offset = max(0, self.detail_offset - (1 if key == curses.KEY_UP else 5))

    def cycle(self, current, choices):
        choices = list(choices)
        return choices[(choices.index(current) + 1) % len(choices)] if current in choices else (choices[0] if choices else None)

    def adjust_interval(self, direction):
        milliseconds = round(self.sampler.interval * 1000)
        self.sampler.set_interval(max(500, min(60000, milliseconds + direction * 100)) / 1000)

    def edit_interval(self):
        self.modal, self.input = 'interval', str(round(self.sampler.interval * 1000))

    def key(self, key):
        self.dirty = True
        if key == curses.KEY_RESIZE:
            self.screen.previous = {}
            return
        if key == '\x03':
            self.quitting = True
            return
        if self.modal:
            self.modal_key(key)
        elif key == 'q':
            self.quitting = True
        elif key in (curses.KEY_DOWN, 'j'):
            self.view.move(1)
        elif key in (curses.KEY_UP, 'l'):
            self.view.move(-1)
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            self.view.move((1 if key == curses.KEY_NPAGE else -1) * max(1, (self.table_area or (0, 0, 10, 0))[2]))
        elif key in (curses.KEY_HOME, curses.KEY_END):
            self.view.move(-len(self.view.rows) if key == curses.KEY_HOME else len(self.view.rows))
        elif key in ('m', 'c', 'p', 'a'):
            self.view.sorting({'m': 'memory', 'c': 'cpu', 'p': 'pid', 'a': 'name'}[key])
        elif key == ' ':
            self.view.frozen = not self.view.frozen
        elif key == '/':
            self.modal, self.input = 'search', self.view.search
        elif key == 'f':
            self.modal, self.field = 'filter', 0
            self.fields = ['' if self.view.minimum is None else str(self.view.minimum),
                           '' if self.view.maximum is None else str(self.view.maximum), self.view.user]
        elif key == '\x1b':
            self.view.search, self.view.user, self.view.minimum, self.view.maximum = '', '', None, None
            self.view.frozen = False
            self.sampler.set_search(False)
        elif key in ('\n', '\r', curses.KEY_ENTER):
            self.start_process_modal('detail')
        elif key in ('k', 'K'):
            self.pending_signal = signal.SIGTERM if key == 'k' else signal.SIGKILL
            self.start_process_modal('confirm')
        elif key == 't':
            self.original_theme, self.modal = self.screen.theme, 'theme'
            self.theme_index = THEMES.index(self.screen.theme)
        elif key == '?':
            self.modal = 'help'
        elif key == '\t':
            self.page = 'overview' if self.page == 'processes' else 'processes'
        elif key == 'i':
            self.edit_interval()
        elif key in ('+', '-', '='):
            self.adjust_interval(-1 if key == '-' else 1)
        elif self.snapshot and key == 'n':
            self.net = self.cycle(self.net, sorted(self.snapshot['network']))
        elif self.snapshot and key == 'd':
            self.disk = self.cycle(self.disk, sorted(self.snapshot['disks']))
        elif self.snapshot and key == 'v':
            self.volume = self.cycle(self.volume, [v['mount'] for v in self.snapshot['volumes']])
        elif key == curses.KEY_MOUSE:
            try:
                _, x, y, _, button = curses.getmouse()
                if y == 0 and button & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                    for name, (start, end) in self.interval_controls.items():
                        if start <= x < end:
                            if name == 'value':
                                self.edit_interval()
                            else:
                                self.adjust_interval(-1 if name == 'minus' else 1)
                            break
                elif button & getattr(curses, 'BUTTON4_PRESSED', 0):
                    self.view.move(-3)
                elif button & getattr(curses, 'BUTTON5_PRESSED', 0):
                    self.view.move(3)
                elif self.table_area:
                    top, left, count, columns = self.table_area
                    index = self.offset + y - top
                    if top <= y < top + count and left <= x < left + columns and index < len(self.view.rows):
                        self.view.selected = self.view.rows[index].identity
            except curses.error:
                pass
        if self.snapshot:
            self.view.update(self.snapshot['processes'])

    def run(self):
        self.window.keypad(True)
        self.window.timeout(25)
        try:
            curses.set_escdelay(25)
        except AttributeError:
            pass
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except curses.error:
            pass
        last_second = None
        last_details = None
        while not self.quitting:
            self.accept_snapshot()
            if self.sampler.details is not last_details:
                last_details, self.dirty = self.sampler.details, True
            second = int(time.monotonic())
            if second != last_second and (self.snapshot is None or time.monotonic() - self.snapshot['time'] > 2):
                self.dirty, last_second = True, second
            if self.dirty:
                self.draw()
                self.dirty = False
            try:
                key = self.window.get_wch()
            except curses.error:
                continue
            self.key(key)
        return dict(theme=self.screen.theme, interval=self.sampler.interval, page=self.page)


def run(sampler, view, args, page):
    locale.setlocale(locale.LC_ALL, '')
    # Apple's ncurses 6.0 truncates wide glyphs passed to kitty's REP
    # capability (U+2501 -> SOH, U+2582 -> invalid 0x82). The compatible
    # xterm profile retains 256 colors/input without that capability.
    original_term = os.environ.get('TERM')
    version = getattr(curses, 'ncurses_version', (0, 0, 0))
    if original_term == 'xterm-kitty' and tuple(version[:2]) <= (6, 0):
        os.environ['TERM'] = 'xterm-256color'
    try:
        return curses.wrapper(lambda window: Monitor(window, sampler, view, args, page).run())
    finally:
        if original_term is None:
            os.environ.pop('TERM', None)
        else:
            os.environ['TERM'] = original_term
