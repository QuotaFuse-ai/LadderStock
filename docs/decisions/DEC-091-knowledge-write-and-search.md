# DEC-091：知识库写入面与跨票检索（E 包）

日期：2026-11-22
关联：DEC-006（隐私边界——本决策不动 journal 隐私库）、DEC-067 / QUOT-488
（知识库档案与向量投影）、DEC-087 ~ DEC-090（本轮功能包授权先例）、
DEC-025（估值段「数字唯一来源是 facts」红线——估值计算器的边界据此划定）
状态：已定稿（用户 2026-11-22 指令："继续"（E 包），按序推进）

## 1. 结论摘要

| 面 | 结论 |
|---|---|
| 用户笔记（写入） | 新库 `data/knowledge.sqlite`（notes / note_tags / note_note_tags 三表）；**不碰 `journal.sqlite`**（隐私库口径不动） |
| 笔记 API | `GET|POST /api/knowledge/notes`、`PUT|DELETE /api/knowledge/notes/{id}`、`GET /api/knowledge/notes/tags`——回环 + Host + 同源写面（与 alert 面同族） |
| 跨票检索 | `GET /api/knowledge/search?q=`：**向量层（qdrant，既有 `vectors.search`）优先，不可用时降级本地 LIKE 扫档案正文**——检索不因向量库挂了而不可用 |
| 估值计算器 | `ldst valuation` + `POST /api/valuation/estimate`：用户面工具（输入自算），**不进研究管线的估值段**（DEC-025 红线原样保留：段内数字唯一来源是 facts） |
| 批量研究 | `ldst research batch <codes...>`：串行队列复用既有 research 管线，逐票跑、逐票落 checkpoint，单票失败不拖垮队列 |

## 2. 为什么笔记单独建库而不是进 journal.sqlite

`journal.sqlite` 是隐私库（`core/privacy.py` 的硬边界 + `check_privacy`
的白名单口径）：trades / positions / watchlist 三表「不进 MCP / AI」。
用户笔记是**对标的的看法**，与档案（profile.md）同族——本地知识资产，
将来允许进 AI 召回上下文（与 DEC-088 的自选只读工具同一条授权思路）。
塞进隐私库只会扩大扫描面；单独建库让「笔记可被检索 / 注入」不需要
再开隐私白名单。

## 3. 跨票检索的两层降级

1. **向量层**（快，语义）：既有 `knowledge/vectors.py` 的 `search`
   （qdrant + bge-small-zh）——**不新建**，只开 Web 面；
2. **本地 LIKE 层**（稳，字面）：qdrant 不通 / 模型缺失时，扫
   `data/knowledge/*/profile.md` 的正文（`knowledge_root` 下的目录枚举，
   复用 `store.safe_path` 的词法前缀口径防越界）。命中数、每档来源
   （`vector` / `local`）在响应里如实分开——**不把降级路径伪装成语义命中**。

## 4. 估值计算器的边界（防碰 DEC-025 红线）

- 计算器是**用户面工具**：输入（PE/PB/PS/股息率/EPS/净资产/价格）由用户
  或页面显式提供，输出**声明为「非研究产物」**；
- 研究管线的 valuation 段**照旧**：数字唯一来源是 facts 快照，计算器结果
  **不进** facts、不进段内 prompt（`tools.py` 的 ALLOWED_TOOLS 不加新工具）；
- 页面文案固定写明「这是换算器，不是估值判断」。

## 5. 边界与红线

- 笔记 API：回环 + Host（读）/ + 同源来源证据（写）——与 `/api/alert` 同一套
  `privacy_read_guard` / `privacy_write_guard`，不新增鉴权体系；
- 笔记 body ≤ 20000 字符、标签 ≤ 24 条 × 32 字（服务端归一，不信前端）；
- 跨票检索的 `q` 长度上限 200 字符（防把检索面当文件读取器）；
- 估值计算器不写任何库（纯函数 + 只读日线可选）；
- 批量研究：队列串行（一次一票），**不并发**——research 管线里有
  DuckDB 读 + AI 调用，并发的失败面没人兜。

## 6. 未做 / 留给后续

| 事项 | 去向 |
|---|---|
| 笔记进 AI 召回上下文（与自选只读同族授权） | 需要时开新 DEC（当前只落库与页面） |
| 笔据的 markdown 渲染（页面） | 本包前端页一并做 |
| 向量层的笔记投影（把笔记也 embed） | 笔记量起来之后再说（当前 LIKE 够用） |
| 批量研究的定时任务形态 | `schedule.yaml` 的 `agent_research` 已有单票形态，批量属 CLI 侧 |

> AI生成
