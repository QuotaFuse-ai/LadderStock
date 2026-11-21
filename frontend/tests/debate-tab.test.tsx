// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DebateTab from '../src/screens/research/DebateTab'
import type { DebateStatus, DebateView } from '../src/api/debate'

/**
 * F7 多空对照 tab 的三条硬口径：
 * 1. 门控不通过时只出「为什么不够格」，绝不渲染空对照表冒充结论；
 * 2. 有论点时按「多头 / 空头 / 两方反驳」四类标注来源角色，方向不写反；
 * 3. 没有报告时给业务空态 + 「起一轮合议」出口，而不是错误态。
 */

const { loadDebate, loadDebateStatus, startSenateAnalysis } = vi.hoisted(() => ({
  loadDebate: vi.fn(),
  loadDebateStatus: vi.fn(),
  startSenateAnalysis: vi.fn(),
}))

vi.mock('../src/api/debate', () => ({ loadDebate, loadDebateStatus, startSenateAnalysis }))

const STATUS: DebateStatus = {
  home: 'D:\\senate',
  homeExists: true,
  cli: 'D:\\senate\\venv\\Scripts\\ssenate.exe',
  cliExists: true,
  configExists: true,
  reports: 7,
  latestRunId: '2026-11-17-600519-e876abf3',
}

function makeView(overrides: Partial<DebateView> = {}): DebateView {
  return {
    runId: '2026-11-17-600519-e876abf3',
    code: '600519',
    date: '2026-11-17',
    generatedAt: '2026-11-17T20:00:00+08:00',
    rating: 'neutral',
    ratingLabel: '中性',
    action: 'hold',
    horizon: '中期',
    confidence: 0.4,
    bull: { side: 'bull', points: [], rebuttals: [], confidence: 0, partial: true, notes: '' },
    bear: { side: 'bear', points: [], rebuttals: [], confidence: 0, partial: true, notes: '' },
    bullRebuttals: [],
    bearRebuttals: [],
    flipConditions: [],
    failedRoles: [{ role: 'bull_researcher', reason: '结构化输出校验失败' }],
    participants: 5,
    quality: { score: 60, threshold: 70, lowQuality: true, partial: true },
    reportDir: 'D:\\senate\\reports\\2026-11-17-600519-e876abf3',
    gate: {
      passed: false,
      score: 60,
      threshold: 70,
      reasons: ['报告标记 partial', '质量分低于阈值（60 < 70）', '多头有效论点不足（0 < 2）'],
      min_points: 2,
      bull_points: 0,
      bear_points: 0,
      failed_roles: [{ role: 'bull_researcher', reason: '结构化输出校验失败' }],
    },
    blocks: [],
    blocksError: null,
    ...overrides,
  }
}

beforeEach(() => {
  loadDebateStatus.mockResolvedValue(STATUS)
  startSenateAnalysis.mockResolvedValue({ taskId: 'task-1' })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('DebateTab', () => {
  it('门控不通过时逐条给原因，且不渲染任何对照行', async () => {
    loadDebate.mockResolvedValue(makeView())
    render(<DebateTab code="600519" date="2026-11-17" />)

    expect(await screen.findByText('这份报告不够格出多空对照')).toBeTruthy()
    expect(screen.getByText('质量分低于阈值（60 < 70）')).toBeTruthy()
    expect(screen.getByText('多头有效论点不足（0 < 2）')).toBeTruthy()
    expect(screen.getByText(/本份报告没有任何可用论点与反驳/)).toBeTruthy()
    // 空数组不许冒充表格行：表体只有空态文案，没有「多头 / 空头」立场单元格
    expect(screen.queryByText('多头')).toBeNull()
    expect(screen.queryByText('空头')).toBeNull()
    expect(screen.getByText(/失败角色（1）/)).toBeTruthy()
  })

  it('达标时渲染多空对照行，并标对反驳方向', async () => {
    loadDebate.mockResolvedValue(
      makeView({
        bull: { side: 'bull', points: ['估值处于历史低位'], rebuttals: [], confidence: 0.6, partial: false, notes: '' },
        bear: { side: 'bear', points: ['渠道库存仍有压力'], rebuttals: [], confidence: 0.5, partial: false, notes: '' },
        bullRebuttals: ['库存已在去化'],
        bearRebuttals: ['低位不等于便宜'],
        flipConditions: ['批价连续两月下滑'],
        gate: {
          ...makeView().gate,
          passed: true,
          reasons: [],
          bull_points: 1,
          bear_points: 1,
          failed_roles: [],
        },
      }),
    )
    render(<DebateTab code="600519" date="2026-11-17" />)

    expect(await screen.findByText('门控通过：多空论点与交锋都够格')).toBeTruthy()
    expect(screen.getByText('估值处于历史低位')).toBeTruthy()
    expect(screen.getByText('渠道库存仍有压力')).toBeTruthy()
    // 方向别写反：空头反驳多头 / 多头反驳空头
    expect(screen.getByText('空头 → 多头')).toBeTruthy()
    expect(screen.getByText('多头 → 空头')).toBeTruthy()
    expect(screen.getByText('批价连续两月下滑')).toBeTruthy()
  })

  it('没有报告时给业务空态与「起一轮合议」出口', async () => {
    loadDebate.mockResolvedValue(null)
    render(<DebateTab code="600519" date="2026-11-17" />)

    expect(await screen.findByText('600519 还没有多空合议报告')).toBeTruthy()
    expect(screen.getByText(/StockSenate 已就位（7 份历史报告）/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '起一轮合议' })).toBeTruthy()
  })
})
