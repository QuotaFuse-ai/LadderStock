// @vitest-environment jsdom
/**
 * QUOT-381 / QUOT-380：首页两路读接口的形状归一。
 *
 * 后端回信封 + `payload.cells[]` + `price`，前端屏层吃 `payload.items[]` + `value`；
 * 自选回 `available` / `price_status` / `price_as_of`。这里钉住归一结果，
 * 形状漂移时先在单测里红，而不是明天真实测试时首页空白。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchIndices, fetchWatchlist } from '../src/api/home'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchIndices', () => {
  it('后端的 cells[] + price 摊平成 items[] + value，8 格顺序固定，缺格带原因', async () => {
    let call = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        call += 1
        if (call === 1) {
          return jsonResponse({
            domain: 'sentiment',
            topic: 'index_quote',
            data_as_of: '2026-11-03T07:00:00Z',
            freshness: 'snapshot',
            confidence: 'high',
            source_status: 'ok',
            payload: {
              cells: [
                { code: 'sh000001', market: 'SH', name: '上证指数', available: true, price: 3345.67, change: 12.3, change_pct: 0.37 },
                { code: 'sh000300', market: 'SH', name: '沪深300', available: true, price: 4012.5, change_pct: 0.52 },
                { code: 'sz399001', market: 'SZ', name: '深证成指', available: true, price: 10567.8, change_pct: -0.11 },
                { code: 'sz399006', market: 'SZ', name: '创业板指', available: false, price: null, change_pct: null, reason: '当日无行情' },
              ],
              footnote: '快照，不是逐笔实时',
              data_source_status: 'partial',
            },
          })
        }
        return jsonResponse({ detail: 'not found' }, 404)
      }),
    )

    const envelope = await fetchIndices()
    expect(envelope.data_as_of).toBe('2026-11-03T07:00:00Z')
    expect(envelope.payload.footnote).toBe('快照，不是逐笔实时')

    const items = envelope.payload.items
    expect(items.map((item) => item.name)).toEqual([
      '上证指数',
      '沪深300',
      '深证成指',
      '创业板指',
      '道琼斯',
      '纳斯达克综合',
      '恒生指数',
      '恒生科技',
    ])
    expect(items[0]).toMatchObject({ code: 'sh000001', market: 'cn', value: 3345.67, change_pct: 0.37 })
    expect(items[1].value).toBe(4012.5)
    expect(items[3]).toMatchObject({ value: null, change_pct: null, reason: '当日无行情' })
    for (const slot of items.slice(4)) {
      expect(slot.value).toBeNull()
      expect(slot.reason).toBeTruthy()
    }
    expect(items[4].market).toBe('us')
    expect(items[6].market).toBe('hk')
  })

  it('404 原样抛 http 错误，由屏层当块级「未接入」处理', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'not found' }, 404)))
    await expect(fetchIndices()).rejects.toMatchObject({ kind: 'http', status: 404 })
  })
})

describe('fetchWatchlist', () => {
  it('available / price_status=missing 不补 0，原因是后端给的 price_reason', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          local_only: true,
          available: true,
          watch_count: 2,
          price_source: 'tickhub_cn',
          price_as_of: '2026-11-03T07:00:00Z',
          data_source_status: 'partial',
          items: [
            {
              code: '600519',
              name: '贵州茅台',
              available: true,
              latest_price: 1520.3,
              change: 8.1,
              change_pct: 0.54,
              prev_close: 1512.2,
              price_as_of: '2026-11-03T07:00:00Z',
              price_status: 'ok',
            },
            {
              code: '000001',
              name: '平安银行',
              available: false,
              latest_price: null,
              change: null,
              change_pct: null,
              price_status: 'missing',
              price_reason: '当日无快照',
            },
          ],
        }),
      ),
    )

    const payload = await fetchWatchlist()
    expect(payload.watch_count).toBe(2)
    expect(payload.as_of).toBe('2026-11-03T07:00:00Z')
    expect(payload.price_source).toBe('tickhub_cn')
    expect(payload.items[0]).toMatchObject({ latest_price: 1520.3, change_pct: 0.54, change: 8.1 })
    expect(payload.items[1]).toMatchObject({ latest_price: null, change_pct: null, reason: '当日无快照' })
  })

  it('本机自选库不可用（available=false）→ 空列表 + 后端原因', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          local_only: true,
          store: 'sqlite',
          available: false,
          reason: '自选库未初始化，先运行 ldst watch list',
          watch_count: null,
          items: [],
          data_source_status: 'missing',
        }),
      ),
    )

    const payload = await fetchWatchlist()
    expect(payload.items).toEqual([])
    expect(payload.watch_count).toBeNull()
    expect(payload.reason).toBe('自选库未初始化，先运行 ldst watch list')
    expect(payload.data_source_status).toBe('missing')
  })
})
