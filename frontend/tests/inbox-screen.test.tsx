// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import InboxScreen from '../src/screens/my-stocks/InboxScreen'
import { formatInboxTime } from '../src/lib/format'

/**
 * P-QUOT-435 `/inbox` 提醒页。
 *
 * 守住五件事：
 * 1. 未读点在、时间口径与参考 `formatInboxTime` 一致（今天 `HH:mm`，更早 `MM-DD HH:mm`）；
 * 2. 单条「标为已读」真的打到 `POST /api/inbox/{id}/read`；
 * 3. 「全部已读」打到 `POST /api/inbox/read-all`；
 * 4. 空态不画假条目、错误态给原因与重试；
 * 5. 定时任务只读（不摆开关）、渠道只有 console / desktop，站内链接可跳、外链不打开。
 */

interface Call {
  url: string
  method: string
}

type Handler = (call: Call) => unknown

function stubApi(handlers: Record<string, Handler>): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const call: Call = { url, method: init?.method ?? 'GET' }
      calls.push(call)
      const prefix = Object.keys(handlers).find((key) => url.startsWith(key))
      if (!prefix) throw new Error(`unexpected fetch: ${url}`)
      const result = handlers[prefix](call)
      if (result instanceof Response) return result
      return new Response(JSON.stringify(result), { status: 200 })
    }),
  )
  return calls
}

/** 今天某时某分（本地时区），用 `toISOString` 交给页面按本地时间格式化。 */
function todayAt(hour: number, minute: number): string {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, minute).toISOString()
}

function item(overrides: Record<string, unknown> = {}) {
  return {
    id: 'item-a',
    job: 'premarket_digest',
    job_label: '09:05 盘前摘要',
    kind: 'digest',
    title: '今日盘前提醒',
    lines: ['自选 600519 现价 1700.00', '持仓 3 只，昨日盈亏 +1.20%'],
    link: '/my-stocks',
    is_read: false,
    read_at: null,
    created_at: todayAt(9, 5),
    ...overrides,
  }
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    local_only: true,
    store: 'local_sqlite',
    items: [item()],
    unread: 1,
    jobs: [
      { id: 'premarket_digest', label: '09:05 盘前摘要', trigger: '09:05', enabled: true },
      { id: 'close_digest', label: '15:40 收盘摘要', trigger: '15:40', enabled: false },
    ],
    ...overrides,
  }
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={['/inbox']}>
      <Routes>
        <Route path="/inbox" element={<InboxScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('formatInboxTime', () => {
  it('今天 `HH:mm`，更早 `MM-DD HH:mm`，读不到时间 `—`', () => {
    const now = new Date(2026, 10, 6, 15, 0, 0)
    expect(formatInboxTime(new Date(2026, 10, 6, 9, 5).toISOString(), now)).toBe('09:05')
    expect(formatInboxTime(new Date(2026, 10, 2, 8, 3).toISOString(), now)).toBe('11-02 08:03')
    expect(formatInboxTime(null, now)).toBe('—')
    expect(formatInboxTime('not-a-date', now)).toBe('—')
  })
})

describe('/inbox 收件箱', () => {
  it('按时间显示条目的任务名、标题、未读点与事实行', async () => {
    stubApi({ '/api/inbox': () => payload() })
    renderScreen()

    const list = await screen.findByRole('list', { name: '提醒列表' })
    expect(within(list).getByText('09:05 盘前摘要')).toBeTruthy()
    expect(within(list).getByText('今日盘前提醒')).toBeTruthy()
    expect(within(list).getByLabelText('未读')).toBeTruthy()
    expect(within(list).getByText('自选 600519 现价 1700.00')).toBeTruthy()
    expect(within(list).getByText('09:05')).toBeTruthy()
    // h1 仍是「提醒」（可见标题由顶栏面包屑承担）
    expect(screen.getByRole('heading', { name: '提醒' })).toBeTruthy()
  })

  it('单条「标为已读」打到 POST /api/inbox/{id}/read，刷新后未读点消失', async () => {
    let read = false
    const calls = stubApi({
      '/api/inbox/item-a/read': () => {
        read = true
        return { item: item({ is_read: true, read_at: todayAt(9, 10) }), unread: 0 }
      },
      '/api/inbox/read-all': () => ({ updated: 0, read_at: todayAt(9, 10), unread: 0 }),
      '/api/inbox': () =>
        payload({
          items: [item({ is_read: read, read_at: read ? todayAt(9, 10) : null })],
          unread: read ? 0 : 1,
        }),
    })
    renderScreen()

    const list = await screen.findByRole('list', { name: '提醒列表' })
    fireEvent.click(within(list).getByRole('button', { name: '标为已读' }))

    await waitFor(() => expect(within(list).queryByLabelText('未读')).toBeNull())
    const post = calls.find((call) => call.url === '/api/inbox/item-a/read')
    expect(post?.method).toBe('POST')
    expect(screen.getByRole('button', { name: '全部已读' })).toBeTruthy()
  })

  it('「全部已读」打到 POST /api/inbox/read-all', async () => {
    let allRead = false
    const calls = stubApi({
      '/api/inbox/read-all': () => {
        allRead = true
        return { updated: 2, read_at: todayAt(9, 12), unread: 0 }
      },
      '/api/inbox': () =>
        payload({
          items: [
            item({ id: 'item-a', is_read: allRead, read_at: allRead ? todayAt(9, 12) : null }),
            item({ id: 'item-b', title: '今日收盘摘要', is_read: allRead, read_at: allRead ? todayAt(9, 12) : null }),
          ],
          unread: allRead ? 0 : 2,
        }),
    })
    renderScreen()

    fireEvent.click(await screen.findByRole('button', { name: '全部已读' }))
    await waitFor(() => expect(screen.queryAllByLabelText('未读')).toHaveLength(0))
    const post = calls.find((call) => call.url === '/api/inbox/read-all')
    expect(post?.method).toBe('POST')
    await waitFor(() => expect((screen.getByRole('button', { name: '全部已读' }) as HTMLButtonElement).disabled).toBe(true))
  })

  it('空态写明「定时任务跑过之后会出现在这里」，不画假条目', async () => {
    stubApi({ '/api/inbox': () => payload({ items: [], unread: 0, jobs: [] }) })
    renderScreen()

    expect(await screen.findByText('还没有提醒')).toBeTruthy()
    expect(screen.getByText(/定时任务跑过之后会出现在这里/)).toBeTruthy()
    expect(screen.queryByRole('list', { name: '提醒列表' })).toBeNull()
    expect(screen.getByText('没有会产生提醒的定时任务。')).toBeTruthy()
  })

  it('加载失败给出原因和重试；重试成功后渲染列表', async () => {
    let failed = true
    stubApi({
      '/api/inbox': () =>
        failed
          ? new Response(JSON.stringify({ detail: '收件箱库打不开' }), { status: 500 })
          : payload(),
    })
    renderScreen()

    expect(await screen.findByText('提醒读取失败')).toBeTruthy()
    expect(screen.getByText(/收件箱库打不开/)).toBeTruthy()

    failed = false
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('list', { name: '提醒列表' })).toBeTruthy()
  })
})

describe('/inbox 定时任务与渠道', () => {
  it('定时任务只读：列 label / trigger / enabled，不摆开关', async () => {
    stubApi({ '/api/inbox': () => payload() })
    renderScreen()

    const jobs = await screen.findByRole('list', { name: '定时任务列表' })
    expect(within(jobs).getByText('09:05 盘前摘要')).toBeTruthy()
    expect(within(jobs).getByText('已启用')).toBeTruthy()
    expect(within(jobs).getByText('15:40 收盘摘要')).toBeTruthy()
    expect(within(jobs).getByText('已停用')).toBeTruthy()
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
  })

  it('渠道只列 console / desktop，没有发送测试按钮', async () => {
    stubApi({ '/api/inbox': () => payload() })
    renderScreen()

    const channels = await screen.findByRole('list', { name: '推送渠道列表' })
    const rows = within(channels).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(channels).getByText('终端（console）')).toBeTruthy()
    expect(within(channels).getByText('桌面通知（desktop）')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /发送测试/ })).toBeNull()
  })

  it('站内链接给「打开」；外链只显示提示、不生成可点击链接', async () => {
    stubApi({
      '/api/inbox': () =>
        payload({
          items: [
            item({ id: 'item-a', link: '/calendar', title: '事件日历有更新' }),
            item({ id: 'item-b', link: 'https://example.com/notice', title: '外部公告' }),
          ],
          unread: 2,
        }),
    })
    renderScreen()

    const list = await screen.findByRole('list', { name: '提醒列表' })
    const open = within(list).getByRole('link', { name: '打开' })
    expect(open.getAttribute('href')).toBe('/calendar')
    expect(within(list).getByText('外链不在页面打开')).toBeTruthy()
    expect(within(list).queryByRole('link', { name: /外部公告/ })).toBeNull()
    expect(within(list).queryByRole('link', { name: 'https://example.com/notice' })).toBeNull()
  })
})
