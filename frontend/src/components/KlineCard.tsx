import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { CandlestickChart, Loader2 } from 'lucide-react'

import { ApiError } from '../api'
import {
  loadKline,
  type KlineAdjust,
  type KlineLoader,
  type KlinePeriod,
} from '../api/research'
import { EChart } from '../charts/EChart'
import { useChartPalette } from '../charts/cssVar'
import {
  fmtChange,
  fmtPrice,
  fmtVolume,
  klineOption,
  type KlineResult,
} from '../charts/klineView'
import { cn } from '../lib/cn'
import { MISSING } from '../lib/format'
import { Segmented } from './ui/Segmented'
import { SectionCard } from './ui/Card'

/**
 * 个股走势卡（QUOT-429 / DEC-057）：K 线 + 4 条均线 + 独立成交量副图。
 *
 * 数据来自 `GET /api/research/{code}/kline`，均线、涨跌幅、成交量单位都由
 * 后端算好，前端只负责画。只画行情，不画买卖点；A 股口径红涨绿跌。
 */

const PERIODS: { value: KlinePeriod; label: string }[] = [
  { value: 'daily', label: '日线' },
  { value: 'weekly', label: '周线' },
  { value: 'monthly', label: '月线' },
]

const ADJUSTS: { value: KlineAdjust; label: string }[] = [
  { value: 'qfq', label: '前复权' },
  { value: 'hfq', label: '后复权' },
  { value: 'none', label: '不复权' },
]

/** 复权只对 A 股有意义。写法与后端认的一致：600519 / 600519.SH / SH.600519。 */
const looksLikeAShare = (code: string) =>
  /^(\d{6}(\.(SH|SZ|BJ))?|(SH|SZ|BJ)\.\d{6})$/i.test(code.trim())

const describeError = (error: unknown): string =>
  error instanceof ApiError ? error.message : String(error)

export interface KlineCardProps {
  symbol: string
  /** 股票名称，显示在标题旁 */
  title?: string
  /** 标题栏右侧的额外操作（如「去个股页」） */
  action?: ReactNode
  className?: string
  /** 取数通道：默认走 HTTP，组件自证页与测试可注入替身 */
  loader?: KlineLoader
}

export function KlineCard({
  symbol,
  title,
  action,
  className,
  loader = loadKline,
}: KlineCardProps) {
  const palette = useChartPalette()
  const [period, setPeriod] = useState<KlinePeriod>('daily')
  const [adjust, setAdjust] = useState<KlineAdjust>('qfq')
  // 结果连同「它是哪次请求」一起记：换股票 / 周期 / 复权后，新结果没回来前
  // 不拿上一次的图顶上（放 effect 里清空会先闪一帧旧图）
  const [got, setGot] = useState<{ key: string; result: KlineResult } | null>(null)
  const [loading, setLoading] = useState(false)

  const aShareCode = looksLikeAShare(symbol)
  const requestKey = `${symbol}|${period}|${aShareCode ? adjust : 'none'}`

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setLoading(true)
    // 港美股不传复权：页面上不显示这个选项，传了上一只 A 股留下的选择只会换来一句看不懂的提示
    const promise = loader(
      aShareCode ? { symbol, period, adjust } : { symbol, period },
      controller.signal,
    )
    promise
      .then((result) => {
        if (active) setGot({ key: requestKey, result })
      })
      .catch((error: unknown) => {
        if (!active) return
        setGot({ key: requestKey, result: { ok: false, error: describeError(error) } })
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    // 切代码 / 周期 / 复权时丢掉上一次还没回来的请求，免得慢的那次后到把新图盖掉
    return () => {
      active = false
      controller.abort()
    }
  }, [symbol, period, adjust, aShareCode, requestKey, loader])

  const result = got?.key === requestKey ? got.result : null
  const view = result?.ok ? result.view : null
  const series = view?.series ?? null
  const option = useMemo(
    () => (view ? klineOption(view, palette) : null),
    [view, palette],
  )
  const aShare = view ? (view.market ? view.market === 'a_share' : aShareCode) : aShareCode

  const last = series ? series.dates.length - 1 : -1
  const lastChange = last >= 0 ? (series?.change[last] ?? null) : null
  const tone =
    lastChange === null || lastChange === 0
      ? 'text-text-secondary'
      : lastChange > 0
        ? 'text-up'
        : 'text-down'

  return (
    <SectionCard
      className={className}
      title={
        <span className="flex items-center gap-2">
          <CandlestickChart aria-hidden="true" className="h-4 w-4 text-accent" />
          走势
          {title && <span className="text-body font-medium text-text-secondary">{title}</span>}
        </span>
      }
      subtitle={
        view
          ? `${view.symbol || symbol} · ${[view.marketLabel, view.adjustLabel].filter(Boolean).join(' · ')}`
          : symbol
      }
      actions={
        <>
          {loading && (
            <Loader2
              aria-label="正在取 K 线"
              className="h-3.5 w-3.5 animate-spin text-text-muted"
            />
          )}
          <Segmented
            ariaLabel="周期"
            size="sm"
            value={period}
            onChange={setPeriod}
            options={PERIODS}
          />
          {aShare && (
            <Segmented
              ariaLabel="复权"
              size="sm"
              value={adjust}
              onChange={setAdjust}
              options={ADJUSTS}
            />
          )}
          {action}
        </>
      }
    >
      {result && !result.ok && (
        <p role="alert" className="text-body text-text-muted">
          K 线没有画出来：{result.error}
        </p>
      )}
      {!result && loading && (
        <p className="text-body text-text-muted" aria-busy="true">
          正在取 K 线…
        </p>
      )}

      {view && series && option && (
        <>
          <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-caption">
            <span className="ldst-num text-text-muted">{series.dates[last]}</span>
            <span>
              收盘{' '}
              <b className={cn('ldst-num text-body', tone)}>
                {fmtPrice(series.close[last] ?? null)}
              </b>
            </span>
            <span className={cn('ldst-num', tone)}>{fmtChange(lastChange)}</span>
            <span className="text-text-muted">
              开 <span className="ldst-num text-text">{fmtPrice(series.open[last] ?? null)}</span>
            </span>
            <span className="text-text-muted">
              高 <span className="ldst-num text-text">{fmtPrice(series.high[last] ?? null)}</span>
            </span>
            <span className="text-text-muted">
              低 <span className="ldst-num text-text">{fmtPrice(series.low[last] ?? null)}</span>
            </span>
            {view.volumeUnit && (
              <span className="text-text-muted">
                量{' '}
                <span className="ldst-num text-text">
                  {fmtVolume(series.volume[last] ?? null)}
                </span>{' '}
                {view.volumeUnit}
              </span>
            )}
          </div>

          <EChart
            option={option}
            height={420}
            ariaLabel={`${view.symbol || symbol} ${view.periodLabel} K 线，${view.bars} 根（${view.first} 至 ${view.last}），含成交量副图`}
          />

          <div className="mt-2 space-y-0.5 text-caption leading-body text-text-muted">
            {view.notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
            <p>
              来源：{view.source || MISSING}
              {view.fetchedAt ? ` · 取数 ${view.fetchedAt.slice(0, 16).replace('T', ' ')}` : ''}
              {' · '}
              {view.periodLabel} {view.bars} 根（{view.first} 至 {view.last}
              ），默认显示最近一段，拖动下方滑条或图表看更早，按住 Ctrl 滚轮（或双指捏合）缩放。
              均线是收盘价的简单移动平均。
            </p>
          </div>
        </>
      )}
    </SectionCard>
  )
}
