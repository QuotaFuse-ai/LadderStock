import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, ChevronUp } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * C-09 DataTable（DESIGN-SPEC §5 / §4.4）。
 *
 * 表格是 14 张新屏最主要的载体，所以列宽、数字基线、sticky、四态都在组件里定死，
 * 屏幕侧只提供数据与文案。
 *
 * **列宽策略**
 * - 短列（代码 / 时间 / 涨跌 / 操作）给 `width`，列宽固定不随内容抖。
 * - 长文本列（名称 / 摘要 / 理由）给 `truncate: true` 且**不给** `width`：该列吃掉
 *   剩余宽度，超出部分省略号，`title` 自动取纯文本内容；富文本单元格用 `cellTitle`
 *   显式给悬浮文案。两者同时给时以 `width` 为准。
 *
 * **数字列基线**：`numeric: true` = 右对齐 + 单元格套 `.ldst-num`（等宽数字，
 * §7.2「所有数值用 .ldst-num」/ §9「数字列右对齐」）。同时声明 `align` 时对齐以
 * `align` 为准，`.ldst-num` 仍生效。
 *
 * **sticky**：表头 `sticky top-0`；`stickyFirstColumn` 时首列 `sticky left-0`。层级
 * 见 tokens：普通单元格 0 < sticky 首列 1 < 表头 10 < 首列×表头交叉格 11。
 * 表体用 `border-separate`——`border-collapse` 下边框属于表格网格，sticky 表头滚动后
 * 下边框会留在原地。纵向吸附要求本容器自己是滚动源，也就是必须给 `maxHeight`；
 * 不给时表头随页面滚，只有横向的首列吸附。
 *
 * **四态**：`loading` / `error` / 空 都在表体内呈现（区域不消失、列宽不跳），
 * 语义与非表格区域同一套 `StatePanel`（见 `Feedback.tsx`）。
 */

export interface Column<T> {
  key: string
  header: ReactNode
  render: (row: T) => ReactNode
  align?: 'left' | 'right' | 'center'
  /** 数字列：右对齐 + `.ldst-num` 等宽基线 */
  numeric?: boolean
  width?: string
  /** 超出列宽时省略号 + `title`；不给 `width` 即吃掉剩余宽度 */
  truncate?: boolean
  /** 富文本单元格的悬浮文案；纯文本 / 数字单元格自动取内容，不必给 */
  cellTitle?: (row: T) => string
  /** 合计行等：该列在 `<tfoot>` 里的单元格，任一列声明即渲染 tfoot */
  summary?: (rows: T[]) => ReactNode
  /** 提供比较函数即表示该列可排序 */
  sort?: (a: T, b: T) => number
  className?: string
}

export interface Expandable<T> {
  /** 展开区内容，整行 `colSpan`，不参与列对齐 */
  render: (row: T) => ReactNode
  /** 展开按钮的可访问名（动词开头，§8） */
  label?: string
  defaultExpanded?: string[]
}

export interface DataTableProps<T> {
  columns: Array<Column<T>>
  rows: T[]
  rowKey: (row: T) => string
  caption?: ReactNode
  loading?: boolean
  error?: ReactNode
  empty?: ReactNode
  density?: 'compact' | 'default' | 'comfortable'
  /** 表体最大高度（滚动时表头固定） */
  maxHeight?: string
  stickyFirstColumn?: boolean
  onRowClick?: (row: T) => void
  rowClassName?: (row: T) => string | undefined
  initialSort?: { key: string; dir: 'asc' | 'desc' }
  /** `cycle`（默认）升序→降序→取消；`flip` 只在升序/降序间切换 */
  sortToggle?: 'cycle' | 'flip'
  /** 给出即多一列展开控制；展开按钮不触发行点击，两者互不干扰 */
  expandable?: Expandable<T>
  /**
   * 整行表尾（跨列）。与列级 `summary` 二选一：列级声明存在时以列级为准。
   * 用于「命中 N / M」这类不适合拆到某一列的结论行。
   */
  summaryRow?: (rows: T[]) => ReactNode
}

const ROW_HEIGHT = {
  compact: 'h-row-compact',
  default: 'h-row',
  comfortable: 'h-row-comfortable',
} as const

/** 展开列固定 `--ldst-expander-col-w`；内容内距压到 4px，保证列宽不被内容撑开 */
const EXPANDER_PAD = 'px-1'

function alignOf<T>(column: Column<T>): 'left' | 'right' | 'center' {
  if (column.align) return column.align
  return column.numeric ? 'right' : 'left'
}

function alignClass(align: 'left' | 'right' | 'center'): string {
  if (align === 'right') return 'text-right'
  if (align === 'center') return 'text-center'
  return 'text-left'
}

/** 自动布局下「吃剩余宽度并截断」的写法：宽度要 100%，上限要 0 */
function truncateCellClass<T>(column: Column<T>): string {
  return column.truncate && !column.width ? 'w-full max-w-0' : ''
}

function titleOf<T>(column: Column<T>, row: T, content: ReactNode): string | undefined {
  if (!column.truncate) return undefined
  const explicit = column.cellTitle?.(row)
  if (explicit !== undefined) return explicit
  return typeof content === 'string' || typeof content === 'number' ? String(content) : undefined
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  loading = false,
  error,
  empty,
  density = 'default',
  maxHeight,
  stickyFirstColumn = true,
  onRowClick,
  rowClassName,
  initialSort,
  sortToggle = 'cycle',
  expandable,
  summaryRow,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(initialSort ?? null)
  const [expanded, setExpanded] = useState<string[]>(expandable?.defaultExpanded ?? [])

  const sorted = useMemo(() => {
    if (!sort) return rows
    const column = columns.find((item) => item.key === sort.key)
    if (!column?.sort) return rows
    const factor = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => column.sort!(a, b) * factor)
  }, [rows, sort, columns])

  const toggleSort = (key: string) => {
    setSort((current) => {
      if (current?.key !== key) return { key, dir: 'asc' }
      if (sortToggle === 'flip') return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
      return current.dir === 'asc' ? { key, dir: 'desc' } : null
    })
  }

  const toggleExpand = (key: string) => {
    setExpanded((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]))
  }

  const cellPad = density === 'compact' ? 'px-2' : 'px-3'
  const rowHeight = ROW_HEIGHT[density]
  const hasSummary = columns.some((column) => column.summary)
  const hasSummaryRow = Boolean(summaryRow) && !hasSummary
  const showFooter = (hasSummary || hasSummaryRow) && !loading && !error && rows.length > 0
  /** 展开列也是列：四态行的 `colSpan` 要把它算进去 */
  const columnCount = columns.length + (expandable ? 1 : 0)
  /** 数据首列的 `left` 偏移：有展开列时让出它的宽度 */
  const firstColumnLeft = expandable ? 'left-expander-col' : 'left-0'

  /** 表体 sticky 首列（含展开列）：`bg-inherit` 让悬停/选中底色跟着行走且保持不透明 */
  const bodySticky = (index: number): string => {
    if (!stickyFirstColumn) return ''
    if (expandable && index === -1) return 'sticky left-0 z-sticky-col bg-inherit'
    if (index === 0) return `sticky ${firstColumnLeft} z-sticky-col bg-inherit`
    return ''
  }
  /** 表头：交叉格必须高于表头本身，否则横向滚动时表头盖住首列表头 */
  const headSticky = (index: number): string => {
    if (!stickyFirstColumn) return 'z-sticky'
    if (expandable && index === -1) return 'left-0 z-sticky-col-head'
    if (index === 0) return `left-${expandable ? 'expander-col' : '0'} z-sticky-col-head`
    return 'z-sticky'
  }

  const expanderHead = expandable && (
    <th
      scope="col"
      style={{ width: 'var(--ldst-expander-col-w)' }}
      className={cn(
        EXPANDER_PAD,
        'sticky top-0 border-b border-border bg-surface-2 text-caption font-medium text-text-secondary',
        headSticky(-1),
      )}
    >
      <span className="sr-only">展开</span>
    </th>
  )

  let body: ReactNode
  if (error) {
    body = (
      <tr>
        <td colSpan={columnCount} className="px-3 py-6">
          {error}
        </td>
      </tr>
    )
  } else if (loading) {
    body = (
      <>
        {Array.from({ length: 5 }).map((_, index) => (
          <tr key={`skeleton-${index}`} className={cn(rowHeight, 'bg-surface')}>
            {expandable && (
              <td className={cn(EXPANDER_PAD, 'border-b border-border', bodySticky(-1))}>
                <span className="ldst-skeleton block h-3 w-3" />
              </td>
            )}
            {columns.map((column, columnIndex) => (
              <td
                key={column.key}
                className={cn(
                  cellPad,
                  'border-b border-border',
                  truncateCellClass(column),
                  bodySticky(columnIndex),
                  index === 4 && 'border-b-0',
                )}
              >
                <span
                  className="ldst-skeleton block h-3"
                  style={{ width: columnIndex === 0 ? '40%' : `${55 + ((index * 7 + columnIndex * 11) % 35)}%` }}
                />
              </td>
            ))}
          </tr>
        ))}
      </>
    )
  } else if (rows.length === 0) {
    body = (
      <tr>
        <td colSpan={columnCount} className="px-3">
          <div className="flex min-h-empty items-center justify-center py-6">{empty}</div>
        </td>
      </tr>
    )
  } else {
    body = sorted.map((row, rowIndex) => {
      const key = rowKey(row)
      const isOpen = expanded.includes(key)
      const isLast = rowIndex === sorted.length - 1
      /** `border-separate` 下 tr 的边框不渲染，分隔线一律画在单元格上 */
      const seam = isOpen || isLast ? '' : 'border-b border-border'

      return (
        <Fragment key={key}>
          <tr
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            className={cn(
              rowHeight,
              'transition-colors duration-fast ease-ldst',
              isOpen ? 'bg-surface-2' : 'bg-surface hover:bg-surface-3',
              onRowClick && 'cursor-pointer',
              rowClassName?.(row),
            )}
          >
            {expandable && (
              <td className={cn(EXPANDER_PAD, seam, bodySticky(-1))}>
                <button
                  type="button"
                  aria-expanded={isOpen}
                  aria-label={expandable.label ?? (isOpen ? '收起详情' : '展开详情')}
                  onClick={(event) => {
                    event.stopPropagation()
                    toggleExpand(key)
                  }}
                  className={cn(
                    'inline-flex items-center justify-center rounded-xs text-text-muted',
                    'transition-transform duration-fast ease-ldst hover:text-text',
                    isOpen && 'rotate-90',
                  )}
                >
                  <ChevronRight className="size-4" aria-hidden="true" />
                </button>
              </td>
            )}
            {columns.map((column, columnIndex) => {
              const content = column.render(row)
              return (
                <td
                  key={column.key}
                  title={titleOf(column, row, content)}
                  className={cn(
                    cellPad,
                    'align-middle text-body text-text',
                    seam,
                    alignClass(alignOf(column)),
                    column.numeric && 'ldst-num',
                    truncateCellClass(column),
                    bodySticky(columnIndex),
                    column.className,
                  )}
                >
                  {column.truncate ? <span className="block truncate">{content}</span> : content}
                </td>
              )
            })}
          </tr>
          {expandable && isOpen && (
            <tr className="bg-sunken">
              <td colSpan={columnCount} className={cn('px-4 py-3', !isLast && 'border-b border-border')}>
                {expandable.render(row)}
              </td>
            </tr>
          )}
        </Fragment>
      )
    })
  }

  return (
    <div
      className={cn('w-full overflow-auto rounded border border-border bg-surface')}
      style={{ maxHeight }}
      aria-busy={loading || undefined}
    >
      {/* border-separate：边框随 sticky 单元格一起吸附，表头下沿不会在滚动后消失 */}
      <table className="w-full min-w-full border-separate border-spacing-0 text-body">
        {caption && <caption className="px-3 py-2 text-left text-caption text-text-secondary">{caption}</caption>}
        <thead>
          <tr className={cn('h-control', 'bg-surface-2')}>
            {expanderHead}
            {columns.map((column, columnIndex) => {
              const active = sort?.key === column.key
              return (
                <th
                  key={column.key}
                  scope="col"
                  style={column.width ? { width: column.width } : undefined}
                  aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  className={cn(
                    cellPad,
                    'sticky top-0 whitespace-nowrap border-b border-border bg-surface-2 text-caption font-medium text-text-secondary',
                    alignClass(alignOf(column)),
                    headSticky(columnIndex),
                  )}
                >
                  {column.sort ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(column.key)}
                      className={cn(
                        'inline-flex items-center gap-1 rounded-xs transition-colors duration-fast ease-ldst hover:text-text',
                        alignOf(column) === 'right' && 'flex-row-reverse',
                      )}
                    >
                      {column.header}
                      {active ? (
                        sort!.dir === 'asc' ? (
                          <ChevronUp className="size-3" aria-hidden="true" />
                        ) : (
                          <ChevronDown className="size-3" aria-hidden="true" />
                        )
                      ) : (
                        <ChevronDown className="size-3 opacity-30" aria-hidden="true" />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>{body}</tbody>
        {showFooter && (
          <tfoot>
            {hasSummaryRow ? (
              <tr className="bg-surface-2 font-medium">
                <td
                  colSpan={columnCount}
                  className={cn(cellPad, 'border-t border-border-strong py-2 text-body text-text')}
                >
                  {summaryRow!(sorted)}
                </td>
              </tr>
            ) : (
              <tr className={cn(rowHeight, 'bg-surface-2 font-medium')}>
                {expandable && <td className={cn(EXPANDER_PAD, 'border-t border-border-strong', bodySticky(-1))} />}
                {columns.map((column, columnIndex) => (
                  <td
                    key={column.key}
                    className={cn(
                      cellPad,
                      'border-t border-border-strong align-middle text-body text-text',
                      alignClass(alignOf(column)),
                      column.numeric && 'ldst-num',
                      bodySticky(columnIndex),
                    )}
                  >
                    {column.summary ? column.summary(sorted) : null}
                  </td>
                ))}
              </tr>
            )}
          </tfoot>
        )}
      </table>
    </div>
  )
}
