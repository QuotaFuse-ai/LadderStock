import { describe, expect, it } from 'vitest'

import { chartPalette } from '../src/charts/cssVar'
import {
  barRises,
  fmtChange,
  fmtPrice,
  fmtVolume,
  klineOption,
  normalizePeriod,
  parseKline,
  VISIBLE_BARS,
  type KlineView,
} from '../src/charts/klineView'

type Obj = Record<string, unknown>

const DATES = ['2026-01-02', '2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08']

function seriesPayload(overrides: Obj = {}) {
  return {
    dates: DATES,
    open: [10, 10.2, 10.1, 9.9, 10],
    high: [10.3, 10.4, 10.2, 10.1, 10.5],
    low: [9.8, 10, 9.7, 9.8, 9.9],
    close: [10.2, 10.1, 9.9, 10, 10.4],
    volume: [1_000_000, 1_200_000, 900_000, 800_000, 1_500_000],
    change: [null, -0.0098, -0.0198, 0.0101, 0.04],
    ma: {
      '5': [null, null, null, null, 10.12],
      '10': [null, null, null, null, null],
      '20': [null, null, null, null, null],
      '60': [null, null, null, null, null],
    },
    ...overrides,
  }
}

function payload(overrides: Obj = {}) {
  return {
    payload: {
      code: '600519',
      period: 'daily',
      adjust: 'qfq',
      market: 'a_share',
      market_label: 'A 股',
      period_label: '日线',
      adjust_label: '前复权',
      source: 'TickHub-CN / tencent_kline',
      fetched_at: '2026-11-05T15:36:00+08:00',
      volume_unit: '股',
      available: true,
      bars: [],
      series: seriesPayload(),
      notes: ['均线按本次返回的窗口计算，窗口不足的值为空'],
      ...overrides,
    },
    data_source_status: 'ok',
  }
}

function okView(data: unknown = payload()): KlineView {
  const result = parseKline(data)
  if (!result.ok) throw new Error(result.error)
  return result.view
}

describe('parseKline', () => {
  it('解析信封载荷：序列、标签与来源', () => {
    const view = okView()
    expect(view.symbol).toBe('600519')
    expect(view.market).toBe('a_share')
    expect(view.marketLabel).toBe('A 股')
    expect(view.periodLabel).toBe('日线')
    expect(view.adjustLabel).toBe('前复权')
    expect(view.volumeUnit).toBe('股')
    expect(view.bars).toBe(5)
    expect(view.first).toBe('2026-01-02')
    expect(view.last).toBe('2026-01-08')
    expect(view.notes).toHaveLength(1)
    expect(view.series.change[0]).toBeNull()
    expect(view.series.ma['5']![4]).toBe(10.12)
  })

  it('兼容平铺载荷（没有 payload 外壳）', () => {
    const flat = payload().payload
    const view = okView(flat)
    expect(view.bars).toBe(5)
  })

  it('缺少 series 时说清楚，不画残图', () => {
    const result = parseKline(payload({ series: undefined }))
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('series')
  })

  it('available=false 原样带出后端原因', () => {
    const result = parseKline(payload({ available: false, reason: '上游没有这批数据' }))
    expect(result).toEqual({ ok: false, error: '上游没有这批数据' })
  })

  it('日期格式不对 / 列长不一致 → 报错', () => {
    expect(parseKline(payload({ series: seriesPayload({ dates: ['2026/01/02'] }) })).ok).toBe(false)
    expect(parseKline(payload({ series: seriesPayload({ close: [1, 2] }) })).ok).toBe(false)
  })

  it('周期别名归一：daily→day', () => {
    expect(normalizePeriod('daily')).toBe('day')
    expect(normalizePeriod('weekly')).toBe('week')
    expect(normalizePeriod('monthly')).toBe('month')
  })
})

describe('klineOption', () => {
  const palette = chartPalette()
  const view = okView()
  const option = klineOption(view, palette) as Obj

  it('蜡烛 + 4 条均线 + 成交量：共 6 条序列', () => {
    const series = option.series as Obj[]
    expect(series).toHaveLength(6)
    expect(series[0].type).toBe('candlestick')
    const lines = series.filter((item) => item.type === 'line')
    expect(lines.map((item) => item.name)).toEqual(['MA5', 'MA10', 'MA20', 'MA60'])
  })

  it('成交量副图挂第二根 x 轴（xAxisIndex=1）', () => {
    const volume = (option.series as Obj[]).find((item) => item.name === '成交量')
    expect(volume?.xAxisIndex).toBe(1)
    expect(volume?.yAxisIndex).toBe(1)
    expect((option.xAxis as Obj[])).toHaveLength(2)
    expect((option.yAxis as Obj[])).toHaveLength(2)
  })

  it('双 dataZoom：inside（Ctrl 滚轮）+ slider，两轴联动', () => {
    const zoom = option.dataZoom as Obj[]
    expect(zoom).toHaveLength(2)
    expect(zoom[0].type).toBe('inside')
    expect(zoom[0].zoomOnMouseWheel).toBe('ctrl')
    expect(zoom[0].moveOnMouseWheel).toBe(false)
    expect(zoom[1].type).toBe('slider')
    for (const item of zoom) expect(item.xAxisIndex).toEqual([0, 1])
  })

  it('默认显示最近 N 根（日线 120）', () => {
    expect(VISIBLE_BARS.day).toBe(120)
    const zoom = option.dataZoom as Obj[]
    expect(zoom[0].endValue).toBe(view.series.dates.length - 1)
    expect(zoom[0].startValue).toBe(0)
  })

  it('空值映射为 "-"，不补 0', () => {
    const ma = (option.series as Obj[]).find((item) => item.name === 'MA10')
    expect((ma?.data as unknown[]).every((value) => value === '-')).toBe(true)

    // 一根缺收盘的蜡烛：数据位是 "-"，不是 0
    const withNull = okView(
      payload({ series: seriesPayload({ close: [10.2, null, 9.9, 10, 10.4] }) }),
    )
    const nullOption = klineOption(withNull, palette) as Obj
    const candles = (nullOption.series as Obj[])[0].data as unknown[][]
    expect(candles[1]).toEqual([10.2, '-', 10, 10.4])
  })

  it('没有成交量单位时不画成交量副图（不猜单位）', () => {
    const noVolume = okView(payload({ volume_unit: null }))
    const noVolumeOption = klineOption(noVolume, palette) as Obj
    expect((noVolumeOption.series as Obj[]).some((item) => item.name === '成交量')).toBe(false)
    expect((noVolumeOption.xAxis as Obj[])).toHaveLength(1)
    expect((noVolumeOption.dataZoom as Obj[])[0].xAxisIndex).toEqual([0])
  })

  it('涨跌颜色来自令牌（红涨绿跌）', () => {
    const candle = (option.series as Obj[])[0]
    expect((candle.itemStyle as Obj).color).toBe(palette.rise)
    expect((candle.itemStyle as Obj).color0).toBe(palette.fall)
    // node 环境没有样式表：走 tokens.css 暗色兜底值
    expect(palette.rise).toBe('#ff5c5c')
    expect(palette.fall).toBe('#2fbf71')
  })
})

describe('格式化与涨跌判据', () => {
  it('fmtPrice：10 元以下三位、其余两位、缺失 —', () => {
    expect(fmtPrice(9.876)).toBe('9.876')
    expect(fmtPrice(10)).toBe('10.00')
    expect(fmtPrice(1234.5)).toBe('1234.50')
    expect(fmtPrice(null)).toBe('—')
  })

  it('fmtVolume：亿 / 万 / 整数', () => {
    expect(fmtVolume(123_456_789)).toBe('1.23亿')
    expect(fmtVolume(12_345)).toBe('1.2万')
    expect(fmtVolume(123)).toBe('123')
    expect(fmtVolume(null)).toBe('—')
  })

  it('fmtChange：小数 → 带符号百分比', () => {
    expect(fmtChange(0.0123)).toBe('+1.23%')
    expect(fmtChange(-0.01)).toBe('-1.00%')
    expect(fmtChange(0)).toBe('0.00%')
    expect(fmtChange(null)).toBe('—')
  })

  it('barRises：收 > 开为涨；平开平收看涨跌幅', () => {
    const series = okView().series
    const rises = series.dates.map((_, index) => barRises(series, index))
    expect(rises).toEqual([true, false, false, true, true])
  })
})
