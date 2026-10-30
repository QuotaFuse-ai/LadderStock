# 数据源、同步与本地缓存（M2）

- 状态：已落地（2026-10-28，M2 数据层）
- 关联：DEC-006（四层生态边界）、DEC-007（重复采集治理）、`docs/CLI.md`（退出码）
- 一句话：**LadderStock 只消费生态三件套**；主源失败只在同一 peer 内换工具，
  再不行就回放本地缓存，永不自行采集。

本文写清四件事：主备链路怎么组装、水位线存在哪、数据落在哪、注册表与冷却的治理规则。
实现落点：`src/ladderstock/data/{provider_registry,watermark,duckdb_store,archive}.py`、
`src/ladderstock/cli/{sync,sources}.py`。

## 1. 四类 peer

配置在 `config/sources.yaml` 的 `peers` 段。地址一律读环境变量，**配置文件里不写密钥**，
缺省值是回环地址（未部署对应 peer 时连接失败即视为不可用）。

| peer | 产品 | CLI | 地址环境变量 | 缺省地址 | 消费方式 |
|---|---|---|---|---|---|
| `tickhub` | TickHub-CN | `th-cn` | `LDST_TICKHUB_MCP_URL` | `http://127.0.0.1:8000/mcp` | 只消费，可缓存回放 |
| `tickhub_us` | TickHub-US | `th-us` | `LDST_TICKHUB_US_MCP_URL` | `http://127.0.0.1:8002/mcp` | 只消费，可缓存回放 |
| `wire` | QuotaFuse Wire | `qfw` | `LDST_WIRE_MCP_URL` | `http://127.0.0.1:8791/mcp` | 可选消费（题材叙事） |
| `senate` | StockSenate | `ssenate` | `LDST_SENATE_MCP_URL` | `http://127.0.0.1:8765/mcp` | 只读消费（不复现合议） |

TickHub-US 的端口按 DEC-013 定：MCP **8002**、管理端 **8789**——8001 是 WSL 转发服务
的固定端口，8787 已被 TickHub-CN 管理端占用，两者都不允许抢占（DEC-004 不自动换端口）。

传输层是 MCP Streamable HTTP（QUOT-232）：首次调用先 `initialize` 握手，拿响应头的
`Mcp-Session-Id` 回一条 `notifications/initialized`，之后每个请求带
`Mcp-Session-Id` 与 `MCP-Protocol-Version`；会话按 peer 复用，服务端 404
（`Session not found`）时重新握手并重发一次。响应按 SSE 切帧（`data:` 行，
`: ping` 保活注释忽略），业务内容优先读 `structuredContent`（拆掉生态统一的
`{"result": {...}}` 外壳），否则把 `content[0].text` 当 JSON 解析；
`isError` 时把 `content[].text` 的原文报出来。peer 是本机回环地址，传输**不走**
系统代理（Windows 的 WinINET 代理会把 127.0.0.1 也转走，表现为无端 503）。

## 2. 数据集归属与主备

唯一真源是 `core/boundary.py:DATASET_CATALOG`；`sources.yaml` 的 `via` / `tool`
必须与之对齐，`sync.yaml` 只负责「用哪几个 source id、增量模式」。

| 数据集 | peer | 主工具 | 同 peer 备源 | 模式 | 水位字段 |
|---|---|---|---|---|---|
| `daily_k` | tickhub | `tencent_kline` | `tdx_daily_package` | append | `trade_date` |
| `limit_pool` | tickhub | `limit_up_pool` | `ths_limit_up_pool` | replace | `trade_date` |
| `broken_board` | tickhub | `broken_board_pool` | — | replace | `trade_date` |
| `yesterday_limit_up` | tickhub | `yesterday_limit_up_pool` | — | replace | `trade_date` |
| `sentiment` | tickhub | `market_sentiment` | — | replace | `trade_date` |
| `dragon_tiger` | tickhub | `dragon_tiger` | `dragon_tiger_backup` | replace | `trade_date` |
| `dragon_tiger_seats` | tickhub | `dragon_tiger_seats` | — | replace | `trade_date` |
| `industry_board` | tickhub | `industry_boards` | — | replace | `trade_date` |
| `concept_board` | tickhub | `concept_boards` | — | replace | `trade_date` |
| `fund_flow` | tickhub | `stock_fund_flow` | — | replace | `trade_date` |
| `hot_rank` | tickhub | `ths_hot_rank` | `eastmoney_hot_rank` | replace | `trade_date` |
| `theme_news` | wire | `wire_get_digest` | `wire_timeline` | replace | `data_as_of` |
| `council_memo` | senate | `report_query` | — | replace | `data_as_of` |

`theme_news` / `council_memo` 在 `sync.yaml` 里标了 `optional: true`：peer 不可用时
只记 `status=skipped`，不拖垮整轮同步，也不影响退出码。

### 2.1 链路登记规则（DEC-008）

「配置里写的链路」与「门禁允许的链路」必须是同一个事实。两边各改一半是
`theme_news` 事故的成因：yaml 有 `wire_timeline`，目录却只登记了 `wire_get_digest`，
于是 `doctor` 全绿、`sources validate` 只给一句不痛不痒的警告。

**规则（`core.boundary.chain_problems()` 逐条检查，`doctor` / `sources validate` /
测试共用这一个函数）：**

1. 每条 `sources.yaml` 记录的 `dataset` 必须已在 `DATASET_CATALOG` 登记；
2. `via` 必须等于该数据集登记的 `peer`（主备同 peer，DEC-006 闸门 3）；
3. `tool` 必须在该数据集的**允许工具集合**内，即（登记主工具 + 登记的备工具）；
4. `primary: true` 的记录，工具必须正好是登记主工具；同一数据集只能有一个主源；
5. `fallback` 引用的 id 必须存在、同 dataset、同 peer；
6. 反向：目录里每个数据集的登记主工具，必须在 yaml 有 primary 记录——
   「门禁允许、配置从未使用」同样是错误；
7. `sync.yaml` 的数据集必须已登记，且其 `primary` 是同一数据集在 yaml 里的记录。

**新增一个工具 / 数据集的顺序**（顺序反了必然被门禁拦下）：

1. 先在 `DATASET_CATALOG` 登记（peer、主工具、可选备工具、模式、水位字段）；
2. 再在 `sources.yaml` 加 peer 内的记录；
3. 要在 `sync.yaml` 同步的，再补主源与同 peer 备源；
4. 跑 `ldst doctor` 与 `ldst sources validate` 确认 `sources:chain` 绿。

**职责边界**：`sources:chain`（`doctor`）只判配置合法性，**不探测 peer**；
可达性只由 `ldst sources validate` / `ldst sources health` 负责，所以 peer 没启动
不会让 `doctor` 变红。`sources validate` 的退出码优先级见 §8。

## 3. 降级顺序：主 → 同 peer 备 → 缓存回放

```
sync.yaml 的 primary
   ├─ 失败/冷却 ──▶ 同 peer 的另一个工具（fallback）
   │                    ├─ 成功 → source_status=degraded，记 fallback 事件
   │                    └─ 失败 ──▶ 本地缓存回放
   └─ 成功 → source_status=ok
                                        └─ 缓存也没有 → 退出码 4 / 8
```

硬约束（`BoundaryViolation`，属于配置错误：退出码 3）：

- 备源必须与主源**同 dataset、同 peer**；跨 peer 的链路直接判违规，不是降级；
- 主备引用的 source id 必须在 `sources.yaml` 里真实存在；
- 未在 `DATASET_CATALOG` 登记的数据集不许同步。

缓存回放（DEC-007 §2.5）读 `archive.snapshot()`：**Parquet 日分区优先 → DuckDB 兜底**。
回放出的信封 `freshness=cached`、`source_status=degraded`、`confidence=low`，
`source_detail` 里写清读的是哪个后端、哪一天。回放**不重写库、不推进水位**。

## 4. 运行期治理（DEC-007 §2.4）

实现在 `provider_registry.ProviderRegistry`，四条规则全部默认开启，测试可显式关掉：

| 规则 | 行为 | 常量 |
|---|---|---|
| in-flight 合并 | 同一 `(dataset, trade_date, params)` 的并发请求只打一次，其余等同一结果 | `INFLIGHT_WAIT_SEC = 180` |
| 短窗缓存 | 成功结果按 `cold_hot.intraday_cache_ttl_sec` 内存缓存，命中标 `freshness=cached` | `DEFAULT_TTL_SEC = 60` |
| 失败冷却 | 工具失败后按 `cooldown_sec` 冷却，冷却期内直接看备源 | 见 `sources.yaml` |
| 并发上限 + 最小间隔 | 每 peer 独立信号量 + 调用间隔，防重试风暴 | 见下表 |

每 peer 限流常量（配置 schema 冻结，故写在代码里）：

| peer | 并发上限 | 最小间隔 |
|---|---|---|
| tickhub | 4 | 0.05 s |
| wire | 2 | 0.2 s |
| senate | 2 | 0.2 s |

重试有界：`sources.yaml` 的 `retries` 是**额外重试次数**（总尝试 = `retries + 1`），
用尽即降级，不做无界重试。传输层异常（超时 / 拒连）原样抛出，轨迹里据此区分
`timeout` 与 `unreachable`；上游业务拒收记 `rejected`。

## 5. 水位线

**存放位置**：`<data_dir>/state/watermark.json`（`LDST_DATA_DIR` 可覆盖数据目录）。
选 JSON 而不是 DuckDB 元数据表，理由是水位要在 DuckDB 打不开（文件占用 / 损坏）时仍可读，
`ldst sync status` 不能跟着挂；且单用户、单写入进程，原子替换足够。

结构：

```json
{
  "version": 1,
  "watermarks": {
    "daily_k": {
      "dataset": "daily_k", "value": "2026-10-24",
      "updated_at": "2026-10-24T15:31:00+08:00",
      "rows": 5218, "source": "tickhub_kline", "status": "ok"
    }
  },
  "runs": [
    {"dataset": "daily_k", "mode": "incremental|full", "started_at": "...", "finished_at": "...",
     "status": "ok|degraded|empty|skipped|failed", "rows": 5218, "source": "tickhub_kline",
     "trail": ["tickhub_kline/tencent_kline:ok"],
     "watermark_before": "2026-10-23", "watermark_after": "2026-10-24", "detail": ""}
  ]
}
```

约定：

- `value` 是 ISO 日期或 ISO 时间串，字典序即时间序；
- **水位只进不退**：历史补拉 / 备源给出更早日期时保留高水位；`--full` 只忽略水位拉全量，
  不会把水位拉回去；
- 每完成一个数据集即推进并落盘（`tmp` + `os.replace` 原子替换），中途中断后重跑
  从水位断点续跑，已完成数据集不重复写；
- `runs` 只保留最近 `MAX_RUNS = 200` 条。

`ldst sync status` 输出：`state_file`、`watermarks`、`datasets_without_watermark`、
`recent_runs`、`active_cooldowns`、`data_dir`。

## 6. 落库与分区布局

```
<data_dir>/market.duckdb                          # 历史库
<data_dir>/archive/YYYY/MM/DD/<dataset>.parquet   # 当日快照，一天一个数据集一个文件
```

表结构统一 5 列（每个数据集一张同名表）：

| 列 | 类型 | 含义 |
|---|---|---|
| `code` | VARCHAR | 个股代码；整日快照（板块 / 情绪 / 叙事）为空串 |
| `trade_date` | DATE | 交易日；`theme_news` / `council_memo` 存 `data_as_of` |
| `ingested_at` | TIMESTAMP | 落库时间（带时区） |
| `source` | VARCHAR | 实际取数的 source id（缓存回放为 `local_cache`） |
| `payload` | VARCHAR | 原始记录 JSON，不做字段猜测与补全 |

幂等键与重跑行为：

| 模式 | 数据集 | 幂等键 | 重跑结果 |
|---|---|---|---|
| `append` | `daily_k` | `(code, trade_date)` | upsert，行数不增长 |
| `replace` | 其余 | `trade_date`（整日） | 整日覆盖，**不按 code 折叠**（席位/龙虎榜一天可能多行） |

事务与原子性：

- 一次 `write_dataset()` 是一个事务，任一行失败整批回滚，库里不留半份；
- 提交成功后才导出 Parquet，先写 `.parquet.tmp` 再 `os.replace` 原子替换；
- peer 返回空记录时**不删除**已有数据，状态记 `empty`，水位保持。

读接口只有一个：`archive.read_dataset(dataset, day)`（`snapshot()` 的薄封装），
后续 `review show` / `sentiment show` 也走它，不各自拼 DuckDB SQL。

## 7. 健康与冷却

`<data_dir>/state/health.json`：

```json
{
  "version": 1,
  "cooldowns": {"tickhub_kline": {"until": "2026-10-24T15:33:00+08:00",
                                   "reason": "timeout", "tool": "tencent_kline"}},
  "events": [{"at": "...", "kind": "failure|fallback|cache_replay|skipped_cooldown",
              "dataset": "daily_k", "source": "...", "tool": "...", "detail": "..."}]
}
```

- 冷却**跨进程生效**（重启后仍然有效），这是设计而非缺陷；要立刻重试见 §10；
- `events` 只保留最近 `MAX_HEALTH_EVENTS = 500` 条；
- `ldst sources health` 输出 `state_file`、`active_cooldowns`、`events`、
  `sources_in_cooldown`、`peers`；
- 健康事件是排障入口：`reason` 区分 `timeout` / `unreachable` / `unavailable` / `rejected`。

### 7.1 控制面的健康与端口预检（DEC-014）

看板 `/api/control/*`（`web/control.py`）复用同一套 peer 元数据，但只做「看与动」，不改数据链路：

- **健康探测**：优先打 peer 的 Web 端口、其次 MCP 端口，路径取 `PeerControl.health_path`
  的实测值——TickHub-CN / US / Senate 是 `/healthz`（CN 在 admin、US 在 MCP、Senate 在 WebUI），
  Wire 是 8790 上的 `/api/health`（8791 只是 MCP）。2 秒超时，失败不影响 `sync`。
- **端口预检**：`netstat -ano -p tcp` 找 LISTENING 的 PID，再 `tasklist` 反查进程名；冲突如实写进
  `port_conflicts`（附 `KNOWN_AVOIDED_PORTS` 的历史坑位说明），**绝不替你换端口、也不杀占用者**。
- **配置写入**：只写 `PeerControl.env_filename` 声明的那个 dotenv（CN/US/Senate 为 `.env`，Wire 为
  `.env.local`），0600；`PeerControl.config_notes` 会原样回给前端，说明哪些键当前不生效
  （CN 无 `MCP_HTTP_PORT`、US 无 admin API、Senate 的 LLM 键其实在 `llm_config.toml`）。

## 8. 命令与退出码

| 命令 | 作用 | 退出码 |
|---|---|---|
| `ldst sync` | 同步 `sync.yaml` 全部数据集 | 0 / 3 / 4 / 8 |
| `ldst sync --dataset <name>` | 只同步一个数据集 | 同上；未知数据集 2 |
| `ldst sync --full` | 忽略水位拉全量（维护用） | 同上 |
| `ldst sync status` | 水位线 + 最近运行 + 冷却 | 0 |
| `ldst sources list` | peer / tool / 主备 / 冷却清单 | 0 |
| `ldst sources validate` | 主备合法性（离线）+ peer 可达性 | 0 / 3（违规）/ 4（不可达） |
| `ldst sources health` | 未过期冷却 + 最近事件 | 0 |

`sync` 退出码语义：

- 全部成功（含 `degraded` 备源 / 缓存回放、`empty`、`skipped`）→ `0`；
- 有失败也有成功 → `8`（部分源失败）；
- 全部必需数据集失败（peer 全挂且无缓存）→ `4`（源不可用）；
- 主备跨 peer / 未登记归属等配置问题 → `3`。

`optional: true` 的数据集永远只记 `skipped`，不参与失败计数。

`sources validate` 的退出码优先级（DEC-008 / docs/CLI.md §F）：

- 配置非法（catalog ↔ yaml 不一致、跨 peer、工具未登记）→ `3`，stderr 与 JSON 的
  `problems` 会指出**哪份文件、哪条记录、哪个字段**；此时**跳过**可达性探测，
  避免「peer 不可达」掩盖配置问题（`peers[].reachable = null`）；
- 配置合法、但 peer 探测失败 → `4`（`reason = "unreachable"`，`problems = []`）；
- 两者都正常 → `0`。

`doctor` 的 `sources:chain` 项只反映配置合法性；peer 没启动不影响它。

## 9. 测试如何做到完全离线

- `tests/fixtures/fake_peers.py`：`FakeTransport` 实现 `Transport` 协议，按 tool 名分派，
  地址只用 `http://127.0.0.1:1/mcp` 占位且从不去拨；`SOURCES_DOC` / `SYNC_DOC` 为最小配置子集。
- 注入点：`provider_registry.build_transport()`（monkeypatch 即离线）；
- 数据目录隔离：`LDST_DATA_DIR` 指向 `tmp_path`，冷却与水位不会污染仓库 `data/`；
- 覆盖用例：`tests/unit/data/`（水位 / DuckDB 幂等 / 分区读回）、
  `tests/integration/test_provider_fallback.py`（主备切换、冷却、in-flight 合并、跨 peer 违规）、
  `tests/integration/test_sync_incremental.py`（增量 `since`、同日重跑不增长、`--full`、
  断点续跑、退出码 4/8、缓存回放不推进水位）。

## 10. 排障

| 现象 | 先看 | 处理 |
|---|---|---|
| `sync` 退出 4 | `ldst sources health` 的 `reason` | peer 未启动 / 地址不对：查环境变量与 `th-cn mcp` |
| 一直说「链路内工具均在冷却期」 | `sources health` 的 `cooldowns.until` | 等冷却到期，或确认故障已修复后清 `state/health.json` 里对应条目 |
| 退出 8 | `sync` 输出的 `results[].detail` 与 `trail` | 只补失败的数据集：`sync --dataset <name>` |
| `validate` 退出 3 | 输出里的 `chains` / `warnings` | 主备不同 peer / 工具未登记：修 `sources.yaml` 或 `sync.yaml` |
| 数据没更新 | `sync status` 的 `watermarks` 与 `recent_runs` | 水位是否已推进；`empty` 说明 peer 返回空，不应覆盖旧数据 |

清冷却只影响「下一次立刻重试」，不会清掉历史事件；清理后建议先跑
`ldst sources validate` 确认链路本身可用。
