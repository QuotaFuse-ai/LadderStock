// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CoveragePanel from '../src/components/config/CoveragePanel'
import type { CoverageBoard, CoverageRow } from '../src/api/coverage'

/**
 * F12 数据覆盖看板：
 * 1. 三个数字分开显示（目录 62 / 正常 / 需要处理），不糊成一个「覆盖率」；
 * 2. 每行缺口显示自己的原话（行数、最新日期、池内覆盖、原因）；
 * 3. 补采是两段式：先出计划，再点「确认补采」才真跑；dry-run 结果写「planned」。
 */

const { fetchCoverageBoard, fetchCoveragePlan, runCoverageBackfill } = vi.hoisted(() => ({
  fetchCoverageBoard: vi.fn(),
  fetchCoveragePlan: vi.fn(),
  runCoverageBackfill: vi.fn(),
}))

vi.mock('../src/api/coverage', () => ({
  fetchCoverageBoard,
  fetchCoveragePlan,
  runCoverageBackfill,
}))

const LABELS: Record<string, string> = {
  ok: '正常',
  partial: '池内覆盖不全',
  stale: '最近更新过旧',
  empty: '表在但零行',
  never: '从未入库（表不存在或无行）',
  unregistered: '未在 sync.yaml 注册主源',
}

function row(overrides: Partial<CoverageRow> = {}): CoverageRow {
  return {
    dataset: 'daily_k',
    table: 'daily_k',
    registered: true,
    primary: 'tickhub_kline',
    rows: 63973,
    codes: 9496,
    latest: '2026-11-20',
    age_days: 1,
    date_column: 'trade_date',
    pool_codes: 85,
    pool_covered: 85,
    pool_relevant: true,
    coverage: 1,
    state: 'ok',
    reason: '',
    ...overrides,
  }
}

function board(overrides: Partial<CoverageBoard> = {}): CoverageBoard {
  const gapA = row({
    dataset: 'daily_k',
    state: 'stale',
    latest: '2026-11-17',
    age_days: 4,
    reason: '最近更新过旧：最近 2026-11-17（4 天前，阈值 3 天）',
  })
  const gapB = row({
    dataset: 'announcement_cninfo',
    rows: 0,
    codes: 0,
    latest: null,
    pool_covered: 0,
    coverage: 0,
    state: 'never',
    reason: '从未入库（表不存在或无行）',
  })
  return {
    local_only: true,
    generated_at: '2026-11-21T15:00:00+08:00',
    elapsed_ms: 270,
    pool: { size: 85, codes: ['600519'], truncated: true },
    declared: 34,
    datasets: [row(), gapA, gapB],
    gaps: [gapB, gapA],
    unregistered: [],
    summary: {
      total: 62,
      counts: { ok: 19, partial: 2, stale: 0, never: 14, unregistered: 27 },
      ok: 19,
      needs_attention: 16,
      unregistered: 27,
    },
    labels: LABELS,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
})

describe('CoveragePanel', () => {
  it('keeps the three numbers apart and lists gaps with their own reasons', async () => {
    fetchCoverageBoard.mockResolvedValue(board())
    render(<CoveragePanel />)

    await waitFor(() => expect(screen.getByText('数据集目录')).toBeTruthy())
    expect(screen.getByText('62')).toBeTruthy()
    expect(screen.getByText('19')).toBeTruthy()
    expect(screen.getByText('16')).toBeTruthy()
    expect(
      screen.getByText(
        '正常 19 · 池内覆盖不全 2 · 从未入库（表不存在或无行） 14 · 未在 sync.yaml 注册主源 27 · 耗时 270 ms',
      ),
    ).toBeTruthy()

    // 缺口各自的原话；状态列用的是短标签（未入库 / 未盖满），完整说法在 reason
    expect(screen.getByText('最近更新过旧：最近 2026-11-17（4 天前，阈值 3 天）')).toBeTruthy()
    expect(screen.getAllByText('从未入库（表不存在或无行）').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('未入库').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('85/85（100.0%）').length).toBeGreaterThanOrEqual(1)
  })

  it('backfill is two-step: plan first, then confirm', async () => {
    fetchCoverageBoard.mockResolvedValue(board())
    fetchCoveragePlan.mockResolvedValue({
      dataset: 'daily_k',
      primary: 'tickhub_kline',
      fallback: ['tickhub_tdx_package'],
      per_code: true,
      codes: ['600519', '000858'],
      code_count: 2,
      max_codes: 100,
      requests_hint: '约 2 只 × 1 批（provider_registry 自带限流与冷却）',
    })
    runCoverageBackfill.mockResolvedValue({
      dataset: 'daily_k',
      primary: 'tickhub_kline',
      fallback: ['tickhub_tdx_package'],
      per_code: true,
      codes: ['600519', '000858'],
      code_count: 2,
      max_codes: 100,
      requests_hint: '约 2 只 × 1 批',
      execute: true,
      root: 'D:/data',
      status: 'planned',
      hint: '这是计划',
    })
    render(<CoveragePanel />)

    await waitFor(() => expect(screen.getByText('看全部')).toBeTruthy())
    screen.getAllByText('补采')[0].click()

    await waitFor(() => expect(screen.getByText('补采 daily_k')).toBeTruthy())
    // 主源名在缺口行里也出现过，用 All 避免歧义
    expect(screen.getAllByText('tickhub_kline').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/600519、000858/)).toBeTruthy()
    expect(runCoverageBackfill).not.toHaveBeenCalled()

    screen.getByText('确认补采（会真发请求）').click()
    await waitFor(() => expect(runCoverageBackfill).toHaveBeenCalledTimes(1))
    expect(runCoverageBackfill.mock.calls[0][1]).toMatchObject({ execute: true })
  })

  it('shows an honest message when nothing is missing', async () => {
    fetchCoverageBoard.mockResolvedValue(
      board({
        datasets: [row()],
        gaps: [],
        unregistered: [],
        summary: {
          total: 62,
          counts: { ok: 62 },
          ok: 62,
          needs_attention: 0,
          unregistered: 0,
        },
      }),
    )
    render(<CoveragePanel />)

    await waitFor(() => expect(screen.getByText(/没有缺口——62 个数据集/)).toBeTruthy())
    expect(screen.getByText('0 个缺口，按严重度排序')).toBeTruthy()
  })
})
