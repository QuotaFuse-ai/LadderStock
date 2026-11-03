# SPEC-LIMIT-POOL 涨停样本只读接口

- 版本：v0.1（首版：`GET /api/limit-pool` 契约、行为边界、数据来源）
- 日期：2026-10-31
- 归属：后端架构（数据面只读接口）
- 实现：`src/ladderstock/core/limit_pool.py`（投影，纯函数）、
  `src/ladderstock/web/server.py`（路由 + `LadderEnvelope`）
- 消费方：前端「验证 → 涨停样本」页（`/limit-up`）
- 数据口径（**本文件不改口径**）：`core/ladder.py` 的涨停池字段容忍与去重
  （D-01 封板时间原样透传、D-04 按 `code` 去重、不补 1）
- 关联决策：DEC-006（程序直出，AI 不参与数字）、DEC-007 §2.6（不新增采集入口）、
  `docs/decisions/DEC-030-radar-http-endpoints.md`（只读接口只做投影，不重算）

## 1. 一句话口径

**把本机 `limit_pool` 缓存原样投影成行**：不采集、不估算、不补空值、不重算指标。
当日没同步 = 200 + `available=false` + `rows=[]`，不是 500，也不是造一行。

## 2. 请求

```
GET /api/limit-pool?date=YYYY-MM-DD
```

| 参数 | 必填 | 口径 |
| --- | --- | --- |
| `date` | 是 | 严格 `YYYY-MM-DD`；缺省与非法均由 `_parse_day` 回 **400**（与 `GET /api/sentiment`、`GET /api/ladder` 同一条口径，不静默当成今天） |

只读：本路由只注册 `GET`，其它方法由 FastAPI 回 405；不写库、不写文件、不取上游。

> 继承 `_parse_day` 的既有口径：它用 `date.fromisoformat`，所以紧凑写法
> `?date=20261030` 与 `?date=2026-10-30` 同样被接受（`/api/sentiment`、`/api/ladder`
> 一直如此）。本接口不另开一套严格正则——前端应始终传 `YYYY-MM-DD`。

## 3. 外壳

沿用 `LadderEnvelope`，与 `/api/ladder` 完全一致：没有可信数据时 `data_as_of=null`，
**不拿请求日期顶替数据时点**。

| 信封字段 | 有数据 | 无数据 |
| --- | --- | --- |
| `domain` | `"sentiment"` | `"sentiment"` |
| `topic` | `"limit_pool"` | `"limit_pool"` |
| `data_as_of` | 请求交易日 | **`null`**（不拿请求日期顶替） |
| `confidence` | `"medium"` | `"low"` |
| `source_status` | `"ok"` | `"partial"` |
| `freshness` | `"cached"` | `"cached"` |
| `payload.data_source_status` | `"ok"` | `"missing"` |

载荷里显式带 `data_source_status`：它是载荷级的四档（多一个 `missing`），比信封的三档细，
前端完整度徽标用它。信封顶层的 `freshness` / `source_status` / `data_as_of` / `fetched_at`
由解析层一并读出（`frontend/src/api/envelope.ts`，QUOT-331）——此前前端只下钻 `payload`，
这四键停在外壳上被丢弃，`/limit-up` 的新度徽标只能退「未知」。后端没给的键一律 `null`，
屏侧照实出「未知」，不补 `cached`、不猜档位。

## 4. `payload` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date` | string | 请求的交易日 `YYYY-MM-DD` |
| `available` | bool | 当日缓存是否可用 |
| `reason` | string \| null | 不可用时的中文原因；可用时为 `null` |
| `source_detail` | string | 数据来源与行数，形如 `limit_pool: duckdb（42 条）` |
| `count` | int | `rows` 行数 |
| `rows[]` | array | 涨停样本行 |
| `rows[].code` | string | 证券代码 |
| `rows[].name` | string | 名称（缓存行里没有就是空串） |
| `rows[].streak` | int \| null | 连板数；取不到为 `null`，**不补 1** |
| `rows[].first_seal_time` | string \| null | 首次封板时间，TickHub 原样 `HHMMSS` 字符串，不做时区换算 |
| `rows[].last_seal_time` | string \| null | 末次封板时间，同上 |
| `rows[].reason` | string \| null | 涨停原因 / 题材，可为 `null` |
| `rows[].trade_date` | string \| null | 行内交易日 `YYYY-MM-DD`，取不到为 `null`，**不用请求日期顶替** |

### 4.1 字段容忍与取值键

与 `core/ladder.py` 共用同一份键别名，不另立口径：

- `code` ← `CODE_KEYS`（`code` / `symbol` / `stock_code` / `sec_code` / `ts_code`）
- `name` ← `NAME_KEYS`
- `streak` ← `BOARD_KEYS`（`consecutive_boards` / `limit_up_days` / `board_count` /
  `boards` / `streak` / `连板数` …）按优先级取第一个可解析值，`"3板"` 这类带单位写法照旧容忍
- `first_seal_time` ← `FIRST_SEAL_KEYS`
- `last_seal_time` ← `SEAL_KEYS`（天梯对外叫 `seal_time`，本接口照 TickHub 叫
  `last_seal_time`，取的是同一个值）
- `reason` ← `REASON_KEYS`（`reason` / `limit_up_reason` / `涨停原因` / `解释`）
- `trade_date` ← `TRADE_DATE_KEYS`（缓存写入时统一补的 `trade_date`）

### 4.2 去重、unknown 与排序

- 先按 `code` 去重（`dedupe_by_code`，保留首条），与天梯、`/api/sentiment` 同源；
- 无 `code` 或无连板数（`streak` 解析不出或 `< 1`）的行**不进 `rows`**，只计入
  `source_detail`，与天梯的 `unknown_board` 是同一批行：
  `limit_pool: duckdb（47 条，重复 code 去重 1 条，无连板数或无代码 2 条）`
- 排序：`streak` 降序、`code` 升序（与 `/api/ladder` 的天梯一致）。

## 5. 数据来源与边界

- 唯一来源：`dataset_snapshot("limit_pool", day, root)` →
  `data/archive.py`（Parquet 日分区优先，DuckDB `market.duckdb` 兜底）。**不自拼 SQL。**
- `source_detail` 里的 backend 就是缓存层的真实身份：`parquet` / `duckdb` /
  `none`（没有该日缓存）/ `error`（缓存读不动）。
- `reason` 来自 TickHub `ths_limit_up_pool`。**它是 `limit_pool` 数据集的备用工具**
  （`core/boundary.py`），落库仍是 `limit_pool` 表（行的 `_source` 可区分），
  不是独立数据集：没有注册新数据集，没有新增外采，没有改 TickHub。
  主工具 `limit_up_pool` 的行不带原因列，所以缓存里没有就是 `null`——
  **不会为了填原因去调上游**。
- 隐私：载荷经 `strip_private()`，`PRIVATE_KEYS` 命中项一律出站前剥掉；
  本接口的字段不含持仓 / 交易日志。
- 访问面：与 `/api/sentiment` / `/api/ladder` 同层（公开数据，数据面），
  **不加回环独占**；非回环来源照常 200。

## 6. 示例

### 6.1 200 有数据

```jsonc
{
  "domain": "sentiment", "topic": "limit_pool", "event_id": null,
  "data_as_of": "2026-10-30",
  "source": "ladderstock", "source_detail": "limit_pool: parquet（47 条）",
  "fetched_at": "2026-10-31T13:33:30.245459Z",
  "freshness": "cached", "confidence": "medium", "source_status": "ok",
  "payload": {
    "date": "2026-10-30", "available": true, "reason": null,
    "source_detail": "limit_pool: parquet（47 条）", "count": 47,
    "rows": [
      { "code": "002086", "name": "…", "streak": 8,
        "first_seal_time": "095436", "last_seal_time": "100327",
        "reason": null, "trade_date": "2026-10-30" }
      // … 其余 46 行，streak 降序 / code 升序
    ],
    "data_source_status": "ok"
  }
}
```

上面这批行来自 `limit_up_pool`（主工具），所以 `reason` 是 `null`——这就是「不为了
填原因去调上游」的真实样子。

### 6.2 200 `available=false`（当日缓存没同步）

```jsonc
{
  "domain": "sentiment", "topic": "limit_pool", "event_id": null,
  "data_as_of": null,
  "source": "ladderstock", "source_detail": "limit_pool: none（0 条）",
  "fetched_at": "2026-10-31T13:33:30.352987Z",
  "freshness": "cached", "confidence": "low", "source_status": "partial",
  "payload": {
    "date": "2026-10-28", "available": false,
    "reason": "涨停池为空，当日尚未同步（先运行 ldst sync）",
    "source_detail": "limit_pool: none（0 条）", "count": 0, "rows": [],
    "data_source_status": "missing"
  }
}
```

缓存有行但一行连板数都解析不出来时，同样是 200 + `available=false` + `rows=[]`，
`reason` 为「涨停池记录缺少可解析的连板数，无法给出涨停样本」。

## 7. 验收项 → 用例

| 契约 | 用例 |
| --- | --- |
| 去重 / unknown 计数 / 排序 | `tests/unit/core/test_limit_pool.py::test_rows_shape_sort_and_dedupe`、`::test_source_detail_counts_dedupe_and_unknown` |
| `streak` 不补 1、缺失值为 `null` | `::test_missing_fields_stay_null_not_defaulted`、`tests/unit/web/test_limit_pool_endpoint.py::test_missing_fields_are_null_not_defaulted` |
| 封板时间原样透传、`trade_date` 不顶替 | `::test_seal_time_passes_through_verbatim` |
| 空缓存 → 200 + `available=false` | `::test_empty_cache_is_unavailable_not_an_error`、`test_limit_pool_endpoint.py::test_empty_cache_is_200_unavailable` |
| 信封两态（`data_as_of` / `confidence` / `source_status`） | `test_limit_pool_endpoint.py::test_success_envelope_and_payload` |
| `date` 缺省 / 非法 → 400；紧凑写法与 `/api/sentiment` 同被接受 | `test_limit_pool_endpoint.py::test_bad_date_is_400`、`::test_compact_date_accepted_like_sentiment` |
| 只读（非 GET → 405）、数据面不加回环独占 | `::test_other_methods_rejected`、`::test_non_loopback_still_served` |
