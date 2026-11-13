# DEC-085：对话落库改为「边到达边 upsert」+ `chat_turn.status` 四态（QUOT-580 第二轮）

日期：2026-11-13
关联：QUOT-580（拆自 QUOT-572 阶段 3）、DEC-084（`chat.sqlite` 与会话表，本决策在其
v1 结构上加列，不推翻其「独立库」结论）、DEC-067（已被 DEC-084 取代）、
QUOT-581（阶段 D 流式）、QUOT-582（阶段 F `SessionList` / `ChatPane`）、
SPEC-AI-CHAT.md §4.1 / §4.1.1 / §6.3
状态：已定稿

## 背景

DEC-084 按任务书原文落地为「每轮问答**成功后**写一条 `chat_turn`」。第二轮评审指出这
与阶段 D 的流式目标冲突：块是逐步到达的，用户在第二块后断开时，按「成功后写」这一轮
在库里什么都不留，D 的续传也就无从谈起。

同时 D7 只批了 `notice.kind = "missing_data"` 一种新 kind，红线又禁止改名复用
`AiErrorNotice` 既有 6 种。「用户点了停止、内容定格」不是数据缺失，不能塞进
`notice.kind`，需要另一个载体。

## 决策

### 1. 写入时机 = 落点 B「边到达边 upsert」

- 首块到达：插入 `chat_turn` 行，`status = streaming`，`blocks_json` 只存已到达的块；
- 后续每块：**更新同一行**的 `blocks_json`（`UPDATE ... WHERE session_id = ? AND seq = ?`），
  不插新行；
- 收尾：同一行落终态 `done` / `aborted` / `failed`。
- 同一轮始终只有一行 `chat_turn`，`status` 是同一行的字段流转，不是新行。
  与阶段 D 的 upsert 天然兼容。

不选落点 A（保持「成功后写」）的理由：阶段 C 已要求「重启服务后会话仍在」，而 A 在
断线轮次上同时丢内容与续传能力，D 开工时必须再改一次库结构；本单一次改到位，
QUOT-581 只接事件流即可。

### 2. `chat_turn` 加 `status` 与 `updated_at`（迁移 v2）

```sql
ALTER TABLE chat_turn ADD COLUMN status TEXT NOT NULL DEFAULT 'streaming';
ALTER TABLE chat_turn ADD COLUMN updated_at TEXT;
```

迁移只做加列（SQLite 不能改列约束），`blocks_json` 保持 v1 的 `NOT NULL`，流式首块用
`'[]'` 占位。存量行一律回填 `done`——v1 只有「成功后写」一种写入路径，存量都是成品。
回填写成**无条件** `UPDATE chat_turn SET status = ?`（不依赖 ADD COLUMN 的 DEFAULT），
结果对新旧 SQLite 构建一致。

**四态语义（逐条）**：

| 值 | 含义 | `blocks_json` |
| --- | --- | --- |
| `streaming` | 正在生成；首块到达时置 | 可能只含已到达的块 |
| `done` | 正常完成（`done{completed:true}`） | 本轮完整块 |
| `aborted` | **用户主动停止**，内容定格，可作为下一问上下文 | 已到达的块 |
| `failed` | 服务端失败（模型 / 数据错，`done{completed:false}`） | 一条失败说明 `notice` |

终态只写一次：`update_turn_blocks` / `finish_turn` 都带 `AND status = 'streaming'`
条件，`aborted` 的定格内容不会被晚到的块覆盖。进程退出时 `streaming` 行留在库里，
重启后读得到（验收见 §3）。

### 3. 「已停止」的载体是 `status`，不是 `notice.kind`

- `notice.kind` 集合不变：既有 6 种 + D7 的 `missing_data`，**本轮不新增任何 kind**；
- 前端（QUOT-582）直接读 `status`：`aborted` →「已停止」+ 可继续问；`failed` →
  失败提示 + 重试；`streaming` →「生成中」（多标签页恢复）。前端不自己加 kind。

### 4. 命名纪律

- `chat_turn.seq` = **轮次序号** `turn_seq`（1,2,3…，跨轮递增，主键
  `(session_id, seq)`）；
- 流式 `event: block` 的 `seq` = **块序号** `block_seq`（1..N，每轮重置）；
- 两个 seq 不共用字段名，日志里分别打 `turn_seq` / `block_seq`，不混着打。

## 验收（本单已覆盖）

- `status` 四态各一条测试，含「用户停止 → aborted」与「模型失败 → failed」可区分；
- 同一轮多次更新只产生一行 `chat_turn`，`status` 从 `streaming` 流向终态；
- `aborted` 行的 `blocks_json` 能完整读出（`/turns` 接口返回，供 QUOT-582 恢复）；
- 未新增 `notice.kind`，`AiErrorNotice` 6 种不变；
- 迁移上线后重启服务再读，四态与 `blocks_json` 都在；
- 「第 2 块后杀进程」：子进程硬退、父进程重开库，读回已落的前 2 块
  （`scripts/quot580_kill_demo.py`，退出码 9 后 PASS）。

## 影响

- 会话库 schema v1 → v2，旧库首启自动迁移；
- `GET /sessions/{id}/turns` 的每条轮次多 `status` 与 `updated_at` 两个字段；
- `POST /turns` 成功响应多 `status`；
- `chat.py` 仍保留未挂载，本决策不涉及它。
