/**
 * 真状态帧源（替换 `turnStatusMock`）：把 SSE 的 `event: status` 直接喂给 `ActivityFeed`。
 *
 * 后端 `chat.py:_stream_status` 只发 `{action, item, state, item_id?, elapsed_ms?, actions?}`，
 * **没有 seq**——`applyFrame` 靠 seq 单调判断崩序，所以这里本地自增补一个。
 * 缺 `item_id` 的帧（例如只报整体动作）按 `action` 兜底成一个稳定 id，避免每次
 * 都新起一行。
 *
 * 心跳帧不进行；它只表示连接还活着，`ActivityFeed` 的「已用时」本来就有本地计时兜底。
 */

import type { StatusFrame, TurnStatusFeed } from './turnStatus'

export interface LiveStatusFeed extends TurnStatusFeed {
  /** 吃一帧 SSE status；字段不全就按缺省补齐，绝不因此丢帧 */
  emitStatus(raw: Record<string, unknown>): void
  close(): void
  /** 当前已发出的最大 seq（调试 / 断言用） */
  readonly seq: number
}

function pickString(raw: Record<string, unknown>, key: string): string {
    const value = raw[key]
    return typeof value === 'string' ? value : ''
}

/**
 * `item` + `state` → 中文动作名。
 *
 * 后端只发机器可读的 item id（`history` / `model`），文案在这里定：同一 item 在
 * running / done 两个状态要说不同的话（「正在生成回答」→「回答已生成」）。
 * 未列出的组合回落到 item 原文，加新 item 时不必先改这里。
 */
const ITEM_LABELS: Record<string, Partial<Record<string, string>>> = {
    history: {
        running: '正在读取会话历史',
        done: '会话历史就绪',
        failed: '会话历史读取失败',
    },
    model: {
        running: '正在生成回答',
        done: '回答已生成',
        failed: '回答生成失败',
    },
}

/**
 * `tool` 是 agent 自己决定调用的取数工具（后端把中文名放在 `action` 里）。
 *
 * 这一行不是装饰：它让人看得见「模型正在查什么」——查不到时也有据可依，
 * 比一句「正在生成回答」更接近真实发生的动作。
 */
function labelFor(item: string, state: string, action: string): string {
    if (item === 'tool') {
        const name = action || '数据'
        return state === 'done' ? `${name}已取到` : `正在查${name}`
    }
    return ITEM_LABELS[item]?.[state] ?? item
}

export function createLiveStatusFeed(): LiveStatusFeed {
    let seq = -1
    const listeners = new Set<(frame: StatusFrame) => void>()

    return {
        subscribe(listener) {
            listeners.add(listener)
            return () => {
                listeners.delete(listener)
            }
        },
        emitStatus(raw) {
            const action = pickString(raw, 'action') || 'model'
            const rawItem = pickString(raw, 'item') || action
            const itemId = pickString(raw, 'item_id') || `auto:${action}`
            const item = labelFor(rawItem, pickString(raw, 'state') || 'running', action)
            const state = pickString(raw, 'state') || 'running'
            const elapsed = typeof raw.elapsed_ms === 'number' ? Math.max(0, raw.elapsed_ms) : 0
            seq += 1
            const frame: StatusFrame = {
                action,
                item,
                state,
                item_id: itemId,
                seq,
                elapsed_ms: elapsed,
                detail: typeof raw.detail === 'string' ? raw.detail : null,
            }
            for (const listener of listeners) listener(frame)
        },
        close() {
            listeners.clear()
        },
        get seq() {
            return seq
        },
    }
}
