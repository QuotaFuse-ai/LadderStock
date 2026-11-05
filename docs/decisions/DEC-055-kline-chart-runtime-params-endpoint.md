# DEC-055（已批准）K 线图端点：`GET /api/research/{code}/kline` 独立数据集、运行时透传、不落库

- 日期：2026-11-05（批准：2026-11-05）
- 提出：后端架构师（QUOT-410，父任务 QUOT-408 首页改版的 K 线主图数据面）
- **状态：已批准（QUOT-410 任务书即批准口径：period / adjust 运行时透传、不沿用
  FIXED_PARAMS 写死口径、peer 不可达时显式空态 200）**
- 编号：`DEC-055`。按 DEC-045 §2 取值：
  `git ls-tree -r --name-only docs/decisions/` → **已扫到的最大号 = 054**（DEC-054
  intraday-batch-slot-store），本单取 055。
- 关联：DEC-006（数据集归属唯一真源 `DATASET_CATALOG`，本单按它登记）/ DEC-023（复权与
  聚合口径归上游，本层不臆算）/ DEC-043（拒绝把不同口径的数据挂进同一备链的先例）/ 
  DEC-045（编号规则）/ DEC-047（peer 端口归属）/ DEC-048（页面命令与模块自启，
  kline 端点不依赖任何需要自启的常驻模块，peer 进程按需可用即可）/
  `docs/specs/SPEC-RESEARCH-PAGES.md` §2.4（本单落定的接口契约）

## 1. 背景

QUOT-408 首页改版要给个股加 K 线主图，用户可切三档周期（日 / 周 / 月）× 三档复权
（前复权 / 后复权 / 不复权）。现有数据面只有两条路，都不够用：

- `overview`（QUOT-400）只回放**本机缓存**的 `daily_k`——sync 写死 `daily + qfq`
  口径，且缓存可能滞后，撑不起「切周期、切换复权」的交互；
- 直接给前端开 peer 工具口：把 TickHub-CN 的取数细节（工具名、参数形状、限流退避）
  泄漏到页面层，绕开了 `DATASET_CATALOG` 的归属闸门（DEC-006）。

上游 TickHub-CN 的 `tencent_kline` 工具本身支持 `period / adjust / limit / start / end`
五个参数，周 / 月聚合与三档复权**全部由上游完成**。缺口只在 LadderStock 这一侧：
没有一条「按请求透传这些参数」的出口。

## 2. 决定

### 2.1 新增独立数据集 `kline_chart`，不共用 `daily_k` 链路

`daily_k` 与 `kline_chart` 用**同一个工具**（`tencent_kline`）、同一个返回形状
（`data.klines` 展开成行），但**分开登记**，理由与 QUOT-233 时期「形状按登记的主工具
查」的教训同源——口径字段是登记的一部分：

- `daily_k` 的口径（`daily + qfq`）是 **sync 常量**，写进 `FIXED_PARAMS`，落库、可回放；
- `kline_chart` 的口径（`period / adjust / limit`）是**运行时参数**，随请求变，
  不落库、不进 `sync.yaml`、无缓存回放（`fetch_live(..., allow_cache=False)`）。

共用一条链路意味着 `owner_of` 归属、TTL 缓存键、日志口径都要为「同一数据集两种语义」
打洞，分开登记是最便宜的表达。

### 2.2 备源留空

`daily_k` 的备源 `tdx_daily_package` 是**盘后全市场日线包**，不收 `period / adjust`
入参——挂它当备源，周 / 月 / 复权档必然一起失败（与 DEC-043 拒绝把 `wire_timeline`
挂进按日链路是同一条原则：**备源与主源必须同口径，否则降级是假的**）。
`fallback_tools` 留空、`sources.yaml` 的 `fallback: []` 留空，失败显式空态。

### 2.3 端点契约（SPEC §2.4 定稿）

`GET /api/research/{code}/kline?period=daily|weekly|monthly&adjust=qfq|hfq|none&limit=N`

- 参数校验：`period` / `adjust` 白名单外 422；`limit` 必须 1..500（缺省 120），
  越界 / 非整数 422；未知代码 404（与 overview 同一 `require_known_code` 判据）；
- 响应走 research 面同款四段信封：`payload / data_source_status / data_as_of / meta`；
  `payload` 含 `code / period / adjust / limit / bars / count / available / reason /
  source_detail / data_source_status`，`bars[]` 每根 `date / open / high / low / close /
  volume / amount`；
- **取不到数是空态不是错误**：peer 不可达、超时、上游返回空 → 200 +
  `available=false` + `data_source_status="missing"` + `reason` 说明，不编造、不补零；
  上游明确降级（`source_status != "ok"`）且有数据 → `degraded`。

### 2.4 缓存与新鲜度

页面每次切换周期 / 复权都是一次新请求，落在 registry 的进程内机制上：

- 同一参数键（`dataset + trade_date + params`）60 秒 TTL 内重复请求直接命中缓存，
  不重复打 peer；
- 并发同参数请求 in-flight 合并，K 线主图初始化时的并发请求不会放大成 N 个上游调用；
- 不同参数各走各的键，不会串缓存——这是把口径放进缓存键的天然结果。

SQLite 回放（`_replay`）对本数据集**不参与**：K 线图不落库，回放无意义，
`allow_cache=False` 把这条路径显式关掉。

### 2.5 明确不做什么

- **不在本层做周 / 月聚合**：上游给了什么周期就透传什么周期，本地从日线拼周线只会
  造出与上游对不上的口径；
- **不在本层算复权因子**：qfq / hfq 是上游按当日因子重算的，本层臆算等于编造；
- **不落库、不进 sync 台账**：K 线图是「现取现回」的展示数据，不是复盘事实；
  复盘要用的日线继续走 `daily_k`；
- **不开写方法**：端点只 GET，闸门沿用研究面既有三条（回环 + Host 白名单 + 写方法
  来源证据——本端点没有写方法，第三条天然不触发）。

## 3. 落地清单（本单已全部完成）

1. `src/ladderstock/core/boundary.py`：`DATASET_CATALOG` 登记 `kline_chart`
   （peer=tickhub、tool=tencent_kline、watermark=trade_date、fallback 空、不进 sync）；
2. `config/sources.yaml`：新增 `tickhub_kline_chart` 源条目（primary，超时 / 重试 /
   冷却与 `tickhub_kline` 同档）；
3. `src/ladderstock/data/adapters/tickhub.py`：`ROW_SHAPES` 登记 `kline_chart` 的
   `klines` 形状（`code / period / adjust` 透传、`date → trade_date`）；
4. `src/ladderstock/web/research.py`：`ResearchPlane.kline()` + `build_router` 注册
   `GET /{code}/kline`（Query 参数 period / adjust / limit）；
5. 测试：`tests/unit/web/test_research_kline.py`（假 registry 注入，30 用例：九组合
   透传 / 参数校验 4xx / 信封形状 / 空态 / 降级）+ 
   `tests/contract/test_kline_chart_payload_mapping.py`（真实 fixture 映射契约）；
6. `docs/specs/SPEC-RESEARCH-PAGES.md`：§2.4 接口契约（前端联调依据），版本升 v0.3。

## 4. 影响与风险

- 新增暴露面：**1 条只读回环路由**（`GET /api/research/{code}/kline`），无写方法、
  不经回环之外；
- 新增外部依赖：无（复用 TickHub-CN 既有工具）；
- 风险：peer 不可用时 K 线主图整体空态——这是有意为之（不编造），空态文案指路
  `ldst sync` 与 peer 启动命令（DEC-048）；
- 前端（QUOT-408 蜡烛组件）按 SPEC §2.4 契约先行开发，fixture/mock 与真实通道
  字段同形，接口无二次改动。
