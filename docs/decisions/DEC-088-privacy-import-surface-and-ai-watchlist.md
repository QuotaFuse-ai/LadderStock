# DEC-088：隐私边界两处新授权（持仓批量导入面 / AI 自选只读工具）

日期：2026-11-22
关联：DEC-006（隐私边界总纲）、DEC-015 / DEC-030（radar 持仓面授权先例）、
DEC-052 / DEC-055（radar 自选读写面）、DEC-058（收件箱）、DEC-087（提醒引擎）、
`scripts/check_privacy.py` 的 `JOURNAL_IMPORT_ALLOWLIST`
状态：已定稿（用户 2026-11-22 拍板："隐私两处——我同意"）

## 1. 结论摘要

| 引用 | 判定 | 条件（缺一不可） |
|---|---|---|
| `web/radar.py` → `journal.portfolio_import` | **批准**（补手续） | 预览走 `_positions_guard`、写入走 `_positions_write_guard`（回环 + Host；写面再加来源证据）；响应 `local_only` + `store=local_sqlite`；`no-store`；不进信封 / MCP / AI |
| `ai/agent_runtime.py` → `journal.watchlist` | **批准（口径变更，非补手续）** | 仅 `lookup_watchlist` 只读工具；仅模型自主调用；只读不回写；不进 MCP / 通知正文；**模型后端为本机网关时数据不出机器**；远端后端场景见 §4 |

`scripts/check_privacy.py` 的扫描规则**整体不变**：两处进白名单，各配「必须同时出现」的证据
字符串（守卫函数名 / `no-store` / `DEC-088` 标记）。其它任何对 `ladderstock.journal` 的
新引用仍判 FAIL。

## 2. 第一处：radar 持仓批量导入（补手续）

QUOT-430 / DEC-056 已批准 `radar.py` 读**持仓账本**（`journal.portfolio` 的流水重放与旧仓
迁移），判据是 `_positions_guard`（回环 + Host）+ `no-store`。本批新增的「贴券商对账单 →
预览 → 批量导入」是同一个持仓面的**写侧延伸**：

- `preview` 走 `_positions_guard`（只读预览，不落库）；
- `import_rows` 走 `_positions_write_guard`（= 读面闸门 + 写方法来源证据，QUOT-285 同款）；
- 与既有 position / portfolio 条目**同一条判据、同一份响应形态**（`local_only` +
  `store=local_sqlite`），不进信封、不进 MCP、不进 AI。

结论：这不是新开一扇门，是把已授权的持仓面补齐到「导入动作」。批准。

## 3. 第二处：AI 的「查我的自选」工具（口径变更）

### 3.1 变更了什么

旧口径（DEC-006 原文）：「`journal` 与 `sqlite_store` 的序列化对象里不含这三个表，
**永不出现在 MCP 输出和 AI prompt 里**」。

本批 `agent_runtime.py` 新增 `lookup_watchlist` 工具（模型在「我的自选今天怎么样」这类问题
下自主调用，返回 `{"items": [...]}` 的 JSON 文本给模型）。这是**自选清单第一次进入 AI
上下文**——用户 2026-11-22 明确同意放行，本条即口径修订：

> **自选清单（`journal.watchlist`）可以进入本机 AI 上下文，仅限只读工具
> `lookup_watchlist`；交易流水 / 持仓明细 / 提醒收件箱的「不进 AI」口径不变。**

### 3.2 放行的硬条件（代码与评审都按这四条看）

1. **只读**：工具只调 `list_watch`，不写、不改、不删；
2. **工具触发**：只在模型判断问题需要时调用（工具描述与 `ALLOWED_TOOLS` 白名单约束）；
   系统提示词**不预置**自选内容；
3. **不扩散**：自选不进 MCP 工具结果、不进通知正文、不进 web 接口响应（各自有独立授权面）；
4. **不进日志**：`tool_calls.jsonl` 只记入参摘要与结果摘要，不落自选明细原文。

### 3.3 本机默认 vs 远端后端（如实写明，不粉饰）

`config/ai.yaml` 的默认后端是**本机网关**（`http://127.0.0.1:8317/v1`）——默认配置下
自选明细不出本机。若用户把后端改成远端服务（如直连 OpenAI / Anthropic），模型上下文会
经网络离开本机，**自选明细也随之离开**——这是用户选择远端后端时自担的风险，DEC 写明，
不做额外拦截（拦截需要模型调用层识别「这段上下文含隐私」，当前不做）。

## 4. 与既有决策的关系

- **DEC-006**：本条是其「AI prompt 不含隐私表」的**唯一例外**（只含 watchlist、只读）；
  journal 其余两表（trades / positions）不进 AI 的口径**保持不变**；
- **DEC-030 / DEC-052 / DEC-056**：radar 面授权体系不变，本批只补 `portfolio_import`
  的写侧延伸；
- **DEC-087**：提醒正文可含持仓盈亏百分数（用户自己的渠道），与本文 §3.2 第 3 条不冲突
  ——那条约束的是「自选明细」这种列表原文。

## 5. 未做 / 留给后续

| 事项 | 去向 |
|---|---|
| 远端后端下自动脱敏（模型调用层识别隐私片段） | 未做；需要时先开新 DEC |
| AI 对自选的**写**能力（增删自选） | 不做，读写分离是底线 |
| 白名单证据字符串的集中管理 | 当前按文件内字符串校验（既有机制），不重构 |

> AI生成
