# DEC-087：提醒规则引擎与出站通知渠道（自定义规则 × 外发投递）

日期：2026-11-21
关联：DEC-006（生态边界——本决策只动"通知出站"，不动"数据入站"）、DEC-058（本机收件箱）、
DEC-028（缺数不顶替）、DEC-043 盯盘信号（同族"越过才报一次"状态机）、CLI.md §B `notify test`
状态：已定稿（用户 2026-11-21 拍板："都实现咯 + 我需要实现真实代码"，本单为功能包 A 的第一个）

## 1. 结论摘要

| 项 | 结论 |
|---|---|
| 规则模型 | 7 类：`price_above / price_below / ma_break_down / ma_break_up / volume_spike / pnl_drop / pnl_gain` |
| 触发语义 | **越线报一次**：`armed → 触发报 → cooldown → 条件离开才重臂`，与 `watch/signal.py` 的 `above` 去重同族 |
| 存储 | `data/alerts.sqlite`（独立于隐私库 `journal.sqlite`；四张表 rules / rule_state / history / channels） |
| 出站渠道 | `console` / `desktop`（仍纯本机，委托 `watch/notifier`）+ `email(SMTP)` / `webhook` / `dingtalk` / `feishu` / `wecom`（**HTTP 出站，DEC-006 旧红线在此子域被显式推翻**） |
| 执行体 | `alert/run.py:run_once()` 唯一；CLI（`ldst alert run`）、Web（`POST /api/alert/run`）、调度（`kind: alert_check`）三面共享 |
| 收件箱 | 触发**必**落 `journal/inbox`（`kind="alert"`，按 `规则id:交易日` 去重）；渠道只是投递，本机留痕是源头 |
| 管理面 | `ldst alert rules|channels|run` + `/api/alert/*`（回环读 / 同源写，复用 radar 闸门） |

## 2. 为什么推翻 DEC-006 的"只本机"通知红线

DEC-006 落地的 2026-10 语境是**数据面**：不采集、不引第三方数据源、不给行情站点发请求。
`watch/notifier.py` 当时的注释把"不发网络请求"一并写进了通知面——那是因为当时只有
console / desktop 两种渠道，没人需要外发。

现状（2026-11-21）：平台已定位为"普通人可用的投研 Agent"，提醒只落本机意味着
**用户不看收件箱就等于没提醒**。P0 需求（用户拍板）要求"主动找人"。因此边界收窄为：

1. **出站只发提醒正文**（title / body / 时间戳），**永不携带**：渠道密钥、用户配置全文、
   任何数据集原始记录；body 可含持仓盈亏百分数（用户自己的数据投到用户自己配的渠道，
   与 F11 简报投递 inbox+desktop 同口径）；
2. **密钥只存名字不存值**：webhook URL 落库但**读面一律打码**（`channels.public_config`）；
   SMTP 密码只存环境变量名（默认 `LDST_ALERT_SMTP_PASSWORD`），值不进库、不进日志、不回显；
3. **数据入站侧零改动**：不采集、不碰 sources.yaml、不新增数据集——求值原料全部来自
   现役链路（`watch_prices` 现价、`daily_k` 本地缓存、`journal/position` 成本）。

`watch/notifier.py` **一个字不改**：console / desktop 的旧语义原地保留，出站能力在新模块
`alert/channels.py`，旧代码无需迁移。

## 3. 缺数语义（红线落地）

- 规则求值原料缺了（现价没取到 / 历史不足窗口 / pnl 规则无持仓）→ 该规则 `status="no_data"`
  + 原因原样带回，**不触发**；不拿昨收 / 成本价 / 0 顶替（DEC-028 同口径）；
- 一轮里出现 `no_data` 或投递降级 → 整体 `status="partial"`；全部干净 → `"ok"`；
  没有任何启用规则 → `"skipped"`（"没配"与"没触发"是两个状态）；
- `volume_spike` 的均量分母 < 100 手 → `no_data`（低流动性票的倍数是噪声，不是信号）。

## 4. 已声明接受的代价

| 代价 | 理由 |
|---|---|
| 外部渠道挂了，本机收件箱已落的那条**不会**在渠道修复后补发 | 状态机在触发时即进 cooldown；"补发"需要渠道重试队列，v1 不做（记入 §6） |
| 盘中 `ma_*` / `volume_spike` 用的是"运行中最新价充当当日收盘"的口径 | 严格口径（收盘才判）等于把均线规则降级成日线规则，用户要的恰是盘中提醒；口径已写进 `rules.py` 模块注释 |
| 钉钉 / 飞书 / 企微只支持**自定义机器人 webhook**（关键词/加签校验不支持 v1） | 加签需要 HMAC 实现，v1 先覆盖最大公约数；`errcode != 0` 会如实带回 `errmsg` |
| SMTP 明文端口（非 587/465）按服务器能力决定 TLS | starttls 失败时按明文发（服务器不支持 STARTTLS 是用户配置选择），结果如实回传 |

## 5. 边界与红线

- 出站请求只允许**用户配置的 URL / SMTP 主机**（渠道表里的 `url` / `smtp_host` 字段），
  无任何内置远端地址；超时 10s，失败降级不抛（沿 `watch/notifier` 语义）；
- 三家 IM 的 body 业务码（`errcode` / `code`）必须为 0 才算送达——HTTP 200 不代表成功；
- `/api/alert/*` 按隐私面处理：回环 + Host 白名单读，写面加同源 `Origin` 或
  `X-LDST-Client: 1`（复用 `web/radar` 的 guard，不新增鉴权体系）；
- 隐私扫描边界不动：`journal.sqlite` 新增零张表；求值读持仓只取 `cost` 一个字段，
  经 `alert/run._cost_map`，不进任何 prompt / MCP 工具。

## 6. 未做 / 留给后续

| 事项 | 去向 |
|---|---|
| 前端规则管理页（列表 / 新建 / 编辑 / 渠道配置 UI） | 包 A 第二步（后端契约已冻结：`/api/alert/*`） |
| 渠道投递失败的重试队列与补发 | 触发历史已留 `deliveries` 摘要，够诊断；补发机制待真实使用反馈再定 |
| 钉钉加签（HMAC-SHA256）/ 飞书签名校验 | 需要时加 `secret` 字段（落库亦打码） |
| 更多规则类型（MACD 金叉、连阳计数、涨跌停触板…） | `rules.RULE_KINDS` 是开放注册点，但每类都要先写求值单测 |
| 提醒模板自定义（正文措辞） | v1 固定「结论 + 依据 + 时点 + 免责声明」四段 |

> AI生成
