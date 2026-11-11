# QUOT-527：跨平台服务抽象（Win 任务/托盘、macOS launchd、Linux systemd user）

状态：待验收（PR + CI 证据见 PR 评论）
范围：把「安装为服务 / 开机自启 / 托盘退出全停」从 Windows-only 计划任务
假设收敛为三平台原生机制；**不在本机真实安装任何 OS 服务**。

## 交付物

| 类型 | 路径 |
|---|---|
| 数据模型 / 校验 | `src/ladderstock/service/model.py` |
| 三平台后端 | `src/ladderstock/service/backends.py` |
| 安装/卸载/状态编排 | `src/ladderstock/service/manager.py` |
| 默认服务定义 | `src/ladderstock/service/spec.py` |
| 托盘抽象与退出契约 | `src/ladderstock/service/tray.py` |
| CLI | `src/ladderstock/cli/service.py`、`src/ladderstock/cli/main.py`（`__service`） |
| supervisor 退出全停 | `src/ladderstock/supervisor/engine.py`（`stop_owned`）、`src/ladderstock/cli/supervisor.py`（`--owned-only`） |
| 看板关停钩子 | `src/ladderstock/web/supervisor_api.py`、`src/ladderstock/web/server.py`、`src/ladderstock/cli/run.py` |
| 设计文档 | `docs/supervisor/cross-platform-service.md` |
| 决策 | `docs/decisions/DEC-077-cross-platform-service-autostart.md` |
| CLI 口径 | `docs/CLI.md` §P |

## 验收要点

- **三平台均可安装为服务/开机自启**：`service render` 生成 Task Scheduler XML
  （UTF-16）、launchd plist、systemd --user unit；`service install --apply` 在
  当前平台执行原生注册命令，跨平台 apply 被闸门拒绝。
- **托盘退出全停（只停自有）**：`TrayController.quit()` → `stop_owned()`，只停
  `origin="spawned"` 模块；adopted peer 保留并在 `kept_adopted` 回报。服务入口
  `__service` 内托盘退出后优雅关闭 uvicorn；看板关停钩子同口径。
- **不依赖散落计划任务**：注册单元名固定为本应用专属，安装/卸载只作用于这一条；
  dry-run 默认，失败关闭，反注册失败不删产物。
- **不碰生产**：本单所有验证使用临时数据目录 + 注入假执行器；未调用
  `schtasks /Create` / `launchctl` / `systemctl` 的真实写路径。

## 验证命令

```bash
python -m pytest tests/unit/service tests/unit/supervisor/test_stop_owned.py \
  tests/unit/cli/test_service.py tests/unit/cli/test_supervisor.py \
  tests/unit/test_cli_output_option.py tests/unit/test_cli_surface.py \
  tests/compat/test_legacy_entry.py tests/unit/web/test_supervisor_process_endpoints.py
python -m ruff check .
python -m mypy
```

## 已知后续

- 托盘图标资源 / 打包器 icon 集成属于打包单；`pystray` 保持可选 extra。
- Linux 无登录会话需 `loginctl enable-linger`（宿主配置，未自动修改）。
- Windows 旧 S4U 任务定义保留用于回滚；新装机不再走旧脚本。
