// @vitest-environment jsdom
/**
 * C 包后续单（DEC-090 §4）：龙虎榜 / 行业 / 候选池 / 周报四页接同一张表格导出面。
 *
 * 这里钉两条不变量：
 * - 「导出的是页面当前渲染的行」：demo 涨停样本里缺失的字段导出**空串**，不补 0；
 * - 「同一面」：四页都走 `downloadTableExport`（列白名单由页面声明），不是各自造轮子。
 *
 * 行业矩阵 / 周报页的接线由 typecheck 与各自页面测试兜住（渲染成本高），本文件
 * 钉最容易被静默接反的两个：缺失值口径与导出面唯一。
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LimitUpScreen from '../src/screens/limitup/LimitUpScreen'
import PoolScreen from '../src/screens/radar/PoolScreen'
import { downloadTableExport } from '../src/api/tableExport'

vi.mock('../src/api/tableExport', () => ({
  downloadTableExport: vi.fn(async () => undefined),
}))

beforeEach(() => {
  ;(downloadTableExport as ReturnType<typeof vi.fn>).mockClear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const POOL_DATE = '2026-11-05'

function poolResponse(): Response {
  return new Response(
    JSON.stringify({
      ok: true,
      domain: 'sentiment',
      topic: 'candidate_pool',
      data_as_of: POOL_DATE,
      fetched_at: `${POOL_DATE}T10:33:00+08:00`,
      freshness: 'realtime',
      source_status: 'ok',
      source_detail: 'limit_pool: archive（1 条）; concept_board: 1 条; fund_flow: 1 条',
      payload: {
        date: POOL_DATE,
        available: true,
        reason: null,
        source_detail: 'limit_pool: archive（1 条）; concept_board: 1 条; fund_flow: 1 条',
        height: 5,
        candidate_count: 1,
        candidates: [
          {
            code: '600001',
            name: '甲科技',
            score: 62.5,
            parts: { strength: 60, standing: 80, capital: 70, theme: 50, turnover: 40 },
            absent_parts: ['theme'],
            raw: {
              boards: 5,
              turnover_rate: 12.3,
              height: 5,
              net_inflow: 30000000,
              theme_tags: [],
              theme_board_change_pct: null,
            },
          },
        ],
        weights: { strength: 0.3, standing: 0.25, capital: 0.2, theme: 0.15, turnover: 0.1 },
        formula: 'score = Σ(weight_i × part_i) / Σ(weight_i)',
        counts: {
          limit_pool_rows: 1,
          dropped_duplicates: 0,
          unknown_code: 0,
          theme_boards: 1,
          fund_flow_rows: 1,
        },
        missing: [],
        data_source_status: 'ok',
        data_as_of: POOL_DATE,
        fund_freshness: 'realtime',
        fund_fetched_at: `${POOL_DATE}T10:30:00+08:00`,
      },
    }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  )
}

function renderAt(path: string, element: React.ReactElement, route: string): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={route} element={element} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('涨停样本导出（DEC-090 后续单）', () => {
  it('demo 视图点导出：行集同源，缺失字段导出空串不补 0', async () => {
    renderAt('/limit-up?demo=1', <LimitUpScreen />, '/limit-up')
    await screen.findByRole('heading', { name: '涨停样本', level: 1 })
    await screen.findByRole('table')

    fireEvent.click(screen.getByRole('button', { name: '导出 CSV' }))

    await waitFor(() => expect(downloadTableExport).toHaveBeenCalledTimes(1))
    const call = (downloadTableExport as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      name: string
      format: string
      rows: Array<Record<string, unknown>>
      only?: string[]
    }
    expect(call.name).toBe('limit-pool')
    expect(call.format).toBe('csv')
    // 600519 在 demo 里名称有值、连板/封板/原因缺失：缺失字段导出空串，不补 0
    const maotai = call.rows.find((row) => row.code === '600519') as Record<string, unknown> | undefined
    expect(maotai).toBeTruthy()
    expect(maotai?.name).toBe('贵州茅台')
    expect(maotai?.streak).toBe('')
    expect(maotai?.first_seal_time).toBe('')
    expect(maotai?.reason).toBe('')
    // 白名单不含任何内部字段
    expect(call.only).toEqual([
      'code', 'name', 'streak', 'first_seal_time', 'last_seal_time', 'reason',
    ])
  })
})

describe('候选池导出（DEC-090 后续单）', () => {
  it('CSV 与既有「导出本地文件」并存，行集与分项缺失一致', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: unknown) => {
      const url = String(input)
      if (url.startsWith('/api/pool')) return poolResponse()
      return new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })
    }))
    renderAt(`/pool?date=${POOL_DATE}`, <PoolScreen />, '/pool')

    const csvButton = await screen.findByRole('button', { name: '导出 CSV' })
    fireEvent.click(csvButton)

    await waitFor(() => expect(downloadTableExport).toHaveBeenCalledTimes(1))
    const call = (downloadTableExport as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      name: string
      rows: Array<Record<string, unknown>>
      only?: string[]
    }
    expect(call.name).toBe('candidate-pool')
    expect(call.rows).toHaveLength(1)
    expect(call.rows[0].code).toBe('600001')
    expect(call.rows[0].score).toBe(62.5)
    // 缺失分项（theme 未进当日概念榜）导出空串，不补 0
    expect(call.rows[0].theme).toBe('')
    expect(call.only).toContain('score')
    expect(call.only).toContain('absent_parts')
    // 既有 JSON 本地导出按钮仍在（同屏并存）
    expect(screen.getByRole('button', { name: '导出本地文件' })).toBeTruthy()
  })
})
