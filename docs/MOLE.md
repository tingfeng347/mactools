# Mole 清理源码接入

上游：https://github.com/tw93/Mole

固定提交：`7b2a196061e1be7fae4ba422cd9102313fcb321d`。
接入日期：2026-10-20。许可证：GPL-3.0，完整文本见仓库根目录及
`third_party/mole/LICENSE`。原作者为 tw93 与 Mole contributors。
源文件修改记录见 `third_party/mole/NOTICE.md`。

## 源码和安装

`third_party/mole/` 保留 clean、purge、installer 入口与共享 lib 源码，
直接调用上游函数，未将其改写成另一份路径列表。`mactools-bridge.sh`
负责类别分派、权限范围与预览授权；`bin/mactools_mole.py` 负责加载和进程生命周期。
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

原有 Homebrew、pip、npm、pnpm、uv、Go、ccache、DerivedData 入口保留原生计划实现，
不调用上游整套 clean_developer_tools，因此不会顺带执行额外 Homebrew 清理。
Mole 类别可有重叠；前一项已删掉的路径不会再次删除。

## 保护与差异

菜单仍沿用 maci 的 fzf 布局；只为选中类别加载上游。详情预览使用独立临时账本，
确认前会再次生成本次执行计划。`--list` 只列类别，不启动引擎扫描。
确认后重新扫描并检查路径身份、白名单及上游活动保护；不接受新增或被替换的目标。
目录授权覆盖该目录内容，不是每个子文件的永久快照；正在写入的数据仍依赖上游活动检查。
子进程退出、超时或中断时，适配器回收整个进程组，避免残留清理进程。

以下是有意收窄的范围，不能将“包含源码”理解为执行 Mole 的所有系统维护功能：

- 不获取或复用 sudo，拒绝 home 之外的删除与管理命令。
- 不清空废纸篓、最近文档、邮件下载、应用保存状态；不删除签名应用的旧版本。
- 不接入 orphaned app data、system services、系统快照、系统更新、磁盘维护或 Nix 系统 store。
- 不接入 simctl 的批量直接删除；仅保留经过清理入口保护的 XCTest 设备与文档缓存。
- 不清理源码、应用账户和会话、Xcode Archives、虚拟机磁盘及受保护模型目录。
- project-artifacts 单独列在菜单，可移除闲置项目的依赖目录；Git 文件、嵌套仓库、
  活跃/近期项目和无法确认状态的项目按上游规则保留。无人值守执行跳过云同步产物。
- 安装包扫描限定在 home 的上游目录列表；不扫描 `/Users/Shared`。普通 ZIP 不视为安装包。

状态位置：`~/.config/mactools/mole/`、`~/.cache/mactools/mole/` 与
`~/Library/Logs/mactools/mole/`。白名单位于第一个目录中的 `whitelist`，
按上游格式每行一个路径。内置保护始终保留，自定义文件替换上游可选默认保护。
预览可能创建本工具的日志、扫描配置及临时文件，不会删除目标。

集成测试在临时 HOME 中验证所有类别可预览，并针对实际临时缓存验证确认后删除、
白名单、身份替换、符号链接、未预览的新目标、普通压缩包保留与日志。
这不等于对每个第三方应用版本完成了实际清理验证；上游规则会随应用变化，
更新 vendored 源码时应复查映射和删除入口并重跑隔离测试。
