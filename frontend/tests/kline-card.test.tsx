// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { KlineRequest } from '../src/api/research'
import type { KlineResult, KlineView } from '../src/charts/klineView'
import { KlineCard } from '../src/components/KlineCard'

/** ECharts 画在 `<canvas>` 上，jsdom 没有 2D 上下文：换成记录高度的替身，只断言卡片结构。 */
vi.mock('../src/charts/EChart', () => ({
  EChart: ({ height }: { height?: number }) => (
    <div data-testid="echart" data-height={height} />
  ),
}))

afterEach(cleanup)

const DATES = ['2026-01-02', '2026-01-05', '2026-01-06']

function view(overrides: Partial<KlineView> = {}): KlineView {
  return {
    symbol: '600519',
    market: 'a_share',
    marketLabel: 'A 股',
    period: 'daily',
    periodLabel: '日线',
    adjust: 'qfq',
    adjustLabel: '前复权',
    source: 'TickHub-CN / tencent_kline',
    fetchedAt: '2026-11-05T15:36:00+08:00',
    volumeUnit: '股',
    bars: DATES.length,
    first: DATES[0],
    last: DATES[DATES.length - 1],
    notes: ['均线按本次返回的窗口计算，窗口不足的值为空'],
    series: {
      dates: DATES,
      open: [10, 10.2, 10.1],
      high: [10.3, 10.4, 10.2],
      low: [9.8, 10, 9.7],
      close: [10.2, 10.1, 10.4],
      volume: [1_000_000, 1_200_000, 1_500_000],
      change: [null, -0.0098, 0.0297],
      ma: {
        '5': [null, null, 10.23],
        '10': [null, null, null],
        '20': [null, null, null],
        '60': [null, null, null],
      },
    },
    ...overrides,
  }
}

const okResult = (): KlineResult => ({ ok: true, view: view() })

describe('KlineCard', () => {
  it('A 股：渲染报价条、420 高图表与日/周/月 + 复权切换', async () => {
    const loader = vi.fn(async () => okResult())
    render(<KlineCard symbol="600519" title="贵州茅台" loader={loader} />)

    expect((await screen.findByTestId('echart')).getAttribute('data-height')).toBe('420')
    expect(screen.getByText('10.40')).toBeTruthy()
    expect(screen.getByText('+2.97%')).toBeTruthy()
    expect(screen.getByRole('radiogroup', { name: '周期' })).toBeTruthy()
    expect(screen.getByRole('radiogroup', { name: '复权' })).toBeTruthy()
    expect(screen.getByText(/均线按本次返回的窗口计算/)).toBeTruthy()
    expect(loader).toHaveBeenCalledWith(
      { symbol: '600519', period: 'daily', adjust: 'qfq' },
      expect.any(AbortSignal),
    )
  })

  it('非 A 股：不显示复权，也不把复权参数传给上游', async () => {
    const loader = vi.fn(async (): Promise<KlineResult> => ({
      ok: true,
      view: view({ symbol: 'AAPL', market: 'us', marketLabel: '美股', adjustLabel: '' }),
    }))
    render(<KlineCard symbol="AAPL" loader={loader} />)

    await screen.findByTestId('echart')
    expect(screen.queryByRole('radiogroup', { name: '复权' })).toBeNull()
    expect(loader).toHaveBeenCalledWith(
      { symbol: 'AAPL', period: 'daily' },
      expect.any(AbortSignal),
    )
  })

  it('切周期：旧请求被 abort，新请求带新周期', async () => {
    const signals: AbortSignal[] = []
    const loader = vi.fn(async (request: KlineRequest, signal?: AbortSignal) => {
      signals.push(signal as AbortSignal)
      return request.period === 'weekly'
        ? { ok: true as const, view: view({ period: 'weekly', periodLabel: '周线' }) }
        : okResult()
    })
    render(<KlineCard symbol="600519" loader={loader} />)
    await screen.findByTestId('echart')

    fireEvent.click(screen.getByRole('radio', { name: '周线' }))
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2))
    expect(signals[0].aborted).toBe(true)
    expect(loader).toHaveBeenLastCalledWith(
      { symbol: '600519', period: 'weekly', adjust: 'qfq' },
      expect.any(AbortSignal),
    )
  })

  it('接口说画不了：原样出错误文案，不画图', async () => {
    const loader = vi.fn(async (): Promise<KlineResult> => ({
      ok: false,
      error: '上游没有这批数据',
    }))
    render(<KlineCard symbol="600519" loader={loader} />)

    expect((await screen.findByRole('alert')).textContent).toContain('上游没有这批数据')
    expect(screen.queryByTestId('echart')).toBeNull()
  })
})
