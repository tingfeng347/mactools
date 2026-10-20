#!/usr/bin/env python3
"""Unified macOS command entry and shell completions."""

import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

# The launcher may be invoked through a symlink or from a directory off PATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mactools_runtime import atomic_write, config_dir, text

VERSION = "0.1.14"
COMMANDS = {
    "install": "maci", "remove": "macr", "update": "macu", "doctor": "macd",
    "clean": "macc", "optimize": "maco", "mirror": "macm", "ai": "maca",
}


def language():
    value = os.environ.get("MACTOOLS_LANG")
    if value not in {"zh", "en"}:
        try:
            value = (config_dir() / "language").read_text().strip()
        except OSError:
            value = "zh"
    os.environ["MACTOOLS_LANG"] = value if value in {"zh", "en"} else "zh"


def help_text():
    output = text("""用法：ma <命令> [选项]

  install   搜索并安装 Formula / Cask 软件 (maci)
  remove    卸载软件、应用及其残留 (macr)
  update    查看并更新软件 (macu)
  doctor    环境、缓存与连通性检查 (macd)
  clean     清理缓存、旧版本与无用依赖 (macc)
  optimize  刷新系统缓存与服务，检查系统状态 (maco)
  mirror    镜像测速、切换与恢复 (macm)
  ai        AI 编程工具管理 (maca)
  setup     设置语言：ma setup zh|en
  completion  输出 bash|zsh|fish 补全

示例：ma install firefox；maci --formula wget；ma ai install --all --dry-run
使用 ma <命令> --help 查看帮助。""", """Usage: ma <command> [options]

  install   Search and install Formula / Cask packages (maci)
  remove    Remove packages, applications and leftovers (macr)
  update    Review and apply package updates (macu)
  doctor    Check environment, caches and connectivity (macd)
  clean     Clean caches, old versions and unused dependencies (macc)
  optimize  Refresh system caches and services, review system health (maco)
  mirror    Benchmark, switch and restore mirrors (macm)
  ai        Manage AI coding tools (maca)
  setup     Set language: ma setup zh|en
  completion  Print bash|zsh|fish completion

Examples: ma install firefox; maci --formula wget; ma ai install --all --dry-run
Run ma <command> --help for command help.""")
    if not sys.stdout.isatty() or os.environ.get("TERM") == "dumb" or os.environ.get("NO_COLOR"):
        return output

    def paint(value, code):
        return f"\033[{code}m{value}\033[0m"

    lines = output.splitlines()
    for index, line in enumerate(lines):
        if index == 0:
            lines[index] = paint(line, "1;36")
        elif line.startswith("  "):
            line = re.sub(r"^(  )(\w+)", lambda m: m[1] + paint(m[2], "1;32"), line)
            lines[index] = re.sub(r"\((mac\w+)\)", lambda m: "(" + paint(m[1], "33") + ")", line)
        elif line.startswith(("示例：", "Examples:")):
            lines[index] = paint(line, "36")
        elif line:
            lines[index] = paint(line, "2")
    return "\n".join(lines)


def completion(shell):
    # All candidates are constants: no subprocess/network/package queries during completion.
    actions = " ".join(COMMANDS) + " setup completion help"
    from mactools_mole import mole_info
    from mactools_optimize import optimize_info
    optimize_actions = ' '.join(optimize_info())
    clean_actions = ('formula-cache cask-cache autoremove brew-cache pip-cache npm-cache '
                     'pnpm-store uv-cache go-cache ccache xcode-derived ' + ' '.join(mole_info()))
    if shell == "bash":
        print('''_mactools_complete() {
    local words cur action
    cur="${COMP_WORDS[COMP_CWORD]}"
    action="${COMP_WORDS[1]:-}"
    case "${COMP_WORDS[0]##*/}" in
        maci) action=install;; macr) action=remove;; macu) action=update;;
        macd) action=doctor;; macc) action=clean;; maco) action=optimize;; macm) action=mirror;; maca) action=ai;;
    esac
    words="--help"
    case "$action" in
        install|remove|update) words="--formula --brew --cask --exact --refresh --list --dry-run --yes --timeout --help";;
        doctor) words="--formula --brew --cask --offline --timeout --help";;
        clean) words="''' + clean_actions + ''' --formula --brew --cask --dev --exact --list --dry-run --yes --timeout --help";;
        optimize) words="''' + optimize_actions + ''' --list --dry-run --yes --timeout --help";;
        mirror) words="test auto restore --json --mirror --timeout --limit --dry-run --yes --force --config-dir --state-dir --help";;
        ai) words="install remove uninstall update status doctor codex claude opencode pi qodercn gemini qwen zcode --all --path --dry-run --yes --check --latest --offline --timeout --help";;
        setup) words="zh en --help";;
        completion) words="bash zsh fish";;
    esac
    if [[ "${COMP_WORDS[0]##*/}" == ma && $COMP_CWORD == 1 ]]; then
        words="''' + actions + ''' --help --version"
    fi
    [[ "$action" == update ]] && words="$words --refresh-index"
    [[ "$action" == remove ]] && words="$words --apps --app-data"
    COMPREPLY=( $(compgen -W "$words" -- "$cur") )
}
complete -F _mactools_complete ma maci macr macu macd macc maco macm maca''')
    elif shell == "zsh":
        print('''_mactools_complete() {
    local -a candidates
    case "${words[1]:t}:${words[2]}" in
        ma:setup) candidates=(zh en);;
        ma:completion) candidates=(bash zsh fish);;
        ma:ai|maca:*) candidates=(install remove uninstall update status doctor codex claude opencode pi qodercn gemini qwen zcode --all --path --dry-run --yes --check --latest --offline --timeout --help);;
        ma:mirror|macm:*) candidates=(test auto restore --json --mirror --timeout --limit --dry-run --yes --force --config-dir --state-dir --help);;
        ma:doctor|macd:*) candidates=(--formula --brew --cask --offline --timeout --help);;
        ma:clean|macc:*) candidates=(''' + clean_actions + ''' --formula --brew --cask --dev --exact --list --dry-run --yes --timeout --help);;
        ma:optimize|maco:*) candidates=(''' + optimize_actions + ''' --list --dry-run --yes --timeout --help);;
        *) candidates=(--formula --brew --cask --exact --refresh --list --dry-run --yes --timeout --help);;
    esac
    if [[ ${words[1]:t} == ma && CURRENT == 2 ]]; then
        candidates=(''' + actions + ''' --help --version)
    fi
    if [[ ${words[1]:t} == macu || ${words[2]} == update ]]; then candidates+=(--refresh-index); fi
    if [[ ${words[1]:t} == macr || ( ${words[1]:t} == ma && ${words[2]} == remove ) ]]; then candidates+=(--apps --app-data); fi
    compadd -- "${candidates[@]}"
}
compdef _mactools_complete ma maci macr macu macd macc maco macm maca''')
    elif shell == "fish":
        print('complete -c ma -f')
        print(f"complete -c ma -n '__fish_use_subcommand' -a '{actions}'")
        print("complete -c ma -n '__fish_seen_subcommand_from setup' -a 'zh en'")
        print("complete -c ma -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish'")
        options = {
            "install": "formula brew cask exact refresh list dry-run yes timeout help",
            "remove": "formula brew cask apps app-data exact refresh list dry-run yes timeout help",
            "update": "formula brew cask exact refresh refresh-index list dry-run yes timeout help",
            "doctor": "formula brew cask offline timeout help",
            "clean": "formula brew cask dev exact list dry-run yes timeout help",
            "optimize": "list dry-run yes timeout help",
            "mirror": "json mirror timeout limit dry-run yes force config-dir state-dir help",
            "ai": "all path dry-run yes check latest offline timeout help",
        }
        for action, alias in COMMANDS.items():
            print(f"complete -c {alias} -f")
            condition = f" -n '__fish_seen_subcommand_from {action}'"
            for option in options[action].split():
                print(f"complete -c ma{condition} -l {option}")
                print(f"complete -c {alias} -l {option}")
            choices = {"ai": "install remove uninstall update status doctor codex claude opencode pi qodercn gemini qwen zcode",
                       "mirror": "test auto restore", "clean": clean_actions,
                       "optimize": optimize_actions}.get(action)
            if choices:
                print(f"complete -c ma{condition} -a '{choices}'")
                print(f"complete -c {alias} -a '{choices}'")
    else:
        raise ValueError("Usage: ma completion bash|zsh|fish")


def main(argv=None):
    language()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv == ["--help"] or argv == ["-h"]:
        print(help_text())
        return 0
    if argv == ["--version"]:
        print(f"mactools {VERSION}")
        return 0
    if argv[0] == "help":
        if len(argv) > 2:
            raise ValueError("Usage: ma help [command]")
        return main([argv[1], "--help"] if len(argv) == 2 else [])
    action, args = argv[0], argv[1:]
    if action == "setup":
        if args in ([], ["--help"], ["-h"]):
            print("ma setup zh|en")
            return 0
        if len(args) != 1 or args[0] not in {"zh", "en"}:
            raise ValueError("Usage: ma setup zh|en")
        path = config_dir() / "language"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(path, (args[0] + "\n").encode())
        os.environ["MACTOOLS_LANG"] = args[0]
        print(text("界面语言已设置为简体中文。", "Language set to English."))
        return 0
    if action == "completion":
        if len(args) != 1:
            raise ValueError("Usage: ma completion bash|zsh|fish")
        completion(args[0])
        return 0
    if action not in COMMANDS:
        raise ValueError(text(f"未知命令：{action}。运行 ma --help。", f"Unknown command: {action}. Run ma --help."))
    if action in {"install", "remove", "update"}:
        module, args = "packages", [action, *args]
    elif action in {"doctor", "clean"}:
        module, args = "system", [action, *args]
    else:
        module = action
    return importlib.import_module("mactools_" + module).main(args) or 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(text("错误：", "Error: ") + str(error), file=sys.stderr)
        sys.exit(1)
