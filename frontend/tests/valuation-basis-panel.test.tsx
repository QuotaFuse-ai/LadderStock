// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import type { StructuredMetrics } from '../src/api/financecoffer'
import {
  ValuationBasisPanel,
  type ValuationLoader,
} from '../src/components/valuation/ValuationBasisPanel'

afterEach(cleanup)

function sample(overrides: Partial<StructuredMetrics> = {}): StructuredMetrics {
  return {
    symbol: '600158',
    basis: 'snapshot',
    default_basis: 'snapshot',
    available_bases: ['snapshot', 'reference'],
    pe: 15.25,
    pe_source: 'quote.pe_ttm',
    pe_reason: null,
    pe_cross: null,
    pb: null,
    pb_reason: 'no_equity',
    roe: null,
    roe_reason: 'equity_unavailable',
    price_date: '2025-06-30',
    price_close: '10.5000',
    report_date: null,
    quarter_basis: null,
    ...overrides,
  }
}

function loaderOf(value: StructuredMetrics | ((basis: string) => StructuredMetrics)): ValuationLoader {
  return vi.fn(async (_symbol: string, basis: string) =>
    typeof value === 'function' ? value(basis) : value,
  )
}

describe('ValuationBasisPanel · 口径切换与回显', () => {
  it('默认请求 snapshot，展示数值与两条口径回显；缺失指标显示「暂无此数据」而不补 0', async () => {
    const loader = loaderOf(sample())
    render(<ValuationBasisPanel symbol="600158" loader={loader} />)

    expect(await screen.findByText('15.25')).toBeTruthy()
    expect(loader).toHaveBeenCalledWith('600158', 'snapshot', expect.any(AbortSignal))

    expect(screen.getByText(/生效口径/).textContent).toContain('snapshot')
    expect(screen.getByText(/服务端默认/).textContent).toContain('snapshot')

    // PB / ROE 缺失：两条「暂无此数据」，且没有把缺失值渲染成 0
    const missing = screen.getAllByText(/暂无此数据/)
    expect(missing).toHaveLength(2)
    expect(screen.queryByText('0.00')).toBeNull()
    expect(screen.getAllByText(/无净资产数据/)).toHaveLength(2)
  })

  it('切换到 reference：带新口径重新取数，并展示响应回显的 basis / default_basis', async () => {
    const loader = loaderOf((basis) =>
      basis === 'reference'
        ? sample({
            basis: 'reference',
            default_basis: 'snapshot',
            pe: 10,
            pe_source: 'deducted_annualized',
            pb: 1.2,
            pb_reason: null,
            roe: null,
            roe_reason: 'equity_unavailable',
            quarter_basis: 'single_quarter_deducted_x4',
            report_date: '2026-09-30',
          })
        : sample(),
    )
    render(<ValuationBasisPanel symbol="600158" loader={loader} />)
    await screen.findByText('15.25')

    fireEvent.click(screen.getByRole('radio', { name: /reference/ }))

    await waitFor(() =>
      expect(loader).toHaveBeenLastCalledWith('600158', 'reference', expect.any(AbortSignal)),
    )
    expect(await screen.findByText('10.00')).toBeTruthy()
    expect(screen.getByText('1.20')).toBeTruthy()
    // 切换后不残留上一口径的 PE
    expect(screen.queryByText('15.25')).toBeNull()

    expect(screen.getByText(/生效口径/).textContent).toContain('reference')
    expect(screen.getByText(/服务端默认/).textContent).toContain('snapshot')
    expect(screen.getByText(/单季扣非净利润 × 4 年化/)).toBeTruthy()
  })

  it('basis_not_built：单独文案，三个指标全缺失，且不拿 pe_cross / 另一口径补位', async () => {
    const loader = loaderOf((basis) =>
      basis === 'reference'
        ? sample({
            basis: 'reference',
            default_basis: 'snapshot',
            available_bases: ['snapshot'],
            pe: null,
            pe_source: null,
            pe_reason: 'basis_not_built',
            pe_cross: 15.25,
            pb: null,
            pb_reason: 'basis_not_built',
            roe: null,
            roe_reason: 'basis_not_built',
          })
        : sample(),
    )
    render(<ValuationBasisPanel symbol="600158" loader={loader} />)
    await screen.findByText('15.25')

    fireEvent.click(screen.getByRole('radio', { name: /reference/ }))

    expect(await screen.findByText('所选口径未构建')).toBeTruthy()
    expect(screen.getByText(/不会回落到其他口径/)).toBeTruthy()
    expect(screen.getAllByText(/暂无此数据/)).toHaveLength(3)
    expect(screen.getAllByText(/该口径未构建/).length).toBeGreaterThan(0)
    // pe_cross 是另一口径的对照值，v1 不展示
    expect(screen.queryByText('15.25')).toBeNull()
  })

  it('服务端生效口径与点选值不同：以响应 basis 为准并给出提示', async () => {
    const loader = loaderOf(
      sample({
        basis: 'reference',
        default_basis: 'snapshot',
        available_bases: ['snapshot', 'reference'],
        pe: 10,
        pe_source: 'deducted_annualized',
      }),
    )
    render(<ValuationBasisPanel symbol="600158" loader={loader} />)

    expect(await screen.findByText(/服务端生效口径为/)).toBeTruthy()
    expect(screen.getByText(/服务端生效口径为/).textContent).toContain('reference')
    expect(screen.getByText('10.00')).toBeTruthy()
  })

  it('取数失败：显示可重试的错误块，重试后恢复', async () => {
    const loader = vi
      .fn<ValuationLoader>()
      .mockRejectedValueOnce(new ApiError('not-connected', '不可达'))
      .mockResolvedValueOnce(sample())

    render(<ValuationBasisPanel symbol="600158" loader={loader} />)

    expect(await screen.findByText('FinanceCoffer 未接入')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '重新取数' }))

    expect(await screen.findByText('15.25')).toBeTruthy()
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('没有标的时不发请求，显示空态', () => {
    const loader = loaderOf(sample())
    render(<ValuationBasisPanel symbol={null} loader={loader} />)

    expect(screen.getByText('未选择标的')).toBeTruthy()
    expect(loader).not.toHaveBeenCalled()
  })

  it('ROE 按小数换算成百分比展示', async () => {
    const loader = loaderOf(sample({ roe: 0.152, roe_reason: null }))
    render(<ValuationBasisPanel symbol="600158" loader={loader} />)

    expect(await screen.findByText('15.20%')).toBeTruthy()
  })
})
