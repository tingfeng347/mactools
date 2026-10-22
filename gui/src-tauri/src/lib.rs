//! Native backend for the mactools desktop app.
//!
//! Two execution paths are provided:
//! * `run_mactools` runs a fixed mactools subcommand with argv (never a shell)
//!   and streams stdout/stderr to the UI.
//! * `pty_*` opens a real pseudo-terminal so interactive flows (`maci`, `macr`,
//!   `macc`, ...) behave exactly like they do in Terminal.app.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::SystemTime;

use base64::Engine as _;
use portable_pty::{native_pty_system, ChildKiller, CommandBuilder, MasterPty, PtySize};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};

const ALLOWED_COMMANDS: &[&str] = &[
    "install", "remove", "update", "doctor", "clean", "optimize", "mirror", "ai", "setup",
    "help", "--help", "--version", "version", "completion",
];

#[derive(Default)]
struct AppState {
    cli: PathBuf,
    sessions: Mutex<HashMap<String, PtySession>>,
    icons: Mutex<HashMap<String, Option<String>>>,
}

struct PtySession {
    writer: Box<dyn Write + Send>,
    master: Box<dyn MasterPty + Send>,
    killer: Box<dyn ChildKiller + Send + Sync>,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct StreamEvent {
    run_id: String,
    stream: String,
    line: String,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PtyOutput {
    id: String,
    data: String,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PtyExit {
    id: String,
    code: Option<i32>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RunResult {
    code: Option<i32>,
    output: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct EnvInfo {
    app_version: String,
    cli_path: String,
    cli_version: String,
    cli_outdated: bool,
    brew_path: String,
    brew_version: String,
    python_path: String,
    macos: String,
    arch: String,
    hostname: String,
    user: String,
    home: String,
    disk_total: u64,
    disk_free: u64,
    formula_count: usize,
    cask_count: usize,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct AppEntry {
    /// Stable identity emitted by `ma remove --apps --list-json`.
    id: String,
    name: String,
    path: String,
    bundle_id: String,
    version: String,
    size: u64,
    system: bool,
}

#[derive(Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PackageEntry {
    source: String,
    name: String,
    display_name: String,
    current: String,
    target: String,
    app_path: Option<String>,
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

fn expanded_path() -> String {
    let home = home_dir();
    let mut parts = vec![
        home.join(".local/bin").to_string_lossy().to_string(),
        "/opt/homebrew/bin".to_string(),
        "/opt/homebrew/sbin".to_string(),
        "/usr/local/bin".to_string(),
        "/usr/local/sbin".to_string(),
    ];
    if let Ok(existing) = std::env::var("PATH") {
        parts.push(existing);
    }
    parts.join(":")
}

fn resolve_cli() -> Option<PathBuf> {
    if let Some(value) = std::env::var_os("MACTOOLS_BIN") {
        let path = PathBuf::from(value);
        if path.is_file() {
            return Some(path);
        }
    }
    // `tauri dev` should exercise the checkout under test, not an older
    // Homebrew release that happens to be first on PATH.
    #[cfg(debug_assertions)]
    {
        let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../bin/ma");
        if dev.is_file() {
            return Some(dev);
        }
    }
    if let Ok(path) = which::which("ma") {
        return Some(path);
    }
    let home = home_dir();
    let candidates = [
        home.join(".local/bin/ma"),
        PathBuf::from("/opt/homebrew/bin/ma"),
        PathBuf::from("/usr/local/bin/ma"),
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../bin/ma"),
    ];
    candidates.into_iter().find(|path| path.is_file())
}

fn base_command(program: &Path) -> Command {
    let mut command = Command::new(program);
    command
        .env("PATH", expanded_path())
        .env("MACTOOLS_LANG", "zh")
        .env("NO_COLOR", "1")
        .env("TERM", "dumb")
        .env("HOMEBREW_NO_AUTO_UPDATE", "1")
        .env("HOMEBREW_NO_ENV_HINTS", "1")
        .env("HOMEBREW_NO_INSTALL_CLEANUP", "1")
        .stdin(Stdio::null());
    if let Ok(home) = std::env::var("HOME") {
        command.current_dir(home);
    }
    command
}

fn run_capture(program: &Path, args: &[String]) -> Result<String, String> {
    let output = base_command(program)
        .args(args)
        .output()
        .map_err(|error| format!("无法启动 mactools：{error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let code = output.status.code();
        if let Some(message) = outdated_cli_message(program, args, &detail, code) {
            return Err(message);
        }
        let code = code.map(|value| value.to_string()).unwrap_or_else(|| "?".into());
        return Err(if detail.is_empty() {
            format!("mactools 退出码 {code}")
        } else {
            detail
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

/// First CLI release that ships the machine-facing flags (`--list-json`,
/// `--pick`) the desktop app drives every query and action with.
const MIN_CLI_VERSION: &str = "0.1.18";

fn parse_version(text: &str) -> Option<(u64, u64, u64)> {
    let token = text
        .split_whitespace()
        .find(|part| part.starts_with(|c: char| c.is_ascii_digit()))?;
    let core = token.split(['-', '+']).next()?;
    let mut parts = core.split('.');
    let major = parts.next()?.parse::<u64>().ok()?;
    let minor = parts.next().map_or(Some(0), |value| value.parse::<u64>().ok())?;
    let patch = parts.next().map_or(Some(0), |value| value.parse::<u64>().ok())?;
    Some((major, minor, patch))
}

fn cli_version(cli: &Path) -> Option<(u64, u64, u64)> {
    parse_version(&capture(cli, &["--version"]))
}

fn is_cli_outdated(version_output: &str) -> bool {
    match (parse_version(version_output), parse_version(MIN_CLI_VERSION)) {
        (Some(installed), Some(minimum)) => installed < minimum,
        _ => false,
    }
}

fn upgrade_hint(installed: Option<(u64, u64, u64)>) -> String {
    let installed = installed
        .map(|(major, minor, patch)| format!("{major}.{minor}.{patch}"))
        .unwrap_or_else(|| "未知版本".to_string());
    format!(
        "命令行工具 {installed} 低于最低要求 {MIN_CLI_VERSION}，缺少桌面版需要的接口。请运行 brew upgrade tingfeng347/tap/mactools，然后重新打开 mactools。"
    )
}

/// Blocks a stale formula before it can answer with a raw argparse error.
fn outdated_cli_error(cli: &Path) -> Option<String> {
    let installed = cli_version(cli)?;
    let expected = parse_version(MIN_CLI_VERSION)?;
    (installed < expected).then(|| upgrade_hint(Some(installed)))
}

/// Fallback for a CLI whose `--version` could not be parsed: recognise argparse
/// rejecting the machine-facing flags and answer with the same upgrade hint.
fn outdated_cli_message(cli: &Path, args: &[String], detail: &str, code: Option<i32>) -> Option<String> {
    let used_machine_flag = args
        .iter()
        .any(|arg| arg == "--list-json" || arg == "--pick");
    let rejected = detail.contains("--list-json") || detail.contains("--pick");
    (code == Some(2) && used_machine_flag && rejected).then(|| upgrade_hint(cli_version(cli)))
}

fn capture(program: &Path, args: &[&str]) -> String {
    base_command(program)
        .args(args)
        .output()
        .ok()
        .filter(|out| out.status.success())
        .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .unwrap_or_default()
}

fn find_program(name: &str) -> Option<PathBuf> {
    let home = home_dir();
    let candidates = [
        PathBuf::from(format!("/opt/homebrew/bin/{name}")),
        PathBuf::from(format!("/usr/local/bin/{name}")),
        home.join(format!(".local/bin/{name}")),
    ];
    for path in candidates {
        if path.is_file() {
            return Some(path);
        }
    }
    which::which(name).ok()
}

fn df_usage() -> (u64, u64) {
    let df = PathBuf::from("/bin/df");
    if let Some(output) = base_command(&df).arg("-k").arg("/").output().ok() {
        let text = String::from_utf8_lossy(&output.stdout);
        if let Some(line) = text.lines().nth(1) {
            let fields: Vec<&str> = line.split_whitespace().collect();
            if fields.len() > 3 {
                let total = fields[1].parse::<u64>().unwrap_or(0) * 1024;
                let free = fields[3].parse::<u64>().unwrap_or(0) * 1024;
                return (total, free);
            }
        }
    }
    (0, 0)
}

fn count_lines(brew: &Path, args: &[&str]) -> usize {
    let text = capture(brew, args);
    text.lines().filter(|line| !line.trim().is_empty()).count()
}

#[tauri::command]
fn environment(state: State<'_, AppState>) -> EnvInfo {
    let cli = if state.cli.as_os_str().is_empty() { None } else { Some(state.cli.clone()) };
    let version = cli
        .as_ref()
        .filter(|path| path.is_file())
        .map(|path| capture(path, &["--version"]))
        .unwrap_or_default();
    let brew = find_program("brew");
    let brew_version = brew
        .as_ref()
        .map(|path| capture(path, &["--version"]).lines().next().unwrap_or("").to_string())
        .unwrap_or_default();
    let python = find_program("python3")
        .map(|path| path.to_string_lossy().to_string())
        .unwrap_or_default();
    let (disk_total, disk_free) = df_usage();
    let formula_count = brew.as_ref().map(|path| count_lines(path, &["list", "--formula", "-1"])).unwrap_or(0);
    let cask_count = brew.as_ref().map(|path| count_lines(path, &["list", "--cask", "-1"])).unwrap_or(0);
    EnvInfo {
        app_version: env!("CARGO_PKG_VERSION").to_string(),
        cli_path: cli.map(|path| path.to_string_lossy().to_string()).unwrap_or_default(),
        cli_version: version.replace("mactools ", ""),
        cli_outdated: is_cli_outdated(&version),
        brew_path: brew.as_ref().map(|path| path.to_string_lossy().to_string()).unwrap_or_default(),
        brew_version,
        python_path: python,
        macos: capture(&PathBuf::from("/usr/bin/sw_vers"), &["-productVersion"]),
        arch: capture(&PathBuf::from("/usr/bin/uname"), &["-m"]),
        hostname: capture(&PathBuf::from("/bin/hostname"), &["-s"]),
        user: std::env::var("USER").unwrap_or_default(),
        home: home_dir().to_string_lossy().to_string(),
        disk_total,
        disk_free,
        formula_count,
        cask_count,
    }
}

/// Read-only package discovery used by the native pages. The CLI emits a
/// single JSON document, so the UI never has to parse human table output and
/// never enters fzf.
#[tauri::command]
async fn search_packages(
    state: State<'_, AppState>,
    action: String,
    query: String,
    source: Option<String>,
) -> Result<Vec<PackageEntry>, String> {
    if state.cli.as_os_str().is_empty() {
        return Err("未找到 mactools。请先运行 brew install tingfeng347/tap/mactools。".into());
    }
    if !matches!(action.as_str(), "install" | "remove" | "update") {
        return Err(format!("不支持的查询类型：{action}"));
    }
    if let Some(value) = source.as_deref() {
        if value != "formula" && value != "cask" {
            return Err(format!("不支持的来源：{value}"));
        }
    }
    let cli = state.cli.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(message) = outdated_cli_error(&cli) {
            return Err(message);
        }
        let mut args = vec![action.clone(), "--list-json".to_string()];
        if action == "remove" {
            args.push("--apps".into());
        }
        if let Some(value) = source {
            args.push(format!("--{value}"));
        }
        let query = query.trim();
        if !query.is_empty() {
            args.push(query.to_string());
        }
        let raw = run_capture(&cli, &args)?;
        serde_json::from_str::<Vec<PackageEntry>>(&raw)
            .map_err(|error| format!("无法解析 mactools 返回的数据：{error}"))
    })
    .await
    .map_err(|error| format!("查询线程失败：{error}"))?
}

#[tauri::command]
async fn run_mactools(
    app: AppHandle,
    state: State<'_, AppState>,
    run_id: String,
    args: Vec<String>,
) -> Result<RunResult, String> {
    if state.cli.as_os_str().is_empty() {
        return Err("未找到 mactools。请先运行 brew install tingfeng347/tap/mactools。".into());
    }
    let head = args.first().map(String::as_str).unwrap_or("");
    if !ALLOWED_COMMANDS.contains(&head) {
        return Err(format!("不允许的命令：{head}"));
    }
    if args.iter().any(|arg| arg.contains('\0')) {
        return Err("命令参数包含非法字符".into());
    }
    let needs_inventory = matches!(head, "install" | "remove" | "update");
    let cli = state.cli.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if needs_inventory {
            if let Some(message) = outdated_cli_error(&cli) {
                return Err(message);
            }
        }
        let mut child = base_command(&cli)
            .args(&args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("无法启动 mactools：{error}"))?;
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let mut readers = Vec::new();
        if let Some(stream) = stdout {
            let handle = app.clone();
            let id = run_id.clone();
            readers.push(thread::spawn(move || pump(handle, id, "stdout", stream)));
        }
        if let Some(stream) = stderr {
            let handle = app.clone();
            let id = run_id.clone();
            readers.push(thread::spawn(move || pump(handle, id, "stderr", stream)));
        }
        let status = child.wait().map_err(|error| format!("等待命令结束失败：{error}"))?;
        for reader in readers {
            let _ = reader.join();
        }
        Ok(RunResult {
            code: status.code(),
            output: String::new(),
        })
    })
    .await
    .map_err(|error| format!("命令线程失败：{error}"))?
}

fn pump(app: AppHandle, run_id: String, stream: &'static str, source: impl Read) {
    let mut collected = String::new();
    for line in BufReader::new(source).lines() {
        let Ok(line) = line else { break };
        if collected.len() < 1024 * 1024 {
            collected.push_str(&line);
            collected.push('\n');
        }
        let _ = app.emit(
            "cmd-output",
            StreamEvent { run_id: run_id.clone(), stream: stream.to_string(), line },
        );
    }
}

#[tauri::command]
fn pty_start(
    app: AppHandle,
    state: State<'_, AppState>,
    id: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    if state.cli.as_os_str().is_empty() {
        return Err("未找到 mactools。请先运行 brew install tingfeng347/tap/mactools。".into());
    }
    let pair = native_pty_system()
        .openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|error| format!("无法创建终端：{error}"))?;
    // Keep a real interactive shell alive so every TUI flow can be run in
    // sequence. The CLI directory is prepended, which also makes `maci`,
    // `macr`, ... resolve to the same build as the native pages in `tauri dev`.
    let shell = if Path::new("/bin/zsh").is_file() { "/bin/zsh" } else { "/bin/bash" };
    let mut command = CommandBuilder::new(shell);
    command.arg("-l");
    command.arg("-i");
    let mut path = expanded_path();
    if let Some(parent) = state.cli.parent() {
        path = format!("{}:{path}", parent.to_string_lossy());
    }
    command.env("PATH", path);
    command.env("MACTOOLS_BIN", state.cli.to_string_lossy().to_string());
    command.env("MACTOOLS_LANG", "zh");
    command.env("TERM", "xterm-256color");
    command.env("COLORTERM", "truecolor");
    command.env("HOMEBREW_NO_ENV_HINTS", "1");
    if let Ok(home) = std::env::var("HOME") {
        command.cwd(home);
    }
    let child = pair
        .slave
        .spawn_command(command)
        .map_err(|error| format!("无法启动终端会话：{error}"))?;
    drop(pair.slave);
    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|error| format!("无法读取终端输出：{error}"))?;
    let writer = pair
        .master
        .take_writer()
        .map_err(|error| format!("无法写入终端：{error}"))?;
    let killer = child.clone_killer();
    {
        let mut sessions = state.sessions.lock().map_err(|_| "终端状态已损坏")?;
        sessions.insert(id.clone(), PtySession { writer, master: pair.master, killer });
    }
    let handle = app.clone();
    let session_id = id.clone();
    thread::spawn(move || {
        let mut carry: Vec<u8> = Vec::new();
        let mut buffer = [0u8; 8192];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(read) => {
                    carry.extend_from_slice(&buffer[..read]);
                    let text = decode_utf8(&mut carry);
                    if !text.is_empty() {
                        let _ = handle.emit(
                            "pty-output",
                            PtyOutput { id: session_id.clone(), data: text },
                        );
                    }
                }
            }
        }
        let _ = handle.emit("pty-exit", PtyExit { id: session_id.clone(), code: None });
    });
    Ok(())
}

/// Decode the longest valid UTF-8 prefix, keeping an incomplete tail for the
/// next read so multi-byte characters split across chunks are not corrupted.
fn decode_utf8(carry: &mut Vec<u8>) -> String {
    match std::str::from_utf8(carry) {
        Ok(text) => {
            let out = text.to_string();
            carry.clear();
            out
        }
        Err(error) => {
            let valid = error.valid_up_to();
            let out = String::from_utf8_lossy(&carry[..valid]).to_string();
            let rest = carry[valid..].to_vec();
            *carry = rest;
            if carry.len() > 4 {
                carry.clear();
            }
            out
        }
    }
}

#[tauri::command]
fn pty_write(state: State<'_, AppState>, id: String, data: String) -> Result<(), String> {
    let mut sessions = state.sessions.lock().map_err(|_| "终端状态已损坏")?;
    let session = sessions.get_mut(&id).ok_or("终端会话不存在")?;
    session
        .writer
        .write_all(data.as_bytes())
        .and_then(|_| session.writer.flush())
        .map_err(|error| format!("写入终端失败：{error}"))
}

#[tauri::command]
fn pty_resize(state: State<'_, AppState>, id: String, cols: u16, rows: u16) -> Result<(), String> {
    let sessions = state.sessions.lock().map_err(|_| "终端状态已损坏")?;
    let session = sessions.get(&id).ok_or("终端会话不存在")?;
    session
        .master
        .resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|error| format!("调整终端尺寸失败：{error}"))
}

#[tauri::command]
fn pty_kill(state: State<'_, AppState>, id: String) -> Result<(), String> {
    let mut sessions = state.sessions.lock().map_err(|_| "终端状态已损坏")?;
    if let Some(mut session) = sessions.remove(&id) {
        let _ = session.killer.kill();
    }
    Ok(())
}

fn app_size(paths: &[PathBuf]) -> HashMap<PathBuf, u64> {
    let mut result = HashMap::new();
    if paths.is_empty() {
        return result;
    }
    let du = PathBuf::from("/usr/bin/du");
    let output = base_command(&du)
        .arg("-sk")
        .args(paths)
        .output();
    if let Ok(output) = output {
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            let mut fields = line.splitn(2, '\t');
            let size = fields.next().and_then(|value| value.trim().parse::<u64>().ok()).unwrap_or(0);
            if let Some(path) = fields.next() {
                result.insert(PathBuf::from(path), size * 1024);
            }
        }
    }
    result
}

fn read_bundle(path: &Path) -> Option<(String, String, String, Option<String>)> {
    let info = path.join("Contents/Info.plist");
    let value = plist::Value::from_file(&info).ok()?;
    let dict = value.as_dictionary()?;
    let get = |key: &str| dict.get(key).and_then(|value| value.as_string()).map(str::to_string);
    let name = get("CFBundleDisplayName")
        .or_else(|| get("CFBundleName"))
        .unwrap_or_else(|| path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default());
    let bundle_id = get("CFBundleIdentifier").unwrap_or_default();
    let version = get("CFBundleShortVersionString")
        .or_else(|| get("CFBundleVersion"))
        .unwrap_or_default();
    let icon = get("CFBundleIconFile");
    Some((name, bundle_id, version, icon))
}

fn icon_path(bundle: &Path, icon: Option<String>) -> Option<PathBuf> {
    let resources = bundle.join("Contents/Resources");
    let candidates: Vec<String> = match icon {
        Some(mut name) => {
            if !name.ends_with(".icns") {
                name.push_str(".icns");
            }
            vec![name]
        }
        None => vec![
            format!("{}.icns", bundle.file_stem()?.to_string_lossy()),
            "AppIcon.icns".to_string(),
            "app.icns".to_string(),
        ],
    };
    for name in candidates {
        let path = resources.join(name);
        if path.is_file() {
            return Some(path);
        }
    }
    let entries = std::fs::read_dir(&resources).ok()?;
    let mut found: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().map(|ext| ext == "icns").unwrap_or(false))
        .collect();
    found.sort();
    found.into_iter().next()
}

fn icon_data(bundle: &Path, icon: Option<String>) -> Option<String> {
    let source = icon_path(bundle, icon)?;
    let stamp = std::fs::metadata(&source)
        .ok()
        .and_then(|meta| meta.modified().ok())
        .and_then(|time| time.duration_since(SystemTime::UNIX_EPOCH).ok())
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    use std::hash::{Hash, Hasher};
    source.hash(&mut hasher);
    stamp.hash(&mut hasher);
    let key = format!("{:x}", hasher.finish());
    let cache = home_dir().join("Library/Caches/mactools/gui-icons");
    let _ = std::fs::create_dir_all(&cache);
    let target = cache.join(format!("{key}.png"));
    if !target.is_file() {
        let status = base_command(&PathBuf::from("/usr/bin/sips"))
            .args(["-s", "format", "png", "-Z", "128"])
            .arg(&source)
            .arg("--out")
            .arg(&target)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        if !status.map(|value| value.success()).unwrap_or(false) {
            return None;
        }
    }
    let bytes = std::fs::read(&target).ok()?;
    if bytes.len() > 2 * 1024 * 1024 {
        return None;
    }
    Some(format!(
        "data:image/png;base64,{}",
        base64::engine::general_purpose::STANDARD.encode(bytes)
    ))
}

#[tauri::command]
async fn scan_apps(state: State<'_, AppState>) -> Result<Vec<AppEntry>, String> {
    if state.cli.as_os_str().is_empty() {
        return Err("未找到 mactools。请先运行 brew install tingfeng347/tap/mactools。".into());
    }
    let cli = state.cli.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let raw = run_capture(
            &cli,
            &["remove".into(), "--apps".into(), "--list-json".into()],
        )?;
        let entries = serde_json::from_str::<Vec<PackageEntry>>(&raw)
            .map_err(|error| format!("无法解析应用列表：{error}"))?;
        let bundles: Vec<(PackageEntry, PathBuf)> = entries
            .into_iter()
            .filter(|entry| entry.source == "app")
            .filter_map(|entry| {
                let path = PathBuf::from(entry.app_path.clone()?);
                if path.is_dir() { Some((entry, path)) } else { None }
            })
            .collect();
        let paths: Vec<PathBuf> = bundles.iter().map(|(_, path)| path.clone()).collect();
        let sizes = app_size(&paths);
        let mut apps = Vec::with_capacity(bundles.len());
        for (entry, path) in bundles {
            let (bundle_name, bundle_id, bundle_version, _) = read_bundle(&path)
                .unwrap_or_else(|| (String::new(), String::new(), String::new(), None));
            let name = if entry.display_name.trim().is_empty() {
                bundle_name
            } else {
                entry.display_name.clone()
            };
            let version = if entry.current.trim().is_empty() || entry.current == "-" {
                bundle_version
            } else {
                entry.current.clone()
            };
            apps.push(AppEntry {
                id: entry.name,
                name,
                path: path.to_string_lossy().to_string(),
                bundle_id,
                version,
                size: sizes.get(&path).copied().unwrap_or(0),
                system: path.starts_with("/System"),
            });
        }
        apps.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
        Ok(apps)
    })
    .await
    .map_err(|error| format!("扫描线程失败：{error}"))?
}

#[tauri::command]
fn app_icon(state: State<'_, AppState>, path: String) -> Result<Option<String>, String> {
    let bundle = PathBuf::from(&path);
    if !bundle.is_dir() {
        return Ok(None);
    }
    if let Some(value) = state.icons.lock().ok().and_then(|icons| icons.get(&path).cloned()) {
        return Ok(value);
    }
    let Some((_, _, _, icon)) = read_bundle(&bundle) else { return Ok(None) };
    let value = icon_data(&bundle, icon);
    if let Ok(mut icons) = state.icons.lock() {
        icons.insert(path, value.clone());
    }
    Ok(value)
}

#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let open = PathBuf::from("/usr/bin/open");
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err("路径不存在".into());
    }
    // `open -R` reveals files and bundles in Finder without launching them.
    let status = Command::new(&open)
        .arg("-R")
        .arg(&target)
        .status()
        .map_err(|error| format!("无法打开 Finder：{error}"))?;
    if status.success() { Ok(()) } else { Err("Finder 无法定位该路径".into()) }
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("仅允许打开 http(s) 链接".into());
    }
    Command::new("/usr/bin/open")
        .arg(&url)
        .status()
        .map_err(|error| format!("无法打开链接：{error}"))?;
    Ok(())
}

#[tauri::command]
fn save_text(path: String, content: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    std::fs::write(&target, content).map_err(|error| format!("保存失败：{error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let cli = resolve_cli().unwrap_or_default();
    tauri::Builder::default()
        .manage(AppState { cli, ..Default::default() })
        .invoke_handler(tauri::generate_handler![
            environment,
            search_packages,
            run_mactools,
            pty_start,
            pty_write,
            pty_resize,
            pty_kill,
            scan_apps,
            app_icon,
            reveal_path,
            open_url,
            save_text
        ])
        .setup(|app| {
            let _ = app.get_webview_window("main");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running mactools");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_multibyte_utf8_split_across_reads() {
        let bytes = "中文abc".as_bytes();
        let mut carry = bytes[..4].to_vec();
        assert_eq!(decode_utf8(&mut carry), "中");
        assert_eq!(carry, &bytes[3..4]);
        carry.extend_from_slice(&bytes[4..]);
        assert_eq!(decode_utf8(&mut carry), "文abc");
        assert!(carry.is_empty());
    }

    #[test]
    fn parses_cli_package_json() {
        let raw = r#"[
            {"source":"cask","name":"firefox","displayName":"Firefox","current":"-","target":"-","appPath":null},
            {"source":"app","name":"/Applications/Foo.app","displayName":"Foo","current":"1.2","target":"-","appPath":"/Applications/Foo.app"}
        ]"#;
        let entries: Vec<PackageEntry> = serde_json::from_str(raw).expect("valid package json");
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].display_name, "Firefox");
        assert_eq!(entries[0].app_path, None);
        assert_eq!(entries[1].source, "app");
        assert_eq!(entries[1].app_path.as_deref(), Some("/Applications/Foo.app"));
    }

    fn stub_cli(version: &str) -> PathBuf {
        let unique = format!("{}-{}", std::process::id(), version.replace('.', "_"));
        let dir = std::env::temp_dir().join(format!("mactools-cli-{unique}"));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let path = dir.join("ma");
        std::fs::write(
            &path,
            format!("#!/bin/sh\ncase \"$1\" in\n  --version) echo 'mactools {version}' ;;\nesac\n"),
        )
        .expect("stub written");
        let mut perms = std::fs::metadata(&path).expect("stub metadata").permissions();
        std::os::unix::fs::PermissionsExt::set_mode(&mut perms, 0o755);
        std::fs::set_permissions(&path, perms).expect("stub executable");
        path
    }

    #[test]
    fn parses_cli_version_output() {
        assert_eq!(parse_version("mactools 0.1.19"), Some((0, 1, 19)));
        assert_eq!(parse_version("mactools 1.2"), Some((1, 2, 0)));
        assert_eq!(parse_version("0.2.0-beta.1"), Some((0, 2, 0)));
        assert_eq!(parse_version("no version here"), None);
    }

    #[test]
    fn rejects_cli_older_than_the_minimum_supported_version() {
        let cli = stub_cli("0.1.17");
        let message = outdated_cli_error(&cli).expect("outdated CLI is rejected");
        assert!(message.contains("0.1.17"), "{message}");
        assert!(message.contains(MIN_CLI_VERSION), "{message}");
        assert!(message.contains("brew upgrade tingfeng347/tap/mactools"), "{message}");
    }

    #[test]
    fn flags_outdated_cli_for_the_sidebar() {
        assert!(is_cli_outdated("mactools 0.1.17"));
        assert!(!is_cli_outdated("mactools 0.1.19"));
        assert!(!is_cli_outdated(""));
    }

    #[test]
    fn accepts_cli_at_or_above_the_minimum_supported_version() {
        assert_eq!(outdated_cli_error(&stub_cli("0.1.18")), None);
        assert_eq!(outdated_cli_error(&stub_cli("0.1.19")), None);
    }

    #[test]
    fn turns_argparse_rejection_into_an_upgrade_hint() {
        let cli = stub_cli("0.1.17");
        let args = vec!["install".to_string(), "--list-json".to_string()];
        let detail = "ma: error: unrecognized arguments: --list-json";
        let message = outdated_cli_message(&cli, &args, detail, Some(2)).expect("hint");
        assert!(message.contains("brew upgrade tingfeng347/tap/mactools"), "{message}");
        assert_eq!(outdated_cli_message(&cli, &args, "boom", Some(1)), None);
    }

    #[test]
    fn serializes_app_entry_for_the_ui() {
        let entry = AppEntry {
            id: "app.123".into(),
            name: "Foo".into(),
            path: "/Applications/Foo.app".into(),
            bundle_id: "com.example.foo".into(),
            version: "1.0".into(),
            size: 1024,
            system: false,
        };
        let json = serde_json::to_string(&entry).expect("serializes");
        assert!(json.contains("\"bundleId\":\"com.example.foo\""));
        assert!(json.contains("\"path\":\"/Applications/Foo.app\""));
    }
}
