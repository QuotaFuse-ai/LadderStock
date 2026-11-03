# DEC-048 每页一键命令 + 模块开机自启

- 日期：2026-11-03
- 提出：总经理（agent `0e5c520f`），QUOT-200（老板要求 4 / 5）
- 状态：生效（实现随分支 `agent/quot-200-peer-ports` 进 `main`）
- 编号：`DEC-048`（按 DEC-045，当前最大号为 047）
- 关联：DEC-004（端口不自动切换）、DEC-006（只走生态 peer）、DEC-014（控制面白名单与
  `shell=False`）、DEC-036（进程级写锁）、QUOT-200、QUOT-350（调度守护自启）

## 1. 背景

老板要求两件事：

1. **每页顶部能直接启动对应的命令行**，不必回终端敲 `ldst …`；
2. **模块开机自启**（明确批准改动本机启动项/计划任务）。

约束：控制面是写通道，任何可从网页触发的命令都必须固定在白名单里、参数不接受请求内容
（DEC-014）；前端演示态约定「全程不碰网络」（既有测试断言 `fetch` 零调用）。

## 2. 决定

### 2.1 命令白名单放在后端，前端只选 key

`PAGE_COMMANDS`（`src/ladderstock/web/control.py`）是 key → (路由, 文案, argv) 的模块常量：

| key | 路由 | argv |
| --- | --- | --- |
| `market` | `/market` | `sync` |
| `review` | `/review` | `review --date {date}` |
| `review-ai` | `/review/ai` | `review --date {date} --force` |
| `weekly` | `/weekly` | `weekly` |
| `news` | `/news` | `news` |
| `calendar` | `/calendar` | `events` |
| `pool` | `/pool` | `pool` |
| `validate` | `/validate` | `validate reconcile --date {date}` |
| `positions` | `/positions` | `position list` |

- `{date}` 是**唯一动态值**，执行时替换为本机当天日期（`_page_argv`）；前端拿不到改写参数的口子。
- `review` / `validate` 必须带 `--date`（CLI 硬要求），因此页面清单里展示的就是替换后的实际命令。
- 复用既有 `Job` 模型与线程池：`GET /api/control/commands` 列清单与各自最近任务，
  `POST /api/control/commands/{key}` 建任务，前端轮询 `GET /api/control/jobs/{id}`。
- **单飞**：同一时刻只允许一条页面命令（`_command_job`），冲突回 409；避免与调度器、彼此在
  同一数据目录上并发写。
- **退出码**：0 成功、8 部分数据源失败、9 AI 降级都算任务成功（结果已落盘），其余记失败并把
  最后一行输出带进错误；与 QUOT-236 的 AI 任务口径一致。

### 2.2 前端命令条挂在 `Layout` 主容器

`PageCommandBar` 放在 `<main>` 里、`<Outlet/>` 之上，按 `location.pathname` **精确匹配**路由：
`/review` 与 `/review/ai` 各自一条，`/market?tab=*` 四视图共用一条。控制面不可用时整条不渲染，
按钮失效不挡住页面数据；**演示态（`?demo=1` 或设置开关）不发请求、不渲染**，维持「演示零网络」。

### 2.3 模块自启：幂等脚本 + 登录计划任务

`D:\QuotaFuseModules\LadderStock\ops\start_all.cmd`：

- 按端口探测（`netstat … LISTENING`）逐个 `ensure`：已在监听就跳过，避免重复拉起；
- TickHub-CN 8000 走 `C:\Users\Administrator\.tickhub\start_peer.cmd`（会加载 `proxy.env`），
  其余 peer 走既有 `ops/peers/*.cmd`；看板 5950 走 `start_board_main.cmd 5950`；
- 末尾 `schedule-ops.cmd start`，调度守护已运行则自行跳过；
- 每个模块经 WMI `Win32_Process.Create` 拉起（`cmd /c <script>`），子进程不在调用方的作业对象里：
  既活过 agent run 收尾清理，也活过登录任务结束——`start /min` 起的子进程会被连同任务一起收走，
  11-03 19:29 已实测丢过一次看板进程，故不采用。

承载为计划任务 `LadderStock Modules`（`-AtLogOn`、`StartWhenAvailable`、`IgnoreNew`、
10 分钟执行时限）。指向已不存在 run 目录的旧任务 `LadderStock UI 5950` 置 `Disabled`，
消除登录时与 `start_all.cmd` 双启。

## 3. 权衡

| 选项 | 取舍 |
| --- | --- |
| 前端直接拼 `ldst` 参数 | 否。等于把任意子命令暴露给网页，违背 DEC-014 白名单 |
| 每个页面单独一套接口 | 否。一条清单 + 一个 POST，新增页面只加一行常量 |
| 命令失败自动重试 | 否。部分失败（8）本身就是可接受终态，重试会重复写盘 |
| 用启动文件夹快捷方式承载模块自启 | 否。快捷方式只能起一个目标；计划任务能给失败重启与错过补起 |
| 删除旧 `LadderStock UI 5950` 任务 | 否。先禁用，保留可回退证据 |

## 4. 影响

- `src/ladderstock/web/control.py`：`PAGE_COMMANDS` / `PAGE_COMMAND_*`、`Job.command`、
  `page_commands()` / `create_command_job()` / `_run_page_command()`、`GET|POST /commands`；
- `frontend/src/components/PageCommandBar.tsx`（新）、`Layout.tsx`、`api.ts`；
- 测试：`tests/unit/web/test_control_plane.py` 新增 5 条（清单/日期替换/退出码/单飞/路由）。
- 机器配置：新增计划任务 `LadderStock Modules`；禁用旧 `LadderStock UI 5950`。
