/**
 * 「可恢复 / 需重取」判定（文档 §6.4、ROADMAP M1 验收）。
 *
 * 草稿类（reviewDraft / validationDraft）永远可恢复；
 * 行情快照（watchSnapshot）只在 TTL 内且交易日未变时才可信，
 * 否则标记 `needsRefetch`，UI 提示重取，而不是把旧值当新值展示。
 */

export type RefetchReason = 'empty' | 'ttl' | 'trade-date-changed' | 'clock-skew'

export interface Freshness {
  needsRefetch: boolean
  reason: RefetchReason | null
  ageSec: number | null
}

const CLOCK_SKEW_TOLERANCE_SEC = 60

/** 本地时区的交易日字符串（YYYY-MM-DD），与 `ldst --date` 口径一致。 */
export function localDateIso(now: Date = new Date()): string {
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function ageSeconds(fetchedAt: string | null, now: Date): number | null {
  if (!fetchedAt) return null
  const ts = Date.parse(fetchedAt)
  if (Number.isNaN(ts)) return null
  return Math.round((now.getTime() - ts) / 1000)
}

export function evaluateSnapshotFreshness(
  snapshot: { fetchedAt: string | null; tradeDate: string | null },
  ttlSec: number,
  now: Date = new Date(),
): Freshness {
  const age = ageSeconds(snapshot.fetchedAt, now)
  if (age === null || !snapshot.tradeDate) {
    return { needsRefetch: true, reason: 'empty', ageSec: age }
  }
  if (age < -CLOCK_SKEW_TOLERANCE_SEC) {
    // 本机时间被改动过：宁可重取，也不猜
    return { needsRefetch: true, reason: 'clock-skew', ageSec: age }
  }
  if (age > ttlSec) {
    return { needsRefetch: true, reason: 'ttl', ageSec: age }
  }
  if (snapshot.tradeDate !== localDateIso(now)) {
    return { needsRefetch: true, reason: 'trade-date-changed', ageSec: age }
  }
  return { needsRefetch: false, reason: null, ageSec: age }
}

export const FRESHNESS_LABELS: Record<RefetchReason, string> = {
  empty: '本机没有可用快照',
  ttl: '快照已超过 TTL',
  'trade-date-changed': '交易日已变更',
  'clock-skew': '本机时间异常',
}

export function describeFreshness(freshness: Freshness): string {
  if (!freshness.needsRefetch) return '快照在有效期内'
  const reason = freshness.reason ? FRESHNESS_LABELS[freshness.reason] : '快照不可用'
  return `${reason}，需重新取数`
}
