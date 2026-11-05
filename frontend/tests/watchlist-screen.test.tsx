// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import WatchlistScreen from '../src/screens/watchlist/WatchlistScreen'

/**
 * P-QUOT-419 `/watchlist`：清单渲染、页面内新增 / 删除、非法代码不发请求。
 * 写入面是 `POST /api/watchlist` 与 `POST /api/watchlist/{code}/delete`（DEC-055），
 * 成功后重新拉 `GET /api/watchlist`，不需要 CLI。
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

interface Call {
  url: string
  method: string
  body: unknown
}

interface Queued {
  status?: number
  body: unknown
}

function stubFetch(queue: Queued[]): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined,
      })
      const next = queue.shift()
      if (!next) throw new Error(`unexpected fetch: ${url}`)
      return new Response(JSON.stringify(next.body), { status: next.status ?? 200 })
    }),
  )
  return calls
}

function watchPayload(codes: string[], watchCount = codes.length) {
  return {
    local_only: true,
    store: 'local_sqlite',
    available: codes.length > 0,
    reason: codes.length > 0 ? null : '本机还没有自选',
    watch_count: watchCount,
    data_source_status: codes.length > 0 ? 'ok' : 'missing',
    price_source: 'spot_quote',
    price_as_of: '2026-11-05T15:00:00+08:00',
    source_detail: null,
    items: codes.map((code, index) => ({
      code,
      added_at: '2026-11-01T09:00:00+08:00',
      available: true,
      name: code === '600000' ? '浦发银行' : `样例${index}`,
      market: 'sh',
      latest_price: 10 + index,
      change: 0.1,
      change_pct: 0.96,
      prev_close: 10 + index - 0.1,
      price_as_of: '2026-11-05T15:00:00+08:00',
      price_status: 'ok',
      price_reason: null,
    })),
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/watchlist']}>
      <Routes>
        <Route path="/watchlist" element={<WatchlistScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('我的自选页（QUOT-419）', () => {
  it('渲染 4 列清单与只数', async () => {
    stubFetch([{ body: watchPayload(['600000', '000001']) }])
    renderPage()

    expect(await screen.findByRole('heading', { name: '我的自选', level: 1 })).toBeTruthy()
    expect(await screen.findByText('600000')).toBeTruthy()
    expect(screen.getByText('浦发银行')).toBeTruthy()
    expect(screen.getByText('000001')).toBeTruthy()
    expect(screen.getByText(/2 只 · 数据时点/)).toBeTruthy()
    expect(screen.getByText(/自选清单 · \(2\)/)).toBeTruthy()
  })

  it('输入合法代码后 POST {code}，成功后重新拉清单', async () => {
    const calls = stubFetch([
      { body: watchPayload([]) },
      { body: { ok: true, local_only: true, store: 'local_sqlite', watch: { code: '000001', created_at: '2026-11-05T10:00:00+08:00' } } },
      { body: watchPayload(['000001']) },
    ])
    renderPage()

    expect(await screen.findByText('还没有自选')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('证券代码'), { target: { value: 'sh000001' } })
    fireEvent.click(screen.getByRole('button', { name: /加入自选/ }))

    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1))
    const post = calls.find((call) => call.method === 'POST')
    expect(post?.url).toBe('/api/watchlist')
    expect(post?.body).toEqual({ code: 'sh000001' })

    expect(await screen.findByText('000001')).toBeTruthy()
    expect(await screen.findByText('已加入自选 000001（本机 SQLite）')).toBeTruthy()
    expect(calls.filter((call) => call.method === 'GET')).toHaveLength(2)
  })

  it('非法代码只在本页报错，不发写请求', async () => {
    const calls = stubFetch([{ body: watchPayload([]) }])
    renderPage()

    await screen.findByText('还没有自选')
    fireEvent.change(screen.getByLabelText('证券代码'), { target: { value: '60000' } })
    fireEvent.click(screen.getByRole('button', { name: /加入自选/ }))

    expect((await screen.findByRole('alert')).textContent).toContain('请输入 6 位代码')
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })

  it('行内删除确认后 POST /delete，并重新拉清单', async () => {
    const calls = stubFetch([
      { body: watchPayload(['600000']) },
      { body: { ok: true, local_only: true, store: 'local_sqlite', removed: { code: '600000' } } },
      { body: watchPayload([]) },
    ])
    renderPage()

    await screen.findByText('600000')
    fireEvent.click(screen.getByRole('button', { name: '移除 600000' }))

    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '移除' }))

    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1))
    const post = calls.find((call) => call.method === 'POST')
    expect(post?.url).toBe('/api/watchlist/600000/delete')
    expect(post?.body).toBeUndefined()

    expect(await screen.findByText('已移除 600000（本机 SQLite）')).toBeTruthy()
    expect(await screen.findByText('还没有自选')).toBeTruthy()
    expect(calls.filter((call) => call.method === 'GET')).toHaveLength(2)
  })
})
