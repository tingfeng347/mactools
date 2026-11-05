"""Shared bounded I/O and confirmation for the mirror and AI commands."""

import json
import os
import signal
import shlex
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


def text(zh, en):
    lang = os.environ.get("MACTOOLS_LANG", "zh")
    return en if lang == "en" else zh


def fetch(url, timeout=10, limit=4 * 1024 * 1024, headers=None, partial=False):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Expected an HTTP(S) URL without credentials")
    request = urllib.request.Request(
        url, headers={"User-Agent": "mactools/1", **(headers or {})}
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if (
            parsed.scheme == "https"
            and urllib.parse.urlsplit(response.url).scheme != "https"
        ):
            raise ValueError("HTTPS download redirected to insecure transport")
        data = bytearray()
        while len(data) < limit:
            if time.monotonic() - started > timeout:
                raise TimeoutError("download timed out")
            chunk = response.read(min(65536, limit - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if not partial and len(data) == limit and response.read(1):
            raise ValueError("download exceeded size limit")
        return bytes(data)


def _signal_group(process, signum):
    process.poll()
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            raise


def run(argv, timeout=10, **kwargs):
    check = kwargs.pop("check", False)
    input_data = kwargs.pop("input", None)
    if input_data is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input may not both be used")
        kwargs["stdin"] = subprocess.PIPE
    capture = kwargs.pop("capture_output", False)
    if capture:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr may not be redirected with capture_output")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdin = kwargs.get("stdin")
    interactive = not capture or (
        hasattr(stdin, "isatty") and stdin.isatty())
    terminal_fd = foreground_group = previous_ttou = None
    if interactive and threading.current_thread() is threading.main_thread():
        try:
            terminal_fd = os.open("/dev/tty", os.O_RDWR)
            foreground_group = os.tcgetpgrp(terminal_fd)
            if foreground_group != os.getpgrp():
                raise ValueError("Run interactive commands in the foreground.")
        except OSError:
            if terminal_fd is not None:
                os.close(terminal_fd)
            terminal_fd = foreground_group = None
        except BaseException:
            os.close(terminal_fd)
            raise
    process = None
    try:
        process = subprocess.Popen(
            [str(value) for value in argv],
            start_new_session=not interactive,
            preexec_fn=os.setpgrp if interactive else None,
            **kwargs,
        )
        if terminal_fd is not None:
            previous_ttou = signal.getsignal(signal.SIGTTOU)
            signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            try:
                os.tcsetpgrp(terminal_fd, process.pid)
                _signal_group(process, signal.SIGCONT)
            except OSError:
                if process.poll() is None:
                    raise
        stdout, stderr = process.communicate(input=input_data, timeout=timeout)
    except BaseException:
        if process is not None:
            _signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                pass
            _signal_group(process, signal.SIGKILL)
            process.communicate()
        raise
    finally:
        try:
            if terminal_fd is not None:
                try:
                    if process is not None and os.tcgetpgrp(terminal_fd) == process.pid:
                        os.tcsetpgrp(terminal_fd, foreground_group)
                finally:
                    os.close(terminal_fd)
        finally:
            if previous_ttou is not None:
                signal.signal(signal.SIGTTOU, previous_ttou)
    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def command(argv):
    print("> " + shlex.join([str(x) for x in argv]), flush=True)


def confirm(message, yes=False):
    if yes:
        return True
    try:
        if os.isatty(0):
            answer = input(message + " [y/N] ")
        else:
            try:
                # A terminal cannot seek, so buffered read/write mode fails.
                with open("/dev/tty", "r+b", buffering=0) as tty:
                    tty.write((message + " [y/N] ").encode("utf-8"))
                    answer = tty.readline().decode("utf-8").strip()
            except OSError:
                answer = input(message + " [y/N] ")
        return answer.lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".mactools-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
            os.fchmod(file.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2).encode())


def within(path, root):
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def brew_path():
    import shutil
    found = shutil.which("brew")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise ValueError(text("未找到 Homebrew，请先安装：https://brew.sh", "Homebrew not found. Install it from https://brew.sh"))


def brew_env():
    env = dict(os.environ)
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
    env.setdefault("HOMEBREW_NO_ANALYTICS", "1")
    return env


def cache_dir():
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / "Library/Caches"))) / "mactools"


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "mactools"


def state_dir():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / "Library/Application Support"))) / "mactools"
