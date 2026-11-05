// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PoolScreen from '../src/screens/radar/PoolScreen'

/**
 * QUOT-421：候选池面板必须每 30 秒静默重取一次。
 * 验收口径是「60 秒内两次截图各自 fetched_at 前进」；这里用假时钟把一拍走完，
 * 同时钉住两条不变量：静默重取不回到骨架屏、单拍失败不推翻已上屏的数据。
 */

const POOL_DATE = '2026-11-05'

function poolResponse(fundFetchedAt: string, score: number): Response {
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
            score,
            parts: { strength: 60, standing: 80, capital: 70, theme: 50, turnover: 40 },
            absent_parts: [],
            raw: {
              boards: 5,
              turnover_rate: 12.3,
              height: 5,
              net_inflow: 30000000,
              theme_tags: ['机器人'],
              theme_board_change_pct: 8,
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
        fund_fetched_at: fundFetchedAt,
      },
    }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  )
}

function renderPool(path = `/pool?date=${POOL_DATE}`) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/pool" element={<PoolScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function headerText(text: RegExp): HTMLElement {
  // v1.17 单顶栏后 PageHeader 不再渲染 <header>，副标题是内容状态行里的 <p>
  const node = screen.getByText(text, { selector: 'p' })
  return node as HTMLElement
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('QUOT-421 候选池 30 秒自动刷新', () => {
  it('一拍之后新数值与 fetched_at 上屏，且不回到骨架屏', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(poolResponse(`${POOL_DATE}T10:31`, 41.7))
      .mockResolvedValueOnce(poolResponse(`${POOL_DATE}T10:32`, 93.2))
    vi.stubGlobal('fetch', fetchMock)

    renderPool()
    await tick(0)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(headerText(/当日实时 · 10:31/)).toBeTruthy()

    await tick(30_000)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(headerText(/当日实时 · 10:32/)).toBeTruthy()
    expect(screen.getByText('93.2')).toBeTruthy()
    // 静默重取不把整屏打回 loading 骨架
    expect(document.querySelector('[aria-busy="true"]')).toBeNull()
  })

  it('单拍失败保留上一帧数据，下一拍恢复', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(poolResponse(`${POOL_DATE}T10:31`, 41.7))
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValueOnce(poolResponse(`${POOL_DATE}T10:33`, 93.2))
    vi.stubGlobal('fetch', fetchMock)

    renderPool()
    await tick(0)
    expect(headerText(/当日实时 · 10:31/)).toBeTruthy()

    await tick(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    // 失败的一拍不能把已有数据换成错误页
    expect(screen.queryByText('候选池取数失败')).toBeNull()
    expect(headerText(/当日实时 · 10:31/)).toBeTruthy()

    await tick(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(headerText(/当日实时 · 10:33/)).toBeTruthy()
  })

  it('演示源不轮询（固定夹具，没有可刷新的远端）', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderPool('/pool?demo=1')
    await tick(0)
    await tick(90_000)

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('手动刷新失败保留旧数据，并给出可见反馈；重试成功后反馈消失', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(poolResponse(`${POOL_DATE}T10:31`, 41.7))
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValueOnce(poolResponse(`${POOL_DATE}T10:32`, 93.2))
    vi.stubGlobal('fetch', fetchMock)

    renderPool()
    await tick(0)
    expect(headerText(/当日实时 · 10:31/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await tick(0)

    // 失败必须可见，且不能把已上屏的数据换成错误页
    expect(screen.getByText('刷新失败')).toBeTruthy()
    expect(screen.getByText(/本机接口不可达/)).toBeTruthy()
    expect(screen.queryByText('候选池取数失败')).toBeNull()
    expect(headerText(/当日实时 · 10:31/)).toBeTruthy()
    expect(screen.getByText('41.7')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await tick(0)

    expect(screen.queryByText('刷新失败')).toBeNull()
    expect(headerText(/当日实时 · 10:32/)).toBeTruthy()
    expect(screen.getByText('93.2')).toBeTruthy()
  })
})
