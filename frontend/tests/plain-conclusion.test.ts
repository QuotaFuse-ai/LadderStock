import { describe, expect, it } from 'vitest'

import {
  parsePlainBlock,
  parsePlainConclusion,
  type PlainConclusion,
} from '../src/api/plainConclusion'
import { parseResearchStatus } from '../src/api/research'

/** QUOT-536 契约评论里的真实样例（600519 @ 2026-01-28，why/risks 各保留 1 条）。 */
function contractSample(): PlainConclusion {
  return {
    version: 'plain-v1',
    what: {
      text: '研究团队认为暂时看不出明显偏向。多空理由大体互相抵消，团队没有给出方向性结论；对普通人来说，这通常意味着「先观察、不急着下判断」。这只是研究评级，代表团队的研究观点，不是买卖指令。',
      evidence: [
        { domain: 'financial', source: 'local_json', data_as_of: '2026-01-28', detail: 'financial/600519/2026-01-28.json' },
        { domain: 'dragon_tiger', source: 'local_json', data_as_of: '2026-01-28', detail: 'dragon_tiger/600519/2026-01-28.json' },
        { domain: 'money_flow', source: 'local_json', data_as_of: '2026-01-28', detail: 'money_flow/600519/2026-01-28.json' },
      ],
      neutral: false,
    },
    why: [
      {
        text: '基本面分析师：未明确。',
        evidence: [
          { domain: 'financial', source: 'local_json', data_as_of: '2026-01-28', detail: 'financial/600519/2026-01-28.json' },
        ],
        neutral: false,
      },
    ],
    risks: [
      {
        text: '激进视角：研究结论为 偏空，需按该视角衡量可承受的不确定性。',
        evidence: [
          { domain: 'kline', source: 'local_json', data_as_of: '2026-01-28', detail: 'kline/600519/2026-01-28.json' },
          { domain: 'quote', source: 'local_json', data_as_of: '2026-01-28', detail: 'quote/600519/2026-01-28.json' },
          { domain: 'money_flow', source: 'local_json', data_as_of: '2026-01-28', detail: 'money_flow/600519/2026-01-28.json' },
        ],
        neutral: false,
      },
    ],
    confidence: 0.3836,
    confidence_note:
      '整体置信度 0.38（0~1，越高表示系统越有把握，但它不是上涨概率）；该数值直接沿用投资经理终审结果，没有另行计算。',
    glossary: [
      { term: '研究评级', note: '研究团队给出的综合意见档位，只代表研究观点，不是买卖指令。' },
      { term: '置信度', note: '系统对自己判断的把握程度，0 到 1，越接近 1 越有把握；它不是上涨概率。' },
    ],
    risk_flags: [],
    degraded: false,
    omitted_claims: 0,
    redline_blocked: 0,
  }
}

describe('plain-v1 解析（QUOT-537）', () => {
  it('逐字段还原契约样例', () => {
    const parsed = parsePlainConclusion(contractSample())
    expect(parsed).toEqual(contractSample())
  })

  it('兼容四个契约出口：root / decision / sections / summary', () => {
    const sample = contractSample()
    expect(parsePlainBlock({ plain: sample }).plain).toEqual(sample)
    expect(parsePlainBlock({ decision: { plain: sample } }).plain).toEqual(sample)
    expect(parsePlainBlock({ sections: { plain: sample } }).plain).toEqual(sample)
    expect(parsePlainBlock({ summary: { plain: sample } }).plain).toEqual(sample)
  })

  it('兼容四段信封 payload', () => {
    const sample = contractSample()
    expect(parsePlainBlock({ payload: { plain: sample } }).plain).toEqual(sample)
  })

  it('免责声明同层读取，缺字段为 null', () => {
    expect(parsePlainBlock({ plain: contractSample(), disclaimer: '仅供研究。' }).disclaimer).toBe('仅供研究。')
    expect(parsePlainBlock({ sections: { plain: contractSample(), disclaimer: '仅供研究。' } }).disclaimer).toBe(
      '仅供研究。',
    )
    expect(parsePlainBlock({ plain: contractSample() }).disclaimer).toBeNull()
    expect(parsePlainBlock(null)).toEqual({ plain: null, disclaimer: null })
  })

  it('what 缺失或非法时整块不可用，不用前端文案顶替', () => {
    expect(parsePlainConclusion({ why: [], risks: [] })).toBeNull()
    expect(parsePlainConclusion({ what: { text: '   ' }, why: [] })).toBeNull()
    expect(parsePlainConclusion('plain')).toBeNull()
  })

  it('非中性论断没有证据视为非法数据，直接丢弃', () => {
    const parsed = parsePlainConclusion({
      what: { text: '一句有证据的结论', evidence: [{ domain: 'kline', source: 'local_json', data_as_of: '2026-01-28' }] },
      why: [
        { text: '有证据', evidence: [{ domain: 'kline', source: 'local_json', data_as_of: '2026-01-28' }] },
        { text: '无证据的方向性论断', evidence: [], neutral: false },
        { text: '没有可核实来源', evidence: [], neutral: true },
      ],
      risks: [{ text: '无证据风险', evidence: [] }],
    })
    expect(parsed?.why.map((claim) => claim.text)).toEqual(['有证据', '没有可核实来源'])
    expect(parsed?.risks).toEqual([])
  })

  it('证据条目缺 domain / source / data_as_of 时不计入', () => {
    const parsed = parsePlainConclusion({
      what: {
        text: '结论',
        evidence: [
          { domain: 'kline', source: 'local_json', data_as_of: '2026-01-28', detail: '' },
          { domain: 'kline', source: 'local_json' },
          { source: 'local_json', data_as_of: '2026-01-28' },
        ],
      },
    })
    expect(parsed?.what.evidence).toHaveLength(1)
    expect(parsed?.what.evidence[0].detail).toBe('')
  })

  it('数值字段越界时按契约边界收敛，缺省为 0 / false / 空数组', () => {
    const parsed = parsePlainConclusion({
      what: { text: '结论', evidence: [{ domain: 'kline', source: 'local_json', data_as_of: '2026-01-28' }] },
      confidence: 1.4,
      omitted_claims: 2.7,
      redline_blocked: -3,
      risk_flags: ['  ', '数据时效性', 42],
    })
    expect(parsed?.confidence).toBe(1)
    expect(parsed?.omitted_claims).toBe(2)
    expect(parsed?.redline_blocked).toBe(0)
    expect(parsed?.risk_flags).toEqual(['数据时效性'])
    expect(parsed?.version).toBe('plain-v1')
    expect(parsed?.degraded).toBe(false)
    expect(parsed?.glossary).toEqual([])
  })
})

describe('研究状态载荷透传（QUOT-537）', () => {
  function statusPayload(extra: Record<string, unknown>) {
    return {
      code: '600519',
      date: '2026-01-28',
      available: true,
      complete: true,
      source_status: 'ok',
      stages: [
        { name: 'company', status: 'ok' },
        { name: 'financial', status: 'ok' },
        { name: 'forecast', status: 'ok' },
        { name: 'valuation', status: 'ok' },
        { name: 'risk', status: 'ok' },
        { name: 'report', status: 'ok' },
      ],
      ...extra,
    }
  }

  it('状态响应带 plain / disclaimer 时一并解析', () => {
    const status = parseResearchStatus(
      statusPayload({ plain: contractSample(), disclaimer: '本报告由 AI 自动生成，仅用于研究与教学。' }),
    )
    expect(status.plain).toEqual(contractSample())
    expect(status.disclaimer).toBe('本报告由 AI 自动生成，仅用于研究与教学。')
  })

  it('状态响应没有 plain 时页面拿到 null（降级态依据）', () => {
    const status = parseResearchStatus(statusPayload({}))
    expect(status.plain).toBeNull()
    expect(status.disclaimer).toBeNull()
  })
})
