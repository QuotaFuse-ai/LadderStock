# SPEC-INTRADAY-READ 盘中 6 时点只读接口

- 版本：v0.1（首版：`GET /api/intraday`、`GET /api/intraday/snapshot` 契约）
- 日期：2026-11-01
- 归属：后端架构（数据面只读接口）
- 实现：`src/ladderstock/web/server.py`（路由 + `LadderEnvelope` + 回环闸门）
- 消费方：前端「盯盘 → 竞价与盘中」页（QUOT-302，本契约是先置条件）
- 落盘口径（**本文件不改口径**）：`SPEC-INTRADAY-SNAPSHOT` / `core/intraday.py`
  —— 窗口、不补跑、只创建不覆盖、`y_streak` 取值顺序全部在采集侧
- 关联决策：DEC-039（本契约的取舍）、DEC-035 §5（它欠的那条读面）、DEC-030（只读接口只做投影）、
  DEC-015 Tier 1（回环独占闸门）、DEC-028（`null` 不当 0）、DEC-034（注册在 SPA 兜底之前）

## 1. 一句话口径

**把已落盘的 `data/intraday/<date>/` 开一条只读通道**：状态汇总读 `status.json`，
快照读文件原文，后端不重算任何指标、不写任何文件、不碰采集。
没有数据 = 200 + `available=false` + 中文 `reason`，不是 404，也不是 500。

## 2. 请求

```
GET /api/intraday?date=YYYY-MM-DD
GET /api/intraday/snapshot?date=YYYY-MM-DD&slot=HH:MM
```

| 参数 | 必填 | 口径 |
| --- | --- | --- |
| `date` | 是 | 缺省与非法均由 `_parse_day` 回 **400**（与 `GET /api/limit-pool`、`/api/sentiment`、`/api/ladder` 同一条口径，不静默当成今天）；紧凑写法 `20261030` 照旧被接受 |
| `slot` | 是（第二支） | 只认 6 个时点 `09:25 / 09:35 / 10:00 / 11:30 / 14:00 / 15:00`，另认落盘文件名的紧凑写法 `HHMM`；其余一律 **400**，`detail` 里列全可选值 |

**回环独占**（DEC-039 §2.4）：来源非回环 → **403** `{"detail":"盘中快照只接受回环来源"}`，
即使 `LDST_WEB_ALLOW_REMOTE=1` + `LDST_WEB_TOKEN` 已配置也一样；`Host` 不在白名单
（DNS rebinding）同样 403。**不受 `LDST_CONTROL_ENABLED` 影响**（关的是写面）。

只读：两支路由只注册 `GET`，其它方法回 405；不写库、不写文件、不取上游。

## 3. 外壳

沿用 `LadderEnvelope`，与 `/api/limit-pool` 完全一致：没有可信数据时 `data_as_of=null`，
**不拿请求日期顶替数据时点**。

| 信封字段 | 有数据 | 无数据 |
| --- | --- | --- |
| `domain` | `"sentiment"` | `"sentiment"` |
| `topic` | `"intraday"` / `"intraday_snapshot"` | 同左 |
| `data_as_of` | 请求日 | **`null`** |
| `confidence` | `"medium"` | `"low"` |
| `source_status` | `"ok"` | `"partial"` |
| `freshness` | `"cached"` | `"cached"` |
| `payload.data_source_status` | `"ok"` | `"missing"` |

`GET /api/intraday` 的「有数据」= 6 个时点里**至少一个时点磁盘上有快照文件**。

## 4. `GET /api/intraday` 的 `payload`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date` | string | 请求日 `YYYY-MM-DD` |
| `available` | bool | 至少一个时点有快照文件 |
| `reason` | string \| null | 不可用时的中文原因（`status.json` 不存在 / 6 个时点逐一列出）；可用时 `null` |
| `has_status_file` | bool | `data/intraday/<date>/status.json` 在不在——「调度器当天没跑过这一天」与「跑了但没抓到」的分界 |
| `slot_count` | int | 恒为 6 |
| `snapshot_count` | int | 有快照文件的时点数（= 下面 `slots[].available` 为 `true` 的行数） |
| `status_updated_at` | string \| null | `status.json` 的 `updated_at` 原样；文件缺失或不可解析为 `null` |
| `slots[]` | array | **固定 6 行**，按 `core/intraday.py:SLOTS` 顺序，含没有记录的时点 |

### 4.1 `slots[]` 每行的字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `slot` | string | 时点名 `HH:MM` |
| `status` | string | `status.json` 里的原值：`ok / degraded / already / skipped / missing / failed`；没有记录时为 `missing`。未知值原样透出，不改写 |
| `recorded` | bool | `status.json` 里**有没有这条记录**。`false` = 这个时点根本没跑过（多半是长驻调度器没起），`true` 而 `status=missing` = 跑过但没有可存的读数 |
| `available` | bool | **磁盘上此刻有没有快照文件**。不照抄 status.json 的 `file`：记着 `ok` 而文件被删 / 换过 `data_dir` 也不谎报 |
| `reason` | string \| null | 没有快照文件时的中文原因；有文件时 `null` |
| `file` | string \| null | 落盘文件名 `<HHMM>.json`，仅在 `available=true` 时给；否则 `null` |
| `detail` | string \| null | `status.json` 里的 `detail` 原样（`skipped` / `missing` / `failed` 的具体原因就在这行字里） |
| `price_field` | string \| null | `open`（仅 `09:25`）或 `price`；`skipped` 等状态没有这个字段 → `null` |
| `stocks` | int \| null | 昨日涨停股只数；没记就是 `null`，**不补 0** |
| `fresh` | int \| null | 其中有当天读数的只数 |
| `at` | string \| null | 该时点写状态的时刻（调度时区，带偏移） |

除 `slot / status / recorded / available / reason / file / detail` 之外，
其余字段一律是 `status.json` 的原值：记录里没有 = `null`，后端不猜、不补默认值（DEC-028）。

## 5. `GET /api/intraday/snapshot` 的 `payload`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date` | string | 请求日 |
| `slot` | string | 归一后的时点名（`1000` 会归一成 `10:00`） |
| `available` | bool | 文件在、能解析、顶层是对象 |
| `reason` | string \| null | 不可用时的中文原因，并指向 `GET /api/intraday?date=` 看逐时点状态 |
| `snapshot` | object \| null | **`<HHMM>.json` 的原文**（`SPEC-INTRADAY-SNAPSHOT` §4 的那个结构），嵌一层，同 `/api/review` 的 `payload.review` |

**一个指标都不重算**：`yesterday_limit_up`（`overall` / `by_board` / `top_board` / `stocks`）、
`sentiment`、`price_field`、`source` 全部是采集侧写进去的东西，逐字节相等。
唯一的加工是全局出口的 `strip_private()` 兜底（`journal` / `positions` / `watchlist` 等
键名即使在文件里出现也不出站）。

## 6. 空态与错误一览

| 情况 | HTTP | 表现 |
| --- | --- | --- |
| 当日 `status.json` 不存在 | 200 | `available=false`、`has_status_file=false`、6 行 `recorded=false` |
| 部分时点缺失（`skipped`/`missing`/`failed` 混排） | 200 | 有文件的行 `available=true`，其余 `false` + `reason` |
| `status.json` 存在但坏了（非 JSON / 顶层不是对象） | 200 | `has_status_file=true`、`available=false`、6 行 `recorded=false` |
| status 记着 `ok` 而快照文件不见了 | 200 | 该行 `status="ok"`、`available=false`、`file=null`、`reason` 说明文件不存在 |
| 快照文件不存在 | 200 | `available=false` + `reason`（指向 `/api/intraday`） |
| 快照文件不可解析 / 顶层不是对象 | 200 | 同上，**不抛 500** |
| 缺 `date` / 非法 `date` | 400 | `{"detail":"缺少 date（YYYY-MM-DD）"}` / `{"detail":"非法日期：…（需要 YYYY-MM-DD）"}` |
| 缺 `slot` / 未知 `slot` | 400 | `{"detail":"缺少 slot（HH:MM，可选：…）"}` / `{"detail":"未知快照时点 '09:45'；可选：…"}` |
| 非回环来源 / `Host` 不在白名单 | 403 | `{"detail":"盘中快照只接受回环来源"}`，响应体里没有任何快照内容 |
| `POST` 等其它方法 | 405 | FastAPI 默认 |

## 7. 原始输出（本机回环，QUOT-286 的桩造的一天）

`2026-10-30` 这一天：`09:25 ok` / `09:35 degraded`（情绪面取数失败）/ `10:00 ok` /
`11:30 missing`（报价的行情日期是昨天）/ `14:00 failed`（peer 拒绝连接）/ `15:00` 一次没跑。

```bash
$ curl -s "http://127.0.0.1:5951/api/intraday?date=2026-10-30"
```

```json
{"domain": "sentiment", "topic": "intraday", "event_id": null, "data_as_of": "2026-10-30",
 "payload": {"date": "2026-10-30", "available": true, "reason": null, "has_status_file": true,
  "slot_count": 6, "snapshot_count": 3, "status_updated_at": "2026-10-30T14:00:00+08:00",
  "slots": [
   {"slot": "09:25", "status": "ok", "recorded": true, "available": true, "reason": null,
    "file": "0925.json", "price_field": "open", "stocks": 3, "fresh": 3,
    "at": "2026-10-30T09:25:00+08:00",
    "detail": "3/3 只昨日涨停股有今天读数，写入 0925.json"},
   {"slot": "09:35", "status": "degraded", "recorded": true, "available": true, "reason": null,
    "file": "0935.json", "price_field": "price", "stocks": 3, "fresh": 3,
    "at": "2026-10-30T09:35:00+08:00",
    "detail": "3/3 只昨日涨停股有今天读数，写入 0935.json"},
   {"slot": "10:00", "status": "ok", "recorded": true, "available": true, "reason": null,
    "file": "1000.json", "price_field": "price", "stocks": 3, "fresh": 3,
    "at": "2026-10-30T10:00:03+08:00",
    "detail": "3/3 只昨日涨停股有今天读数，写入 1000.json"},
   {"slot": "11:30", "status": "missing", "recorded": true, "available": false, "file": null,
    "price_field": null, "stocks": null, "fresh": null, "at": "2026-10-30T11:30:00+08:00",
    "reason": "3 只昨日涨停股没有一条今天（2026-10-30）的报价：行情日期不是今天（节假日回节前收盘价）或上游未更新",
    "detail": "3 只昨日涨停股没有一条今天（2026-10-30）的报价：行情日期不是今天（节假日回节前收盘价）或上游未更新"},
   {"slot": "14:00", "status": "failed", "recorded": true, "available": false, "file": null,
    "price_field": null, "stocks": null, "fresh": null, "at": "2026-10-30T14:00:00+08:00",
    "reason": "快照抓取失败：peer 拒绝连接", "detail": "快照抓取失败：peer 拒绝连接"},
   {"slot": "15:00", "status": "missing", "recorded": false, "available": false, "file": null,
    "price_field": null, "stocks": null, "fresh": null, "at": null, "detail": null,
    "reason": "status.json 里没有 15:00 的记录：该时点没跑过，或没落进有效窗口 15:00:00–15:07:59（不补跑，见 DEC-035 §2.3）"}],
   "data_source_status": "ok"},
 "source": "ladderstock", "source_detail": "local intraday dir: 2026-10-30/（3/6 个时点有快照）",
 "fetched_at": "2026-11-01T06:03:22.238635Z", "freshness": "cached", "confidence": "medium",
 "source_status": "ok"}
```

（上面为便于阅读做了缩进与换行，字段与值未经改动；下面几条是原样输出。）

```bash
$ curl -s "http://127.0.0.1:5951/api/intraday/snapshot?date=2026-10-30&slot=15:00"
{"domain":"sentiment","topic":"intraday_snapshot","event_id":null,"data_as_of":null,"payload":{"date":"2026-10-30","slot":"15:00","available":false,"reason":"1500.json 不存在：2026-10-30 的 15:00 没有落盘快照（逐时点状态见 GET /api/intraday?date=2026-10-30）","snapshot":null,"data_source_status":"missing"},"source":"ladderstock","source_detail":"local intraday dir: 2026-10-30/1500.json 不存在","fetched_at":"2026-11-01T06:03:22.303907Z","freshness":"cached","confidence":"low","source_status":"partial"}

$ curl -s "http://127.0.0.1:5951/api/intraday/snapshot?date=2026-10-30&slot=09:25" | head -c 400
{"domain":"sentiment","topic":"intraday_snapshot","event_id":null,"data_as_of":"2026-10-30","payload":{"date":"2026-10-30","slot":"09:25","available":true,"reason":null,"snapshot":{"date":"2026-10-30","slot":"09:25","captured_at":"2026-10-30T09:25:00+08:00","price_field":"open","source":{"dataset":"spot_quote","tool":"tencent_quote_batch","source_detail":"spot_quote_stub@stub","yesterday_limit_up":"yesterday_limit_up_stub@stub","trail":["spot_quote_stub@stub"]},"yesterday_limit_up":{"count":3,"fresh":3,

$ curl -s "http://127.0.0.1:5951/api/intraday?date=not-a-day"
{"detail":"非法日期：'not-a-day'（需要 YYYY-MM-DD）"}

$ curl -s "http://127.0.0.1:5951/api/intraday/snapshot?date=2026-10-30&slot=09:45"
{"detail":"未知快照时点 '09:45'；可选：09:25、09:35、10:00、11:30、14:00、15:00"}

$ curl -s "http://127.0.0.1:5951/api/intraday?date=2026-11-11" | head -c 300
{"domain":"sentiment","topic":"intraday","event_id":null,"data_as_of":null,"payload":{"date":"2026-11-11","available":false,"reason":"status.json 不存在：这台机器上没有 2026-11-11 的快照记录（调度器未运行过这一天，或 data_dir 不是这个）","has_status_file":false,"slot_count":6,"snapshot_count":0,

$ curl -s -H "Host: evil.example.com" "http://127.0.0.1:5951/api/intraday?date=2026-10-30"
{"detail":"盘中快照只接受回环来源"}
```

## 8. 验收对照

| 要求 | 用例（`tests/unit/web/test_intraday_endpoints.py`） |
| --- | --- |
| 6 个时点与 `status.json` 逐字段一致 | `test_summary_matches_status_json_field_by_field`、`test_summary_reads_status_json_not_the_snapshot_bodies` |
| 有数据 / 无数据 / 部分缺失混排 | 上一行 + `test_empty_root_is_200_unavailable`、`test_mixed_statuses_degraded_and_missing` |
| 缺文件不谎报、坏文件不 500 | `test_status_record_without_snapshot_file_is_not_claimed`、`test_corrupt_status_json_is_200_not_500`、`test_unreadable_snapshot_is_200_not_500` |
| 快照原文不重算 | `test_snapshot_returns_file_body_verbatim`、`test_snapshot_strips_private_keys` |
| 参数 400（date / slot） | `test_bad_date_is_400`、`test_unknown_slot_is_400`、`test_missing_slot_is_400`、`test_compact_slot_accepted` |
| 非回环 403、DNS rebinding 403 | `test_non_loopback_source_is_403` |
| 不受 `LDST_CONTROL_ENABLED` 影响 | `test_control_switch_does_not_close_intraday` |
| 只 GET、注册在 SPA 兜底之前、不写文件 | `test_other_methods_rejected`、`test_routes_take_precedence_over_spa_fallback`、`test_get_writes_nothing` |

## 9. 明确不做

- 不给 `date` 缺省值（不静默当成今天），也不给「有哪些天」的目录列表；
- 不在读面重算 `avg_change_pct` / 分板位 / 1进2——采集侧算过一次，第二份算法迟早漂移；
- 不做前端页面（QUOT-302）；
- 不新增采集入口，也不提供「按日期补抓」的口子（DEC-035 §2.3）。
