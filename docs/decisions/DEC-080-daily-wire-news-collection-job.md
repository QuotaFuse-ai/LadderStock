# DEC-080：题材资讯每日采集任务——wire_news 触发 Wire 自己的采集（QUOT-554）

日期：2026-11-11
关联：QUOT-554（拆自 QUOT-519）、DEC-047（收盘全量同步 job 的注册面模式）、
DEC-006（四层边界：数据只从生态 peer 取）、DEC-043（theme_news 只有主源）、
QUOT-328（赛道池扇出）
状态：已定稿

## 背景

`theme_news` 的唯一数据源是 Wire 的 `wire_get_digest`，而该工具**只读 Wire 本地
库**——不触发采集。Wire 的新鲜度来自它自己的 `qfw run`（抓 RSS → 聚类 → LLM
提炼 → 落库），但 Wire 的内置调度默认关闭，LadderStock supervisor 也只拉它的
serve/mcp 面。于是 `theme_news` 水位长期停在上次手动运行的日子，
`GET /api/market/theme` 常态 `available=false`，题材/资讯要靠人手动同步才新鲜。

QUOT-554 要求：schedule 里有一个每日自动的采集+落库 job，零手动 sync；
复用现有 `QF_WIRE_API_KEY`（不新购）；部分免费 RSS 源 403/不可用时如实降级。

## 决策

1. **新增独立每日 job `wire_news_daily`（07:30，kind=`wire_news`，默认启用，
   每天跑含周末）**，不并入 `CLOSE_SYNC_DATASETS`。资讯不收盘：07:30 给出盘前
   新鲜度，周末照跑；收盘同步的仅交易日口径与退出码语义原样不动。
2. **两段式，采集由 Wire peer 自己完成**：先子进程跑
   `qfw --output json run`（cwd 钉在 Wire 目录），再复用
   `sync_one(registry, store, "theme_news")` 走既有赛道池扇出落库。
   LadderStock 不直连资讯站点、不复造 Wire 的聚类/提炼（DEC-006）。
3. **密钥边界**：`QF_WIRE_API_KEY` 由 Wire 从它自己的 `.env/.env.local` 加载
   （真实环境 > .env.local > .env，cwd 机制决定）；LadderStock 的 yaml、argv、
   日志、提交都不出现密钥。不新购 key、不新订阅。
4. **诚实降级，整体取差者**：Wire 退出码 0→ok、8（部分源失败）→degraded、
   其余→failed；超时（900s 上限）/启动失败→failed；Wire 未安装→skipped。
   采集失败**不挡落库**（Wire 手里有什么就同步什么），但当日采集没达成整体
   记 failed，明细保留同步结果；同步再 skipped（peer 不可达）也是 failed。
   水位跟 `data_as_of` 走：empty/partial 不推水位、不拿旧数据顶替当日。
5. **幂等可补跑**：落库是 replace 整日覆盖，`qfw run` 同日重跑只覆盖当日；
   手动补跑 `ldst schedule run --once --job wire_news_daily`。
   cron `max_instances=1` 不重叠。
6. **注册面按 DEC-047 模式全量同步**：`JOB_KINDS` / `DEFAULT_JOBS` / `run_job`
   分发 / schema enum / 结构自检 / 仓库与随包两份 `schedule.yaml`（字节一致）。

## 考虑过的替代方案

- **把 `theme_news` 加进 `CLOSE_SYNC_DATASETS`（issue 的二选一）**：被否——
  15:35 仅交易日跑，给不了盘前/周末新鲜度；收盘同步只做落库、不触发采集，
  digest 仍是旧的（治标不治本）；还会改变收盘同步的数据集范围与退出码口径，
  回归面更大。独立 job 两个目标都满足且互不牵连。
- **LadderStock 直接抓 RSS / 调 LLM**：被否——踩 DEC-006 边界（数据只从生态
  peer 取），且重复实现 Wire 已有的去重/聚类/信源打分。
- **打开 Wire 自己的内置调度**：被否——Wire 是独立部署单元，改它的配置属于
  代管 peer 内部事务；且采集与落库的状态/水位观测会分裂在两个系统里。
  由 LadderStock 调度触发 `qfw run`，两段状态在一条 job 明细里如实可见。
- **只调 `wire_get_digest` 当作采集**：被否——这正是根因：该工具只读本地库，
  调一万次也不会产生新数据。

## 验收

- `tests/unit/test_schedule_wire_news.py`（33 项，全离线桩）：
  - 注册面：schema enum / 默认表 14 条 / 仓库 yaml / 结构自检；
  - 采集面：argv 与 cwd 契约、退出码 0/8/4/3 映射、无 JSON 摘要、超时、
    启动失败、Wire 未安装、**密钥哨兵不进 argv/明细**；
  - 分发面：8 种采集×同步状态组合、周末照跑、配置错折 failed、
    Wire 未安装不构造 registry；
  - 落库面（真 sync_one）：日分区 + 水位=当日、同日重复不膨胀、
    partial 降级但水位诚实、全赛道不可达不推水位不落分区；
  - 验收模拟：调度自动跑一次 → 水位=当日 → `GET /api/market/theme`
    `available=true`，全程零手动 sync。
- 离线隔离补口：CLI 矩阵的两处 autouse fixture 把 qfw 子进程通道也钉成
  「Wire 未安装」——`build_transport` 桩覆盖不到子进程，否则装了 Wire 的
  机器上矩阵会拨真采集（慢且花 token）。
- 收盘同步回归：`test_schedule_sync_close.py` / `test_watch_signals.py` 等
  既有断言全保留（job 数 13→14 是新增事实，不是改断言迁就）。
- `ruff check .` / `mypy`（191 文件）全绿；后端全量 pytest 绿。