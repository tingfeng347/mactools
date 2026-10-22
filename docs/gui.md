# mactools 桌面版

桌面版使用 Rust + Tauri 2 构建，界面与 CLI 共用同一套实现：所有安装、卸载、更新、清理和优化动作都由 `ma` 执行，GUI 负责展示状态、预览计划和收集输入。

- `main` 分支：仅 CLI。
- `dev` 分支：CLI + 桌面版，源码位于 [`gui/`](../gui)。

![mactools 桌面版概览](images/gui-overview.jpg)

## 前置条件

1. **Homebrew**：软件安装、更新与镜像功能依赖 Homebrew。尚未安装时先执行：

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **mactools CLI**：

   ```bash
   brew install tingfeng347/tap/mactools
   ```

3. **开发环境**：Node.js 20+、npm、Rust 1.77.2+、Xcode Command Line Tools。

   ```bash
   xcode-select --install
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

首次启动时，界面会检查 Homebrew 与 `ma` 命令。缺少 Homebrew 时提供安装文档入口；缺少 `ma` 时给出安装命令并可一键复制。

## 功能

| 页面 | 对应命令 | 说明 |
|---|---|---|
| 概览 | `ma --version`、`brew` | 系统状态、磁盘占用、快捷入口 |
| 安装 | `ma install` | 搜索 Formula / Cask，多选后精确安装 |
| 卸载 | `ma remove --apps` | 应用列表、图标、残留预览，保留或清理由用户选择 |
| 更新 | `ma update` | 检查可更新项，按项更新或更新全部 |
| 清理 | `ma clean` | 扫描并预览可清理的缓存与旧文件 |
| 优化 | `ma optimize` | 刷新缓存与服务，执行系统优化 |
| 镜像 | `ma mirror` | 测速、切换与恢复镜像源 |
| AI 工具 | `ma ai` | 安装、更新与移除 AI 编程工具 |
| 检查 | `ma doctor` | 环境、缓存与网络连通性诊断 |
| 终端 | PTY | 完整交互终端，可直接运行 `maci`、`macr`、`macc`、`maco` |

## 运行开发版

```bash
git clone https://github.com/tingfeng347/mactools.git
cd mactools
git checkout dev
cd gui
npm install
npm run tauri -- dev
```

开发构建会优先使用仓库中的 `bin/ma`，避免误用 Homebrew 里的旧版本。需要指定其他 CLI 时：

```bash
MACTOOLS_BIN=/path/to/ma npm run tauri -- dev
```

## 构建安装包

```bash
cd gui
npm run tauri -- build --bundles app   # .app
npm run tauri -- build --bundles dmg   # .dmg
```

产物位于：

```text
gui/src-tauri/target/release/bundle/macos/mactools.app
gui/src-tauri/target/release/bundle/dmg/
```

当前构建未签名、未公证。首次打开时如被 Gatekeeper 拦截，可在 Finder 中右键选择「打开」。

## 实现说明

- **直接参数调用**：Rust 后端只允许固定的 `ma` 子命令，使用 argv 传参，不经过 shell 拼接。
- **精确选择**：搜索走 `ma <action> --list-json`，安装、更新与卸载走 `--pick <source>:<name>`，不会把自由文本当作包名执行。
- **只读查询**：`--list-json` 不写入 selector、catalog 或 alias 缓存，`maci` / `macr` 仍读取实时状态。
- **完整终端**：内置 PTY 启动 `/bin/zsh -l -i`，通过 xterm.js 呈现，保留 TUI 的全部交互能力。
- **应用图标**：应用图标来自 `.app` 的 `Info.plist`，经 `sips` 转为 128px PNG 后缓存。

## 缓存与卸载

GUI 会把应用图标缓存到：

```text
~/Library/Caches/mactools/gui-icons
```

可随时删除，下次打开会重新生成。卸载 CLI：

```bash
brew uninstall mactools
```

桌面版本身可直接移到废纸篓；如需连同 CLI 缓存一起清理：

```bash
rm -rf "${XDG_CACHE_HOME:-$HOME/Library/Caches}/mactools"
```

## 故障排查

| 现象 | 处理 |
|---|---|
| 提示未找到 mactools | 先 `brew install tingfeng347/tap/mactools`，或设置 `MACTOOLS_BIN` |
| 提示未检测到 Homebrew | 先安装 Homebrew（见上文），然后重新打开应用 |
| 开发窗口空白 | 确认 1420 端口未被占用，重新执行 `npm run tauri -- dev` |
| 构建失败提示缺少 Rust | 安装 Rust 1.77.2+ 与 Xcode Command Line Tools |
| 应用被系统拦截 | Finder 中右键「打开」；正式分发需签名与公证 |
| DMG 打包报 AppleEvent 超时 | 允许终端控制 Finder，或以 `CI=true` 前缀跳过 Finder 美化 |
