// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobView, LadderEnvelope, WatchSnapshotPayload } from '../src/api'
import JournalScreen from '../src/screens/JournalScreen'
import ValidateScreen from '../src/screens/ValidateScreen'
import WatchScreen from '../src/screens/WatchScreen'

/**
 * QUOT-561 接线：原来只能靠命令行的产物面 / 永久 disabled 的按钮，现在都在页内
 * 走真实 HTTP 入口。本文件只断言「点了真的提交、结果照后端事实说」——
 * 成功不补成成功、失败不回落演示、不出现任何命令行指引。
 */

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown }

let calls: Call[]

function record(input: RequestInfo | URL, init: RequestInit = {}) {
  calls.push({
    url: String(input),
    method: init.method ?? 'GET',
    headers: (init.headers ?? {}) as unknown as Record<string, string>,
    body: init.body ? JSON.parse(String(init.body)) : null,
  })
}

/** 提交按钮挂在表单外（footer），jsdom 不会按 `form=` 转发点击，这里直接提交表单。 */
function submitTrade() {
  const form = document.querySelector('#ldst-trade-form') as HTMLFormElement
  fireEvent.submit(form)
}

beforeEach(() => {
  calls = []
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

// ---------------------------------------------------------------- 记一笔

const JOURNAL_STATS = {
  local_only: true,
  store: 'local_sqlite',
  data_source_status: 'ok',
  range: { from: null, to: null },
  total_trades: 0,
  buy_count: 0,
  sell_count: 0,
  closed_rounds: 0,
  wins: 0,
  losses: 0,
  flats: 0,
  win_rate: null,
  avg_win: null,
  avg_loss: null,
  profit_loss_ratio: null,
  realized_pnl: 0,
  unmatched_sell_qty: 0,
  deviation_review: { threshold_pct: 1, count: 0 },
}

const WRITTEN = {
  code: '600000',
  side: 'buy',
  price: 10.5,
  qty: 100,
  note: null,
  trade_date: '2026-11-12',
  plan_price: null,
}

describe('交易日志「记一笔」（QUOT-561）', () => {
  it('填写后提交 → POST /api/journal/trades（带来源证据头）→ 成功后刷新统计', async () => {
    const statsAfter = { ...JOURNAL_STATS, total_trades: 1, buy_count: 1 }
    let statsCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        record(input, init)
        const url = String(input)
        if (url.includes('/api/journal/trades') && (init.method ?? 'GET') === 'POST') {
          return json(WRITTEN)
        }
        if (url.includes('/api/journal/stats')) {
          statsCalls += 1
          return json(statsCalls === 1 ? JOURNAL_STATS : statsAfter)
        }
        return json({ detail: 'Not Found' }, 404)
      }),
    )

    render(
      <MemoryRouter initialEntries={['/journal']}>
        <JournalScreen />
      </MemoryRouter>,
    )

    const openers = await screen.findAllByRole('button', { name: '记一笔' })
    fireEvent.click(openers[0])
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(dialog.querySelector('#ldst-trade-code') as HTMLInputElement, {
      target: { value: '600000' },
    })
    fireEvent.change(dialog.querySelector('#ldst-trade-price') as HTMLInputElement, {
      target: { value: '10.5' },
    })
    fireEvent.change(dialog.querySelector('#ldst-trade-qty') as HTMLInputElement, {
      target: { value: '100' },
    })
    submitTrade()
    await new Promise((r) => setTimeout(r, 800))

    // 抽屉里的成功条：页面不整页重载（静默刷新），所以「已记到本机」不会被卸载掉
    await waitFor(() => {
      const inDrawer = document.querySelector('[role=dialog]')
      expect(inDrawer?.textContent).toContain('已记到本机')
    })
    const posted = calls.filter((call) => call.method === 'POST')
    expect(posted).toHaveLength(1)
    expect(posted[0].url).toContain('/api/journal/trades')
    expect(posted[0].headers['X-LDST-Client']).toBe('1')
    expect(posted[0].body).toMatchObject({ code: '600000', side: 'buy', price: 10.5, qty: 100 })
    // 写成功后统计重取一次（页面不自己算数字）
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes('/api/journal/stats')).length).toBeGreaterThan(1),
    )
  })

  it('服务端 400 原样说原因，不假装成功', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        record(input, init)
        const url = String(input)
        if (url.includes('/api/journal/trades') && (init.method ?? 'GET') === 'POST') {
          return json({ detail: 'price 必须大于 0' }, 400)
        }
        return json(JOURNAL_STATS)
      }),
    )

    render(
      <MemoryRouter initialEntries={['/journal']}>
        <JournalScreen />
      </MemoryRouter>,
    )

    const openers = await screen.findAllByRole('button', { name: '记一笔' })
    fireEvent.click(openers[0])
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(dialog.querySelector('#ldst-trade-code') as HTMLInputElement, {
      target: { value: '600000' },
    })
    fireEvent.change(dialog.querySelector('#ldst-trade-price') as HTMLInputElement, {
      target: { value: '10.5' },
    })
    fireEvent.change(dialog.querySelector('#ldst-trade-qty') as HTMLInputElement, {
      target: { value: '100' },
    })
    submitTrade()

    expect(await screen.findByText('没有记上')).toBeTruthy()
    expect(screen.getByText(/price 必须大于 0/)).toBeTruthy()
    expect(screen.queryByText(/已记到本机/)).toBeNull()
  })
})

// ---------------------------------------------------------------- 发一条测试通知

const WATCH_EMPTY: LadderEnvelope<WatchSnapshotPayload> = {
  domain: 'sentiment',
  topic: 'watch_snapshot',
  event_id: null,
  data_as_of: null,
  payload: { trade_date: null, rows: [], data_source_status: 'missing' },
  source: 'ladderstock',
  source_detail: 'watch/status.py: 未找到状态文件（盯盘守护进程未启动）',
  fetched_at: '2026-11-12T07:00:00Z',
  freshness: 'cached',
  confidence: 'low',
  source_status: 'partial',
}

function job(status: JobView['status'], extra: Partial<JobView> = {}): JobView {
  return {
    id: 'job-notify',
    peer: 'ldst',
    action: 'command',
    status,
    step: '执行中',
    progress: 0.5,
    started_at: '2026-11-12T07:00:00Z',
    ended_at: null,
    log_tail: [],
    error: null,
    ...extra,
  }
}

function stubNotify(sequence: JobView[]) {
  let polls = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      record(input, init)
      const url = String(input)
      if (url.includes('/api/watch/snapshot')) return json(WATCH_EMPTY)
      if (url.includes('/api/control/commands/notify') && (init.method ?? 'GET') === 'POST') {
        return json({ job_id: 'job-notify', status: 'queued' })
      }
      if (url.includes('/api/control/jobs/job-notify')) {
        const next = sequence[Math.min(polls, sequence.length - 1)]
        polls += 1
        return json(next)
      }
      return json({ detail: 'Not Found' }, 404)
    }),
  )
}

describe('盯盘「发一条测试通知」（QUOT-561）', () => {
  it('提交控制面 notify 命令；没送到时照实说「没送到」，不冒充成功', async () => {
    stubNotify([
      job('running'),
      job('ok', {
        progress: 1,
        ended_at: '2026-11-12T07:00:05Z',
        log_tail: ['通知没有送达：渠道不可用或后端缺失，原因见上方输出'],
      }),
    ])

    render(
      <MemoryRouter initialEntries={['/watch']}>
        <WatchScreen />
      </MemoryRouter>,
    )

    const button = await screen.findByRole('button', { name: '发一条测试通知' })
    expect((button as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(button)

    await waitFor(
      () => expect(document.body.textContent).toContain('通知没有送达'),
      { timeout: 5000 },
    )
    const posted = calls.filter((call) => call.method === 'POST')
    expect(posted).toHaveLength(1)
    expect(posted[0].url).toContain('/api/control/commands/notify')
    expect(posted[0].headers['X-LDST-Client']).toBe('1')
  }, 10000)

  it('真的送到了才说送到', async () => {
    stubNotify([job('ok', { progress: 1, log_tail: ['通知已送达本机渠道'] })])

    render(
      <MemoryRouter initialEntries={['/watch']}>
        <WatchScreen />
      </MemoryRouter>,
    )

    fireEvent.click(await screen.findByRole('button', { name: '发一条测试通知' }))
    await waitFor(
      () => expect(document.body.textContent).toContain('测试通知已送到本机渠道'),
      { timeout: 5000 },
    )
  }, 10000)
})

// ---------------------------------------------------------------- 运行收盘对账

const VALIDATION_EMPTY: LadderEnvelope<{ conditions: unknown[] }> = {
  domain: 'validation',
  topic: 'validation',
  event_id: null,
  data_as_of: null,
  payload: { conditions: [] },
  source: 'ladderstock',
  source_detail: 'validation/reconcile.py: 本日还没有对账结果',
  fetched_at: '2026-11-12T07:00:00Z',
  freshness: 'realtime',
  confidence: 'low',
  source_status: 'partial',
}

describe('条件与对账「运行收盘对账」（QUOT-561）', () => {
  it('15:00 后按钮可点，提交 validate 命令并在结束后重新读取结论', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date(2026, 10, 12, 16, 0, 0))

    let polls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        record(input, init)
        const url = String(input)
        if (url.includes('/api/validation')) return json(VALIDATION_EMPTY)
        if (url.includes('/api/control/commands/validate') && (init.method ?? 'GET') === 'POST') {
          return json({ job_id: 'job-validate', status: 'queued' })
        }
        if (url.includes('/api/control/jobs/job-validate')) {
          polls += 1
          return json(
            job('ok', {
              id: 'job-validate',
              action: 'command',
              progress: 1,
              log_tail: ['退出码 8：部分数据源失败或 AI 降级，结果已落盘'],
            }),
          )
        }
        return json({ detail: 'Not Found' }, 404)
      }),
    )

    render(
      <MemoryRouter initialEntries={['/validate']}>
        <ValidateScreen />
      </MemoryRouter>,
    )

    fireEvent.click(await screen.findByRole('radio', { name: '收盘对账' }))
    const buttons = await screen.findAllByRole('button', { name: '运行收盘对账' })
    const enabled = buttons.find((button) => !(button as HTMLButtonElement).disabled)
    expect(enabled).toBeTruthy()
    const validationReads = () => calls.filter((call) => call.url.includes('/api/validation')).length
    const readsBefore = validationReads()
    fireEvent.click(enabled as HTMLElement)

    await waitFor(
      () => expect(calls.some((call) => call.url.includes('/api/control/commands/validate'))).toBe(true),
      { timeout: 5000 },
    )
    const posted = calls.find(
      (call) => call.method === 'POST' && call.url.includes('/api/control/commands/validate'),
    )
    expect(posted?.headers['X-LDST-Client']).toBe('1')
    // 任务结束后重新读取 GET /api/validation（判定仍由后端给，前端不重算）
    await waitFor(() => expect(validationReads()).toBeGreaterThan(readsBefore), { timeout: 5000 })
    expect(polls).toBeGreaterThan(0)
  }, 10000)
})
