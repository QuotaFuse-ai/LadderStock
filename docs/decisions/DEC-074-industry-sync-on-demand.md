# DEC-074 `/industry` 一键同步补齐：显式联网同步与只读重算分家

- 状态：已采纳（QUOT-533，2026-11-11）
- 关联：DEC-018（懒缓存与口径逐字段标注）、DEC-014（控制面命令白名单）、
  DEC-048（QFSD-7001 退避）、DEC-061（页面命令条策略）、QUOT-233 / QUOT-367（代码池与批量池）、
  `docs/specs/SPEC-INDUSTRY-METRICS.md` §1.1 / §1.4、`docs/SOURCES.md` §2.3

## 背景

`/industry` 数据不全不是「重算不出来」，而是**本机缓存没覆盖到页面要的代码**：

1. `config/sync.yaml` 的 `base_pool` 只有约 76 只锚点，不是全市场；
   行业清单（`config/industry.yaml`）里有 30+ 个行业、60 只锚点股，
   其中 32 只不在 `base_pool` 里——它们的申万归属 / 财务 / 估值 / 日 K / 资金流全是空；
2. 北向行业拆分与申万行业指数历史是**结构性缺口**（上游没有对应工具），同步不解决；
3. `data_as_of` 是 `sw_industry_map` 的分类计入日（`start_date`），不是行情日，
   语义不因补齐而改变；
4. 页面上原有的「重算行业快照」是 `ldst industry list`——**只读缓存重组、零网络**，
   无论点多少次都不会补一个字节。

## 决策

新增**显式用户触发**的真实联网同步，与只读重算在命令、控制面 key、文案三个层面分开：

| 动作 | CLI | 控制面 key | 语义 |
|---|---|---|---|
| 重算行业快照 | `ldst industry list` | `industry` | 只读本机缓存，零外呼（不变） |
| 同步补齐行业数据 | `ldst sync industry` | `industry-sync` | 真实上游请求，补懒缓存覆盖 |

### 范围与成本（明示，不靠猜）

- **数据集** = `industry/snapshot.py: SNAPSHOT_DATASETS` 的六个页面输入
  （`sw_industry_map` / `finance_indicators` / `valuation_history` / `fund_flow` /
  `industry_fund_flow` / `daily_k`），不另列一份、不抄第二套清单；
- **代码池** = `config/industry.yaml` 中已启用行业的锚点股（去重保序），
  `--codes` 可显式覆盖；**不是全市场**，`MAX_POOL_CODES = 100` 在发请求前校验；
- **成本非 0**：stderr 与载荷 `industry_scope.estimated_requests`
  （逐只数据集 × 代码数 + 批量数据集分批数 + 整批替换 1 次）同时给出上限；
- **同步语义不另造**：水位增量、`(code, trade_date)` 幂等 upsert、
  QFSD-7001 有界退避（DEC-048）、同库进程级写锁（QUOT-234）全部复用 `cli/sync.py`；
- **控制面**：只新增白名单 key，不新增 HTTP 路由。`/api/control/commands/{key}`
  原有的回环 + Host 白名单 + 写来源闸门、一次一个 command job、
  `PAGE_COMMAND_TIMEOUT_SEC` 全部继续生效；成本/范围通过 `GET /api/control/commands`
  的 `note` 字段透出，前端不自己拼文案。

### 不改变的语义

- `config/sync.yaml` 的 `codes`（懒缓存 DEC-018 默认值）不动；补齐是显式动作，
  定时 `ldst sync` 仍按原池子跑；
- 只读接口 `/api/industry` 与 `ldst industry list` 仍是零外呼；
- `data_as_of` 口径不变（`sw_industry_map` 的最新分类计入日）；
- 结构性缺口（北向行业拆分、申万行业指数历史）不在同步范围内，继续显式 `null`。

## 备选方案与否决理由

1. **把页面按钮直接改成 `ldst sync`（全量）**：会顺手同步资讯 / 财报事件等
   与行业无关的数据集，成本不可控，也不符合「补行业覆盖」的意图；
2. **给「重算行业快照」加一个 `--fetch` 开关**：同一个 key 有两种成本语义，
   按钮文案与日志无法自证「这次会不会联网」，审计时容易误判；
3. **把 6 个数据集写进 `config/sync.yaml` 的一个新分组**：多一份范围清单就多一处漂移；
   范围直接引用 `SNAPSHOT_DATASETS`，页面读什么就补什么；
4. **默认拉全市场**：违反成本红线，且上游逐只接口不支持；全市场口径应由盘后包备源承担。

## 影响与验收

- 新增 CLI `ldst sync industry`（`--codes` / `--full` / `--output`），
  控制面新增 `industry-sync` 白名单命令与 `note` 字段；
- 同步后 `ldst industry list` 中 `data_source_status=partial|ok` 的行应增加、
  `constituent_count` 从 0 变为正数（离线契约见
  `tests/integration/test_sync_industry.py`）；
- 前端按钮由独立子单承接：同一路由下必须同时渲染两条命令，文案区分
  「重算（只读）」与「同步补齐（联网、成本非 0）」。
