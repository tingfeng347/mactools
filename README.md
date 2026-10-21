<p align="center">
  <img src="docs/images/logo.svg" width="96" height="96" alt="mactools logo">
</p>

<h1 align="center">mactools</h1>

<p align="center">
  <strong>macOS 终端工具集 — 软件管理、应用卸载、空间清理与系统优化</strong>
</p>

<p align="center">
  <a href="https://github.com/tingfeng347/mactools/actions/workflows/ci.yml"><img src="https://github.com/tingfeng347/mactools/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/tingfeng347/mactools"><img src="https://img.shields.io/badge/macOS-Intel%20%7C%20Apple%20Silicon-111827" alt="macOS: Intel and Apple Silicon"></a>
  <a href="https://brew.sh/"><img src="https://img.shields.io/badge/Homebrew-supported-FBB040?logo=homebrew&amp;logoColor=white" alt="Homebrew supported"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.9+"></a>
  <a href="https://www.gnu.org/software/bash/"><img src="https://img.shields.io/badge/Bash-3.2%2B-4EAA25?logo=gnubash&amp;logoColor=white" alt="Bash 3.2+"></a>
</p>

<p align="center">
  <strong>简体中文</strong> · <a href="README.en.md">English</a>
</p>

使用 `ma + 动作` 和 `maci` 等快捷命令，统一管理 Homebrew、本机应用与残留、空间清理、系统优化、镜像源和 AI 编程工具。

```bash
ma install firefox
maci firefox                 # 与 ma install 相同
macr                         # 应用及残留卸载
macc                         # 空间清理
maco                         # 系统优化
ma ai install --all --dry-run
```

![maci 搜索 fish，显示已安装版本和软件详情](docs/images/install-status.png)

## 安装与卸载

Homebrew：

```bash
brew install tingfeng347/tap/mactools
brew uninstall mactools
```

脚本安装（部署到 `~/.local/bin`，不需要 sudo）：

```bash
git clone https://github.com/tingfeng347/mactools.git
cd mactools && bash install.sh
bash uninstall.sh
```

## 命令

| 功能 | 完整命令 | 缩写 |
|---|---|---|
| 搜索并安装软件 | `ma install` | `maci` |
| 卸载软件、应用及其残留 | `ma remove` | `macr` |
| 查看并更新软件 | `ma update` | `macu` |
| 环境、缓存和连通性检查 | `ma doctor` | `macd` |
| 预览并清理空间 | `ma clean` | `macc` |
| 刷新缓存与服务、系统优化 | `ma optimize` | `maco` |
| 镜像源测速、切换和恢复 | `ma mirror` | `macm` |
| AI 编程工具管理 | `ma ai` | `maca` |

运行 `ma --help` 或 `ma help <命令>` 查看选项。

## 文档

- [使用说明](docs/USAGE.md)：安装细节、搜索与选择、卸载与残留、清理范围、系统优化、镜像源、AI 工具、Shell 补全
- [功能规格](docs/SPEC.md)
- [Mole 接入说明](docs/MOLE.md)
- [审查报告](docs/REVIEW.md)

## 许可

[GPL-3.0](LICENSE)。清理、应用卸载与系统优化复用 [Mole](https://github.com/tw93/Mole)，上游归属见 [NOTICE](NOTICE.md)。
