import { RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { quoteSourceLabel, type PositionItem, type PositionsPayload } from '../../api/radar'
import {
  Button,
  DataTable,
  EmptyState,
  ErrorBlock,
  PrivacyNotice,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
  type Column,
} from '../../components/ui'
import {
  MISSING,
  countOrMissing,
  formatAmount,
  formatMoney,
  formatRatePct,
  formatSignedAmount,
  formatTime,
} from '../../lib/format'
import { useDensity } from '../../lib/prefs'
import type { BlockView } from './useHomeSource'

/**
 * 首页 D 块「我的持仓」（SPEC-HOME-V2 §5）。
 *
 * 两张表都是 C-09 的实例（同一 `DataTable`，不写第二份表体）：
 * ① 币种汇总 —— 后端没有 `currency` 分组，退化为单行「合计」，并把原因写在表下（不猜币种、不按代码前缀推）；
 * ② 逐条持仓 —— 名称 / 代码·股数 / 现价 / 市值 / 持仓盈亏 / 当日盈亏，列宽 `剩余/176/112/112/112/112`。
 *
 * 逐条表**只放金额**：参考页在「现价」「持仓盈亏」里塞了第二行百分比，我们的行高刻度是单行 38px，
 * 两行格要么新增行高档要么放弃唯一表体，两条都比「少一个百分比」贵（§5.1）。收益率只在汇总行出现。
 *
 * 缺数口径（§5.2）：名称缺 → 「名称未接入」，不拿 code / industry 顶替；`qty` 缺 → `—`，不写 `0 股`；
 * 市值 / 盈亏 / 当日盈亏缺 → `—` + 脚注说明，不补 0、不拿持仓盈亏的差冒充当日盈亏。
 */

const HOLDING_COLUMNS: Array<Column<PositionItem>> = [
  {
    key: 'name',
    header: '名称',
    // 有 `width` 时以宽度为准（`truncate` 仍负责省略号）：名称列此前吃剩余宽度，
    // 1280px 下把后面四个数字列挤到换行，这里钉成 §7.3 的 128 刻度
    width: '128px',
    truncate: true,
    render: (row) => (
      <span className={row.name ? 'font-medium' : 'text-text-muted'}>{row.name ?? '名称未接入'}</span>
    ),
  },
  {
    key: 'code',
    header: '代码 · 股数',
    width: '168px',
    render: (row) => (
      <span className="ldst-num text-text-secondary">
        {row.code}
        {row.qty === null ? ` · ${MISSING}` : ` · ${formatAmount(row.qty)}`}
      </span>
    ),
  },
  {
    key: 'price',
    header: '现价',
    width: '112px',
    numeric: true,
    sort: (a, b) => (a.latest_price ?? Number.NEGATIVE_INFINITY) - (b.latest_price ?? Number.NEGATIVE_INFINITY),
    render: (row) => (
      <span className={row.latest_price === null ? 'text-text-muted' : undefined}>
        {row.latest_price === null ? MISSING : row.latest_price.toFixed(2)}
      </span>
    ),
  },
  {
    key: 'value',
    header: '市值',
    width: '128px',
    numeric: true,
    sort: (a, b) => (a.market_value ?? Number.NEGATIVE_INFINITY) - (b.market_value ?? Number.NEGATIVE_INFINITY),
    render: (row) => (
      <span className={row.market_value === null ? 'text-text-muted' : undefined}>
        {formatIntOrMissing(row.market_value)}
      </span>
    ),
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
  },
  {
    key: 'day',
    header: '当日盈亏',
    width: '112px',
    numeric: true,
    sort: (a, b) => (a.daily_pnl ?? Number.NEGATIVE_INFINITY) - (b.daily_pnl ?? Number.NEGATIVE_INFINITY),
    render: (row) => {
      const pnl = formatSignedAmount(row.daily_pnl ?? null)
      return <SignedNumber text={pnl.text} tone={pnl.tone} arrow="" />
    },
  },
]

/** 市值等金额列：`null` 一律 `—`；有值走 `formatAmount` 的原样读数（§8），不用「13.92 万」折算——
 *  折算带空格，在 112px 列里会折成「13.92 / 万」两行。 */
function formatIntOrMissing(value: number | null | undefined): string {
  if (value === null || value === undefined) return MISSING
  return formatAmount(value)
}

interface SummaryRow {
  currency: string
  marketValue: string
  pnl: string
  pnlTone: 'up' | 'down' | 'flat'
  pnlPct: string
  pnlPctTone: 'up' | 'down' | 'flat'
  dayPnl: string
  dayPnlTone: 'up' | 'down' | 'flat'
}

export function HomePositionsCard({ view }: { view: BlockView<PositionsPayload> }) {
  const density = useDensity()
  const navigate = useNavigate()
  const payload = view.state === 'normal' || view.state === 'partial' ? view.data : null
  const items = payload?.items ?? []
  const valuation = payload?.valuation ?? null
  const pricedCount = payload ? payload.position_count : null
  const unpricedCost = valuation?.unpriced_cost ?? 0
  const dayMissing = valuation?.daily_pnl_missing ?? []
  const dayStatus = valuation?.daily_pnl_status
  const dayUnavailable = !valuation || valuation.daily_pnl === null || valuation.daily_pnl === undefined

  const pnl = formatSignedAmount(valuation?.unrealized_pnl ?? null)
  const pnlPct = formatRatePct(valuation?.unrealized_pnl_pct ?? null)
  const dayPnl = formatSignedAmount(valuation?.daily_pnl ?? null)

  const summary: SummaryRow[] = valuation
    ? [
        {
          // 后端这一版恒为 CNY 且不分组，汇总卡退化为单行「合计」（§5.2）
          currency: '合计',
          marketValue: formatIntOrMissing(valuation.market_value),
          pnl: pnl.text,
          pnlTone: pnl.tone,
          pnlPct: pnlPct.text,
          pnlPctTone: pnlPct.tone,
          dayPnl: dayUnavailable ? MISSING : dayPnl.text,
          dayPnlTone: dayUnavailable ? 'flat' : dayPnl.tone,
        },
      ]
    : []

  const summaryColumns: Array<Column<SummaryRow>> = [
    { key: 'currency', header: '币种', width: '88px', render: (row) => <span className="font-medium">{row.currency}</span> },
    { key: 'value', header: '市值', numeric: true, render: (row) => <span>{row.marketValue}</span> },
    { key: 'pnl', header: '持仓盈亏', width: '112px', numeric: true, render: (row) => <SignedNumber text={row.pnl} tone={row.pnlTone} arrow="" /> },
    { key: 'pnlPct', header: '收益率', width: '112px', numeric: true, render: (row) => <SignedNumber text={row.pnlPct} tone={row.pnlPctTone} arrow="" /> },
    { key: 'day', header: '当日盈亏', width: '112px', numeric: true, render: (row) => <SignedNumber text={row.dayPnl} tone={row.dayPnlTone} arrow="" /> },
  ]

  return (
    <SectionCard
      title={payload ? `我的持仓 · (${countOrMissing(pricedCount)})` : '我的持仓'}
      tone={payload && payload.data_source_status !== 'ok' ? 'warn' : 'neutral'}
      subtitle={
        payload ? (
          <span className="flex flex-wrap items-center gap-1">
            <SourceStatusBadge
              status={payload.data_source_status}
              detail={payload.price_lookup?.reason ?? null}
            />
            {payload.as_of && <span className="ldst-num text-text-muted">估值 {formatTime(payload.as_of)}</span>}
          </span>
        ) : (
          '持仓只在本机'
        )
      }
      actions={
        <Button variant="ghost" onClick={() => navigate('/positions')}>
          看明细 →
        </Button>
      }
      bodyClassName="p-0"
    >
      {/* 隐私承诺不随数据状态消失：加载中 / 失败 / 空态都常驻（§5.3） */}
      <div className="p-4 pb-0">
        <PrivacyNotice />
      </div>

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

      {(view.state === 'normal' || view.state === 'partial') && valuation && (
        <>
          <div className="pt-4">
            <DataTable<SummaryRow>
              columns={summaryColumns}
              rows={summary}
              rowKey={(row) => row.currency}
              caption="持仓币种汇总"
              density={density}
            />
          </div>
          <p className="px-4 pb-3 pt-2 text-caption leading-body text-text-muted">
            持仓表无 <code className="ldst-num">currency</code> 列，未按币种拆分，不猜币种。
            {unpricedCost > 0 && `其中 ${formatMoney(unpricedCost)} 成本未定价，市值不含它们。`}
          </p>

          <div className="border-t border-border">
            <DataTable<PositionItem>
              columns={HOLDING_COLUMNS}
              rows={items}
              rowKey={(row) => row.code}
              caption={`逐条持仓（${countOrMissing(pricedCount)} 只）`}
              density={density}
              maxHeight="240px"
              empty={
                <EmptyState
                  compact
                  title="本机还没有持仓"
                  description="导入 / 新增都在我的持仓页，首页不放新增表单。"
                />
              }
            />
          </div>
        </>
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
              description="导入 / 新增都在我的持仓页，首页不放新增表单。"
              action={
                <Button variant="secondary" onClick={() => navigate('/positions')}>
                  去我的持仓
                </Button>
              }
            />
          </div>
        ))}

      <div className="border-t border-border px-4 py-3 text-caption leading-body text-text-muted">
        <p>成本法：移动加权平均（DEC-038）；不含分红，已实现盈亏在交易日志页。</p>
        <p>
          当日盈亏 =（现价 − 昨收）× 股数
          {dayUnavailable
            ? `；本次未算出（${dayStatus === 'not_computed' ? '本次没计算' : (payload?.daily_lookup?.reason ?? '本机缺昨收价')}），整列显示 ${MISSING}，不推算。`
            : `；${dayMissing.length > 0 ? `有 ${dayMissing.length} 只算不出，整列按合计数展示。` : '逐条与合计同源。'}`}
        </p>
        <p>
          表头排序状态只记在这台浏览器的 localStorage；行情源：
          {quoteSourceLabel(payload?.price_source ?? 'spot_quote')}。持仓只走 127.0.0.1，不上传、不进 AI、不被 MCP 读取。
        </p>
      </div>
    </SectionCard>
  )
}
