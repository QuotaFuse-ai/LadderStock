/**
 * mock 状态帧源（QUOT-585）。
 *
 * QUOT-581 的 `event: status` SSE 还没落地（并行中）：现行 `/api/conversation` 是同步
 * 端点，请求期间拿不到过程帧。这里给一个可手工 `emit` 帧的可替换 `TurnStatusFeed`，
 * 帧形状与 581 修正契约一致（action/item/state/item_id/seq/elapsed_ms）。
 * 581 落地后把 ChatPane 里的订阅换到 SSE 即可，`ActivityFeed` 与测试都不用动。
 */

import type { StatusFrame, TurnStatusFeed } from './turnStatus'

export function createMockStatusFeed(): TurnStatusFeed & {
  frames: StatusFrame[]
  emit: (frame: StatusFrame) => void
} {
  const listeners = new Set<(frame: StatusFrame) => void>()
  const frames: StatusFrame[] = []
  return {
    frames,
    emit(frame) {
      frames.push(frame)
      listeners.forEach((listener) => listener(frame))
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
  }
}
