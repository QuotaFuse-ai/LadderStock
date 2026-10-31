# 盘中 6 时点快照（QUOT-286）

- 状态：已实现（2026-11-01）
- 关联：DEC-006（生态边界）、DEC-007（同步与缓存）、`docs/SOURCES.md` §2
- 实现：`src/ladderstock/core/intraday.py`、`src/ladderstock/cli/schedule.py`
- 一句话：**当天当场**把盘面读数存成一个 JSON；不补跑、不覆盖、不用今天的价格回填过去。

## 1. 目标与边界

| 做 | 不做 |
|---|---|
| 6 个固定时点各存一份快照 | 历史补跑（即使带 date 参数） |
| 昨日涨停股 × 此刻报价的市场级聚合 | 新增直连行情站点 / 新采集入口 |
| 09:35 起附情绪面（涨停/炸板/跌停/最高板/炸板率/1进2） | 集合竞价 9:15–9:20 过程（上游无源，不摆占位） |
| 逐只股票的**客观读数**（价、涨跌幅、是否封板） | 个股排序、评分、推荐 |

数据只来自生态 peer（DEC-006）：`tencent_quote_batch`（TickHub-CN）、
`yesterday_limit_up_pool`、`market_sentiment`、`limit_up_pool`。快照**不进 sync 台账**
——`spot_quote` 只登记在 `config/sources.yaml`，链路走
`ProviderRegistry.fetch_live()`（只按 sources.yaml 组装，主备同 peer 校验不变）。

### 1.1 为什么 `spot_quote` 不进 `sync.yaml`

实时报价没有「一天一条」的交易日水位。进 sync.yaml 会被 `ldst sync` 当成按日
增量落库的数据集，而它每 5 分钟就该是不同的一份读数。`DATASET_CATALOG` 里仍登记
`mode=replace`、`watermark_field=timestamp`，但那条水位只描述单次响应内的行情时间
（Tencent `YYYYMMDDHHMMSS`），**不是**交易日水位；快照的日期校验另走 `quote_day()`。

## 2. 时点与窗口

窗口按 `scheduler.timezone`（默认 `Asia/Shanghai`）的本地时间判定，左闭右开：

| 时点 | 有效窗口 | 读数口径 |
|---|---|---|
| `09:25` | `09:25:00` – `09:29:59` | 开盘价（集合竞价成交价），涨跌幅按开盘价重算 |
| `09:35` | `09:35:00` – `09:42:59` | 最新价；附情绪面 |
| `10:00` | `10:00:00` – `10:07:59` | 最新价；附情绪面 |
| `11:30` | `11:30:00` – `11:37:59` | 最新价（上午收盘）；附情绪面 |
| `14:00` | `14:00:00` – `14:07:59` | 最新价；附情绪面 |
| `15:00` | `15:00:00` – `15:07:59` | 最新价（收盘）；附情绪面 |

- `09:25` 的窗口在 09:30 截止：9:30 起进入连续竞价，竞价读数已经不存在；
- 其余时点给 8 分钟余量（misfire / 抓取耗时）；
- **窗口外一律 `skipped`，不补跑**：用今天的价格给过去的日子写快照会永久污染数据，
  且从文件里看不出异样；
- 周末直接 `skipped`（`day.weekday() >= 5`）。

## 3. 存储

```
data/intraday/<YYYY-MM-DD>/
  ├── 0925.json / 0935.json / 1000.json / 1130.json / 1400.json / 1500.json
  └── status.json
```

- 快照用 `O_CREAT|O_EXCL` **只创建不覆盖**（与 vibe-astock 的「临时文件 + linkSync」
  等价）：文件已存在即记 `already`，内容一个字节都不动，也不再请求 peer；
- `status.json` 每次原子替换（临时文件 + `os.replace`），逐时点记录：

```json
{
  "date": "2026-10-30",
  "updated_at": "2026-10-30T10:00:06+08:00",
  "slots": {
    "10:00": {
      "status": "ok",
      "at": "2026-10-30T10:00:06+08:00",
      "detail": "3/3 只昨日涨停股有今天读数，写入 1000.json",
      "file": "1000.json",
      "price_field": "price",
      "stocks": 3,
      "fresh": 3
    }
  }
}
```

`status` 取值：

| 值 | 含义 |
|---|---|
| `ok` | 快照已写入，读数为当天 |
| `degraded` | 快照已写入，但有降级（情绪面缺、部分报价走备源等） |
| `already` | 文件已存在，未覆盖 |
| `skipped` | 窗口外 / 周末 / 不在补跑 |
| `missing` | 没有可存的读数（行情日期不是今天、昨日池为空、上游未更新） |
| `failed` | 抓取抛错（例如 peer 拒绝连接），只写 status.json |

## 4. 快照内容

```json
{
  "date": "2026-10-30",
  "slot": "10:00",
  "captured_at": "2026-10-30T10:00:06+08:00",
  "price_field": "price",
  "source": {"dataset": "spot_quote", "tool": "tencent_quote_batch", "trail": ["..."]},
  "yesterday_limit_up": {
    "count": 3, "fresh": 3, "stale": 0, "missing": [],
    "overall": {"count": 3, "avg_change_pct": 2.43, "up": 2, "flat": 0, "down": 1, "limit_up": 1},
    "by_board": [{"board": "首板", "count": 2, "...": "..."}, {"board": "三板及以上", "count": 1}],
    "top_board": {"streak": 4, "stocks": [{"code": "001219", "reading": 19.94, "...": "..."}]},
    "stocks": [{"code": "...", "yesterday_streak": 1, "reading_field": "price", "reading": 7.92,
                "change_pct": -3.77, "at_limit_up": false, "fresh": true, "reason": null}]
  },
  "sentiment": {
    "available": true, "limit_up": 47, "broken": 31, "limit_down": 82,
    "max_streak": 5, "seal_rate_pct": 60.3, "broken_rate_pct": 39.7,
    "one_to_two": {"available": true, "yesterday_first_board": 2,
                   "today_second_board": 2, "promoted": 0, "rate_pct": 0.0}
  }
}
```

口径：

- 昨日连板数读 `y_streak`（**不是** `limit_up_days`——后者在该工具里含今日封板数）；
  上游没给就记 `未知`，不拿别的字段顶替；
- `at_limit_up` 只在报价同时给出 `limit_up` 时才是布尔值，否则 `null`（不拿涨幅阈值猜）；
- `by_board` 只给聚合（`首板/二板/三板及以上/未知`），空分组不输出；
- `09:25` 的 `reading` 取 `open`、`reading_field="open"`，涨跌幅按开盘价重算；
- 情绪面里的日期字段与信封 `data_as_of` 都必须等于今天，否则 `available=false`；
- 单只股票取不到报价 ≠ 整只丢弃：记 `fresh=false` 与 `reason`，聚合只用 fresh 行。

## 5. 校验：日期必须等于今天

Tencent 在节假日会回**节前收盘价**，数字看起来完全合理。所以：

1. 行情行内 `timestamp`（`YYYYMMDDHHMMSS`）的日期必须等于今天，否则该行 `stale`；
2. 一条 fresh 都没有 → `missing`，不写快照；
3. 昨日涨停池 / 情绪面的 `data_as_of` 不是今天 → `missing` / `available=false`；
4. 非交易日不跑（周末直接 `skipped`；节假日的兜底就是上面三条）。

## 6. 调度接入

`config/schedule.yaml` 增 6 条 `kind: snapshot`，`trigger` 即时点
（`09:25 … 15:00`，id 为 `snapshot_HHMM`）。`run_job` 把 `trigger` 当时点、把
`scheduler.timezone` 传下去；窗口判定全部在 `core.intraday.run_slot()`，调度器只负责
准点触发。窗口外的一次触发也写 `status.json`，便于复盘「为什么这个时点没有文件」。

schema：`config/schema/schedule.schema.json` 的 `kind` 枚举加入 `snapshot`；
`cli/schedule.py:_structural_problems` 的白名单同步扩展。

## 7. 验收对照

| 要求 | 用例 |
|---|---|
| 6 个窗口边界（含 09:29 接受 / 09:30 拒绝） | `tests/unit/core/test_intraday.py::test_slot_window_is_left_closed_right_open`、`test_auction_window_rejects_continuous_trading` |
| 窗口外不落快照 | `test_outside_window_skips_without_snapshot` |
| 已存在不覆盖 | `test_write_new_json_never_overwrites`、`test_existing_snapshot_is_reported_as_already` |
| 行情日期不是今天不落快照 | `test_quotes_from_another_day_are_not_stored` |
| 抓取失败写 status.json、无快照 | `test_fetch_failure_writes_status_but_no_snapshot` |
| `spot_quote` 边界拦截 / 不进台账 | `test_spot_quote_is_owned_by_tickhub_and_stays_out_of_the_ledger` 等 |
| 调度表 6 条 job 与 schema | `tests/unit/test_schedule_snapshot.py` |
| `tencent_quote_batch` 形状（`quotes` 是对象） | `tests/contract/test_tickhub_payload_mapping.py` |

## 8. 明确不做

- 历史补跑（哪怕命令带 `--date`）：窗口外只记 `skipped`；
- 9:15–9:20 集合竞价过程：上游没有这个源，不摆占位字段；
- 新增直连行情站点或采集通道（DEC-006）；
- 对个股做排序 / 评分 / 推荐——快照只存客观读数。
