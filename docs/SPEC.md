# mactools 功能与开发规格

入口 ma，缩写 maci/macr/macu/macd/macc/maco/macm/maca/mato。maci = ma install。
Python 3.9+ 标准库，启动器兼容系统 Bash 3.2；支持 Intel 和 Apple Silicon 的 brew 路径。
包源为 Homebrew formula/cask，--brew 是 --formula 别名，不保留无效 apt/snap/flatpak 参数。
执行包管理器时不使用 sudo；默认不自动刷新索引，update --refresh-index 才显式 brew update。
只读/预览命令不修改包、缓存清理目标或镜像配置；--yes 仅跳过本工具确认。
卸载/清理不使用 --zap；应用个人数据仅由 macr --app-data 纳入预览，专门的数据清理类别按需选择。网络查询有超时，失败明确非零退出。
清理扩展复用 third_party/mole 源码（GPL-3.0），按菜单类别运行；管理员操作仅在已预览并确认执行后按需授权，检查类项目不自动删除。
project-artifacts 经预览确认可删除至少闲置 7 天且通过项目保护检查的项目依赖及构建产物。
Mole 清理与卸载只能处理当前计划已预览的目标；新目标或身份变化的目标跳过。规则与归属见 docs/MOLE.md。
镜像配置为专属 shell 片段，用户 source 后生效；备份、并发测速、回滚与精确恢复，不修改用户 shell 配置。
AI 工具管理识别 macOS 原生/Homebrew/npm/pnpm/Bun 来源，保留多副本和登录数据。
Homebrew 安装由 formula 管理；脚本安装/卸载默认 ~/.local/bin，支持 BIN_DIR/--bin-dir，完整暂存校验后部署，无需 sudo。

## 卸载残留

应用卸载先扫描并展示应用本体与残留。执行前由用户选择，或通过参数明确指定：

- `--clean-leftovers`：确认后清理预览中的残留。
- `--keep-leftovers`：只卸载应用，保留所有残留。
- 交互模式未指定时询问；默认保留。
- `--yes` 只跳过确认，不会替用户清理残留；脚本需要清理时必须显式传入 `--clean-leftovers`。
- `--app-data` 将个人数据纳入清理范围，与 `--keep-leftovers` 互斥。

个人偏好与支持数据默认保留，只有 `--app-data` 才会进入清理计划。

## 缓存刷新

maci、macu、macr 的列表快照只用于缩短首次显示时间。安装、卸载、更新实际执行后原地更新相关 selector 快照：
从卸载选择器中删除本次卸载的行，把本次安装/升级的版本写入对应行，其他行与原时间戳保持不变，下次打开继续命中缓存。
快照已过期、损坏或无法安全更新时才删除该文件并回退到实时读取；`--refresh-index` 成功刷新 Homebrew 索引后整体失效。缓存从不授权修改软件。
应用卸载（含 `--apps`）按应用 ID 更新本机应用快照，并按实际归属的 Homebrew 路径及 cask token 更新包快照，不依赖当前选中的 Homebrew。
卸载后的软件仍可出现在 `maci` 的可安装目录中，但版本显示为未安装、不带已安装勾号。
`macr` 识别 Homebrew 应用归属时，同时支持 `brew list --cask` 的普通路径输出与带文件数的显示格式；Caskroom 中的应用符号链接按解析后的真实路径匹配，卸载由归属的 Homebrew 执行以清除安装记录。

## 开发契约

包选择器的名称列填充宽度最多 40 个终端字符，避免宽窗口中名称与版本相距过远；保留完整名称用于搜索。
名称、已安装版本及可更新版本的表头与内容统一左对齐；绿色勾号紧跟版本号，末列不填充空格，更新列表仍对齐可更新版本。
包选择器的右侧详情面板占宽度 30%；不足 30 字符时才移到底部显示。

bin/ma -> python3 bin/mactools.py。mactools.py 设置 MACTOOLS_LANG 并按功能导入模块。
包功能模块 bin/mactools_packages.py: main(argv)，argv 包含 action install/remove/update。
本机应用模块 bin/mactools_apps.py: installed_apps/resolve_apps/AppUninstallPlan，复用 Mole 卸载源码。
macr 默认将黄色 app 排在 Homebrew cask/formula 前，明确类型选项可限制来源。cask artifact 的绝对 app target 与已发现应用路径完全一致时，只保留 app 行（支持残留选择），隐藏对应 cask 行，并把该 token 作为 app 行的灰色搜索别名保留，输入 cask 名仍可命中；仅名称相同但路径不同或归属有歧义时不去重。
维护模块 bin/mactools_system.py: main(argv)，argv 包含 action doctor/clean。
优化模块 bin/mactools_optimize.py: main(argv)，argv 不包含 optimize；复用 Mole 19 项任务，先预览、确认后仅运行所选任务。
镜像模块 bin/mactools_mirror.py: main(argv)，argv 不包含 mirror。
AI 模块 bin/mactools_ai.py: main(argv)，argv 不包含 ai。
运行时 bin/mactools_runtime.py 提供 brew_path()/brew_env()/cache_dir()/config_dir()/state_dir()。
main 返回退出状态整数；入口统一捕获 OSError/ValueError/TimeoutExpired/KeyboardInterrupt。
每个功能模块只修改自身范围及独有测试文件；禁止真实安装/卸载机器软件或切源。

## 实时资源监控

`ma top` / `mato` 由 `mactools_top.py` 调度，`mactools_top_native.py` 采集，`mactools_top_ui.py` 绘制。具体行为、指标口径和性能证据维护在 [TOP.md](TOP.md)。使用 Python 标准库，macmon 为可选外部采集器；不自动安装依赖或提升权限。
