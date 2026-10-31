import { describe, expect, it } from 'vitest'

import {
  DIMENSION_TITLE,
  metricOf,
  normalizeIndustryName,
  parseIndustryDetail,
  parseIndustryList,
  parseSignals,
} from '../src/api/industry'
import { demoIndustryDetail, demoIndustryList, demoSignals } from '../src/screens/industry/demo'

describe('产业清单解析', () => {
  it('平铺契约：partial 演示包含 15 个行业，缺失维度是 null 而不是 0', () => {
    const payload = parseIndustryList(demoIndustryList('partial'))
    expect(payload.industries).toHaveLength(15)
    expect(payload.available).toBe(true)
    const realEstate = payload.industries.find((row) => row.name === '房地产')
    expect(realEstate).toBeTruthy()
    expect(realEstate?.data_source_status).toBe('partial')
    expect(metricOf(realEstate!.metrics, 'valuation.pe_median')).toBeNull()
    expect(metricOf(realEstate!.metrics, 'crowding.turnover_percentile')).toBe(0.31)
    expect(realEstate?.dimensions_available.valuation).toBe(false)
    expect(realEstate?.as_of.valuation).toBe('2026-10-30')
  })

  it('接受 {payload:{...}} 外壳；缺 industries 会明确报错', () => {
    const inner = demoIndustryList('full')
    expect(parseIndustryList({ payload: inner }).industries).toHaveLength(13)
    expect(() => parseIndustryList({ payload: {} })).toThrow(/industries/)
  })
})

describe('单行业解析', () => {
  it('接受 MCP 的 payload.industry_metrics 外壳，五维/历史/成分/相关都在', () => {
    const detail = parseIndustryDetail(
      { payload: { industry_metrics: demoIndustryDetail('食品饮料') } },
      '食品饮料',
    )
    expect(detail.meta.name).toBe('食品饮料')
    expect(detail.dimensions.valuation.pe_percentile).toBeCloseTo(0.18)
    expect(detail.history.pe_percentile.length).toBeGreaterThan(2)
    expect(detail.constituents[0]?.code).toBe('600519')
    expect(detail.related.length).toBeGreaterThan(0)
    expect(detail.unavailable_dimensions).toEqual([])
    expect(detail.provenance.sources.length).toBeGreaterThan(0)
  })

  it('partial 变体：估值不可用、历史/成分/资讯为空，但其余维度保留', () => {
    const detail = parseIndustryDetail(demoIndustryDetail('食品饮料', 'partial'))
    expect(detail.available).toBe(true)
    expect(detail.unavailable_dimensions).toContain('valuation')
    expect(detail.dimensions.momentum.change_pct_20d).toBeCloseTo(4.2)
    expect(detail.history.pe_percentile).toEqual([])
    expect(detail.constituents).toEqual([])
    expect(detail.related).toEqual([])
    expect(detail.data_source_status).toBe('partial')
  })

  it('未绑定行业（综合）五维全 null，原因保留', () => {
    const detail = parseIndustryDetail(demoIndustryDetail('综合', 'partial'))
    expect(detail.available).toBe(false)
    expect(detail.meta.bound).toBe(false)
    expect(detail.unavailable_dimensions).toHaveLength(5)
    expect(detail.dimensions.crowding.reason).toContain('未绑定')
  })
})

describe('产业信号解析', () => {
  it('演示信号每条都有可回溯证据：字段 + 值 + 时点 + 数据集', () => {
    const payload = parseSignals(demoSignals('full'))
    expect(payload.available).toBe(true)
    expect(payload.signals.length).toBeGreaterThan(0)
    expect(payload.dropped).toBe(0)
    for (const signal of payload.signals) {
      expect(signal.evidence.length).toBeGreaterThan(0)
      for (const item of signal.evidence) {
        expect(item.path).toMatch(/^[a-z]+\.[a-zA-Z0-9_.]+$/)
        expect(item.as_of).toMatch(/^\d{4}-\d{2}-\d{2}$/)
        expect(item.dataset.length).toBeGreaterThan(0)
        expect(item.passed).toBe(true)
      }
    }
  })

  it('证据缺时点的条目整条丢弃，数量记在 dropped', () => {
    const base = demoSignals('full')
    const broken = {
      ...base,
      signals: [
        base.signals[0],
        {
          ...base.signals[1],
          evidence: base.signals[1].evidence.map((item, index) =>
            index === 0 ? { ...item, as_of: null } : item,
          ),
        },
      ],
    }
    const payload = parseSignals(broken)
    expect(payload.signals).toHaveLength(1)
    expect(payload.dropped).toBe(1)
  })

  it('规则清单保留表达式与方向', () => {
    const payload = parseSignals(demoSignals('partial'))
    expect(payload.rules).toHaveLength(4)
    expect(payload.rules[0].expression).toContain('valuation.pe_percentile')
    // `dropped` 由解析层按「丢弃条数」重算，不采信响应里的同名字段
    expect(payload.signals).toHaveLength(2)
    expect(payload.dropped).toBe(0)
  })
})

describe('工具函数', () => {
  it('行业名只接受非空且 ≤ 40 字', () => {
    expect(normalizeIndustryName('食品饮料')).toBe('食品饮料')
    expect(normalizeIndustryName('  电子  ')).toBe('电子')
    expect(normalizeIndustryName('')).toBeNull()
    expect(normalizeIndustryName('   ')).toBeNull()
    expect(normalizeIndustryName('行'.repeat(41))).toBeNull()
  })

  it('五维标题与 R5 口径一致', () => {
    expect(DIMENSION_TITLE.valuation).toBe('估值')
    expect(DIMENSION_TITLE.crowding).toBe('拥挤度')
  })
})
