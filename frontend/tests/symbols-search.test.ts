/**
 * 标的字典源（QUOT-405）：`httpSearchSources` 新增的 `symbol_dictionary`。
 *
 * 两组口径：
 * - 端点形状：代码 / 中文名称意图都打 `/api/symbols/search`，命中显示真实名称且
 *   `verified: true`（顶掉 `code_direct` 的「未核实」占位）；
 * - 竞态保护：换输入时上一轮 in-flight 作废，**晚到的旧字典响应不得覆盖新结果**
 *   （参考库 `liveMatches` 语义，`createSearchRunner` 的 `superseded` 契约）。
 *
 * fetch 夹具字段名抄自 `src/api.ts` 的 `symbolsSearch` 与后端 `web/symbols.py` 契约。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { createSearchRunner, httpSearchSources } from '../src/lib/searchCore'

const DATE = '2026-11-03'

function symbolsEnvelope(matches: Array<{ symbol: string; name: string; market?: string; board?: string }>) {
  return {
    domain: 'sentiment',
    topic: 'symbol_search',
    event_id: null,
    data_as_of: DATE,
    source: 'ladderstock',
    source_detail: 'local symbol cache',
    fetched_at: `${DATE}T15:00:00+08:00`,
    freshness: 'cached',
    confidence: 'medium',
    source_status: 'ok',
    payload: {
      query: 'q',
      limit: 10,
      count: matches.length,
      matches: matches.map((match) => ({
        symbol: match.symbol,
        name: match.name,
        market: match.market ?? null,
        kind: 'stock',
        board: match.board ?? null,
        pinyin: null,
      })),
      data_source_status: 'ok',
    },
  }
}

const OTHER_ENDPOINTS_404 = (url: string) => {
  const path = url.split('?')[0]
  if (path === '/api/symbols/search') return undefined
  return new Response('{"detail":"Not Found"}', { status: 404 })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('symbol_dictionary 源', () => {
  it('代码意图：打 /api/symbols/search，命中显示真实名称且不再是「未核实」占位', async () => {
    vi.stubGlobal('fetch', (input: string) => {
      const url = String(input)
      const fallback = OTHER_ENDPOINTS_404(url)
      if (fallback) return Promise.resolve(fallback)
      return Promise.resolve(
        new Response(JSON.stringify(symbolsEnvelope([{ symbol: '600000', name: '浦发银行', market: 'SH', board: '主板' }])), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })

    const result = await (await import('../src/lib/searchCore')).runSearch('600000', {
      date: DATE,
      sources: httpSearchSources,
    })
    const stocks = result.groups.find((group) => group.id === 'stocks')
    const hit = stocks?.hits.find((item) => item.key === '600000')
    expect(hit).toBeDefined()
    expect(hit).toMatchObject({
      name: '浦发银行',
      detail: 'SH · 主板',
      verified: true,
      route: '/research/600000',
    })
  })

  it('中文名称意图：把词原样传给服务端，命中 600519 贵州茅台', async () => {
    let asked = ''
    vi.stubGlobal('fetch', (input: string) => {
      const url = String(input)
      const fallback = OTHER_ENDPOINTS_404(url)
      if (fallback) return Promise.resolve(fallback)
      asked = url
      return Promise.resolve(
        new Response(JSON.stringify(symbolsEnvelope([{ symbol: '600519', name: '贵州茅台' }])), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })

    const { runSearch } = await import('../src/lib/searchCore')
    const result = await runSearch('茅台', { date: DATE, sources: httpSearchSources })
    expect(asked).toContain('/api/symbols/search?q=')
    expect(asked).toContain(encodeURIComponent('茅台'))
    const hit = result.groups
      .find((group) => group.id === 'stocks')
      ?.hits.find((item) => item.key === '600519')
    expect(hit).toMatchObject({ name: '贵州茅台', code: '600519', verified: true })
  })

  it('竞态保护：旧输入的字典响应晚到，结果必须整体作废（superseded）', async () => {
    const slowGate: { resolve: ((body: Response) => void) | null } = { resolve: null }
    vi.stubGlobal('fetch', (input: string) => {
      const url = String(input)
      const fallback = OTHER_ENDPOINTS_404(url)
      if (fallback) return Promise.resolve(fallback)
      if (url.includes('q=600000')) {
        return new Promise<Response>((resolve) => {
          slowGate.resolve = resolve
        })
      }
      return Promise.resolve(
        new Response(JSON.stringify(symbolsEnvelope([{ symbol: '600030', name: '中信证券' }])), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })

    const runner = createSearchRunner({ date: DATE, sources: httpSearchSources })
    const slow = runner.search('600000')
    const fast = await runner.search('600030')

    // 新一轮已定：600030 命中中信证券，且没有被取代
    expect(fast.superseded).toBe(false)
    const fastHit = fast.groups
      .find((group) => group.id === 'stocks')
      ?.hits.find((item) => item.key === '600030')
    expect(fastHit).toMatchObject({ name: '中信证券' })

    // 慢的那轮此刻才回来：调用方必须整体丢弃，600000 不得混进结果
    slowGate.resolve?.(
      new Response(JSON.stringify(symbolsEnvelope([{ symbol: '600000', name: '浦发银行' }])), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    const stale = await slow
    expect(stale.superseded).toBe(true)
  })
})
