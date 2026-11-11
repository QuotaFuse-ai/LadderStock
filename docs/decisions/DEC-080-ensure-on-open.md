# DEC-080 打开即补取（ensure-on-open）：只读 GET 陈旧时经 peer 免费懒补取

- 状态：已采纳（QUOT-555，2026-11-11）
- 关联：DEC-006（app 不自采、只经 peer）、DEC-018（懒缓存与口径逐字段标注）、
  DEC-048（QFSD-7001 退避）、DEC-074（行业显式同步与只读重算分家）、
  DEC-014（控制面命令白名单）、QUOT-519（S1 每日自动同步）、
  `docs/specs/SPEC-HOME-V2.md` §5.6、`docs/specs/SPEC-RADAR-PAGES.md` §3–§6.1

## 背景

「打开即 fetch」在读层已经做到（前端 `useAsync` 挂载即请求），但四个页面 GET
（`/api/market/theme`、`/api/news`、`/api/pool`、`/api/industry`）**纯只读本机库**：
本机数据陈旧/缺失就出 `available=false` + 「先同步再刷新」手动指引。产品方向是
普通用户冷启动打开页面 → 立刻看到当日数据，零手动步骤。

DEC-074 §不改变的语义曾钉死「只读接口 `/api/industry` 与 `ldst industry list`
仍是零外呼」。本决策**只放宽其中 web 数据面 GET 的「零外呼」一句**，且仅限
**免费 peer 读**；CLI 与付费动作的口径一字不改（见下）。

## 决策

给四个只读 GET 加一层**受控懒补取**：探测到本机陈旧/缺失时，复用既有同步路径
（`cli/sync.py` 的 `sync_one` / `run_sync`、`news/stream.py` 的 `collect_stream`）
经 TickHub/Wire peer 缓存补一轮，补完再读。核心落在新模块 `web/ensure.py: OpenEnsure`。

### 接线边界：默认不启用，生产入口显式 opt-in

- `create_app(open_ensure=None)` 缺省 → 四个 GET 行为与改造前**逐字节一致**，
  既有契约测试零改动（与 `own_processes_on_lifecycle` 同一范式）；
- 只有生产入口 `cli/main.py: service_entry` 与 `cli/run.py: launch` 显式传入
  `OpenEnsure()` 才启用；`RadarPlane` / `IndustryPlane` 的 `ensure=` 同样缺省 None。

### 免费自动 / 付费显式（DEC-074 边界保留）

| 动作 | 入口 | 成本 | 本决策 |
|---|---|---|---|
| 页面 GET 懒补取 | web `/api/{market/theme,news,pool,industry}` | 免费 peer 读 | **自动**（新增） |
| 重算行业快照 | `ldst industry list` / 控制面 key `industry` | 零外呼 | 不变（仍零外呼） |
| 同步补齐行业 | `ldst sync industry` / 控制面 key `industry-sync` | 成本非 0 | 不变（仍显式确认） |
| 每日自动同步 | S1 `ldst sync`（base_pool 常规口径） | 免费 | 不变（QUOT-519） |

industry 组的懒补取**只补 `SNAPSHOT_DATASETS` 的 base_pool 常规口径**
（`run_sync(..., codes=None)`，与 S1 每日自动同范围），**绝不**触发 DEC-074 的
锚点扩池（`config/industry.yaml` 锚点股、`MAX_POOL_CODES` 校验、成本非 0）。
`ldst industry list` 直接走 `industry/snapshot.py: industry_list()`，不经
`IndustryPlane`，故 CLI 零外呼天然不受影响。

### 四条纪律（与硬红线一一对应）

1. **DEC-006 合规**：只走已登记 peer 的既有同步链路，不新增任何采集源；数据集未在
   `config/sync.yaml` 登记 → `no_source`，一次外呼都不发（闸门 4：无源不自采）。
2. **免费 peer 读自动、付费同步仍显式**：见上表；industry 不做锚点扩池。
3. **幂等 + 冷却 + 并发上限**：复用同步路径的水位增量 / `(code, trade_date)` 幂等
   upsert / QFSD-7001 有界退避（DEC-048）；本层再加每组冷却（默认 300s，**无论成败
   都进冷却**，上游挂了也不每次刷新重打）+ 单飞（同组同时只补一轮，锁内双检）+
   历史库写锁短超时（`LOCK_TIMEOUT_SEC=2s`，拿不到锁 → `busy` 跳过，不阻塞 GET）。
4. **失败如实降级、不回落演示**：补取失败/上游全挂 → `degraded`/`failed`/`empty`，
   GET 仍按本机真实读取出空态；演示态是前端 `?state=` 的事，后端从不返回演示数据
   （SPEC-HOME-V2 §5.6）。`OpenEnsure.ensure` 永不向 GET 抛异常（内部全收）。

### 落库口径

与 CLI 完全一致：`sync_one`/`run_sync` 经 `write_dataset()` 写的是 `data_dir()`
（`LDST_DATA_DIR`），不读 `registry.root`。`OpenEnsure.root` 只用于**探测读取**；
生产里 `create_app` 的 `data_root` 默认即 `data_dir()`，读写同库。资讯落
`news.sqlite`（独立库），不占历史库写锁。

### 陈旧探针（零外呼）

- theme：`dataset_snapshot("theme_news", day)` 无行 → 陈旧；
- pool：`dataset_snapshot("limit_pool", day)` 无行 → 陈旧；
- industry：`archive.latest_day("sw_industry_map")` 为 None → 冷启动（DEC-074：
  `data_as_of` 是分类计入日而非行情日，不按「当日」判陈旧，只按「有没有」判）；
- news：`news.sqlite` 不存在或无覆盖窗口的采集运行 → 陈旧。

## 备选方案与否决理由

1. **前端打开页面先 POST 同步再 GET**：把免费读变成显式写动作，违背「零手动步骤」，
   且同步范围/成本难以按页面收敛；
2. **GET 内直接调上游采集**：绕过 peer 与同步路径，违反 DEC-006，也丢掉水位/幂等/退避；
3. **industry 懒补取直接复用 `ldst sync industry`（锚点扩池）**：把成本非 0 动作变成
   自动触发，违反付费显式红线；本决策只走 base_pool 常规口径；
4. **默认对所有 `create_app` 启用**：会改动既有契约测试的空态语义；改为生产入口
   opt-in，既有测试零改动、行为可自证。

## 影响与验收

- 新增 `web/ensure.py`；`web/{radar,industry,server}.py` 四个 GET 接线（缺省不启用）；
  `cli/{main,run}.py` 生产入口传 `OpenEnsure()`；
- 冷启动（本机库陈旧）打开 market/news/pool/industry 各页**自动**有当日数，
  无「先同步再刷新」指引；补取幂等、有冷却；上游失败如实 degraded/empty，不回落演示；
- 显式付费同步（`industry-sync`）仍需确认（回归）；`ldst industry list` 仍零外呼；
- 离线契约：`tests/unit/web/test_ensure.py`（闸门/冷却/单飞/失败/无源/付费边界）、
  `tests/unit/web/test_ensure_endpoints.py`（四页冷启动自动补取 + 失败如实空态 +
  缺省不启用回归）；夹具 `tests/fixtures/ensure_peers.py`（FakeTransport，绝不真联网）；
- 后端 web 全套绿（1007 passed / 1 平台 skip / 0 failed）；CI 双绿由 PR 承接。