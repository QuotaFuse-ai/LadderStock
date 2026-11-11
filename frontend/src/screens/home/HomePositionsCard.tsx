import { RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { type PositionItem, type PositionsPayload } from '../../api/radar'
import {
  Badge,
  Button,
  CardRefreshButton,
  DataTable,
  EmptyState,
  ErrorBlock,
  PrivacyBadge,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
  type Column,
} from '../../components/ui'
import {
  MISSING,
  countOrMissing,
  formatAmount,
  formatInboxTime,
  formatMoney,
  formatSignedAmount,
} from '../../lib/format'
import { cn } from '../../lib/cn'
import { useDensity } from '../../lib/prefs'
import type { BlockView } from './useHomeSource'

/**
 * 首页「我的持仓」紧凑摘要（QUOT-552 / SPEC-HOME-V2 v2.0 §5）。
 *
 * 首页只压 **3 列**：名称 / 市值 / 持仓盈亏 + 合计行（C-09 列级 `summary`，不写第二份表体）；
 * **全 6 列双表（币种汇总 + 逐条）原样保留在 `/positions`**，不删列不丢信息——
 * 收益率 / 当日盈亏 / 代码·股数 / 现价都在那边，「完整持仓 →」直达。
 *
 * 两颗口径徽标常驻卡头（不随数据状态消失）：
 * - 「毛额·未扣费」：持仓盈亏是毛额（`fee_included=false`，trades 无费用列），不是净盈亏；
 * - 「仅本机」：隐私长文案收进徽标，hover 出全文、点击展开 `PrivacyNotice` 本体（§5.4）。
 *
 * 缺数口径（§5.2）不变：名称缺 → 「名称未接入」，不拿 code 顶替（代码在悬浮文案与全表里）；
 * 市值 / 盈亏缺 → `—`，不补 0。
 */

/** 市值等金额列：`null` 一律 `—`；有值走 `formatAmount` 原样读数（§8），不折算万 / 亿。 */
function formatIntOrMissing(value: number | null | undefined): string {
  if (value === null || value === undefined) return MISSING
  return formatAmount(value)
}

const GROSS_TITLE =
  '持仓盈亏为毛额口径（fee_included=false）：trades 表无费用列，未扣佣金 / 印花税 / 过户费，不是净盈亏。'

export function HomePositionsCard({ view, className }: { view: BlockView<PositionsPayload>; className?: string }) {
  const density = useDensity()
  const navigate = useNavigate()
  const payload = view.state === 'normal' || view.state === 'partial' ? view.data : null
  const items = payload?.items ?? []
  const valuation = payload?.valuation ?? null
  const pricedCount = payload ? payload.position_count : null
  const unpricedCost = valuation?.unpriced_cost ?? 0

  const columns: Array<Column<PositionItem>> = [
    {
      key: 'name',
      header: '名称',
      truncate: true,
      cellTitle: (row) => `${row.code} ${row.name ?? '名称未接入'}`,
      render: (row) => (
        <span className={row.name ? 'font-medium' : 'text-text-muted'}>{row.name ?? '名称未接入'}</span>
      ),
      summary: () => <span className="font-medium">合计</span>,
    },
    {
      key: 'value',
      header: '市值',
      width: '112px',
      numeric: true,
      sort: (a, b) => (a.market_value ?? Number.NEGATIVE_INFINITY) - (b.market_value ?? Number.NEGATIVE_INFINITY),
      render: (row) => (
        <span className={row.market_value === null ? 'text-text-muted' : undefined}>
          {formatIntOrMissing(row.market_value)}
        </span>
      ),
      summary: () => formatIntOrMissing(valuation?.market_value),
    },
    {
      key: 'pnl',
      header: '持仓盈亏',
      width: '112px',
      numeric: true,
      sort: (a, b) => (a.unrealized_pnl ?? Number.NEGATIVE_INFINITY) - (b.unrealized_pnl ?? Number.NEGATIVE_INFINITY),
      render: (row) => {
        const pnl = formatSignedAmount(row.unrealized_pnl)
        return <SignedNumber text={pnl.text} tone={pnl.tone} arrow="" />
      },
      summary: () => {
        const pnl = formatSignedAmount(valuation?.unrealized_pnl ?? null)
        return <SignedNumber text={pnl.text} tone={pnl.tone} arrow="" />
      },
    },
  ]

  const grossBadge = (
    <Badge tone="flat" title={GROSS_TITLE}>
      毛额·未扣费
    </Badge>
  )
  const privacyBadge = <PrivacyBadge subject="持仓" storeCommand="ldst position" />

  return (
    <SectionCard
      title={payload ? `我的持仓 · (${countOrMissing(pricedCount)})` : '我的持仓'}
      tone={payload && payload.data_source_status !== 'ok' ? 'warn' : 'neutral'}
      className={cn('flex flex-col', className)}
      subtitle={
        <span className="flex flex-wrap items-center gap-1">
          {grossBadge}
          {payload && (
            <SourceStatusBadge
              status={payload.data_source_status}
              detail={payload.price_lookup?.reason ?? null}
            />
          )}
        </span>
      }
      actions={
        <>
          {/* 隐私徽标常驻卡头操作区（§5.4），不随数据状态消失 */}
          {privacyBadge}
          <CardRefreshButton
            label="刷新我的持仓"
            onClick={view.onRefresh}
            loading={view.refreshing}
          />
        </>
      }
      bodyClassName="flex grow flex-col p-0"
    >
      {view.state === 'loading' && (
        <div className="flex flex-col gap-2 p-4" aria-hidden="true">
          <div className="h-row rounded bg-surface-2" />
          <div className="h-row rounded bg-surface-2" />
          <div className="h-row rounded bg-surface-2" />
        </div>
      )}

      {view.state === 'error' && (
        <div className="p-4">
          <ErrorBlock
            variant="banner"
            title="持仓取数失败"
            detail={view.error instanceof Error ? view.error.message : String(view.error ?? '')}
            onRetry={view.onRetry}
            retryLabel="重试"
          />
        </div>
      )}

      {view.state === 'offline' && (
        <div className="p-4">
          <EmptyState compact title="本机服务未连接" description="持仓只经 127.0.0.1 回环读取，断连时不显示任何金额。" />
        </div>
      )}

      {(view.state === 'normal' || view.state === 'partial') && (
        <DataTable<PositionItem>
          columns={columns}
          rows={items}
          rowKey={(row) => row.code}
          caption={`持仓摘要（${countOrMissing(pricedCount)} 只），合计行与逐条同源`}
          density={density}
          maxHeight="240px"
          empty={
            <EmptyState
              compact
              title="本机还没有持仓"
              description="在「我的持仓」页点「新增持仓」录入第一条，保存后这里自动出现。"
            />
          }
        />
      )}

      {view.state === 'empty' &&
        (view.endpointMissing ? (
          <div className="p-4">
            <EmptyState
              compact
              title="持仓接口未接入"
              description="页面已按 GET /api/positions 契约实现；启动 ldst run 后自动接入。"
              action={
                <Button variant="secondary" onClick={view.onRetry} leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}>
                  重新检查
                </Button>
              }
            />
          </div>
        ) : (
          <div className="p-4">
            <EmptyState
              compact
              title="本机还没有持仓"
              description="在「我的持仓」页点「新增持仓」录入第一条，保存后这里自动出现。"
              action={
                <Button variant="secondary" onClick={() => navigate('/positions')}>
                  去我的持仓新增
                </Button>
              }
            />
          </div>
        ))}

      {/* 一行页脚：口径短句 + 全表入口（322px 宽卡放不下长文案，全文在 title） */}
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-border px-4 py-1">
        <p
          className="min-w-0 truncate text-11px leading-body text-text-muted"
          title={
            '成本法：移动加权平均（DEC-038），不含分红；盈亏为毛额·未扣费。' +
            (payload?.as_of ? `估值时间 ${payload.as_of}。` : '') +
            (unpricedCost > 0 ? `其中 ${formatMoney(unpricedCost)} 成本未定价，市值不含它们。` : '') +
            '当日盈亏与完整六列见「我的持仓」页。'
          }
        >
          移动加权平均 · 不含分红
          {payload?.as_of ? ` · ${formatInboxTime(payload.as_of)}` : ' · 毛额'}
          {unpricedCost > 0 && ` · 未定价成本 ${formatMoney(unpricedCost)}`}
        </p>
        <button
          type="button"
          onClick={() => navigate('/positions')}
          className="inline-flex h-5 shrink-0 items-center rounded px-2 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:bg-surface-3 hover:text-text"
        >
          完整持仓 →
        </button>
      </div>
    </SectionCard>
  )
}