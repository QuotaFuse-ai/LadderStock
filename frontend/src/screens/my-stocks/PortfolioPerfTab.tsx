import { useMemo } from 'react'
import { AlertTriangle, RefreshCw, TrendingUp } from 'lucide-react'

import { describeApiError } from '../../api'
import { fetchPortfolioPerf, type PortfolioPerfGroup } from '../../api/radar'
import { EChart } from '../../charts/EChart'
import { useChartPalette, type ChartPalette } from '../../charts/cssVar'
import {
  Banner,
  Button,
  EmptyState,
  ErrorBlock,
  MetricCard,
  SectionCard,
  SkeletonRows,
} from '../../components/ui'
import { MISSING } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'
import {
  TONE_CLASS,
  moneyText,
  pctOfDecimal,
  signedMoney,
  toneOf,
} from './portfolioFormat'
import { PrivacyText } from './PrivacyText'

/**
 * 收益子页（QUOT-434 / 后端 A2）：TWR、资金加权、金额合计与净值走势。
 *
 * 每个数都取自 `GET /api/portfolio/perf`，页面不重算；金额在隐私模式下遮成掩码，
 * 百分比保留（推不出本金）。不足 2 个交易日时后端不给 `series`，这里就不画线。
 */

export interface PortfolioPerfTabProps {
  /** 流水变了以后由页面加一，触发重算（避免拿旧收益率配新持仓）。 */
  refreshKey: number
  /** 旧记录还没确认时的提示条数 */
  pendingLegacy: number
}

export function PortfolioPerfTab({ refreshKey, pendingLegacy }: PortfolioPerfTabProps) {
  const palette = useChartPalette()
  const view = useAsync((signal) => fetchPortfolioPerf(signal), [refreshKey])
  const payload = view.data

  if (view.status === 'loading' && !payload) {
    return (
      <SectionCard title="收益率" subtitle="按交易流水逐日估值，只读本机日线缓存">
        <SkeletonRows rows={4} />
      </SectionCard>
    )
  }

  if (view.status === 'error' && !payload) {
    const error = describeApiError(view.error)
    return <ErrorBlock title={error.title} detail={error.detail} code={error.code} onRetry={view.reload} />
  }

  if (!payload) return null

  return (
    <SectionCard
      title="收益率"
      subtitle="按交易流水逐日估值；只算 A 股 / 人民币，不跨币种"
      actions={
        <Button
          variant="ghost"
          loading={view.refreshing}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          onClick={() => view.refresh({ manual: true })}
        >
          重新计算
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {pendingLegacy > 0 && (
          <Banner tone="warn" title={`还有 ${pendingLegacy} 条旧持有记录等你确认`}>
            确认成流水之前，这些记录不算进收益率。
          </Banner>
        )}

        {payload.groups.length === 0 ? (
          <EmptyState
            icon={<TrendingUp />}
            title="还没有可算的收益率"
            description={payload.notes[0] ?? '记一笔之后会自动计算；价格只读本机日线缓存，不联网补价。'}
          />
        ) : (
          payload.groups.map((group) => <GroupView key={group.currency} group={group} palette={palette} />)
        )}

        {payload.skipped.length > 0 && (
          <div className="border-t border-border pt-3 text-caption text-text-secondary">
            <p>{payload.skipped.length} 笔数据不对的流水没算（原因见上方持仓表的问题流水）。</p>
          </div>
        )}

        <div className="flex flex-col gap-1 border-t border-border pt-3 text-caption leading-body text-text-muted">
          {payload.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          <p>
            价格来源：{payload.sources.length > 0 ? payload.sources.join('、') : MISSING} · 算到{' '}
            {payload.as_of ?? MISSING}
          </p>
        </div>
      </div>
    </SectionCard>
  )
}

function GroupView({ group, palette }: { group: PortfolioPerfGroup; palette: ChartPalette }) {
  const option = useMemo(() => perfOption(group, palette), [group, palette])
  const twr = pctOfDecimal(group.twr)
  const annualized = pctOfDecimal(group.twr_annualized)
  const mwr = pctOfDecimal(group.mwr_period)
  const xirr = pctOfDecimal(group.xirr)

  return (
    <section className="flex flex-col gap-3 border-t border-border pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-subtitle font-semibold text-text">{group.label}</h3>
        {group.ok && (
          <span className="ldst-num text-caption text-text-secondary">
            {group.start ?? MISSING} 至 {group.end ?? MISSING}
            {group.span_days === null ? '' : `（${group.span_days} 天）`}
          </span>
        )}
      </div>

      {!group.ok && (
        <Banner tone="warn" title="这一组没算成">
          {group.reason}
        </Banner>
      )}

      {group.ok && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="时间加权收益"
              value={<span className={TONE_CLASS[twr.tone]}>{twr.text}</span>}
              note={group.twr_annualized === null ? '不满一年，不给年化' : `年化 ${annualized.text}`}
            />
            <MetricCard
              label="资金加权收益"
              value={<span className={TONE_CLASS[mwr.tone]}>{mwr.text}</span>}
              note={group.xirr === null ? '不满一年，不给年化' : `年化（XIRR）${xirr.text}`}
            />
            <MetricCard
              label="区间盈亏"
              value={<PrivacyText className={TONE_CLASS[toneOf(group.gain)]} text={signedMoney(group.gain)} />}
              note="期末市值 + 卖出所得 + 分红 − 投入"
            />
            <MetricCard
              label="期末市值"
              value={<PrivacyText text={moneyText(group.end_value)} />}
              note={
                <>
                  投入 <PrivacyText text={moneyText(group.invested)} />
                </>
              }
            />
            <MetricCard
              label="卖出所得"
              value={<PrivacyText text={moneyText(group.proceeds)} />}
              note="已扣卖出费用"
            />
            <MetricCard label="现金分红" value={<PrivacyText text={moneyText(group.dividends)} />} />
            <MetricCard
              label="费用合计"
              value={<PrivacyText text={moneyText(group.fees)} />}
              note="买入费用算进投入，卖出费用从所得里扣"
            />
          </div>

          {option ? (
            <div>
              <p className="mb-1 text-caption font-medium text-text-secondary">时间加权累计收益</p>
              <EChart
                option={option}
                height={200}
                ariaLabel={`${group.label}时间加权累计收益走势，${group.start ?? MISSING} 至 ${group.end ?? MISSING}`}
              />
            </div>
          ) : (
            <p className="text-caption text-text-muted">本地日线不足 2 个交易日，不画净值走势。</p>
          )}
        </>
      )}

      {group.warnings.length > 0 && (
        <ul role="status" className="flex flex-col gap-1 rounded border border-warn bg-warn-soft px-3 py-2 text-caption text-text-secondary">
          {group.warnings.map((warning) => (
            <li key={warning} className="flex gap-1.5">
              <AlertTriangle className="mt-half size-3.5 shrink-0 text-warn" aria-hidden="true" />
              {warning}
            </li>
          ))}
        </ul>
      )}

      {group.excluded.length > 0 && (
        <div className="text-caption text-text-secondary">
          <p className="font-medium text-text">没算进来</p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {group.excluded.map((row) => (
              <li key={row.symbol}>
                <span className="ldst-num">{row.name ? `${row.name} ${row.symbol}` : row.symbol}</span>：{row.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-col gap-0.5 text-caption leading-body text-text-muted">
        {group.notes.map((note) => (
          <p key={note}>{note}</p>
        ))}
      </div>
    </section>
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

/** 累计收益走势：只画百分比（金额在隐私模式下要遮），缺失点用 `-` 占位。 */
function perfOption(group: PortfolioPerfGroup, palette: ChartPalette) {
  const series = group.series
  if (!series || series.dates.length < 2) return null
  return {
    animation: false,
    grid: { left: 8, right: 32, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.axis,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: series.dates,
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
        name: '时间加权累计收益',
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 1.6, color: palette.line },
        itemStyle: { color: palette.line },
        data: series.twr_index.map((value) => (value === null ? '-' : value - 1)),
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
