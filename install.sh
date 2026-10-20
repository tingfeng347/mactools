#!/bin/bash
# Standalone, Bash 3.2 compatible installer. Also supports curl | bash.
set -euo pipefail
install_mactools() {
    local bin_dir="${BIN_DIR:-$HOME/.local/bin}" dry_run=false install_deps=false
    local script_dir stage file base ready=false
    while (( $# )); do
        case "$1" in
            --bin-dir) [[ $# -ge 2 && -n "$2" ]] || { echo '--bin-dir requires a path' >&2; return 2; }; bin_dir="$2"; shift;;
            --dry-run) dry_run=true;;
            --install-deps) install_deps=true;;
            --help|-h) echo 'Usage: bash install.sh [--bin-dir DIR] [--dry-run] [--install-deps]'; return 0;;
            *) echo "Unknown option: $1" >&2; return 2;;
        esac
        shift
    done
    [[ "$bin_dir" == /* && "$bin_dir" != / ]] || { echo 'Install directory must be absolute and cannot be /' >&2; return 2; }
    [[ "$(uname -s)" == Darwin ]] || { echo 'mactools supports macOS only.' >&2; return 1; }
    if [[ "$install_deps" == true && "$dry_run" == false ]]; then
        local brew_cmd=""
        brew_cmd="$(command -v brew || true)"
        if [[ -z "$brew_cmd" ]]; then
            for file in /opt/homebrew/bin/brew /usr/local/bin/brew; do
                if [[ -x "$file" ]]; then brew_cmd="$file"; break; fi
            done
        fi
        [[ -n "$brew_cmd" ]] || { echo 'Install Homebrew first: https://brew.sh' >&2; return 1; }
        "$brew_cmd" install python fzf
    fi
    command -v python3 >/dev/null 2>&1 || { echo 'Python 3.9+ required. Run brew install python fzf, then retry.' >&2; return 1; }
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || { echo 'Python 3.9+ required.' >&2; return 1; }
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    stage="$(mktemp -d "${TMPDIR:-/tmp}/mactools-install.XXXXXX")"
    trap "rm -rf -- $(printf '%q' "$stage")" EXIT
    local files=(ma maci macr macu macd macc macm maca mactools.py mactools_runtime.py mactools_packages.py mactools_system.py mactools_cleanup.py mactools_mirror.py mactools_ai.py)
    local local_ready=true
    for file in "${files[@]}"; do
        [[ -s "$script_dir/bin/$file" ]] || local_ready=false
    done
    mkdir -p "$stage/bin"
    if [[ "$local_ready" == true ]]; then
        for file in "${files[@]}"; do cp -- "$script_dir/bin/$file" "$stage/bin/$file"; done
        ready=true
    else
        local bases=("https://raw.githubusercontent.com/tingfeng347/mactools/main" "https://cdn.jsdelivr.net/gh/tingfeng347/mactools@main")
        if [[ -n "${MACTOOLS_DOWNLOAD_BASES:-}" ]]; then
            IFS=' ' read -r -a bases <<< "$MACTOOLS_DOWNLOAD_BASES"
        fi
        for base in "${bases[@]}"; do
            ready=true
            for file in "${files[@]}"; do
                printf 'Download %s\n' "$file"
                if ! curl -fsSL --connect-timeout 10 --max-time 30 --retry 1 "$base/bin/$file" -o "$stage/bin/$file" || [[ ! -s "$stage/bin/$file" ]]; then
                    ready=false
                    break
                fi
            done
            [[ "$ready" == true ]] && break
        done
    fi
    [[ "$ready" == true ]] || { echo 'Download incomplete; existing installation untouched.' >&2; return 1; }
    python3 - "$stage/bin" "$bin_dir" "$dry_run" <<'PY'
import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
dry_run = sys.argv[3] == 'true'
files = sorted(source.iterdir(), key=lambda p: p.name == 'ma')
# Check the complete staged payload before touching the installation.
for path in files:
    if path.suffix == '.py':
        ast.parse(path.read_text(), filename=str(path))
    else:
        if not path.read_bytes().startswith(b'#!/bin/bash\n'):
            raise SystemExit(f'Invalid launcher: {path.name}')
        subprocess.run(['/bin/bash', '-n', str(path)], check=True)
    if (target / path.name).is_dir() and not (target / path.name).is_symlink():
        raise SystemExit(f'Refusing to overwrite directory: {target / path.name}')
if dry_run:
    for path in files:
        print(f'Install: {target / path.name}')
    raise SystemExit(0)
target.mkdir(parents=True, exist_ok=True)
# Back up every old payload before replacement, preserving symlinks on rollback.
with tempfile.TemporaryDirectory(prefix='.mactools-stage-', dir=target) as temporary:
    temp = Path(temporary)
    existed = []
    written = []
    for path in files:
        destination = target / path.name
        if destination.exists() or destination.is_symlink():
            shutil.copy2(destination, temp / (path.name + '.old'), follow_symlinks=False)
            existed.append(path.name)
        staged = temp / path.name
        shutil.copyfile(path, staged)
        staged.chmod(0o644 if path.suffix == '.py' else 0o755)
    try:
        for path in files:
            os.replace(temp / path.name, target / path.name)
            written.append(path.name)
    except BaseException:
        for name in reversed(written):
            if name in existed:
                os.replace(temp / (name + '.old'), target / name)
            else:
                (target / name).unlink()
        raise
print(f'mactools installed in {target}')
PY
    if [[ "$dry_run" == true ]]; then
        echo 'Preview complete; no installation files changed.'
    else
        printf 'Commands: ma install / maci, macr, macu, macd, macc, macm, maca\n'
        printf 'Add this directory to PATH if needed: %s\n' "$bin_dir"
        echo 'Interactive package selection requires fzf: brew install fzf'
    fi
    rm -rf -- "$stage"
    trap - EXIT
}
install_mactools "$@"
