# DEC-076 · 打包自带固定解释器（PyInstaller / py2app / AppImage）

日期：2026-11-11（重编号：main 已占用 DEC-075）
状态：采纳
关联：QUOT-525、QUOT-524（bundle 相对路径）、QUOT-523（supervisor）、QUOT-522（页面自启）

## 背景

交付物要求「双击即跑，不依赖系统 Python / venv」。源码态所有目录都从
`__file__` 上溯，打包后模块落在临时解包目录，原有写法会指到不存在的路径；
多进程入口还依赖 `-m ladderstock.cli.main`，冻结态不成立。

## 决策

1. **Windows 先行用 PyInstaller onedir**：解释器与依赖随包，启动快、排障直观
   （`_internal/` 可见）；onefile 仅作为可选布局兼容。
2. **macOS 用 py2app 产出 `.app`**：`Contents/MacOS` 放二进制，随包资源放
   `Contents/Resources`，与 Finder 双击习惯一致。
3. **Linux 用 PyInstaller onedir + AppImage 封装**：AppImage 作为单文件交付，
   内部仍是 onedir，`install_root` 取原始 `.AppImage` 文件所在目录。
4. **路径收口到 `core.runtime`**：`bundle_root` / `install_root` / `app_dir` /
   `project_root` 四个概念，其余模块只许调用；`LDST_INSTALL_ROOT` 为唯一显式覆盖。
5. **exe 即 CLI**：冻结态子进程命令为裸 exe + 隐藏子命令，不再追加 `-m`；
   新增 `__runtime-info` 供机器可读验真。
6. **资源清单单一来源**：`packaging/bundle_manifest.py` 同时供 spec、py2app
   setup 与单测使用，目标路径必须相对；缺 `frontend/dist` 在构建期失败。
7. **用户数据仍走 platformdirs**（DEC-071）；冻结态禁用 cwd 兜底，避免把安装
   目录当用户配置。

## 权衡

- onedir 产物是目录（非单文件），但启动更快、DLL 冲突更少；单文件用 onefile 兼容。
- 未引入 Nuitka / Briefcase：PyInstaller 生态成熟、三平台脚本可共用一份 spec。
- 不做签名/公证：需要证书与外部账号，超出本任务权限；已在规格中记为已知限制。
- 打包机用系统 Python 现场构建（CI 用 setup-python），不在仓库提交解释器二进制。

## 影响

- 任何新增随包资源必须登记到 `RESOURCE_DIRS`，否则运行时会找不到。
- 新增依赖若为动态导入，需补进 `HIDDEN_IMPORTS`。
- 子进程入口改动必须同时验证源码态与冻结态（单测 `test_frozen_runtime.py`）。
