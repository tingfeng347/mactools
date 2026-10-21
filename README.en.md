<p align="center">
  <img src="docs/images/logo.svg" width="96" height="96" alt="mactools logo">
</p>

<h1 align="center">mactools</h1>

<p align="center">
  <strong>A macOS terminal toolkit — packages, app removal, cleanup, and system optimization</strong>
</p>

<p align="center">
  <a href="https://github.com/tingfeng347/mactools/actions/workflows/ci.yml"><img src="https://github.com/tingfeng347/mactools/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/tingfeng347/mactools"><img src="https://img.shields.io/badge/macOS-Intel%20%7C%20Apple%20Silicon-111827" alt="macOS: Intel and Apple Silicon"></a>
  <a href="https://brew.sh/"><img src="https://img.shields.io/badge/Homebrew-supported-FBB040?logo=homebrew&amp;logoColor=white" alt="Homebrew supported"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.9+"></a>
  <a href="https://www.gnu.org/software/bash/"><img src="https://img.shields.io/badge/Bash-3.2%2B-4EAA25?logo=gnubash&amp;logoColor=white" alt="Bash 3.2+"></a>
</p>

<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

Use `ma` with an action, or shortcuts such as `maci`, to manage Homebrew packages, native apps and leftovers, disk cleanup, system optimization, mirrors, and AI coding tools.

```bash
ma install firefox
maci firefox                 # Same as ma install
macr                         # Remove apps and leftovers
macc                         # Clean up disk space
maco                         # Optimize the system
ma ai install --all --dry-run
```

![maci searching for fish with installed versions and package details](docs/images/install-status.png)

## Install and uninstall

Homebrew:

```bash
brew install tingfeng347/tap/mactools
brew uninstall mactools
```

From a checkout (installs to `~/.local/bin`, no sudo):

```bash
git clone https://github.com/tingfeng347/mactools.git
cd mactools && bash install.sh
bash uninstall.sh
```

## Commands

| Task | Command | Shortcut |
|---|---|---|
| Search and install packages | `ma install` | `maci` |
| Remove packages, apps, and leftovers | `ma remove` | `macr` |
| List and install updates | `ma update` | `macu` |
| Check environment, caches, and connectivity | `ma doctor` | `macd` |
| Preview and reclaim space | `ma clean` | `macc` |
| Refresh caches and services, system optimization | `ma optimize` | `maco` |
| Benchmark, switch, and restore mirrors | `ma mirror` | `macm` |
| Manage AI coding tools | `ma ai` | `maca` |

Run `ma --help` or `ma help <command>` for options.

## Documentation

- [Usage guide](docs/USAGE.en.md): install details, search and selection, removal and leftovers, cleanup scopes, optimization, mirrors, AI tools, shell completion
- [Functional specification](docs/SPEC.md)
- [Mole integration notes](docs/MOLE.md)
- [Review report](docs/REVIEW.md)

## License

[GPL-3.0](LICENSE). Cleanup, app removal, and optimization reuse [Mole](https://github.com/tw93/Mole); see [NOTICE](NOTICE.md).
