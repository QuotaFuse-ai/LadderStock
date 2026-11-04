# DEC-047 peer 端口归属：US 不设管理端、Senate WebUI 改 8786、非控制面监听降级为归属提示

- 日期：2026-11-03
- 提出：总经理（agent `0e5c520f`），用户反馈「设置页全是冲突」
- 状态：生效（实现随 PR 进 `main`）
- 编号：`DEC-047`。按 `DEC-045` 规则取值：`origin/main` 的 `docs/decisions/` 最大号 = 046
- 关联：DEC-013（TickHub-US 端口口径）、DEC-014（控制面白名单 / 不杀非本控制面进程）、
  DEC-004（不自动换端口）

## 1. 背景

设置页把每个 peer 的端口占用都渲染成黄色冲突，用户看到 8 条「端口已被其他进程占用」
却四个模块都显示「运行中」。实测只有两条是真冲突：

| 端口 | 占用者 | 性质 |
| --- | --- | --- |
| 8000 / 8787 | TickHub-CN 自身（`th-cn start-all` / `th-cn admin serve`） | 控制面之外手动启动 |
| 8002 | TickHub-US MCP 自身 | 同上 |
| 8790 / 8791 | Wire 自身（`qfw serve` / `qfw mcp http`） | 同上 |
| 8765 | StockSenate MCP 自身 | 同上 |
| **8788** | **ProxyFleet**（REST / 管理台） | 真冲突：Senate WebUI 起不来 |
| **8789** | **ProxyFleet**（本地转发网关） | 真冲突：US 本就没有 admin API，却把该端口登记成 US 管理端 |

根因两条：
1. `PEER_CONTROL["senate"].web_port = 8788`、`PEER_CONTROL["tickhub_us"].web_port = 8789`
   与本机 ProxyFleet 固定端口重叠，且 US 构建根本没有 admin API（`ADMIN_HTTP_PORT` 无处读取）；
2. 控制面把「端口被控制面之外的进程监听」一律当冲突。手动启动是常态（TickHub-CN 必须先加载
   `.tickhub/proxy.env`），模块健康时这不是故障。

## 2. 决定

### 2.1 TickHub-US 不再声明管理端端口

`PEER_CONTROL["tickhub_us"].web_port = None`。没有 admin API 就不该广告一个端口：
否则控制面会去探 ProxyFleet 的 8789（`/healthz` 回 400）、`open_url` 指向别人的网关。
`ADMIN_HTTP_PORT` 键与 PRD §5 的说明保留，等在 US 构建真的提供 admin API 时再分配端口。

### 2.2 Senate WebUI 改用 8786

`web_port = 8786`，启动命令 `ssenate webui --host 127.0.0.1 --port 8786`。MCP 仍为 8765。
8788 / 8789 明确归 ProxyFleet，控制面不登记、不探测、不抢占。

### 2.3 「非控制面启动」与「真冲突」分级

`port_conflicts` 增加 `severity`：

- `info`：模块状态为 `running`，且被占的是它自己的声明端口（mcp/web）→ 文案改为
  「端口由控制面之外的进程监听（手动启动）；控制面不接管启停，也不会终止它」；
- `conflict`：其余情况（健康检查未通过、非声明端口、历史坑位）→ 保留 DEC-004 的冲突文案。

前端只用颜色区分：`info` 走中性色 + 信息图标，`conflict` 才是告警三角。端口总览里
`info` 记作「本模块监听中」，不再记「占用」。判定不改变行为：启停预检、拒绝终止非本
控制面进程（DEC-014 §3）原样保留。

### 2.4 历史坑位表只留 8001

`KNOWN_AVOIDED_PORTS["tickhub_us"]` 删掉 8787 一条：US 不再有管理端，那条「改用 8789」
的提示已是错的，留着只会在 US 卡片上重复一条 CN 的端口。

## 3. 权衡

| 选项 | 取舍 |
| --- | --- |
| 把 ProxyFleet 挪走（8788/8789） | 否。`PROXY_URL` 与用户现有转发配置都指向 8789，代价不可逆且与本产品无关 |
| 保留 8789 但隐藏冲突行 | 否。`open_url` 仍会打开 ProxyFleet，属静默误导 |
| 给控制面加命令行识别（判断监听者是不是本 peer） | 否。每端口一次进程命令行查询，收益只是更精确的提示；健康 + 声明端口已够用 |
| 所有占用都降级为 info | 否。健康检查没通过时仍可能是别人占了端口，必须留告警 |

## 4. 影响

- `src/ladderstock/core/boundary.py`：US `web_port=None`、Senate `web_port=8786` 与启动命令、notes；
- `src/ladderstock/web/control.py`：`port_conflicts[].severity`、`KNOWN_AVOIDED_PORTS` 去 8787；
- `frontend/src/screens/SettingsScreen.tsx`、`frontend/src/api.ts`：分级渲染、静态基线与页脚文案；
- `docs/CLI.md` §F、`docs/SOURCES.md` §1：端口清单；
- 测试：`tests/unit/web/test_control_plane.py` 更新 US 端口断言与两条 severity 断言。