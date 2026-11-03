import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { isEndpointMissing } from '../../api'
import type { PositionItem, PositionsPayload } from '../../api/radar'
import {
  DataTable,
  EmptyState,
  ErrorBlock,
  PrivacyNotice,
  SectionCard,
  Skeleton,
  SkeletonRows,
  SourceBadges,
  type Column,
  type SourceBadgeMeta,
} from '../../components/ui'
import {
  countOrMissing,
  formatChangePct,
  formatMoney,
  isFiniteNumber,
  MISSING,
} from '../../lib/format'

interface MoneyRow {
  key: string
  label: string
  marketValue: number | null
  pnl: number | null
  roi: number | null
  dayPnl: number | null
}

function signedMoney(value: number | null | undefined): { text: string; tone: 'up' | 'down' | 'flat' } {
  if (!isFiniteNumber(value)) return { text: MISSING, tone: 'flat' }
  return {
    text: `${value > 0 ? '+' : ''}${formatMoney(value)}`,
    tone: value > 0 ? 'up' : value < 0 ? 'down' : 'flat',
  }
}

function metaOf(payload: PositionsPayload | null): SourceBadgeMeta | null {
  if (!payload) return null
  return {
    freshness: payload.as_of ? 'cached' : null,
    source_status: payload.data_source_status,
    source_detail: payload.price_source,
    data_as_of: payload.as_of,
  }
}

const SUMMARY_COLUMNS: Array<Column<MoneyRow>> = [
  { key: 'currency', header: '币种', width: '88px', render: (row) => <span className="text-text">{row.label}</span> },
  {
    key: 'market_value',
    header: '市值',
    numeric: true,
    className: 'whitespace-nowrap',
    render: (row) => <span className="text-text">{formatMoney(row.marketValue)}</span>,
  },
  {
    key: 'pnl',
    header: '持仓盈亏',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    render: (row) => {
      const value = signedMoney(row.pnl)
      return <span className={value.tone === 'up' ? 'text-up' : value.tone === 'down' ? 'text-down' : 'text-text-secondary'}>{value.text}</span>
    },
  },
  {
    key: 'roi',
    header: '收益率',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    render: (row) => {
      const value = formatChangePct(row.roi)
      return <span className={value.tone === 'up' ? 'text-up' : value.tone === 'down' ? 'text-down' : 'text-text-secondary'}>{value.text}</span>
    },
  },
  {
    key: 'day_pnl',
    header: '当日盈亏',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    render: (row) => {
      const value = signedMoney(row.dayPnl)
      return <span className={value.tone === 'up' ? 'text-up' : value.tone === 'down' ? 'text-down' : 'text-text-secondary'}>{value.text}</span>
    },
  },
]

const DETAIL_COLUMNS: Array<Column<PositionItem>> = [
  {
    key: 'name',
    header: '名称',
    truncate: true,
    render: (row) =>
      row.name ? <span className="font-medium">{row.name}</span> : <span className="text-text-muted">名称未接入</span>,
  },
  {
    key: 'code_qty',
    header: '代码 · 股数',
    width: '176px',
    className: 'whitespace-nowrap',
    render: (row) => (
      <span className="ldst-num text-text-secondary">
        {row.code} · {row.qty} 股
      </span>
    ),
  },
  {
    key: 'price',
    header: '现价',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    sort: (a, b) => (a.latest_price ?? -Infinity) - (b.latest_price ?? -Infinity),
    render: (row) => <span className="text-text">{formatMoney(row.latest_price)}</span>,
  },
  {
    key: 'market_value',
    header: '市值',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    sort: (a, b) => (a.market_value ?? -Infinity) - (b.market_value ?? -Infinity),
    render: (row) => <span className="text-text">{formatMoney(row.market_value)}</span>,
  },
  {
    key: 'pnl',
    header: '持仓盈亏',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    sort: (a, b) => (a.unrealized_pnl ?? -Infinity) - (b.unrealized_pnl ?? -Infinity),
    render: (row) => {
      const value = signedMoney(row.unrealized_pnl)
      return <span className={value.tone === 'up' ? 'text-up' : value.tone === 'down' ? 'text-down' : 'text-text-secondary'}>{value.text}</span>
    },
  },
  {
    key: 'day_pnl',
    header: '当日盈亏',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    sort: (a, b) => (a.day_pnl ?? -Infinity) - (b.day_pnl ?? -Infinity),
    render: (row) => {
      const value = signedMoney(row.day_pnl)
      return <span className={value.tone === 'up' ? 'text-up' : value.tone === 'down' ? 'text-down' : 'text-text-secondary'}>{value.text}</span>
    },
  },
]

export interface HomePositionsCardProps {
  payload: PositionsPayload | null
  status: 'loading' | 'ready' | 'error'
  error: unknown
  offline: boolean
  onRetry: () => void
  density: 'compact' | 'default' | 'comfortable'
}

/**
 * 块 D：我的持仓（SPEC-HOME-V2 §5）。
 *
 * 隐私说明常驻（失败 / 空态也在）；币种汇总在载荷没有 `currency` 时退化为单行「合计」；
 * `day_pnl` 缺失时列保留、值 `—`。名称缺失显示「名称未接入」，不拿代码或行业顶替。
 */
export function HomePositionsCard({
  payload,
  status,
  error,
  offline,
  onRetry,
  density,
}: HomePositionsCardProps) {
  const count = countOrMissing(payload?.position_count)
  const items = payload?.items ?? []
  const summaryRows: MoneyRow[] = payload
    ? [
        {
          key: 'total',
          label: '合计',
          marketValue: payload.valuation.market_value,
          pnl: payload.valuation.unrealized_pnl,
          roi: payload.valuation.unrealized_pnl_pct,
          dayPnl: payload.valuation.day_pnl ?? null,
        },
      ]
    : []

  let body: ReactNode
  if (status === 'loading') {
    body = (
      <div className="flex flex-col gap-4">
        <div className="rounded border border-border p-3">
          <Skeleton className="h-5 w-1/3" />
        </div>
        <SkeletonRows rows={3} />
      </div>
    )
  } else if (isEndpointMissing(error)) {
    body = (
      <EmptyState
        compact
        title="持仓接口未接入"
        description="GET /api/positions 由 QUOT-377 S2 交付；本机隐私库已有 positions 表，缺的是读接口。"
      />
    )
  } else if (status === 'error') {
    body = (
      <ErrorBlock
        variant="banner"
        title="持仓取数失败"
        detail={error instanceof Error ? error.message : '本机持仓接口返回异常，稍后重试。'}
        onRetry={onRetry}
      />
    )
  } else if (items.length === 0) {
    body = (
      <EmptyState
        compact
        title="本机还没有持仓"
        description={
          offline
            ? '本地服务未连接，先运行 ldst run 再回来。'
            : '导入或新增都在「我的持仓」页，首页不放新增表单。'
        }
        action={
          offline ? undefined : (
            <Link
              to="/positions"
              className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text transition-colors duration-fast ease-ldst hover:bg-surface-3"
            >
              去我的持仓 →
            </Link>
          )
        }
      />
    )
  } else {
    body = (
      <div className="flex flex-col gap-4">
        <div>
          <DataTable<MoneyRow>
            columns={SUMMARY_COLUMNS}
            rows={summaryRows}
            rowKey={(row) => row.key}
            caption="币种汇总"
            density={density}
            stickyFirstColumn={false}
          />
          <p className="mt-2 text-caption text-text-muted">
            未按币种拆分：持仓表没有 currency 列，首页不猜币种。
            {payload && payload.valuation.unpriced_cost > 0 && (
              <>
                {' '}
                未定价成本 <span className="ldst-num">{formatMoney(payload.valuation.unpriced_cost)}</span>，市值不含它们。
              </>
            )}
          </p>
        </div>

        <DataTable<PositionItem>
          columns={DETAIL_COLUMNS}
          rows={items}
          rowKey={(row) => row.code}
          caption="逐条持仓"
          density={density}
          maxHeight="240px"
        />
      </div>
    )
  }

  return (
    <SectionCard
      title={`我的持仓 · (${count})`}
      subtitle={`成本法 · ${payload?.as_of ?? MISSING}`}
      actions={
        <Link to="/positions" className="text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:text-text">
          看明细 →
        </Link>
      }
      bodyClassName="p-0"
    >
      <div className="flex flex-col gap-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <PrivacyNotice className="flex-1" />
          <SourceBadges envelope={metaOf(payload)} />
        </div>
        {body}
      </div>
      <div className="border-t border-border px-4 py-2 text-caption leading-body text-text-muted">
        <p>成本法：移动加权平均（DEC-038）；不含分红，已实现盈亏在交易日志页。</p>
        <p>当日盈亏 =（现价 − 昨收）× 股数；当日有买卖 / 除权或昨收缺失时整列显示「—」，不推算。</p>
        <p>表头排序只影响本次浏览；持仓只走 127.0.0.1，不上传、不进 AI、不被 MCP 读取。</p>
      </div>
    </SectionCard>
  )
}