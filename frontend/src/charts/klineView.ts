/**
 * 个股 K 线：解析 `/api/research/{code}/kline` 的载荷，生成 ECharts 配置。
 *
 * 🔴 图里的每个数都直接取自后端：均线、涨跌幅、成交量单位都由载荷算好，这里不重算。
 * 🔴 红涨绿跌；只画 K 线、均线、成交量，不画买卖点或任何信号标记。
 * 🔴 缺失值映射成 "-"（ECharts 的空值占位），不补 0、不画假线。
 */
import { MISSING } from '../lib/format'
import type { ChartPalette } from './cssVar'

type Obj = Record<string, unknown>
export type KlineNum = number | null

export interface KlineSeries {
  dates: string[]
  open: KlineNum[]
  high: KlineNum[]
  low: KlineNum[]
  close: KlineNum[]
  volume: KlineNum[]
  change: KlineNum[]
  ma: Record<string, KlineNum[]>
}

export interface KlineView {
  symbol: string
  market: string
  marketLabel: string
  period: string
  periodLabel: string
  adjust: string
  adjustLabel: string
  source: string
  fetchedAt: string | null
  volumeUnit: string | null
  bars: number
  first: string
  last: string
  notes: string[]
  series: KlineSeries
}

export type KlineResult = { ok: true; view: KlineView } | { ok: false; error: string }

const obj = (value: unknown): value is Obj =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const str = (value: unknown): string | null => (typeof value === 'string' ? value : null)

const numArr = (value: unknown, length: number): KlineNum[] | null =>
  Array.isArray(value) &&
  value.length === length &&
  value.every((item) => item === null || (typeof item === 'number' && Number.isFinite(item)))
    ? (value as KlineNum[])
    : null

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/

/** 周期枚举在后端是 daily / weekly / monthly，参考库的 `VISIBLE_BARS` 用 day / week / month。 */
const PERIOD_ALIAS: Record<string, string> = {
  day: 'day',
  daily: 'day',
  week: 'week',
  weekly: 'week',
  month: 'month',
  monthly: 'month',
}

export const PERIOD_LABEL: Record<string, string> = {
  day: '日线',
  daily: '日线',
  week: '周线',
  weekly: '周线',
  month: '月线',
  monthly: '月线',
}

export const ADJUST_LABEL: Record<string, string> = {
  qfq: '前复权',
  hfq: '后复权',
  none: '不复权',
}

export function normalizePeriod(raw: string): string {
  return PERIOD_ALIAS[raw] ?? 'day'
}

/**
 * 接口载荷 → 视图。接口说画不了就把原因原样带出来；形状不对也说清楚，
 * 不画一张缺胳膊少腿的图。
 */
export function parseKline(payload: unknown, expectedCode?: string): KlineResult {
  if (!obj(payload)) return { ok: false, error: 'K 线接口没有返回结果' }
  // 后端统一信封 `{ payload, data_source_status, ... }`；测试与旧接口可能是平铺
  const root = obj(payload.payload) ? payload.payload : payload

  if (root.available === false) {
    return { ok: false, error: str(root.reason) ?? 'K 线接口没有返回数据' }
  }

  const series = obj(root.series) ? root.series : null
  if (!series || !Array.isArray(series.dates)) {
    return {
      ok: false,
      error:
        str(root.reason) ??
        'K 线载荷里没有 series 序列（涨跌幅与均线需要后端随行情一起返回）',
    }
  }
  const dates = series.dates
  const n = dates.length
  if (
    !n ||
    !dates.every((date) => typeof date === 'string' && DATE_PATTERN.test(date))
  ) {
    return { ok: false, error: 'K 线日期格式不对' }
  }

  const columns = (['open', 'high', 'low', 'close', 'volume', 'change'] as const).map(
    (key) => [key, numArr(series[key], n)] as const,
  )
  const bad = columns.filter(([, value]) => value === null).map(([key]) => key)
  if (bad.length) {
    return { ok: false, error: `K 线序列长度或数值不对：${bad.join('、')}` }
  }
  const column = Object.fromEntries(columns) as Record<
    (typeof columns)[number][0],
    KlineNum[]
  >

  const ma: Record<string, KlineNum[]> = {}
  if (obj(series.ma)) {
    for (const [key, value] of Object.entries(series.ma)) {
      const array = numArr(value, n)
      if (array && /^\d+$/.test(key)) ma[key] = array
    }
  }

  const period = str(root.period) ?? 'daily'
  return {
    ok: true,
    view: {
      symbol: str(root.code) ?? expectedCode ?? '',
      market: str(root.market) ?? '',
      marketLabel: str(root.market_label) ?? '',
      period,
      periodLabel: str(root.period_label) ?? PERIOD_LABEL[period] ?? '',
      adjust: str(root.adjust) ?? '',
      adjustLabel: str(root.adjust_label) ?? ADJUST_LABEL[str(root.adjust) ?? ''] ?? '',
      source: str(root.source) ?? '',
      fetchedAt: str(root.fetched_at),
      volumeUnit: str(root.volume_unit),
      bars: n,
      first: dates[0] as string,
      last: dates[n - 1] as string,
      notes: Array.isArray(root.notes)
        ? root.notes.filter((item): item is string => typeof item === 'string')
        : [],
      series: { dates: dates as string[], ...column, ma },
    },
  }
}

/** 价格：10 元以下（ETF、低价股）保留三位，其余两位。 */
export function fmtPrice(value: KlineNum): string {
  if (value === null) return MISSING
  return Math.abs(value) < 10 ? value.toFixed(3) : value.toFixed(2)
}

/** 成交量：上亿写「亿」，上万写「万」；单位由载荷的 `volume_unit` 标注。 */
export function fmtVolume(value: KlineNum): string {
  if (value === null) return MISSING
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(abs >= 1e6 ? 0 : 1)}万`
  return String(Math.round(value))
}

/** 涨跌幅：载荷里是小数（0.0123 = +1.23%）。 */
export function fmtChange(value: KlineNum): string {
  if (value === null) return MISSING
  return `${value > 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

/** 涨跌颜色的判据与 K 线柱一致：收盘高于开盘为涨；平开平收时看相对上一根收盘。 */
export function barRises(series: KlineSeries, index: number): boolean {
  const open = series.open[index] ?? null
  const close = series.close[index] ?? null
  if (open === null || close === null) return (series.change[index] ?? 0) >= 0
  return close === open ? (series.change[index] ?? 0) >= 0 : close > open
}

/** 默认显示最近多少根：日线约半年、周线两年、月线十年；其余用缩放条拖出来。 */
export const VISIBLE_BARS: Record<string, number> = { day: 120, week: 104, month: 120 }

/** ECharts 的空值占位；`null` 会被当成 0 画成一根假柱。 */
const EMPTY = '-'

const escape = (text: string): string =>
  text.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`)

/** 生成 K 线图配置：蜡烛 + 4 条均线 + 独立成交量副图 + 双 dataZoom。 */
export function klineOption(view: KlineView, palette: ChartPalette): Obj {
  const series = view.series
  const n = series.dates.length
  const visible = VISIBLE_BARS[normalizePeriod(view.period)] ?? 120
  const startValue = Math.max(0, n - visible)
  const axisLabel = { color: palette.text, fontSize: 11 }
  const maKeys = Object.keys(series.ma).sort((a, b) => Number(a) - Number(b))
  const hasVolume = view.volumeUnit !== null && series.volume.some((value) => value !== null)
  const candles = series.dates.map((_, index) =>
    [series.open[index], series.close[index], series.low[index], series.high[index]].map(
      (value) => value ?? EMPTY,
    ),
  )

  const chartSeries: Obj[] = [
    {
      name: 'K 线',
      type: 'candlestick',
      data: candles,
      barMaxWidth: 14,
      itemStyle: {
        color: palette.rise,
        color0: palette.fall,
        borderColor: palette.rise,
        borderColor0: palette.fall,
      },
    },
    ...maKeys.map((key, index) => {
      const color = palette.ma[index % palette.ma.length]
      return {
        name: `MA${key}`,
        type: 'line',
        data: series.ma[key]!.map((value) => value ?? EMPTY),
        showSymbol: false,
        smooth: false,
        lineStyle: { width: 1, color },
        itemStyle: { color },
      }
    }),
  ]
  if (hasVolume) {
    chartSeries.push({
      name: '成交量',
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      barMaxWidth: 14,
      data: series.volume.map((value, index) => ({
        value: value ?? EMPTY,
        itemStyle: {
          color: barRises(series, index) ? palette.rise : palette.fall,
          opacity: 0.7,
        },
      })),
    })
  }

  const tooltipFormatter = (params: unknown) => {
    const first = Array.isArray(params) ? params[0] : params
    const index = obj(first) && typeof first.dataIndex === 'number' ? first.dataIndex : -1
    if (index < 0 || index >= n) return ''
    const change = series.change[index] ?? null
    const tone = change === null ? palette.tooltipText : change > 0 ? palette.rise : change < 0 ? palette.fall : palette.tooltipText
    const row = (label: string, value: string, color?: string) =>
      `<div style="display:flex;justify-content:space-between;gap:16px"><span style="opacity:.7">${label}</span><span style="font-variant-numeric:tabular-nums${color ? `;color:${color}` : ''}">${value}</span></div>`
    return [
      `<div style="font-weight:600;margin-bottom:4px">${escape(series.dates[index]!)}</div>`,
      row('开盘', fmtPrice(series.open[index] ?? null)),
      row('最高', fmtPrice(series.high[index] ?? null)),
      row('最低', fmtPrice(series.low[index] ?? null)),
      row('收盘', fmtPrice(series.close[index] ?? null), tone),
      row('涨跌幅', fmtChange(change), tone),
      ...(hasVolume
        ? [row('成交量', `${fmtVolume(series.volume[index] ?? null)} ${escape(view.volumeUnit ?? '')}`)]
        : []),
      ...maKeys.map((key) => row(`MA${key}`, fmtPrice(series.ma[key]![index] ?? null))),
    ].join('')
  }

  return {
    animation: false,
    legend: {
      top: 0,
      left: 0,
      itemWidth: 14,
      itemHeight: 2,
      textStyle: { color: palette.text, fontSize: 11 },
      data: maKeys.map((key) => `MA${key}`),
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
      label: { backgroundColor: palette.tooltipBg, color: palette.tooltipText },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: tooltipFormatter,
      backgroundColor: palette.tooltipBg,
      borderColor: palette.axis,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
    },
    grid: hasVolume
      ? [
          { left: 60, right: 16, top: 28, height: '56%' },
          { left: 60, right: 16, top: '72%', height: '14%' },
        ]
      : [{ left: 60, right: 16, top: 28, bottom: 64 }],
    xAxis: [
      {
        type: 'category',
        data: series.dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: palette.axis } },
        axisTick: { show: false },
        axisLabel: hasVolume ? { show: false } : axisLabel,
        splitLine: { show: false },
      },
      ...(hasVolume
        ? [
            {
              type: 'category',
              gridIndex: 1,
              data: series.dates,
              boundaryGap: true,
              axisLine: { lineStyle: { color: palette.axis } },
              axisTick: { show: false },
              axisLabel,
              splitLine: { show: false },
            },
          ]
        : []),
    ],
    yAxis: [
      {
        type: 'value',
        scale: true,
        axisLabel: { ...axisLabel, formatter: (value: number) => fmtPrice(value) },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: palette.grid } },
      },
      ...(hasVolume
        ? [
            {
              type: 'value',
              gridIndex: 1,
              splitNumber: 2,
              axisLabel: { ...axisLabel, formatter: (value: number) => fmtVolume(value) },
              axisLine: { show: false },
              splitLine: { lineStyle: { color: palette.grid } },
            },
          ]
        : []),
    ],
    // 图在页面中间：普通滚轮留给页面滚动，Ctrl 滚轮（触控板双指捏合）才缩放，拖动平移。
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: hasVolume ? [0, 1] : [0],
        startValue,
        endValue: n - 1,
        zoomOnMouseWheel: 'ctrl',
        moveOnMouseWheel: false,
      },
      {
        type: 'slider',
        xAxisIndex: hasVolume ? [0, 1] : [0],
        startValue,
        endValue: n - 1,
        height: 18,
        bottom: 8,
        borderColor: palette.axis,
        textStyle: { color: palette.text },
      },
    ],
    series: chartSeries,
  }
}
