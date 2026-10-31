# 资讯雷达与事件日历（R6）规格

- 日期：2026-10-31（首版）
- 归属：数据面 R6
- 相关：生态边界闸门（不外采、同 peer 主备）、去重与幂等口径、来源标签与日历缺口口径；
  指标口径不变，见 `docs/specs/METRICS.md`
- 实现：`src/ladderstock/news/`（`models` / `normalize` / `events` / `stream` /
  `calendar` / `store` / `ai_enrich`，去重在 `stream.py:dedupe`）、`src/ladderstock/cli/news.py`、
  `mcp/server.py:_tool_news_stream|_tool_event_calendar`
- **页面不在本能力范围**：资讯雷达 / 日历 / 候选池 / 持仓四页归 R10

## 1. 统一条目 schema（对外契约，八个键）

`news/models.py:CONTRACT_KEYS` 是唯一出处，`ldst news --output json` 的 `items[]`、
MCP `news_stream` 信封的 `payload.items[]`、本机库 `news_items` 表都按这一份走：

| 键 | 类型 | 口径 |
|---|---|---|
| `id` | str | 去重键（规范化 url，其次归一化标题）的 sha1 派生值；**同一篇稿子在任何进程任何轮次都是同一个 id** |
| `source` | str | 数据集名（`NEWS_STREAM_DATASETS` 之一），不是厂商名 |
| `title` | str | 上游原文标题，不做美化 |
| `url` | str | 规范化后的地址（丢跟踪参数、统一 scheme/host 小写）；**上游不给 url 时为空串**，空 url 不产事件 |
| `published_at` | str \| null | ISO 字符串，保留上游粒度：只给到日就是 `2026-10-30`，不补 `T00:00:00` |
| `related_codes` | list[str] | 规范化 6 位代码（`000001` / `600519`），来自上游字段或标题抽取 |
| `summary` | str | 摘要；跨源合并时取**最长**的那一份 |
| `content_hash` | str | 标题 + 正文指纹，用于同稿不同地址的兜底判定 |

派生但不外发：`origins`（来源列表，合并证据）、`provenance`（dataset / tool / peer /
confidence / fetched_at / trace_id）、`dedup_key`、`title_key`、`day`。

## 2. 去重：并查集合并，被合并的一方进 `origins`

`news/stream.py:dedupe`（并查集）：

1. 两两建立等价边——**规范化 url 相同**（`canonical_url` 先剥 `utm_*` / `_t` / `#frag`）
   或**归一化标题相同**（NFKC、去标点空白、去【】〔〕等装饰）；
2. 并查集收成组，每组留一条代表（发布时间最新、摘要最长、代码取并集）；
3. 落选条目的 `{dataset, title, url}` 进代表项的 `origins`，所以「来源列表」是**可查证的**，
   不是把重复直接丢掉。

反面同样重要：**同标题但不同日期不合并**（两份真公告标题撞了是常态，
`tests/unit/news/test_stream.py::test_same_title_different_day_is_not_merged`）。
已知残留：同一件事、两家媒体措辞不同 → 合并不了，会出两条同类事件（见抽查表 `KNOWN_DUPLICATE_TITLE`）。

计数字段口径：`fetched` 是上游返回条数，`new_items` / `known_items` 按**窗口视图**数
（进入窗口、可能入库的那批），不按 `fetched` 数——否则跨源重复与窗口外条目会被当成「新增」，
重跑就报不出 0。

## 3. 事件抽取：规则优先，八类，每条带证据

`news/events.py:EVENT_RULES`，规则结构 `EventRule(key, label, groups, negations, sources, date_in_text)`：
`groups` 之间是「与」，组内任一关键词命中即该组满足。

| key | 类别 | 关键口径 |
|---|---|---|
| `earnings_preview` | 业绩预告 | 只认「业绩预告 / 预增 / 预减 / 快报 / 扭亏 / 首亏 / 续亏 / 业绩预计」；只提「净利润」的业绩新闻**不算**预告 |
| `shareholder_reduction` | 股东减持 | 「减持」+ 局部否定窗口（未减持 / 不减持 / 终止减持 …） |
| `shareholder_increase` | 股东增持 | 同上；**研报栏目整体不参与抽取**，否则「评级：增持」被当成股东增持 |
| `lockup_expiry` | 限售解禁 | 「解禁 / 解除限售 / 限售股份上市」；`date_in_text` → 日期取自原文，`date_basis="evidence_text"` |
| `ma_restructuring` | 并购重组 | 「并购 / 重大资产重组 / 收购 / 要约收购 / 控制权」；终止、不构成、取消一律不抽 |
| `contract_award` | 中标合同 | 「中标 / 中选 / 重大合同 / 签署合同」；「订单」不进关键词（「订单充足」是调研描述） |
| `policy` | 政策发布 | **必须同时**命中「发布主体」（国务院 / 央行 / 证监会 / 交易所 …）与「政策动作」（政策 / 新规 / 部署 / 降准 / LPR …）；只有『政策』二字的公司公告不算 |
| `shareholder_meeting` | 股东大会 | 只来自公告正文（上游无独立工具）；`date_in_text` |

硬约束：

- 每条事件必须带 `evidence = {snippet, url, matched, dataset}`，`NewsEvent.__post_init__`
  拒绝无证据构造；snippet 是命中位置前后 `EVIDENCE_RADIUS=60` 字的原文片段。
- 无 url 的条目**不产事件**（没有来源标签的内容不能当资料）。
- `id` 由 `(type, event_date, code, title_key)` 单向派生，**顺序无关、跨进程稳定**，
  所以事件表也是幂等的（`rule_id` 参与，模型叙述不参与）。
- 被否定词压掉的命中计入 `sources_status` / `extraction.suppressed_by_negation`，
  不是静默丢弃——「漏抽」与「故意不抽」必须能区分。

## 4. 事件日历：五个小节，三个「暂无数据源」

`news/calendar.py:build_calendar(month)`，`SECTION_ORDER` 固定顺序：

| key | 小节 | status | 来源 / 说明 |
|---|---|---|---|
| `earnings_disclosure` | 财报预约披露日 | **`no_source`** | TickHub-CN 无「预约披露时间表」工具；`earnings_forecast.notice_date` 是业绩预告自己的披露日，**不能替代** |
| `lockup_expiry` | 解禁日 | `sourced` | `lockup_expiry`（history + upcoming），上游要求 `code`；未给 `--code` 时小节降为 `no_source` 并说明原因 |
| `shareholder_meeting` | 股东大会 | **`no_source`** | 无工具；公告正文写了会议日期的，只作为带证据的抽取结果出现在 `extracted_events` |
| `policy_window` | 政策发布窗口 | **`no_source`** | 无官方发布日程源；`macro_calendar` 是宏观指标日历，口径不同，**不做顶包** |
| `earnings_preview` | 业绩预告 | `sourced` | `earnings_forecast`；无时间窗参数 → 取回前 N 条后按 `notice_date` 本地过滤，所以「窗口内为空」**不等于**当月没有预告 |

`no_source` 小节必须带 `reason`（为什么没有）与 `ask`（要补哪个上游工具），`entries` 恒为 `[]`；
载荷里**不允许**出现 `data_source` / `tool` 两个键——否则页面会画成「有源但本月没有」，
正是最危险的误读。绝不估算、不编日期。

`extracted_events` 是**另一件事**：规则从公告正文抽出的、与日历口径相关的四类事件
（`lockup_expiry` / `shareholder_meeting` / `earnings_preview` / `policy`），
每条带原文片段 + url + `date_basis`，标 `basis="rule_extracted"`。它是可审计的抽取结果，
不在上面五个小节里冒充数据源。

## 5. 本机存储：`data/news.sqlite`（三张表）

| 表 | 用途 | 幂等键 |
|---|---|---|
| `news_items` | 统一条目（含 `origins` / `provenance` JSON） | `id`，`ON CONFLICT(id) DO UPDATE`，`first_seen_at` 不变、`updated_at` 刷新 |
| `news_events` | 抽取事件（`evidence`、`date_basis`、`extractor`、`ai_note`） | `id`，同上 |
| `news_runs` | 取数账本（窗口 / 栏目 / codes / fetched / stored / deduped / events） | 自增，重跑**多一行**是预期行为 |

与隐私库 `journal.sqlite` 分库：资讯是公开数据，
分库让「资讯面不含隐私字段」成为可以机器证明的命题。
`connect(root, create=False)` 在文件不存在时返回 `None`——只读查询（日历的 `extracted_events`、
MCP 默认口径）不建空库。

## 6. 模型叙述（默认关闭）

`news/ai_enrich.py`：三条硬规矩由代码保证，不靠调用方自觉。

1. `AI_ENRICH_ENABLED_BY_DEFAULT = False`；只有 CLI 带 `--ai` / MCP 传 `ai=true` 才走到这里，
   未开启时载荷是 `{"requested": false, "enabled": false, "skipped_reason": "默认关闭：…"}`。
2. 模型只写 `ai_note` 一个字段，不许新增事件、不许改类型 / 日期 / 代码；
   入口只接受规则已抽出的事件，产出前后各过一道 `core.boundary.assert_narrative_only`。
3. prompt 是最小视图（标题 + ≤200 字摘要 + url + 事件类型 + 命中关键词），
   正文全文 / `related_codes` / `provenance` 一律不进；发送前跑 `private_keys_present`，
   命中即 `PromptPrivacyViolation`，拒绝发送。

失败不装成功：模型没配 / 超时 / 返回空 / 「资料不足」→ 该条事件原样留着，
记进 `failed`；一条都没成但请求了 → CLI 退出 9（`EXIT_AI_DEGRADED`）。
叙述补全成功后若本轮已入库，则回写 `news_events.ai_note`（同一份 upsert）。

## 7. 命令与工具契约

| 面 | 形态 | 要点 |
|---|---|---|
| CLI | `ldst news <date> [--until] [--source] [--code] [--limit] [--store/--no-store] [--ai] [--output]` | 默认写本机库；日期只接受 `YYYY-MM-DD`（简写 `20261030` 拒绝，exit 2）；窗口上限 31 天 |
| CLI | `ldst events --month YYYY-MM [--code] [--limit] [--fetch/--no-fetch] [--output]` | `--month` 必填（不默认当月）；`--no-fetch` 只读本机已入库事件 |
| MCP | `news_stream` | 信封域 `news`；**`store` 默认 false**，`read_only=true`；`data_as_of` = 窗口右端 |
| MCP | `event_calendar` | 信封域 `news`；`data_as_of` = 月份首日；三个缺口 ⇒ `source_status="partial"` |

退出码（`ldst news`）：全成功 0 / 参数非法 2 / 一个栏目都没成 4 / 部分失败 8 /
`--ai` 请求了但零叙述 9。日历：有源小节全挂 4、部分挂 8。
两个命令都不读隐私库，要按代码过滤只能显式 `--code`。

日期口径在 CLI 与 MCP 两侧一致：资讯这两个入口都只收 `YYYY-MM-DD`
（`_news_day_arg`），其它 MCP 工具沿用宽松的 `_parse_day`，既有契约不动。

## 8. 验收项 → 测试对应

| issue 验收项 | 钉住它的测试 |
|---|---|
| 同一 fixture 重跑幂等 | `test_rerun_is_idempotent`、`test_ids_are_stable_across_runs_and_processes`、`test_news_is_idempotent_at_the_command_level` |
| 去重生效（多源只留一条并记录来源列表） | `test_cross_source_story_keeps_one_row_with_all_origins`、`test_transitive_duplicates_collapse_into_one_group`、`test_same_title_different_day_is_not_merged` |
| 抽取准确性抽查表（准确 / 漏抽 / 误抽） | `test_events.py` 逐行 parametrize `fixtures/news_samples.SPOT_CHECK`（16 行），表本身另有一条自洽性断言 |
| 隐私（无持仓 / 自选 / 日志字段） | `test_no_private_keys_anywhere`、CLI/MCP 两侧的 JSON 键扫描、`test_store.py` 的列名与 `PRIVATE_KEYS` 相撞检查、`PRAGMA database_list` 结构证明、日历「不读自选」用例 |
| 事件都带证据与 url | `test_events.py` 的证据硬约束组 + `NewsEvent.__post_init__` 拒绝用例 |
| 日历缺口明说「暂无数据源」 | `test_calendar.py` 的三节 `no_source` / `entries==[]` / `reason`+`ask` / 不出现 `data_source`+`tool` / 不拿 `macro_calendar` 顶包 |
| 模型默认关闭 | `test_ai_enrich.py::AI_ENRICH_ENABLED_BY_DEFAULT is False`、CLI 用例把 `_default_chat` monkeypatch 成抛异常仍 0 退出 |
| 完全离线可测 | `tests/fixtures/fake_peers.py` + `news_samples.py`，monkeypatch `provider_registry.build_transport` |

## 9. 已知限制（如实记录，不在本单偷偷补）

1. **三个日历小节暂无数据源**，补齐路径写在每节的 `ask` 里。
2. `earnings_forecast` 无时间窗参数，只能取回前 N 条再本地过滤：**当月窗口内为空不等于没有预告**。
3. `lockup_expiry` 上游要求 `code`，没有「全市场解禁日历」这条路；未指定 `--code` 时该节 `no_source`。
4. `eastmoney_global_news` 上游不给 url，这类条目进资讯流可阅读，但**不产事件**。
5. 同事件不同措辞的标题合并不了，会出两条同类事件（已知残留，页面按 type + 日期聚合时可合并展示）。
6. 事件抽取是**标题 + 摘要级**关键词判定，正文全文不进规则；漏抽项已逐条列在抽查表并给理由。
