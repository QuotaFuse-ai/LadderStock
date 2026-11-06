// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MyStocksScreen from '../src/screens/my-stocks/MyStocksScreen'

/**
 * P-QUOT-431 `/my-stocks?tab=watch|hold`：一页两标签。
 *
 * `hold` 由 QUOT-434 的流水驱动持仓页渲染（单独见 `portfolio-panel.test.tsx`），
 * 本文件只守住「地址优先 / 本机记忆 / 切标签不丢状态」与自选面板的七列、
 * 缺失值、持有标记、批量确认、点行看 K 线、行内删除这些页面行为。
 */

// 图表组件在 jsdom 里画不出来，只验证「点行后它挂上了、再点收起」
vi.mock('../src/components/KlineCard', () => ({
  KlineCard: ({ symbol, title }: { symbol: string; title?: string }) => (
    <div data-testid="kline-card">{title ? `${symbol} ${title}` : symbol}</div>
  ),
}))

const TAB_KEY = 'ldst.my-stocks.tab'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

beforeEach(() => {
  window.localStorage.clear()
})

interface Call {
  url: string
  method: string
  body: unknown
}

type Handler = (call: Call) => unknown

/** 按 URL 前缀分派；返回 `Response` 时原样交回（用来造 5xx）。 */
function stubApi(handlers: Record<string, Handler>): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const call: Call = {
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined,
      }
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

function renderAt(path: string) {
  const pathname = path.split('?')[0]
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={pathname} element={<MyStocksScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

interface RowSeed {
  code: string
  name?: string | null
  price?: number | null
  changePct?: number | null
  pe?: number | null
  pb?: number | null
  turnover?: number | null
  metricsReason?: string | null
}

function row(seed: RowSeed) {
  const price = seed.price === undefined ? 10 : seed.price
  return {
    code: seed.code,
    added_at: '2026-11-01T09:00:00+08:00',
    available: price !== null,
    name: seed.name === undefined ? `样例${seed.code}` : seed.name,
    market: 'sh',
    latest_price: price,
    change: price === null ? null : 0.1,
    change_pct: seed.changePct === undefined ? 0.96 : seed.changePct,
    prev_close: price === null ? null : price - 0.1,
    price_as_of: price === null ? null : '2026-11-05T15:00:00+08:00',
    price_status: price === null ? 'unavailable' : 'ok',
    price_reason: price === null ? '这批报价没有它' : null,
    pe_ttm: seed.pe === undefined ? 12.3 : seed.pe,
    pb: seed.pb === undefined ? 1.2 : seed.pb,
    turnover_pct: seed.turnover === undefined ? 0.8 : seed.turnover,
    metrics_reason: seed.metricsReason ?? null,
  }
}

function watchPayload(items: unknown[], count = items.length) {
  return {
    local_only: true,
    store: 'local_sqlite',
    available: items.length > 0,
    reason: items.length > 0 ? null : '本机还没有自选',
    watch_count: count,
    data_source_status: items.length > 0 ? 'ok' : 'missing',
    price_source: 'spot_quote',
    price_as_of: '2026-11-05T15:00:00+08:00',
    source_detail: null,
    items,
  }
}

describe('我的股票：一页两标签（QUOT-431）', () => {
  it('`?tab=watch` 先渲染自选，点「持仓」切过去并记住选择', async () => {
    stubApi({
      '/api/watchlist': () => watchPayload([]),
      '/api/portfolio/perf': () => ({ groups: [], notes: [], skipped: [], gaps: [], sources: [], as_of: null }),
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

    expect(await screen.findByRole('heading', { name: '我的自选', level: 1 })).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: '持仓' }))

    expect(await screen.findByRole('heading', { name: '我的持仓', level: 1 })).toBeTruthy()
    expect(window.localStorage.getItem(TAB_KEY)).toBe('hold')
    expect(screen.getByRole('tab', { name: '持仓' }).getAttribute('aria-selected')).toBe('true')
  })

  it('地址没带 `?tab=` 时回到上次记住的那一页', async () => {
    window.localStorage.setItem(TAB_KEY, 'hold')
    stubApi({
      '/api/portfolio/perf': () => ({ groups: [], notes: [], skipped: [], gaps: [], sources: [], as_of: null }),
      '/api/watchlist': () => watchPayload([]),
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks')

    expect(await screen.findByRole('heading', { name: '我的持仓', level: 1 })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '持仓' }).getAttribute('aria-selected')).toBe('true')
  })
})

describe('自选面板：七列与缺失值（QUOT-431）', () => {
  it('渲染 名称/代码/现价/涨跌%/PE(TTM)/PB/换手% 七列', async () => {
    stubApi({
      '/api/watchlist': () => watchPayload([row({ code: '600000', name: '浦发银行' })]),
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

    await screen.findByText('浦发银行')
    for (const header of ['名称', '代码', '现价', '涨跌%', 'PE(TTM)', 'PB', '换手%']) {
      expect(screen.getByRole('columnheader', { name: header })).toBeTruthy()
    }
    expect(screen.getByText(/1 只 · 数据时点/)).toBeTruthy()
  })

  it('缺失的估值显示 `—`，原因放在单元格 tooltip 里', async () => {
    stubApi({
      '/api/watchlist': () =>
        watchPayload([
          row({
            code: '600519',
            name: '贵州茅台',
            pe: null,
            metricsReason: '市盈率没有取到',
          }),
        ]),
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

    await screen.findByText('贵州茅台')
    const cell = document.querySelector<HTMLElement>('[title="市盈率没有取到"]')
    expect(cell).toBeTruthy()
    expect(cell?.textContent).toBe('—')
  })

  it('「持有」标记来自 /api/portfolio；接口失败只少标记，不影响表格', async () => {
    stubApi({
      '/api/watchlist': () => watchPayload([row({ code: '600000', name: '浦发银行' })]),
      '/api/portfolio': () => ({ items: [{ code: '600000', symbol: '600000', qty: 100 }] }),
    })
    const first = renderAt('/my-stocks?tab=watch')
    expect(await screen.findByText('持有')).toBeTruthy()
    first.unmount()

    stubApi({
      '/api/watchlist': () => watchPayload([row({ code: '600000', name: '浦发银行' })]),
      '/api/portfolio': () => new Response('{}', { status: 500 }),
    })
    renderAt('/my-stocks?tab=watch')
    expect(await screen.findByText('浦发银行')).toBeTruthy()
    expect(await screen.findByText(/没取到持仓/)).toBeTruthy()
  })

  it('空清单只写「还没有自选」，不画假行', async () => {
    stubApi({
      '/api/watchlist': () => watchPayload([]),
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

    expect(await screen.findByText('还没有自选')).toBeTruthy()
    expect(document.querySelectorAll('tbody tr')).toHaveLength(1)
  })
})

describe('自选面板：批量添加（QUOT-431）', () => {
  it('先预览、再确认；非 A 股逐条给原因，确认后逐条 POST 并重新拉清单', async () => {
    let codes: string[] = []
    const calls = stubApi({
      '/api/watchlist': (call) => {
        if (call.method === 'POST') {
          const body = call.body as { code: string }
          codes = [...codes, body.code]
          return {
            ok: true,
            local_only: true,
            store: 'local_sqlite',
            watch: { code: body.code, created_at: '2026-11-05T10:00:00+08:00' },
          }
        }
        return watchPayload(codes.map((code) => row({ code })))
      },
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

    await screen.findByText('还没有自选')
    fireEvent.change(screen.getByLabelText('批量代码'), {
      target: { value: '600519 000858, 09988.HK AAPL' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    const pending = await screen.findByTestId('batch-pending')
    expect(within(pending).getByLabelText('待加入代码').textContent).toContain('600519')
    expect(within(pending).getByLabelText('待加入代码').textContent).toContain('000858')
    const rejected = within(pending).getByLabelText('不能加入的代码').textContent ?? ''
    expect(rejected).toContain('09988.HK：港股暂不支持')
    expect(rejected).toContain('AAPL：美股代码（字母）暂不支持')
    // 预览阶段不写库
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)

    fireEvent.click(within(pending).getByRole('button', { name: '确认加入 2 只' }))

    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(2))
    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts.map((call) => call.url)).toEqual(['/api/watchlist', '/api/watchlist'])
    expect(posts.map((call) => call.body)).toEqual([{ code: '600519' }, { code: '000858' }])
    expect(await screen.findByText('已加入 2 只自选（本机 SQLite）')).toBeTruthy()
    expect(calls.filter((call) => call.method === 'GET' && call.url.startsWith('/api/watchlist'))).toHaveLength(2)
    expect(await screen.findByText('600519')).toBeTruthy()
  })

  it('「不加了」取消预览，不产生任何写请求', async () => {
    const calls = stubApi({
      '/api/watchlist': () => watchPayload([]),
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

    await screen.findByText('还没有自选')
    fireEvent.change(screen.getByLabelText('批量代码'), { target: { value: '600519' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    const pending = await screen.findByTestId('batch-pending')
    fireEvent.click(within(pending).getByRole('button', { name: '不加了' }))

    expect(screen.queryByTestId('batch-pending')).toBeNull()
    expect(screen.getByText('已取消，本次没有写入任何自选。')).toBeTruthy()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })
})

describe('自选面板：点行看 K 线与行内删除（QUOT-431）', () => {
  it('点一行挂上 K 线卡，再点一次收起', async () => {
    stubApi({
      '/api/watchlist': () => watchPayload([row({ code: '600000', name: '浦发银行' })]),
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

    fireEvent.click(await screen.findByText('600000'))
    expect(screen.getByTestId('kline-card').textContent).toBe('600000 浦发银行')

    fireEvent.click(screen.getByText('600000'))
    expect(screen.queryByTestId('kline-card')).toBeNull()
  })

  it('行内移除确认后 POST /delete 并重新拉清单', async () => {
    let codes = ['600000']
    const calls = stubApi({
      '/api/watchlist': (call) => {
        if (call.method === 'POST') {
          codes = codes.filter((code) => !call.url.includes(code))
          return { ok: true, local_only: true, store: 'local_sqlite', removed: { code: '600000' } }
        }
        return watchPayload(codes.map((code) => row({ code })))
      },
      '/api/portfolio': () => ({ items: [] }),
    })
    renderAt('/my-stocks?tab=watch')

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
  })
})
