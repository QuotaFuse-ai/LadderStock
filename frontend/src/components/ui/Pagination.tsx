import { ChevronLeft, ChevronRight } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * C-23 Pagination（QUOT-530）：页码按钮组 + 每页条数显示。
 *
 * 分页条本身只负责「第几页 / 共几页 / 共几条 / 每页几条」的呈现与点击，
 * 不持有页码状态：页码由页面落进 URL（`?page=`），刷新与分享都能复原。
 *
 * 状态不靠色（§9）：当前页除填色外还有 `aria-current="page"` 与加粗字重；
 * 上一页 / 下一页用「图标 + 文字」同现，到边界时按钮 `disabled`，读屏与视觉同时可辨。
 */

const BASE =
  'inline-flex h-control min-w-icon items-center justify-center gap-1 rounded border px-2 text-caption font-medium transition-colors duration-fast ease-ldst disabled:cursor-not-allowed disabled:opacity-50'

const TONES = {
  idle: 'border-border-strong bg-surface text-text-secondary hover:bg-surface-3 hover:text-text',
  current: 'border-accent-solid bg-accent-solid text-accent-contrast font-semibold',
}

/** 页数 ≤ 7 全量铺开；更多时只留首尾 + 当前页两侧两格，中间用省略号收口。 */
const PLAIN_MAX = 7

type PageSlot = number | 'gap-start' | 'gap-end'

function pageSlots(page: number, pageCount: number): PageSlot[] {
  if (pageCount <= PLAIN_MAX) {
    return Array.from({ length: pageCount }, (_, index) => index + 1)
  }
  let start = Math.max(2, page - 2)
  let end = Math.min(pageCount - 1, page + 2)
  if (page <= 3) {
    start = 2
    end = 5
  }
  if (page >= pageCount - 2) {
    start = pageCount - 4
    end = pageCount - 1
  }
  const slots: PageSlot[] = [1]
  if (start > 2) slots.push('gap-start')
  for (let n = start; n <= end; n += 1) slots.push(n)
  if (end < pageCount - 1) slots.push('gap-end')
  slots.push(pageCount)
  return slots
}

export interface PaginationProps {
  /** 当前页，从 1 开始；越界时按 `[1, pageCount]` 收敛后再渲染 */
  page: number
  /** 总页数，至少按 1 处理 */
  pageCount: number
  onPageChange: (page: number) => void
  /** 每页条数；与 `total` 同时给出时在摘要里显示 */
  pageSize?: number
  /** 总条数（过滤后的条数） */
  total?: number
  /** 导航名，同屏多个分页时用于区分 */
  label?: string
  className?: string
}

export function Pagination({
  page,
  pageCount,
  onPageChange,
  pageSize,
  total,
  label = '分页',
  className,
}: PaginationProps) {
  const lastPage = Math.max(1, pageCount)
  const current = Math.min(Math.max(1, page), lastPage)

  const go = (target: number) => {
    if (target < 1 || target > lastPage || target === current) return
    onPageChange(target)
  }

  return (
    <nav
      aria-label={label}
      className={cn('flex flex-wrap items-center justify-between gap-2', className)}
    >
      <p className="ldst-num text-caption text-text-secondary" aria-live="polite">
        第 <span className="font-medium text-text">{current}</span> / {lastPage} 页
        {typeof total === 'number' && pageSize ? (
          <>
            {' · '}共 {total} 条 · 每页 {pageSize} 条
          </>
        ) : null}
      </p>

      <div className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          onClick={() => go(current - 1)}
          disabled={current === 1}
          className={cn(BASE, TONES.idle)}
        >
          <ChevronLeft className="size-4 shrink-0" aria-hidden="true" />
          <span>上一页</span>
        </button>

        {pageSlots(current, lastPage).map((slot) =>
          typeof slot === 'number' ? (
            <button
              key={slot}
              type="button"
              aria-label={`第 ${slot} 页`}
              aria-current={slot === current ? 'page' : undefined}
              onClick={() => go(slot)}
              className={cn(BASE, slot === current ? TONES.current : TONES.idle)}
            >
              {slot}
            </button>
          ) : (
            <span
              key={slot}
              aria-hidden="true"
              className="inline-flex h-control min-w-icon items-center justify-center text-caption text-text-muted"
            >
              …
            </span>
          ),
        )}

        <button
          type="button"
          onClick={() => go(current + 1)}
          disabled={current === lastPage}
          className={cn(BASE, TONES.idle)}
        >
          <span>下一页</span>
          <ChevronRight className="size-4 shrink-0" aria-hidden="true" />
        </button>
      </div>
    </nav>
  )
}
