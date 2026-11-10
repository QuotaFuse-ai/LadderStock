/**
 * 运行卡 → 页面展示的纯函数（QUOT-534）。
 *
 * **红线**：这里只做「读值 + 单位显示」，不重算任何指标、收益或曲线。
 * 主指标一律直接取运行卡的 `metrics_display`；成本三档与配置块没有配套的展示串，
 * 才由这里把后端原值换成百分数 / 金额文本（`×100` 只是小数 → 百分数的单位换算，
 * 不是重新计算收益率）。曲线直接用 `equity_curve` / `benchmark.curve` 的原始点。
 */

import type { EChartsCoreOption } from 'echarts/core'

import type { ChartPalette } from '../../charts/cssVar'
import { formatAmount, isFiniteNumber, MISSING } from '../../lib/format'
import type {
  BacktestConfigValue,
  BacktestGate,
  BacktestRunCard,
  BacktestSegment,
} from '../../api/backtest'

export type MetricKey =
  | 'cumulative_return'
  | 'annualized_return'
  | 'max_drawdown'
  | 'sharpe'
  | 'calmar'
  | 'win_rate'
  | 'profit_loss_ratio'
  | 'trade_count'
  | 'avg_turnover'
  | 'avg_holding_days'
  | 'exposure_ratio'

export interface MetricMeta {
  key: MetricKey
  label: string
}

/** 运行卡契约的 11 个指标，顺序固定；数值与展示串都由后端给。 */
export const METRIC_META: readonly MetricMeta[] = [
  { key: 'cumulative_return', label: '累计收益' },
  { key: 'annualized_return', label: '年化收益' },
  { key: 'max_drawdown', label: '最大回撤' },
  { key: 'sharpe', label: '夏普' },
  { key: 'calmar', label: '卡玛' },
  { key: 'win_rate', label: '胜率' },
  { key: 'profit_loss_ratio', label: '盈亏比' },
  { key: 'trade_count', label: '完成回合' },
  { key: 'avg_turnover', label: '平均换手' },
  { key: 'avg_holding_days', label: '平均持仓' },
  { key: 'exposure_ratio', label: '持仓暴露' },
]

/** 分段表只挑最能解释校验门的六项，其余仍可从 JSON 下载查看。 */
export const SEGMENT_METRICS: readonly MetricKey[] = [
  'cumulative_return',
  'annualized_return',
  'max_drawdown',
  'sharpe',
  'win_rate',
  'trade_count',
]

const CONFIG_LABELS: Record<string, string> = {
  commission: '佣金率',
  slippage: '滑点',
  stamp_duty: '印花税',
  min_trades: '最小交易笔数',
  in_sample_ratio: '样本内占比',
  cost_tiers: '成本档位',
}

export function metricDisplay(card: BacktestRunCard, key: MetricKey): string {
  return card.metrics_display[key] ?? MISSING
}

export function segmentMetricDisplay(segment: BacktestSegment, key: MetricKey): string {
  return segment.metrics_display[key] ?? MISSING
}

/** 小数 → 百分数文本（`0.2338` → `23.38%`）。仅用于运行卡没有展示串的字段。 */
export function percentText(value: number | null, digits = 2): string {
  if (!isFiniteNumber(value)) return MISSING
  return `${(value * 100).toFixed(digits)}%`
}

export function ratioText(value: number | null, digits = 2): string {
  if (!isFiniteNumber(value)) return MISSING
  return value.toFixed(digits)
}

export function countText(value: number | null): string {
  if (!isFiniteNumber(value)) return MISSING
  return String(value)
}

export function amountText(value: number | null): string {
  return isFiniteNumber(value) ? formatAmount(value) : MISSING
}

/**
 * 佣金档位的人类可读标签：`0.0002` → `万2`，`0.001` → `千1`。
 * 只是同一比率的换单位写法，不改数值；悬浮时仍给出原值。
 */
export function commissionTierLabel(rate: number | null): string {
  if (!isFiniteNumber(rate)) return MISSING
  const wan = rate * 10000
  const trim = (value: number): string =>
    Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
  return wan >= 10 ? `千${trim(wan / 10)}` : `万${trim(wan)}`
}

export function configLabel(key: string): string {
  return CONFIG_LABELS[key] ?? key
}

export function configValueText(value: BacktestConfigValue): string {
  if (value === null) return MISSING
  if (Array.isArray(value)) return value.map((item) => String(item)).join(' / ')
  return String(value)
}

export function gateStatusText(gate: BacktestGate): string {
  return gate.passed ? '通过' : '未通过'
}

export function gatePassedCount(gate: BacktestGate): string {
  const passed = gate.checks.filter((check) => check.status === 'pass').length
  return `${passed}/${gate.checks.length}`
}

function alignedBenchmark(card: BacktestRunCard): Array<{ date: string; equity: number }> | null {
  const strategy = card.equity_curve
  const benchmark = card.benchmark.curve
  if (strategy.length === 0 || benchmark.length !== strategy.length) return null
  for (let index = 0; index < strategy.length; index += 1) {
    if (strategy[index].date !== benchmark[index].date) return null
  }
  return benchmark
}

/** 权益曲线：只把运行卡已有的点搬上画布，不做累计 / 归一 / 插值。 */
export function buildEquityOption(card: BacktestRunCard, palette: ChartPalette): EChartsCoreOption | null {
  const points = card.equity_curve
  if (points.length === 0) return null
  const dates = points.map((point) => point.date)
  const benchmark = alignedBenchmark(card)
  const compact = points.length > 120
  const labelInterval = Math.max(0, Math.floor(points.length / 6) - 1)

  const series: Array<Record<string, unknown>> = [
    {
      name: '策略权益',
      type: 'line',
      showSymbol: false,
      sampling: 'lttb',
      data: points.map((point) => point.equity),
      lineStyle: { width: 2, color: palette.line },
      itemStyle: { color: palette.line },
      areaStyle: { color: palette.band },
    },
  ]
  if (benchmark) {
    series.push({
      name: '基准（买入持有）',
      type: 'line',
      showSymbol: false,
      sampling: 'lttb',
      data: benchmark.map((point) => point.equity),
      lineStyle: { width: 1.5, color: palette.series2 },
      itemStyle: { color: palette.series2 },
    })
  }

  return {
    animation: false,
    grid: { left: 12, right: 20, top: benchmark ? 40 : 16, bottom: compact ? 60 : 28, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBg,
      borderWidth: 0,
      textStyle: { color: palette.tooltipText },
    },
    legend: benchmark
      ? {
          top: 0,
          right: 0,
          itemWidth: 12,
          itemHeight: 8,
          textStyle: { color: palette.text },
          data: ['策略权益', '基准（买入持有）'],
        }
      : undefined,
    dataZoom: compact
      ? [
          { type: 'inside', start: 55, end: 100 },
          { type: 'slider', height: 16, bottom: 8, borderColor: palette.grid, textStyle: { color: palette.text } },
        ]
      : undefined,
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates,
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.axis, interval: labelInterval, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: palette.axis, formatter: (value: number) => formatAmount(value) },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series,
  }
}

/** 曲线等价文本：给读屏与截图核对用，数值全部来自运行卡原始点。 */
export function equityAriaLabel(card: BacktestRunCard): string {
  const first = card.equity_curve[0]
  const last = card.equity_curve[card.equity_curve.length - 1]
  if (!first || !last) return '权益曲线：运行卡没有可绘制的序列点'
  const benchmark = card.benchmark.curve[card.benchmark.curve.length - 1]
  const benchmarkText = benchmark ? `，基准期末权益 ${amountText(benchmark.equity)}` : ''
  return `权益曲线：${first.date} 至 ${last.date}，共 ${card.equity_curve.length} 个交易日；策略期末权益 ${amountText(last.equity)}${benchmarkText}`
}
