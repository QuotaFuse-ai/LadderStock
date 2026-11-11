# QUOT-555：打开即补取 ensure-on-open（只读 GET 陈旧经 peer 懒补取）

关联：QUOT-519（S1 每日自动同步，互补）、DEC-006（app 不自采、只经 peer）、
DEC-074（行业显式同步与只读重算分家）、DEC-048（QFSD-7001 退避）、
DEC-080（本单决策：打开即补取）、`docs/specs/SPEC-HOME-V2.md` §5.6
状态：已交付，待验收

## 1. 目标

四个页面 GET（`/api/market/theme`、`/api/news`、`/api/pool`、`/api/industry`）
原本纯只读本机库，陈旧/缺失就出 `available=false` + 「先同步再刷新」手动指引。
本单给它们加**受控懒补取**：冷启动打开页面 → 经 TickHub/Wire peer 缓存自动补一轮 →
立刻看到当日数据，零手动步骤。

验收：冷启动各页**自动**有当日数、无手动指引；补取幂等 + 冷却 + 并发上限；
上游失败如实 degraded/empty、**不回落演示**；**免费 peer 读自动、付费同步仍显式确认**
（industry 锚点扩池回归）；五态演示（`?state=`）不受影响；后端测试绿、CI 双绿。

## 2. 实现

核心新模块 `web/ensure.py: OpenEnsure`——探测 → 冷却/单飞判定 → 复用既有同步路径补取，
永不向 GET 抛异常。**默认不接线**：`create_app(open_ensure=None)` 时四个 GET 与改造前
逐字节一致；只有生产入口显式传 `OpenEnsure()` 才启用。

| 改动 | 文件 | 说明 |
|---|---|---|
| 懒补取闸门 | `web/ensure.py`（新增） | `OpenEnsure.ensure(group, day, end)`：陈旧探针（零外呼）→ 每组冷却 300s（成败都进）+ 单飞（锁内双检）+ 写锁短超时 2s（`busy` 跳过）→ 复用 `sync_one`/`run_sync`/`collect_stream`；状态词表 fresh/cooldown/inflight/no_source/probe_error/ok/degraded/empty/failed/busy |
| 资讯/候选池接线 | `web/radar.py` | `RadarPlane(ensure=None)`；`news_view`/`pool_view` **先于读取**触发 ensure，note 追加进 `source_detail` |
| 产业接线 | `web/industry.py` | `IndustryPlane(ensure=None)`；`list_view`/`detail_view`/`signals_view` **先于读取**触发 ensure（先读后补会投影到补取前的空态），`_apply_note` 追加 `source_detail` |
| 题材接线 + 装配 | `web/server.py` | `create_app(open_ensure=None)`；`market_theme` 先触发 ensure 再 `dataset_snapshot`；缺省 plane 装配传 `ensure=open_ensure` |
| 生产入口 | `cli/main.py`、`cli/run.py` | `service_entry` / `launch` 显式 `create_app(open_ensure=OpenEnsure())` |

补取范围：theme=`theme_news`；pool=`POOL_DATASETS`+`fund_flow_realtime`；
industry=`SNAPSHOT_DATASETS`（**base_pool 常规口径 `codes=None`**，与 S1 同范围）；
news=`collect_stream` 窗口采集。落库走 `write_dataset()` → `data_dir()`，与 CLI 同库。

## 3. 边界

- **DEC-006**：只走已登记 peer 的既有同步链路；数据集未登记 → `no_source`，零外呼；
- **免费/付费分家（DEC-074 保留）**：industry 懒补取只做 base_pool 常规口径，
  **绝不**触发 `ldst sync industry` 锚点扩池（成本非 0、显式确认）；
  `ldst industry list` 直接走 `snapshot.industry_list()`、不经 `IndustryPlane`，CLI 零外呼不变；
- **不回落演示**：后端从不返回演示数据，演示态是前端 `?state=` 的事；失败如实出空态；
- **不改 frontend/src**：前端零改动，note 只进 `source_detail`，空态文案不污染；
- **幂等**：复用水位增量 / `(code, trade_date)` 幂等 upsert / QFSD-7001 退避，不另造同步语义。

## 4. 测试（全程离线，FakeTransport 绝不真联网）

| 文件 | 覆盖 |
|---|---|
| `tests/unit/web/test_ensure.py`（12） | 新鲜只读零外呼、四组冷启动补取并落库、冷却界定重试、单飞第二请求只读本机、失败降级不抛、无源 no_source、未知组 disabled、组集合恰为四页、**industry 走 base_pool 非锚点扩池**（`per_code==ENSURE_CODES`）、industry 有任意历史即新鲜 |
| `tests/unit/web/test_ensure_endpoints.py`（8） | 四页冷启动自动补取（available/有数据、无「先配置/先同步」指引）、冷却界定上游外呼、theme/news 失败如实空态非演示、**缺省不启用回归**（legacy 空态、零外呼） |
| `tests/fixtures/ensure_peers.py`（新增） | dataset→peer/tool 映射、`ensure_transport`/`make_ensure_registry`、base_pool 锚点码 |

结果：ensure 20 绿；改动文件契约（industry/radar/theme）108 绿；
**web 全套 54 文件 1007 passed / 1 平台 skip（POSIX-only）/ 0 failed**。
（共享机多 agent 并发 +  live server 下整套约 7–8 分钟，非死锁；faulthandler 无挂起栈。）

## 5. 交付

- 决策：`docs/decisions/DEC-080-ensure-on-open.md`（只增不改）；
- 分支 `agent/b/4ff63c38abba`，PR 承接 CI 双绿；
- 下一步：前端 S6 依赖本单落地后切真实通道（前端子单承接）；S1 每日自动（QUOT-519）
  与本单互补——S1 定时预热、本单冷启动兜底。