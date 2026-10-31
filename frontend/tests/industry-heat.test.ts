import { describe, expect, it } from 'vitest'

import {
  compareMetric,
  dimensionValues,
  formatEvidenceValue,
  formatMetric,
  percentileValue,
  type MetricSpec,
} from '../src/screens/industry/metrics'
import { levelHeatStyle, rankRatio, signedHeatStyle } from '../src/screens/industry/heat'
import { demoIndustryDetail } from '../src/screens/industry/demo'
import { parseIndustryDetail } from '../src/api/industry'

const spec = (kind: MetricSpec['kind'], path = 'x.y'): MetricSpec => ({ path, label: '指标', kind })

describe('热力图色阶（DEC-028）', () => {
  it('同列相对位置：最小 0、最大 1、并列不越界', () => {
    const values = [1, 2, 3]
    expect(rankRatio(values, 1)).toBe(0)
    expect(rankRatio(values, 2)).toBe(0.5)
    expect(rankRatio(values, 3)).toBe(1)
    expect(rankRatio(values, null)).toBeNull()
  })

  it('并列与常数序列取中间色，不把并列都推成最高值', () => {
    expect(rankRatio([5, 5, 5], 5)).toBe(0.5)
    expect(rankRatio([1, 2, 2, 4], 2)).toBe(0.5)
  })

  it('颜色只来自令牌：低值 4%、高值 52%，不出现色值字面量', () => {
    const low = levelHeatStyle(0)
    const high = levelHeatStyle(1)
    expect(low?.backgroundColor).toContain('var(--ldst-accent) 4%')
    expect(high?.backgroundColor).toContain('var(--ldst-accent) 52%')
    expect(low?.backgroundColor).toContain('var(--ldst-surface)')
    expect(levelHeatStyle(null)).toBeUndefined()
  })

  it('带方向列使用涨跌 soft 令牌', () => {
    expect(signedHeatStyle(1.2)).toEqual({ backgroundColor: 'var(--ldst-up-soft)' })
    expect(signedHeatStyle(-1.2)).toEqual({ backgroundColor: 'var(--ldst-down-soft)' })
    expect(signedHeatStyle(0)).toEqual({ backgroundColor: 'var(--ldst-flat-soft)' })
    expect(signedHeatStyle(null)).toBeUndefined()
  })
})

describe('五维指标格式化', () => {
  it('缺失一律 —，不补 0', () => {
    expect(formatMetric(spec('ratioPct'), null).text).toBe('—')
    expect(formatMetric(spec('money'), undefined).missing).toBe(true)
  })

  it('分位 0–1 换算成百分数；变化率带正负号与红涨绿跌', () => {
    expect(formatMetric(spec('ratioPct'), 0.18).text).toBe('18.0%')
    const down = formatMetric(spec('pctSigned'), -2.1)
    expect(down.text).toBe('-2.10%')
    expect(down.tone).toBe('down')
    expect(formatMetric(spec('pctSigned'), 1.6).text).toBe('+1.60%')
  })

  it('水平百分比（ROE/换手）不上涨跌色', () => {
    expect(formatMetric(spec('pct'), 12.4)).toEqual({ text: '12.40%', tone: 'flat', missing: false })
  })

  it('金额与倍数保留单位', () => {
    expect(formatMetric(spec('money'), 1_830_000_000).text).toBe('18.3 亿')
    expect(formatMetric(spec('multiple'), 22.1).text).toBe('22.10 倍')
  })

  it('分位只接受 0–1，越界视为无数据', () => {
    expect(percentileValue(0.18)).toBe(0.18)
    expect(percentileValue(18)).toBeNull()
    expect(percentileValue(-0.1)).toBeNull()
    expect(percentileValue(null)).toBeNull()
  })

  it('证据值与契约单位一致', () => {
    expect(formatEvidenceValue(0.3, 'ratio')).toBe('30.0%')
    expect(formatEvidenceValue(1.5, '%')).toBe('1.50%')
    expect(formatEvidenceValue(230_000_000, '元')).toBe('2.3 亿')
  })

  it('可空排序把缺失放最后', () => {
    const list = [3, null, 1]
    expect([...list].sort(compareMetric)).toEqual([1, 3, null])
  })
})

describe('单行业块摊平', () => {
  it('五维路径与清单投影同名，capital 拆成分/板块', () => {
    const detail = parseIndustryDetail(demoIndustryDetail('食品饮料'))
    const capital = dimensionValues('capital', detail.dimensions)
    expect(capital['capital.constituents.main_net_inflow_nd']).toBe(1_830_000_000)
    expect(capital['capital.board.main_net_inflow']).toBeNull()
    const valuation = dimensionValues('valuation', detail.dimensions)
    expect(valuation['valuation.pe_percentile']).toBeCloseTo(0.18)
  })
})
