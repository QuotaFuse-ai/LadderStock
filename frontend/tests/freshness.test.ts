/**
 * 「可恢复 / 需重取」判定：草稿永远可信，快照只在 TTL 内且交易日未变时可信。
 */

import { describe, expect, it } from 'vitest'

import {
  evaluateSnapshotFreshness,
  localDateIso,
} from '../persist/freshness'

const NOW = new Date('2026-10-28T10:30:00+08:00')

function iso(offsetSec: number): string {
  return new Date(NOW.getTime() + offsetSec * 1000).toISOString()
}

describe('evaluateSnapshotFreshness', () => {
  it('TTL 内且交易日一致 → 可信', () => {
    const result = evaluateSnapshotFreshness(
      { fetchedAt: iso(-60), tradeDate: localDateIso(NOW) },
      300,
      NOW,
    )
    expect(result).toEqual({ needsRefetch: false, reason: null, ageSec: 60 })
  })

  it('超过 TTL → 需重取（reason=ttl）', () => {
    const result = evaluateSnapshotFreshness(
      { fetchedAt: iso(-301), tradeDate: localDateIso(NOW) },
      300,
      NOW,
    )
    expect(result.needsRefetch).toBe(true)
    expect(result.reason).toBe('ttl')
  })

  it('交易日变更 → 需重取（reason=trade-date-changed）', () => {
    const result = evaluateSnapshotFreshness(
      { fetchedAt: iso(-30), tradeDate: '2026-10-27' },
      300,
      NOW,
    )
    expect(result.needsRefetch).toBe(true)
    expect(result.reason).toBe('trade-date-changed')
  })

  it('没有快照 → 需重取（reason=empty）', () => {
    expect(evaluateSnapshotFreshness({ fetchedAt: null, tradeDate: null }, 300, NOW)).toMatchObject({
      needsRefetch: true,
      reason: 'empty',
    })
  })

  it('本机时间被改到未来 → 需重取（reason=clock-skew）', () => {
    const result = evaluateSnapshotFreshness(
      { fetchedAt: iso(600), tradeDate: localDateIso(NOW) },
      300,
      NOW,
    )
    expect(result.needsRefetch).toBe(true)
    expect(result.reason).toBe('clock-skew')
  })

  it('少量时钟抖动（< 60s）不算异常', () => {
    const result = evaluateSnapshotFreshness(
      { fetchedAt: iso(30), tradeDate: localDateIso(NOW) },
      300,
      NOW,
    )
    expect(result.needsRefetch).toBe(false)
  })

  it('边界：恰好等于 TTL 仍可信', () => {
    const result = evaluateSnapshotFreshness(
      { fetchedAt: iso(-300), tradeDate: localDateIso(NOW) },
      300,
      NOW,
    )
    expect(result.needsRefetch).toBe(false)
  })

  it('坏时间戳 → 需重取（reason=empty）', () => {
    const result = evaluateSnapshotFreshness(
      { fetchedAt: '不是时间', tradeDate: localDateIso(NOW) },
      300,
      NOW,
    )
    expect(result.needsRefetch).toBe(true)
    expect(result.reason).toBe('empty')
  })
})

describe('localDateIso', () => {
  it('按本地时区输出 YYYY-MM-DD，与 ldst --date 口径一致', () => {
    expect(localDateIso(new Date(2026, 0, 5, 23, 59))).toBe('2026-01-05')
  })
})
