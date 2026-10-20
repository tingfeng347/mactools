#!/bin/bash
# mactools application adapter for Mole's GPL-3.0 uninstall engine.
# Plans are NUL records, never shell source. The ma caller owns confirmation.
set -euo pipefail
bridge_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$bridge_dir/bin/uninstall.sh"

ma_apps_text() {
    if [[ "${MACTOOLS_LANG:-zh}" == en ]]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi
}
ma_apps_clean_value() { [[ "$1" != *[$'\n\r\t|']* ]]; }
ma_apps_private_file() { [[ -f "$1" && ! -L "$1" && -O "$1" ]]; }

# Reuse Mole's discovery roots, nested-helper exclusions and system policy,
# without Spotlight size lookup or Homebrew ownership queries in the list UI.
ma_apps_list() {
    local output="$1" root path bundle name
    ma_apps_private_file "$output" || return 2
    : > "$output"
    while IFS= read -r root; do
        [[ -d "$root" ]] || continue
        while IFS= read -r -d '' path; do
            ma_apps_clean_value "$path" || continue
            uninstall_should_skip_app_path "$path" && continue
            bundle=$(uninstall_resolve_eligible_bundle_id "$path") || continue
            name="${path##*/}"; name="${name%.[aA][pP][pP]}"
            printf '%s\0%s\0%s\0' "$path" "$bundle" "$name" >> "$output"
        done < <(command find "$root" -mindepth 1 -maxdepth 3 \
            \( -type d -name '.*' ! -iname '*.app' -prune \) -o \
            \( -iname '*.app' -print0 -prune \) 2> /dev/null)
    done < <(uninstall_print_app_search_dirs)
}

# Bind directory contents too: a new file beneath a reviewed cache is new
# data, even if the parent directory keeps its inode. No symlinks are followed.
ma_apps_tree_fingerprint() {
    "${MACTOOLS_APPS_PYTHON:-python3}" - "$1" <<'PYTREE'
import hashlib, os, stat, sys
root = os.fsencode(sys.argv[1])
hash_ = hashlib.sha256()
pending = [root]
try:
    while pending:
        path = pending.pop()
        info = os.lstat(path)
        relative = os.path.relpath(path, root)
        hash_.update(relative + b'\0')
        hash_.update((':'.join(str(value) for value in
            (info.st_dev, info.st_ino, info.st_mode, info.st_size,
             info.st_mtime_ns, info.st_ctime_ns))).encode() + b'\0')
        if stat.S_ISLNK(info.st_mode):
            hash_.update(os.readlink(path) + b'\0')
        elif stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(sorted((item.path for item in entries), reverse=True))
    print(hash_.hexdigest())
except OSError:
    sys.exit(1)
PYTREE
}

ma_apps_record() {
    local path="$1" ledger="$2" fingerprint
    [[ ! -L "$path" ]] || return 1
    _mole_snapshot_path_identity "$path" || return 1
    fingerprint=$(ma_apps_tree_fingerprint "$path") || return 1
    printf '%s\0%s\0%s\0%s\0%s\0' "$path" "$_MOLE_PATH_SNAPSHOT_PARENT" \
        "$_MOLE_PATH_SNAPSHOT_PARENT_ID" "$_MOLE_PATH_SNAPSHOT_TARGET_ID" "$fingerprint" >> "$ledger"
}
ma_apps_approved() {
    local path="$1" i fingerprint
    [[ ! -L "$path" ]] || return 1
    for ((i=0; i<${#MA_APP_PATHS[@]}; i++)); do
        [[ "${MA_APP_PATHS[i]}" == "$path" ]] || continue
        _mole_path_matches_identity "$path" "${MA_APP_PARENTS[i]}" \
            "${MA_APP_PARENT_IDS[i]}" "${MA_APP_IDS[i]}" || continue
        fingerprint=$(ma_apps_tree_fingerprint "$path") || return 1
        [[ "$fingerprint" == "${MA_APP_TREES[i]}" ]] && return 0
    done
    return 1
}
ma_apps_read_details() {
    local file="$1" detail
    app_details=()
    while IFS= read -r -d '' detail; do app_details+=("$detail"); done < "$file"
    [[ ${#app_details[@]} -gt 0 ]]
}

ma_apps_load_owners() {
    local file="$1" path brew token
    ma_apps_private_file "$file" || return 2
    MA_OWNER_PATHS=(); MA_OWNER_BREWS=(); MA_OWNER_TOKENS=()
    while IFS= read -r -d '' path && IFS= read -r -d '' brew && IFS= read -r -d '' token; do
        [[ "$brew" == /* && -x "$brew" && "$token" != -* ]] || return 2
        MA_OWNER_PATHS+=("$path"); MA_OWNER_BREWS+=("$brew"); MA_OWNER_TOKENS+=("$token")
    done < "$file"
}
ma_apps_owner() {
    local path="$1" i
    _MA_OWNER_BREW=""; _MA_OWNER_TOKEN=""
    for ((i=0; i<${#MA_OWNER_PATHS[@]}; i++)); do
        [[ "${MA_OWNER_PATHS[i]}" == "$path" ]] || continue
        _MA_OWNER_BREW="${MA_OWNER_BREWS[i]}"; _MA_OWNER_TOKEN="${MA_OWNER_TOKENS[i]}"
        return 0
    done
    return 1
}
# The Python caller verifies full-token ownership against every installed brew
# prefix. Do not allow upstream's cross-Caskroom/PATH short-token fallback.
get_brew_cask_name() { ma_apps_owner "$1" || return 1; printf '%s\n' "$_MA_OWNER_TOKEN"; }
is_homebrew_available() { return 1; }
is_brew_cask_installed() {
    ma_apps_owner "${app_path:-}" || return 2
    local output rc=0
    output=$(HOMEBREW_NO_ENV_HINTS=1 run_with_timeout "$MOLE_TIMEOUT_PKG_LIST_SEC" \
        "$_MA_OWNER_BREW" list --cask --full-name) || rc=$?
    [[ $rc -eq 0 ]] || { [[ $rc -lt 128 ]] || return "$rc"; return 2; }
    grep -qxF "$_MA_OWNER_TOKEN" <<< "$output"
}
brew_uninstall_cask() {
    local selected_token="$1" selected_path="$2" rc=0
    ma_apps_owner "$selected_path" || return 2
    [[ "$selected_token" == "$_MA_OWNER_TOKEN" && "${3:-}" == nozap ]] || return 2
    # Keep Mole's application identity/sibling checks in the calling phase;
    # pin only the package-manager sink to its verified executable and tap.
    HOMEBREW_NO_ENV_HINTS=1 HOMEBREW_NO_AUTO_UPDATE=1 NONINTERACTIVE=1 \
        run_with_timeout 900 "$_MA_OWNER_BREW" uninstall --cask "$_MA_OWNER_TOKEN" || rc=$?
    [[ $rc -lt 128 && $rc -ne 124 ]] || return "$rc"
    local installed_rc=0
    is_brew_cask_installed "$selected_token" || installed_rc=$?
    [[ $installed_rc -eq 1 && ! -e "$selected_path" && ! -L "$selected_path" ]] || return 2
    log_operation uninstall REMOVED "$selected_path" "brew uninstall $selected_token"
}

ma_apps_preview() {
    local request="$1" details="$2" ledger="$3" casks="$4" with_data="$5" owners="$6"
    local path bundle name detail encoded files file total review_system i=0
    for file in "$request" "$details" "$ledger" "$casks"; do ma_apps_private_file "$file" || return 2; done
    ma_apps_load_owners "$owners" || return $?
    : > "$details"; : > "$ledger"; : > "$casks"
    selected_apps=(); apps_data=()
    while IFS= read -r -d '' path; do
        [[ "$path" == /* && "$path" == *.[aA][pP][pP] && ! -L "$path" ]] || return 2
        ma_apps_clean_value "$path" || return 2
        uninstall_should_skip_app_path "$path" && return 2
        bundle=$(uninstall_resolve_eligible_bundle_id "$path") || return 2
        name="${path##*/}"; name="${name%.[aA][pP][pP]}"
        selected_apps+=("0|$path|$name|$bundle|0|0")
    done < "$request"
    [[ ${#selected_apps[@]} -gt 0 ]] || return 2
    apps_data=("${selected_apps[@]}")
    local -a running_apps=() sudo_apps=() brew_cask_apps=() blocked_apps=() manual_removal_apps=() leftover_notes=() app_details=()
    local total_estimated_size=0 _batch_scan_stage="" _batch_scan_app_name=""
    _batch_scan_app_details || return $?
    # A partially inspectable selection must be reselected, never silently omitted.
    [[ ${#app_details[@]} -eq ${#selected_apps[@]} ]] || return 1
    local -a _review_original_details=("${app_details[@]}")
    local -a _review_auto_files=() _review_personal_files=() _review_app_sizes=()
    local -a _review_personal_paths=() _review_selected_paths=()
    export MOLE_UNINSTALL_WITH_DATA="$with_data"
    _uninstall_prepare_data_review || return $?
    ma_apps_text '应用及残留卸载预览（本机文件移到废纸篓，Homebrew 应用交由 brew 卸载）：' 'App and leftover uninstall preview (native files to Trash; Homebrew apps via brew uninstall):'
    local is_brew cask needs_sudo encoded_system encoded_diag encoded_review rest
    for detail in "${app_details[@]}"; do
        IFS='|' read -r name path bundle total encoded encoded_system _ needs_sudo is_brew cask encoded_diag encoded_review rest <<< "$detail"
        ma_apps_record "$path" "$ledger" || return 1
        printf '\n%s · %s\n' "$(mole_terminal_safe_text "$name")" "$(bytes_to_human "$((total * 1024))")"
        printf '  %s\n' "$(format_uninstall_preview_path "$path" "${_review_app_sizes[i]}")"
        files=$(decode_file_list "$encoded" "$name") || return $?
        while IFS= read -r file; do
            [[ -n "$file" ]] || continue
            ma_apps_clean_value "$file" || return 1
            ma_apps_record "$file" "$ledger" || return 1
        done <<< "$files"
        _uninstall_print_preview_paths "$files" '  ' || return $?
        review_system=$(decode_file_list "$encoded_review" "$name") || return $?
        if [[ -n "$review_system" ]]; then
            ma_apps_text '  以下系统残留仅列出，保留不删除：' '  System leftovers below are review only, kept:'
            _uninstall_print_preview_paths "$review_system" '    ' || return $?
        fi
        if [[ "$is_brew" == true && -n "$cask" ]]; then
            printf '%s\0' "$cask" >> "$casks"
            ma_apps_owner "$path" || return 1
            printf '  Homebrew: %s uninstall --cask %s\n' "$(mole_terminal_safe_text "$_MA_OWNER_BREW")" "$(mole_terminal_safe_text "$cask")"
        fi
        [[ "$needs_sudo" != true ]] || ma_apps_text '  执行时需要管理员授权。' '  Administrator authorization is needed during apply.'
        printf '%s\0' "$detail" >> "$details"
        i=$((i + 1))
    done
    if [[ ${#running_apps[@]} -gt 0 ]]; then
        ma_apps_text '执行时会先退出所选的运行中应用；请保存工作：' 'Apply will quit the selected running apps; save your work:'
        printf '  %s\n' "${running_apps[@]}"
    fi
    for file in "${_review_personal_paths[@]+"${_review_personal_paths[@]}"}"; do
        mole_identity_in_list "$file" "${_review_selected_paths[@]+"${_review_selected_paths[@]}"}" && continue
        ma_apps_text "保留个人数据：$(mole_terminal_safe_text "$file")" "Personal data kept: $(mole_terminal_safe_text "$file")"
    done
    for rest in "${leftover_notes[@]+"${leftover_notes[@]}"}"; do printf '  %s\n' "$(mole_terminal_safe_text "${rest#*|}")"; done
}

ma_apps_apply() {
    local details="$1" ledger="$2" owners="$3" path parent parent_id target_id tree detail
    ma_apps_private_file "$details" && ma_apps_private_file "$ledger" || return 2
    ma_apps_load_owners "$owners" || return $?
    local -a MA_APP_PATHS=() MA_APP_PARENTS=() MA_APP_PARENT_IDS=() MA_APP_IDS=() MA_APP_TREES=()
    while IFS= read -r -d '' path && IFS= read -r -d '' parent &&
        IFS= read -r -d '' parent_id && IFS= read -r -d '' target_id && IFS= read -r -d '' tree; do
        MA_APP_PATHS+=("$path"); MA_APP_PARENTS+=("$parent")
        MA_APP_PARENT_IDS+=("$parent_id"); MA_APP_IDS+=("$target_id"); MA_APP_TREES+=("$tree")
    done < "$ledger"
    local -a app_details=()
    ma_apps_read_details "$details" || return 1
    # Fail before process/service teardown if any previewed object was replaced.
    for path in "${MA_APP_PATHS[@]}"; do
        ma_apps_approved "$path" || { printf 'Changed after preview; nothing removed: %s\n' "$(mole_terminal_safe_text "$path")" >&2; return 1; }
    done
    for name in mole_delete remove_file_list _mole_move_to_trash_batch; do
        eval "$(declare -f "$name" | sed "1s/$name/ma_apps_original_$name/")"
    done
    mole_delete() {
        ma_apps_approved "$1" || { printf 'SKIP changed/unreviewed: %s\n' "$1" >&2; return 1; }
        ma_apps_original_mole_delete "$@"
    }
    remove_file_list() {
        local file
        while IFS= read -r file; do
            [[ -n "$file" ]] || continue
            # Missing leftovers were already removed by the owning cask.
            [[ -e "$file" || -L "$file" ]] || continue
            ma_apps_approved "$file" || return 1
        done <<< "$1"
        ma_apps_original_remove_file_list "$@"
    }
    _mole_move_to_trash_batch() {
        local file
        for file in "$@"; do ma_apps_approved "$file" || return 1; done
        ma_apps_original__mole_move_to_trash_batch "$@"
    }
    local name bundle total encoded encoded_system sensitive needs_sudo is_brew cask rest
    local admin=false
    for detail in "${app_details[@]}"; do
        IFS='|' read -r name path bundle total encoded encoded_system sensitive needs_sudo is_brew cask rest <<< "$detail"
        [[ "$needs_sudo" != true && "$is_brew" != true ]] || admin=true
    done
    export MOLE_UNINSTALL_MODE=1 MOLE_DRY_RUN=0 MOLE_DELETE_MODE=trash
    trap 'stop_sudo_session; cleanup_temp_files' EXIT
    if [[ "$admin" == true ]]; then
        ensure_sudo_session 'Admin required to uninstall selected apps' || return 1
    fi
    local total_size_freed=0 success_count=0 failed_count=0 brew_apps_removed=0
    local -a failed_items=() success_items=() success_names=() success_dock_targets=()
    local -a system_extension_warning_apps=() review_only_system_leftovers=() review_only_system_leftover_keys=() running_at_uninstall_apps=()
    local _batch_exec_app_name="" _batch_exec_stage=""
    local status=0
    _batch_execute_removals || status=$?
    _batch_render_summary
    stop_sudo_session
    [[ $status -eq 0 && $failed_count -eq 0 ]]
}

ma_apps_main() {
    local mode="${1:?mode required}"; shift
    export MOLE_CURRENT_COMMAND=uninstall MOLE_DRY_RUN=1 MOLE_DELETE_MODE=trash
    case "$mode" in
        list) ma_apps_list "$@" ;;
        preview) ma_apps_preview "$@" ;;
        apply) ma_apps_apply "$@" ;;
        *) return 2 ;;
    esac
}
[[ "${BASH_SOURCE[0]}" != "$0" ]] || ma_apps_main "$@"
