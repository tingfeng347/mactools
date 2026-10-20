#!/bin/bash
# System Configuration Maintenance Module.
# Fix broken preferences and login items.

set -euo pipefail

_preference_plist_is_protected() {
    local plist_file="$1"
    local protect_loginwindow="${2:-false}"
    local filename="${plist_file##*/}"

    case "$filename" in
        com.apple.* | .GlobalPreferences*)
            return 0
            ;;
        loginwindow.plist)
            [[ "$protect_loginwindow" == "true" ]]
            return
            ;;
    esac

    return 1
}

# Lint through the system binary by absolute path: a PATH shim must never
# decide whether a user preference file is corrupt. Tests replace this
# function to count probes.
_mole_plutil_lint() {
    /usr/bin/plutil -lint "$@"
}

_repair_preference_plists_in_dir() {
    local search_dir="$1"
    local maxdepth="$2"
    local protect_loginwindow="${3:-false}"
    [[ -d "$search_dir" ]] || {
        echo "0"
        return 0
    }

    local -a find_args=("$search_dir")
    if [[ "$maxdepth" -gt 0 ]]; then
        find_args+=("-maxdepth" "$maxdepth")
    fi
    find_args+=("-name" "*.plist" "-type" "f")

    local -a candidates=()
    local plist_file=""
    while IFS= read -r plist_file; do
        [[ -f "$plist_file" ]] || continue
        _preference_plist_is_protected "$plist_file" "$protect_loginwindow" && continue
        candidates+=("$plist_file")
    done < <(command find "${find_args[@]}" 2> /dev/null || true)

    # Preferences dirs can hold tens of thousands of plists (leaky test
    # suites write one per run), so lint in large batches: plutil exits
    # non-zero only when a file in the batch fails, and only failing
    # batches pay a per-file fallback pass. Protection checks run on
    # broken files only, right before removal. The HINT_SCAN budget keeps
    # a pathological tree from making the scan appear hung; returns 1
    # when it stopped early so callers can report partial results.
    local broken_count=0
    local total=${#candidates[@]}
    local batch_size=512
    local start=0
    local deadline=$((SECONDS + ${MOLE_TIMEOUT_HINT_SCAN_SEC:-15}))
    local partial=0

    while [[ $start -lt $total ]]; do
        if [[ $SECONDS -ge $deadline ]]; then
            partial=1
            break
        fi
        local -a batch=("${candidates[@]:start:batch_size}")
        start=$((start + batch_size))
        if _mole_plutil_lint "${batch[@]}" > /dev/null 2>&1; then
            continue
        fi
        local candidate=""
        for candidate in "${batch[@]}"; do
            [[ -f "$candidate" ]] || continue
            # A file the current user cannot read is unknown, never corrupt.
            # Deleting it would destroy settings that may be perfectly valid.
            if [[ ! -r "$candidate" ]]; then
                debug_log "Kept unreadable preference plist: $candidate"
                if declare -f log_operation > /dev/null 2>&1; then
                    log_operation "optimize" "SKIPPED" "$candidate" "unreadable preference"
                fi
                continue
            fi
            _mole_plutil_lint "$candidate" > /dev/null 2>&1 && continue
            if declare -f should_protect_path > /dev/null 2>&1 && should_protect_path "$candidate"; then
                continue
            fi
            if declare -f is_path_whitelisted > /dev/null 2>&1 && is_path_whitelisted "$candidate"; then
                continue
            fi
            if safe_remove "$candidate" true > /dev/null 2>&1; then
                debug_file_action "Removed corrupted preference" "$candidate"
                broken_count=$((broken_count + 1))
            fi
        done
    done

    echo "$broken_count"
    return "$partial"
}

# Remove corrupted preference files.
# Prints the repaired count; returns 1 when a scan stopped at its time
# budget and the count is therefore partial.
fix_broken_preferences() {
    local prefs_dir="$HOME/Library/Preferences"
    [[ -d "$prefs_dir" ]] || return 0

    local broken_count=0
    local repaired_count=0
    local partial=0

    repaired_count=$(_repair_preference_plists_in_dir "$prefs_dir" 1 true) || partial=1
    broken_count=$((broken_count + repaired_count))

    # Check ByHost preferences recursively.
    repaired_count=$(_repair_preference_plists_in_dir "$prefs_dir/ByHost" 0 false) || partial=1
    broken_count=$((broken_count + repaired_count))

    echo "$broken_count"
    return "$partial"
}
