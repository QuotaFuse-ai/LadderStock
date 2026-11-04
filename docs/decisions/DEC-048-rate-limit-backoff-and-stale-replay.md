# DEC-048 上游限流（QFSD-7001）：按建议时长有界退避，退不到就用带标记的昨日池

- 日期：2026-11-03
- 提出：后端架构师（QUOT-349，QUOT-345 范围 3 健壮性）
- 状态：生效
- 编号：`DEC-048`。**让号来源**：开单时按 DEC-045 §2.1 取值 `DEC-046`（origin/main `8436dc1`
  已扫到的最大号 = 045），合并时 main 上 `DEC-046` 已有两份实体——QUOT-344 的
  `DEC-046-review-nav-three-independent-entries.md`（反向引用十几处，保留原号）与 QUOT-347 的
  `DEC-046-schedule-close-sync-job.md`。按 DEC-045 §2.3「让号成本由零反向引用的一方承担」，
  本决策零反向引用，让号至 `DEC-048`；合并前重扫 `git ls-tree -r --name-only origin/main docs/decisions/`
  最大号 = 046，047 让给同批的 `DEC-047-schedule-close-sync-job.md`（原 DEC-046），本决策取 048。
  裁定来源：QUOT-349 收口审计意见（2026-11-03）。
- 关联：DEC-007 §2.4（请求治理：短 TTL 缓存 / 失败冷却 / 并发与最小间隔 / 有界重试）、
  DEC-006（生态边界：不自建采集、主备同 peer）、DEC-035（盘中 6 时点快照，写一次、不补跑）、
  DEC-043（`retry_after_s` 与冷却同为「跨轮」闸门的另一头）、DEC-034 同族口径（缺数如实标记）、
  `docs/SOURCES.md` §2、`docs/CLI.md` §sync / §schedule、QUOT-233（`data_as_of` 说真话）、
  QUOT-286（快照状态机 ok/degraded/already/skipped/missing/failed）

## 1. 背景与实测根因

上游 TickHub-CN 命中自身限流/熔断时回 `QFSD-7001 rate_limited`，`retryable=true`，
建议等待时长放在 `error.detail.retry_after_s`（整秒）——
`TickHub-CN/src/tickhub_cn/core/http.py` 的三条抛出路径（`endpoint_breaker` /
`endpoint_paced` / 连接重置后熔断）都是这个形状，`core/errors.py:21` 给出 `name`/`action`。

改动前 LadderStock 侧的实际行为（逐条对着代码复核，不是推测）：

1. `ok:false` 一律走同一句「TickHub-CN {tool} 返回失败」→ 与 `QFSD-9001`（入参错）、
   `QFSD-1001`（超时）**同一种失败**，`retry_after_s` 直接丢掉；
2. 冷却时长照抄 `sources.yaml` 的 `cooldown_sec`，上游说「8 秒后再来」也照样按 60 秒关门，
   反之上游说「600 秒」我们也 60 秒后再撞一次；
3. 缓存回放这条路**已经存在**（链路全挂 → `archive.snapshot()` 回最近一天），
   但回放出来的信封只说 `freshness=cached`，**不说自己是被限流挤下来的、也不说数据是哪一天的**；
4. 盘中快照（DEC-035）要求昨日池的 `data_as_of == 今天`，日期不符一律判 `missing`——
   一次 pacing 于是把整轮变成「今天没有数据」，看板显示空白，`ldst schedule` 的日志里
   看起来像任务没跑成。

也就是说：缺的不是「降级」这条路，而是**认得出限流、等得起、并且把降级说清楚**。

## 2. 决定

### 2.1 识别只放在适配器层，其它层不再看错误码

`src/ladderstock/data/adapters/tickhub.py` 新增 `classify_failure()`：
`QFSD-7001`（含早期草稿别名 `QF-7001`）或 `error.name == "rate_limited"` → 抛 `RateLimited`，
把建议时长与上游原话一起带出；其它 `ok:false` 口径**一字不改**。
`retry_after_s()` 只接受 `0 ≤ 秒数 ≤ 86400` 的数字（`error.detail.retry_after_s` →
`error.retry_after_s` → 顶层），布尔、负数、字符串、荒谬值一律回 `None`。

理由：错误码是**上游契约**，只有适配器该知道它。注册表与 CLI 此后只认
`RateLimited` / `SourceUnavailable.rate_limited` 这两个内部事实，将来 wire / senate
若要接同样的语义，改的是它们自己的适配器，不是治理层。

### 2.2 退避预算：一次取数共享，次数与单次时长都有硬上限

`sync.rate_limit`（`config/sync.yaml`，schema 校验）：

```yaml
rate_limit:
  max_retries: 2          # 一次取数最多等几次
  max_wait_sec: 15        # 单次最多等多久；等不起就不等
  max_cooldown_sec: 900   # 冷却封顶，别让一个坏建议把数据集冻住
```

- **预算挂在「一次取数」上，不是挂在工具上**：主源把两次用完，备源就一次都不等，
  直接切过去报错。最坏耗时 = `max_retries × max_wait_sec` = 30 秒，与链路长度无关；
- **等不起就不假装在等**：`retry_after_s` 缺失或 > `max_wait_sec` → 立刻按失败处理并降级，
  但冷却时长按上游说的来（`max(retry_after_s, cooldown_sec)`，封顶 `max_cooldown_sec`）；
- **等待放在 peer 闸门之外**：攥着并发信号量睡觉会把同 peer 的其它取数一起拖住；
- **退避成功后清掉该源的冷却**：这一路刚证明自己通，继续背着 60 秒闸门就是
  「明明能取却被 cooldown 跳过」，pacing 越频繁越明显。

### 2.3 降级产物：昨日池 + 明确的 stale 标记，绝不冒充当天

链路全挂后有本地缓存 → 回放，并额外写
`payload.stale = {as_of, requested_as_of, reason: rate_limited|peer_unavailable, cached_at}`，
`source_detail` 从「peer 全部不可用」改成
**「限流降级，回放本地昨日池（backend，数据日 X）；轨迹 …」**。
台账侧 `status=degraded`、**不重写数据、不推进水位**（`degraded` 本来就在
`GOOD_STATUSES` 里，退出码保持 0——只有 `failed` 才会翻成 4/8）。

### 2.4 盘中快照：旧池子只有一种情况可以用，且必须写明是哪天的

`capture()` 接受 `data_as_of != 今天` 的昨日池，**当且仅当** `from_cache and rate_limited`，
并在快照里写 `source.pool_stale = {as_of, reason, source_detail}`，`status.json` 同步带这一项。
其它日期不符（上游发了别的日子、缓存里是旧一轮的账）照旧判 `missing`：
用别的 cohort 冒充今天的读数会永久污染快照，从文件里也看不出异样（DEC-035 的不变量）。
批量报价整批被限流打回时，`degraded` 但**不写快照**——一条今天的读数都没有，写了就是污染。

**消费方口径（合并时补，QUOT-366 验收项）**：快照顶层再写一处
`meta = {degraded, pool_as_of}`——限流回放时 `degraded="stale_cache"`、`pool_as_of` 是昨日；
有当天池子时 `degraded=null`、`pool_as_of` 就是今天。看板不必去翻嵌套的 `source.pool_stale`
是否存在，读 `meta` 一处就能同时拿到「这批数据是哪天的」和「它是不是降级来的」；
这一对值互斥且总在场，正是为了不让消费方靠「字段缺失」去猜。

### 2.5 冷却期里的跳过也算「还在 pacing 窗口里」

`HealthLog.set_cooldown()` 记了 `reason`；本轮如果某个 source 因为**上一轮的限流**在冷却，
一次调用都没发出去，`_resolve` 也把它算作 pacing（`rate_limited=True`）→ 判 `degraded`
而不是 `failed` / `missing`。仿真里正是这一条把 10:00 快照从「missing（今天没数据）」
纠正成「degraded + 昨日池 2026-11-02」。

### 2.6 观测：限流是单独一类事件

`sources health` 的 `kind` 增加 `rate_limited`（与 `failure` 并列），事件 detail 带
`QFSD-7001` 与建议时长；退避成功那一路也记一条「退避 X 秒后重试」，
否则轨迹只看到一路顺利、看不出这一轮慢在哪。

## 3. 不做什么（以及为什么）

- **不引入退避库**（tenacity 之类）：项目依赖是冻结口径（`requirements.lock` + schema 治理），
  而这里要的只是「最多等两次、每次最多 15 秒、等不起就走」，二十行自己写清楚更好审计。
- **不改上游限流策略**：那是另一个仓库（TickHub-CN）。本决策只消费它给出的
  `retry_after_s`，不要求它给得更准、也不依赖它以后一定给（给不出就按 §2.2 降级）。
- **不做「换个工具绕开限流」**：同 peer 的备源在同一个限流窗口里，把等待换成两次失败
  只是把慢变成吵（DEC-006 闸门 3 同族口径）。
- **不把限流判成任务失败**：调度契约是「单任务失败只记 degraded / skipped，不中断整轮」，
  pacing 不是崩溃；把它记成 `failed` 会让真正的崩溃淹没在噪声里。

## 4. 影响

- 盘中高频轮询（QUOT-345 范围 2，S5 的 30–60 秒节奏）可以直接复用这条路：
  单次取数最坏 30 秒是**配置算得出来的上界**，觉得紧了把 `max_retries` 调到 1 即可，
  不需要动代码。
- 看板在限流期间显示的是「上一批数据 + 数据时点」，不是空白；`stale` 是载荷里的字段，
  前端不需要猜（QUOT-311/313 那批屏轨的读数口同理）。
- 代价：一次 pacing 会留下 `degraded` 记录与冷却，`ldst sync` 的汇总里降级条数变多——
  这是有意的，日志该说「因为限流降级」，而不是「一切正常」或「任务失败」。
- 验证：`tests/integration/test_rate_limit_backoff.py`（13 例，锁 (a) 按建议时长退避、
  (b) 上限后回放昨日池、(c) 整轮 degraded 退出码 0，外加 stale 标记、冷却封顶、
  荒谬建议、无建议、配置可调、冷却期跳过、快照两态）+
  `tests/contract/test_tickhub_payload_mapping.py`（限流形状的 4 例）+
  `scripts/simulate_rate_limit.py`（仿真交易日端到端证据，日志见
  `docs/tasks/QUOT-345/rate-limit-trading-day-evidence.md`）。
