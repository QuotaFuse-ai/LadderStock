# DEC-039 盘中 6 时点读面：两条只读接口，状态汇总读 status.json，快照原文不重算

- 日期：2026-11-01
- 提出：后端架构师（QUOT-301，补 DEC-035 §5「页面另行立项」的前置）
- 状态：生效（随 QUOT-301 实现落地）
- 关联：DEC-004（默认只绑回环 5950）、DEC-015 Tier 1（回环独占闸门）、
  DEC-028（判定在后端、`null` 不当 0）、DEC-030（只读接口只做投影，不重算）、
  DEC-034（新路由必须注册在 SPA 兜底之前）、DEC-035 §5（本决策补的那条欠账）、
  `SPEC-INTRADAY-READ`（两接口契约）、`SPEC-INTRADAY-SNAPSHOT`（落盘口径，本决策不改）、
  `SPEC-LIMIT-POOL` / DEC-031（同族只读接口的信封与参数口径）

## 1. 背景

DEC-035 把 6 时点快照合进 `main`（`42d923d`）时自己写明：**「前端『盯盘·竞价与盘中』入口：
本决策只落数据面，页面另行立项」**。数据面落地后，`git grep intraday -- src/ladderstock/web`
**零命中**——生产态没有任何读接口。前端屏（QUOT-302）就算立项，也没有取数的地方。

本决策只解决一件事：**把已落盘的 `data/intraday/<date>/` 开一条只读通道**，
让「这个时点为什么没有文件」在页面上看得见，而不是让前端去猜目录结构。

## 2. 决定

### 2.1 两条接口，各自对应一个消费场景

| 接口 | 读什么 | 给谁用 |
| --- | --- | --- |
| `GET /api/intraday?date=YYYY-MM-DD` | `data/intraday/<date>/status.json` | 页面顶部的「今天 6 个时点跑到哪一步」状态条 |
| `GET /api/intraday/snapshot?date&slot=HH:MM` | 单个 `data/intraday/<date>/<HHMM>.json` | 点开某一时点看快照原文 |

时点列表由后端固定给全 6 条（含一条记录都没有的），**前端不自己拼 `SLOTS`**——
将来加时点只改 `core/intraday.py:SLOTS`，页面不用跟着改。

### 2.2 状态汇总读 `status.json`，不读快照文件本体

`status.json` 是 DEC-035 定的「这个时点发生了什么」的唯一事实来源，也是运维排查入口。
逐时点行原样透出 `status / detail / price_field / stocks / fresh / at`，
记录里没有的字段就是 `null`（DEC-028：不补 0、不拿请求日期顶替）。

再加两个正交的布尔，把两件容易被混起来的事分开：

- `recorded`：`status.json` 里**有没有这条记录**。「调度器跑到过这个时点但没抓到读数」
  （`recorded=true, status=missing`）与「这个时点根本没跑」（`recorded=false`）是两种
  空态，文案不一样（前者是数据问题，后者多半是长驻进程没起来）。没有记录时 `status`
  仍给 `missing`（这个时点确实没有读数），区别由 `recorded` 承载，`status` 保持闭集；
- `available`：**磁盘上此刻到底有没有快照文件**。不照抄 status.json 里的 `file` 字段——
  status 说 `ok` 而文件被手工删过 / 换过 `data_dir` 时不谎报，`file` 也随之给 `null`。

### 2.3 快照原文一律嵌在 `payload.snapshot` 一层

`<HHMM>.json` 里有什么就出什么（`yesterday_limit_up` / `sentiment` / `price_field` /
`source`），后端**一个指标都不重算、不排序、不补字段**。嵌一层而不是平铺，是为了和
`/api/review`（`payload.review`）、`/api/weekly`（`payload.weekly`）保持同一个形状：
外层是请求与可用性，内层是文件本体。唯一一道加工是全局出口都过的 `strip_private()`。

### 2.4 回环独占（这是它与 `/api/limit-pool` 唯一的语义差别）

两条接口都走 `_loopback_only()`（DEC-015 §3.1 / §3.2 那道闸门，同
`/api/journal/stats`、`/api/market/*`）：非回环来源 403，`Host` 不在白名单也 403，
**即使开了 `LDST_WEB_ALLOW_REMOTE=1` + `LDST_WEB_TOKEN` 也一样**。

理由：公开的涨停池是「市场发生了什么」，而 6 时点快照逐时点记着**这台机器当天在盯哪些票的
盘面读数**（`stocks` / `fresh` / 昨日池名单），是「这台机器在做什么」的信号——与 DEC-031 给
`/api/market/*` 上同一条闸门的理由一致。因此**不跟** `/api/limit-pool` 的「数据面不加回环独占」。

同样**不挂** `control_enabled()`：`LDST_CONTROL_ENABLED=0` 关的是写面，不该连带关掉只读数据。

### 2.5 参数口径照 `/api/limit-pool`，不另开一套

- `date`：沿用 `_parse_day`。缺省 → 400「缺少 date」；解析不了 → 400 + 中文 `detail`。
  它继承 `date.fromisoformat` 的容忍度（紧凑写法 `20261030` 也接受），与
  `/api/sentiment` / `/api/ladder` / `/api/limit-pool` 同一条口径。**拼路径用的是解析后的
  `date` 对象的 `isoformat()`，不是原始入参**，所以目录穿越在这条路上不成立；
- `slot`：只认 `SLOTS` 里的 6 个值，另认落盘文件名的紧凑写法 `HHMM`。其余一律 400，
  `detail` 里把 6 个可选值列全。它同样先过白名单再拼文件名（同 `research.normalize_code`）；
- 空态一律 **200 + `available=false` + 中文 `reason`**：文件不存在、`status.json` 不存在、
  JSON 解析失败、顶层不是对象——**都不抛 500**（这一条比 `/api/weekly` 更宽：读盘失败
  对页面来说只是「这个时点没有数据」，不是服务端事故）。

### 2.6 注册位置与只读性

两条都注册在 SPA 兜底 `@app.get("/{full_path:path}")` **之前**（DEC-034 的教训：兜底只接
GET，注册在它后面等于生产态 404）。只注册 `GET`，其它方法由 FastAPI 回 405；
不写库、不写文件、不碰采集，`ldst doctor` 的 `boundary:no_collection` 不受影响。

## 3. 替代方案（及否决理由）

| 方案 | 否决理由 |
| --- | --- |
| 前端直接读 `data/intraday/` 目录 | 浏览器拿不到本机文件系统；Electron 端读文件会把落盘结构变成前端契约，改一次目录布局要动两端 |
| 汇总接口顺手把 6 个时点的快照都读出来 | 一天 6 个文件、每个几十只股票的逐只读数，页面状态条只要 `status.json` 那几行；全读是拿带宽换一次 `fs` 遍历，且把「点进去才看」的数据塞进首屏 |
| 在后端把 `avg_change_pct` / 分板位重算一遍 | DEC-035 §2.7 与 DEC-028 已经把口径钉在采集侧（`y_streak`、fresh 行才进聚合）；读面重算等于第二个地方有一份算法，两份迟早漂移 |
| `status.json` 记着 `ok` 就认为快照存在 | 手工删文件、换 `data_dir` 后页面会指向一个 404 不出来的时点；`available` 必须来自磁盘 |
| 没有记录的时点给 `status=null` | 前端要处理「6 个时点里有 3 种状态形状」，闭集 enum 变成开放集；用 `recorded` 区分两个语义更省一层分支 |
| 跟 `/api/limit-pool` 一样不加回环闸门 | 见 §2.4：快照内容是「这台机器在看什么」，与公开涨停池不是一个隐私等级 |

## 4. 明确不做

- 不做前端页面（QUOT-302）；
- 不改 `core/intraday.py` 的窗口定义与「不补跑」语义（QUOT-290 要在真实窗口验收它）；
- 不新增采集入口、不做「按日期补抓」的接口；
- 不在读面计算任何指标、排序、评分；
- 不暴露目录列表接口（`?date=` 不给缺省值，也不给「有哪些天」——要列表另立接口，别混进读面）。

## 5. 影响

- 新增：`GET /api/intraday`、`GET /api/intraday/snapshot`（`src/ladderstock/web/server.py`）、
  `docs/specs/SPEC-INTRADAY-READ.md`、`tests/unit/web/test_intraday_endpoints.py`；
- 改动：`web/server.py` 把回环闸门抽成 `_loopback_only(request, detail)`，
  `_journal_guard()` 变成它的一行委托（行为与 `detail` 文案不变，`/api/market/*` 照旧）；
- 文档面：`SPEC-INTRADAY-SNAPSHOT` 补一节指向本契约；`UI-SPEC.md` §10 的映射表补盯盘屏一行；
- 前端面：QUOT-302 可以直接按本契约开发，不再需要读目录结构。
