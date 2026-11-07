// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LadderEnvelope, ValidationPayload, WatchSnapshotPayload } from '../src/api'
import type { IndicesPayload, WatchlistPayload } from '../src/api/radar'
import { HomeResearchChat } from '../src/screens/home/HomeResearchChat'
import { buildHomeAssistant, type HomeAssistantBundle } from '../src/screens/home/homeAssistant'
import type { BlockView } from '../src/screens/home/useHomeSource'

/**
 * QUOT-472 首页 AI 研究入口。
 *
 * 钉的全是「真的发出去 / 真的显示出来」：
 * - 发出去的上下文里有首页已展示的行情，**没有持仓**（白名单）；
 * - 回答下面挂「本轮来源」，来源就是这一轮实际发出去的那几块，带取数时间与落点链接；
 * - 一块数据都没取到就写「本轮未取数」，不补默认值；
 * - 无 Key / 断网 走 QUOT-454 的降级文案，重试不重复用户气泡；
 * - 停止 / 清空 / Shift+Enter 换行。
 */

function normal<T>(data: T): BlockView<T> {
  return { state: 'normal', data, error: null, offline: false, endpointMissing: false, onRetry: () => {} }
}

const INDICES: IndicesPayload = {
  available: true,
  reason: null,
  footnote: '快照，不是逐笔实时',
  price_source: 'index_quote',
  datasets: ['index_quote'],
  data_source_status: 'ok',
  cells: [
    {
      code: 'sh000001',
      market: 'SH',
      name: '上证指数',
      available: true,
      price: 3649.18,
      change: -40.73,
      change_pct: -1.1,
      prev_close: 3689.91,
      as_of: '2026-11-06T16:14:01',
      reason: null,
    },
  ],
}

const WATCHLIST: WatchlistPayload = {
  local_only: true,
  store: 'local_sqlite',
  available: true,
  reason: null,
  watch_count: 1,
  data_source_status: 'ok',
  price_source: 'spot_quote',
  price_as_of: '2026-11-06T15:00:00',
  source_detail: null,
  freshness: null,
  source_status: null,
  data_as_of: '2026-11-06',
  fetched_at: null,
  items: [
    {
      code: '300308',
      added_at: '2026-10-01',
      available: true,
      name: '中际旭创',
      market: 'SZ',
      latest_price: 268.4,
      change: 5.2,
      change_pct: 1.98,
      prev_close: 263.2,
      price_as_of: '2026-11-06T15:00:00',
      price_status: 'ok',
      price_reason: null,
      pe_ttm: 32.4,
      pb: 8.1,
      turnover_pct: 2.3,
      metrics_reason: null,
    },
  ],
}

const WATCH = {
  payload: {
    trade_date: '2026-11-06',
    rows: [
      { code: '300308', name: '中际旭创', limit_up_count: 1, change_pct: 1.98, status: 'limit_up' },
    ],
  },
} as unknown as LadderEnvelope<WatchSnapshotPayload>

const VALIDATION = {
  payload: {
    trade_date: '2026-11-06',
    conditions: [{ code: '600519', trigger: '跌破 1600 减仓', note: null }],
    reconcile: null,
  },
} as unknown as LadderEnvelope<ValidationPayload>

function fullBundle(): HomeAssistantBundle {
  return buildHomeAssistant('2026-11-06', {
    indices: normal(INDICES),
    watchlist: normal(WATCHLIST),
    watch: normal(WATCH),
    validation: normal(VALIDATION),
  })
}

function emptyBundle(): HomeAssistantBundle {
  const offline = <T,>(): BlockView<T> => ({
    state: 'error',
    data: null,
    error: new Error('offline'),
    offline: true,
    endpointMissing: false,
    onRetry: () => {},
  })
  return buildHomeAssistant('2026-11-06', {
    indices: offline<IndicesPayload>(),
    watchlist: offline<WatchlistPayload>(),
    watch: offline<LadderEnvelope<WatchSnapshotPayload>>(),
    validation: offline<LadderEnvelope<ValidationPayload>>(),
  })
}

function jsonOk(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }
}

function renderChat(bundle: HomeAssistantBundle = fullBundle()) {
  return render(
    <MemoryRouter>
      <HomeResearchChat assistant={bundle} date="2026-11-06" />
    </MemoryRouter>,
  )
}

const textarea = () => screen.getByLabelText('向研究助手提问') as HTMLTextAreaElement
const sendButton = () => screen.getByRole('button', { name: '发送' })

function postedBody(call = 0): { question: string; context: string; date?: string } {
  const init = vi.mocked(fetch).mock.calls[call][1] as RequestInit
  return JSON.parse(String(init.body))
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonOk({ reply: '上证指数收跌 1.10%。', model: 'test', fetched_at: '2026-11-06T16:20:00Z' })))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('首页 AI 研究入口（QUOT-472）', () => {
  it('四条起步问题点一下只填输入框，不自动发请求', () => {
    renderChat()
    const suggestion = screen.getByRole('button', { name: '今天大盘和自选有哪些值得注意的变化？' })
    fireEvent.click(suggestion)
    expect(textarea().value).toBe('今天大盘和自选有哪些值得注意的变化？')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('Enter 发送：走同一条 assistant 通道，上下文含首页行情且不含持仓', async () => {
    renderChat()
    fireEvent.change(textarea(), { target: { value: '今天大盘怎么样？' } })
    fireEvent.keyDown(textarea(), { key: 'Enter' })

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toBe('/api/research/home/assistant')
    expect((init as RequestInit).method).toBe('POST')

    const body = postedBody()
    expect(body.question).toBe('今天大盘怎么样？')
    expect(body.date).toBe('2026-11-06')
    const context = JSON.parse(body.context)
    expect(Object.keys(context).sort()).toEqual([
      'date',
      'market',
      'page',
      'unavailable',
      'validation',
      'watch',
      'watchlist',
    ])
    expect(context.market.indices[0]).toMatchObject({ code: 'sh000001', price: 3649.18, changePct: -1.1 })
    expect(context.watchlist.rows[0]).toMatchObject({ code: '300308', price: 268.4 })
    // 持仓（成本 / 数量 / 盈亏）一律不进上下文
    expect(body.context).not.toMatch(/position|cost|profit|持仓/)
  })

  it('回答下面挂「本轮来源」：来源名 + 取数时间 + 落点链接', async () => {
    renderChat()
    fireEvent.change(textarea(), { target: { value: '今天大盘怎么样？' } })
    fireEvent.click(sendButton())

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(screen.getByText('上证指数收跌 1.10%。')).toBeTruthy()
    expect(screen.getByText('本轮来源')).toBeTruthy()
    expect(screen.getByText('大盘与全球指数')).toBeTruthy()
    expect(screen.getByText('我的自选')).toBeTruthy()
    expect(screen.getByText('盯盘快照')).toBeTruthy()
    expect(screen.getByText('验证条件')).toBeTruthy()
    expect(screen.getAllByText(/取数 |交易日 /).length).toBeGreaterThan(0)
    const links = screen.getAllByRole('link', { name: '查看' })
    expect(links[0].getAttribute('href')).toBe('/my-stocks?tab=watch')
  })

  it('首页一块数据都没取到：明说「本轮未取数」', async () => {
    renderChat(emptyBundle())
    fireEvent.change(textarea(), { target: { value: '今天大盘怎么样？' } })
    fireEvent.click(sendButton())

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(screen.getByText(/本轮未取数/)).toBeTruthy()
    const context = JSON.parse(postedBody().context)
    expect(context.market).toBeNull()
    // 指数 / 自选 / 盯盘三块写进 unavailable；验证条件为空是空态，不算「没取到」
    expect(context.unavailable.length).toBe(3)
  })

  it('无 Key：给「未配置模型」的降级文案，重试不重复用户气泡', async () => {
    const failure = jsonOk(
      {
        detail: '未配置 AI 通道：缺少 LDST_LLM_API_KEY。请到「设置 → 接入 AI」配置后再试。',
        code: 'ai_not_configured',
        retryable: true,
      },
      503,
    )
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(failure)
        .mockResolvedValueOnce(jsonOk({ reply: '重试成功。', model: 'test', fetched_at: null })),
    )

    renderChat()
    fireEvent.change(textarea(), { target: { value: '今天大盘怎么样？' } })
    fireEvent.click(sendButton())
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(screen.getByText('研究助手未配置模型')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(screen.getByText('重试成功。')).toBeTruthy()
    expect(screen.getAllByText('今天大盘怎么样？').length).toBe(1)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('断网：给「本机服务未连接」，不静默', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline')
      }),
    )

    renderChat()
    fireEvent.change(textarea(), { target: { value: '今天大盘怎么样？' } })
    fireEvent.click(sendButton())
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(screen.getByText('本机服务未连接')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  })

  it('停止：把「已停止本次回答。」写进对话', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: RequestInfo | URL, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            )
          }),
      ),
    )

    renderChat()
    fireEvent.change(textarea(), { target: { value: '今天大盘怎么样？' } })
    fireEvent.click(sendButton())
    await act(async () => {
      await Promise.resolve()
    })

    fireEvent.click(screen.getByRole('button', { name: '停止' }))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(screen.getByText('已停止本次回答。')).toBeTruthy()
    expect(screen.queryByText('本轮来源')).toBeNull()
  })

  it('Shift+Enter 只换行不发送；清空把对话收起', async () => {
    renderChat()
    fireEvent.change(textarea(), { target: { value: '第一行' } })
    fireEvent.keyDown(textarea(), { key: 'Enter', shiftKey: true })
    expect(fetch).not.toHaveBeenCalled()

    fireEvent.click(sendButton())
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText('上证指数收跌 1.10%。')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '清空' }))
    expect(screen.queryByText('上证指数收跌 1.10%。')).toBeNull()
    expect(screen.queryByText('本轮来源')).toBeNull()
    expect(screen.getByRole('button', { name: '今天大盘和自选有哪些值得注意的变化？' })).toBeTruthy()
  })
})
