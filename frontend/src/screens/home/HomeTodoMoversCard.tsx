import { Link } from 'react-router-dom'

import type { LadderEnvelope, ValidationPayload, WatchSnapshotPayload, WatchSnapshotRow } from '../../api'
import {
  Badge,
  CardRefreshButton,
  DataTable,
  EmptyState,
  SectionCard,
  SignedNumber,
  type BadgeTone,
  type Column,
} from '../../components/ui'
import { MISSING, formatChangePct } from '../../lib/format'
import { cn } from '../../lib/cn'
import { useDensity } from '../../lib/prefs'
import { researchHref } from '../../lib/searchRoutes'
import type { BlockView } from './useHomeSource'

/**
 * 首页「待办与异动」合并卡（QUOT-552 / SPEC-HOME-V2 v2.0 §6）。
 *
 * 原 E（待办）/ F（最近异动）两张并排卡合成**一张 C-09 表**：「类型」列区分
 * 待对账 / 待写 / 数据降级 / 异动，不再各占一块版面。口径一条没动：
 * - 待办按处理优先级（对账 → 复盘 → 数据源），最多 3 条，判定仍在 `HomeScreen`；
 * - 异动按快照涨跌幅绝对值取前 3，排序在 `HomeScreen` 完成后传入。
 *
 * 卡头两颗 ⟳ 常驻（QUOT-521 不变量）：「刷新待办」只刷验证条件端点，
 * 「刷新最近异动」只刷盯盘快照端点——合并的是呈现，不是取数口。
 */

export interface HomeTodo {
  tone: BadgeTone
  tag: string
  title: string
  to: string
  action: string
}

interface MergedRow {
  id: string
  tag: string
  tone: BadgeTone
  /** 事项主文案：待办是结论句，异动是名称（缺名称 → 「—」，不拿代码顶替） */
  title: string
  /** 异动行的代码前缀；待办为 null */
  code: string | null
  to: string
  /** 待办的去向动词（去验证 / 去复盘 / 看数据源）；异动为 null */
  action: string | null
  changePct: number | null
}

const COLUMNS: Array<Column<MergedRow>> = [
  {
    key: 'kind',
    header: '类型',
    width: '88px',
    render: (row) => <Badge tone={row.tone}>{row.tag}</Badge>,
  },
  {
    key: 'item',
    header: '事项',
    truncate: true,
    cellTitle: (row) => (row.code ? `${row.code} ${row.title}` : row.title),
    render: (row) => (
      <Link
        to={row.to}
        className="transition-colors duration-fast ease-ldst hover:text-accent focus-visible:text-accent"
      >
        {row.code && <span className="ldst-num mr-1 text-caption text-text-muted">{row.code}</span>}
        {row.title}
      </Link>
    ),
  },
  {
    key: 'value',
    header: '涨跌 / 去向',
    width: '112px',
    // 混合列：异动是 SignedNumber（自带 .ldst-num），待办是去向动词，不能整列套等宽字体
    align: 'right',
    render: (row) => {
      if (row.action) {
        return <span className="text-caption text-text-secondary">{row.action} →</span>
      }
      const change = formatChangePct(row.changePct)
      return <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} />
    },
  },
]

export interface HomeTodoMoversCardProps {
  todos: HomeTodo[]
  movers: WatchSnapshotRow[]
  validation: BlockView<LadderEnvelope<ValidationPayload>>
  watch: BlockView<LadderEnvelope<WatchSnapshotPayload>>
  className?: string
}

export function HomeTodoMoversCard({ todos, movers, validation, watch, className }: HomeTodoMoversCardProps) {
  const density = useDensity()

  const rows: MergedRow[] = [
    ...todos.map((todo) => ({
      id: `todo-${todo.tag}`,
      tag: todo.tag,
      tone: todo.tone,
      title: todo.title,
      code: null,
      to: todo.to,
      action: todo.action,
      changePct: null,
    })),
    ...movers.map((row) => ({
      id: `mover-${row.code}`,
      tag: '异动',
      tone: 'flat' as BadgeTone,
      title: row.name ?? MISSING,
      code: row.code,
      to: researchHref(row.code),
      action: null,
      changePct: row.change_pct,
    })),
  ]

  const loading = rows.length === 0 && (validation.state === 'loading' || watch.state === 'loading')
  const tone = todos.some((todo) => todo.tone === 'danger')
    ? 'danger'
    : todos.length > 0
      ? 'warn'
      : 'neutral'

  return (
    <SectionCard
      title="待办与异动"
      tone={tone}
      className={cn('flex flex-col', className)}
      subtitle={todos.length > 0 ? `${todos.length} 条待办 · 异动涨跌幅前 3` : '今日无待办 · 异动涨跌幅前 3'}
      actions={
        <>
          <CardRefreshButton
            label="刷新待办"
            onClick={validation.onRefresh}
            loading={validation.refreshing}
          />
          <CardRefreshButton
            label="刷新最近异动"
            onClick={watch.onRefresh}
            loading={watch.refreshing}
          />
        </>
      }
      bodyClassName="grow p-0"
    >
      {rows.length === 0 && !loading ? (
        <div className="p-4">
          <EmptyState
            compact
            title="今日无待办"
            description="没有待对账 / 待写 / 数据降级事项；盯盘快照为空或不可用时，去盯盘页取数。"
          />
        </div>
      ) : (
        <DataTable<MergedRow>
          columns={COLUMNS}
          rows={rows}
          rowKey={(row) => row.id}
          caption="待办按处理优先级 · 异动按涨跌幅绝对值取前 3 · 仅供扫读，不构成建议"
          density={density}
          loading={loading}
          maxHeight="240px"
        />
      )}
    </SectionCard>
  )
}