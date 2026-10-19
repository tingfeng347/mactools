#!/usr/bin/env python3
"""Render recorded terminal sessions for README screenshots on macOS.

Optional development dependencies: pip install pillow pyte
Record in a 160-column, 32-row terminal from the repository root:
    stty cols 160 rows 32
    env -u NO_COLOR TERM=xterm-256color script -q .reference/help.typescript ./bin/ma --help
    env -u NO_COLOR TERM=xterm-256color script -q .reference/install.typescript ./bin/maci --dry-run --cask --exact firefox
Exit the selector with ESC after the preview loads. Then run:
    python scripts/capture_demo.py
"""

from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont
import pyte

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs' / 'images'
COLS, ROWS = 160, 35
COLORS = {
    'default': '#d6deeb', 'black': '#10151d', 'red': '#ef7382',
    'green': '#a7cf90', 'brown': '#e3c885', 'blue': '#87bdf4',
    'magenta': '#cda2ed', 'cyan': '#84c8cc', 'white': '#e6edf3',
    'brightblack': '#5b6473', 'brightwhite': '#ffffff',
}


def recording(path, interactive=False):
    screen = pyte.Screen(COLS, ROWS)
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
    chinese = ImageFont.truetype('/System/Library/Fonts/PingFang.ttc', 16, index=2)
    cell_w, cell_h, padding, bar = 10, 23, 24, 48
    visible_rows = max((index + 1 for index, line in enumerate(screen.display) if line.strip()), default=1)
    image = Image.new('RGB', (COLS * cell_w + padding * 2,
                             visible_rows * cell_h + padding * 2 + bar), '#10151d')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, bar), fill='#1c2430')
    for index, fill in enumerate(('#ff6059', '#ffbd2e', '#28c840')):
        x = padding + index * 24
        draw.ellipse((x, 17, x + 12, 29), fill=fill)
    draw.text((padding + 105, 12), command, font=regular, fill='#c8d2df')
    for row in range(visible_rows):
        for column in range(COLS):
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


if __name__ == '__main__':
    render(recording(ROOT / '.reference' / 'help.typescript'), 'commands-color.png', 'ma --help')
    render(recording(ROOT / '.reference' / 'install.typescript', interactive=True),
           'install-color.png', 'maci --cask --exact firefox  # dry-run demo')
    print('Saved docs/images/commands-color.png and docs/images/install-color.png')
