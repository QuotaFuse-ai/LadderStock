import { ChartLine, FileClock } from 'lucide-react'

import { describeResearchError, type ResearchOverview } from '../../api/research'
import { MISSING, formatChangePct, formatMoney } from '../../lib/format'
import { BarChart, LineChart } from '../../charts/Chart'
import {
  Badge,
  Button,
  EmptyState,
  ErrorBlock,
  MetricCard,
  SectionCard,
  SkeletonText,
  SourceStatusBadge,
} from '../../components/ui'

/**
 * 个股深挖「概览」标签（QUOT-400）。
 *
 * 块序照参考实现（vibe-astock 个股页）：行情条 → K 线走势 → 财报卡。
 * 数据只来自 `/api/research/{code}/overview` 的本机缓存回放：
 * K 线（daily_k）、快照（spot_quote）、财务指标（finance_indicators），
 * 哪块缺就如实出哪块的缺数态，不补 0、不编数。概览**不依赖六段产物**。
 */

/**
 * 走势图：复用仓库已有的 uPlot 图表（`charts/Chart.tsx`，CH-01/CH-02），
 * 收盘线走 `LineChart`（`yScale="auto"`，收盘价不是 0 起的量）、
 * 成交额副图走 `BarChart`，**不引新依赖**。数值一律来自 bars 原值，页面不重算。
 */
function KlineChart({ overview }: { overview: ResearchOverview }) {
  const bars = overview.kline.bars
  const closes = bars.map((bar) => bar.close)
  const volumes = bars.map((bar) => bar.volume)

  if (closes.filter((value) => value !== null).length < 2) {
    return (
      <EmptyState
        compact
        icon={<ChartLine aria-hidden="true" />}
        title="数据点不足，暂不绘制走势"
        description={overview.kline.reason ?? '本地日线缓存不足 2 根，页面不会拼一条假趋势线。'}
      />
    )
  }

  const points = bars.map((bar) => ({ label: bar.date, value: bar.close }))
  const volumeOk = volumes.every((value) => value !== null)
  const volumePoints = volumeOk
    ? bars.map((bar) => ({ label: bar.date, value: bar.volume as number }))
    : []
  const firstDate = bars[0].date
  const lastDate = bars[bars.length - 1].date
  const first = closes[0]
  const last = closes[closes.length - 1]
  const trend = first !== null && last !== null ? (last > first ? '上涨' : last < first ? '下跌' : '持平') : '缺收盘价'
  const stroke = trend === '上涨' ? 'text-up' : trend === '下跌' ? 'text-down' : 'text-text-secondary'

  return (
    <figure className="flex flex-col gap-2">
      <div className={stroke}>
        <LineChart
          points={points}
          height={220}
          yScale="auto"
          ariaLabel={`${overview.name ?? overview.code} 收盘价走势，${bars.length} 个交易日，从 ${firstDate} 到 ${lastDate}，区间内${trend}`}
        />
      </div>
      {volumeOk && (
        <BarChart
          points={volumePoints}
          height={72}
          ariaLabel={`成交额副图，${bars.length} 个交易日`}
        />
      )}
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-caption text-text-muted">
        <span className="ldst-num">{firstDate} → {lastDate}</span>
        <span className="ldst-num">
          {bars.length} 个交易日{volumeOk ? ' · 成交额副图' : ' · 成交额缺数，副图不绘'}
        </span>
      </figcaption>
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
  error,
  requesting,
  reload,
}: {
  overview: ResearchOverview | null
  code: string | null
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
        subtitle="日线收盘价 + 成交额副图（本地 daily_k 原值回放）"
        actions={
          overview.kline.reason ? <Badge tone="warn">部分数据</Badge> : <Badge tone="ok">日线</Badge>
        }
      >
        <KlineChart overview={overview} />
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
