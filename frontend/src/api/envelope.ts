/**
 * `LadderEnvelope` 外壳上的溯源字段（QUOT-331）。
 *
 * 各模块的解析层（`limitPool.payloadOf()` / `industry.unwrap()`）只取 `root.payload`，
 * envelope 顶层的 `freshness` / `source_status` / `data_as_of` / `fetched_at` 停在外壳上，
 * 屏侧拿不到只能退「未知」。这里统一读出，**缺的键就是 `null`**：不补默认值、不猜档位
 * （§5.5 R-06 要防的正是「编一个看起来合理的来源」）。
 */

import type { EnvelopeFreshness, EnvelopeSourceStatus } from '../api'

/** 后端 key 原样透传；未登记的档位由徽标退「未知」（同 `api/radar.ts` 的 `freshness: string`）。 */
export type FreshnessKey = EnvelopeFreshness | (string & {})
export type SourceStatusKey = EnvelopeSourceStatus | (string & {})

/** 徽标与来源行要用的外壳字段，与 `core/models.py:LadderEnvelope` 同名同义。 */
export interface EnvelopeMeta {
  freshness: FreshnessKey | null
  source_status: SourceStatusKey | null
  source_detail: string | null
  data_as_of: string | null
  fetched_at: string | null
}

/**
 * 载荷接口上追加的外壳键。声明为可选：只有走真实 HTTP 的解析层保证填值，
 * 各屏的演示源可以暂时没有（QUOT-331 先把「后端给了、解析层丢掉」这一段接上）。
 */
export interface EnvelopeFields {
  freshness?: FreshnessKey | null
  source_status?: SourceStatusKey | null
  source_detail?: string | null
  data_as_of?: string | null
  fetched_at?: string | null
}

function text(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/**
 * 读响应外壳；平面响应（没有 `payload` 对象那一层）没有外壳可读，
 * 返回全 `null`，屏侧据此出「未知」而不是消失。
 */
export function envelopeMetaOf(raw: unknown): EnvelopeMeta {
  const root =
    typeof raw === 'object' && raw !== null && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : null
  const wrapped = root !== null && typeof root.payload === 'object' && root.payload !== null
  if (!root || !wrapped) {
    return {
      freshness: null,
      source_status: null,
      source_detail: null,
      data_as_of: null,
      fetched_at: null,
    }
  }
  return {
    freshness: text(root.freshness),
    source_status: text(root.source_status),
    source_detail: text(root.source_detail),
    data_as_of: text(root.data_as_of),
    fetched_at: text(root.fetched_at),
  }
}
