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

# Never acquire or reuse administrator authorization, including cached sudo.
SYSTEM_CLEAN=false
has_sudo_session() { return 1; }
adopt_sudo_session() { return 1; }
request_sudo_access() { return 1; }
safe_sudo_remove() { return 1; }
safe_sudo_find_delete() { return 1; }
_mole_bounded_sudo() { return 1; }
sudo() { return 1; }

# Keep ma's scope: disposable caches, never trash, recent documents, mail
# attachments, signed applications, downloaded models, or saved app state.
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
    "$HOME/Applications" "$HOME/.Trash"
    "$HOME/Library/Saved Application State" "$HOME/Library/IdentityCaches"
    "$HOME/Library/Suggestions" "$HOME/Library/Developer/Xcode/Archives"
    "$HOME/.cache/huggingface" "$HOME/.cache/torch" "$HOME/.cache/wandb"
)

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
    # Upstream independently resolves symlinks and rebinds identities.
    [[ "$1" == "$HOME/"* && "$1" != *'/../'* && "$1" != *'/./'* ]]
}
ma_record() {
    local kind="$1" key="$2" path="$3"
    ma_home_target "$path" || return 1
    _mole_snapshot_path_identity "$path" || return 1
    [[ "$_MOLE_PATH_SNAPSHOT_PARENT" == "$HOME" || "$_MOLE_PATH_SNAPSHOT_PARENT" == "$HOME/"* ]] || return 1
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
    ma_upstream_record_dry_run_cleanup_target "$@" || return $?
    ma_record P "$1" "$1" || true
}
safe_remove() {
    ma_home_target "$1" || return 1
    if [[ "$mode" == preview ]]; then
        ma_record P "$1" "$1" || return 1
        ma_upstream_safe_remove "$@"
    else
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
    printf -v key '%q ' "$@"
    if [[ "$mode" == preview ]]; then
        ma_record C "$key" "$path" || return 0
    else
        ma_approved C "$key" "$path" || { printf 'SKIP changed/unreviewed command: %s\n' "$1"; return 0; }
    fi
    ma_upstream_clean_tool_cache "$@"
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
                    [[ "$path" == "$HOME/"* ]] || continue
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
ma_run_category || result=$?
pending="${MOLE_CLEAN_CANCEL_STATUS:-0}"
[[ "$pending" == 0 ]] || result="$pending"
end_section
if [[ "$mode" == preview ]]; then
    render_clean_preview_from_ledger
    [[ ! -s "$CLEAN_PREVIEW_FINAL_FILE" ]] || cat "$CLEAN_PREVIEW_FINAL_FILE"
fi
log_operation_session_end clean "$files_cleaned" "$total_size_cleaned"
exit "$result"
