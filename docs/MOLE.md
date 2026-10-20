# Mole 清理与应用卸载源码接入

上游：https://github.com/tw93/Mole

固定提交：`7b2a196061e1be7fae4ba422cd9102313fcb321d`。
接入日期：2026-10-20。许可证：GPL-3.0，完整文本见仓库根目录及
`third_party/mole/LICENSE`。原作者为 tw93 与 Mole contributors。
源文件修改记录见 `third_party/mole/NOTICE.md`。

## 源码和安装

`third_party/mole/` 保留 clean、purge、installer、uninstall 入口与共享 lib 源码，
直接调用上游函数，未将其改写成另一份路径列表。`mactools-bridge.sh`
负责类别分派、权限范围与预览授权；`bin/mactools_mole.py` 负责加载和进程生命周期。
`mactools-apps.sh` 与 `bin/mactools_apps.py` 提供本机应用识别、残留预览及卸载。
`mactools-optimize.sh` 与 `bin/mactools_optimize.py` 接入优化任务。
这不需要安装、更新或调用系统中的 `mo`。

`bin/mactools_mole.zip` 是上述目录的可读源码包，包含 GPL 文本与归属说明。
脚本安装和 Homebrew 安装都携带它，执行前核验内置 SHA-256，解压至私有临时目录。
安装脚本在替换现有文件前验证完整源码包，并与其他命令一起原子回滚。
卸载只移除分发文件，保留保护规则和历史记录。

修改上游源码或适配器后执行：

```sh
python3 scripts/build_mole_bundle.py
python3 -m unittest discover -s tests -v
```

脚本按固定顺序、时间戳和权限构建包并同步校验值；测试核对包内文件与源码逐字节一致。

## 执行映射

| ma clean 类别 | 复用的 Mole 函数或模块 |
| --- | --- |
| user-cache | clean_user_essentials |
| app-cache | clean_app_caches |
| browser-cache | clean_browsers |
| cloud-cache / office-cache | clean_cloud_storage / clean_office_applications |
| app-tools / vm-cache / app-logs | clean_user_gui_applications / clean_virtualization_tools / clean_application_support_logs |
| rust-cache / jvm-cache / frontend-cache / ruby-cache | clean_dev_rust / clean_dev_jvm / clean_dev_frontend / clean_dev_ruby + clean_dev_perl |
| cloud-cli-cache | clean_dev_cloud |
| ai-cache | clean_dev_ai_agents + clean_dev_misc + clean_dev_automation_browsers |
| language-cache | clean_dev_python + clean_dev_mise + clean_dev_other_langs + clean_dev_elixir + clean_dev_ocaml |
| build-tools-cache | clean_dev_docker + clean_dev_cicd + clean_dev_database + clean_dev_api_tools + clean_dev_shell + clean_dev_network |
| editor-cache | clean_code_editors + clean_dev_jetbrains_logs |
| xcode-extra | clean_xcode_documentation_cache + clean_xcode_xctest_devices |
| project-cache | clean_project_caches，使用上游扫描和 Git 保护 |
| project-artifacts | clean_project_artifacts，使用上游闲置、内容、活动与删除前复查 |
| installers | collect_installers + build_installer_delete_plan + execute_installer_delete_plan |
| trash / recent-items / mail-downloads / incomplete-downloads | clean_trash / _clean_recent_items / _clean_mail_downloads / _clean_incomplete_downloads |
| saved-state | opt_saved_state_cleanup |
| old-app-versions | Chrome / Edge / Brave / Autodesk Fusion 的旧版本清理函数 |
| orphan-app-data | clean_orphaned_app_data + clean_orphaned_container_stubs |
| orphan-services / system-cache | clean_orphaned_system_services / clean_deep_system |
| finder-metadata / group-cache / silicon-cache | clean_finder_metadata / clean_group_container_caches / clean_apple_silicon_caches |
| device-firmware / failed-backups | clean_cached_device_firmware / clean_time_machine_failed_backups |
| mobile-cache / toolbox-cache | clean_dev_mobile + clean_xcode_tools / clean_dev_jetbrains_toolbox |
| nix-garbage | clean_dev_nix，通过 nix-store 查询并仅删除已预览的无引用路径 |
| local-snapshots | clean_local_snapshots，仅提供上游检查与建议 |
| system-updates | 保留上游不删除更新暂存的策略，仅查看目录大小 |
| large-files | check_large_file_candidates，仅列建议 |
| periodic-maintenance / disk-verify | opt_periodic_maintenance / opt_disk_verify，磁盘校验需显式启用 |

原有 Homebrew、pip、npm、pnpm、uv、Go、ccache、DerivedData 入口保留原生计划实现，
不调用上游整套 clean_developer_tools，因此不会顺带执行额外 Homebrew 清理。
Mole 类别可有重叠；前一项已删掉的路径不会再次删除。

## 保护与差异

菜单仍沿用 maci 的 fzf 布局；只为选中类别加载上游。详情预览使用独立临时账本，
确认前会再次生成本次执行计划。`--list` 只列类别，不启动引擎扫描。
确认后重新扫描并检查路径身份、白名单及上游活动保护；不接受新增或被替换的目标。
目录授权覆盖该目录内容，不是每个子文件的永久快照；正在写入的数据仍依赖上游活动检查。
子进程退出、超时或中断时，适配器回收整个进程组，避免残留清理进程。

`ma clean` 仅运行所选清理类别；系统优化由独立的 `ma optimize` / `maco` 入口选择：

- 普通缓存类别禁用顺带清空废纸篓、邮件下载、最近记录及旧应用版本；这些功能单独列为可选类别。
- 系统缓存、失效服务及其他需要管理员权限的任务，仅在预览与确认后按需授权。
  预览不调用 sudo；无权读取的目录保持不可预览，不会在授权后追加为可删除目标。
- 最近记录与 Finder 元数据保留 Mole 的强制保护；相关菜单标为检查，受保护的记录及 `.DS_Store` 不删除。
- 系统快照由 Time Machine 管理、更新暂存由 Software Update 管理，所复用版本不自动删除它们。
  大文件也只列建议；磁盘校验不执行修复，且需要 `MOLE_ENABLE_DISK_VERIFY=1`。
- 不清理源代码、Xcode Archives、虚拟机磁盘及受保护模型目录。专门的保存状态、
  邮件下载、最近记录和应用残留类别会清理其描述中的数据，并在确认前展示范围。
- project-artifacts 可移除闲置项目的依赖目录；Git 文件、嵌套仓库、活跃/近期项目和无法确认
  状态的项目按上游规则保留。无人值守执行跳过云同步产物。
- 安装包扫描沿用上游用户目录和 `/Users/Shared`，仍受身份与路径保护；普通 ZIP 不视为安装包。
- Nix 重新查询无引用路径，只把已预览并保持身份的路径交给原生删除接口；
  无效模拟器逐个核对设备目录，只处理预览时已记录的实例。
- 失败备份沿用上游直接调用 `tmutil delete` 的行为；权限不足时保留并报告，不自动提权重试。

## macr 应用及残留卸载

默认 `macr` 列出 Mole 允许卸载的本机应用，以黄色 `app` 排在 Homebrew 行前面；
类型与名称仍使用原有对齐方式，名称为白色。`macr --apps` 只列本机应用，
`--formula` / `--cask` 单独使用只列 Homebrew；组合 `--apps --cask` 可同时选择。
列表仅发现应用，不在打开界面时扫描所有残留；缓存刷新和选择后核验沿用包管理界面。

卸载复用 Mole 的 `_batch_scan_app_details`、`_uninstall_prepare_data_review`、
`_batch_execute_removals` 与删除保护。确认前显示应用及残留完整路径，默认保留个人偏好
和支持数据；`--app-data` 显式纳入这些数据。系统残留中的仅供审查项会保留。
本机文件移到废纸篓；Homebrew 管理的应用使用其所属 Homebrew 的完整 cask 名卸载，
不传 `--zap`。同时选中应用及同一 cask 时去重，不按软件短名跨 tap 或 Homebrew 前缀合并。
Homebrew 来源查询失败或声明冲突时不自动卸载；没有 cask 归属的本机应用仍可卸载。
应用发现沿用 Mole 的标准安装目录，非标准位置的应用可能不会列出。卸载前再次核对目标身份；运行中应用与权限需求
在预览中说明，确认后才退出应用或申请权限。

## ma optimize / maco

`optimize_info()` 的 19 个任务 ID 与固定上游 `lib/optimize/catalog.sh` 一一对应，
以连字符代替下划线。通过 `execute_optimization` 调用对应处理函数，保留
上游条件检查、白名单、结果分类、VPN 与运行中应用保护。打开菜单仅生成元数据；
选中后运行上游 dry-run 并展示权限、持久偏好、历史记录和服务刷新影响，再确认执行。
`--list` 不扫描；`--dry-run` 不执行优化；非交互默认只预览，`--yes` 需要明确指定任务名。

优化确认针对所选任务与说明的操作范围，数据库整理及系统命令按执行时的上游检测结果处理；
不将它们声称为逐行数据库快照。文件删除另有预览路径身份核验。
管理员权限由所选任务实际需要的受限命令触发；磁盘校验仍需 `MOLE_ENABLE_DISK_VERIFY=1`，
不执行修复。登录项与失效启动代理只报告；维护脚本在不支持的 macOS 版本跳过。
具体任务及影响见中英文 README，详细诊断沿用上游原文。

状态位置：`~/.config/mactools/mole/`、`~/.cache/mactools/mole/` 与
`~/Library/Logs/mactools/mole/`。白名单位于第一个目录中的 `whitelist`，
按上游格式每行一个路径。内置保护始终保留，自定义文件替换上游可选默认保护。
预览可能创建本工具的日志、扫描配置及临时文件，不会删除目标。

集成测试在临时 HOME 中验证所有类别可预览，并针对实际临时缓存验证确认后删除、
白名单、身份替换、符号链接、未预览的新目标、普通压缩包保留与日志。
应用卸载测试也只使用临时应用与替身工具，不卸载本机软件。
这不等于对每个第三方应用版本完成了实际清理或卸载验证；上游规则会随应用变化，
更新 vendored 源码时应复查映射和删除入口并重跑隔离测试。
