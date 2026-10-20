# mactools 移植规格

来源：https://github.com/tingfeng347/ubtools ，移植保留原有功能和中英文、fzf 多选、确认、dry-run、缓存、补全。
入口 ma，缩写 maci/macr/macu/macd/macc/maco/macm/maca。maci = ma install。
Python 3.9+ 标准库，启动器兼容系统 Bash 3.2；支持 Intel 和 Apple Silicon 的 brew 路径。
包源为 Homebrew formula/cask，--brew 是 --formula 别名，不保留无效 apt/snap/flatpak 参数。
执行包管理器时不使用 sudo；默认不自动刷新索引，update --refresh-index 才显式 brew update。
只读/预览命令不修改包、缓存清理目标或镜像配置；--yes 仅跳过本工具确认。
卸载/清理不使用 --zap；应用个人数据仅由 macr --app-data 纳入预览，专门的数据清理类别按需选择。网络查询有超时，失败明确非零退出。
清理扩展复用 third_party/mole 源码（GPL-3.0），按菜单类别运行；管理员操作仅在已预览并确认执行后按需授权，检查类项目不自动删除。
project-artifacts 经预览确认可删除至少闲置 7 天且通过上游保护检查的项目依赖及构建产物。
Mole 清理与卸载只能处理当前计划已预览的目标；新目标或身份变化的目标跳过。规则与归属见 docs/MOLE.md。
镜像配置为专属 shell 片段，用户 source 后生效；备份、并发测速、回滚与精确恢复，不修改用户 shell 配置。
AI 复用上游管理逻辑，macOS 原生/Homebrew/npm/pnpm/Bun 来源识别，保留多副本和登录数据。
安装/卸载默认 ~/.local/bin，支持 BIN_DIR/--bin-dir，完整暂存校验后部署，无需 sudo。

## 开发契约

bin/ma -> python3 bin/mactools.py。mactools.py 设置 MACTOOLS_LANG 并按功能导入模块。
包功能模块 bin/mactools_packages.py: main(argv)，argv 包含 action install/remove/update。
本机应用模块 bin/mactools_apps.py: installed_apps/resolve_apps/AppUninstallPlan，复用 Mole 卸载源码。
macr 默认将黄色 app 排在 Homebrew cask/formula 前，明确类型选项可限制来源，应用与 cask 去重。
维护模块 bin/mactools_system.py: main(argv)，argv 包含 action doctor/clean。
优化模块 bin/mactools_optimize.py: main(argv)，argv 不包含 optimize；复用 Mole 19 项任务，先预览、确认后仅运行所选任务。
镜像模块 bin/mactools_mirror.py: main(argv)，argv 不包含 mirror。
AI 模块 bin/mactools_ai.py: main(argv)，argv 不包含 ai。
运行时 bin/mactools_runtime.py 复用上游接口，新增 brew_path()/brew_env()/cache_dir()/config_dir()/state_dir()。
main 返回退出状态整数；入口统一捕获 OSError/ValueError/TimeoutExpired/KeyboardInterrupt。
每分支提交实现及隔离测试，只修改所负责模块和独有测试文件，禁止真实安装/卸载机器软件或切源。

## 并行任务

- feat/packages：Homebrew 目录/缓存、fzf 安装卸载更新、隔离包管理器测试。
- feat/system：诊断/清理、镜像瓶校验/备份/回滚/恢复、本地 HTTP 测试。
- feat/ai：上游 AI 管理复用、macOS 多 Homebrew 前缀识别、伪终端测试。
- main：ma/八个缩写、语言设置、三种 shell 补全、安装卸载、文档与 CI。
- 独立 review/merge agent：核对本规格与上游、审查各功能分支、合并并完整验证。

参考源码和并行工作目录保存在项目自身 .reference/ubtools 和 .worktrees 下。
