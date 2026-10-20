#!/bin/bash
# mactools adapter; Mole sources retain their upstream GPL-3.0 notices.
set -euo pipefail
bridge_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
category="${1:?category required}"
mode="${2:?preview or apply required}"
approval_file="${3:?private approval file required}"
[[ "$mode" == preview || "$mode" == apply ]] || exit 2
[[ -f "$approval_file" && ! -L "$approval_file" && -O "$approval_file" ]] || exit 2
export MOLE_DRY_RUN=1
[[ "$mode" == apply ]] && export MOLE_DRY_RUN=0
source "$bridge_dir/bin/clean.sh"

# Clone sources before narrowing the shared cache categories. Dedicated actions
# below can opt into the original, independently reviewed behavior.
for name in clean_trash _clean_recent_items _clean_mail_downloads _clean_incomplete_downloads \
    clean_chrome_old_versions clean_edge_old_versions clean_edge_updater_old_versions \
    clean_brave_old_versions clean_autodesk_fusion_old_bundles \
    safe_sudo_remove safe_sudo_find_delete _mole_bounded_sudo \
    _remove_verified_container_stub _run_simctl run_with_timeout; do
    eval "$(declare -f "$name" | sed "1s/$name/ma_upstream_$name/")"
done
SYSTEM_CLEAN=false
case "$category" in
    system-cache|orphan-services|mobile-cache|old-app-versions|periodic-maintenance) SYSTEM_CLEAN=true ;;
esac
MA_SUDO_READY=false
has_sudo_session() { [[ "$MA_SUDO_READY" == true ]]; }
adopt_sudo_session() { has_sudo_session; }
request_sudo_access() { has_sudo_session; }

# Selecting a cache category must not also empty Trash or remove attachments.
clean_trash() { :; }
_clean_recent_items() { :; }
_clean_mail_downloads() { :; }
_clean_incomplete_downloads() { :; }
clean_chrome_old_versions() { :; }
clean_edge_old_versions() { :; }
clean_edge_updater_old_versions() { :; }
clean_brave_old_versions() { :; }
clean_autodesk_fusion_old_bundles() { :; }
WHITELIST_PATTERNS+=(
    "$HOME/.config/mactools" "$HOME/Library/Logs/mactools"
    "$HOME/Library/Caches/mactools" "$HOME/Library/Caches/Homebrew"
    "$HOME/.cache/mactools" "${XDG_CACHE_HOME:-$HOME/.cache}/mactools"
    "$HOME/Library/IdentityCaches"
    "$HOME/Library/Suggestions" "$HOME/Library/Developer/Xcode/Archives"
    "$HOME/.cache/huggingface" "$HOME/.cache/torch" "$HOME/.cache/wandb"
)
[[ "$category" == old-app-versions ]] || WHITELIST_PATTERNS+=("$HOME/Applications")
[[ "$category" == trash ]] || WHITELIST_PATTERNS+=("$HOME/.Trash")
[[ "$category" == saved-state ]] || WHITELIST_PATTERNS+=("$HOME/Library/Saved Application State")

# Copy functions before wrapping, preserving all of Mole's guards at the sink.
for name in safe_remove mole_delete record_dry_run_cleanup_target clean_tool_cache; do
    eval "$(declare -f "$name" | sed "1s/$name/ma_upstream_$name/")"
done
MA_TYPES=(); MA_KEYS=(); MA_PARENTS=(); MA_PARENT_IDS=(); MA_TARGET_IDS=()
if [[ "$mode" == apply ]]; then
    while IFS= read -r -d '' kind && IFS= read -r -d '' key &&
        IFS= read -r -d '' parent && IFS= read -r -d '' parent_id &&
        IFS= read -r -d '' target_id; do
        MA_TYPES+=("$kind"); MA_KEYS+=("$key"); MA_PARENTS+=("$parent")
        MA_PARENT_IDS+=("$parent_id"); MA_TARGET_IDS+=("$target_id")
    done < "$approval_file"
fi

ma_home_target() {
    # Scope outside HOME is explicit per action; upstream still independently
    # validates protected paths, immutable ancestors, symlinks and identities.
    local path="$1"
    [[ "$path" == /* && "$path" != *'/../'* && "$path" != */.. &&
        "$path" != *'/./'* && "$path" != */. && "$path" != *'//'* ]] || return 1
    [[ "$path" == "$HOME/"* ]] && return 0
    case "$category:$path" in
        system-cache:/Library/*|system-cache:/private/var/*|\
        orphan-services:/Library/LaunchAgents/*|orphan-services:/Library/LaunchDaemons/*|\
        orphan-services:/Library/PrivilegedHelperTools/*|\
        old-app-versions:/Applications/*|silicon-cache:/Library/Apple/*|\
        mobile-cache:/Library/Developer/*|failed-backups:/Volumes/*|\
        installers:/Users/Shared/*|nix-garbage:/nix/store/*|\
        periodic-maintenance:/usr/sbin/periodic|disk-verify:/usr/sbin/diskutil) return 0 ;;
    esac
    return 1
}
ma_record() {
    local kind="$1" key="$2" path="$3"
    ma_home_target "$path" || return 1
    _mole_snapshot_path_identity "$path" || return 1
    # Snapshot uses the physical parent; requiring the reconstructed physical
    # path to remain in scope also rejects an intermediate symlink escaping it.
    ma_home_target "$_MOLE_PATH_SNAPSHOT_PARENT/$(basename "$path")" || return 1
    [[ ! -L "$path" ]] || return 1
    printf '%s\0%s\0%s\0%s\0%s\0' "$kind" "$key" \
        "$_MOLE_PATH_SNAPSHOT_PARENT" "$_MOLE_PATH_SNAPSHOT_PARENT_ID" \
        "$_MOLE_PATH_SNAPSHOT_TARGET_ID" >> "$approval_file"
}
ma_approved() {
    local kind="$1" key="$2" path="$3" i
    ma_home_target "$path" || return 1
    [[ ! -L "$path" ]] || return 1
    for ((i=0; i<${#MA_KEYS[@]}; i++)); do
        [[ "${MA_TYPES[i]}" == "$kind" && "${MA_KEYS[i]}" == "$key" ]] || continue
        _mole_path_matches_identity "$path" "${MA_PARENTS[i]}" \
            "${MA_PARENT_IDS[i]}" "${MA_TARGET_IDS[i]}" && return 0
    done
    return 1
}
ma_final_guard() {
    ma_approved P "$1" "$1" || return 1
    [[ -z "${ma_previous_guard:-}" ]] || "$ma_previous_guard" "$1"
}
record_dry_run_cleanup_target() {
    if [[ "$mode" == apply ]]; then
        ma_approved P "$1" "$1"
        return $?
    fi
    ma_upstream_record_dry_run_cleanup_target "$@" || return $?
    ma_record P "$1" "$1" || return 1
    if [[ "$category" == orphan-app-data && -f "$1/.com.apple.containermanagerd.metadata.plist" ]]; then
        ma_record M "$1" "$1/.com.apple.containermanagerd.metadata.plist" || return 1
    fi
}
safe_remove() {
    ma_home_target "$1" || return 1
    if [[ "$mode" == preview ]]; then
        ma_record P "$1" "$1" || return 1
        ma_upstream_safe_remove "$@"
    else
        local MOLE_DRY_RUN="${MOLE_DRY_RUN:-0}" DRY_RUN="$DRY_RUN"
        if [[ "${MA_FIND_APPLY:-false}" == true ]]; then MOLE_DRY_RUN=0; DRY_RUN=false; fi
        local ma_previous_guard="${_MOLE_SAFE_REMOVE_FINAL_GUARD:-}"
        local _MOLE_SAFE_REMOVE_FINAL_GUARD=ma_final_guard
        ma_approved P "$1" "$1" || { printf 'SKIP changed/unreviewed: %s\n' "$1"; return 1; }
        ma_upstream_safe_remove "$@"
    fi
}
mole_delete() {
    [[ "${2:-false}" == false ]] || return 1
    ma_home_target "$1" || return 1
    if [[ "$mode" == preview ]]; then
        ma_record P "$1" "$1" || return 1
    else
        ma_approved P "$1" "$1" || return 1
    fi
    ma_upstream_mole_delete "$@"
}
clean_tool_cache() {
    local key path="$2"
    if [[ "$category" == nix-garbage && "$1" == 'Nix store garbage' ]]; then
        ma_nix_garbage
        return $?
    fi
    printf -v key '%q ' "$@"
    if [[ "$mode" == preview ]]; then
        ma_record C "$key" "$path" || return 0
    else
        ma_approved C "$key" "$path" || { printf 'SKIP changed/unreviewed command: %s\n' "$1"; return 0; }
    fi
    ma_upstream_clean_tool_cache "$@"
}

# Read-only preview emulates sudo probes with the invoking user's permissions.
# Inaccessible roots stay unavailable; preview never acquires credentials.
_mole_bounded_sudo() {
    local duration="$1"; shift
    [[ "${1:-}" == -n ]] && shift
    local command_name="${1:-}" argument
    if [[ "$mode" == preview ]]; then
        case "$command_name" in
            true) return 0 ;;
            test|du|"$STAT_BSD") ma_upstream_run_with_timeout "$duration" "$@" ;;
            find)
                for argument in "$@"; do
                    case "$argument" in -exec|-execdir|-ok|-okdir|-delete|-fprint*|-fprintf) return 2;; esac
                done
                ma_upstream_run_with_timeout "$duration" "$@" ;;
            /usr/libexec/PlistBuddy)
                [[ "${2:-}" == -c && "${3:-}" == 'Print '* ]] || return 2
                ma_upstream_run_with_timeout "$duration" "$@" ;;
            *) return 1 ;;
        esac
        return $?
    fi
    [[ "$MA_SUDO_READY" == true ]] || return 1
    if [[ "$command_name" == rm ]]; then
        [[ $# == 3 && "$2" == -rf ]] || return 2
        ma_approved P "$3" "$3" || return 1
        if [[ "${MA_FIND_AGE:-0}" != 0 ]]; then
            local now mtime
            now=$(date +%s) || return 1
            mtime=$("$STAT_BSD" -f%m "$3") || return 1
            [[ "$mtime" =~ ^[0-9]+$ ]] || return 1
            ((now - mtime >= (MA_FIND_AGE + 1) * 86400)) || return 1
        fi
    else
        case "$command_name" in
            true|test|du|"$STAT_BSD") : ;;
            find)
                for argument in "$@"; do
                    case "$argument" in -exec|-execdir|-ok|-okdir|-delete|-fprint*|-fprintf) return 2;; esac
                done ;;
            /usr/libexec/PlistBuddy)
                [[ "${2:-}" == -c && "${3:-}" == 'Print '* ]] || return 2 ;;
            *) return 1 ;;
        esac
    fi
    local sudo_bin
    sudo_bin=$(type -P sudo) || return 1
    ma_upstream_run_with_timeout "$duration" "$sudo_bin" -n "$@"
}
safe_sudo_remove() {
    local path="$1" i
    ma_home_target "$path" || return 1
    if [[ "$mode" == preview ]]; then
        ma_record P "$path" "$path" || return 1
        ma_upstream_safe_sudo_remove "$@"
        return $?
    fi
    ma_approved P "$path" "$path" || return 1
    local MOLE_DRY_RUN="${MOLE_DRY_RUN:-0}" DRY_RUN="$DRY_RUN"
    if [[ "${MA_FIND_APPLY:-false}" == true ]]; then MOLE_DRY_RUN=0; DRY_RUN=false; fi
    for ((i=0; i<${#MA_KEYS[@]}; i++)); do
        [[ "${MA_TYPES[i]}" == P && "${MA_KEYS[i]}" == "$path" ]] || continue
        # Keep upstream owner hash / app-absence guards and additionally bind
        # the privileged sink to the identity accepted by ma's preview.
        ma_upstream_safe_sudo_remove "$path" "${2:-}" "${3:-}" \
            "${MA_PARENTS[i]}" "${MA_PARENT_IDS[i]}" "${MA_TARGET_IDS[i]}" "${7:-}" "${8:-}"
        return $?
    done
    return 1
}
safe_sudo_find_delete() {
    # Use upstream's complete bounded scan, age policy and per-item protection.
    # Its dry-run branch avoids the unreviewed xargs deletion batch; the single
    # sink above alone may apply reviewed identities after current checks.
    local MOLE_DRY_RUN=1 DRY_RUN=true MA_FIND_APPLY=false MA_FIND_AGE="${3:-7}"
    [[ "$mode" != apply ]] || MA_FIND_APPLY=true
    ma_upstream_safe_sudo_find_delete "$@"
}
sudo() {
    # Only periodic maintenance calls sudo directly. File operations route
    # through the bounded, identity-checked wrapper above.
    [[ "$mode" == apply && "$MA_SUDO_READY" == true && "$category" == periodic-maintenance &&
        "$*" == 'periodic daily weekly monthly' ]] || return 1
    local executable
    executable=$(type -P periodic) || return 1
    ma_approved Q 'periodic daily weekly monthly' "$executable" || return 1
    local sudo_bin
    sudo_bin=$(type -P sudo) || return 1
    ma_upstream_run_with_timeout "$MOLE_TIMEOUT_PKG_CLEANUP_SEC" "$sudo_bin" -n "$@"
}
ma_authenticate() {
    [[ "$mode" == apply && "$SYSTEM_CLEAN" == true ]] || return 0
    local i required=false
    for ((i=0; i<${#MA_KEYS[@]}; i++)); do
        if [[ "${MA_TYPES[i]}" == Q ||
            ("${MA_TYPES[i]}" == P && "${MA_KEYS[i]}" != "$HOME/"*) ]]; then
            required=true; break
        fi
    done
    [[ "$required" == true ]] || return 0
    [[ "${MOLE_TEST_MODE:-0}" != 1 && "${MOLE_TEST_NO_AUTH:-0}" != 1 ]] || return 0
    local sudo_bin tty_path
    sudo_bin=$(type -P sudo) || return 1
    # Confirmed actions retain the controlling terminal. -S uses its TTY
    # while Mole's echo-restoring helper protects passwords on interruption.
    if "$sudo_bin" -n -v < /dev/null 2>/dev/null; then
        MA_SUDO_READY=true; return 0
    fi
    tty_path=$(tty < /dev/tty 2>/dev/null) || {
        printf 'Administrator access requires an interactive terminal.\n' >&2; return 1;
    }
    ma_validate_sudo() { "$sudo_bin" -S -v < "$tty_path" > /dev/null 2> "$tty_path"; }
    _mole_tty_quiet "$tty_path" ma_validate_sudo || return $?
    MA_SUDO_READY=true
}
_remove_verified_container_stub() {
    ma_approved P "$1" "$1" && ma_approved M "$1" "$2" || return 1
    ma_upstream__remove_verified_container_stub "$@"
}
run_with_timeout() {
    if [[ "${2:-}" == tmutil && "${3:-}" == delete ]]; then
        [[ "$mode" == apply && $# == 4 ]] || return 1
        ma_approved P "$4" "$4" || return 1
    fi
    ma_upstream_run_with_timeout "$@"
}
_run_simctl() {
    if [[ "${2:-}" == delete && "${3:-}" == unavailable ]]; then
        [[ "$mode" == apply ]] || return 1
        local listing uuid path rc=0
        listing=$(ma_upstream__run_simctl "$MOLE_TIMEOUT_PKG_LIST_SEC" list devices unavailable) || return $?
        while IFS= read -r uuid; do
            [[ -n "$uuid" ]] || continue
            path="$HOME/Library/Developer/CoreSimulator/Devices/$uuid"
            ma_approved P "$path" "$path" || continue
            ma_upstream__run_simctl "$1" delete "$uuid" || { rc=$?; break; }
        done < <(printf '%s\n' "$listing" | sed -nE 's/.*\(([0-9A-Fa-f-]{36})\).*\(unavailable.*/\1/p')
        return "$rc"
    fi
    ma_upstream__run_simctl "$@"
}
ma_nix_garbage() {
    # Nix's batch --gc would include newly dead paths. Query the native owner
    # again and pass only the exact reviewed store paths to its deletion API.
    local scan_file path status=0
    scan_file=$(create_temp_file) || return 1
    ma_upstream_run_with_timeout "$MOLE_TIMEOUT_PKG_LIST_SEC" nix-store --gc --print-dead > "$scan_file" || status=$?
    [[ "$status" == 0 ]] || return "$status"
    while IFS= read -r path; do
        [[ "$path" == /nix/store/* && "$path" != *$'\t'* && "$path" != *$'\r'* ]] || continue
        is_path_whitelisted "$path" && continue
        if [[ "$mode" == preview ]]; then
            ma_record N "$path" "$path" || continue
            printf 'Nix unreferenced: %s\n' "$path"
        else
            ma_approved N "$path" "$path" || continue
            ma_upstream_run_with_timeout "$MOLE_TIMEOUT_PKG_CLEANUP_SEC" nix-store --delete "$path" || return $?
            log_operation clean REMOVED "$path" 'reviewed Nix garbage'
        fi
    done < "$scan_file"
}
ma_opt_task() {
    source "$bridge_dir/lib/optimize/tasks.sh"
    optimize_task_start || return $?
    "$@" || return $?
    local outcome="$MOLE_OPTIMIZE_TASK_OUTCOME"
    MA_OPT_LAST_OUTCOME="$outcome"
    optimize_task_finish "$1" || return $?
    [[ "$outcome" != failed ]]
}

export MOLE_CURRENT_COMMAND=clean
MOLE_CLEAN_CANCEL_STATUS=0
MOLE_CLEAN_CANCEL_SOURCE=""
MOLE_CLEAN_SIZING_TIMEOUTS=0
MOLE_CLEAN_REMOVAL_TIMEOUTS=0
MOLE_CLEAN_REMOVAL_TIMEOUT_PATHS=""
DRY_RUN_SEEN_IDENTITIES=()
DRY_RUN_TOTAL_PARTIAL=false
DEFERRED_CLEANUP_FAMILIES=()
total_items=0; files_cleaned=0; total_size_cleaned=0
# The only preview writes are ma's own logs and this private scratch ledger.
CLEAN_PREVIEW_FINAL_FILE="$(dirname "$approval_file")/preview.txt"
write_clean_preview_header() {
    printf '# ma clean: %s\n# Whitelist: ~/.config/mactools/mole/whitelist\n' "$category" > "$EXPORT_LIST_FILE"
}
trap 'cleanup_temp_files' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$mode" == preview ]]; then
    prepare_clean_preview_file
    printf '# ma clean: %s\n' "$category" > "$EXPORT_LIST_FILE"
fi
log_operation_session_start clean

ma_run_category() {
    case "$category" in
        trash) ma_upstream_clean_trash ;;
        recent-items)
            printf 'Mole preserves protected macOS recent-item databases. Original documents remain untouched.\n'
            ma_upstream__clean_recent_items ;;
        mail-downloads) ma_upstream__clean_mail_downloads ;;
        incomplete-downloads) ma_upstream__clean_incomplete_downloads ;;
        saved-state)
            ma_opt_task opt_saved_state_cleanup ;;
        old-app-versions)
            ma_upstream_clean_chrome_old_versions || return $?
            ma_upstream_clean_edge_old_versions || return $?
            ma_upstream_clean_edge_updater_old_versions || return $?
            ma_upstream_clean_brave_old_versions || return $?
            ma_upstream_clean_autodesk_fusion_old_bundles ;;
        orphan-app-data)
            clean_orphaned_app_data || return $?
            clean_orphaned_container_stubs || return $?
            show_user_launch_agent_hint_notice ;;
        orphan-services) clean_orphaned_system_services ;;
        system-cache) clean_deep_system ;;
        finder-metadata)
            if [[ "$PROTECT_FINDER_METADATA" == true ]]; then
                printf 'Preserved: Finder metadata is protected by Mole safety rules.\n'
            fi
            clean_finder_metadata ;;
        group-cache) clean_group_container_caches ;;
        silicon-cache) clean_apple_silicon_caches ;;
        device-firmware) clean_cached_device_firmware ;;
        failed-backups) clean_time_machine_failed_backups ;;
        mobile-cache) clean_dev_mobile || return $?; clean_xcode_tools ;;
        toolbox-cache) clean_dev_jetbrains_toolbox ;;
        nix-garbage) clean_dev_nix ;;
        local-snapshots)
            printf 'Review only: local snapshots remain managed by Time Machine.\n'
            clean_local_snapshots ;;
        system-updates)
            # This pinned Mole release deliberately does not delete staging:
            # age, plists and process checks do not prove update inactivity.
            printf 'Review only: Software Update owns these staging directories.\n'
            local staging
            for staging in /Library/Updates '/macOS Install Data'; do
                [[ -d "$staging" ]] || continue
                printf 'Preserved: %s\n' "$staging"
                run_with_timeout "$MOLE_TIMEOUT_DISK_VERIFY_SEC" du -skP "$staging" || return $?
            done ;;
        large-files)
            printf 'Review only: large files are never automatically deleted.\n'
            check_large_file_candidates ;;
        periodic-maintenance)
            source "$bridge_dir/lib/optimize/tasks.sh"
            local executable
            executable=$(type -P periodic) || { ma_opt_task opt_periodic_maintenance; return $?; }
            if [[ "$mode" == preview ]]; then
                ma_opt_task opt_periodic_maintenance || return $?
                if [[ "$MA_OPT_LAST_OUTCOME" == "$MOLE_OPTIMIZE_OUTCOME_APPLIED" ]]; then
                    ma_record Q 'periodic daily weekly monthly' "$executable" || return 1
                fi
            else
                if ma_approved Q 'periodic daily weekly monthly' "$executable"; then
                    ma_opt_task opt_periodic_maintenance
                else
                    printf 'Skipped: no reviewed periodic maintenance command.\n'
                fi
            fi ;;
        disk-verify)
            source "$bridge_dir/lib/optimize/tasks.sh"
            if [[ "${MOLE_ENABLE_DISK_VERIFY:-0}" != 1 ]]; then
                ma_opt_task opt_disk_verify
                return $?
            fi
            if [[ "$mode" == preview ]]; then
                ma_record Q 'diskutil verifyVolume /' /usr/sbin/diskutil || return 1
                printf 'Would run: diskutil verifyVolume / (verification only; no repair)\n'
            else
                ma_approved Q 'diskutil verifyVolume /' /usr/sbin/diskutil || return 1
                ma_opt_task opt_disk_verify
            fi ;;
        user-cache) clean_user_essentials ;;
        app-cache) clean_app_caches ;;
        browser-cache) clean_browsers ;;
        cloud-cache) clean_cloud_storage ;;
        office-cache) clean_office_applications ;;
        app-tools) clean_user_gui_applications ;;
        vm-cache) clean_virtualization_tools ;;
        app-logs) clean_application_support_logs ;;
        rust-cache) clean_dev_rust ;;
        jvm-cache) clean_dev_jvm ;;
        frontend-cache) clean_dev_frontend ;;
        ruby-cache) clean_dev_ruby || return $?; clean_dev_perl ;;
        cloud-cli-cache) clean_dev_cloud ;;
        ai-cache)
            clean_dev_ai_agents || return $?
            clean_dev_misc || return $?
            clean_dev_automation_browsers ;;
        language-cache)
            clean_dev_python || return $?
            clean_dev_mise || return $?
            clean_dev_other_langs || return $?
            clean_dev_elixir || return $?
            clean_dev_ocaml ;;
        build-tools-cache)
            clean_dev_docker || return $?
            clean_dev_cicd || return $?
            clean_dev_database || return $?
            clean_dev_api_tools || return $?
            clean_dev_shell || return $?
            clean_dev_network ;;
        editor-cache) clean_code_editors || return $?; clean_dev_jetbrains_logs ;;
        xcode-extra) clean_xcode_documentation_cache || return $?; clean_xcode_xctest_devices ;;
        project-cache) clean_project_caches ;;
        project-artifacts)
            source "$bridge_dir/lib/clean/project.sh" || return $?
            # The standalone purge entry point normally initializes these.
            # Scanners treat a missing marker as cancellation, so fail closed
            # before scanning if its private state cannot be prepared.
            local stats_dir="${XDG_CACHE_HOME:-$HOME/.cache}/mactools/mole" stats_name
            ensure_user_dir "$stats_dir" || return $?
            for stats_name in purge_stats purge_count purge_scanning; do
                ensure_user_file "$stats_dir/$stats_name" || return $?
                printf '0\n' > "$stats_dir/$stats_name" || return $?
            done
            export MOLE_PURGE_YES=1
            clean_project_artifacts || return $?
            case "${PURGE_RUN_OUTCOME:-scan_failed}" in
                completed|no_candidates|cancelled) return 0;;
                *) return 1;;
            esac
            ;;
        installers)
            source "$bridge_dir/bin/installer.sh"
            scan_all_installers() {
                local path
                for path in "${INSTALLER_SCAN_PATHS[@]}"; do
                    [[ "$path" == "$HOME/"* || "$path" == /Users/Shared || "$path" == /Users/Shared/* ]] || continue
                    [[ -d "$path" ]] || continue
                    scan_installers_in_path "$path" "${1:-}" || return $?
                done
            }
            # installer.sh changes traps, never its main/interactive menu.
            local rc=0 i
            collect_installers || rc=$?
            [[ "$rc" != 1 ]] || return 0
            [[ "$rc" == 0 ]] || return "$rc"
            reset_installer_delete_results
            local -a indexes=()
            for ((i=0; i<${#INSTALLER_PATHS[@]}; i++)); do
                indexes+=("$i")
                printf '%s\n' "${INSTALLER_PATHS[i]}"
            done
            build_installer_delete_plan "${indexes[@]}" || return $?
            execute_installer_delete_plan
            ;;
        *) printf 'Unknown Mole category: %s\n' "$category" >&2; return 2 ;;
    esac
}
start_section "$category"
# Do not let a harmless missing cache trip upstream's set -e.
result=0
ma_authenticate || result=$?
if [[ "$result" == 0 ]]; then
    ma_run_category || result=$?
fi
pending="${MOLE_CLEAN_CANCEL_STATUS:-0}"
[[ "$pending" == 0 ]] || result="$pending"
end_section
if [[ "$mode" == preview ]]; then
    render_clean_preview_from_ledger
    [[ ! -s "$CLEAN_PREVIEW_FINAL_FILE" ]] || cat "$CLEAN_PREVIEW_FINAL_FILE"
fi
log_operation_session_end clean "$files_cleaned" "$total_size_cleaned"
exit "$result"
