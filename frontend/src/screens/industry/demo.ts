/**
 * R9 页面演示数据（`?demo=1` 显式开启，页面带「演示数据」水印）。
 *
 * 形状与 R5 `industry_snapshot` / R9 信号契约逐键对齐：五维指标路径、
 * `dimensions_available`、`provenance`、历史分位序列、成分股、相关资讯都在。
 * 用途只有两个：后端接口上线前验证页面结构与五态；截图取证。
 *
 * **绝不在真实通道里回落到这份数据**；演示信号的规则判定也只在这里（夹具生成），
 * 生产判定由后端 `/api/signals` 负责（DEC-028）。
 */

import {
  DIMENSION_METRIC_PATHS,
  INDUSTRY_DIMENSIONS,
  type ConstituentRow,
  type DataSourceStatus,
  type DatasetStatus,
  type HistoryPoint,
  type IndustryDetailPayload,
  type IndustryDimension,
  type IndustryListPayload,
  type IndustryProvenance,
  type IndustrySignal,
  type IndustrySummaryRow,
  type IndustryWindows,
  type SignalsPayload,
  type SignalRule,
} from '../../api/industry'

export const DEMO_DATE = '2026-10-31'

export const DEMO_AS_OF: Record<IndustryDimension, string> = {
  prosperity: '2026-09-30',
  capital: '2026-10-30',
  valuation: '2026-10-30',
  momentum: '2026-10-30',
  crowding: '2026-10-30',
}

export type DemoVariant = 'full' | 'partial' | 'empty' | 'all-null'

interface DemoIndustry {
  name: string
  l1_code: string
  bound: boolean
  constituent_count: number
  status: DataSourceStatus
  metrics: Record<string, number | null>
  reasons?: Partial<Record<IndustryDimension, string>>
  membership_reason?: string
}

function values(entries: Record<string, number | null>): Record<string, number | null> {
  // 显式铺满五维承诺字段：没给的一律 null（与 R5 schema 一个键都不省）
  const out: Record<string, number | null> = {}
  for (const name of INDUSTRY_DIMENSIONS) {
    for (const path of DIMENSION_METRIC_PATHS[name]) out[path] = entries[path] ?? null
  }
  return out
}

/** 13 个完整行业（normal 演示）；金额单位：元。 */
const COMPLETE: DemoIndustry[] = [
  {
    name: '食品饮料', l1_code: '340000', bound: true, constituent_count: 118, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 6.8,
      'prosperity.profit_yoy_median': 9.2,
      'prosperity.roe_median': 12.4,
      'capital.constituents.main_net_inflow_nd': 1_830_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 1_830_000_000,
      'valuation.pe_median': 22.1,
      'valuation.pe_percentile': 0.18,
      'valuation.pb_median': 3.2,
      'valuation.pb_percentile': 0.21,
      'momentum.change_pct_5d': 1.6,
      'momentum.change_pct_20d': 4.2,
      'momentum.amount_share_pct': 7.4,
      'crowding.turnover_median': 1.8,
      'crowding.turnover_percentile': 0.44,
      'crowding.fund_concentration_pct': 9.6,
    }),
  },
  {
    name: '电子', l1_code: '270000', bound: true, constituent_count: 286, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 18.5,
      'prosperity.profit_yoy_median': 24.1,
      'prosperity.roe_median': 8.9,
      'capital.constituents.main_net_inflow_nd': 2_610_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 2_610_000_000,
      'valuation.pe_median': 41.2,
      'valuation.pe_percentile': 0.72,
      'valuation.pb_median': 4.6,
      'valuation.pb_percentile': 0.68,
      'momentum.change_pct_5d': 3.4,
      'momentum.change_pct_20d': 9.8,
      'momentum.amount_share_pct': 12.1,
      'crowding.turnover_median': 3.4,
      'crowding.turnover_percentile': 0.86,
      'crowding.fund_concentration_pct': 28.4,
    }),
  },
  {
    name: '医药生物', l1_code: '370000', bound: true, constituent_count: 402, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 9.1,
      'prosperity.profit_yoy_median': 12.3,
      'prosperity.roe_median': 10.2,
      'capital.constituents.main_net_inflow_nd': 940_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 940_000_000,
      'valuation.pe_median': 28.4,
      'valuation.pe_percentile': 0.27,
      'valuation.pb_median': 3.9,
      'valuation.pb_percentile': 0.32,
      'momentum.change_pct_5d': 0.9,
      'momentum.change_pct_20d': 2.1,
      'momentum.amount_share_pct': 8.2,
      'crowding.turnover_median': 2.1,
      'crowding.turnover_percentile': 0.51,
      'crowding.fund_concentration_pct': 11.2,
    }),
  },
  {
    name: '银行', l1_code: '480000', bound: true, constituent_count: 42, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 2.4,
      'prosperity.profit_yoy_median': 5.1,
      'prosperity.roe_median': 9.8,
      'capital.constituents.main_net_inflow_nd': 1_210_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 1_210_000_000,
      'valuation.pe_median': 5.9,
      'valuation.pe_percentile': 0.12,
      'valuation.pb_median': 0.62,
      'valuation.pb_percentile': 0.16,
      'momentum.change_pct_5d': 0.4,
      'momentum.change_pct_20d': 1.2,
      'momentum.amount_share_pct': 3.1,
      'crowding.turnover_median': 0.6,
      'crowding.turnover_percentile': 0.22,
      'crowding.fund_concentration_pct': 14.8,
    }),
  },
  {
    name: '电力设备', l1_code: '630000', bound: true, constituent_count: 331, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 14.2,
      'prosperity.profit_yoy_median': 11.5,
      'prosperity.roe_median': 11.1,
      'capital.constituents.main_net_inflow_nd': -620_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': -620_000_000,
      'valuation.pe_median': 26.8,
      'valuation.pe_percentile': 0.38,
      'valuation.pb_median': 3.1,
      'valuation.pb_percentile': 0.42,
      'momentum.change_pct_5d': -1.1,
      'momentum.change_pct_20d': -3.6,
      'momentum.amount_share_pct': 11.8,
      'crowding.turnover_median': 2.4,
      'crowding.turnover_percentile': 0.63,
      'crowding.fund_concentration_pct': 19.7,
    }),
  },
  {
    name: '有色金属', l1_code: '240000', bound: true, constituent_count: 132, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 11.7,
      'prosperity.profit_yoy_median': 18.4,
      'prosperity.roe_median': 13.6,
      'capital.constituents.main_net_inflow_nd': 1_520_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 1_520_000_000,
      'valuation.pe_median': 18.9,
      'valuation.pe_percentile': 0.46,
      'valuation.pb_median': 2.8,
      'valuation.pb_percentile': 0.52,
      'momentum.change_pct_5d': 2.2,
      'momentum.change_pct_20d': 7.5,
      'momentum.amount_share_pct': 9.3,
      'crowding.turnover_median': 2.6,
      'crowding.turnover_percentile': 0.64,
      'crowding.fund_concentration_pct': 17.4,
    }),
  },
  {
    name: '计算机', l1_code: '710000', bound: true, constituent_count: 336, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 5.3,
      'prosperity.profit_yoy_median': -2.1,
      'prosperity.roe_median': 3.2,
      'capital.constituents.main_net_inflow_nd': -1_100_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': -1_100_000_000,
      'valuation.pe_median': 58.6,
      'valuation.pe_percentile': 0.81,
      'valuation.pb_median': 5.4,
      'valuation.pb_percentile': 0.77,
      'momentum.change_pct_5d': -0.6,
      'momentum.change_pct_20d': 2.4,
      'momentum.amount_share_pct': 10.4,
      'crowding.turnover_median': 3.8,
      'crowding.turnover_percentile': 0.91,
      'crowding.fund_concentration_pct': 31.2,
    }),
  },
  {
    name: '汽车', l1_code: '280000', bound: true, constituent_count: 261, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 8.9,
      'prosperity.profit_yoy_median': 6.7,
      'prosperity.roe_median': 9.4,
      'capital.constituents.main_net_inflow_nd': 480_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 480_000_000,
      'valuation.pe_median': 23.5,
      'valuation.pe_percentile': 0.34,
      'valuation.pb_median': 2.6,
      'valuation.pb_percentile': 0.39,
      'momentum.change_pct_5d': 1.2,
      'momentum.change_pct_20d': 3.1,
      'momentum.amount_share_pct': 8.8,
      'crowding.turnover_median': 2.0,
      'crowding.turnover_percentile': 0.48,
      'crowding.fund_concentration_pct': 12.9,
    }),
  },
  {
    name: '机械设备', l1_code: '640000', bound: true, constituent_count: 528, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 7.2,
      'prosperity.profit_yoy_median': 4.9,
      'prosperity.roe_median': 8.1,
      'capital.constituents.main_net_inflow_nd': 310_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 310_000_000,
      'valuation.pe_median': 25.7,
      'valuation.pe_percentile': 0.41,
      'valuation.pb_median': 2.9,
      'valuation.pb_percentile': 0.45,
      'momentum.change_pct_5d': 0.8,
      'momentum.change_pct_20d': 1.9,
      'momentum.amount_share_pct': 6.7,
      'crowding.turnover_median': 1.9,
      'crowding.turnover_percentile': 0.46,
      'crowding.fund_concentration_pct': 10.5,
    }),
  },
  {
    name: '家用电器', l1_code: '330000', bound: true, constituent_count: 88, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 5.9,
      'prosperity.profit_yoy_median': 8.8,
      'prosperity.roe_median': 14.2,
      'capital.constituents.main_net_inflow_nd': 2_210_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 2_210_000_000,
      'valuation.pe_median': 14.8,
      'valuation.pe_percentile': 0.24,
      'valuation.pb_median': 2.4,
      'valuation.pb_percentile': 0.28,
      'momentum.change_pct_5d': 1.0,
      'momentum.change_pct_20d': 2.8,
      'momentum.amount_share_pct': 5.2,
      'crowding.turnover_median': 1.4,
      'crowding.turnover_percentile': 0.36,
      'crowding.fund_concentration_pct': 8.1,
    }),
  },
  {
    name: '煤炭', l1_code: '740000', bound: true, constituent_count: 38, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 3.1,
      'prosperity.profit_yoy_median': -1.8,
      'prosperity.roe_median': 7.2,
      'capital.constituents.main_net_inflow_nd': 560_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 560_000_000,
      'valuation.pe_median': 9.4,
      'valuation.pe_percentile': 0.15,
      'valuation.pb_median': 1.3,
      'valuation.pb_percentile': 0.19,
      'momentum.change_pct_5d': 0.3,
      'momentum.change_pct_20d': -1.4,
      'momentum.amount_share_pct': 2.9,
      'crowding.turnover_median': 1.0,
      'crowding.turnover_percentile': 0.28,
      'crowding.fund_concentration_pct': 6.7,
    }),
  },
  {
    name: '传媒', l1_code: '720000', bound: true, constituent_count: 148, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 6.4,
      'prosperity.profit_yoy_median': 7.7,
      'prosperity.roe_median': 6.8,
      'capital.constituents.main_net_inflow_nd': 390_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 390_000_000,
      'valuation.pe_median': 32.1,
      'valuation.pe_percentile': 0.58,
      'valuation.pb_median': 3.4,
      'valuation.pb_percentile': 0.61,
      'momentum.change_pct_5d': 2.8,
      'momentum.change_pct_20d': 8.1,
      'momentum.amount_share_pct': 4.6,
      'crowding.turnover_median': 3.2,
      'crowding.turnover_percentile': 0.83,
      'crowding.fund_concentration_pct': 22.6,
    }),
  },
  {
    name: '通信', l1_code: '730000', bound: true, constituent_count: 126, status: 'ok',
    metrics: values({
      'prosperity.revenue_yoy_median': 10.8,
      'prosperity.profit_yoy_median': 15.2,
      'prosperity.roe_median': 8.4,
      'capital.constituents.main_net_inflow_nd': 870_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': 870_000_000,
      'valuation.pe_median': 27.3,
      'valuation.pe_percentile': 0.39,
      'valuation.pb_median': 2.7,
      'valuation.pb_percentile': 0.43,
      'momentum.change_pct_5d': 2.1,
      'momentum.change_pct_20d': 6.4,
      'momentum.amount_share_pct': 5.8,
      'crowding.turnover_median': 2.2,
      'crowding.turnover_percentile': 0.55,
      'crowding.fund_concentration_pct': 12.4,
    }),
  },
]

/** partial 演示：房地产缺景气/估值缓存、综合未绑定申万代码。 */
const PARTIAL_EXTRA: DemoIndustry[] = [
  {
    name: '房地产', l1_code: '430000', bound: true, constituent_count: 96, status: 'partial',
    metrics: values({
      'capital.constituents.main_net_inflow_nd': -340_000_000,
      'capital.constituents.main_net_inflow_nd_sampled': -340_000_000,
      'momentum.change_pct_5d': -0.9,
      'momentum.change_pct_20d': -2.2,
      'crowding.turnover_median': 1.1,
      'crowding.turnover_percentile': 0.31,
      'crowding.fund_concentration_pct': 7.4,
    }),
    reasons: {
      prosperity: '本地没有房地产成分股的财务指标缓存（finance_indicators 为空）',
      valuation: '本地没有房地产成分股的估值缓存（valuation_history 为空）',
    },
  },
  {
    name: '综合', l1_code: '', bound: false, constituent_count: 0, status: 'missing',
    metrics: values({}),
    membership_reason:
      'config/industry.yaml 未绑定申万一级代码（锚点实测归属不一致：600816→490000、600654→710000），成分派生维度全部显式 null，不猜码',
    reasons: {
      prosperity: '未绑定申万一级代码，无成分集合',
      capital: '未绑定申万一级代码，无成分集合',
      valuation: '未绑定申万一级代码，无成分集合',
      momentum: '未绑定申万一级代码，无成分集合',
      crowding: '未绑定申万一级代码，无成分集合',
    },
  },
]

const DEMO_WINDOWS: IndustryWindows = {
  fund_days: 5,
  valuation_lookback_days: 250,
  turnover_lookback_days: 60,
  momentum_windows: [5, 20],
}

function pool(variant: DemoVariant): DemoIndustry[] {
  if (variant === 'partial') return [...COMPLETE, ...PARTIAL_EXTRA]
  return COMPLETE
}

function dimensionsAvailable(metrics: Record<string, number | null>): Record<IndustryDimension, boolean> {
  const out = {} as Record<IndustryDimension, boolean>
  for (const name of INDUSTRY_DIMENSIONS) {
    out[name] = DIMENSION_METRIC_PATHS[name].some((path) => metrics[path] !== null)
  }
  return out
}

function buildRow(entry: DemoIndustry): IndustrySummaryRow {
  const available = Object.values(entry.metrics).some((value) => value !== null)
  return {
    name: entry.name,
    l1_code: entry.l1_code || null,
    bound: entry.bound,
    enabled: true,
    available,
    constituent_count: entry.constituent_count,
    data_source_status: entry.status,
    reason: entry.membership_reason ?? null,
    dimensions_available: dimensionsAvailable(entry.metrics),
    metrics: entry.metrics,
    as_of: { ...DEMO_AS_OF },
  }
}

export function demoIndustryList(variant: DemoVariant = 'full'): IndustryListPayload {
  if (variant === 'empty') {
    return {
      available: false,
      caliber: 'sw_l1',
      windows: DEMO_WINDOWS,
      count: 0,
      as_of: { ...DEMO_AS_OF },
      industries: [],
      datasets: demoDatasets(false),
      source_detail: '演示数据：六个输入数据集在本机缓存里都没有可用记录（零外呼）',
      reason: '演示数据：六个输入数据集在本机缓存里都没有可用记录，指标全部显式 null',
      data_as_of: null,
    }
  }
  const industries = pool(variant).map(buildRow)
  return {
    available: industries.some((row) => row.available),
    caliber: 'sw_l1',
    windows: DEMO_WINDOWS,
    count: industries.length,
    as_of: { ...DEMO_AS_OF },
    industries,
    datasets: demoDatasets(true),
    source_detail: '演示数据：本地缓存回放（sw_industry_map / finance_indicators / valuation_history / fund_flow / industry_fund_flow / daily_k）',
    reason: null,
    data_as_of: '2025-12-01',
  }
}

// ---------------------------------------------------------------- 单行业

const DEMO_SOURCES = [
  {
    id: 'ds:sw_industry_map',
    dataset: 'sw_industry_map',
    params: { windows: DEMO_WINDOWS, as_of: '2025-12-01' },
    retrieved_at: '2026-10-31T06:20:00+08:00',
  },
  {
    id: 'ds:finance_indicators',
    dataset: 'finance_indicators',
    params: { windows: DEMO_WINDOWS, as_of: '2026-09-30' },
    retrieved_at: '2026-10-31T06:21:00+08:00',
  },
  {
    id: 'ds:valuation_history',
    dataset: 'valuation_history',
    params: { windows: DEMO_WINDOWS, as_of: '2026-10-30' },
    retrieved_at: '2026-10-31T06:22:00+08:00',
  },
  {
    id: 'ds:fund_flow',
    dataset: 'fund_flow',
    params: { windows: DEMO_WINDOWS, as_of: '2026-10-30' },
    retrieved_at: '2026-10-31T06:23:00+08:00',
  },
  {
    id: 'ds:daily_k',
    dataset: 'daily_k',
    params: { windows: DEMO_WINDOWS, as_of: '2026-10-30' },
    retrieved_at: '2026-10-31T06:24:00+08:00',
  },
]

function demoDatasets(populated: boolean): Record<string, DatasetStatus> {
  const rows: Array<[string, string, number]> = [
    ['sw_industry_map', '2025-12-01', 5132],
    ['finance_indicators', '2026-09-30', 4830],
    ['valuation_history', '2026-10-30', 6120],
    ['fund_flow', '2026-10-30', 590],
    ['industry_fund_flow', '2026-10-30', 31],
    ['daily_k', '2026-10-30', 5980],
  ]
  return Object.fromEntries(
    rows.map(([dataset, asOf, records]) => [
      dataset,
      {
        records: populated ? records : 0,
        as_of: populated ? asOf : null,
        retrieved_at: populated ? '2026-10-31T06:20:00+08:00' : null,
        error: null,
      },
    ]),
  )
}

function demoProvenance(metrics: Record<string, number | null>): IndustryProvenance {
  const mapping: Record<string, string> = {
    'prosperity.': 'ds:finance_indicators',
    'capital.constituents.': 'ds:fund_flow',
    'capital.board.': 'ds:industry_fund_flow',
    'valuation.': 'ds:valuation_history',
    'crowding.turnover': 'ds:valuation_history',
    'crowding.': 'ds:fund_flow',
    'momentum.': 'ds:daily_k',
  }
  const values: IndustryProvenance['values'] = {}
  for (const [path, value] of Object.entries(metrics)) {
    const source = Object.entries(mapping).find(([prefix]) => path.startsWith(prefix))?.[1]
    values[path] = {
      sources: value !== null && source ? [source] : [],
      unverified: value === null || !source,
    }
  }
  return {
    values,
    sources: DEMO_SOURCES.filter((source) =>
      Object.values(values).some((entry) => entry.sources.includes(source.id)),
    ),
    unverified: Object.values(values).some((entry) => entry.unverified),
  }
}

/** 以当期值结尾的确定性序列（演示用，不参与真实口径）。 */
function series(end: number, points = 12, amplitude = 0.06): HistoryPoint[] {
  const start = new Date(Date.UTC(2026, 9, 15))
  const out: HistoryPoint[] = []
  for (let index = 0; index < points; index += 1) {
    const date = new Date(start.getTime() + index * 86_400_000)
    const wave = Math.sin(index / 2.4) * amplitude
    const drift = (index / (points - 1) - 1) * amplitude
    const value = Math.min(1, Math.max(0, end + wave + drift))
    out.push({ date: date.toISOString().slice(0, 10), value: Number(value.toFixed(4)) })
  }
  out[out.length - 1] = { date: '2026-10-30', value: end }
  return out
}

const FOOD_CONSTITUENTS: ConstituentRow[] = [
  { code: '600519', name: '贵州茅台', pe_ttm: 21.4, pb: 7.8, turnover_rate: 0.32, revenue_yoy: 8.1, profit_yoy: 9.6, main_net_nd: 620_000_000, as_of: '2026-10-30' },
  { code: '000858', name: '五粮液', pe_ttm: 18.2, pb: 4.1, turnover_rate: 0.61, revenue_yoy: 6.4, profit_yoy: 7.2, main_net_nd: 310_000_000, as_of: '2026-10-30' },
  { code: '000568', name: '泸州老窖', pe_ttm: 16.8, pb: 3.9, turnover_rate: 0.74, revenue_yoy: 5.2, profit_yoy: 6.8, main_net_nd: 180_000_000, as_of: '2026-10-30' },
  { code: '600809', name: '山西汾酒', pe_ttm: 24.6, pb: 6.2, turnover_rate: 0.55, revenue_yoy: 11.3, profit_yoy: 13.4, main_net_nd: 240_000_000, as_of: '2026-10-30' },
  { code: '603288', name: '海天味业', pe_ttm: 33.1, pb: 7.4, turnover_rate: 0.42, revenue_yoy: 4.1, profit_yoy: 5.6, main_net_nd: 96_000_000, as_of: '2026-10-30' },
  { code: '600887', name: '伊利股份', pe_ttm: 15.4, pb: 3.1, turnover_rate: 0.83, revenue_yoy: 2.8, profit_yoy: 9.1, main_net_nd: 205_000_000, as_of: '2026-10-30' },
  { code: '002304', name: '洋河股份', pe_ttm: 12.6, pb: 2.4, turnover_rate: 0.47, revenue_yoy: -1.2, profit_yoy: -3.4, main_net_nd: -42_000_000, as_of: '2026-10-30' },
  { code: '000596', name: '古井贡酒', pe_ttm: 19.7, pb: 4.6, turnover_rate: 0.58, revenue_yoy: 9.8, profit_yoy: 11.2, main_net_nd: 121_000_000, as_of: '2026-10-30' },
]

const GENERIC_CONSTITUENTS: ConstituentRow[] = [
  { code: '600001', name: null, pe_ttm: 24.3, pb: 3.8, turnover_rate: 1.2, revenue_yoy: 7.4, profit_yoy: 8.9, main_net_nd: 86_000_000, as_of: '2026-10-30' },
  { code: '600002', name: null, pe_ttm: 19.8, pb: 2.9, turnover_rate: 1.6, revenue_yoy: 4.2, profit_yoy: 6.1, main_net_nd: 54_000_000, as_of: '2026-10-30' },
  { code: '600003', name: null, pe_ttm: 31.2, pb: 4.4, turnover_rate: 2.1, revenue_yoy: 12.6, profit_yoy: 15.3, main_net_nd: 132_000_000, as_of: '2026-10-30' },
  { code: '600004', name: null, pe_ttm: 27.6, pb: 3.3, turnover_rate: 0.9, revenue_yoy: 3.1, profit_yoy: 2.2, main_net_nd: -18_000_000, as_of: '2026-10-30' },
]

function demoRelated(name: string) {
  if (name === '食品饮料') {
    return [
      { kind: 'report' as const, title: '白酒行业 2026Q3 跟踪：动销边际改善，龙头估值处历史低位', source: '东财研报', as_of: '2026-10-29', url: 'https://data.eastmoney.com/report/', related_codes: ['600519', '000858'], industries: ['食品饮料'] },
      { kind: 'news' as const, title: '食品饮料板块午后走强，主力资金净流入居前', source: '财联社', as_of: '2026-10-30', url: null, related_codes: [], industries: ['食品饮料'] },
      { kind: 'report' as const, title: '大众品 2026 年三季报总结：成本红利延续', source: '东财研报', as_of: '2026-10-28', url: 'https://data.eastmoney.com/report/', related_codes: ['600887', '603288'], industries: ['食品饮料'] },
    ]
  }
  return [
    { kind: 'report' as const, title: `${name}行业周报：景气与资金面跟踪`, source: '东财研报', as_of: '2026-10-29', url: 'https://data.eastmoney.com/report/', related_codes: [], industries: [name] },
    { kind: 'news' as const, title: `${name}板块异动，关注资金流向`, source: '财联社', as_of: '2026-10-30', url: null, related_codes: [], industries: [name] },
  ]
}

function findEntry(name: string, variant: DemoVariant): DemoIndustry | null {
  const list = pool(variant)
  return list.find((entry) => entry.name === name) ?? null
}

export function demoIndustryDetail(name: string, variant: DemoVariant = 'full'): IndustryDetailPayload {
  const entry = findEntry(name, variant)
  const degraded = variant === 'partial'
  if (variant === 'empty' || !entry || variant === 'all-null') {
    const missingMetrics = values({})
    const reasons = Object.fromEntries(
      INDUSTRY_DIMENSIONS.map((dimension) => [
        dimension,
        `演示：${name || '该行业'} 没有可用缓存（六个数据集为空），字段全部显式 null`,
      ]),
    ) as Record<IndustryDimension, string>
    return skeletonDetail(name || '未知行业', missingMetrics, reasons, {
      available: false,
      inCatalog: variant !== 'empty',
      candidates: variant === 'empty' ? [] : [COMPLETE[0].name, COMPLETE[1].name, COMPLETE[2].name],
      reason: variant === 'empty'
        ? '演示：后端接口未接入（HTTP 404）时的空态'
        : '演示：六个输入数据集在本机缓存里都没有可用记录，五维全部显式 null',
    })
  }
  const metrics = entry.metrics
  const dimensions = {
    prosperity: {
      available: dimensionsAvailable(metrics).prosperity,
      reason: entry.reasons?.prosperity ?? null,
      caliber: 'sw_constituents',
      revenue_yoy_median: metrics['prosperity.revenue_yoy_median'],
      profit_yoy_median: metrics['prosperity.profit_yoy_median'],
      roe_median: metrics['prosperity.roe_median'],
      stocks_with_report: entry.constituent_count,
      report_basis: '东财财务指标原值：营收 / 净利润同比为单季或累计口径以字段名为准，ROE 为累计值非年化',
    },
    capital: {
      available: dimensionsAvailable(metrics).capital,
      reason: entry.reasons?.capital ?? null,
      caliber: { sw: 'sw_constituents', board: null },
      constituents: {
        main_net_inflow_nd: metrics['capital.constituents.main_net_inflow_nd'],
        main_net_inflow_nd_sampled: metrics['capital.constituents.main_net_inflow_nd_sampled'],
        window_days: 5,
        days: ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'],
        sample_count: entry.constituent_count,
        coverage: {
          expected_rows: entry.constituent_count * 5,
          rows_used: entry.constituent_count * 5,
          complete: true,
        },
        reason: null,
      },
      board: {
        caliber: null,
        board_code: null,
        board_name: entry.name,
        trade_date: null,
        main_net_inflow: null,
        main_net_pct: null,
        change_pct: null,
        reason: 'config/industry.yaml 未给该行业配置同名板块（同名≠同成分），板块口径不给数',
      },
      northbound: null,
      northbound_reason: '北向资金上游只有市场级（northbound_flow 无行业拆分），行业北向净流入接不上',
    },
    valuation: {
      available: dimensionsAvailable(metrics).valuation && !degraded,
      reason: degraded
        ? '演示：variant=partial 关闭估值缓存，分位与中位数留空'
        : entry.reasons?.valuation ?? null,
      caliber: 'sw_constituents',
      pe_median: degraded ? null : metrics['valuation.pe_median'],
      pe_percentile: degraded ? null : metrics['valuation.pe_percentile'],
      pb_median: degraded ? null : metrics['valuation.pb_median'],
      pb_percentile: degraded ? null : metrics['valuation.pb_percentile'],
      window_days: 250,
      window_days_used: 249,
      percentile_definition: '窗口内每日序列中 <= 当前值的天数 / 窗口天数（经验分位，不插值）',
      stocks_with_valuation: entry.constituent_count,
    },
    momentum: {
      available: dimensionsAvailable(metrics).momentum,
      reason: entry.reasons?.momentum ?? null,
      index_change_pct: null,
      index_change_pct_reason:
        '申万行业指数历史不可得：TickHub-CN sw_industry_index 实测「东财未发布该代码的 K 线，且无零鉴权替代源」，指数口径涨跌幅不降级为成分股聚合',
      change_pct_5d: metrics['momentum.change_pct_5d'],
      change_pct_20d: metrics['momentum.change_pct_20d'],
      amount_share_pct: metrics['momentum.amount_share_pct'],
      amount_share_base: '本地缓存代码池 5980 只（全市场口径需先同步盘后全市场包）',
      amount_reason: null,
      trade_date: '2026-10-30',
      market_codes: 5980,
    },
    crowding: {
      available: dimensionsAvailable(metrics).crowding,
      reason: entry.reasons?.crowding ?? null,
      turnover_median: metrics['crowding.turnover_median'],
      turnover_percentile: metrics['crowding.turnover_percentile'],
      turnover_lookback_days: 60,
      fund_concentration_pct: metrics['crowding.fund_concentration_pct'],
      fund_concentration_base: '本地缓存代码池 5980 只主力净流入绝对值合计',
      missing_flow_rows: 0,
    },
  }
  const unavailable = INDUSTRY_DIMENSIONS.filter((dimension) => !dimensions[dimension].available)
  const membershipReason = entry.membership_reason ?? null
  return {
    available: Object.values(metrics).some((value) => value !== null),
    meta: {
      name: entry.name,
      l1_code: entry.l1_code || null,
      caliber: 'sw_l1',
      in_catalog: true,
      bound: entry.bound,
      anchors: entry.name === '食品饮料' ? ['600519', '000858'] : [],
      board_name: null,
      enabled: true,
      note: membershipReason,
    },
    membership: {
      constituent_count: entry.constituent_count,
      classified_codes: 5132,
      as_of: '2025-12-01',
      reason: membershipReason,
    },
    dimensions,
    windows: DEMO_WINDOWS,
    formulas: {
      prosperity: '成分股最近一期报表的 revenue_yoy / profit_yoy / roe 中位数（%）',
      capital: '近 N 个交易日成分股 main_net 合计（申万口径）+ 同名板块资金流（板块口径，独立标注）',
      valuation: '成分股每日中位数 PE/PB → 窗口内经验分位（<= 当前值的占比）',
      momentum: '成分股等权涨跌幅（%）+ 成交额占本地缓存代码池比（%）',
      crowding: '成分股每日中位数换手率 → 窗口内经验分位 + 主力净流入活跃度集中度',
    },
    datasets: demoDatasets(true),
    provenance: demoProvenance(metrics),
    metrics_present: Object.entries(metrics)
      .filter(([, value]) => value !== null)
      .map(([path]) => path)
      .sort(),
    missing: Object.entries(metrics)
      .filter(([, value]) => value === null)
      .map(([path]) => path)
      .sort(),
    unavailable_dimensions: [...unavailable],
    data_source_status: unavailable.length === 0 ? 'ok' : unavailable.length === INDUSTRY_DIMENSIONS.length ? 'missing' : 'partial',
    reason: unavailable.length === 0 ? null : '部分维度无本地缓存，其余照常展示；缺的字段显式 null',
    source_detail: '演示数据：本地缓存回放（零外呼）',
    history:
      degraded || entry.name === '综合'
        ? { pe_percentile: [], pb_percentile: [], turnover_percentile: [], change_pct_20d: [] }
        : {
            pe_percentile: metrics['valuation.pe_percentile'] === null ? [] : series(metrics['valuation.pe_percentile'] as number, 30, 0.05),
            pb_percentile: metrics['valuation.pb_percentile'] === null ? [] : series(metrics['valuation.pb_percentile'] as number, 30, 0.04),
            turnover_percentile: metrics['crowding.turnover_percentile'] === null ? [] : series(metrics['crowding.turnover_percentile'] as number, 20, 0.08),
            change_pct_20d:
              metrics['momentum.change_pct_20d'] === null
                ? []
                : series(Math.min(1, Math.max(0, ((metrics['momentum.change_pct_20d'] as number) + 10) / 20)), 20, 0.1),
          },
    constituents: degraded ? [] : entry.name === '食品饮料' ? FOOD_CONSTITUENTS : GENERIC_CONSTITUENTS,
    related: degraded ? [] : demoRelated(entry.name),
    candidates: [],
    requested_industry: name || null,
  }
}

function skeletonDetail(
  name: string,
  metrics: Record<string, number | null>,
  reasons: Record<IndustryDimension, string>,
  options: {
    available: boolean
    inCatalog: boolean
    candidates: string[]
    reason: string
  },
): IndustryDetailPayload {
  const empty = dimensionsAvailable(metrics)
  return {
    available: options.available,
    meta: {
      name,
      l1_code: null,
      caliber: 'sw_l1',
      in_catalog: options.inCatalog,
      bound: false,
      anchors: [],
      board_name: null,
      enabled: true,
      note: null,
    },
    membership: {
      constituent_count: 0,
      classified_codes: options.available ? 0 : 0,
      as_of: null,
      reason: options.reason,
    },
    dimensions: {
      prosperity: { available: empty.prosperity, reason: reasons.prosperity, caliber: null, revenue_yoy_median: null, profit_yoy_median: null, roe_median: null, stocks_with_report: null, report_basis: null },
      capital: {
        available: empty.capital,
        reason: reasons.capital,
        caliber: {},
        constituents: { main_net_inflow_nd: null, main_net_inflow_nd_sampled: null, window_days: null, days: [], sample_count: null, coverage: { expected_rows: null, rows_used: null, complete: null }, reason: reasons.capital },
        board: { caliber: null, board_code: null, board_name: null, trade_date: null, main_net_inflow: null, main_net_pct: null, change_pct: null, reason: reasons.capital },
        northbound: null,
        northbound_reason: '北向资金上游只有市场级，行业口径接不上',
      },
      valuation: { available: empty.valuation, reason: reasons.valuation, caliber: null, pe_median: null, pe_percentile: null, pb_median: null, pb_percentile: null, window_days: null, window_days_used: null, percentile_definition: null, stocks_with_valuation: null },
      momentum: { available: empty.momentum, reason: reasons.momentum, index_change_pct: null, index_change_pct_reason: '申万行业指数历史不可得', change_pct_5d: null, change_pct_20d: null, amount_share_pct: null, amount_share_base: null, amount_reason: reasons.momentum, trade_date: null, market_codes: null },
      crowding: { available: empty.crowding, reason: reasons.crowding, turnover_median: null, turnover_percentile: null, turnover_lookback_days: null, fund_concentration_pct: null, fund_concentration_base: null, missing_flow_rows: null },
    },
    windows: DEMO_WINDOWS,
    formulas: {},
    datasets: demoDatasets(false),
    provenance: { values: {}, sources: [], unverified: true },
    metrics_present: [],
    missing: Object.keys(metrics).sort(),
    unavailable_dimensions: [...INDUSTRY_DIMENSIONS],
    data_source_status: 'missing',
    reason: options.reason,
    source_detail: '演示数据：本地缓存回放（零外呼）',
    history: { pe_percentile: [], pb_percentile: [], turnover_percentile: [], change_pct_20d: [] },
    constituents: [],
    related: [],
    candidates: options.candidates,
    requested_industry: name,
  }
}

// ---------------------------------------------------------------- 产业信号（演示夹具）

interface EvidenceSpec {
  path: string
  label: string
  unit: string | null
  dataset: string
  dimension: IndustryDimension
  comparator: '>' | '<'
  threshold: number
}

interface DemoRule {
  id: string
  title: string
  direction: 'bullish' | 'bearish'
  expression: string
  severity: 'info' | 'warn'
  specs: EvidenceSpec[]
}

const DEMO_RULES: DemoRule[] = [
  {
    id: 'capital_valuation',
    title: '资金流入 + 估值低位',
    direction: 'bullish',
    severity: 'info',
    expression: 'capital.constituents.main_net_inflow_nd > 0 且 valuation.pe_percentile < 0.30',
    specs: [
      { path: 'capital.constituents.main_net_inflow_nd', label: '近 5 日主力净流入', unit: '元', dataset: 'fund_flow', dimension: 'capital', comparator: '>', threshold: 0 },
      { path: 'valuation.pe_percentile', label: 'PE 近 250 日分位', unit: 'ratio', dataset: 'valuation_history', dimension: 'valuation', comparator: '<', threshold: 0.3 },
    ],
  },
  {
    id: 'prosperity_valuation',
    title: '景气向上 + 估值低位',
    direction: 'bullish',
    severity: 'info',
    expression: 'prosperity.revenue_yoy_median > 0 且 prosperity.profit_yoy_median > 0 且 valuation.pe_percentile < 0.30',
    specs: [
      { path: 'prosperity.revenue_yoy_median', label: '营收同比中位数', unit: '%', dataset: 'finance_indicators', dimension: 'prosperity', comparator: '>', threshold: 0 },
      { path: 'prosperity.profit_yoy_median', label: '净利同比中位数', unit: '%', dataset: 'finance_indicators', dimension: 'prosperity', comparator: '>', threshold: 0 },
      { path: 'valuation.pe_percentile', label: 'PE 近 250 日分位', unit: 'ratio', dataset: 'valuation_history', dimension: 'valuation', comparator: '<', threshold: 0.3 },
    ],
  },
  {
    id: 'momentum_crowding',
    title: '动量转强 + 拥挤度不高',
    direction: 'bullish',
    severity: 'info',
    expression: 'momentum.change_pct_5d > 0 且 momentum.change_pct_20d > 0 且 crowding.turnover_percentile < 0.70',
    specs: [
      { path: 'momentum.change_pct_5d', label: '5 日等权涨跌', unit: '%', dataset: 'daily_k', dimension: 'momentum', comparator: '>', threshold: 0 },
      { path: 'momentum.change_pct_20d', label: '20 日等权涨跌', unit: '%', dataset: 'daily_k', dimension: 'momentum', comparator: '>', threshold: 0 },
      { path: 'crowding.turnover_percentile', label: '换手分位', unit: 'ratio', dataset: 'valuation_history', dimension: 'crowding', comparator: '<', threshold: 0.7 },
    ],
  },
  {
    id: 'crowding_risk',
    title: '拥挤度高位（风险）',
    direction: 'bearish',
    severity: 'warn',
    expression: 'crowding.turnover_percentile > 0.80 且 crowding.fund_concentration_pct > 25',
    specs: [
      { path: 'crowding.turnover_percentile', label: '换手分位', unit: 'ratio', dataset: 'valuation_history', dimension: 'crowding', comparator: '>', threshold: 0.8 },
      { path: 'crowding.fund_concentration_pct', label: '资金集中度', unit: '%', dataset: 'fund_flow', dimension: 'crowding', comparator: '>', threshold: 25 },
    ],
  },
]

function passes(value: number, comparator: '>' | '<', threshold: number): boolean {
  return comparator === '>' ? value > threshold : value < threshold
}

function formatEvidence(value: number, unit: string | null): string {
  if (unit === 'ratio') return `${(value * 100).toFixed(1)}%`
  if (unit === '元') {
    const abs = Math.abs(value)
    if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
    if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万`
    return value.toFixed(2)
  }
  return unit === '%' ? `${value.toFixed(2)}%` : value.toFixed(2)
}

function buildSignals(): IndustrySignal[] {
  const out: IndustrySignal[] = []
  for (const entry of COMPLETE) {
    for (const rule of DEMO_RULES) {
      const values = rule.specs.map((spec) => entry.metrics[spec.path])
      if (values.some((value) => value === null)) continue
      const evidence = rule.specs.map((spec, index) => ({
        path: spec.path,
        label: spec.label,
        value: values[index] as number,
        unit: spec.unit,
        as_of: DEMO_AS_OF[spec.dimension],
        dataset: spec.dataset,
        comparator: spec.comparator,
        threshold: spec.threshold,
        passed: passes(values[index] as number, spec.comparator, spec.threshold),
      }))
      if (!evidence.every((item) => item.passed)) continue
      out.push({
        id: `sig-${rule.id}-${entry.name}`,
        rule_id: rule.id,
        rule_title: rule.title,
        industry: entry.name,
        direction: rule.direction,
        severity: rule.severity,
        as_of: evidence.map((item) => item.as_of).sort().at(-1) as string,
        summary: evidence.map((item) => `${item.label} ${formatEvidence(item.value, item.unit)}`).join(' · '),
        evidence,
      })
    }
  }
  return out
}

function demoRules(): SignalRule[] {
  return DEMO_RULES.map((rule) => ({
    id: rule.id,
    title: rule.title,
    direction: rule.direction,
    expression: rule.expression,
  }))
}

export function demoSignals(variant: DemoVariant = 'full'): SignalsPayload {
  const rules = demoRules()
  if (variant === 'empty' || variant === 'all-null') {
    return {
      available: false,
      as_of: null,
      rules,
      signals: [],
      counts: { total: 0, bullish: 0, bearish: 0, by_rule: {} },
      reason:
        variant === 'empty'
          ? '演示：信号接口未接入（HTTP 404）时的空态'
          : '演示：六个输入数据集在本机缓存里都没有可用记录，无法判定任何规则',
      dropped: 0,
      data_as_of: null,
    }
  }
  const all = buildSignals()
  const signals = variant === 'partial' ? all.slice(0, 2) : all
  const dropped = variant === 'partial' ? 1 : 0
  return {
    available: true,
    as_of: '2026-10-30',
    rules,
    signals,
    counts: {
      total: signals.length + dropped,
      bullish: signals.filter((signal) => signal.direction === 'bullish').length,
      bearish: signals.filter((signal) => signal.direction === 'bearish').length,
      by_rule: Object.fromEntries(
        rules.map((rule) => [rule.id, signals.filter((signal) => signal.rule_id === rule.id).length]),
      ),
    },
    reason: dropped > 0 ? '演示：1 条信号的证据链不完整，已丢弃（不展示半条证据）' : null,
    dropped,
    data_as_of: '2025-12-01',
  }
}
