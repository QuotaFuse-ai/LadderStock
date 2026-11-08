# DEC-070: ProxyFleet 纳管为控制面 peer + SYSTEM 计划任务垫片 + MCP 薄封装（QUOT-485）

日期：2026-11-09 · 状态：已定稿 · 关联：QUOT-485，推翻 DEC-050（ProxyFleet 只登记为工具组件）/ 收窄 DEC-051（第五模块卡）

## 1. 决策

ProxyFleet 从「只读观测的组件」（DEC-050/051）升级为**纳管 peer**：进
`core/boundary.py` 的 `PEER_CONTROL` + `config/sources.yaml` 的 peers 块，
控制面出卡、可查状态、可起停；LS 侧新增两个 MCP 工具
（`proxyfleet_status` / `proxyfleet_control`）把它的能力暴露给 AI 通道。

本机部署事实：ProxyFleet 由 Windows 计划任务 `ProxyFleet`（SYSTEM 账户，
Boot 触发 + 1 分钟自愈重复触发）带起 `python -m proxyfleet serve`。
控制面**不接管它的运行身份**，起停走这条既有垫片。

## 2. 间接层：SYSTEM 计划任务垫片（硬约束）

- 保留 `ProxyFleet` 计划任务原样（SYSTEM 账户、触发器、权限**一律不动**）；
  禁止改运行身份、禁止降权、禁止绕过它直接 spawn 进程。
- 控制面生命周期走 `PEER_CONTROL.task_name="ProxyFleet"` 分支（control.py）：
  - **start** = `Enable-ScheduledTask` → `Start-ScheduledTask`
  - **stop** = 先 `Disable-ScheduledTask`（压住 1 分钟自愈）再 `Stop-ScheduledTask`——
    纯 `/end` 会在 1 分钟内被自愈拉回，实测已验证（stop 后 70s 端口仍为 0）。
  - enable/disable 不触碰任务账户、触发器与权限。
- **账本口径**：由垫片拉起的服务记 `origin="spawned-via-task"`（`task.origin`），
  `pids=[]`——控制面只是打了一下计划任务，进程归属属于 SYSTEM 垫片，
  不冒充直接子进程。

## 3. 两个面的判定口径

| 面 | 端口 | 判定口径 |
|---|---|---|
| 控制台 / REST | **8788** | `web_port=8788`（`mcp_port` 同口）：8788 同时挂控制台 HTML（`/`）与 `/v1/*` API；健康端点实测 `/readyz`（免鉴权，200 = `biz_ok≥1`，503 = 活着但池空；`/healthz` `/health` 均 404）。出「打开」按钮。 |
| 出口网关 | **8789** | 按 481「按面判定」口径进 `extra_ports=(8789,)`：只探测归属并计入 `listening`，**不出「打开」按钮**——网关不是能点的站点。 |

## 4. MCP 能力（薄封装，不重写 ProxyFleet 内部逻辑）

| 工具 | 只读 | 入参 | 数据来源 |
|---|---|---|---|
| `proxyfleet_status` | 是 | 无（可用 `LDST_PROXYFLEET_API_URL` 覆盖目标） | `/readyz` + `/v1/stats/summary` + `/v1/nodes?limit=500` 原样转发/摘取 |
| `proxyfleet_control` | 否 | `action ∈ {start, stop}` | 不碰 REST 面：经 `ControlPlane.create_job("proxyfleet", …)` 走 §2 垫片 |

结论口径：LS 能通过 MCP 查 ProxyFleet 状态（池水位 / 网关 / 采集源错误 /
最近失败节点）并起停（任务 ID + log_tail 回执）；信封 domain 用 `review`
（`ladder_get_sources_health` 先例），不动 `Domain` Literal 与
`ENVELOPE_OUTPUT_SCHEMA`；四层边界不变——DATASET_CATALOG 不给代理池任何
数据集，sources 不许 `via: proxyfleet`。详见 `docs/MCP_TOOLS.md` §1.4。

## 5. config_keys 白名单键表

**空白名单（`config_keys=()`，配置编辑入口置灰）**，原因（逐键核实结论）：
ProxyFleet 不读任何 dotenv，配置真源是 `config.json`
（`proxyfleet/config.py` DEFAULTS）叠加 `PF_<SECTION>_<KEY>` 环境变量覆盖
（`proxyfleet/config.py:184-186`）。控制面的「dotenv + 键级白名单」模型套不上，
按 PRD §5「核实不了就空白名单 + 置灰」处理；配置编辑在本 peer 的控制台
（8788）完成。`secret_keys=()` 同理；`env_filename="config.json"` 仅作展示。

## 6. 验收实测（2026-11-09 本机）

- 运行态卡：`GET /api/control/peers/proxyfleet` → `running` / health ok /
  `can_start=false` `can_stop=true` `can_open=true`；停止态 → `installed`
  / `can_start=true` `can_stop=false` `can_open=false`，两面均与真实端口一致。
- 停止实测：MCP `proxyfleet_control{stop}` → 8788/8789 监听 0、任务 State=Disabled、
  70s 后自愈未拉回；启动实测：`{start}` → Enable→Start→8788 3.8s 监听，
  State=Running，LastRunTime 推进。
- MCP 调用：`proxyfleet_status` 实回 available=true / pool biz_ok=81 /
  nodes_total=500 / recent_failures 10 条 / problems=[]。
- 回归：pytest 全量绿；`npm run build` 绿。
