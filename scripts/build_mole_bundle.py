#!/usr/bin/env python3
"""Reproducibly bundle the vendored, readable GPL source for both installers."""
import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    source = ROOT / 'third_party/mole'
    target = ROOT / 'bin/mactools_mole.zip'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob('*')):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (2026, 10, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    module = ROOT / 'bin/mactools_mole.py'
    module.write_text(re.sub(r"BUNDLE_SHA256 = '[^']*'", f"BUNDLE_SHA256 = '{digest}'", module.read_text()))
    print(digest)


if __name__ == '__main__':
    build()
