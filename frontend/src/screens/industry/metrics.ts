import { MISSING, formatMoney } from '../../lib/format'
import type { IndustryDimension, IndustryDimensions } from '../../api/industry'

/**
 * 五维指标的展示规格（R9）。
 *
 * 键名与 R5 `metric_paths()` 完全一致；这里只负责「怎么显示」，
 * 不做换算（分位 0–1 → 百分比属于单位展示，不改变数值本身）。
 *
 * `pct` 是「水平值」（ROE、成交额占比、换手率），不上涨跌色；
 * `pctSigned` 只给真正的变化率（同比、涨跌幅、净流入占比），红涨绿跌。
 */

export type MetricKind = 'ratioPct' | 'pct' | 'pctSigned' | 'money' | 'multiple' | 'count'

export interface MetricSpec {
  path: string
  /** 表头 / 仪表标签 */
  label: string
  kind: MetricKind
  /** 该指标高值代表「拥挤 / 昂贵」时，仪表文案提示方向；不参与计算 */
  highMeans?: string
}

export const DIMENSION_METRICS: Record<IndustryDimension, MetricSpec[]> = {
  prosperity: [
    { path: 'prosperity.revenue_yoy_median', label: '营收同比', kind: 'pctSigned' },
    { path: 'prosperity.profit_yoy_median', label: '净利同比', kind: 'pctSigned' },
    { path: 'prosperity.roe_median', label: 'ROE', kind: 'pct' },
  ],
  capital: [
    { path: 'capital.constituents.main_net_inflow_nd', label: '主力净流入（申万）', kind: 'money' },
    { path: 'capital.constituents.main_net_inflow_nd_sampled', label: '已取样本合计', kind: 'money' },
    { path: 'capital.board.main_net_inflow', label: '板块净流入', kind: 'money' },
    { path: 'capital.board.main_net_pct', label: '板块净流入占比', kind: 'pctSigned' },
    { path: 'capital.board.change_pct', label: '板块涨跌幅', kind: 'pctSigned' },
  ],
  valuation: [
    { path: 'valuation.pe_median', label: 'PE 中位数', kind: 'multiple' },
    { path: 'valuation.pe_percentile', label: 'PE 分位', kind: 'ratioPct', highMeans: '越高越贵' },
    { path: 'valuation.pb_median', label: 'PB 中位数', kind: 'multiple' },
    { path: 'valuation.pb_percentile', label: 'PB 分位', kind: 'ratioPct', highMeans: '越高越贵' },
  ],
  momentum: [
    { path: 'momentum.change_pct_5d', label: '5 日涨跌（等权）', kind: 'pctSigned' },
    { path: 'momentum.change_pct_20d', label: '20 日涨跌（等权）', kind: 'pctSigned' },
    { path: 'momentum.amount_share_pct', label: '成交额占比', kind: 'pct' },
  ],
  crowding: [
    { path: 'crowding.turnover_median', label: '换手率', kind: 'pct' },
    { path: 'crowding.turnover_percentile', label: '换手分位', kind: 'ratioPct', highMeans: '越高越拥挤' },
    { path: 'crowding.fund_concentration_pct', label: '资金集中度', kind: 'pct' },
  ],
}

export interface FormattedMetric {
  text: string
  /** up/down 仅用于带方向的数字（A 股口径红涨绿跌） */
  tone: 'up' | 'down' | 'flat'
  missing: boolean
}

const TWO_DECIMAL = { minimumFractionDigits: 2, maximumFractionDigits: 2 } as const
const ONE_DECIMAL = { minimumFractionDigits: 1, maximumFractionDigits: 1 } as const

/** 与全站一致的数值格式：缺失 `—`，不补 0。 */
export function formatMetric(spec: MetricSpec, value: number | null | undefined): FormattedMetric {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return { text: MISSING, tone: 'flat', missing: true }
  }
  if (spec.kind === 'ratioPct') {
    return { text: `${(value * 100).toFixed(1)}%`, tone: 'flat', missing: false }
  }
  if (spec.kind === 'pct') {
    return { text: `${value.toFixed(2)}%`, tone: 'flat', missing: false }
  }
  if (spec.kind === 'pctSigned') {
    const rounded = Number(value.toFixed(2))
    const tone = rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat'
    const sign = rounded > 0 ? '+' : ''
    return { text: `${sign}${rounded.toFixed(2)}%`, tone, missing: false }
  }
  if (spec.kind === 'money') {
    return { text: formatMoney(value), tone: value > 0 ? 'up' : value < 0 ? 'down' : 'flat', missing: false }
  }
  if (spec.kind === 'multiple') {
    return { text: `${value.toLocaleString('zh-CN', TWO_DECIMAL)} 倍`, tone: 'flat', missing: false }
  }
  return { text: value.toLocaleString('zh-CN', ONE_DECIMAL), tone: 'flat', missing: false }
}

/** 矩阵单元格用的紧凑文本：金额保留单位、其它两位。 */
export function compactMetric(spec: MetricSpec, value: number | null): string {
  if (value === null) return '无数据'
  const formatted = formatMetric(spec, value)
  return formatted.text
}

/** 详情页分位条：只接受 0–1 的比率；越界或缺失返回 null（不夹取伪造）。 */
export function percentileValue(value: number | null): number | null {
  if (value === null || !Number.isFinite(value) || value < 0 || value > 1) return null
  return value
}

/** 可空数值排序：缺失恒排在有数之后（升序时）。 */
export function compareMetric(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return a - b
}

/** 详情的五个仪表各用一个「主指标」；其余进卡内明细。 */
export const DIMENSION_HEADLINE: Record<IndustryDimension, string> = {
  prosperity: 'prosperity.revenue_yoy_median',
  capital: 'capital.constituents.main_net_inflow_nd',
  valuation: 'valuation.pe_percentile',
  momentum: 'momentum.change_pct_20d',
  crowding: 'crowding.turnover_percentile',
}

/** 把五维块摊平成「指标路径 → 数值」，与清单投影同一套键。 */
export function dimensionValues(
  dimension: IndustryDimension,
  dimensions: IndustryDimensions,
): Record<string, number | null> {
  const out: Record<string, number | null> = {}
  for (const spec of DIMENSION_METRICS[dimension]) out[spec.path] = null
  if (dimension === 'prosperity') {
    const block = dimensions.prosperity
    out['prosperity.revenue_yoy_median'] = block.revenue_yoy_median
    out['prosperity.profit_yoy_median'] = block.profit_yoy_median
    out['prosperity.roe_median'] = block.roe_median
    return out
  }
  if (dimension === 'capital') {
    const block = dimensions.capital
    out['capital.constituents.main_net_inflow_nd'] = block.constituents.main_net_inflow_nd
    out['capital.constituents.main_net_inflow_nd_sampled'] = block.constituents.main_net_inflow_nd_sampled
    out['capital.board.main_net_inflow'] = block.board.main_net_inflow
    out['capital.board.main_net_pct'] = block.board.main_net_pct
    out['capital.board.change_pct'] = block.board.change_pct
    return out
  }
  if (dimension === 'valuation') {
    const block = dimensions.valuation
    out['valuation.pe_median'] = block.pe_median
    out['valuation.pe_percentile'] = block.pe_percentile
    out['valuation.pb_median'] = block.pb_median
    out['valuation.pb_percentile'] = block.pb_percentile
    return out
  }
  if (dimension === 'momentum') {
    const block = dimensions.momentum
    out['momentum.change_pct_5d'] = block.change_pct_5d
    out['momentum.change_pct_20d'] = block.change_pct_20d
    out['momentum.amount_share_pct'] = block.amount_share_pct
    return out
  }
  const block = dimensions.crowding
  out['crowding.turnover_median'] = block.turnover_median
  out['crowding.turnover_percentile'] = block.turnover_percentile
  out['crowding.fund_concentration_pct'] = block.fund_concentration_pct
  return out
}

/** 信号证据值的展示：契约里 `unit` 是原始单位（`ratio` / `%` / `元`）。 */
export function formatEvidenceValue(value: number, unit: string | null): string {
  if (unit === 'ratio') return `${(value * 100).toFixed(1)}%`
  if (unit === '元') return formatMoney(value)
  if (unit === '%') return `${value.toFixed(2)}%`
  return value.toLocaleString('zh-CN', TWO_DECIMAL)
}
