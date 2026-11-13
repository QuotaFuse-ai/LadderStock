/**
 * 对话等待态指示条（QUOT-585 · L1 阶段 + L2 已用时）。
 *
 * - 阶段文字来自 `event: status` 的 `label`（QUOT-581）；未收到帧时按 L0 兜底文案。
 * - 「已用时 x.xs · 第 n/m 步」每 100ms 本地刷新（TICK_INTERVAL_MS）：后端零帧也持续变化，
 *   `prefers-reduced-motion` 关掉所有动画后数字仍跳，不出现死屏（E5 红线）。
 * - 首块到达后置 `compact`（窄条），块内容接管画面；整轮结束后由父级卸载。
 */

import { useEffect, useRef, useState } from 'react'

import {
  TICK_INTERVAL_MS,
  fallbackDisplay,
  mergeFrame,
  type StatusDisplay,
  type StatusFrame,
  type TurnStatusFeed,
} from './turnStatus'

export interface WaitStatusProps {
  /** 状态帧源；可注入 mock，不依赖真实后端 */
  feed: TurnStatusFeed | null
  /** 兜底起点：帧未到达时已用时从发送时刻起算 */
  startedAt: number
  /** 首块已到达 → 降级为窄条 */
  compact: boolean
  /** 强制隐藏（例如请求已收口，动画中的卸载过渡） */
  hidden?: boolean
}

function formatElapsed(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

export function WaitStatus({ feed, startedAt, compact, hidden = false }: WaitStatusProps) {
  const [display, setDisplay] = useState<StatusDisplay>(() => fallbackDisplay(startedAt))
  const [, setTick] = useState(0)
  const startedAtRef = useRef(startedAt)
  startedAtRef.current = startedAt
  const seqRef = useRef(-1)

  useEffect(() => {
    seqRef.current = -1
    setDisplay(fallbackDisplay(startedAtRef.current))
    if (!feed) return
    return feed.subscribe((frame: StatusFrame) => {
      if (frame.seq < seqRef.current) return
      seqRef.current = frame.seq
      setDisplay((prev) => mergeFrame(prev, frame, performance.now()))
    })
  }, [feed])

  // 本地计时器：每 100ms 重算一次已用时。这是 E5 的兜底，不依赖任何后端帧。
  useEffect(() => {
    const timer = window.setInterval(() => setTick((n) => n + 1), TICK_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [])

  const elapsed = display.baseElapsedMs + Math.max(0, performance.now() - display.baseAt)

  if (hidden) return null

  const stageText = `第 ${display.stageNo}/${display.stageTotal} 步`
  const elapsedText = `已用时 ${formatElapsed(elapsed)}`

  if (compact) {
    return (
      <p
        data-testid="wait-status"
        aria-busy="true"
        className="text-caption text-text-muted"
      >
        {display.label} · {elapsedText}
      </p>
    )
  }

  return (
    <div
      data-testid="wait-status"
      role="status"
      aria-busy="true"
      className="flex flex-col gap-0.5 rounded border border-border bg-accent-soft px-3 py-2"
    >
      <p className="text-body text-text" data-testid="wait-status-label">
        {display.label}
      </p>
      <p className="text-caption text-text-muted" data-testid="wait-status-meta">
        {elapsedText} · {stageText}
      </p>
    </div>
  )
}
