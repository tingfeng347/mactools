#!/bin/bash
# Current-user Chromium signing clones. Sizes are unknown because APFS shares extents.

_mole_browser_clone_root() {
    local root
    root=$(/usr/bin/getconf DARWIN_USER_DIR 2> /dev/null) || return 1
    case "$root" in /var/folders/*) root="/private$root" ;; esac
    [[ "$root" =~ ^/private/var/folders/[A-Za-z0-9_]{2}/[A-Za-z0-9_]+/0/?$ ]] || return 1
    root=${root%/}
    printf '%s/X\n' "${root%/0}"
}

_mole_browser_clones_idle() {
    local output rc=0 line
    output=$(run_with_timeout 2 /bin/ps -axo comm= 2>&1) || rc=$?
    [[ $rc -lt 128 ]] || return "$rc"
    [[ $rc -eq 0 && "$output" == *"/sbin/launchd"* ]] || return 1
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        case "$line" in
            *"/Google Chrome"*".app/"* | *"/Google Chrome"*".app.bundle/"* | \
                *"/Chromium.app/"* | *"/Chromium.app.bundle/"* | \
                *"/Helium.app/"* | *"/Helium.app.bundle/"* | \
                *"/com.google.Chrome"*".code_sign_clone/"* | *"/org.chromium.Chromium.code_sign_clone/"* | \
                *"/net.imput.helium.code_sign_clone/"* | \
                "Google Chrome"* | "Chromium" | *"/Google Chrome" | *"/Chromium" | \
                "Helium" | "Helium Helper"* | *"/Helium") return 1 ;;
        esac
    done <<< "$output"
}

_mole_browser_clone_snapshot() {
    local path="$1" root rest id app component uid bundle plist exe stats
    root=$(_mole_browser_clone_root) || return 1
    [[ "$path" == "$root/"* ]] || return 1
    rest=${path#"$root/"}
    id=${rest%%/*}
    case "$id" in
        com.google.Chrome.code_sign_clone) app="Google Chrome" ;;
        com.google.Chrome.beta.code_sign_clone) app="Google Chrome Beta" ;;
        com.google.Chrome.dev.code_sign_clone) app="Google Chrome Dev" ;;
        com.google.Chrome.canary.code_sign_clone) app="Google Chrome Canary" ;;
        org.chromium.Chromium.code_sign_clone) app="Chromium" ;;
        # Helium: 20 idle clones left on a Mac (2026-10-19). Mole Mac parity:
        # BrowserClonePolicy.browsers.
        net.imput.helium.code_sign_clone) app="Helium" ;;
        *) return 1 ;;
    esac
    [[ "${rest#*/}" =~ ^code_sign_clone\.[A-Za-z0-9]{6}$ ]] || return 1
    component="$path"
    while [[ "$component" != / && -n "$component" ]]; do
        [[ ! -L "$component" ]] || return 1
        component=${component%/*}
    done
    uid=$(/usr/bin/id -u) || return 1
    [[ -d "$path" && -d "${path%/*}" ]] || return 1
    [[ "$(/usr/bin/stat -f %u "$path")" == "$uid" && "$(/usr/bin/stat -f %u "${path%/*}")" == "$uid" ]] || return 1
    # Exactly one known application bundle, including Chromium's newer .bundle suffix.
    local entry count=0
    for entry in "$path"/* "$path"/.[!.]* "$path"/..?*; do
        [[ -e "$entry" || -L "$entry" ]] || continue
        count=$((count + 1))
        bundle="$entry"
    done
    [[ $count -eq 1 && ("$bundle" == "$path/$app.app" || "$bundle" == "$path/$app.app.bundle") ]] || return 1
    plist="$bundle/Contents/Info.plist"
    exe="$bundle/Contents/MacOS/$app"
    for component in "$bundle" "$bundle/Contents" "$bundle/Contents/MacOS" "$plist" "$exe"; do
        [[ -e "$component" && ! -L "$component" ]] || return 1
    done
    [[ -f "$exe" && -f "$plist" && "$(/usr/bin/stat -f %z "$plist")" -le 65536 ]] || return 1
    local metadata rc=0
    metadata=$(run_with_timeout 1 /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist" 2> /dev/null) || rc=$?
    [[ $rc -lt 128 ]] || return "$rc"
    [[ $rc -eq 0 && "$metadata" == "${id%.code_sign_clone}" ]] || return 1
    metadata=$(run_with_timeout 1 /usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist" 2> /dev/null) || rc=$?
    [[ $rc -lt 128 ]] || return "$rc"
    [[ $rc -eq 0 && "$metadata" == "$app" ]] || return 1
    local opened
    opened=$(run_with_timeout 1 /usr/sbin/lsof -nP -F pn -- "$exe" 2>&1) || rc=$?
    [[ $rc -lt 128 ]] || return "$rc"
    [[ $rc -eq 1 && -z "$opened" ]] || return 1
    stats=$(/usr/bin/stat -f '%d:%i:%m:%c' "${path%/*}" "$path" "$bundle" "$plist" "$exe") || return 1
    printf '%s\n' "$stats"
}

_mole_browser_clone_final_guard() {
    local current
    _mole_browser_clones_idle || return $?
    current=$(_mole_browser_clone_snapshot "$1") || return $?
    [[ "$current" == "${_MOLE_BROWSER_CLONE_REVIEW:-}" ]] || return 1
    _mole_browser_clones_idle
}

clean_browser_code_sign_clones() {
    local deadline="${1:-}" root id path rc=0
    root=$(_mole_browser_clone_root) || return 0
    _mole_browser_clones_idle || rc=$?
    [[ $rc -lt 128 ]] || return "$rc"
    [[ $rc -eq 0 ]] || return 0
    local _MOLE_BROWSER_CLONE_REVIEW="" _MOLE_SAFE_REMOVE_FINAL_GUARD=_mole_browser_clone_final_guard
    for id in com.google.Chrome com.google.Chrome.beta com.google.Chrome.dev com.google.Chrome.canary org.chromium.Chromium net.imput.helium; do
        for path in "$root/$id.code_sign_clone"/code_sign_clone.*; do
            [[ -d "$path" && ! -L "$path" ]] || continue
            system_cleanup_budget_reached "$deadline" && return 0
            rc=0
            _MOLE_BROWSER_CLONE_REVIEW=$(_mole_browser_clone_snapshot "$path") || rc=$?
            [[ $rc -lt 128 ]] || return "$rc"
            [[ $rc -eq 0 ]] || continue
            _mole_snapshot_path_identity "$path" || continue
            local parent="$_MOLE_PATH_SNAPSHOT_PARENT" parent_id="$_MOLE_PATH_SNAPSHOT_PARENT_ID" target_id="$_MOLE_PATH_SNAPSHOT_TARGET_ID"
            rc=0
            _mole_browser_clone_final_guard "$path" || rc=$?
            [[ $rc -lt 128 ]] || return "$rc"
            [[ $rc -eq 0 ]] || continue
            rc=0
            safe_remove "$path" true unknown "$deadline" "$parent" "$parent_id" "$target_id" || rc=$?
            mole_rc_timeout_or_signal "$rc" && return "$rc"
            [[ $rc -ne 0 ]] || code_sign_cleaned=$((code_sign_cleaned + 1))
        done
    done
    return 0
}
