#!/usr/bin/env python3
"""Render README terminal demos on macOS (development deps: pillow, pyte).

Use --capture to open each current selector in a private pseudo-terminal and
exit without accepting a selection. --only help install remove optimize top
refreshes README's five screenshots. No installations, removals, or cleanup
actions are executed.
Without --capture, render existing .reference/*.typescript recordings.
"""

import argparse
import os
from pathlib import Path
import tempfile
import unicodedata

from PIL import Image, ImageDraw, ImageFont
import pyte

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs' / 'images'
COLS, ROWS = 160, 35
DEMOS = (
    ('install', 'install-status.png', ['maci', '--dry-run', '--formula', '--exact', 'fish'], 'maci fish'),
    ('remove', 'remove-color.png', ['macr', '--dry-run', '--exact', 'chrome'], 'macr chrome'),
    ('clean', 'clean-color.png', ['macc', '--dry-run'], 'ma clean'),
    ('optimize', 'optimize-color.png', ['maco'], 'ma optimize'),
    ('ai', 'ai-color.png', ['maca', 'install', '--dry-run'], 'ma ai'),
    ('top', 'top-color.png', ['mato', '--no-macmon', '--theme', 'terminal', '--sort', 'memory'], 'mato --sort memory'),
)
COLORS = {
    'default': '#d6deeb', 'black': '#10151d', 'red': '#ef7382',
    'green': '#a7cf90', 'brown': '#e3c885', 'blue': '#87bdf4',
    'magenta': '#cda2ed', 'cyan': '#84c8cc', 'white': '#e6edf3',
    'brightblack': '#5b6473', 'brightwhite': '#ffffff',
}


def recording(path, interactive=False, rows=ROWS):
    screen = pyte.Screen(COLS, rows)
    stream = pyte.Stream(screen)
    # Preserve CRLF: LF alone advances pyte's row without resetting its column.
    data = path.read_bytes().decode('utf-8', errors='replace')
    # fzf clears its screen when exiting: render the last frame before cleanup.
    if interactive:
        marker = '\x1b[?1000l'
        end = data.rfind(marker)
        if end >= 0:
            clear = data.rfind('\x1b[J', 0, end)
            data = data[:clear if clear >= 0 else end]
    stream.feed(data)
    return screen


def color(value, default):
    if value == 'default':
        return default
    return COLORS.get(value, '#' + value if len(value) == 6 else default)


def render(screen, filename, command):
    regular = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 16)
    chinese_path = Path('/System/Library/Fonts/PingFang.ttc')
    if not chinese_path.exists():
        chinese_path = next(Path('/System/Library/AssetsV2').glob(
            'com_apple_MobileAsset_Font*/*.asset/AssetData/PingFang.ttc'),
            Path('/System/Library/Fonts/STHeiti Medium.ttc'))
    chinese = ImageFont.truetype(str(chinese_path), 16)
    cell_w, cell_h, padding, bar = 10, 23, 24, 48
    visible_rows = max((index + 1 for index, line in enumerate(screen.display) if line.strip()), default=1)
    image = Image.new('RGB', (screen.columns * cell_w + padding * 2,
                             visible_rows * cell_h + padding * 2 + bar), '#10151d')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, bar), fill='#1c2430')
    for index, fill in enumerate(('#ff6059', '#ffbd2e', '#28c840')):
        x = padding + index * 24
        draw.ellipse((x, 17, x + 12, 29), fill=fill)
    draw.text((padding + 105, 12), command, font=regular, fill='#c8d2df')
    for row in range(visible_rows):
        for column in range(screen.columns):
            char = screen.buffer[row][column]
            if not char.data:
                continue
            wide = unicodedata.east_asian_width(char.data[0]) in ('W', 'F')
            x, y = padding + column * cell_w, padding + bar + row * cell_h
            fg, bg = color(char.fg, '#d6deeb'), color(char.bg, '#10151d')
            if char.reverse:
                fg, bg = bg, fg
            draw.rectangle((x, y, x + cell_w * (2 if wide else 1) - 1,
                            y + cell_h - 1), fill=bg)
            draw.text((x, y), char.data, font=chinese if wide else regular, fill=fg)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT / filename, optimize=True)


def capture(arguments, seconds=8, rows=ROWS):
    # Terminal-only dependencies stay out of the recording renderer.
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios
    import time

    settings = tempfile.TemporaryDirectory(prefix='mactools-demo-')
    child, fd = pty.fork()
    if child == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', rows, COLS, 0, 0))
        env = dict(os.environ, TERM='xterm-256color', MACTOOLS_LANG='zh', NO_COLOR='',
                   XDG_CONFIG_HOME=settings.name)
        executable = str(ROOT / 'bin' / arguments[0])
        os.execve(executable, [executable, *arguments[1:]], env)
    data = bytearray()
    finished = False
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                data.extend(chunk)
                if b'\x1b[6n' in chunk:
                    os.write(fd, b'\x1b[1;1R')
        # Capture the live frame BEFORE quitting triggers screen cleanup.
        finished = bool(os.waitpid(child, os.WNOHANG)[0])
        if not finished:
            os.write(fd, b'q' if arguments[0] == 'mato' else b'\x1b')
        deadline = time.monotonic() + 3
        while not finished and time.monotonic() < deadline:
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    os.read(fd, 65536)
                except OSError:
                    pass
            if os.waitpid(child, os.WNOHANG)[0]:
                finished = True
                break
    finally:
        if not finished:
            try:
                os.killpg(child, signal.SIGTERM)
            except ProcessLookupError:
                pass
            os.waitpid(child, 0)
        os.close(fd)
        settings.cleanup()
    return bytes(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--only', nargs='+', choices=['help', *(demo[0] for demo in DEMOS)],
                        help='Refresh only the named screenshots, e.g. help install top')
    args = parser.parse_args()
    recordings = ROOT / '.reference'
    selected = set(args.only or ['help', *(demo[0] for demo in DEMOS)])
    if args.capture:
        recordings.mkdir(exist_ok=True)
        if 'help' in selected:
            (recordings / 'help.typescript').write_bytes(capture(['ma', '--help'], seconds=1))
        for name, filename, arguments, title in DEMOS:
            if name not in selected:
                continue
            (recordings / (name + '.typescript')).write_bytes(capture(
                arguments, seconds=12 if name == 'top' else 8, rows=44 if name == 'top' else ROWS))
            print('Captured ' + title, flush=True)
    if 'help' in selected:
        render(recording(recordings / 'help.typescript'), 'commands-color.png', 'ma --help')
    for name, filename, arguments, title in DEMOS:
        if name not in selected:
            continue
        path = recordings / (name + '.typescript')
        if path.exists():
            render(recording(path, interactive=not args.capture and name != 'top',
                             rows=44 if name == 'top' else ROWS), filename, title)
    print('Saved terminal screenshots in docs/images/')
