/**
 * `GET /api/intraday` / `GET /api/intraday/snapshot` 客户端解析（QUOT-302）。
 *
 * 夹具直接抄 `docs/specs/SPEC-INTRADAY-READ` §7 的原始 `curl` 输出，
 * 目的是把「前端字段名 = 后端实际字段名」钉住：改错一个键名，这里先红。
 */

import { describe, expect, it, vi } from 'vitest'

import {
  isIntradayEndpointMissing,
  isIntradayOffline,
  httpIntradaySource,
  isKnownSlotStatus,
  parseIntradaySnapshot,
  parseIntradaySummary,
  statusImpliesStored,
} from '../src/api/intraday'

const SUMMARY = {
  domain: 'sentiment',
  topic: 'intraday',
  event_id: null,
  data_as_of: '2026-10-30',
  payload: {
    date: '2026-10-30',
    available: true,
    reason: null,
    has_status_file: true,
    slot_count: 6,
    snapshot_count: 3,
    status_updated_at: '2026-10-30T14:00:00+08:00',
    slots: [
      {
        slot: '09:25',
        status: 'ok',
        recorded: true,
        available: true,
        reason: null,
        file: '0925.json',
        price_field: 'open',
        stocks: 3,
        fresh: 3,
        at: '2026-10-30T09:25:00+08:00',
        detail: '3/3 只昨日涨停股有今天读数，写入 0925.json',
      },
      {
        slot: '11:30',
        status: 'missing',
        recorded: true,
        available: false,
        file: null,
        price_field: null,
        stocks: null,
        fresh: null,
        at: '2026-10-30T11:30:00+08:00',
        reason: '3 只昨日涨停股没有一条今天（2026-10-30）的报价',
        detail: '3 只昨日涨停股没有一条今天（2026-10-30）的报价',
      },
      {
        slot: '15:00',
        status: 'missing',
        recorded: false,
        available: false,
        file: null,
        price_field: null,
        stocks: null,
        fresh: null,
        at: null,
        detail: null,
        reason: 'status.json 里没有 15:00 的记录：该时点没跑过',
      },
    ],
    data_source_status: 'ok',
  },
  source: 'ladderstock',
  source_detail: 'local intraday dir: 2026-10-30/（3/6 个时点有快照）',
  fetched_at: '2026-11-01T06:03:22.238635Z',
  freshness: 'cached',
  confidence: 'medium',
  source_status: 'ok',
}

describe('parseIntradaySummary', () => {
  it('逐字段读 SPEC-INTRADAY-READ §4.1 的每一个键', () => {
    const parsed = parseIntradaySummary(SUMMARY)
    expect(parsed.date).toBe('2026-10-30')
    expect(parsed.available).toBe(true)
    expect(parsed.has_status_file).toBe(true)
    expect(parsed.slot_count).toBe(6)
    expect(parsed.snapshot_count).toBe(3)
    expect(parsed.status_updated_at).toBe('2026-10-30T14:00:00+08:00')
    expect(parsed.data_source_status).toBe('ok')
    expect(parsed.slots).toHaveLength(3)
    expect(parsed.slots[0]).toEqual({
      slot: '09:25',
      status: 'ok',
      recorded: true,
      available: true,
      reason: null,
      file: '0925.json',
      detail: '3/3 只昨日涨停股有今天读数，写入 0925.json',
      price_field: 'open',
      stocks: 3,
      fresh: 3,
      at: '2026-10-30T09:25:00+08:00',
    })
  })

  it('缺字段一律 null，不补 0（DEC-028）', () => {
    const parsed = parseIntradaySummary({
      payload: { slots: [{ slot: '09:35' }], available: false, slot_count: 1, snapshot_count: 0 },
    })
    const row = parsed.slots[0]
    expect(row.status).toBe('missing')
    expect(row.recorded).toBe(false)
    expect(row.available).toBe(false)
    expect(row.stocks).toBeNull()
    expect(row.fresh).toBeNull()
    expect(row.price_field).toBeNull()
    expect(row.at).toBeNull()
    expect(row.file).toBeNull()
  })

  it('未知状态原样透出，不改写成已知项', () => {
    const parsed = parseIntradaySummary({
      payload: { slots: [{ slot: '09:25', status: 'queued', recorded: true, available: false }] },
    })
    expect(parsed.slots[0].status).toBe('queued')
    expect(isKnownSlotStatus('queued')).toBe(false)
    expect(isKnownSlotStatus('degraded')).toBe(true)
  })

  it('只有 ok / degraded / already 算「落过盘」（同后端 _INTRADAY_STORED）', () => {
    expect(statusImpliesStored('already')).toBe(true)
    expect(statusImpliesStored('skipped')).toBe(false)
    expect(statusImpliesStored('nope')).toBe(false)
  })

  it('没有 payload 外壳也读得动，但不是对象就报错', () => {
    expect(parseIntradaySummary({ available: false, slots: [] }).slots).toEqual([])
    expect(() => parseIntradaySummary('nope')).toThrow(/不是对象/)
  })
})

describe('parseIntradaySnapshot', () => {
  const ENVELOPE = {
    domain: 'sentiment',
    topic: 'intraday_snapshot',
    data_as_of: '2026-10-30',
    payload: {
      date: '2026-10-30',
      slot: '10:00',
      available: true,
      reason: null,
      data_source_status: 'ok',
      snapshot: {
        date: '2026-10-30',
        slot: '10:00',
        captured_at: '2026-10-30T10:00:06+08:00',
        price_field: 'price',
        source: {
          dataset: 'spot_quote',
          tool: 'tencent_quote_batch',
          source_detail: 'spot_quote_stub@stub',
          trail: ['spot_quote_stub@stub'],
        },
        yesterday_limit_up: {
          count: 3,
          fresh: 3,
          stale: 0,
          missing: [],
          overall: { count: 3, avg_change_pct: 2.43, up: 2, flat: 0, down: 1, limit_up: 1 },
          by_board: [{ board: '首板', count: 2, avg_change_pct: 1.1 }],
          top_board: {
            streak: 4,
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
            ],
          },
          stocks: [
            {
              code: '600745',
              yesterday_streak: null,
              reading_field: 'price',
              reading: 7.92,
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
          limit_down: 82,
          max_streak: 5,
          seal_rate_pct: 60.3,
          broken_rate_pct: 39.7,
          one_to_two: {
            available: true,
            yesterday_first_board: 2,
            today_second_board: 2,
            promoted: 0,
            rate_pct: 0.0,
          },
        },
      },
    },
  }

  it('读 out 采集侧原文，不重算', () => {
    const parsed = parseIntradaySnapshot(ENVELOPE)
    expect(parsed.slot).toBe('10:00')
    expect(parsed.snapshot?.price_field).toBe('price')
    expect(parsed.snapshot?.yesterday_limit_up?.count).toBe(3)
    expect(parsed.snapshot?.yesterday_limit_up?.overall?.avg_change_pct).toBe(2.43)
    expect(parsed.snapshot?.sentiment?.limit_down).toBe(82)
    expect(parsed.snapshot?.sentiment?.one_to_two?.rate_pct).toBe(0.0)
    expect(parsed.snapshot?.source.trail).toEqual(['spot_quote_stub@stub'])
  })

  it('at_limit_up 没给就是 null（不拿涨幅阈值猜）；y_streak 缺就是 null（不当 0）', () => {
    const parsed = parseIntradaySnapshot(ENVELOPE)
    const stock = parsed.snapshot?.yesterday_limit_up?.stocks[0]
    expect(stock?.at_limit_up).toBeNull()
    expect(stock?.yesterday_streak).toBeNull()
    expect(stock?.fresh).toBe(false)
    expect(stock?.reason).toBe('报价日期不是今天')
  })

  it('不可用时 snapshot 为 null + 中文 reason', () => {
    const parsed = parseIntradaySnapshot({
      payload: { date: '2026-10-30', slot: '15:00', available: false, reason: '1500.json 不存在', snapshot: null },
    })
    expect(parsed.available).toBe(false)
    expect(parsed.snapshot).toBeNull()
    expect(parsed.reason).toBe('1500.json 不存在')
    expect(parsed.data_source_status).toBe('missing')
  })

  it('采集侧新增的键进 extra，不丢也不解读', () => {
    const parsed = parseIntradaySnapshot({
      payload: { available: true, slot: '10:00', snapshot: { slot: '10:00', brand_new: { a: 1 } } },
    })
    expect(parsed.snapshot?.extra).toEqual({ brand_new: { a: 1 } })
  })
})

describe('httpIntradaySource', () => {
  it('按契约拼 URL：date 与 slot 都带', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(JSON.stringify(SUMMARY), { headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await httpIntradaySource.summary({ date: '2026-10-30' })
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/intraday?date=2026-10-30')

    const snapshotMock = vi.fn(
      async (_input: RequestInfo | URL) => new Response(JSON.stringify({ payload: { available: false } }), { status: 200 }),
    )
    vi.stubGlobal('fetch', snapshotMock)
    await httpIntradaySource.snapshot({ date: '2026-10-30', slot: '09:25' })
    expect(snapshotMock.mock.calls[0]?.[0]).toBe('/api/intraday/snapshot?date=2026-10-30&slot=09%3A25')
    vi.unstubAllGlobals()
  })

  it('404 是「接口未接入」，不是取数失败；断连是离线', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Not Found"}', { status: 404 })))
    await expect(httpIntradaySource.summary({ date: '2026-10-30' })).rejects.toSatisfy(isIntradayEndpointMissing)

    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed') }))
    await expect(httpIntradaySource.summary({ date: '2026-10-30' })).rejects.toSatisfy(isIntradayOffline)
    vi.unstubAllGlobals()
  })

  it('403 保留后端 detail（回环独占闸门）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"盘中快照只接受回环来源"}', { status: 403 })),
    )
    await expect(httpIntradaySource.summary({ date: '2026-10-30' })).rejects.toThrow(/盘中快照只接受回环来源/)
    vi.unstubAllGlobals()
  })
})
