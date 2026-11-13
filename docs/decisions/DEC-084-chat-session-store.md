# DEC-084：对话会话落库——新建 chat.sqlite，能力迁进现役 assistant 链路（QUOT-580）

日期：2026-11-13
关联：QUOT-580（拆自 QUOT-572 阶段 3）、DEC-067（对话会话进程内记忆，本决策取代其
「不做持久化」）、DEC-006（隐私边界：会话内容不进 envelope / MCP / AI 提示词）、
QUOT-577（阶段 A 答案块协议）、SPEC-AI-CHAT.md
状态：已定稿

## 背景

对话模式此前把会话记忆放在进程内 `OrderedDict`（DEC-067）：重启即清空，列表页无可
展示的历史。QUOT-580 要求会话与轮次落库，供下游阶段 F 的 `SessionList` 消费；同时
派单给出四项已拍板口径（D1 / D2 / D3 / D7），本决策记录它们的落地方式与理由。

## 决策

1. **D1 —— 另建 `chat.sqlite`，不挤进 duckdb / 现有 sqlite。**
   会话与轮次是「用户私有意图」，生命周期、备份、清理口径都与行情数据无关；写进
   duckdb 的行情库会让行情重建（全量刷新、schema 迁移）牵连用户对话，也让隐私边界
   （DEC-006）变模糊。故新建独立库文件，落在既有数据根目录下：
   - 路径：`<data_root>/chat.sqlite`，由 `chat_store.db_path(root)` 给出；
   - 不动 `duckdb_store.py` / `sqlite_store.py` 的现有 schema，一行不改；
   - 复用既有 sqlite 基建习惯：WAL、`foreign_keys=ON`、`busy_timeout=5000`、
     `PRAGMA user_version` + `schema_meta` 双版本号、`BEGIN IMMEDIATE` 事务；
   - 版本号高于本代码认识的上限时拒绝打开（`LadderStockError(..., EXIT_CONFIG)`），
     避免旧版程序写坏新库。

   表结构（v1）：

   ```sql
   CREATE TABLE IF NOT EXISTS schema_meta (
       key   TEXT PRIMARY KEY,
       value TEXT NOT NULL
   );
   CREATE TABLE IF NOT EXISTS chat_session (
       session_id TEXT PRIMARY KEY,
       title      TEXT NOT NULL,
       created_at TEXT NOT NULL,
       updated_at TEXT NOT NULL
   );
   CREATE TABLE IF NOT EXISTS chat_turn (
       session_id  TEXT NOT NULL REFERENCES chat_session(session_id) ON DELETE CASCADE,
       seq         INTEGER NOT NULL,
       question    TEXT NOT NULL,
       blocks_json TEXT NOT NULL,
       created_at  TEXT NOT NULL,
       PRIMARY KEY (session_id, seq)
   );
   CREATE INDEX IF NOT EXISTS idx_chat_session_updated ON chat_session(updated_at DESC);
   CREATE INDEX IF NOT EXISTS idx_chat_turn_created ON chat_turn(session_id, created_at);
   ```

   落库口径：
   - `seq` 从 1 开始、按会话递增（`MAX(seq)+1`），主键 `(session_id, seq)` 兜底；
   - `created_at` 单调不减：新轮次的时间取 `max(now, 本会话上一轮的 created_at)`；
   - 首轮建会话，`title = 首问压空白后截 40 个 Unicode 码位`（不做省略号，表情按码位
     计），空白则回落「未命名对话」；
   - 每轮成功都刷新 `chat_session.updated_at`；
   - 会话上限是常量 `MAX_SESSIONS = 50`（`chat_store.append_turn(..., max_sessions=)`），
     淘汰按 `updated_at ASC` 挑最旧的非当前会话，`chat_turn` 依 `ON DELETE CASCADE`
     一并清掉；当前会话永不淘汰。

2. **D3 —— 能力迁进现役链路，`chat.py` 保留不删不挂。**
   `ChatSource` / `ChatRequestContext` / `ChatRequest` / `ChatFailure` 与来源注记文案
   迁入 `src/ladderstock/web/assistant.py`，现役问答链（`run_turn` → `ask`）直接用这批
   类型，服务端从不从上下文反推来源。`src/ladderstock/web/chat.py` 原样保留且不挂载
   到 `create_app`，留作对照与后续合并，本单不删、不改。

3. **D2 —— 块上限取 6。**
   `assistant.MAX_ANSWER_BLOCKS = 6`，装配后 `blocks[:6]` 截断。阶段 A 协议层自带的
   `MAX_BLOCKS = 8` 是纯函数层的校验上限，本单不改；对话端点按 D2 收在 6。

4. **D7 —— 新增 `notice.kind = "missing_data"`。**
   本轮没有任何可核实来源时补一条 `notice(kind="missing_data", section="来源")`，提醒
   「没数据、不编」。该 kind 独立新增，不复用 `AiErrorNotice` 既有 6 种，也不改名。

5. **端点落在 `/api/conversation`。**
   `GET /sessions`（列会话）、`GET /sessions/{id}/turns`（取一轮）、
   `PATCH /sessions/{id}`（重命名）、`POST /turns`（追加一轮）。守卫沿用
   `research._guard` 的既有闸门，不另开旁路；写失败与读失败都降级为可读失败响应，
   不让会话库的问题拖垮问答本身。

## 权衡

- **独立库 vs 复用 duckdb**：多一个文件、多一套连接与迁移；换来行情重建与用户对话
  解耦、隐私面清晰、备份粒度可控。会话量级（≤50 会话 × 每会话数十轮）远小于行情，
  单独 sqlite 的开销可忽略。
- **会话记忆仍在请求内拼**：历史只作同一 `session_id` 的用户消息片段，不进
  `LadderEnvelope` / MCP 工具结果 / 系统提示词固定段，守 DEC-006。
- **淘汰口径按「当前会话不淘汰」**：用户正在进行的会话永远在列表里，避免自己刷新
  页面就丢上下文；代价是上限紧张时可能淘汰刚切走的历史会话——这是有意的取舍。

## 红线对账

1. `reply` 类型与语义不变（仍是字符串，原样透传）；
2. `blocks` 只由 `assistant.build_answer_blocks` 从 `metrics + ai` 装配，模型不产出；
3. 无第三套问答实现，`chat.py` 未删未挂；
4. 叙述文本去数字：块结论过 `strip_numbers`，`reply` 不动；
5. 未改 `AI_WRITABLE` / `assert_narrative_only`；
6. 无来源即 `notice(missing_data)`，不编数据；
7. 不造路由，新端点登记在 SPEC-AI-CHAT.md；
8. 无买卖建议 / 价格预测；
9. 未放松任何既有断言；
10–12. 前端未动（侧边栏、`Layout.tsx`、`HomeScreen`）；
13. `HomeShell` 未动；
14. 未重建组件；
15. `tailwind.config` 未动。
