// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import IndustryMatrixScreen from '../src/screens/industry/IndustryMatrixScreen'
import CalendarScreen from '../src/screens/radar/CalendarScreen'
import NewsScreen from '../src/screens/radar/NewsScreen'
import PoolScreen from '../src/screens/radar/PoolScreen'
import PositionsScreen from '../src/screens/radar/PositionsScreen'
import ResearchDetailScreen from '../src/screens/research/ResearchDetailScreen'
import ResearchReportScreen from '../src/screens/research/ResearchReportScreen'
import SignalsScreen from '../src/screens/signals/SignalsScreen'

/**
 * QUOT-319-B 屏侧两件事：
 * 1. B 族缺数不冒充真数（§8）：payload 没取到时，副标题写 `—`，不写 `0` / `0 只` / `0 个行业` / `0.0%`。
 * 2. `data_source_status` 上屏（§5.2 / §5.3）：radar 四屏 + research 两屏各一枚独立徽标，与水印并排不合并。
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/industry" element={<IndustryMatrixScreen />} />
        <Route path="/signals" element={<SignalsScreen />} />
        <Route path="/news" element={<NewsScreen />} />
        <Route path="/calendar" element={<CalendarScreen />} />
        <Route path="/pool" element={<PoolScreen />} />
        <Route path="/positions" element={<PositionsScreen />} />
        <Route path="/research/:code" element={<ResearchDetailScreen />} />
        <Route path="/research/:code/report" element={<ResearchReportScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

function stubNotFound() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })),
  )
}

/** 徽标本体是 Badge 的外层 span（内层 `.ldst-num` 才是文字）。 */
function badgeClass(text: string): string {
  const label = screen.getByText(text)
  const badge = label.parentElement
  expect(badge).not.toBeNull()
  return badge?.getAttribute('class') ?? ''
}

describe('B 族缺数不写 0（UI-SPEC §8）', () => {
  it('/signals 没取到载荷：副标题「命中 — 条」，不是「命中 0 条」', async () => {
    stubNotFound()
    renderAt('/signals')
    expect(await screen.findByText(/命中 — 条/)).toBeTruthy()
    expect(screen.queryByText(/命中 0 条/)).toBeNull()
  })

  it('/industry 没取到载荷：副标题「— 个行业」，不是「0 个行业」', async () => {
    stubNotFound()
    renderAt('/industry')
    expect(await screen.findByText(/— 个行业/)).toBeTruthy()
    expect(screen.queryByText(/0 个行业/)).toBeNull()
  })

  it('/positions 没取到载荷：副标题「— 只」，不是「0 只」', async () => {
    stubNotFound()
    renderAt('/positions')
    expect(await screen.findByText(/— 只/)).toBeTruthy()
    expect(screen.queryByText(/0 只/)).toBeNull()
  })

  it('/positions 载荷齐备时照常给真实计数', async () => {
    renderAt('/positions?demo=1')
    expect(await screen.findByText(/· \d+ 只 ·/)).toBeTruthy()
  })

  it('/pool 抽屉：后端没给某个分项权重 → 该格「—」，合计也是「—」，不画 0.0%', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({
          payload: {
            date: '2026-10-28',
            available: true,
            source_detail: 'ldst pool',
            height: 3,
            candidate_count: 1,
            candidates: [
              {
                code: '600001',
                name: '甲科技',
                score: 61.5,
                parts: { strength: 80, standing: 60, capital: 50, theme: 40 },
                absent_parts: ['turnover'],
              },
            ],
            // 换手权重后端没给：不是 0
            weights: { strength: 0.3, standing: 0.25, capital: 0.2, theme: 0.15 },
            formula: 'score = Σ(weight_i × part_i) / Σ(weight_i)',
            counts: {
              limit_pool_rows: 7,
              dropped_duplicates: 1,
              unknown_code: 0,
              theme_boards: 3,
              fund_flow_rows: 5,
            },
            missing: [],
            data_source_status: 'ok',
            data_as_of: '2026-10-28',
          },
        }),
      ),
    )
    renderAt('/pool')
    const table = await screen.findByRole('table')
    fireEvent.click(within(table).getByRole('button', { name: '展开' }))

    const dialog = await screen.findByRole('dialog')
    const rows = within(dialog).getAllByRole('row')
    const turnoverRow = rows.find((row) => within(row).queryByText('换手'))
    expect(turnoverRow).toBeTruthy()
    expect(within(turnoverRow as HTMLElement).getAllByRole('cell')[1].textContent).toBe('—')
    expect(within(dialog).queryByText('0.0%')).toBeNull()

    const totalRow = rows.find((row) => within(row).queryByText('合计'))
    expect(within(totalRow as HTMLElement).getAllByRole('cell')[1].textContent).toBe('—')
  })

  it('/pool 权重齐备时仍显示真实百分比', async () => {
    renderAt('/pool?demo=1')
    await screen.findByRole('table')
    fireEvent.click(screen.getAllByRole('button', { name: '展开' })[0])
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('30.0%')).toBeTruthy()
    expect(within(dialog).getByText('100.0%')).toBeTruthy()
  })

  it('/positions 输入非法：CLI 兜底命令给占位符，不给看似可执行的 0', async () => {
    renderAt('/positions?demo=1')
    fireEvent.click(await screen.findByRole('button', { name: /新增持仓/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/^代码/), { target: { value: '600001' } })
    fireEvent.change(within(dialog).getByLabelText(/^成本/), { target: { value: 'abc' } })
    fireEvent.change(within(dialog).getByLabelText(/^数量/), { target: { value: '' } })
    fireEvent.click(within(dialog).getByRole('button', { name: /新增持仓|保存修改/ }))

    expect(await within(dialog).findByText(/成本价必须大于 0/)).toBeTruthy()
    const hint = within(dialog).findByText(/ldst position add/)
    expect((await hint).textContent).toContain('<成本>')
    expect((await hint).textContent).not.toContain('--cost 0')
  })
})

describe('data_source_status 上屏（UI-SPEC §5.2 / §5.3）', () => {
  it('radar 四屏：ok 载荷给「数据完整」（ok 青绿）', async () => {
    for (const path of ['/news', '/calendar', '/pool', '/positions']) {
      const view = renderAt(`${path}?demo=1`)
      await screen.findByText('● 数据完整')
      expect(badgeClass('● 数据完整')).toContain('bg-ok-soft')
      view.unmount()
      cleanup()
    }
  })

  it('radar 四屏：partial 载荷给「部分缺失」（danger 橙红，不是 warn 黄）', async () => {
    for (const path of ['/news', '/calendar', '/pool']) {
      const view = renderAt(`${path}?demo=1&variant=partial`)
      await screen.findByText('◑ 部分缺失')
      expect(badgeClass('◑ 部分缺失')).toContain('bg-danger-soft')
      view.unmount()
      cleanup()
    }
    const positions = renderAt('/positions?demo=1&variant=partial')
    await screen.findByText('◑ 部分缺失')
    expect(badgeClass('◑ 部分缺失')).toContain('bg-danger-soft')
    positions.unmount()
  })

  it('research 两屏：降级态给「部分缺失」，与演示水印并排两枚', async () => {
    renderAt('/research/600519?demo=1&state=partial')
    await screen.findByText('◑ 部分缺失')
    expect(screen.getByText(/演示态：部分失败/)).toBeTruthy()
    expect(screen.getByText('演示数据（?demo=1）')).toBeTruthy()
  })

  it('research 报告页同样上屏', async () => {
    renderAt('/research/600519/report?demo=1&state=partial')
    await screen.findByText('◑ 部分缺失')
  })
})
