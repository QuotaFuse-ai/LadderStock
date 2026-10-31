import { useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * C-09 Table（DESIGN-SPEC §5 / §4.4）。
 *
 * - 表头 sticky、行高 36/38/40 三档（由密度决定）、首列 sticky。
 * - 排序只对声明了 `sort` 的列开放，`aria-sort` 同步。
 * - loading / empty / error 都在表体内呈现，避免整块区域消失。
 */

export interface Column<T> {
  key: string
  header: ReactNode
  render: (row: T) => ReactNode
  align?: 'left' | 'right' | 'center'
  width?: string
  /** 提供比较函数即表示该列可排序 */
  sort?: (a: T, b: T) => number
  className?: string
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
}

const ROW_HEIGHT = {
  compact: 'h-row-compact',
  default: 'h-row',
  comfortable: 'h-row-comfortable',
} as const

function alignClass(align: 'left' | 'right' | 'center' | undefined): string {
  if (align === 'right') return 'text-right'
  if (align === 'center') return 'text-center'
  return 'text-left'
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
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(initialSort ?? null)

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

  const cellPad = density === 'compact' ? 'px-2' : 'px-3'
  const rowHeight = ROW_HEIGHT[density]
  const headerHeight = 'h-control'

  let body: ReactNode
  if (error) {
    body = (
      <tr>
        <td colSpan={columns.length} className="px-3 py-6">
          {error}
        </td>
      </tr>
    )
  } else if (loading) {
    body = (
      <>
        {Array.from({ length: 5 }).map((_, index) => (
          <tr key={`skeleton-${index}`} className={rowHeight}>
            {columns.map((column, columnIndex) => (
              <td key={column.key} className={cn(cellPad, 'border-b border-border')}>
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
        <td colSpan={columns.length} className="px-3">
          <div className="flex min-h-empty items-center justify-center py-6">{empty}</div>
        </td>
      </tr>
    )
  } else {
    body = sorted.map((row) => (
      <tr
        key={rowKey(row)}
        onClick={onRowClick ? () => onRowClick(row) : undefined}
        className={cn(
          rowHeight,
          'border-b border-border transition-colors duration-fast last:border-b-0 hover:bg-surface-3',
          onRowClick && 'cursor-pointer',
          rowClassName?.(row),
        )}
      >
        {columns.map((column, columnIndex) => (
          <td
            key={column.key}
            className={cn(
              cellPad,
              'align-middle text-body text-text',
              alignClass(column.align),
              stickyFirstColumn && columnIndex === 0 && 'sticky left-0 z-[1] bg-surface',
              column.className,
            )}
          >
            {column.render(row)}
          </td>
        ))}
      </tr>
    ))
  }

  return (
    <div className={cn('w-full overflow-auto rounded border border-border bg-surface')} style={{ maxHeight }}>
      <table className="w-full min-w-full border-collapse text-body">
        {caption && <caption className="px-3 py-2 text-left text-caption text-text-secondary">{caption}</caption>}
        <thead>
          <tr className={cn(headerHeight, 'bg-surface-2')}>
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
                    'sticky top-0 z-sticky whitespace-nowrap border-b border-border bg-surface-2 text-caption font-medium text-text-secondary',
                    alignClass(column.align),
                    stickyFirstColumn && columnIndex === 0 && 'left-0 z-[11]',
                  )}
                >
                  {column.sort ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(column.key)}
                      className={cn(
                        'inline-flex items-center gap-1 rounded-xs hover:text-text',
                        column.align === 'right' && 'flex-row-reverse',
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
      </table>
    </div>
  )
}
