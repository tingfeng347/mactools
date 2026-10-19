#!/bin/bash
# Removes only mactools payload. Does not remove applications or their data.
set -euo pipefail
uninstall_mactools() {
    local bin_dir="${BIN_DIR:-$HOME/.local/bin}" dry_run=false purge_cache=false file
    while (( $# )); do
        case "$1" in
            --bin-dir) [[ $# -ge 2 && -n "$2" ]] || { echo '--bin-dir requires a path' >&2; return 2; }; bin_dir="$2"; shift;;
            --dry-run) dry_run=true;;
            --purge-cache) purge_cache=true;;
            --help|-h) echo 'Usage: bash uninstall.sh [--bin-dir DIR] [--dry-run] [--purge-cache]'; return 0;;
            *) echo "Unknown option: $1" >&2; return 2;;
        esac
        shift
    done
    [[ "$bin_dir" == /* && "$bin_dir" != / ]] || { echo 'Install directory must be absolute and cannot be /' >&2; return 2; }
    local files=(ma maci macr macu macd macc macm maca mactools.py mactools_runtime.py mactools_packages.py mactools_system.py mactools_mirror.py mactools_ai.py)
    for file in "${files[@]}"; do
        if [[ -e "$bin_dir/$file" || -L "$bin_dir/$file" ]]; then
            if [[ "$dry_run" == true ]]; then
                printf 'Remove: %s\n' "$bin_dir/$file"
            else
                rm -f -- "$bin_dir/$file"
                printf 'Removed: %s\n' "$bin_dir/$file"
            fi
        fi
    done
    if [[ "$purge_cache" == true ]]; then
        local cache="${XDG_CACHE_HOME:-$HOME/Library/Caches}/mactools"
        # This is always a dedicated child; never remove the XDG base itself.
        if [[ "$dry_run" == true ]]; then printf 'Remove cache: %s\n' "$cache"; else rm -rf -- "$cache"; fi
    fi
    echo 'Applications, dependencies, AI settings, language, mirror settings and backups are preserved.'
}
uninstall_mactools "$@"
