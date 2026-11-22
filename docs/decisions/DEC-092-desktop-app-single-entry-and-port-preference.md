# DEC-092 桌面应用形态：双击启动全模块 + 端口偏好 + 应用内 MCP 面

日期：2026-11-23
状态：已实施

## 背景

此前装机形态有一个结构性断层：双击入口（`__desktop`）只起看板（附加模式），
子模块靠本机手工 / 计划任务另起；MCP 面（5951）是独立命令 `ldst mcp http`，
装机后没人帮用户跑；看板端口无法从界面修改。用户裁决（2026-11-23）：

1. 装机后**双击 exe 快捷方式**即可启动，Win / Mac 一致；
2. 启动后**子模块正常运行**，不是往某台机器的计划任务里塞；
3. 应用打开 `localhost:固定端口`；端口可在**关于页修改**，改完重启生效，
   **改之前要检测冲突**；
4. 应用是**单一个程序**：一个窗口（浏览器页面），不弹一堆终端。

## 决策

### A. 双击 = 全家桶（`__desktop` 接管子模块）

`__desktop` 与 `__service` 一样置 `LDST_SUPERVISOR_OWN_PROCESSES=1`：
子模块随应用冷启动、自愈每 60 秒一轮、应用退出时停自有子进程（adopted
peer 原样保留）。两个入口不再各自预检端口——`launch()` 统一处理
QUOT-594 避让语义（默认口被 foreign 占用先避让并持久化，+100 内无空闲才退出）。

### B. 应用内 MCP 面（`web/app_mcp.py`）

`__desktop` / `__service` 置 `LDST_APP_MCP=1`，主进程内 daemon 线程起
MCP Streamable HTTP；`ldst run`（开发态）默认不带上。端口按环境取：
prod=5951，test=15951（test 环境看板本身占 5951，按模块 +10000 同口径平移）。
端口被占（如手动起的旧 `ldst mcp http`）→ 记警告、不挡看板启动。

### C. 端口偏好（`board_user`）与关于页修改

`config/ports.yaml` 新增 `board_user` 键，与自动避让结果 `board` 分开存：

- 启动优先级：显式 `--port` > `board_user` > `board`（避让结果）> 默认 5950；
- 偏好被 foreign 占用时**照常避让**但偏好不丢，端口空闲后下次启动回用偏好；
- 环境切换（prod ↔ test）时偏好与 modules 段同口径整段失效。

`/api/settings/port`（GET/PUT/DELETE）：PUT 校验范围（1024–65535）、保留段
（当前环境全部模块端口 + MCP 面端口 + 双环境面板口 5950/5951）、**实时占用**
（被占用返回 409，带 PID / 进程名）。保存只写配置，重启应用生效——绝不热
替换监听。前端关于页新增「端口设置」卡：当前口 / 默认 / 偏好三件套 + 输入
检测保存 + 清除偏好；409/422 的服务端人话原因原样上屏。

设回默认端口 = 清除偏好（避免「偏好==默认」的怪状态）。

### D. 单窗口无终端

- PyInstaller 主 EXE 改 `console=False`（双击不闪黑窗）；smoke/e2e 的
  `-RedirectStandardOutput/-RedirectError` 句柄在 windowed 形态下仍有效；
- 无控制台时 stdout/stderr 为 None 会把 print/logging 全打死——新增
  `cli/windowed.ensure_windowed_sinks()`，入口最早处把标准流落盘到
  `<数据目录>/service/logs/app-stdout.log|app-stderr.log`；
- 启动失败（如偏好端口彻底起不来）在无控制台形态下弹系统对话框
  （Windows MessageBoxW / macOS osascript），指向日志文件；
- Windows 上服务入口（`__service`）优先 `pythonw.exe`，登录触发时不闪控制台。

## 与既有红线的关系

- DEC-004「不静默换端口」：QUOT-594 已将其收敛为「foreign 占用时自动避让
  并持久化」；本次新增的用户偏好在被占时同样避让但**偏好优先回用**，不静默
  永久漂移；
- DEC-014 不碰外部进程：不变（adopted peer / foreign 一律不动）；
- 端口修改面只写本机配置文件，与控制面「白名单写入」同等级，不做任何
  命令执行。

## 验证

- `tests/unit/web/test_port_settings_api.py`（9 例）、`test_app_mcp.py`（4 例）、
  `supervisor/test_port_allocator.py` 全绿；
- 前端 `tests/board-port-settings.test.tsx`（5 例）绿，typecheck / build 绿；
- cli / supervisor / service / compat / 相关 web 分块测试全绿。

> AI生成
