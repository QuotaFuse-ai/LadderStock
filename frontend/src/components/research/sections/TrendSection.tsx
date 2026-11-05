import { useState } from 'react'
import { ChartLine, RotateCw } from 'lucide-react'

import type { ResearchOverview } from '../../../api/research'
import { MISSING, formatChangePct, formatMoney } from '../../../lib/format'
import { fmtPrice, fmtVolume } from '../../../charts/klineView'
import { KlineCard } from '../../KlineCard'
import {
  Button,
  EmptyState,
  MetricCard,
  SectionCard,
  SourceStatusBadge,
} from '../../ui'

/**
 * 区块 E：行情条 + 走势（QUOT-449）。
 *
 * 数据面来自 `/api/research/{code}/overview`（`payload.quote` 实时快照）与
 * `KlineCard` 自取的 `/api/research/{code}/kline`。取不到的字段一律 `—`，
 * 不补 0、不拿上一根 K 线倒填实时行情。
 *
 * 周期 / 复权选择记在 `KlineCard` 组件状态里：同一标的内切换保留，刷新页面或
 * 切到别的代码后回到「日线 / 前复权」默认值（不做跨会话持久化）。
 */

export const KLINE_SLOT = 'kline-card-slot'

export interface TrendSectionProps {
  overview: ResearchOverview | null
  code: string | null
  className?: string
}

/** 当日行情条：收盘 / 涨跌 / 开 / 高 / 低 / 量，全部取自实时快照。 */
function QuoteBar({ overview }: { overview: ResearchOverview }) {
  const quote = overview.quote.quote
  if (!quote) {
    return (
      <p role="status" className="mb-3 text-caption leading-body text-text-muted">
        实时快照未取到：{overview.quote.reason ?? '上游没有返回该标的的报价'}。读数留 —，不补 0。
      </p>
    )
  }

  const change = formatChangePct(quote.change_pct)
  const tone =
    change.tone === 'up' ? 'text-up' : change.tone === 'down' ? 'text-down' : 'text-text-secondary'
  const source = quote.source_detail
  const fetchedAt = source?.fetched_at
    ? source.fetched_at.slice(0, 16).replace('T', ' ')
    : null
  const sourceLabel = [source?.peer, source?.tool].filter(Boolean).join(' · ')

  return (
    <div className="mb-3 flex flex-col gap-0.5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-caption">
        <span className="text-text-muted">
          收盘 <b className={`ldst-num text-body ${tone}`}>{fmtPrice(quote.price)}</b>
        </span>
        <span className={`ldst-num ${tone}`}>{change.text}</span>
        <span className="text-text-muted">
          开 <span className="ldst-num text-text">{fmtPrice(quote.open)}</span>
        </span>
        <span className="text-text-muted">
          高 <span className="ldst-num text-text">{fmtPrice(quote.high)}</span>
        </span>
        <span className="text-text-muted">
          低 <span className="ldst-num text-text">{fmtPrice(quote.low)}</span>
        </span>
        <span className="text-text-muted">
          量 <span className="ldst-num text-text">{fmtVolume(quote.volume)}</span> 手
        </span>
      </div>
      <p className="text-caption text-text-muted">
        来源：{sourceLabel || MISSING}
        {fetchedAt ? ` · 取数 ${fetchedAt}` : ''}
      </p>
    </div>
  )
}

function QuoteStrip({ overview }: { overview: ResearchOverview }) {
  const { quote } = overview.quote
  const change = formatChangePct(quote?.change_pct ?? null)
  const note = quote ? undefined : '实时快照未取到'
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
        <MetricCard label="现价" value={fmtPrice(quote?.price ?? null)} note={note} />
        <MetricCard
          label="涨跌幅"
          value={`${change.text}${change.arrow ? ` ${change.arrow}` : ''}`}
          note={note}
        />
        <MetricCard
          label="成交量"
          value={fmtVolume(quote?.volume ?? null)}
          unit={quote ? '手' : undefined}
          note={note}
        />
        <MetricCard
          label="成交额"
          value={quote?.amount !== null && quote?.amount !== undefined ? formatMoney(quote.amount) : MISSING}
          note={note}
        />
      </div>
      <div className="mt-3 border-t border-border pt-2">
        <QuoteBar overview={overview} />
      </div>
    </SectionCard>
  )
}

export function TrendSection({ overview, code, className }: TrendSectionProps) {
  const [retryKey, setRetryKey] = useState(0)

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

  const retry = (
    <Button
      variant="ghost"
      onClick={() => setRetryKey((value) => value + 1)}
      leadingIcon={<RotateCw aria-hidden="true" className="h-3.5 w-3.5" />}
    >
      重试
    </Button>
  )

  const enoughBars = overview.kline.count >= 2

  return (
    <div className={className}>
      <QuoteStrip overview={overview} />
      <div className="mt-4" data-slot={KLINE_SLOT}>
        {enoughBars ? (
          <KlineCard key={retryKey} symbol={code ?? overview.code} action={retry} />
        ) : (
          <SectionCard
            title="走势"
            subtitle={`${code ?? overview.code} · K 线`}
            actions={retry}
          >
            <QuoteBar overview={overview} />
            <EmptyState
              compact
              icon={<ChartLine aria-hidden="true" />}
              title="K 线不足 2 根，暂不绘图"
              description={
                overview.kline.reason ??
                '上游没有返回足够的日线（未上市 / 长期停牌 / 取数失败），页面不会拼一条假走势。'
              }
            />
          </SectionCard>
        )}
      </div>
      {overview.kline.reason && enoughBars && (
        <p className="mt-2 text-caption leading-body text-text-muted">{overview.kline.reason}</p>
      )}
    </div>
  )
}
