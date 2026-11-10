// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { PlainConclusion } from '../src/api/plainConclusion'
import { PlainConclusionCard } from '../src/components/research/PlainConclusionCard'

const DISCLAIMER = '本报告由 AI 自动生成，仅用于研究与教学，不构成任何投资建议。'

function sample(overrides: Partial<PlainConclusion> = {}): PlainConclusion {
  return {
    version: 'plain-v1',
    what: {
      text: '研究团队认为暂时看不出明显偏向，多空理由大体互相抵消。',
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
        ],
        neutral: false,
      },
    ],
    confidence: 0.3836,
    confidence_note: '整体置信度 0.38（0~1，越高表示系统越有把握，但它不是上涨概率）。',
    glossary: [{ term: '研究评级', note: '研究团队给出的综合意见档位，不是买卖指令。' }],
    risk_flags: [],
    degraded: false,
    omitted_claims: 0,
    redline_blocked: 0,
    ...overrides,
  }
}

afterEach(() => cleanup())

describe('白话结论卡（QUOT-537）', () => {
  it('渲染三段、置信度、名词小抄与免责声明，文本逐字来自载荷', () => {
    const plain = sample()
    render(<PlainConclusionCard plain={plain} disclaimer={DISCLAIMER} />)

    expect(screen.getByRole('heading', { name: '白话结论' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '这是什么' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '为什么' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '风险点' })).toBeTruthy()
    expect(screen.getByText(plain.what.text)).toBeTruthy()
    expect(screen.getByText(plain.why[0].text)).toBeTruthy()
    expect(screen.getByText(plain.risks[0].text)).toBeTruthy()
    expect(screen.getByText('0.3836')).toBeTruthy()
    expect(screen.getByText(plain.confidence_note)).toBeTruthy()
    expect(screen.getByText('研究评级')).toBeTruthy()
    expect(screen.getAllByText(DISCLAIMER, { exact: false }).length).toBeGreaterThan(0)
  })

  it('证据默认收起，点开后才显示来源与定位', () => {
    const plain = sample()
    render(<PlainConclusionCard plain={plain} disclaimer={DISCLAIMER} />)

    expect(screen.queryByText('financial/600519/2026-01-28.json')).toBeNull()
    const toggle = screen.getByRole('button', { name: /证据 3 条/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getAllByText('financial/600519/2026-01-28.json').length).toBeGreaterThan(0)
    expect(screen.getByText('dragon_tiger/600519/2026-01-28.json')).toBeTruthy()
    fireEvent.click(toggle)
    expect(screen.queryByText('financial/600519/2026-01-28.json')).toBeNull()
  })

  it('降级载荷展示合规说明与摘除条数', () => {
    render(
      <PlainConclusionCard
        plain={sample({ degraded: true, omitted_claims: 2, redline_blocked: 1 })}
        disclaimer={DISCLAIMER}
      />,
    )
    expect(screen.getByText('部分表述已做合规降级')).toBeTruthy()
    expect(screen.getByText(/1 条表述因合规红线被拦截/)).toBeTruthy()
    expect(screen.getByText(/2 条论断因缺少可引用证据未展示/)).toBeTruthy()
  })

  it('载荷未透传时显示降级态，不渲染任何结论文案', () => {
    render(<PlainConclusionCard plain={null} disclaimer={DISCLAIMER} />)
    expect(screen.getByText('后端尚未透传白话结论')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '这是什么' })).toBeNull()
    expect(screen.queryByText(sample().what.text)).toBeNull()
    // 免责声明有就必须展示，不因缺白话块被吞掉
    expect(screen.getAllByText(DISCLAIMER, { exact: false }).length).toBeGreaterThan(0)
  })

  it('why / risks 为空数组时按空态说明，不补结论', () => {
    render(<PlainConclusionCard plain={sample({ why: [], risks: [] })} disclaimer={DISCLAIMER} />)
    expect(screen.getByText('载荷中没有可挂证据的「为什么」论断。')).toBeTruthy()
    expect(screen.getByText('载荷中没有可挂证据的风险论断。')).toBeTruthy()
  })

  it('风险标识按载荷字符串展示', () => {
    render(<PlainConclusionCard plain={sample({ risk_flags: ['数据时效性偏低'] })} disclaimer={DISCLAIMER} />)
    expect(screen.getByText('数据时效性偏低')).toBeTruthy()
  })

  it('中性且无证据的论断可展示并标记中性，不出现证据按钮', () => {
    render(
      <PlainConclusionCard
        plain={sample({
          what: { text: '没有可核实来源，本条为中性表述。', evidence: [], neutral: true },
          why: [],
          risks: [],
        })}
        disclaimer={DISCLAIMER}
      />,
    )
    expect(screen.getByText('没有可核实来源，本条为中性表述。')).toBeTruthy()
    expect(screen.getByText('中性表述')).toBeTruthy()
    expect(screen.queryAllByRole('button', { name: /证据/ })).toHaveLength(0)
  })

  it('缺免责声明字段时如实说明，不补写免责声明', () => {
    render(<PlainConclusionCard plain={sample()} disclaimer={null} />)
    expect(screen.getByText('载荷未提供免责声明')).toBeTruthy()
  })
})
