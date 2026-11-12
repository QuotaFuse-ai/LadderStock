# QUOT-566 助手问答链接入标的档案召回

问题：`web/assistant.py ask()` 之前只召回 mem0 记忆，`knowledge/recall.py` 的
`recall_wrapped()` 除 `GET /api/knowledge/:market/:symbol` 外没有任何调用方——
用户在家页问「浦发银行三季度业绩怎么样」，模型拿不到档案，只能回「页面没取到」。

本单把**知识库档案召回**接进同一条问答链，与 mem0 记忆**物理分段、各自带边界**：

```
SYSTEM_PROMPT
【对话记忆（可能过时，仅作线索）】   ← mem0 命中（800 字预算）
【标的档案（带 as_of，优先于对话记忆）】 ← recall_wrapped() 成品，原样放，不截断
_MEMORY_CONFLICT_RULE               ← 冲突时以档案/页级上下文为准
```

## 改动

| 文件 | 内容 |
|---|---|
| `src/ladderstock/web/assistant.py` | `resolve_target()` 解析 `market:symbol`；`_recall_knowledge()` 调 `recall_wrapped()` 并吞掉所有异常（`knowledge_unavailable` DEBUG 日志）；`ask()` 新增 `data_root`，经 `render_memory_block` 把档案段排在记忆段之后；三个 router 工厂透传 `data_root` |
| `src/ladderstock/ai/memory.py` | `render_memory_block(hits, knowledge=...)`：档案正文原样放在独立段，**不二次包边界、不按 800 字截断**（限额归 `recall.py` 的 12000 字章节截断）；空态文案改为「该标的没有可用档案」 |
| `src/ladderstock/web/server.py` | `build_assistant_router(root)` / `build_skills_router(root)` / `build_page_router(root)`，与 REST 档案面读同一份 `data_root` |
| `src/ladderstock/knowledge/store.py` | **缺陷修复**：`render_run_archive()` 的 frontmatter 补齐 `market` / `status` / `valid_days`。原归档落的 `latest.md` 缺 `market`，`dossier_from_text()` 整份拒绝 → 归档产物永远召不回来（接线时实测发现） |
| `src/ladderstock/knowledge/recall.py` | **未改一行** |

## 标的解析口径

- 优先级：调用方的 `symbol`（研究页 code）> 问题文本里第一个代码；
- 认 `600000` / `600000.SH` / `sh600000`，大小写不敏感；
- 无显式市场时按号段推断：`6`→SH、`0`/`3`→SZ、其余→BJ；
- 不做公司名→代码的模糊匹配（名称库不在本单范围），解析不出 → 跳过档案段；
- 数字前紧贴 `.` `,` `、` 不算代码（「10.600000 元」不会被当成浦发银行）。

## 降级

档案缺失 / `refuted` / 解析不出标的 / 读取抛错，一律返回「无档案」，提示词不含
档案段，`ask()` 正常返回；异常只留一条 `knowledge_unavailable` DEBUG 日志
（与 `memory_unavailable` 同级），不影响回答。

## 验证

命令与结果见同目录 `acceptance.md`。
