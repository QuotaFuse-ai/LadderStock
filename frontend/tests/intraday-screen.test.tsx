// @vitest-environment jsdom
/**
 * 「盯盘 · 竞价与盘中」（QUOT-302）：六种时点状态、五种界面状态、
 * 「不补跑」不是失败、缺数据是「—」不是 0、时点表来自后端。
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Layout from '../src/components/Layout'
import IntradayScreen from '../src/screens/intraday/IntradayScreen'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/intraday" element={<IntradayScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

interface RowSeed {
  slot: string
  status: string
  recorded?: boolean
  available?: boolean
  price_field?: string | null
  stocks?: number | null
  fresh?: number | null
  detail?: string | null
  reason?: string | null
}

function row(seed: RowSeed) {
  const available = seed.available ?? false
  return {
    slot: seed.slot,
    status: seed.status,
    recorded: seed.recorded ?? true,
    available,
    reason: available ? null : (seed.reason ?? null),
    file: available ? `${seed.slot.replace(':', '')}.json` : null,
    detail: seed.detail ?? null,
    price_field: available ? (seed.price_field ?? 'price') : null,
    stocks: available ? (seed.stocks ?? 3) : null,
    fresh: available ? (seed.fresh ?? 3) : null,
    at: seed.recorded === false ? null : `2026-10-30T${seed.slot}:00+08:00`,
  }
}

type RowItem = ReturnType<typeof row>

function summaryEnvelope(rows: RowItem[]) {
  const held = rows.filter((item) => item.available).length
  return {
    domain: 'sentiment',
    topic: 'intraday',
    data_as_of: '2026-10-30',
    payload: {
      date: '2026-10-30',
      available: held > 0,
      reason: held > 0 ? null : '6 个时点都没有快照文件',
      has_status_file: true,
      slot_count: rows.length,
      snapshot_count: held,
      status_updated_at: '2026-10-30T15:00:00+08:00',
      slots: rows,
      data_source_status: held > 0 ? 'ok' : 'missing',
    },
    source_detail: `local intraday dir: 2026-10-30/（${held}/6 个时点有快照）`,
  }
}

function snapshotEnvelope(slot: string, available: boolean) {
  if (!available) {
    return {
      payload: {
        date: '2026-10-30',
        slot,
        available: false,
        reason: `${slot.replace(':', '')}.json 不存在：2026-10-30 的 ${slot} 没有落盘快照`,
        snapshot: null,
        data_source_status: 'missing',
      },
    }
  }
  return {
    payload: {
      date: '2026-10-30',
      slot,
      available: true,
      reason: null,
      data_source_status: 'ok',
      snapshot: {
        date: '2026-10-30',
        slot,
        captured_at: `2026-10-30T${slot}:06+08:00`,
        price_field: slot === '09:25' ? 'open' : 'price',
        source: { dataset: 'spot_quote', tool: 'tencent_quote_batch', source_detail: 'stub', trail: ['stub'] },
        yesterday_limit_up: {
          count: 3,
          fresh: 2,
          stale: 1,
          missing: ['002195'],
          overall: { count: 3, avg_change_pct: 2.43, up: 2, flat: 0, down: 1, limit_up: 1 },
          by_board: [{ board: '首板', count: 2, avg_change_pct: 1.1 }],
          top_board: { streak: 4, stocks: [] },
          stocks: [
            {
              code: '001219',
              yesterday_streak: 4,
              reading_field: 'price',
              reading: 19.94,
              change_pct: 7.83,
              at_limit_up: true,
              fresh: true,
              reason: null,
            },
            {
              code: '600745',
              yesterday_streak: null,
              reading_field: 'price',
              reading: null,
              change_pct: -3.77,
              at_limit_up: null,
              fresh: false,
              reason: '报价日期不是今天',
            },
          ],
        },
        sentiment: {
          available: true,
          limit_up: 47,
          broken: 31,
          limit_down: 8,
          max_streak: 5,
          seal_rate_pct: 60.3,
          broken_rate_pct: 39.7,
          one_to_two: { available: true, yesterday_first_board: 2, today_second_board: 2, promoted: 0, rate_pct: 0.0 },
        },
      },
    },
  }
}

/** 按 URL 分流的 fetch 桩：summary 一行一答，snapshot 按时点回答。 */
function stubFetch(rows: RowItem[]) {
  const availableBySlot = new Map(rows.map((item) => [item.slot, item.available]))
  const fetchMock = vi.fn(async (input: unknown) => {
    const url = String(input)
    if (url.startsWith('/api/intraday/snapshot')) {
      const slot = new URL(url, 'http://localhost').searchParams.get('slot') ?? ''
      return new Response(JSON.stringify(snapshotEnvelope(slot, availableBySlot.get(slot) ?? false)), {
        headers: { 'content-type': 'application/json' },
      })
    }
    return new Response(JSON.stringify(summaryEnvelope(rows)), {
      headers: { 'content-type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function railCells(): HTMLElement[] {
  return screen.getAllByRole('button', { name: /^\d{2}:\d{2} / })
}

const SIX_STATES: RowSeed[] = [
  { slot: '09:25', status: 'ok', available: true, price_field: 'open' },
  { slot: '09:35', status: 'degraded', available: true },
  { slot: '10:00', status: 'already', available: true },
  { slot: '11:30', status: 'skipped', detail: '11:39:12 不在有效窗口 11:30:00–11:37:59，未落快照（不补跑）' },
  { slot: '14:00', status: 'missing', detail: '昨日涨停池为空，没有可存的读数' },
  { slot: '15:00', status: 'failed', detail: '快照抓取失败：peer 拒绝连接' },
]

describe('六种时点状态', () => {
  it('ok / degraded / already / skipped / missing / failed 各有独立文案 + 图形', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30')
    await waitFor(() => expect(railCells()).toHaveLength(6))

    const expected: Array<[string, string]> = [
      ['09:25', '已写入'],
      ['09:35', '降级写入'],
      ['10:00', '已存在·未覆盖'],
      ['11:30', '已过窗口，不补跑'],
      ['14:00', '未取到'],
      ['15:00', '抓取失败'],
    ]
    for (const [slot, label] of expected) {
      const cell = screen.getByRole('button', { name: `${slot} ${label}` })
      // 双通道：图形（svg）与文字同时存在，不能只有颜色
      expect(cell.querySelector('svg')).toBeTruthy()
    }
    // 整屏没有把任何一格当成错误态：页面上唯一的提示条是「部分时点没有落盘」
    for (const alert of screen.getAllByRole('alert')) {
      expect(alert.textContent).toContain('部分时点没有落盘')
    }
  })

  it('skipped 显示「已过窗口，不补跑」，不渲染成加载失败或空数据', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30&slot=11:30')
    await waitFor(() => expect(railCells()).toHaveLength(6))
    expect(screen.getByRole('button', { name: '11:30 已过窗口，不补跑' })).toBeTruthy()
    expect(await screen.findByText(/窗口外不补跑/)).toBeTruthy()
    expect(screen.queryByText('快照取数失败')).toBeNull()
    expect(screen.queryByText('盘中快照取数失败')).toBeNull()
    // 也不是整屏空数据：只有「部分时点没有落盘」这一条提示
    expect(screen.queryByText('这一天没有落盘快照')).toBeNull()
    for (const alert of screen.getAllByRole('alert')) {
      expect(alert.textContent).toContain('部分时点没有落盘')
    }
  })

  it('status.json 里没有记录的时点说「当天没跑过」，与跑过但没读数分开', async () => {
    stubFetch(
      [
        { slot: '09:25', status: 'ok', available: true },
        { slot: '15:00', status: 'missing', recorded: false },
      ].map(row),
    )
    renderAt('/intraday?date=2026-10-30')
    await waitFor(() => expect(railCells()).toHaveLength(2))
    expect(screen.getByRole('button', { name: '15:00 当天没跑过' })).toBeTruthy()
  })

  it('未知状态原样透出，不折叠成「未取到」', async () => {
    stubFetch([{ slot: '09:25', status: 'queued', available: false }].map(row))
    renderAt('/intraday?date=2026-10-30')
    await waitFor(() => expect(railCells()).toHaveLength(1))
    expect(screen.getByRole('button', { name: '09:25 queued' })).toBeTruthy()
  })

  it('时点表来自后端：给 4 个就渲染 4 格，页面没有自己的 6 时点常量', async () => {
    stubFetch(
      [
        { slot: '09:25', status: 'ok', available: true },
        { slot: '09:35', status: 'ok', available: true },
        { slot: '14:00', status: 'ok', available: true },
        { slot: '15:00', status: 'ok', available: true },
      ].map(row),
    )
    renderAt('/intraday?date=2026-10-30')
    await waitFor(() => expect(railCells()).toHaveLength(4))
    expect(screen.queryByRole('button', { name: /^11:30 / })).toBeNull()
    expect(screen.getAllByText(/4\/4 个时点有快照/).length).toBeGreaterThan(0)
  })
})

describe('缺失值与数字样式', () => {
  it('state=partial：6 格里 2 个 missing 显示「未取到」，格子里是「—」不是 0', async () => {
    stubFetch(
      [
        { slot: '09:25', status: 'ok', available: true, stocks: 3, fresh: 3 },
        { slot: '09:35', status: 'ok', available: true, stocks: 3, fresh: 3 },
        { slot: '10:00', status: 'ok', available: true, stocks: 3, fresh: 3 },
        { slot: '11:30', status: 'ok', available: true, stocks: 3, fresh: 3 },
        { slot: '14:00', status: 'missing', stocks: null, fresh: null },
        { slot: '15:00', status: 'missing', stocks: null, fresh: null },
      ].map(row),
    )
    renderAt('/intraday?date=2026-10-30&state=partial')
    await waitFor(() => expect(railCells()).toHaveLength(6))

    expect(screen.getByText('部分时点没有落盘')).toBeTruthy()
    const missingCells = [
      screen.getByRole('button', { name: '14:00 未取到' }),
      screen.getByRole('button', { name: '15:00 未取到' }),
    ]
    for (const cell of missingCells) {
      expect(within(cell).getByText('—', { exact: false })).toBeTruthy()
      expect(cell.textContent).not.toMatch(/0\/0/)
    }
    expect(screen.getAllByText('未取到').length).toBe(2)
  })

  it('数值全部在 .ldst-num 里；涨红跌绿带 ↑↓', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30&slot=09:25')
    const table = await screen.findByRole('table')
    const up = within(table).getByText('+7.83%')
    const down = within(table).getByText('-3.77%')
    expect(up.className).toContain('ldst-num')
    expect(up.className).toContain('text-up')
    expect(up.textContent).toContain('↑')
    expect(down.className).toContain('text-down')
    expect(down.textContent).toContain('↓')
    // 没有读数的价格是「—」，不是 0（DEC-028，具体单元格见下一条）
    expect(within(table).getAllByText('—').length).toBeGreaterThan(0)
  })

  it('at_limit_up 为 null 时显示「—」并说明不按阈值猜', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30&slot=09:25')
    const table = await screen.findByRole('table')
    expect(within(table).getByText('封板')).toBeTruthy()
    const cells = within(table).getAllByRole('cell')
    expect(cells.some((cell) => cell.textContent === '—')).toBe(true)
  })

  it('price_field 说明按载荷取值，不是按时点名硬编码', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30&slot=09:25')
    expect(await screen.findByText('开盘价（集合竞价成交价，涨跌幅按开盘价重算）')).toBeTruthy()

    cleanup()
    stubFetch([{ slot: '13:30', status: 'ok', available: true }].map(row))
    renderAt('/intraday?date=2026-10-30&slot=13:30')
    expect(await screen.findByText('最新价（连续竞价）')).toBeTruthy()
  })
})

describe('五种界面状态', () => {
  it('empty：这一天调度器没跑过 → 空态 + 6 个时点仍然列出', async () => {
    stubFetch(SIX_STATES.map((seed) => row({ ...seed, status: 'missing', recorded: false, available: false })))
    renderAt('/intraday?date=2026-10-30')
    expect(await screen.findByText('这一天没有落盘快照')).toBeTruthy()
    await waitFor(() => expect(railCells()).toHaveLength(6))
  })

  it('loading / error / offline 各自可复现（?state=）', async () => {
    const busy = renderAt('/intraday?demo=1&state=loading')
    expect(await screen.findByText('演示态：加载中（?state=loading）')).toBeTruthy()
    expect(document.querySelector('[aria-busy="true"]')).toBeTruthy()
    busy.unmount()

    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"boom"}', { status: 500 })))
    const broken = renderAt('/intraday?date=2026-10-30')
    expect(await screen.findByText('盘中快照取数失败')).toBeTruthy()
    broken.unmount()

    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed') }))
    const offline = renderAt('/intraday?date=2026-10-30')
    expect(await screen.findByText('本机服务未连接')).toBeTruthy()
    offline.unmount()
  })

  it('404 是「接口未接入」，不是整屏报错', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Not Found"}', { status: 404 })))
    renderAt('/intraday?date=2026-10-30')
    expect(await screen.findByText('接口未接入')).toBeTruthy()
    expect(screen.queryByText('盘中快照取数失败')).toBeNull()
  })

  it('演示态五态齐备（?demo=1&state=…）', async () => {
    const cases: Array<[string, string | RegExp]> = [
      ['empty', '这一天没有落盘快照'],
      ['partial', /部分时点没有落盘/],
      ['offline', '本机服务未连接'],
      ['error', '盘中快照取数失败'],
    ]
    for (const [state, expected] of cases) {
      cleanup()
      renderAt(`/intraday?demo=1&state=${state}`)
      expect(await screen.findByText(expected as string & RegExp)).toBeTruthy()
      expect(screen.getByText(`演示态：` , { exact: false })).toBeTruthy()
    }
  })

  it('?demo=1&state=partial 就是「2 个 missing」的形态', async () => {
    renderAt('/intraday?demo=1&state=partial')
    await waitFor(() => expect(railCells()).toHaveLength(6))
    expect(screen.getAllByText('未取到')).toHaveLength(2)
  })
})

describe('侧边栏「盯盘」第二入口', () => {
  function renderShell(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/*" element={<Layout />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('盯盘 组里是「盘中信号」+「竞价与盘中」两段', () => {
    renderShell('/intraday')
    const group = document.getElementById('nav-group-watch')
    expect(group).not.toBeNull()
    const links = within(group as HTMLElement).getAllByRole('link')
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['/watch', '/intraday'])
    expect(within(group as HTMLElement).getByRole('link', { name: '竞价与盘中' })).toBeTruthy()
  })

  it('当前页只高亮一个入口：/intraday 时「盘中信号」不带 aria-current', () => {
    renderShell('/intraday')
    const group = document.getElementById('nav-group-watch') as HTMLElement
    expect(within(group).getByRole('link', { name: '竞价与盘中' }).getAttribute('aria-current')).toBe('page')
    expect(within(group).getByRole('link', { name: '盘中信号' }).getAttribute('aria-current')).toBeNull()
  })
})

describe('时点选中 → 快照节选', () => {
  it('默认落到第一个有快照的时点，点别的格子就换那格的快照', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30')
    expect(await screen.findByRole('heading', { name: '09:25 时点快照' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '09:35 降级写入' }))
    expect(await screen.findByRole('heading', { name: '09:35 时点快照' })).toBeTruthy()
  })

  it('选中没有快照的时点：说清这一格为什么没有，不给空白面板', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30&slot=14:00')
    await screen.findByRole('heading', { name: '14:00 时点快照' })
    expect(await screen.findByText('14:00 没有快照文件')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '15:00 抓取失败' }))
    expect(await screen.findByText('15:00 没有快照文件')).toBeTruthy()
    expect(screen.getByText(/peer 拒绝连接/)).toBeTruthy()
  })

  it('页面不重算：读数、封板数、涨跌幅都按后端原值显示', async () => {
    stubFetch(SIX_STATES.map(row))
    renderAt('/intraday?date=2026-10-30&slot=09:25')
    const table = await screen.findByRole('table')
    expect(within(table).getByText('+7.83%')).toBeTruthy()
    expect(within(table).getAllByText('19.9').length).toBeGreaterThan(0)
    expect(screen.getByText('47')).toBeTruthy()
    expect(screen.getByText('60.3%')).toBeTruthy()
  })
})
