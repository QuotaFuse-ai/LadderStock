import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { History, Play, RefreshCw } from 'lucide-react'

import { isApiError } from '../../api'
import {
  fetchPortfolioReplay,
  type PortfolioItem,
  type PortfolioReplayPayload,
  type PortfolioReplayScenario,
} from '../../api/radar'
import { EChart } from '../../charts/EChart'
import { useChartPalette, type ChartPalette } from '../../charts/cssVar'
import {
  Banner,
  Button,
  EmptyState,
  MetricCard,
  SectionCard,
  Segmented,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING } from '../../lib/format'
import { PrivacyText } from './PrivacyText'
import { TONE_CLASS, moneyText, pctOfDecimal, signedMoney, toneOf } from './portfolioFormat'

/**
 * 情景回放子页（QUOT-467）：把**当前持仓**按市值比例放回 4 段固定 A 股压力窗口，
 * 看那段时间组合的收益、回撤、最差单日、覆盖率与净值路径。
 *
 * 四条硬约束：
 * 1. 默认不请求：进标签页只显示说明，点「开始回放」才取数（要读整段历史日线）；
 * 2. 只回放历史：不预测、不给买卖建议；
 * 3. 金额只在隐私模式下遮（`PrivacyText`），百分比与 `—` 不遮；
 * 4. 算不了的情景只显示原因，绝不显示 0。
 */

const CACHE_MS = 30 * 60 * 1000

/** 模块级缓存：同一份持仓半小时内反复切标签不重跑。只在内存，不写 localStorage。 */
let cache: { signature: string; at: number; payload: PortfolioReplayPayload } | null = null

function readCache(signature: string): PortfolioReplayPayload | null {
  if (!cache || cache.signature !== signature) return null
  return Date.now() - cache.at < CACHE_MS ? cache.payload : null
}

/** 缓存 key：`symbol:shares:market_value` 用 `|` 拼接；持仓一变就换 key。 */
export function holdingsSignature(items: readonly PortfolioItem[]): string {
  return items.map((item) => `${item.symbol}:${item.shares}:${item.market_value}`).join('|')
}

export interface PortfolioReplayTabProps {
  /** 当前持仓签名；变了说明页面上的结果已经不是按现在的持仓算的。 */
  signature: string
  /** 当前持仓只数：0 就不让点，提示先记流水。 */
  holdingCount: number
}

interface ReplayState {
  /** 这份结果算的时候持仓是什么签名。 */
  signature: string
  payload: PortfolioReplayPayload
}

export function PortfolioReplayTab({ signature, holdingCount }: PortfolioReplayTabProps) {
  const palette = useChartPalette()
  const [state, setState] = useState<ReplayState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [pick, setPick] = useState('all')
  const controller = useRef<AbortController | null>(null)

  const run = useCallback(() => {
    controller.current?.abort()
    setError(null)
    const hit = readCache(signature)
    if (hit) {
      setLoading(false)
      setState({ signature, payload: hit })
      return
    }
    const next = new AbortController()
    controller.current = next
    setLoading(true)
    fetchPortfolioReplay(next.signal)
      .then((payload) => {
        if (next.signal.aborted) return
        cache = { signature, at: Date.now(), payload }
        setState({ signature, payload })
      })
      .catch((cause) => {
        if (next.signal.aborted) return
        setError(isApiError(cause) ? (cause.detail ?? cause.message) : String(cause))
      })
      .finally(() => {
        if (!next.signal.aborted) setLoading(false)
      })
  }, [signature])

  // 切走 / 卸载：上一次请求没回来就不等了
  useEffect(() => () => controller.current?.abort(), [])

  const payload = state?.payload ?? null
  const group = payload?.groups[0] ?? null
  const scenarios = group?.scenarios ?? []
  const noHoldings = holdingCount === 0 || (payload !== null && payload.groups.length === 0)
  const stale = state !== null && state.signature !== signature

  const filterOptions = useMemo(
    () => [{ value: 'all', label: '全部' }, ...scenarios.map((item) => ({ value: item.id, label: item.label }))],
    [scenarios],
  )
  const activePick = filterOptions.some((option) => option.value === pick) ? pick : 'all'
  const visible = activePick === 'all' ? scenarios : scenarios.filter((item) => item.id === activePick)

  return (
    <SectionCard
      title="情景回放"
      subtitle="按现在的持仓比例回看历史；只回放，不预测"
      actions={
        <Button
          variant={state ? 'secondary' : 'primary'}
          loading={loading}
          disabled={noHoldings}
          disabledReason={noHoldings ? '先记一笔流水，有持仓后才能回放' : undefined}
          leadingIcon={
            state && !loading ? <RefreshCw className="size-3.5" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />
          }
          onClick={run}
        >
          {state ? '重新回放' : '开始回放'}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {loading && <p className="text-caption text-text-muted">正在回放…只读本机日线，不联网</p>}

        {error !== null && (
          <Banner tone="warn" title="这次回放没做成">
            {error}
          </Banner>
        )}

        {noHoldings && (
          <EmptyState
            icon={<History />}
            title="还没有可回放的持仓"
            description="先记一笔流水，有持仓后才能回放。"
          />
        )}

        {!noHoldings && !state && !loading && error === null && (
          <p className="text-body leading-body text-text-secondary">
            把现在持仓按市值比例放回 4 段历史压力窗口（2015 股灾、2018 熊市、2020 疫情、2024 流动性），
            看组合那段时间的区间收益、最大回撤、最差单日，以及哪些持仓因为本机没有日线没算进来。
            要读整段历史日线，不会自动跑，点「开始回放」开始。
          </p>
        )}

        {stale && (
          <Banner tone="warn" title="持仓已变化，重新回放以更新">
            下面还是按上一次的持仓算的结果；点「重新回放」按现在的持仓重算。
          </Banner>
        )}

        {state && group && scenarios.length > 0 && (
          <Segmented ariaLabel="压力窗口" size="sm" options={filterOptions} value={activePick} onChange={setPick} />
        )}

        {state && !group && (
          <p className="text-body text-text-secondary">
            {state.payload.notes[0] ?? '本机还没有可回放的持仓流水。'}
          </p>
        )}

        {group && (
          <>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-border pt-3 text-caption text-text-secondary">
              <span className="text-subtitle font-semibold text-text">{group.label}</span>
              <span>
                当前持仓市值 <PrivacyText className="ldst-num" text={moneyText(group.total_value)} /> ·{' '}
                {group.input} 只
              </span>
            </div>
            {visible.map((scenario) => (
              <ScenarioCard
                key={scenario.id}
                scenario={scenario}
                input={group.input}
                palette={palette}
              />
            ))}
          </>
        )}

        {payload && payload.notes.length > 0 && (
          <div className="flex flex-col gap-0.5 border-t border-border pt-3 text-caption leading-body text-text-muted">
            {payload.notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
            <p>
              价格来源：{payload.sources.length > 0 ? payload.sources.join('、') : MISSING} · 估值日{' '}
              {payload.as_of ?? MISSING}
            </p>
          </div>
        )}

        <p className="border-t border-border pt-3 text-caption text-text-muted">
          按现在的持仓比例回看历史，不代表未来；不构成建议。
        </p>
      </div>
    </SectionCard>
  )
}

function ScenarioCard({
  scenario,
  input,
  palette,
}: {
  scenario: PortfolioReplayScenario
  input: number
  palette: ChartPalette
}) {
  const stats = scenario.portfolio
  const coverage = scenario.coverage === null ? MISSING : `${Math.round(scenario.coverage * 100)}%`

  return (
    <section className="flex flex-col gap-3 border-t border-border pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-body font-semibold text-text">{scenario.label}</h3>
        <span className="ldst-num text-caption text-text-muted">
          {scenario.start || MISSING} 至 {scenario.end || MISSING}
        </span>
      </div>

      {scenario.unavailable !== null ? (
        <Banner tone="warn" title="这段窗口算不了">
          {scenario.unavailable}
        </Banner>
      ) : stats === null ? (
        <p className="text-caption text-text-muted">这段窗口没有给出组合收益。</p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="组合收益"
              value={<span className={TONE_CLASS[toneOf(stats.return)]}>{pctOfDecimal(stats.return).text}</span>}
              note={
                <>
                  纳入市值 <PrivacyText text={moneyText(stats.included_value)} /> · 变动{' '}
                  <PrivacyText text={signedMoney(stats.value_change)} />
                </>
              }
            />
            <MetricCard
              label="最大回撤"
              value={<span className={TONE_CLASS[toneOf(stats.max_drawdown)]}>{pctOfDecimal(stats.max_drawdown).text}</span>}
              note={
                stats.max_drawdown_peak && stats.max_drawdown_trough
                  ? `${stats.max_drawdown_peak} → ${stats.max_drawdown_trough}`
                  : '峰谷日期未给出'
              }
            />
            <MetricCard
              label="最差单日"
              value={<span className={TONE_CLASS[toneOf(stats.worst_day)]}>{pctOfDecimal(stats.worst_day).text}</span>}
              note={stats.worst_day_date ?? '日期未给出'}
            />
            <MetricCard
              label="覆盖率"
              value={<span className="text-text">{coverage}</span>}
              note={`${scenario.included}/${input} 只 · 纳入持仓的当前市值占比`}
            />
          </div>

          <PathChart scenario={scenario} palette={palette} />
          <HoldingsTable scenario={scenario} />
          <GapList gaps={scenario.gaps} />
        </>
      )}
    </section>
  )
}

function PathChart({ scenario, palette }: { scenario: PortfolioReplayScenario; palette: ChartPalette }) {
  const path = scenario.path
  const option = useMemo(() => (path ? pathOption(path.dates, path.portfolio, palette) : null), [path, palette])
  if (!option) {
    return <p className="text-caption text-text-muted">有效交易日不足 2 天，不画净值路径。</p>
  }
  return (
    <div>
      <p className="mb-1 text-caption font-medium text-text-secondary">组合净值路径</p>
      <EChart
        option={option}
        height={200}
        ariaLabel={`${scenario.label}组合净值路径，${scenario.start} 至 ${scenario.end}`}
      />
    </div>
  )
}

function HoldingsTable({ scenario }: { scenario: PortfolioReplayScenario }) {
  if (scenario.holdings.length === 0) return null
  const head = 'px-2 py-1.5 font-medium'
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-caption">
        <thead>
          <tr className="border-b border-border text-left text-text-muted">
            <th className={head}>持仓</th>
            <th className={cn(head, 'text-right')}>占比</th>
            <th className={cn(head, 'text-right')}>区间涨跌</th>
            <th className={cn(head, 'text-right')}>贡献</th>
            <th className={head}>用到的价格</th>
          </tr>
        </thead>
        <tbody>
          {scenario.holdings.map((holding) => (
            <tr key={holding.symbol} className="border-b border-border/60">
              <td className="px-2 py-1.5">
                {holding.name || holding.symbol}{' '}
                {holding.name && <span className="ldst-num text-text-muted">{holding.symbol}</span>}
              </td>
              <td className="ldst-num px-2 py-1.5 text-right">
                {holding.weight === null ? MISSING : `${(holding.weight * 100).toFixed(1)}%`}
              </td>
              <td className={cn('ldst-num px-2 py-1.5 text-right', TONE_CLASS[toneOf(holding.return)])}>
                {pctOfDecimal(holding.return).text}
              </td>
              <td className={cn('ldst-num px-2 py-1.5 text-right', TONE_CLASS[toneOf(holding.contribution)])}>
                {pctOfDecimal(holding.contribution).text}
              </td>
              <td className="px-2 py-1.5 text-text-secondary">
                {holding.first_date || MISSING} → {holding.last_date || MISSING}
                {holding.halted_days > 0 ? `，其间 ${holding.halted_days} 个交易日没有价格` : ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GapList({ gaps }: { gaps: PortfolioReplayScenario['gaps'] }) {
  if (gaps.length === 0) return null
  return (
    <div className="text-caption text-text-secondary">
      <p className="font-medium text-text">未纳入的持仓与原因</p>
      <ul className="mt-1 flex flex-col gap-0.5">
        {gaps.map((gap) => (
          <li key={gap.symbol}>
            <span className="ldst-num">{gap.name ? `${gap.name} ${gap.symbol}` : gap.symbol}</span>：{gap.reason}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 轴刻度：0.0095 → `0.95%`（区间窄时两位小数，宽时自然去掉多余的 0）。 */
function pctTick(value: number): string {
  const text = (value * 100)
    .toFixed(2)
    .replace(/\.00$/, '')
    .replace(/(\.\d)0$/, '$1')
  return `${text}%`
}

/** 净值路径：只画百分比（`x - 1`），金额在隐私模式下要遮，因此不进图。 */
function pathOption(dates: string[], netValues: number[], palette: ChartPalette) {
  if (dates.length < 2 || netValues.length !== dates.length) return null
  return {
    animation: false,
    grid: { left: 8, right: 32, top: 12, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.axis,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      valueFormatter: (value: unknown) => (typeof value === 'number' ? pctTick(value) : MISSING),
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { color: palette.text, fontSize: 11 },
      axisLine: { lineStyle: { color: palette.axis } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: {
        color: palette.text,
        fontSize: 11,
        formatter: (value: number) => pctTick(value),
      },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series: [
      {
        name: '组合净值',
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 1.6, color: palette.line },
        itemStyle: { color: palette.line },
        data: netValues.map((value) => value - 1),
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: palette.axis, type: 'dashed' },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  }
}

export default PortfolioReplayTab
