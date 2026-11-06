// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ResearchOverview } from '../src/api/research'
import { ResearchAiDock } from '../src/components/research/sections/AiDock'

/**
 * QUOT-454 研究助手抽屉：
 * 标题 / 三个快捷问题 / 上下文摘要 / 发送（含 Enter）/ 停止 / 四类降级。
 * 请求由 `fetch` 替身接住，断言真正发出去的上下文里没有私有字段。
 */

const OVERVIEW: ResearchOverview = {
  code: '600000',
  name: '浦发银行',
  as_of: '2026-11-06',
  dataSourceStatus: 'ok',
  kline: {
    bars: [
      { date: '2026-11-04', open: 10.1, high: 10.3, low: 10, close: 10.2, volume: 1000 },
      { date: '2026-11-05', open: 10.2, high: 10.5, low: 10.1, close: 10.4, volume: 1200 },
      { date: '2026-11-06', open: 10.4, high: 10.6, low: 10.2, close: 10.3, volume: 900 },
    ],
    count: 3,
    requested: 120,
    reason: null,
  },
  quote: {
    quote: {
      name: '浦发银行',
      price: 10.3,
      prev_close: 10.4,
      change: -0.1,
      change_pct: -0.96,
      open: 10.4,
      high: 10.6,
      low: 10.2,
      volume: 900,
      amount: 9200,
      timestamp: '2026-11-06 15:00:00',
      source_detail: null,
    },
    reason: null,
  },
  financials: {
    financials: {
      report_name: '2026Q3',
      report_date: '2026-09-30',
      eps: 1.2,
      revenue: 120360000000,
      net_profit: 42000000000,
      roe: 9.8,
      revenue_yoy: 5.6,
      profit_yoy: 4.2,
    },
    reason: null,
  },
}

const EARNINGS = {
  payload: {
    code: '600000',
    name: '浦发银行',
    periods: 2,
    consensus: { as_of: '2026-10-31', analyst_count: 12, eps_current_year: 1.5 },
    earnings: [{ report_date: '2026-09-30' }, { report_date: '2026-06-30' }],
    stats: { with_forecast: 2, latest_report_date: '2026-09-30' },
    data_source_status: 'ok',
  },
}

const EVENTS = {
  payload: {
    code: '600000',
    benchmark: '000300',
    trading_days: 240,
    event_types: [
      { dataset: 'holder_trades', label: '增持减持', count: 2 },
      { dataset: 'dragon_tiger', label: '龙虎榜', count: 1 },
    ],
    data_source_status: 'ok',
  },
}

interface Captured {
  url: string
  method: string
  headers: Record<string, string>
  body: unknown
}

type AssistantHandler = (init: RequestInit) => Promise<Response>

function stubFetch(handler: AssistantHandler): Captured[] {
  const calls: Captured[] = []
  vi.stubGlobal('fetch', (input: string, init: RequestInit = {}) => {
    const url = String(input)
    calls.push({
      url,
      method: init.method ?? 'GET',
      headers: (init.headers ?? {}) as Record<string, string>,
      body: typeof init.body === 'string' ? JSON.parse(init.body) : null,
    })
    if (url.endsWith('/assistant')) return handler(init)
    if (url.endsWith('/earnings-brief')) return Promise.resolve(json(EARNINGS))
    if (url.endsWith('/event-study')) return Promise.resolve(json(EVENTS))
    return Promise.resolve(json({ detail: 'unexpected' }, 404))
  })
  return calls
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function renderDock() {
  return render(
    <ResearchAiDock
      code="600000"
      name="浦发银行"
      scope={{ tab: 'overview', date: '2026-11-06' }}
      overview={OVERVIEW}
    />,
  )
}

async function openDock(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: '研究助手' }))
  await screen.findByRole('heading', { name: '研究助手 · 个股深挖 · 浦发银行' })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('抽屉结构与上下文摘要', () => {
  it('标题、三个快捷问题、四类可见数据摘要都在', async () => {
    stubFetch(() => Promise.resolve(json({ reply: 'ok' })))
    // jsdom 没有 matchMedia：显式模拟窄屏，验证抽屉铺满整屏
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }))
    renderDock()
    await openDock()

    expect(
      screen.getByRole('button', { name: '浦发银行最近的走势怎么样' }),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: '浦发银行下一份财报要看什么' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '浦发银行有哪些值得留意的事件' })).toBeTruthy()

    expect(screen.getByText('K 线区间')).toBeTruthy()
    expect(screen.getByText('2026-11-04 → 2026-11-06')).toBeTruthy()
    await screen.findByText('2 期 · 一致预期 12 家')
    expect(await screen.findByText('3 条')).toBeTruthy()
    expect(screen.getByText('增持减持 2 · 龙虎榜 1')).toBeTruthy()

    // 窄屏全屏能力落在抽屉面板上
    const dialog = screen.getByRole('dialog')
    expect(dialog.className).toContain('w-full')
    expect(dialog.className).not.toContain('w-drawer-wide')
  })

  it('换到深度研究 tab 时快捷问题换成研究口径', async () => {
    stubFetch(() => Promise.resolve(json({ reply: 'ok' })))
    render(
      <ResearchAiDock
        code="600000"
        name="浦发银行"
        scope={{ tab: 'research', date: '2026-11-06' }}
        overview={OVERVIEW}
      />,
    )
    await openDock()

    expect(screen.getByRole('button', { name: '浦发银行这份研究的裁决点是什么' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '浦发银行哪些数据有缺口' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '浦发银行反证部分说了什么' })).toBeTruthy()
  })
})

describe('发送与上下文载荷', () => {
  it('点快捷问题发送，请求只带白名单字段', async () => {
    const calls = stubFetch(() =>
      Promise.resolve(json({ reply: '最近三根日线收盘 10.2 → 10.3。', model: 'test', fetched_at: 'x' })),
    )
    renderDock()
    await openDock()
    await screen.findByText('3 条')

    fireEvent.click(screen.getByRole('button', { name: '浦发银行最近的走势怎么样' }))

    expect(await screen.findByText('最近三根日线收盘 10.2 → 10.3。')).toBeTruthy()
    const post = calls.find((call) => call.method === 'POST')
    expect(post?.url).toBe('/api/research/600000/assistant')
    expect(post?.headers['X-LDST-Client']).toBe('1')

    const body = post?.body as { question: string; context: string; date: string; tab: string }
    expect(body.question).toBe('浦发银行最近的走势怎么样')
    expect(body.date).toBe('2026-11-06')
    expect(body.tab).toBe('overview')

    const context = JSON.parse(body.context) as Record<string, unknown>
    expect(context).toMatchObject({
      page: '个股深挖',
      code: '600000',
      name: '浦发银行',
      tab: 'overview',
      quote: { price: 10.3, changePct: -0.96 },
      kline: { bars: 3, start: '2026-11-04', end: '2026-11-06' },
      earnings: { periods: 2, latestReportDate: '2026-09-30' },
      events: { total: 3 },
    })
    const text = body.context.toLowerCase()
    for (const forbidden of ['thesis', 'holding', 'position', 'token', 'api_key', 'secret']) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('Enter 发送，Shift+Enter 只换行', async () => {
    const calls = stubFetch(() => Promise.resolve(json({ reply: '收到。' })))
    renderDock()
    await openDock()
    await screen.findByText('3 条')

    const input = screen.getByLabelText('向研究助手提问')
    fireEvent.change(input, { target: { value: '有哪些事件' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(await screen.findByText('收到。')).toBeTruthy()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  it('发送中显示停止，点停止后保留一条可读的停止提示', async () => {
    stubFetch(
      (init) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          )
        }),
    )
    renderDock()
    await openDock()
    await screen.findByText('3 条')

    fireEvent.click(screen.getByRole('button', { name: '浦发银行最近的走势怎么样' }))
    expect(await screen.findByRole('button', { name: '停止' })).toBeTruthy()
    expect(screen.getByText('研究助手正在回答…')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(await screen.findByText('已停止本次回答。')).toBeTruthy()
  })
})

describe('降级文案', () => {
  it('未配置 Key：给出可读原因与重试', async () => {
    stubFetch(() =>
      Promise.resolve(
        json(
          {
            detail: '未配置 AI 通道：缺少 LDST_LLM_API_KEY。请到「设置 → 接入 AI」配置后再试。',
            code: 'ai_not_configured',
            retryable: true,
          },
          503,
        ),
      ),
    )
    renderDock()
    await openDock()
    await screen.findByText('3 条')

    fireEvent.click(screen.getByRole('button', { name: '浦发银行下一份财报要看什么' }))
    expect(await screen.findByText('研究助手未配置模型')).toBeTruthy()
    expect(screen.getByText(/LDST_LLM_API_KEY/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  })

  it('接口不可达：提示本机服务未连接', async () => {
    vi.stubGlobal('fetch', (input: string) => {
      if (String(input).endsWith('/assistant')) {
        return Promise.reject(new TypeError('Failed to fetch'))
      }
      if (String(input).endsWith('/earnings-brief')) return Promise.resolve(json(EARNINGS))
      return Promise.resolve(json(EVENTS))
    })
    renderDock()
    await openDock()
    await screen.findByText('3 条')

    fireEvent.click(screen.getByRole('button', { name: '浦发银行最近的走势怎么样' }))
    expect(await screen.findByText('本机服务未连接')).toBeTruthy()
  })

  it('超时：504 给出「回答超时」', async () => {
    stubFetch(() =>
      Promise.resolve(json({ detail: 'AI 响应超时（60s）', code: 'ai_timeout' }, 504)),
    )
    renderDock()
    await openDock()
    await screen.findByText('3 条')

    fireEvent.click(screen.getByRole('button', { name: '浦发银行最近的走势怎么样' }))
    expect(await screen.findByText('回答超时')).toBeTruthy()
  })
})
