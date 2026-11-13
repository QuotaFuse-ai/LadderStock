/**
 * 对话等待态：动作流水账（QUOT-585 规格修正，替代原状态条）。
 *
 * - 一行一个具体动作，逐条追加到顶部「已用时」之下；同 `item_id` 原地 running→done。
 * - running 行带低干扰指示器（spinner）；reduced-motion 下换静态符号 ◐，
 *   「在动」载体仍是已用时数字（每 100ms tick，E5 红线）。
 * - 请求收口（done / 首块到达）后整块收成一行「共 N 步 · x.xs」，可点开看明细；
 *   首块到达后整体降级为窄条（compact），块内容接管画面。
 */

import { useEffect, useRef, useState } from 'react'
import { CircleX, Loader2 } from 'lucide-react'

import {
  TICK_INTERVAL_MS,
  applyFrame,
  EMPTY_FEED,
  type FeedState,
  type StatusFrame,
  type TurnStatusFeed,
} from './turnStatus'

export interface ActivityFeedProps {
  /** 状态帧源；可注入 mock，不依赖真实后端 */
  feed: TurnStatusFeed | null
  /** 兜底起点：帧未到达时已用时从发送时刻起算 */
  startedAt: number
  /** 首块已到达 / 请求已收口 → 折叠为窄条（可点开明细） */
  compact: boolean
  /** 收口时后端给的总耗时（`done` 帧的 `elapsed_ms`）；无则用本地计时 */
  totalElapsedMs?: number | null
  /** 强制隐藏（例如请求已收口且用户撤走流水账） */
  hidden?: boolean
}

function formatMs(ms: number): string {
  return ms >= 100_000 ? `${(ms / 60_000).toFixed(0)} 分 ${(Math.round((ms % 60_000) / 100) / 10).toFixed(1)} 秒` : `${(ms / 1000).toFixed(1)}s`
}

function formatElapsed(ms: number): string {
  return `已用时 ${(ms / 1000).toFixed(1)}s`
}

/** 折叠摘要「共 N 步 · x.xs」 */
function summaryText(items: FeedState['items'], totalMs: number): string {
  const steps = items.length
  return `共 ${steps} 步 · ${(totalMs / 1000).toFixed(1)}s`
}

function ActionRow({ item }: { item: FeedState['items'][number] }) {
  if (item.state === 'done') {
    return (
      <li data-testid="feed-item" data-item-id={item.itemId} data-state="done" className="flex items-center gap-1.5 text-caption text-text-secondary">
        <span aria-hidden="true" className="text-ok">✓</span>
        <span className="min-w-0 flex-1 truncate">{item.item}</span>
        {item.elapsedMs !== null && <span className="tabular-nums text-text-muted">{formatMs(item.elapsedMs)}</span>}
      </li>
    )
  }
  if (item.state === 'failed') {
    return (
      <li data-testid="feed-item" data-item-id={item.itemId} data-state="failed" className="flex items-start gap-1.5 text-caption text-danger" role="alert">
        <CircleX className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="block truncate">{item.item}</span>
          {item.detail && <span className="block text-text-muted">{item.detail}</span>}
        </span>
      </li>
    )
  }
  return (
    <li data-testid="feed-item" data-item-id={item.itemId} data-state="running" aria-busy="true" className="flex items-center gap-1.5 text-caption text-text">
      <Loader2 data-testid="feed-spinner" className="size-3.5 shrink-0 animate-spin motion-reduce:hidden text-accent" aria-hidden="true" />
      <span data-testid="feed-static-marker" className="hidden shrink-0 motion-reduce:inline text-accent" aria-hidden="true">
        ◐
      </span>
      <span className="min-w-0 flex-1 truncate">{item.item}</span>
    </li>
  )
}

export function ActivityFeed({ feed, startedAt, compact, totalElapsedMs = null, hidden = false }: ActivityFeedProps) {
  const [state, setState] = useState<FeedState>(EMPTY_FEED)
  const [, setTick] = useState(0)
  const [expanded, setExpanded] = useState(false)
  const seqRef = useRef(-1)
  const listRef = useRef<HTMLUListElement>(null)
  const stickToBottomRef = useRef(true)

  useEffect(() => {
    seqRef.current = -1
    setState(EMPTY_FEED)
    setExpanded(false)
    if (!feed) return
    return feed.subscribe((frame: StatusFrame) => {
      if (frame.seq < seqRef.current) return
      seqRef.current = frame.seq
      setState((prev) => applyFrame(prev, frame))
    })
  }, [feed])

  // 本地计时器：每 100ms 重算一次已用时。后端零帧时这是唯一的"在动"（E5 兜底）。
  useEffect(() => {
    const timer = window.setInterval(() => setTick((n) => n + 1), TICK_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [])

  // 自动滚到底；用户手动上滚后不再抢滚动
  useEffect(() => {
    const node = listRef.current
    if (node && stickToBottomRef.current) node.scrollTop = node.scrollHeight
  }, [state.items.length])

  const onScroll = () => {
    const node = listRef.current
    if (!node) return
    stickToBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24
  }

  const elapsed = totalElapsedMs ?? performance.now() - startedAt
  const runningCount = state.items.filter((item) => item.state === 'running').length
  const collapsed = compact && !expanded

  if (hidden) return null

  const rows = state.items
  const visible = collapsed ? rows.slice(-4) : rows
  const hiddenCount = rows.length - visible.length

  return (
    <div
      data-testid="activity-feed"
      role="status"
      aria-busy={runningCount > 0 || !compact}
      className={compact ? 'flex flex-col gap-1' : 'flex flex-col gap-1 rounded border border-border bg-surface-2 px-3 py-2'}
    >
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          data-testid="feed-summary"
          className="min-w-0 truncate text-left text-caption text-text-muted hover:text-text-secondary transition-colors duration-fast"
          aria-expanded={compact ? expanded : undefined}
        >
          {compact ? summaryText(rows, elapsed) : formatElapsed(elapsed)}
        </button>
        {collapsed && hiddenCount > 0 && (
          <span className="shrink-0 text-caption text-text-muted">+{hiddenCount}</span>
        )}
      </div>
      <ul
        ref={listRef}
        onScroll={onScroll}
        data-testid="feed-list"
        aria-label="动作流水账"
        className={collapsed ? 'hidden' : 'flex max-h-48 flex-col gap-1 overflow-y-auto'}
      >
        {visible.map((item) => (
          <ActionRow key={item.itemId} item={item} />
        ))}
      </ul>
    </div>
  )
}
