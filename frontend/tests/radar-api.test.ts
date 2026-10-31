import { afterEach, describe, expect, it, vi } from 'vitest'

import { isApiError } from '../src/api'
import {
  httpRadarSource,
  isRadarEndpointMissing,
  parseCandidatePool,
  parseEventCalendar,
  parseNewsStream,
  parsePositions,
} from '../src/api/radar'

/**
 * R10 载荷解析：缺字段不补 0、`null` 保持 `null`、状态由来源账本推导。
 * 这些用例钉住「页面不重算指标」的前提——解析层也不重算。
 */

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('资讯流解析', () => {
  it('保留 R6 八键，可选 origins / industries 缺省为空数组', () => {
    const payload = parseNewsStream({
      ok: true,
      domain: 'news',
      topic: 'news_stream',
      data_as_of: '2026-10-30',
      payload: {
        window: { start: '2026-10-30', end: '2026-10-30' },
        sources: ['flash_cls'],
        summary: { items: 2, fetched: 3, events: 1 },
        sources_status: [
          { dataset: 'flash_cls', status: 'ok', items: 2, degraded: false },
          { dataset: 'research_report', status: 'error', items: 0, detail: '上游 502', degraded: false },
        ],
        extraction: { events: 1, by_type: { policy: 1 } },
        items: [
          {
            id: 'n1',
            source: 'flash_cls',
            title: '央行开展 MLF 操作',
            url: 'https://example.test/n1',
            published_at: '2026-10-30 09:00:00',
            related_codes: ['600519'],
            summary: '摘要',
            content_hash: 'h1',
          },
          { id: 'n2', source: 'flash_cls', title: '缺字段的条目', url: '' },
        ],
        events: [
          {
            id: 'e1',
            type: 'policy',
            event_date: '2026-10-30',
            title: '事件',
            item_id: 'n1',
            evidence: { snippet: '证据', dataset: 'flash_cls' },
          },
        ],
        data_source_status: 'partial',
      },
    })

    expect(payload.items).toHaveLength(2)
    expect(payload.items[1].published_at).toBeNull()
    expect(payload.items[0].industries).toEqual([])
    expect(payload.items[0].origins).toEqual([])
    expect(payload.events[0].label).toBe('政策发布')
    expect(payload.events[0].item_id).toBe('n1')
    expect(payload.data_source_status).toBe('partial')
    expect(payload.data_as_of).toBe('2026-10-30')
  })

  it('来源账本全挂且没有显式状态时推导为 missing，并写明原因', () => {
    const payload = parseNewsStream({
      payload: {
        window: { start: '2026-10-30', end: '2026-10-30' },
        sources_status: [
          { dataset: 'announcement_cninfo', status: 'error', items: 0, detail: '未连接上游' },
        ],
        items: [],
        events: [],
        exit_code: 1,
      },
    })
    expect(payload.data_source_status).toBe('missing')
    expect(payload.reason).toContain('巨潮公告')
    expect(payload.reason).toContain('未连接上游')
  })

  it('任一路降级（degraded）即使 status=ok 也算 partial', () => {
    const payload = parseNewsStream({
      payload: {
        sources_status: [
          { dataset: 'flash_cls', status: 'ok', items: 3, degraded: false },
          { dataset: 'research_report', status: 'ok', items: 1, degraded: true, detail: '缓存 2 天' },
        ],
        items: [{ id: 'n1', title: 't', url: 'u' }],
        exit_code: 0,
      },
    })
    expect(payload.data_source_status).toBe('partial')
  })
})

describe('事件日历解析', () => {
  const raw = {
    payload: {
      month: '2026-10',
      window: { start: '2026-10-01', end: '2026-10-31' },
      sections: [
        {
          key: 'earnings_disclosure',
          label: '财报预约披露日',
          status: 'no_source',
          entries: [],
          note: '暂无数据源',
          reason: '上游没有预约披露时间表工具',
          ask: '上游需求：增加 disclosure_schedule 数据集',
        },
        {
          key: 'lockup_expiry',
          label: '解禁日',
          status: 'sourced',
          data_source: 'lockup_calendar',
          entries: [
            {
              id: 'l1',
              date: '2026-10-20',
              code: '600519',
              section: 'lockup_expiry',
              label: '限售解禁',
              detail: '可流通 120 万股',
              source: 'lockup_calendar',
            },
          ],
        },
      ],
      extracted_events: [
        {
          id: 'c1',
          type: 'shareholder_meeting',
          date: '2026-10-28',
          title: '股东大会决议',
          codes: ['600519'],
          evidence: { snippet: '于 2026 年 10 月 28 日召开', dataset: 'announcement_cninfo' },
          date_basis: 'evidence_text',
        },
      ],
      exit_code: 0,
    },
  }

  it('no_source 的 reason / ask 原样保留，规则抽取事件带 basis', () => {
    const payload = parseEventCalendar(raw)
    const section = payload.sections[0]
    expect(section.status).toBe('no_source')
    expect(section.entries).toEqual([])
    expect(section.reason).toContain('预约披露')
    expect(section.ask).toContain('上游需求')
    expect(payload.extracted_events[0].basis).toBe('rule_extracted')
    expect(payload.extracted_events[0].date).toBe('2026-10-28')
    expect(payload.extracted_events[0].label).toBe('股东大会')
    expect(payload.data_source_status).toBe('ok')
  })

  it('有源小节降级 → partial；全部 no_source 且无抽取事件 → missing', () => {
    const degraded = parseEventCalendar({
      payload: {
        month: '2026-10',
        sections: [
          { key: 'lockup_expiry', status: 'sourced', degraded: true, entries: [] },
          { key: 'earnings_disclosure', status: 'no_source', entries: [] },
        ],
        extracted_events: [],
      },
    })
    expect(degraded.data_source_status).toBe('partial')

    const missing = parseEventCalendar({
      payload: {
        month: '2026-11',
        sections: [{ key: 'earnings_disclosure', status: 'no_source', entries: [] }],
        extracted_events: [],
        exit_code: 4,
      },
    })
    expect(missing.data_source_status).toBe('missing')
  })
})

describe('候选池解析', () => {
  it('null 分项保持 null（不补 0），absent_parts 与权重原样保留', () => {
    const payload = parseCandidatePool({
      payload: {
        date: '2026-10-30',
        available: true,
        height: 5,
        candidate_count: 1,
        candidates: [
          {
            code: '600003',
            name: '丙',
            score: 72.5,
            parts: { strength: 50, standing: 50, capital: 75, theme: 50, turnover: null },
            absent_parts: ['turnover'],
            raw: { boards: 3, turnover_rate: null, net_inflow: 12_000_000 },
          },
        ],
        weights: { strength: 0.3, standing: 0.25, capital: 0.2, theme: 0.15, turnover: 0.1 },
        formula: 'score = Σ(weight_i × part_i) / Σ(weight_i)',
        counts: { limit_pool_rows: 3 },
        missing: ['fund_flow'],
        data_source_status: 'partial',
      },
    })

    expect(payload.candidates[0].parts.turnover).toBeNull()
    expect(payload.candidates[0].parts.strength).toBe(50)
    expect(payload.candidates[0].raw.turnover_rate).toBeNull()
    expect(payload.candidates[0].absent_parts).toEqual(['turnover'])
    expect(payload.weights.turnover).toBeCloseTo(0.1)
    expect(payload.missing).toEqual(['fund_flow'])
    expect(payload.data_source_status).toBe('partial')
  })

  it('available=false 时状态缺省为 missing', () => {
    const payload = parseCandidatePool({
      payload: { date: '2026-10-30', available: false, reason: '没有涨停池快照', candidates: [] },
    })
    expect(payload.available).toBe(false)
    expect(payload.data_source_status).toBe('missing')
    expect(payload.reason).toBe('没有涨停池快照')
  })
})

describe('持仓解析', () => {
  it('market_value=null 保持 null，口径退回 cost，不把缺价当 0', () => {
    const payload = parsePositions({
      local_only: true,
      store: 'local_sqlite',
      data_source_status: 'partial',
      as_of: '2026-10-30',
      price_source: 'daily_k',
      position_count: 1,
      valuation: {
        total_cost: 10000,
        market_value: null,
        priced_cost: 0,
        unpriced_cost: 10000,
        coverage_pct: 0,
        unrealized_pnl: null,
        unrealized_pnl_pct: null,
      },
      items: [
        {
          code: '600000',
          cost: 10,
          qty: 1000,
          entry_date: '2026-10-01',
          industry: '银行',
          note: '',
          latest_price: null,
          market_value: null,
          unrealized_pnl: null,
          unrealized_pnl_pct: null,
        },
      ],
      concentration: { available: false, reason: '没有取到价格', basis: 'cost' },
      sector_distribution: { available: false, reason: '没有取到价格', basis: 'cost' },
    })

    expect(payload.items[0].latest_price).toBeNull()
    expect(payload.items[0].market_value).toBeNull()
    expect(payload.items[0].unrealized_pnl).toBeNull()
    expect(payload.valuation.market_value).toBeNull()
    expect(payload.valuation.unpriced_cost).toBe(10000)
    expect(payload.concentration.available).toBe(false)
    expect(payload.concentration.basis).toBe('cost')
    expect(payload.data_source_status).toBe('partial')
  })

  it('空持仓是 missing 而不是错误', () => {
    const payload = parsePositions({
      data_source_status: 'missing',
      position_count: 0,
      items: [],
      valuation: { total_cost: 0, market_value: null },
      concentration: { available: false },
      sector_distribution: { available: false },
    })
    expect(payload.position_count).toBe(0)
    expect(payload.items).toEqual([])
    expect(payload.data_source_status).toBe('missing')
  })
})

describe('HTTP 源', () => {
  it('代码参数重复拼接，limit 一并下发', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ ok: true, payload: { items: [], sources_status: [], exit_code: 0 } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await httpRadarSource.news({ date: '2026-10-30', codes: ['600519', '300750'], limit: 60 })

    const url = String((fetchMock.mock.calls[0] as unknown as [string])[0])
    expect(url).toBe('/api/news?date=2026-10-30&code=600519&code=300750&limit=60')
  })

  it('404 归类为「接口未接入」而不是普通错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })),
    )
    const error = await httpRadarSource.pool({ date: '2026-10-30' }).catch((cause: unknown) => cause)
    expect(isApiError(error)).toBe(true)
    expect(isRadarEndpointMissing(error)).toBe(true)
  })

  it('连接失败归类为 not-connected（页面进离线态）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed')
      }),
    )
    const error = await httpRadarSource.pool({ date: '2026-10-30' }).catch((cause: unknown) => cause)
    expect(isApiError(error)).toBe(true)
    if (isApiError(error)) expect(error.kind).toBe('not-connected')
  })

  it('持仓写入全部走 POST：upsert /api/positions，删除 /api/positions/{code}/delete', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ ok: true, local_only: true, store: 'local_sqlite', position: { code: '600519', cost: 10, qty: 100 } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await httpRadarSource.addPosition({ code: '600519', cost: 10, qty: 100 })
    await httpRadarSource.removePosition('600 519')

    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>
    expect(calls[0][0]).toBe('/api/positions')
    expect(calls[0][1].method).toBe('POST')
    expect(calls[1][0]).toBe('/api/positions/600%20519/delete')
    expect(calls[1][1].method).toBe('POST')
  })
})
