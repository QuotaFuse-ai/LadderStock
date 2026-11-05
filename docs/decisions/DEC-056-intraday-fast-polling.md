# DEC-056: 盘中高频轮询——间隔触发器 + 秒级快照槽，报价 30s / 资金流 60s

日期：2026-11-05 ｜ 状态：已采纳 ｜ 关联：QUOT-352（本单）、QUOT-345（父单范围 2）、
DEC-006（取数边界）、DEC-035（6 时点快照）、DEC-048（限流退避）、DEC-053/054（实时资金流与槽存储）

编号取值：开工时扫 `origin/main` 的 `docs/decisions/`，最大号为 `DEC-054`，本决策取 `DEC-056`。

## 1. 问题

QUOT-286 的盘中快照是 6 个定点时点（09:25 / 09:35 / 10:00 / 11:30 / 14:00 / 15:00）。
两轮之间盘面读数最长可停 2 小时，离参考产品的「盘中近实时、缓存 ≤15s」差一个数量级。
QUOT-345 把这件事拆为后续项，要求先确认 TickHub 缓存与限流配额能撑住再开工。

2026-11-05 11:5x 盘中实测（4 工具 × T0/+30s/+60s，共 12 次调用）：

| 工具 | T0 | +30s | +60s |
|---|---|---|---|
| `tencent_quote_batch` | miss 0.06s | **hit** 0.01s | miss 0.06s |
| `yesterday_limit_up_pool` | miss 0.08s | **hit** 0.00s | miss 0.08s |
| `market_sentiment` | miss 3.14s | **hit** 0.01s | miss 2.16s |
| `limit_up_pool` | 限流 → stale 兜底 | miss 0.05s | **hit** 0.01s |

结论：**TickHub 缓存 TTL 精确等于 60 秒**（+30s 5/5 命中，+60s 全部 miss）。
一轮完整快照常态 2.5–3.5s，冷拉最坏 ~20s。限流样本复现了 QUOT-349 的软着陆
（`degraded=stale_cache`，调用方拿到可用数据而非失败）。

## 2. 决定

### 2.1 调度表新增「秒级间隔」触发器

`config/schedule.yaml` 的 `trigger` 两种写法共存：

- `HH:MM`：定点 cron，行为不变；
- `Ns`（如 `30s` / `60s`）：APScheduler `interval` 触发器。

同一个 daemon 里两种触发器可以混排；间隔任务用 `max_instances=1` 防重叠，
`misfire_grace_time` 收到一个周期内，避免休眠唤醒后按错过的轮数连环补跑。
`coalesce=True` 保证积压的多轮只补一次。

### 2.2 拆成两条任务，各自只干一件事

| job | kind | 间隔 | 内容 | 为什么是这个频率 |
|---|---|---|---|---|
| `intraday_fast` | `snapshot_fast` | 30s | 昨日涨停池 + 此刻报价（「天梯」关键快照） | 报价走腾讯源（180 次/60s），30s 节拍每轮 4 次工具调用，压力可忽略；缓存 60s，隔轮必命中 |
| `fund_flow_fast` | `sync_batch` | 60s | `stock_fund_flow_batch` 一次取整池（「资金流」） | 东财 push2 端点闸门 3 次/60s + 25s 最小间隔，60s 已是「每轮都出网」的上限节拍；且槽存储本身就是分钟粒度 |

「候选池」没有独立取数：它由上面的天梯读数 + 资金流在读取时本地打分得出，
两条数据源一刷新，候选池的 `fetched_at` 就随之更新。

其余 job（盘前提醒、6 个定点快照、收盘对账、收盘全量）**一律保持定点**，
不跟着提频。

### 2.3 高频快照另起目录，6 个定点时点一个字节都不动

```
data/intraday/<YYYY-MM-DD>/
  ├── 0925.json … 1500.json      # 6 个定点时点（DEC-035，语义不变）
  ├── status.json                # 定点时点状态（不变）
  └── fast/
      ├── 093003.json …          # 30s 级快照，秒级文件名
      └── rounds.jsonl           # 每轮一行观测记录（状态 / 耗时 / 读数条数）
```

- 高频读数**不写** `<HHMM>.json`：定点快照是「那一刻的历史证据」，混写会让
  09:35 的快照被 09:35:30 的读数污染（DEC-035 §2.4 的同一理由）。
- 秒级文件名仍然 `O_CREAT|O_EXCL` 只创建不覆盖；同一秒的并发写入记 `already`。
- 保留策略复用 `purge_batch_slots()`：它按天清理 `data/intraday/<date>/` 下的
  数据集子目录（默认保留最近 3 天），`fast/` 与 `fund_flow_realtime/` 一起被清；
  实测单份快照 ≈ 2.4KB（3 只），150 只约 39KB，单日 480 份 ≈ 18MB，3 天上界 ~55MB。
- `latest_fast_snapshot()` 给读面预留「最新盘面」入口；本次不改 `web/server.py`
  的 6 时点契约。

### 2.4 交易时段门控：时段外一次上游都不打

`09:30–11:30`、`13:00–15:00`（左闭右开），周末直接跳过。时段外返回 `skipped`
且**不落盘、不写日志**——30 秒节拍下一整天写 `skipped` 只会把真正的告警淹掉。
集合竞价（09:15–09:25）仍交给 09:25 定点快照，不做高频采样。

### 2.5 高频轮不取情绪面

`capture(fast=True)` 只打 `yesterday_limit_up` + `spot_quote` 两组工具；
`sentiment` / `limit_pool`（1进2）仍只由 6 个定点时点取。理由：情绪面的价值是
「一个时点的横截面」，30 秒内重复取几乎是同一份数，却要多打两个上游，不划算。

### 2.6 限流退避复用 S2，不改轮询层

高频任务与 `ldst sync` 走同一个 `ProviderRegistry`：QFSD-7001 的
`retry_after_s` 退避、本地缓存回放、`rate_limited` 标记全部沿用 QUOT-349（DEC-048）。
`run_fast()` 把限流折成 `degraded`、把「没有今天读数」折成 `missing`，
**不判 `failed`**；`sync_batch` 把 `sync_one` 的 `degraded/empty` 映射成 `degraded`，
`skipped` 保持 `skipped`。调度器整轮契约（单任务失败只记状态、不中断）不变。

### 2.7 频率依据（为什么是 30s + 60s，不是拍脑袋）

1. **有效新鲜度上限由缓存 TTL 决定，不由节拍决定。** TickHub TTL=60s，30s 采样
   不会比 60s 拿到更多「新」数据；它改善的是「看到新数据的时间偏差」：
   30s 节拍平均滞后缓存刷新 ~15s、最坏 30s；60s 节拍平均 ~30s、最坏 60s。
2. **上游请求数两者相同。** 60s TTL 决定每个工具每分钟最多 1 次真实出网：
   30s 节拍是「命中/未命中」交替（实测 +30s 5/5 命中），60s 节拍则每轮都未命中。
   所以 30s 的报价压力≈60s，但显示滞后减半、限流后恢复窗口从 60s 缩到 30s。
3. **单轮预算够。** 150 只 → 1 次昨日池 + 3 批报价 = 4 次工具调用；实测一轮
   2.5–3.5s，对 30s 周期有 8–12 倍余量；`max_instances=1` 下最坏 20s 的冷拉
   也不会重叠。
4. **资金流单独用 60s。** 批量工具只发 1 次调用，但走东财端点闸门（3 次/60s），
   再叠加 25s 最小间隔；60s 已是每轮出网的上限节拍。分钟槽存储也不支持
   同一分钟写第二份，30s 对它是纯浪费。

## 3. 否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 沿用 6 条 cron，把 `* * * * *` 加到每分钟 | 只能到 60s；30s 无法用现有 cron 表达；且所有 job 混在一张表里没有「只给关键快照提频」的边界 |
| 把所有 job 一起提频 | 收盘对账 / 收盘全量 / 盘前提醒不需要高频，提频只会放大上游压力与失败面 |
| 高频读数覆盖写 `<HHMM>.json` | 改写历史证据，事后无法区分「当时读数」与「最后一次成功读数」（DEC-035 §2.4） |
| 每轮都抓情绪面 | 30 秒内情绪横截面几乎不变，多打两个上游，收益为零 |
| 全量写 Parquet / DuckDB | 30s 一次全量重写 = 写放大 + 跨进程写锁（DEC-054 已否决过的同一条） |
| 直连行情站点补频率 | 违反 DEC-006，且上游不支持；TickHub 缓存就是唯一取数通道 |

## 4. 明确不做

- **守护常驻化 / Windows 自启注册**：属 `D:\QuotaFuseModules\LadderStock\ops\`
  的 QUOT-350 产物，是运维配置，不在本决策与代码仓范围内（见任务记录 §5）。
- 前端「最新高频盘面」展示：本次只落数据与 `latest_fast_snapshot()` 读取口，
  页面入口另立。
- 集合竞价高频采样：上游无可用的竞价过程数据（DEC-035 §2.1）。
- 修改 6 个定点时点的任何语义（窗口、只创建不覆盖、不补跑）。

## 5. 影响

- 新增：`docs/tasks/QUOT-345/QUOT-352-intraday-fast-poll.md`、
  `tests/unit/test_schedule_fast.py`。
- 改动：`src/ladderstock/core/intraday.py`（`TRADING_WINDOWS` / `run_fast` /
  `fast_snapshot_path` / `append_fast_round` / `latest_fast_snapshot`；
  `capture(..., fast=True)`）、`src/ladderstock/cli/schedule.py`（`trigger_interval_seconds`、
  `snapshot_fast` / `sync_batch` 两种 kind、daemon 的 interval 分支）、
  `config/schedule.yaml`（`intraday_fast` / `fund_flow_fast`）、
  `config/schema/schedule.schema.json`（kind 枚举、`Ns` 触发器、`dataset` 条件必填）。
- 运维面：daemon 需常驻并按 `config/schedule.yaml` 运行；间隔任务在时段外静默跳过。
  排查入口是 `data/intraday/<date>/fast/rounds.jsonl`。
- 数据面：`fast/` 目录保留 3 天，与 `fund_flow_realtime/` 同一清理口径。
