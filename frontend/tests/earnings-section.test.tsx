// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import { parseEarningsBrief, type EarningsBriefLoader } from '../src/api/earningsBrief'
import { EarningsSection } from '../src/components/research/sections/EarningsSection'
import { earningsEnvelope } from './fixtures/earningsBrief'

/**
 * QUOT-450 区块渲染：
 * - 一致预期 / 六列表格 / 统计句 / 没取到的 / 来源注脚都在；
 * - 缺数显示 `—`，且原因能在「没取到的」里找到；
 * - 空态与接口失败态不编数。
 */

afterEach(cleanup)

function renderSection(loader: EarningsBriefLoader) {
  return render(
    <MemoryRouter initialEntries={['/research/600000']}>
      <EarningsSection overview={null} code="600000" loader={loader} />
    </MemoryRouter>,
  )
}

const okLoader = (): EarningsBriefLoader => async () =>
  parseEarningsBrief(earningsEnvelope(), '600000')

describe('EarningsSection（QUOT-450）', () => {
  it('渲染一致预期、六列表格、统计句、缺口与来源注脚', async () => {
    renderSection(okLoader())

    await screen.findByText(/一致预期 · 2026E/)
    expect(screen.getByText('1.53 元')).toBeTruthy()
    expect(screen.getByText(/最高 ÷ 最低/).textContent).toContain('1.15 倍')
    // 数字用 ldst-num 单独包了一层，getByText 只能读直接文本节点，这里按整段 textContent 断言
    const coverage = screen.getAllByText(/家覆盖/).map((node) => node.textContent ?? '')
    expect(coverage.some((text) => text.includes('12 家覆盖'))).toBe(true)

    for (const header of ['报告期', '发布日', '反应日', '当天涨跌', '实际', '预期']) {
      expect(screen.getByRole('columnheader', { name: header })).toBeTruthy()
    }
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(screen.getByText(/营收 1,381.33 亿元/)).toBeTruthy()

    expect(screen.getByText(/样本不足 0 次，不给平均/)).toBeTruthy()

    const gaps = screen.getByRole('heading', { name: '没取到的' })
    expect(gaps).toBeTruthy()
    expect(screen.getByText(/发布日：finance_indicators 没有公告日字段/)).toBeTruthy()
    expect(screen.getByText(/历史预期：本地 earnings_preview/)).toBeTruthy()
    expect(screen.getByText(/预约披露日：上游 TickHub-CN/)).toBeTruthy()
    expect(screen.getByText(/历史逐期一致预期未接入/)).toBeTruthy()

    expect(screen.getByText(/ths_eps_forecast/)).toBeTruthy()
    expect(screen.getByText(/eastmoney_finance_indicators/)).toBeTruthy()
  })

  it('整块空态：没有财报也没有一致预期时给出口，不显示 0', async () => {
    renderSection(async () =>
      parseEarningsBrief(
        earningsEnvelope({
          consensus: null,
          consensus_reason: '本地没有一致预期快照',
          earnings: [],
          earnings_reason: '本地 finance_indicators 没有 600000 的报告期记录（未同步）',
          stats: {
            periods: 0,
            with_forecast: 0,
            with_publish_date: 0,
            with_reaction: 0,
            latest_report_date: null,
            latest_report_eps: null,
          },
          data_source_status: 'missing',
        }),
        '600000',
      ),
    )

    expect(await screen.findByText(/暂无财报与一致预期/)).toBeTruthy()
    expect(screen.getByText(/未同步/)).toBeTruthy()
    expect(screen.queryByText(/样本不足/)).toBeNull()
  })

  it('接口失败：给错误横幅与重试，不渲染任何数字', async () => {
    const loader = vi.fn(async () => {
      throw new ApiError('not-connected', '财报接口不可达')
    })
    renderSection(loader)

    await waitFor(() => expect(screen.getByText('财报数据读取失败')).toBeTruthy())
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
    expect(screen.queryByText(/一致预期 ·/)).toBeNull()
    expect(screen.queryByText(/样本不足/)).toBeNull()
  })
})
