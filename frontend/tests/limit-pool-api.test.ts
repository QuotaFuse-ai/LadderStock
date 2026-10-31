import { describe, expect, it } from 'vitest'

import { parseLimitPool } from '../src/api/limitPool'

/**
 * 涨停样本载荷解析：`null` 保持 `null`、缺 code 的行丢弃、`count` 不重算。
 */

describe('涨停样本解析', () => {
  it('envelope 载荷逐键透传，缺失字段保持 null', () => {
    const payload = parseLimitPool({
      ok: true,
      domain: 'sentiment',
      topic: 'limit_pool',
      data_as_of: '2026-10-30',
      payload: {
        date: '2026-10-30',
        available: true,
        reason: null,
        source_detail: 'limit_pool: duckdb（2 条）',
        count: 2,
        data_source_status: 'ok',
        rows: [
          {
            code: '600000',
            name: '浦发银行',
            streak: 2,
            first_seal_time: '093015',
            last_seal_time: '142500',
            reason: '银行',
            trade_date: '2026-10-30',
          },
          {
            code: '600519',
            name: null,
            streak: null,
            first_seal_time: null,
            last_seal_time: null,
            reason: null,
            trade_date: null,
          },
        ],
      },
    })

    expect(payload.count).toBe(2)
    expect(payload.data_source_status).toBe('ok')
    expect(payload.rows[0].first_seal_time).toBe('093015')
    expect(payload.rows[1]).toEqual({
      code: '600519',
      name: null,
      streak: null,
      first_seal_time: null,
      last_seal_time: null,
      reason: null,
      trade_date: null,
    })
  })

  it('available=false 时保留中文原因与空行集', () => {
    const payload = parseLimitPool({
      payload: {
        date: '2026-10-29',
        available: false,
        reason: '当日涨停池未同步：先执行 ldst sync',
        source_detail: 'limit_pool: 未找到（0 条）',
        count: 0,
        data_source_status: 'missing',
        rows: [],
      },
    })

    expect(payload.available).toBe(false)
    expect(payload.reason).toContain('ldst sync')
    expect(payload.rows).toEqual([])
    expect(payload.data_source_status).toBe('missing')
  })

  it('缺 code 的行丢弃；数字封板时间补足 HHMMSS', () => {
    const payload = parseLimitPool({
      payload: {
        available: true,
        rows: [{ name: '无代码' }, { code: '000001', streak: '2', first_seal_time: 93015 }],
      },
    })

    expect(payload.rows).toHaveLength(1)
    expect(payload.rows[0].streak).toBe(2)
    expect(payload.rows[0].first_seal_time).toBe('093015')
    expect(payload.count).toBe(1)
  })
})
