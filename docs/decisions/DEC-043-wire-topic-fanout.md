# DEC-043 Wire 赛道叙事：按 slug 扇出取数，`wire_get_timeline` 不进按日链路

- 日期：2026-11-01
- 提出：后端架构师（QUOT-328，跨服务契约缺陷）
- 状态：生效
- 关联：DEC-006（生态边界：数据集归属唯一 peer、主备同 peer、不自建采集）、
  DEC-007 §2.4（请求治理：短 TTL 缓存 / 失败冷却 / 并发与最小间隔 / 有界重试）、
  DEC-008（`sources.yaml` ↔ `DATASET_CATALOG` 双向一致性）、DEC-034 同族口径（缺数如实 `null`）、
  QUOT-233（代码池：一个必填入参一次调用）、QUOT-311 / QUOT-313（被本缺陷挡住的屏轨字段）、
  `docs/SOURCES.md` §2、`docs/specs/SPEC-MARKET-SCREEN-API.md` §4.1

## 1. 背景与实测根因

`theme_news` 自上线起永远 `unavailable`，`/api/market/theme` 的 `available=false`，
QUOT-311 / QUOT-313 的题材字段整条被挡。对着**真实 Wire 服务**（本机 `qfw mcp http`，
127.0.0.1:8791）复算，确认是**两个叠在一起的缺陷**，不是环境问题：

1. **入参缺陷**。`wire_get_digest(topic, date=None)` 的 `topic` 是**必填赛道 slug**
   （`ai` / `semiconductor` / …，Wire 的 `config/topics.yaml` 的 `id`）。
   LadderStock 传的是 `{}`（`src/ladderstock/cli/sync.py:451` 的
   `params = {} if full else {"since": before}`，经 `provider_registry.py:992` 的
   `transport.call(peer, spec.tool, dict(params))` 原样发出），
   上游回 `1 validation error for wire_get_digestArguments / topic Field required`。
   中文赛道名（`半导体`）另回 `未知赛道: 半导体（可用: ai、semiconductor…）`。
   **没有「空参 = 取全部赛道」的调用形状**——这一点是本轮实测的前提，不是推测。
2. **拆壳缺陷**。Wire 的四个工具返回 `envelope.model_dump_json()`，FastMCP 把这串
   JSON 放进 `structuredContent.result`。原 `_unwrap_mcp_result` 只认 `result` 是**对象**
   的形状，字符串直接原样返回外层空壳 → **参数修对了，落库仍是一行没有叙事的空壳**。

第二个缺陷会让"只修入参"的验证看起来仍然失败，所以两处必须一起修。

## 2. 决定

1. **赛道池**：`config/sync.yaml` 的 `sync.datasets.theme_news.topics` 给出一组 Wire 赛道
   slug，`ldst sync` 按 slug **逐个取数**（一个 slug 一次调用），合并成一个事务落库。
   slug 只来自配置，**代码里不留赛道名常量表、不硬编码中文名**——合法取值以 Wire 的
   报错回包为准，LadderStock 不复制一份会漂移的影子注册表。
   `topics` 为空 → 判 `skipped`（可选数据集）且**一次调用都不发**（DEC-006 阶梯的上游拒绝
   空参，落下来的只会是脏数据）。上限沿用 `MAX_POOL_CODES = 100`。
2. **`wire_get_timeline` 不进按日链路**，`config/sync.yaml` 的 `theme_news.fallback: []`：
   它要的是 `event_id`（单个事件的下钻），按日摘要**没有事件维度**，挂进链路等于
   「主源失败后备源必然一起失败」，白烧一次调用还把降级轨迹写成两次同因失败。
   它在 `config/sources.yaml` **仍作为同 peer 备源登记在册**（DEC-006 合规、
   DEC-008 一致性校验照旧跑），`event_id` 下钻留给日报/复盘按事件单独调。
   为让「显式空值」真的生效，`chain()` 改为：**sync.yaml 写了 `fallback` 键（哪怕是 `[]`）
   就以它为准，只有完全没写这个键才回填 sources.yaml**。此前空列表会被回填成
   `[wire_timeline]`，配置里写 `[]` 是无效的——这是本单的第三处修正。
   `sync_topic_pool` 另有链路守卫：链路里出现非 slug 工具直接判配置错误（`EXIT_CONFIG`），
   不把"备源永远一起报错"留到运行时。
3. **扇出不吃 source 冷却**：`registry.fetch(..., ignore_cooldown=True)`，
   并在扇出内以 `FANOUT_FAILURE_STOP = 3` 连续失败止损。冷却是**跨轮**闸门
   （DEC-007 §2.4），而扇出里一次失败常常只是**某个入参**被上游拒了（赛道打错、代码不存在）。
   实测对照：1 个坏 slug + 2 个好 slug，改前报「赛道池全部失败」（好 slug 一次没发出去），
   改后 `degraded`、`成功 2 个 / 失败 1 个`。peer 真不健康时由连续失败止损兜住，
   不会把整池撞一遍；失败仍照旧记健康事件、照旧设冷却（下一轮受保护）。
4. **缺数不冒名**：Wire 的 `TopicDigest` 只有 `topic` / `summary` / `key_points` / `items`，
   **没有任何强度或涨停家数字段**。`/api/market/theme` 因此只出题材名，
   `strength` / `limit_up_count` / `leader` / `delta` 一律 `null`（UI-SPEC §5.6/§5.8 的
   `—` + 出处徽标），同 DEC-034 口径：不补 0、不估算。
   `THEME_NAME_KEYS` 补上 `topic`（Wire 的真实字段名），否则 12 个赛道全被当"无题材名"丢弃。

## 3. 被否决的备选

- **在 Wire 侧给个可判定的默认赛道**：本单边界是 Wire 只读、不提 PR；而且"默认赛道"
  等于把 12 个赛道的叙事压成 1 个，屏上仍是残缺。
- **LadderStock 里维护 slug → 中文赛道名映射**：多一份状态就多一处与 Wire
  `config/topics.yaml` 漂移的可能；展示名归前端/屏轨 owner 决定，数据面只透传 slug。
- **把 `wire_timeline` 整条记录从 sources.yaml 删掉**：能力确实存在（按 `event_id` 可取），
  删掉会让 DEC-008 的登记/一致性覆盖面出现空洞，检视面 `tool_registered` 口径要重写。
- **只改 sync.yaml 不改 `chain()`**：见 §2.2，空列表会被回填，改了不生效。

## 4. 验证

- 离线单测/契约：`tests/integration/test_sync_topic_pool.py`（扇出、空池零调用、
  非 slug 备源判配置错误、坏 slug 不废整池、连续失败止损、`status=partial` 记 `degraded`）、
  `tests/contract/test_mcp_streamable_http.py`（字符串壳拆壳）、
  `tests/unit/web/test_market_theme_endpoint.py`（真实 Wire 行 → 题材名有值、四项指标 `null`）。
- 真实 Wire（2026-11-01，12 个赛道，全部 `status=partial`：上游 LLM 不可达）：
  `ldst sync --dataset theme_news` 由 `skipped/failed` 变为 `degraded` + 12 行；
  `/api/market/theme?date=2026-11-01` 由 `available=false` + `data_source_status=missing`
  变为 `available=true` + `data_source_status=ok` + 12 个题材名（指标列 `null`）；
  2026-10-30（周五）Wire 当日**确实没有摘要**，接口照旧 200 + `available=false` +
  `reason` 指路，不产生任何假数。
