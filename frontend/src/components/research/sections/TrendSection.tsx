import { ChartLine } from 'lucide-react'

import type { ResearchOverview } from '../../../api/research'
import { MISSING, formatChangePct, formatMoney } from '../../../lib/format'
import { BarChart, LineChart } from '../../../charts/Chart'
import {
  Badge,
  EmptyState,
  MetricCard,
  SectionCard,
  SourceStatusBadge,
} from '../../ui'

/**
 * 区块 E：行情条 + 走势（QUOT-448 只落容器与挂载点）。
 *
 * props 契约（后续 QUOT-449 只改本文件，不动页面文件）：
 * - `overview`：`/api/research/{code}/overview` 的完整载荷；`null` = 还没取回；
 * - `code`：当前规范代码，缺数文案用；
 * - `className`：页面给的栅格类。
 * 取数失败时的「重试」由页面 `OverviewTab` 的 ErrorBlock 统一给，本区块不重复挂。
 *
 * 本单**不实现** K 线本体（QUOT-429/449 负责）。这里保留页面原有的
 * 「收盘价折线 + 成交额副图」（QUOT-400 已交付的临时实现，不新增图表能力），
 * 等 `KlineCard` 合入后由 QUOT-449 在同一位置替换 —— 见文件内 `KLINE_SLOT` 标记。
 */

export const KLINE_SLOT = 'kline-card-slot'

export interface TrendSectionProps {
  overview: ResearchOverview | null
  code: string | null
  className?: string
}

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
  const trend =
    first !== null && last !== null ? (last > first ? '上涨' : last < first ? '下跌' : '持平') : '缺收盘价'
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
        <BarChart points={volumePoints} height={72} ariaLabel={`成交额副图，${bars.length} 个交易日`} />
      )}
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-caption text-text-muted">
        <span className="ldst-num">
          {firstDate} → {lastDate}
        </span>
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
      {/* rail 是「≤1279px」的收窄档，宽度 ≥1280 必须显式写 desk，否则宽屏反而停回两列 */}
      <div className="grid grid-cols-2 gap-3 rail:grid-cols-4 desk:grid-cols-4">
        <MetricCard
          label="现价"
          value={quote?.price !== null && quote?.price !== undefined ? String(quote.price) : MISSING}
          note={quote ? undefined : '实时快照未取到'}
        />
        <MetricCard
          label="涨跌幅"
          value={`${change.text}${change.arrow ? ` ${change.arrow}` : ''}`}
          note={quote ? undefined : '实时快照未取到'}
        />
        <MetricCard
          label="成交量"
          value={quote?.volume !== null && quote?.volume !== undefined ? String(quote.volume) : MISSING}
          unit={quote?.volume !== null && quote?.volume !== undefined ? '手' : undefined}
          note={quote ? undefined : '实时快照未取到'}
        />
        <MetricCard
          label="成交额"
          value={quote?.amount !== null && quote?.amount !== undefined ? formatMoney(quote.amount) : MISSING}
          note={quote ? undefined : '实时快照未取到'}
        />
      </div>
    </SectionCard>
  )
}

export function TrendSection({ overview, code, className }: TrendSectionProps) {
  if (!overview) {
    return (
      <SectionCard className={className} title="走势" subtitle="加载中">
        <div
          aria-busy="true"
          className="flex h-48 items-center justify-center text-caption text-text-muted"
        >
          正在读取 {code ?? '该标的'} 的行情与日线缓存…
        </div>
      </SectionCard>
    )
  }

  return (
    <div className={className}>
      <QuoteStrip overview={overview} />
      <SectionCard
        className="mt-4"
        title="走势"
        subtitle="日线收盘价 + 成交额副图（本地 daily_k 原值回放）"
        actions={
          overview.kline.reason ? <Badge tone="warn">部分数据</Badge> : <Badge tone="ok">日线</Badge>
        }
      >
        <div data-slot={KLINE_SLOT}>
          <KlineChart overview={overview} />
        </div>
        {overview.kline.reason && (
          <p className="mt-2 text-caption leading-body text-text-muted">{overview.kline.reason}</p>
        )}
      </SectionCard>
    </div>
  )
}
