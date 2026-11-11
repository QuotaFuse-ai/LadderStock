# DEC-076 平台工具注册表：真实 MCP 发现 + 命名空间快照

- 状态：已采纳（QUOT-543，2026-11-11）
- 关联：DEC-006（唯一取数入口 / 生态边界）、DEC-008（诊断与配置优先）、
  DEC-071（安装态数据目录）、QUOT-498（`agent_id` / `kb_scope` 记忆命名空间）、
  `docs/SOURCES.md`（peer 注册表）、`src/ladderstock/ai/tool_registry.py`

## 背景

战略 C1 要求智能体层能**发现平台有哪些工具**，作为 C2（function-calling 自主
调用循环）的前置。平台里已经有两类真实工具源：

1. 本仓 MCP 工具（`mcp/tools.py: TOOL_SPECS`，18 条，进程内常量）；
2. 生态 peer 的 MCP 面（`config/sources.yaml` 声明的 tickhub / tickhub_us /
   wire / senate），工具清单由各自 `tools/list` 回答——本机实测 TickHub-CN 为
   151 条（含 `em_kline`），且随上游版本变化。

研究段的受控工具集（`ai/tools.py`）解决的是另一个问题：**一次研究里允许模型
调用哪几个工具**。它不能当平台目录用，也不该被扩展成平台目录。

## 决策

新增 `ai/tool_registry.py`，把「平台当前真实可用工具」聚合成带元数据的注册表：

| 维度 | 取值 |
|---|---|
| 来源 | `ladderstock`（本地 MCP 常量）+ 每个声明了 `mcp_name` 的 peer |
| 条目字段 | `name` / `source` / `product` / `description` / `parameters`（inputSchema）/ `read_only` / `domain` |
| 发现方式 | 每个 peer 走真实 MCP `tools/list`（`HttpMcpTransport.list_tools`），不读静态清单 |
| 查询接口 | `ldst ai tools list --agent-id … --kb-scope … [--query] [--source] [--limit] [--refresh]`；Python 侧 `ToolRegistry.query` |
| 落盘 | `<data_dir>/ai/tool_registry/<agent_id>/<kb_scope 转义>.json`（原子写） |

### 不编数

- 条目只来自**某次真实返回**；某 peer 不可达时该来源记 `status=unreachable`、
  `tool_count=0`、`error` 写一句原因，不补历史名单、不猜工具名；
- 没有 MCP 面的 peer（mem0 / FinanceCoffer / qdrant / ProxyFleet）不列入发现，
  不显示成「0 个工具」的伪来源；
- 计数以 peer 当次回答为准：TickHub 有多少条、有没有 `em_kline`，由 TickHub 说，
  代码与文档都不写死数字。

### 命名空间隔离

- 查询与落盘必须**同时**带 `agent_id` / `kb_scope`，校验正则与
  `ai/fallback.py: MemoryScope` 同一份（新增 `is_valid_agent_id` /
  `is_valid_kb_scope` 两个公开判定，记忆与注册表共用）；
- 快照只写本命名空间那一个文件，读也只读它；其它命名空间的文件不读、不写；
- 文档内容再校验一次命名空间：文件被拷错位置 / 被替换时拒绝读取，而不是把
  别的命名空间的工具表交出去；
- `kb_scope` 允许 `*`（通配语义），但 `*` 在 Windows 上是非法文件名字符，
  文件名里转义成 `~`（正则不接受 `~`，转义是单射）。

### 缓存语义

快照是缓存不是真源：`query()` 默认读快照，没有才现发现；`--refresh` 重新发现
并覆盖本命名空间快照。输出同时带 `generated_at` 与每源 `status`，调用方据此
判断「这份清单是什么时候、从谁那里来的」。

## 备选方案与否决理由

1. **读 TickHub 仓库里的工具清单文件**：跨仓耦合，且上线后清单会漂移；
   `tools/list` 是 peer 自己声明的契约，永远与运行中的服务一致。
2. **写进 `config/*.yaml` 维护一份工具目录**：等于把「真实可用」变成手工同步的
   第二份名单，必然漂移，也违反「不编数」。
3. **只做 HTTP 端点**：C2 的运行体是进程内的 AI 编排，Python API 是主通道；
   CLI 是给人/脚本的验证入口。本轮先给 CLI（按 issue「HTTP 或 CLI 择一」），
   看板展示另开前端单，不在后端写页面。
4. **复用 `ai/tools.py` 的受控工具集**：那是有白名单与路径边界的**段内执行器**，
   扩成平台目录会把「可发现」与「可调用」混为一谈。

## 影响与验收

- 新增 `ldst ai tools list`（叶子路径已登记 `tests/fixtures/command_matrix.py`）；
- 测试 `tests/unit/ai/test_tool_registry.py` 覆盖：真实形状聚合（含 `em_kline`）、
  计数与来源一致、本地工具入表、不可达 0 条不编数、命名空间读写隔离 / 缺参 /
  越界拒绝、缓存与 `refresh` 语义、CLI 冒烟；
- 本机实测：`--refresh` 时 TickHub-CN 151 条 + LadderStock 18 条，`em_kline` 在列；
  其余三个 peer 未启动时如实标 `unreachable`。
