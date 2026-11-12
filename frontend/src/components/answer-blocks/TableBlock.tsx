import { useMemo, useState } from 'react'
import { ChevronDown } from 'lucide-react'

import { TABLE_COLLAPSE_ROWS, type TableBlockPayload } from './types'
import { BlockShell } from './BlockShell'
import { cn } from '../../lib/cn'
import { DataTable, type Column } from '../ui'

/**
 * `table` 块（任务书 §6.2 / §8.2 / §9.2）：`Card` 容器 + 现有表格样式。
 *
 * 表格本身交给 `ui/DataTable`：§8.2 的行高 `row-h` 38px、单元格内距 space-3 12px、
 * 首列 sticky、表头 `surface-2` 本来就是它的口径，全仓也不允许组件里另起裸表格。
 * 本块只负责把载荷摊成列，不动表本身的行为（四态 / 排序 / 展开都不开）。
 *
 * 折叠规则（§8.3）：超过 5 行只给前 5 行，按钮写「还有 N 行」（N 取载荷
 * `rows_total` 与已给行数的差，服务端折叠时它才是真实总数）；展开用
 * duration-fast 120ms。
 */

/** `undefined` / `null` 按空处理：载荷要求单元格原样展示，不补推算值 */
type Row = TableBlockPayload['rows'][number]
function isBlank(value: unknown): boolean {
  return value === null || value === undefined || value === ''
}

export function TableBlock({ payload }: { payload: TableBlockPayload }) {
  const [expanded, setExpanded] = useState(false)

  const given = payload.rows.length
  const foldable = payload.collapsed && given > TABLE_COLLAPSE_ROWS
  const hidden = Math.max(0, payload.rows_total - TABLE_COLLAPSE_ROWS)
  const rows = foldable && !expanded ? payload.rows.slice(0, TABLE_COLLAPSE_ROWS) : payload.rows
  /** 展开后仍有行在服务端没传过来：如实说明，不假装是全部 */
  const stillMissing = expanded && payload.rows_total > given

  /** 数字列判定看整列：载荷里同一列混有字符串时按文本列处理，不把数字对到半截 */
  const numericColumns = useMemo(
    () =>
      payload.columns.map((_, index) => {
        const cells = payload.rows.map((row) => row[index]).filter((cell) => !isBlank(cell))
        return cells.length > 0 && cells.every((cell) => typeof cell === 'number')
      }),
    [payload.columns, payload.rows],
  )

  /** 行本身没有稳定 id：折叠只是同一份数组的切片，按行引用定位下标即可 */
  const rowKeys = useMemo(
    () => new Map<Row, string>(payload.rows.map((row, index) => [row, String(index)])),
    [payload.rows],
  )

  const columns: Array<Column<Row>> = payload.columns.map((header, index) => ({
    key: `${index}-${header}`,
    header,
    numeric: numericColumns[index],
    render: (row) => {
      const cell = row[index]
      if (isBlank(cell)) return '—'
      return typeof cell === 'number' ? cell.toLocaleString('en-US') : String(cell)
    },
    className: index === 0 ? 'font-medium' : undefined,
  }))

  return (
    <BlockShell
      type="table"
      title={payload.caption || '数据表'}
      meta={
        payload.rows_total > rows.length || stillMissing ? (
          <span className="ldst-num">共 {payload.rows_total} 行</span>
        ) : undefined
      }
    >
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => rowKeys.get(row) ?? ''}
        stickyFirstColumn
      />

      {foldable && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          className="mt-2 inline-flex items-center gap-1 rounded-xs text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:text-text"
        >
          <ChevronDown
            aria-hidden="true"
            className={cn('size-3.5 transition-transform duration-fast ease-ldst', expanded && 'rotate-180')}
          />
          {expanded ? '收起表格' : `还有 ${hidden} 行`}
        </button>
      )}
      {stillMissing && (
        <p className="mt-1 text-caption text-text-muted">
          其余 {payload.rows_total - given} 行未随本块下发。
        </p>
      )}
    </BlockShell>
  )
}
