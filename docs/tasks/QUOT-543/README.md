# QUOT-543 战略 C1 工具注册表：交付与验收取证

- 日期：2026-11-11
- 决策：`docs/decisions/DEC-076-platform-tool-registry.md`
- 命令：`ldst ai tools list`（`docs/CLI.md` §O）

## 1. 交付内容

1. **聚合真实工具**：`ai/tool_registry.py` 从两类来源发现工具——本仓 MCP
   `TOOL_SPECS`（source `ladderstock`）与 `config/sources.yaml` 里声明了 MCP 面的
   peer（tickhub / tickhub_us / wire / senate）的真实 `tools/list`。每条含
   `name` / `source` / `product` / `description` / `parameters`(inputSchema) /
   `read_only` / `domain`。
2. **命名空间隔离**：查询与落盘都必须同时带 `agent_id` / `kb_scope`
   （校验正则与 `ai/fallback.py: MemoryScope` 共用）；快照只落
   `<data_dir>/ai/tool_registry/<agent_id>/<kb_scope 转义>.json`，读取前再校验文档内
   命名空间，跨命名空间一律拒绝。
3. **查询接口**：CLI `ldst ai tools list --agent-id … --kb-scope …`
   （`--query` / `--source` / `--limit` / `--refresh` / `--output`），Python 侧
   `ToolRegistry.query()`；未 `--refresh` 时读本命名空间快照，没有才现发现。
4. **不编数**：peer 不可达 → 该来源 `status=unreachable` + `tool_count=0` + `error`，
   不补历史名单；无 MCP 面的 peer 不列入发现；计数以 peer 当次回答为准。

### 1.1 改动文件

| 文件 | 内容 |
|---|---|
| `src/ladderstock/ai/tool_registry.py` | 新增。`ToolNamespace`（双维校验 + `*` 转义）、`ToolEntry` / `SourceStatus` / `ToolSnapshot`、`ToolRegistry.discover/query/load/save` |
| `src/ladderstock/data/provider_registry.py` | `HttpMcpTransport.list_tools()`（`tools/list` 原始条目），`probe()` 复用它 |
| `src/ladderstock/ai/fallback.py` | 抽出公开判定 `is_valid_agent_id` / `is_valid_kb_scope`，记忆解析改为复用 |
| `src/ladderstock/cli/ai.py` | 新增 `ai_tools_list` 与 table 视图裁剪，`register` 挂 `ai tools list` |
| `src/ladderstock/cli/main.py` | 新增 `ai_tools_app` 并注册到 `ai` 组 |
| `tests/unit/ai/test_tool_registry.py` | 新增 20 例：真实形状 / 计数一致 / 本地工具入表 / 不可达 0 条 / 命名空间隔离与拒绝 / 缓存与 refresh / CLI |
| `tests/fixtures/command_matrix.py` | `LEAF_PATHS` 登记 `("ai", "tools", "list")` |
| `docs/decisions/DEC-076-…`、`docs/CLI.md` §O、`docs/ARCHITECTURE.md` | 决策与口径 |

### 1.2 明确没做

- 没写前端页面（如需看板展示，按红线另开前端子单）；
- 没把「发现」扩展成「调用」——C2 的自主调用循环不在本单；
- 没读 TickHub 仓库里的静态清单、没在 yaml 里维护第二份工具名单。

## 2. 本机验收取证

`ldst ai tools list --agent-id ladderstock-research --kb-scope research --refresh`：

| 来源 | 状态 | 条数 |
|---|---|---|
| `ladderstock` | ok | 18 |
| `tickhub` | ok | **151**（含 `em_kline`） |
| `tickhub_us` | unreachable | 0（本机未启动，如实标注） |
| `wire` | unreachable | 0（同上） |
| `senate` | unreachable | 0（同上） |

合计 169 条；`--source tickhub -q kline` 命中 6 条（`em_kline` / `get_kline` /
`index_kline` / `futures_kline` / `tencent_kline` / `baidu_kline_with_ma`）。
快照落在 `<LDST_DATA_DIR>/ai/tool_registry/ladderstock-research/research.json`，
未产生其它命名空间文件。

## 3. 测试与静态检查

- `pytest tests/unit/ai tests/unit/data tests/contract`：568 passed
- `pytest tests/unit/cli tests/unit/test_sources_consistency.py …`：112 passed
- `pytest tests/unit/test_cli_output_option.py tests/unit/test_cli_surface.py`：302 passed / 2 skipped
- `ruff check .`、`mypy`：通过（CI 复验）
