#!/bin/bash
# Explicit optimization adapter. Upstream task and protection code remains GPL-3.0.
set -euo pipefail
[[ "$EUID" -ne 0 ]] || { printf 'Run ma optimize without sudo.\n' >&2; exit 1; }
bridge_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:?mode required}"; approval_file="${2:?private ledger required}"; shift 2
[[ "$mode" == preview || "$mode" == apply ]] || exit 2
[[ -f "$approval_file" && ! -L "$approval_file" && -O "$approval_file" ]] || exit 2
[[ $# -gt 0 ]] || exit 2
export LC_ALL=C LANG=C MOLE_CURRENT_COMMAND=optimize
export MOLE_DRY_RUN=1
[[ "$mode" != apply ]] || export MOLE_DRY_RUN=0
source "$bridge_dir/lib/core/common.sh"
source "$bridge_dir/lib/core/sudo.sh"
source "$bridge_dir/lib/optimize/maintenance.sh"
source "$bridge_dir/lib/optimize/tasks.sh"
source "$bridge_dir/lib/manage/whitelist.sh"
load_mole_whitelist "$HOME"
MA_CLEAN_WHITELIST=("${WHITELIST_PATTERNS[@]}")
load_whitelist optimize
WHITELIST_PATTERNS=("${MA_CLEAN_WHITELIST[@]}")
trap cleanup_temp_files EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for name in safe_remove run_with_timeout; do
    eval "$(declare -f "$name" | sed "1s/$name/ma_upstream_$name/")"
done
MA_PATHS=(); MA_PARENTS=(); MA_PARENT_IDS=(); MA_TARGET_IDS=()
MA_NOTIFICATION_DB=""
ma_target_allowed() {
    [[ "$1" == "$HOME/"* ]] && return 0
    [[ "$MA_ACTION" == notification_cleanup && -n "$MA_NOTIFICATION_DB" && "$1" == "$MA_NOTIFICATION_DB" && -O "$1" ]]
}
if [[ "$mode" == apply ]]; then
    while IFS= read -r -d '' path && IFS= read -r -d '' parent &&
        IFS= read -r -d '' parent_id && IFS= read -r -d '' target_id; do
        MA_PATHS+=("$path"); MA_PARENTS+=("$parent")
        MA_PARENT_IDS+=("$parent_id"); MA_TARGET_IDS+=("$target_id")
    done < "$approval_file"
fi
ma_record_path() {
    local path="$1"
    ma_target_allowed "$path" && [[ ! -L "$path" ]] || return 1
    _mole_snapshot_path_identity "$path" || return 1
    if [[ "$path" == "$HOME/"* ]]; then
        [[ "$_MOLE_PATH_SNAPSHOT_PARENT/$(basename "$path")" == "$HOME/"* ]] || return 1
    fi
    printf '%s\0%s\0%s\0%s\0' "$path" "$_MOLE_PATH_SNAPSHOT_PARENT" \
        "$_MOLE_PATH_SNAPSHOT_PARENT_ID" "$_MOLE_PATH_SNAPSHOT_TARGET_ID" >> "$approval_file"
}
ma_approved_path() {
    local path="$1" i
    ma_target_allowed "$path" && [[ ! -L "$path" ]] || return 1
    for ((i=0; i<${#MA_PATHS[@]}; i++)); do
        [[ "${MA_PATHS[i]}" == "$path" ]] || continue
        _mole_path_matches_identity "$path" "${MA_PARENTS[i]}" "${MA_PARENT_IDS[i]}" "${MA_TARGET_IDS[i]}" && return 0
    done
    return 1
}
ma_final_remove_guard() {
    ma_approved_path "$1" || return 1
    [[ -z "${ma_previous_guard:-}" ]] || "$ma_previous_guard" "$1"
}
safe_remove() {
    if [[ "$mode" == preview ]]; then
        ma_record_path "$1" || return 1
        ma_upstream_safe_remove "$@"
        return $?
    else
        ma_approved_path "$1" || return 1
    fi
    local ma_previous_guard="${_MOLE_SAFE_REMOVE_FINAL_GUARD:-}"
    local _MOLE_SAFE_REMOVE_FINAL_GUARD=ma_final_remove_guard
    ma_upstream_safe_remove "$@"
}
ma_record_databases() {
    local target
    case "$MA_ACTION" in
        sqlite_vacuum)
            for target in "$HOME"/Library/Mail/V*/MailData/Envelope\ Index* \
                "$HOME/Library/Messages/chat.db" "$HOME/Library/Safari/History.db" "$HOME/Library/Safari/TopSites.db"; do
                [[ -f "$target" && "$target" != *-wal && "$target" != *-shm ]] || continue
                ma_record_path "$target" || return 1
            done ;;
        quarantine_cleanup)
            target="$HOME/Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2"
            [[ ! -f "$target" ]] || ma_record_path "$target" ;;
        coreduet_cleanup)
            target="$HOME/Library/Application Support/Knowledge/knowledgeC.db"
            [[ ! -f "$target" ]] || ma_record_path "$target" ;;
        notification_cleanup)
            target=$(resolve_notification_center_db) || return 0
            ma_record_path "$target" ;;
    esac
    return 0
}
sqlite3() {
    local executable
    executable=$(type -P sqlite3) || return 1
    [[ $# == 2 ]] || return 1
    if [[ "$mode" == preview ]]; then
        case "$2" in
            'SELECT COUNT(*) FROM LSQuarantineEvent;'|'PRAGMA page_count; PRAGMA freelist_count;') : ;;
            *) return 1 ;;
        esac
        # Even -readonly can create SHM beside a WAL database. Probe a private
        # copy, retaining the WAL so committed pages remain visible; the source
        # database and its sidecars are never opened by SQLite during preview.
        local copy_dir copy_path source="$1"
        [[ -f "$source" && ! -L "$source" ]] || return 1
        copy_dir=$(create_temp_dir) || return 1
        copy_path="$copy_dir/database"
        command cp -p "$source" "$copy_path" || return 1
        if [[ -e "$source-wal" ]]; then
            [[ -f "$source-wal" && ! -L "$source-wal" ]] || return 1
            command cp -p "$source-wal" "$copy_path-wal" || return 1
        fi
        ma_upstream_run_with_timeout "${MA_SQLITE_TIMEOUT:-$MOLE_TIMEOUT_PKG_LIST_SEC}" "$executable" -readonly "$copy_path" "$2"
    else
        ma_approved_path "$1" || { printf 'Skipped changed/unreviewed database: %s\n' "$1" >&2; return 1; }
        ma_upstream_run_with_timeout "${MA_SQLITE_TIMEOUT:-$MOLE_TIMEOUT_PKG_LIST_SEC}" "$executable" "$@"
    fi
}
run_with_timeout() {
    if [[ "${2:-}" == sudo ]]; then
        shift 2
        sudo "$@"
    elif [[ "${2:-}" == sqlite3 ]]; then
        local MA_SQLITE_TIMEOUT="$1"
        shift 2
        sqlite3 "$@"
    else
        ma_upstream_run_with_timeout "$@"
    fi
}
MA_SUDO_READY=false
ma_authenticate() {
    [[ "$mode" == apply ]] || return 1
    [[ "$MA_SUDO_READY" != true ]] || return 0
    [[ "${MOLE_TEST_NO_AUTH:-0}" != 1 && "${MOLE_TEST_MODE:-0}" != 1 ]] || return 1
    local sudo_bin tty_path
    sudo_bin=$(type -P sudo) || return 1
    if ma_upstream_run_with_timeout "$MOLE_TIMEOUT_QUICK_DETECT_SEC" "$sudo_bin" -n -v < /dev/null 2>/dev/null; then
        MA_SUDO_READY=true
        return 0
    fi
    tty_path=$(tty < /dev/tty 2>/dev/null) || { printf 'Administrator access requires an interactive terminal.\n' >&2; return 1; }
    ma_validate_sudo() { "$sudo_bin" -S -v < "$tty_path" > /dev/null 2> "$tty_path"; }
    _mole_tty_quiet "$tty_path" ma_validate_sudo || return $?
    MA_SUDO_READY=true
}
sudo() {
    [[ "$mode" == apply ]] || return 1
    local sudo_bin
    sudo_bin=$(type -P sudo) || return 1
    # Login audit never obtains new credentials. Its fallback is read-only.
    if [[ "$MA_ACTION" == login_items_audit ]]; then
        [[ "$*" == '-n true' || "$*" == '-n sfltool dumpbtm' ]] || return 1
        ma_upstream_run_with_timeout "$MOLE_TIMEOUT_MEDIUM_PROBE_SEC" "$sudo_bin" "$@"
        return $?
    fi
    case "$MA_ACTION:$*" in
        'network_optimization:dscacheutil -flushcache'|\
        'network_optimization:killall -HUP mDNSResponder'|\
        'network_stack_optimize:route -n flush'|\
        'network_stack_optimize:arp -a -d'|\
        'spotlight_index_optimize:mdutil -E /'|\
        'periodic_maintenance:periodic daily weekly monthly') : ;;
        disk_permissions_repair:*)
            [[ $# == 4 && "$1" == diskutil && "$2" == resetUserPermissions && "$3" == / && "$4" == "$(id -u)" ]] || return 1 ;;
        *) return 1 ;;
    esac
    ma_authenticate || return $?
    ma_upstream_run_with_timeout "$MOLE_TIMEOUT_PKG_CLEANUP_SEC" "$sudo_bin" -n "$@"
}
# Availability allows task conditions to run; actual authentication is lazy at
# the restricted sudo sink, after a selected task detects a needed operation.
export MOLE_OPTIMIZE_SUDO_AVAILABLE=true
optimize_outcomes_reset
log_operation_session_start optimize
for selected in "$@"; do
    MA_ACTION="${selected//-/_}"
    optimize_catalog_handler_for "$MA_ACTION" > /dev/null || exit 2
    if [[ "$MA_ACTION" == notification_cleanup ]]; then
        MA_NOTIFICATION_DB=$(resolve_notification_center_db) || MA_NOTIFICATION_DB=""
    fi
    if [[ "$mode" == preview ]]; then ma_record_databases || exit $?; fi
    printf '\n%s\n' "$selected"
    execute_optimization "$MA_ACTION"
done
printf '\nTask outcomes:\n'
for ((i=0; i<${#MOLE_OPTIMIZE_RESULT_ACTIONS[@]}; i++)); do
    printf '%s: %s\n' "${MOLE_OPTIMIZE_RESULT_ACTIONS[i]//_/-}" "${MOLE_OPTIMIZE_RESULT_OUTCOMES[i]}"
done
optimize_outcomes_succeeded
