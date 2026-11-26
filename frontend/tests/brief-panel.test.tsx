// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import BriefPanel from '../src/components/brief/BriefPanel'
import type { BriefPayload, BriefView } from '../src/api/brief'

/**
 * F11 今日简报面板：
 * 1. 三份简报各自显示状态、条目数与**出处**；
 * 2. skipped（无持仓/周末）如实显示原因，不画假条目；
 * 3. 条目超过 4 行时提示「另有 N 行」，标题与出处不截断。
 */

const { fetchBriefToday, runBrief, updateBriefConfig } = vi.hoisted(() => ({
  fetchBriefToday: vi.fn(),
  runBrief: vi.fn(),
  updateBriefConfig: vi.fn(),
}))

vi.mock('../src/api/brief', () => ({ fetchBriefToday, runBrief, updateBriefConfig }))

function view(overrides: Partial<BriefView> = {}): BriefView {
  return {
    job: 'brief:morning',
    kind: 'morning',
    label: '晨报',
    trade_day: '2026-11-20',
    status: 'ok',
    detail: '晨报 共 5 条，推送 5 条',
    generated_at: '2026-11-20T09:10:00+08:00',
    title: '晨报 2026-11-20',
    items: [
      {
        kind: 'quotes',
        title: '盘前行情',
        lines: ['自选 3 只 · 持仓 2 只', '600519 +1.2%'],
        source: 'watch/digest · premarket · ok',
        link: '/my-stocks',
        fingerprint: 'aaaaaaaaaaaaaaaa',
      },
    ],
    ...overrides,
  }
}

function payload(overrides: Partial<BriefPayload> = {}): BriefPayload {
  return {
    local_only: true,
    day: '2026-11-20',
    settings: { notify: false, channel: 'console', threshold: 5 },
    briefs: [view(), view({ kind: 'close_review', label: '收盘复盘', job: 'brief:close', title: '收盘复盘 2026-11-20' })],
    published: [],
    runs: [],
    ...overrides,
  }
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={['/inbox']}>
      <BriefPanel />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
})

describe('BriefPanel', () => {
  it('renders each brief with its status, count and source', async () => {
    fetchBriefToday.mockResolvedValue(payload())
    renderPanel()

    await waitFor(() => expect(screen.getByText('今日简报')).toBeTruthy())
    // 两份简报都在
    expect(screen.getByText('晨报')).toBeTruthy()
    expect(screen.getByText('收盘复盘')).toBeTruthy()
    // 出处必须显示，读者要能顺着回查（两份简报各有一条，所以是复数）
    expect(screen.getAllByText(/watch\/digest · premarket · ok/).length).toBe(2)
    expect(screen.getAllByText(/出处：/).length).toBe(2)
    // 条目数写进标题行
    expect(screen.getAllByText(/1 条 · /).length).toBe(2)
  })

  it('shows skipped reason without fake items', async () => {
    fetchBriefToday.mockResolvedValue(
      payload({
        briefs: [
          view({
            kind: 'position_alerts',
            label: '持仓异动',
            job: 'brief:alerts',
            status: 'skipped',
            detail: '本机没有持仓或自选，异动检查无对象',
            items: [],
          }),
        ],
      }),
    )
    renderPanel()

    await waitFor(() => expect(screen.getByText('持仓异动')).toBeTruthy())
    expect(screen.getByText('跳过')).toBeTruthy()
    expect(screen.getByText('本机没有持仓或自选，异动检查无对象')).toBeTruthy()
    expect(screen.getByText(/这一份没有条目/)).toBeTruthy()
  })

  it('warns when a run pushed nothing because everything was already sent', async () => {
    fetchBriefToday.mockResolvedValue(payload())
    runBrief.mockResolvedValue({
      ok: true,
      kind: 'morning',
      trade_day: '2026-11-20',
      title: '晨报 2026-11-20',
      detail: '晨报 共 5 条，推送 5 条',
      pushed: [],
      skipped: [{ title: '盘前行情', fingerprint: 'aaaaaaaaaaaaaaaa', reason: '同日已推送' }],
      delivered: [],
      state_path: 'D:/data/brief/2026-11-20.json',
      brief: view(),
    })
    renderPanel()

    await waitFor(() => expect(screen.getAllByText('生成并落提醒')[0]).toBeTruthy())
    screen.getAllByText('生成并落提醒')[0].click()

    await waitFor(() => expect(screen.getByText(/今日已推送过，未重发/)).toBeTruthy())
  })
})
