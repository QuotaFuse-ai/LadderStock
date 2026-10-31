# SPEC-MARKET-SCREEN-API 市场屏两段只读接口

- 版本：v0.2（2026-11-01：空态文案微调，字段 / 状态码 / 表头不变）
- 日期：2026-11-01
- 归属：后端架构（数据面只读接口）
- 实现：`src/ladderstock/web/server.py`（路由 + 投影 + 回环闸门），
  取数复用 `core/sentiment.py::compute_sentiment`、`data/archive.py::has_partition`、
  `core/validation.py::session_days`、`core/ladder.py::dataset_snapshot`
- 消费方：前端「市场」屏 `/market` 的 CH-01 情绪温度折线、CH-03 题材轮动
  （`frontend/src/screens/MarketScreen.tsx`、`frontend/src/api.ts:marketHistory/marketTheme`）
- 需求来源：QUOT-199（Q172-8），被 QUOT-172 / Q172-7 消费
- 关联决策：DEC-031（本文件的取舍与理由）、DEC-030（只读接口只投影不重算）、
  DEC-015（回环独占闸门）、DEC-008 §4（资讯不产数值）、DEC-028（`null` 不当 0）、
  DEC-006（程序直出，AI 不参与数字）

## 0. 一句话口径

**把本机已经算得出的东西原样排成序列**：CH-01 = 逐日 `compute_sentiment` 的可用点 +
两个派生指标（分位、较昨日）；CH-03 = Wire `theme_news` 记录的白名单投影。
不采集、不重算、不补 0、不猜节假日；算不出来就是 `available=false` + 一句「为什么」。

两个接口都：**只 GET / 回环独占 / `Cache-Control: no-store` / 无副作用**（不触发 `sync`、
不触发 `reconcile`，不写任何文件）。

## 1. `GET /api/market/history` — 近 N 日情绪温度序列

```
GET /api/market/history?days=20&to=2026-10-23
GET /api/market/history?date=2026-10-23&window=20     # 前端 api.ts 的名字（别名）
```

### 1.1 参数

| 参数 | 本名 | 别名 | 必填 | 口径 |
| --- | --- | --- | --- | --- |
| 窗口天数 | `days` | `window` | 否 | 整数，默认 **20**；越界**钳制**到 1–60（`999` → 60，`0`/`-5` → 1）；非整数 → **400** |
| 窗口末日 | `to` | `date` | 否 | 严格 `^\d{4}-\d{2}-\d{2}$`；缺省 = 本机今天；非法 → **400** |

本名与别名同时给出时**以本名为准**（`days` 赢 `window`，`to` 赢 `date`）。

只读：其它方法 405（`Allow: GET`）；非回环来源或 `Host` 不在白名单 → **403**
（`_journal_guard`，同 DEC-015 §3）；`LDST_CONTROL_ENABLED=0` 不影响本接口。

### 1.2 交易日窗口怎么定（DEC-031 §2.1）

1. 从 `to` 往前逐日看，跳过周末，取**本机有 `limit_pool` Parquet 日分区**的日期，
   够 `days` 个就停（回看步数上界 `2 × days + 30`）；
2. 一条分区都没有时退回 `session_days()`（DuckDB 全表扫描）。

所以窗口宽度**不会超过本机缓存**：缓存 4 天就只有 4 个点，`source_detail` 会写
`limit_pool 缓存: 4/20 个交易日（2026-10-20~2026-10-23）`。
法定节假日不进窗口（那天本来没有涨停池），代价是「缺数据」与「不开市」在
`missing_days` 里同形——所以务必连窗口覆盖一起看。

### 1.3 响应（`LadderEnvelope` + 顶层 `available`）

```json
{
  "domain": "sentiment",
  "topic": "shortline_sentiment_history",
  "data_as_of": "2026-10-23",
  "available": true,
  "source": "ladderstock",
  "source_detail": "limit_pool 缓存: 5/5 个交易日（2026-10-19~2026-10-23）; 可用点: 5",
  "fetched_at": "2026-10-31T15:40:02Z",
  "freshness": "cached",
  "confidence": "medium",
  "source_status": "ok",
  "payload": {
    "date": "2026-10-23",
    "available": true,
    "reason": null,
    "window": 20,
    "points": [
      {"date": "2026-10-20", "temperature": 58.3, "promotion_rate": 42.1, "limit_up_count": 61}
    ],
    "missing_days": ["2026-10-21"],
    "quantile": 68.0,
    "delta_vs_prev": 4.2,
    "data_source_status": "ok"
  }
}
```

| 字段 | 类型 | 口径 |
| --- | --- | --- |
| `available`（顶层） | bool | = `payload.available` = **可用点 ≥ 2**。单点画不出折线，不给前端留「一个点也连成线」的空间 |
| `data_as_of` | date\|null | 有数 = 最后一个可用日；无数 = `null`，**不拿请求日期顶替**（D-02） |
| `payload.date` | string | 最后一个可用日；一个点都没有时 = 请求的窗口末日 |
| `payload.available` | bool | 前端 `MarketHistoryPayload.available` 在读 |
| `payload.reason` | string\|null | 可用时 `null`；空态时一句话说明三种原因之一（没缓存 / 全算不出 / 只剩 1 个点） |
| `payload.window` | int | 钳制后的天数，供文案「近 N 日」 |
| `payload.points[]` | array | **升序**；每个点只有 `date` / `temperature` / `promotion_rate` / `limit_up_count` 四个键，与 `compute_sentiment` 同源同值（比率与温度都是 0–100 百分数，1 位小数） |
| `payload.missing_days[]` | array | 窗口内**算不出情绪**的日子（升序，`YYYY-MM-DD`）；同时写进 `source_detail` |
| `payload.quantile` | float\|null | 窗口内 `temperature ≤ 最后一个可用日` 的点数 / 总点数 × 100，1 位小数；点数 < 2 → `null` |
| `payload.delta_vs_prev` | float\|null | 最后一个可用日 − 倒数第二个可用日，1 位小数；点数 < 2 → `null` |

**空态形状完全一致**：`points=[]`、`missing_days=[]`、`quantile=null`、`delta_vs_prev=null`、
`data_source_status="missing"`、`source_status="partial"`、`confidence="low"`——
键不会少，值不会用 0 冒充。

## 2. `GET /api/market/theme` — 题材强度轮动

```
GET /api/market/theme?date=2026-10-23
```

| 参数 | 必填 | 口径 |
| --- | --- | --- |
| `date` | 否 | 严格 `YYYY-MM-DD`；缺省 = 本机今天；非法 → **400** |

数据源**只有一个**：`dataset_snapshot("theme_news", day, root)`，即 QuotaFuse Wire 的
`theme_news`（`core/boundary.py` 登记为可选消费，主工具 `wire_get_digest`、备源
`wire_get_timeline`）。不借 `industry_board` / `concept_board` 的涨跌幅折算强度，
也不用资讯条数当热度（DEC-031 §2.4）。

```json
{
  "domain": "sentiment",
  "topic": "theme_rotation",
  "data_as_of": null,
  "available": false,
  "source_detail": "theme_news 未接入（QuotaFuse Wire），先配置 qfw：缓存后端 none，2026-10-31 没有 theme_news 记录（配置后运行 ldst sync --dataset theme_news）",
  "source_status": "partial",
  "payload": {
    "date": "2026-10-31",
    "available": false,
    "reason": "theme_news 未接入（QuotaFuse Wire），先配置 qfw：缓存后端 none，2026-10-31 没有 theme_news 记录（配置后运行 ldst sync --dataset theme_news）",
    "themes": [],
    "data_source_status": "missing"
  }
}
```

| 字段 | 类型 | 口径 |
| --- | --- | --- |
| `payload.themes[]` | array | **按 `strength` 降序**，无强度的排最后，最多 **20** 条；每行只有五个键 |
| `theme` | string | 题材名（记录里没有题材名的行**直接丢弃**，不并入「未知题材」） |
| `strength` | float\|null | 记录自带的强度分（别名：`strength` / `score` / `heat` / `intensity` / 强度 / 热度）；同题材多条取**最大**；没有就是 `null`，**不折算、不猜** |
| `limit_up_count` | int\|null | 题材内涨停家数（`limit_up_count` / `limit_up_cnt` / 涨停家数 / 涨停数），取最大；没有 → `null` |
| `leader` | string\|null | 龙头（`leader` / `leader_name` / 龙头 / 龙头股），否则用 `leader_code` + `龙头名称` 拼成 `300001 某某科技`；都取不到 → `null` |
| `delta` | float\|null | 较前一日的强度变化（`delta` / `strength_delta` / `score_delta` / `change` / 变化），取第一条非空；没有 → `null` |

`payload.available` / `payload.reason` 与顶层 `available` 同步（语义同上）。

**出境白名单是硬边界**：`theme_news` 记录里的正文、摘要、标题、链接（`summary` /
`content` / `title` / `url` / 正文 / 摘要 / 链接 …）**一律不进响应**——投影只拼五个键，
再加一遍递归剔除兜底，最后 `strip_private()` 恒等由测试钉住（DEC-008 §4：资讯只做叙事）。

## 3. 空态矩阵（前端两段共用）

| 情况 | HTTP | `available` | 关键字段 | 前端应做 |
| --- | --- | --- | --- | --- |
| 缓存为空（没跑过 `ldst sync`） | 200 | false | `points=[]`、`reason` 说明没有缓存 | 「数据未同步」+ 引导 `ldst sync` |
| 只有 1 个可用点 | 200 | false | `points` 1 个、`quantile`/`delta_vs_prev` = `null` | 走当日单点（`/api/sentiment`），不画折线 |
| 窗口内部分日算不出 | 200 | true（≥2 点时） | `missing_days` 点名、`source_detail` 同带 | 折线断点，别补 0 |
| `days` 越界 | 200 | 视数据 | `payload.window` = 钳制后的值 | 直接用 `payload.window` 写文案 |
| 日期 / `days` 格式非法 | 400 | — | `detail` 一句话 | 视为调用方 bug |
| 非回环来源 / `Host` 不在白名单 | 403 | — | `detail` = 「日志只接受回环来源」 | 不重试，提示只能在机上读 |
| 用 `POST` 打 | 405 | — | `Allow: GET` | — |
| Wire 未接入（当前默认状态） | 200 | false | `themes=[]`、`reason` 含「先配置 qfw」 | 「数据未同步」，**不是错误** |

## 4. 已知边界（如实，不留到线上才发现）

1. **CH-03 上线即为空态**：接入 QuotaFuse Wire（`qfw`）是配置 / 账号动作，需要人类批准，
   不在 QUOT-199 范围内。真实 `theme_news` 的字段名以接入后第一条数据为准复核——
   本文件的别名表按 `wire_get_digest` 的常见形状列，`strength` 是否真给、给的是什么量纲，
   接入前无法证实。若 Wire 不提供任何数值强度，本接口就长期只出 `theme` / `limit_up_count`，
   「题材强度」这一段要么等上游、要么改口径（届时更新本文件并记录变更）。
2. **序列长度受缓存限制**：`days=60` 不等于一定有 60 个点。
3. **窗口成本**：每个点是一次 `compute_sentiment`（三张池快照 + 天梯），60 日最坏情况
   约 180 次缓存读；只读、无并发写，回环独占，不给远端调用。
4. `payload.available` 与顶层 `available` 是同一结论的两份写法（前端读前者、契约读后者），
   收敛时先收前端。
5. 前端类型 `MarketThemeRow.strength: number` 应为 `number | null`（后端可能给 `null`）；
   前端已按有限数过滤，功能上不受影响，属类型如实性问题，交由前端目录处理。

## 5. 变更记录

- v0.1（2026-10-31）：首版。两接口参数表、交易日窗口口径、分位与较昨日的算法、
  题材字段别名与白名单、空态矩阵、已知边界。理由与取舍见 DEC-031。
- v0.2（2026-11-01）：实机冒烟（真 uvicorn + 真 HTTP）后发现「未接入」文案里
  「后端 none 在 … 没有记录」读起来像故障，改为「缓存后端 none，… 没有 theme_news 记录」，
  §2 的示例同步。字段、状态码、`no-store` 均未变。
