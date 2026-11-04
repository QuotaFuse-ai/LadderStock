import { useState, type ReactNode } from 'react'
import { ChartLine, FileClock } from 'lucide-react'

import {
  describeResearchError,
  KLINE_ADJUSTS,
  KLINE_ADJUST_LABEL,
  KLINE_PERIODS,
  KLINE_PERIOD_LABEL,
  type KlineAdjust,
  type KlineData,
  type KlinePeriod,
  type ResearchOverview,
  type ResearchSource,
} from '../../api/research'
import { MISSING, formatChangePct, formatMoney } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'
import { CandlestickChart } from '../../charts/CandlestickChart'
import {
  Badge,
  Button,
  EmptyState,
  ErrorBlock,
  MetricCard,
  SectionCard,
  Segmented,
  SkeletonText,
  SourceStatusBadge,
} from '../../components/ui'

/**
 * 个股深挖「概览」标签（QUOT-400 / QUOT-411）。
 *
 * 块序照参考实现（vibe-astock 个股页）：行情条 → K 线走势 → 财报卡。
 * 数据只来自两个只读接口的本机缓存回放：
 * - `/api/research/{code}/overview`：行情条（spot_quote）、财务指标（finance_indicators）；
 * - `/api/research/{code}/kline?period=…&adjust=…`（QUOT-411）：蜡烛主图 + 成交量副图
 *   + MA5/10/20/60，日/周/月、前/后/不复权切换各打一次端点，前端不合成行情。
 * 哪块缺就如实出哪块的缺数态，不补 0、不编数。概览**不依赖六段产物**。
 */

/**
 * K 线走势（QUOT-411）：蜡烛主图 + 成交量副图 + MA5/10/20/60 + 日期区间滑条，
 * 走 `charts/CandlestickChart.tsx`（uPlot 自定义 paths，**不引新图表库**）。
 * 周期（日/周/月）与复权（前/后/不复权）切换各自打新端点
 * `GET /api/research/{code}/kline?period=…&adjust=…`，前端不合成行情。
 * 数据源明确没有（available=false）或 bars 为空 → 「无数据（未核实）」，
 * 不造数、不补零、不放骨架假数据；加载中才显示骨架。
 */
function KlineChart({
  source,
  code,
  name,
}: {
  source: ResearchSource
  code: string
  name?: string
}) {
  const [period, setPeriod] = useState<KlinePeriod>('daily')
  const [adjust, setAdjust] = useState<KlineAdjust>('qfq')
  const request = useAsync<KlineData>(
    (signal) => source.kline(code, period, adjust, signal),
    [source, code, period, adjust],
  )

  const data = request.data
  const bars = data?.bars ?? []
  const empty = data !== null && (data.available === false || bars.length === 0)
  const firstDate = bars.length > 0 ? bars[0].date : ''
  const lastDate = bars.length > 0 ? bars[bars.length - 1].date : ''
  const periodLabel = KLINE_PERIOD_LABEL[period]

  const controls = (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented
        ariaLabel="K线周期"
        size="sm"
        options={KLINE_PERIODS.map((value) => ({ value, label: KLINE_PERIOD_LABEL[value] }))}
        value={period}
        onChange={setPeriod}
      />
      <Segmented
        ariaLabel="K线复权"
        size="sm"
        options={KLINE_ADJUSTS.map((value) => ({ value, label: KLINE_ADJUST_LABEL[value] }))}
        value={adjust}
        onChange={setAdjust}
      />
    </div>
  )

  let body: ReactNode
  if (request.status === 'error') {
    body = (
      <ErrorBlock {...describeResearchError(request.error)} variant="inline" onRetry={request.reload} />
    )
  } else if (request.status === 'loading' && data === null) {
    body = <SkeletonText lines={6} />
  } else if (empty) {
    body = (
      <EmptyState
        compact
        icon={<ChartLine aria-hidden="true" />}
        title="无数据（未核实）"
        description={
          data?.reason ??
          '数据源没有返回该周期/复权的 K 线；页面不拼接、不补零、不放占位假数据。'
        }
      />
    )
  } else if (bars.length > 0) {
    body = (
      <CandlestickChart
        bars={bars}
        ariaLabel={`${name ?? code} ${periodLabel}K线（${KLINE_ADJUST_LABEL[adjust]}），${bars.length} 根，从 ${firstDate} 到 ${lastDate}`}
      />
    )
  } else {
    body = <SkeletonText lines={6} />
  }

  return (
    <figure className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {controls}
        {bars.length > 0 && (
          <span className="ldst-num text-caption text-text-muted">
            {bars.length} 根 · {firstDate} → {lastDate} · {periodLabel}线 {KLINE_ADJUST_LABEL[adjust]}
          </span>
        )}
      </div>
      {body}
    </figure>
  )
}

function QuoteStrip({ overview }: { overview: ResearchOverview }) {
  const { quote } = overview.quote
  const change = formatChangePct(quote?.change_pct ?? null)
  return (
    <SectionCard
      title={overview.name ?? overview.code}
      subtitle={
        quote?.timestamp
          ? `行情快照 ${quote.timestamp.replace(/^(\d{4})(\d{2})(\d{2})/, '$1-$2-$3 ')}`
          : (overview.quote.reason ?? '实时快照')
      }
      actions={
        <SourceStatusBadge
          status={overview.dataSourceStatus === 'ok' ? 'ok' : 'missing'}
          detail={overview.quote.reason}
        />
      }
    >
      <div className="grid grid-cols-2 gap-3 rail:grid-cols-4">
        <MetricCard
          label="现价"
          value={quote?.price !== null && quote?.price !== undefined ? String(quote.price) : MISSING}
          note={quote ? undefined : '本地 spot_quote 无快照'}
        />
        <MetricCard
          label="涨跌幅"
          value={`${change.text}${change.arrow ? ` ${change.arrow}` : ''}`}
          note={quote ? undefined : '本地 spot_quote 无快照'}
        />
        <MetricCard
          label="成交量"
          value={quote?.volume !== null && quote?.volume !== undefined ? String(quote.volume) : MISSING}
          unit={quote?.volume !== null && quote?.volume !== undefined ? '手' : undefined}
          note={quote ? undefined : '本地 spot_quote 无快照'}
        />
        <MetricCard
          label="成交额"
          value={quote?.amount !== null && quote?.amount !== undefined ? formatMoney(quote.amount) : MISSING}
          note={quote ? undefined : '本地 spot_quote 无快照'}
        />
      </div>
    </SectionCard>
  )
}

function FinancialCard({ overview }: { overview: ResearchOverview }) {
  const { financials, reason } = overview.financials
  const metrics: Array<{ label: string; value: number | null; unit?: string }> = financials
    ? [
        { label: 'EPS', value: financials.eps, unit: '元' },
        { label: '营业收入', value: financials.revenue },
        { label: '归母净利润', value: financials.net_profit },
        { label: 'ROE', value: financials.roe, unit: '%' },
        { label: '营收同比', value: financials.revenue_yoy, unit: '%' },
        { label: '净利润同比', value: financials.profit_yoy, unit: '%' },
      ]
    : []
  return (
    <SectionCard
      title="财报前瞻与复盘"
      subtitle={
        financials
          ? `${financials.report_name ?? '最近一期'}（${financials.report_date ?? '日期缺失'}）原值`
          : '最近一期财报'
      }
      actions={<Badge tone={financials ? 'ok' : 'neutral'}>{financials ? '有缓存' : '缺数'}</Badge>}
    >
      {!financials ? (
        <EmptyState compact icon={<FileClock aria-hidden="true" />} title="本地无财务指标缓存" description={reason ?? undefined} />
      ) : (
        <div className="grid grid-cols-2 gap-3 rail:grid-cols-3">
          {metrics.map(({ label, value, unit }) => (
            <MetricCard
              key={label}
              label={label}
              value={value === null ? MISSING : formatMoney(value)}
              unit={unit && value !== null ? unit : undefined}
              note={value === null ? '上游未提供' : undefined}
            />
          ))}
        </div>
      )}
      <p className="mt-3 border-t border-border pt-2 text-caption leading-body text-text-muted">
        口径：东财财务指标原值，同比为单季或累计以字段名为准，ROE 为累计值非年化；缺失显示 —，不补 0。
      </p>
    </SectionCard>
  )
}

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载概览…</span>
      <SectionCard title="行情快照" subtitle="加载中">
        <SkeletonText lines={2} />
      </SectionCard>
      <SectionCard title="走势" subtitle="加载中">
        <SkeletonText lines={6} />
      </SectionCard>
      <SectionCard title="财报前瞻与复盘" subtitle="加载中">
        <SkeletonText lines={3} />
      </SectionCard>
    </div>
  )
}

export default function OverviewTab({
  overview,
  code,
  source,
  error,
  requesting,
  reload,
}: {
  overview: ResearchOverview | null
  code: string | null
  source: ResearchSource
  error: unknown
  requesting: boolean
  reload: () => void
}) {
  if (error !== null && error !== undefined) {
    const info = describeResearchError(error)
    return (
      <div className="flex flex-col gap-4">
        <ErrorBlock {...info} onRetry={reload} />
      </div>
    )
  }
  if (!overview) {
    return <OverviewSkeleton />
  }
  if (overview.dataSourceStatus === 'missing') {
    return (
      <div className="flex flex-col gap-4">
        <EmptyState
          icon={<ChartLine aria-hidden="true" />}
          title="概览数据缺失"
          description={
            overview.kline.reason ??
            `本地缓存里没有 ${code ?? overview.code} 的日线数据；先运行 ldst sync --dataset daily_k --codes ${code ?? overview.code}，当前不会伪造任何数据。`
          }
          action={
            <Button variant="secondary" onClick={reload} loading={requesting}>
              重试
            </Button>
          }
        />
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <QuoteStrip overview={overview} />
      <SectionCard
        title="走势"
        subtitle="K 线 + 成交量 + 均线（本地 daily_k 原值回放，前端不合成行情）"
        actions={
          overview.kline.reason ? <Badge tone="warn">部分数据</Badge> : <Badge tone="ok">K线</Badge>
        }
      >
        <KlineChart
          source={source}
          code={overview.code}
          name={overview.name ?? undefined}
        />
        {overview.kline.reason && (
          <p className="mt-2 text-caption leading-body text-text-muted">{overview.kline.reason}</p>
        )}
      </SectionCard>
      <FinancialCard overview={overview} />
      <p className="text-caption leading-body text-text-muted">
        口径：概览只读本机缓存（daily_k / spot_quote / finance_indicators），不依赖六段研究产物；
        缺数显示 — + 原因，不补 0、不联网取数。
      </p>
    </div>
  )
}
