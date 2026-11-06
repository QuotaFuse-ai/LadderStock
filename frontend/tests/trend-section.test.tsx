// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ResearchOverview } from '../src/api/research'
import { TrendSection } from '../src/components/research/sections/TrendSection'

/** K 线本体由 QUOT-429 的单测覆盖；这里只验页面集成：行情条读数、缺口文案与挂载。 */
vi.mock('../src/components/KlineCard', () => ({
  KlineCard: ({ symbol }: { symbol: string }) => (
    <div data-testid="kline-card" data-symbol={symbol} />
  ),
}))

afterEach(cleanup)

function overview(overrides: Partial<ResearchOverview> = {}): ResearchOverview {
  return {
    code: '600000',
    name: '浦发银行',
    as_of: '2026-11-05',
    dataSourceStatus: 'ok',
    kline: { bars: [], count: 120, requested: 120, reason: null },
    quote: {
      quote: {
        name: '浦发银行',
        price: 9.87,
        prev_close: 10.09,
        change: -0.22,
        change_pct: -2.18,
        open: 10.09,
        high: 10.1,
        low: 9.82,
        volume: 62907800,
        amount: 6.2e9,
        timestamp: '20261105150003',
        source_detail: {
          peer: 'tickhub-cn',
          tool: 'tencent_quote_batch',
          fetched_at: '2026-11-05T15:00:03+08:00',
        },
      },
      reason: null,
    },
    financials: { financials: null, reason: null },
    ...overrides,
  }
}

describe('走势块（QUOT-449）', () => {
  it('行情条逐项对照 payload.quote，并带真实来源与取数时刻', () => {
    render(<TrendSection overview={overview()} code="600000" />)

    expect(screen.getByTestId('kline-card').getAttribute('data-symbol')).toBe('600000')
    expect(screen.getAllByText('9.870').length).toBeGreaterThan(0)
    expect(screen.getByText('-2.18%')).toBeTruthy()
    expect(screen.getByText('10.09')).toBeTruthy()
    expect(screen.getByText('10.10')).toBeTruthy()
    expect(screen.getByText('9.820')).toBeTruthy()
    expect(
      screen.getByText(/来源：tickhub-cn · tencent_quote_batch · 取数 2026-11-05 15:00/),
    ).toBeTruthy()
  })

  it('快照缺失时读数留 —，并写明原因，不编价', () => {
    render(
      <TrendSection
        overview={overview({
          quote: { quote: null, reason: 'tencent_quote_batch 没有返回 600000 的报价（停牌或上游未更新）' },
        })}
        code="600000"
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('实时快照未取到')
    expect(screen.getByRole('status').textContent).toContain('停牌或上游未更新')
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(screen.queryByText('9.870')).toBeNull()
  })

  it('K 线不足 2 根不出图，给原因与重试', () => {
    render(
      <TrendSection
        overview={overview({
          kline: { bars: [], count: 1, requested: 120, reason: '上游只返回 1 根日线' },
        })}
        code="600000"
      />,
    )

    expect(screen.queryByTestId('kline-card')).toBeNull()
    expect(screen.getByText('K 线不足 2 根，暂不绘图')).toBeTruthy()
    expect(screen.getByText('上游只返回 1 根日线')).toBeTruthy()
    expect(screen.getByRole('button', { name: /重试/ })).toBeTruthy()
  })
})
