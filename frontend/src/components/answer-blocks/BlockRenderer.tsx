import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'

import type { RenderedBlock } from './types'
import { parseBlocks } from './types'
import { ANSWER_FLOW, EmptyAnswerPanel, InvalidBlockPanel, UnreachableActionPanel } from './BlockShell'
import { ConclusionBlock } from './ConclusionBlock'
import { MetricBlock } from './MetricBlock'
import { ChartBlock } from './ChartBlock'
import { TableBlock } from './TableBlock'
import { CitationBlock } from './CitationBlock'
import { NoticeBlock } from './NoticeBlock'
import { ActionBlock } from './ActionBlock'
import { isKnownTarget } from './targets'

/**
 * `BlockRenderer`：答案块的**唯一**渲染入口（任务书阶段 E）。
 *
 * 「按 type 分发」这件事只在这一处发生——页面侧拿到 `blocks` 原样传进来即可，
 * 不许在外面写 `if (block.type === ...)`。新增块类型时只改这里的 `dispatch` 表。
 *
 * 用法（阶段 F 的 ChatPane 也按这个签名接）：
 *
 * ```tsx
 * <BlockRenderer
 *   blocks={turn.blocks}            // 后端原样透传的 [{type, payload}]（raw 也可）
 *   onRetry={() => rerun(turnId)}   // 可选：notice 的「重试该块」
 *   onOpenStatus={() => navigate('/settings')}
 * />
 * ```
 *
 * 兜底都在这里：形状不合法 → 占位说明；`action.target` 不在路由表 → 说明而不是死按钮；
 * 整列没有可渲染块 → 明确说「没有可展示的块」，不伪造结论。
 *
 * 【整块进、整块出】块只在解析那一刻成形，块内文本是服务端定稿、**不是流式增量**：
 * 块内不做打字机 / 逐字渐显（既有语义错位，也等于把逐字重排搬回组件内部）——
 * 文本一次性完整呈现，动效只留 `duration-fast` 120ms 的 hover / 展开档。
 * 块是不可变对象：`parseBlocks` 每次返回新对象、组件不就地改载荷；阶段 F 的 `ChatPane`
 * 靠这个前提做增量 append（新块直接追加到 `blocks` 尾部，旧块引用不变即可免重绘）。
 */

export interface BlockRendererProps {
  /** 后端给的 `blocks` 数组（未解析也行）；形状由 `parseBlocks` 把关 */
  blocks: unknown
  /** 提示块的重试回调（整轮失败时不会摆「重试该块」） */
  onRetry?: () => void
  onOpenStatus?: () => void
  /** 覆盖跳转：测试或嵌入场景下不接 react-router 时用 */
  onNavigate?: (target: string) => void
  className?: string
}

export function BlockRenderer({ blocks, onRetry, onOpenStatus, onNavigate, className }: BlockRendererProps) {
  const routerNavigate = useNavigate()
  const parsed = useMemo(() => parseBlocks(blocks), [blocks])
  const wholeRoundFailure = parsed.length === 1 && parsed[0].type === 'notice'

  if (parsed.length === 0) {
    return (
      <div className={[ANSWER_FLOW, className].filter(Boolean).join(' ')}>
        <EmptyAnswerPanel onRetry={onRetry} />
      </div>
    )
  }

  const go = onNavigate ?? ((target: string) => routerNavigate(target))

  return (
    <div className={[ANSWER_FLOW, className].filter(Boolean).join(' ')}>
      {parsed.map((block, index) => (
        <BlockView
          key={block.type !== 'invalid' && block.id ? block.id : `${block.type}-${index}`}
          block={block}
          wholeRoundFailure={wholeRoundFailure}
          onNavigate={go}
          onRetry={onRetry}
          onOpenStatus={onOpenStatus}
        />
      ))}
    </div>
  )
}

function BlockView({
  block,
  wholeRoundFailure,
  onNavigate,
  onRetry,
  onOpenStatus,
}: {
  block: RenderedBlock
  wholeRoundFailure: boolean
  onNavigate: (target: string) => void
  onRetry?: () => void
  onOpenStatus?: () => void
}) {
  switch (block.type) {
    case 'conclusion':
      return <ConclusionBlock payload={block.payload} />
    case 'metric':
      return <MetricBlock payload={block.payload} />
    case 'chart':
      return <ChartBlock payload={block.payload} />
    case 'table':
      return <TableBlock payload={block.payload} />
    case 'citation':
      return <CitationBlock payload={block.payload} />
    case 'notice':
      return (
        <NoticeBlock
          payload={block.payload}
          wholeRoundFailure={wholeRoundFailure}
          onRetry={onRetry}
          onOpenStatus={onOpenStatus}
        />
      )
    case 'action': {
      const items = block.payload.items.filter((item) => isKnownTarget(item.target))
      const dropped = block.payload.items.filter((item) => !isKnownTarget(item.target))
      return (
        <div className="flex flex-col gap-2">
          {items.length > 0 && <ActionBlock payload={{ items }} onNavigate={onNavigate} />}
          {dropped.length > 0 && (
            <UnreachableActionPanel
              labels={dropped.map((item) => item.label)}
              targets={dropped.map((item) => item.target)}
            />
          )}
        </div>
      )
    }
    case 'invalid':
      return <InvalidBlockPanel reason={block.reason} />
  }
}
