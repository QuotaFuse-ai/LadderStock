/**
 * 对话等待态数据层（QUOT-585 规格修正）：动作流水账。
 *
 * 帧源形状按 QUOT-581 修正后的 `event: status` 契约写死（并行落地，前端不等待）：
 *   { "stage": "context" | ...,
 *     "action": "history" | "context" | "model" | "assemble" | "persist",
 *     "item": "正在读取行情与资金数据",
 *     "state": "running" | "done" | "failed",
 *     "item_id": "c1", "seq": 0, "elapsed_ms": 340,
 *     "heartbeat"?: boolean, "detail"?: string }
 *
 * 核心规则：**同 `item_id` 的 done/failed 帧原地把那一行从 running 更新过去**，
 * 不新增行；崩序（seq 回退）整帧丢弃。接口设计为**可注入**（`TurnStatusFeed`），
 * 真后端落地前由 mock 帧源驱动；零帧时「已用时」由本地 100ms 计时器兜底（E5 红线）。
 */

export type ActionState = 'running' | 'done' | 'failed'

/** 后端 status 帧原样形状（QUOT-581 修正契约） */
export interface StatusFrame {
  stage?: string
  action: string
  item: string
  state: ActionState | string
  item_id: string
  seq: number
  elapsed_ms: number
  heartbeat?: boolean
  /** failed 帧的原因 */
  detail?: string | null
}

export interface TurnStatusFeed {
  /** 订阅当前轮的状态帧；返回注销函数 */
  subscribe(listener: (frame: StatusFrame) => void): () => void
}

/** 本地兜底计时步长：100ms —— 数字持续跳动，reduced-motion 下也"在动" */
export const TICK_INTERVAL_MS = 100

/** 流水账的一行：同 item_id 只有一行，running → done/failed 原地更新 */
export interface ActionItem {
  itemId: string
  action: string
  item: string
  state: ActionState
  /** done / failed 帧带耗时；running 行为 null */
  elapsedMs: number | null
  detail: string | null
}

export interface FeedState {
  items: ActionItem[]
  /** 已见最大 seq（乱序丢弃基准；无帧时 -1） */
  seq: number
}

export const EMPTY_FEED: FeedState = { items: [], seq: -1 }

function asState(value: string): ActionState {
  return value === 'done' || value === 'failed' ? value : 'running'
}

/** 崩序帧丢弃；同 item_id 原地更新，新 id 追加到底部 */
export function applyFrame(prev: FeedState, frame: StatusFrame): FeedState {
  if (frame.seq < prev.seq) return prev
  const next: ActionItem = {
    itemId: frame.item_id,
    action: frame.action,
    item: frame.item,
    state: asState(frame.state),
    elapsedMs: frame.state === 'running' ? null : Math.max(0, frame.elapsed_ms),
    detail: frame.detail ?? null,
  }
  const index = prev.items.findIndex((item) => item.itemId === frame.item_id)
  const items =
    index >= 0
      ? prev.items.map((item, i) => (i === index ? next : item))
      : [...prev.items, next]
  return { items, seq: frame.seq }
}
