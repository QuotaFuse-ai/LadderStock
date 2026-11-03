import { cn } from '../../lib/cn'
import type { EnvelopeFreshness, EnvelopeSourceStatus } from '../../api'
import { Badge, type BadgeTone } from './Badge'

/**
 * 溯源徽标同源（UI-SPEC §5.1 / §5.2 / §5.3，组件清单 §6；QUOT-309 审计聚合项 M-1）。
 *
 * 全仓曾有 4 份本地实现（`HomeScreen.tsx:69` / `MarketScreen.tsx:105` / `WeeklyScreen.tsx:166` /
 * `ReviewScreen.tsx:196`）和三份各自的 `FRESHNESS_LABEL`，后果是：
 * - `source_status` 只剩底色没有文字，`degraded` / `partial` 读不出来（违 §5.2 与 §9「状态不只靠颜色」）；
 * - `partial` 被错映射成 warn、`degraded` 被错映射成 danger，与 §5.2 的色序正好相反；
 * - `WeeklyScreen.tsx:176` 把 `realtime` / `archive` 的英文枚举直显在徽标上（违「后端 key 不得直显」）。
 *
 * 所以标签表、图形符号、严重度排序**只准写在这一个模块里**，任何屏不得自带 label map。
 *
 * **两枚并排，不合并成一个「健康度」**（§5.3：数据新 ≠ 数据全，是两条独立事实）。
 * 图形符号只给 `freshness` 定义（§5.1 的 ●/◐/○）；§5.2 没给 `source_status` 登记图形，
 * 这里不自创第三套符号，靠文字标签满足「颜色不是唯一编码」。
 */

/** §5.1 标签列。是「存档」不是「归档」——本模块是唯一真源。 */
export const FRESHNESS_LABEL: Record<EnvelopeFreshness, string> = {
  realtime: '实时',
  cached: '缓存',
  archive: '存档',
}

/** §5.2 标签列。 */
export const SOURCE_STATUS_LABEL: Record<EnvelopeSourceStatus, string> = {
  ok: '数据完整',
  degraded: '已降级',
  partial: '部分缺失',
}

/**
 * `data_source_status` 系契约（`api/radar.ts:27`、`api/industry.ts:80`、`api/limitPool.ts:18`、
 * `api/research.ts:122`）在 §5.2 三态之外还会给 `missing`——数据集整块没取到。
 * 规范没给它档位，本模块按「不轻于 `partial`」定成 danger；排序仍守 §5.3 四条不变。
 */
export type SourceStatusExtension = 'missing'

type FreshnessSpec = { label: string; glyph: string; tone: 'info' | 'warn' | 'neutral'; hint: (asOf: string) => string }
type SourceStatusSpec = { label: string; tone: 'ok' | 'warn' | 'danger'; hint: string }

/** §5.1 的标签 / 图形 / 色调 / 悬浮说明四列，一一对应表格行（`satisfies` 保留每个 key 的字面量档位）。 */
const FRESHNESS = {
  realtime: { label: FRESHNESS_LABEL.realtime, glyph: '●', tone: 'info', hint: (a) => `数据时间 ${a}，本窗内实时获取` },
  cached: { label: FRESHNESS_LABEL.cached, glyph: '◐', tone: 'warn', hint: (a) => `数据时间 ${a}，本地缓存回放，非实时` },
  archive: { label: FRESHNESS_LABEL.archive, glyph: '○', tone: 'neutral', hint: (a) => `历史存档，数据时间 ${a}` },
} satisfies Record<EnvelopeFreshness, FreshnessSpec>

/** §5.2 的标签 / 色调 / 说明三列：degraded 黄、partial 橙红——屏侧旧实现把这两条写反了。 */
const SOURCE_STATUS = {
  ok: { label: SOURCE_STATUS_LABEL.ok, tone: 'ok', hint: '主工具正常返回' },
  degraded: { label: SOURCE_STATUS_LABEL.degraded, tone: 'warn', hint: '同 peer 备工具或缓存回放' },
  partial: { label: SOURCE_STATUS_LABEL.partial, tone: 'danger', hint: '数据集缺项' },
} satisfies Record<EnvelopeSourceStatus, SourceStatusSpec>

/**
 * `missing` 是 `data_source_status` 系契约的第四档，§5.2 表里没有它，
 * 但 radar / industry / limitPool / research 的后端确会给（整块没取到）。
 * 档位定成 danger：整块缺失不可能比缺项更轻。§5.3 的四条排序不含它，`SOURCE_SEVERITY` 不动。
 */
const SOURCE_STATUS_EXTRA: Record<SourceStatusExtension, SourceStatusSpec> = {
  missing: { label: '未取到', tone: 'danger', hint: '数据集整块未取到' },
}

/**
 * §5.3 严重度排序，一次定死：**partial > degraded > cached > realtime**。
 * 排序里只有这四条，`archive` 与 `ok` 规范没给位次，本模块也不补位次（见 `worstSourceTone`）。
 */
export const SOURCE_SEVERITY = ['partial', 'degraded', 'cached', 'realtime'] as const

export type SourceSeverity = (typeof SOURCE_SEVERITY)[number]

/** `api/radar.ts:129` 的 `freshness: string` 是无约束类型（`radar/demo.ts` 里出现过 `'daily'`）。 */
export type FreshnessLike = EnvelopeFreshness | (string & {})
export type SourceStatusLike = EnvelopeSourceStatus | (string & {})

/** 徽标只用到 envelope 这几个字段，payload 类型不同也能传同一份。
 *
 * 允许 `null`：后端没给这一档时屏侧仍要出「未知」，而不是让徽标消失（QUOT-331）——
 * 「未登记」与「没有值」是同一类事实，都只能落到「不编造」那一档。
 */
export type SourceBadgeMeta = {
  freshness: FreshnessLike | null
  source_status: SourceStatusLike | null
  source_detail: string | null
  data_as_of: string | null
}

function titleOf(lines: Array<string | false | null | undefined>): string {
  return lines.filter((line): line is string => !!line).join('\n')
}

/** 后端 key 可能不在规范枚举里（`api/radar.ts:129`），查表统一走这两个「命中/未命中」入口。 */
function findFreshness(value: string | null | undefined): FreshnessSpec | null {
  if (!value) return null
  const keys = Object.keys(FRESHNESS) as string[]
  return keys.includes(value) ? FRESHNESS[value as EnvelopeFreshness] : null
}

/**
 * 只认已登记的档位（§5.2 三态 + 契约扩展的 `missing`）。
 * 调用点用它决定是否用后端给的那一档，未登记的 key 不猜档位（research 用它回落到 `source_status`）。
 */
export function asSourceBadgeStatus(value: string | null | undefined): SourceStatusLike | null {
  return value && findSourceStatus(value) ? (value as SourceStatusLike) : null
}

function findSourceStatus(value: string | null | undefined): SourceStatusSpec | null {
  if (!value) return null
  const extraKeys = Object.keys(SOURCE_STATUS_EXTRA) as string[]
  if (extraKeys.includes(value)) return SOURCE_STATUS_EXTRA[value as SourceStatusExtension]
  const keys = Object.keys(SOURCE_STATUS) as string[]
  return keys.includes(value) ? SOURCE_STATUS[value as EnvelopeSourceStatus] : null
}

export interface FreshnessBadgeProps {
  /** `null` = 后端外壳里没有这一档，徽标出「未知」，不回落成 `cached` */
  freshness: FreshnessLike | null
  /** `data_as_of`：悬浮说明里的数据时间 */
  asOf?: string | null
  sourceDetail?: string | null
  className?: string
}

/** `freshness` 三态徽标（§5.1）：文字 + ●/◐/○ + 色调，缺一不算合规。 */
export function FreshnessBadge({ freshness, asOf, sourceDetail, className }: FreshnessBadgeProps) {
  const spec = findFreshness(freshness)
  const asOfText = asOf ?? '未知'
  return (
    <Badge
      tone={spec ? spec.tone : 'neutral'}
      title={titleOf([
        spec
          ? spec.hint(asOfText)
          : freshness
            ? `未登记的 freshness：${freshness}（数据时间 ${asOfText}）`
            : `后端未提供 freshness（数据时间 ${asOfText}）`,
        sourceDetail,
      ])}
      icon={<Glyph symbol={spec ? spec.glyph : '○'} />}
      className={className}
    >
      {spec ? spec.label : '未知'}
    </Badge>
  )
}

export interface SourceStatusBadgeProps {
  status: SourceStatusLike | null
  /** 走了哪条备路 / 缺了哪些项：§5.2 要求 `degraded` 悬浮必须能说明路线 */
  detail?: string | null
  className?: string
}

/** `source_status` 三态徽标（§5.2）：三态都必须有文字标签，颜色只是辅助。 */
export function SourceStatusBadge({ status, detail, className }: SourceStatusBadgeProps) {
  const spec = findSourceStatus(status)
  return (
    <Badge
      tone={spec ? spec.tone : 'neutral'}
      title={titleOf([
        spec
          ? spec.hint
          : status
            ? `未登记的 source_status：${status}`
            : '后端未提供 source_status',
        status === 'partial' && '必须列出缺失项，否则不给结论',
        detail,
      ])}
      className={className}
    >
      {spec ? spec.label : '来源状态未知'}
    </Badge>
  )
}

function Glyph({ symbol }: { symbol: string }) {
  return (
    <span aria-hidden="true" className="text-caption leading-none">
      {symbol}
    </span>
  )
}

/**
 * 两枚并排（§5.3）：屏侧一处调用即得到同源结果。这是 `FreshnessBadge` + `SourceStatusBadge`
 * 的组合，不是一条合并的「健康度」。
 */
export function SourceBadges({ envelope, className }: { envelope: SourceBadgeMeta | null; className?: string }) {
  if (!envelope) return null
  return (
    <span className={cn('inline-flex items-center gap-1', className)}>
      <FreshnessBadge
        freshness={envelope.freshness}
        asOf={envelope.data_as_of}
        sourceDetail={envelope.source_detail}
      />
      <SourceStatusBadge status={envelope.source_status} />
    </span>
  )
}

/** `Banner` 只吃这四档；§5.3 的排序本来就只在这四档里取「最严重者」。 */
export type SourceTone = Extract<BadgeTone, 'ok' | 'warn' | 'danger' | 'info'>

const WORST_TONE: Record<SourceSeverity, SourceTone> = {
  partial: SOURCE_STATUS.partial.tone,
  degraded: SOURCE_STATUS.degraded.tone,
  cached: FRESHNESS.cached.tone,
  realtime: FRESHNESS.realtime.tone,
}

/**
 * 提示条取色：§5.3「颜色取二者最严重者」= 两条各自在 `SOURCE_SEVERITY` 里查位次，取靠前的那条。
 * 排序外的取值（`ok` / `archive`）不参与比较，退回另一条；`missing` 是 §5.2 表外的契约扩展档，
 * 本身比 `partial` 更严重，直接取它自己的色调；两条都不在排序里时按完整性判断：
 * `ok` 才是 `ok`，未登记的 key 一律按 `warn` 处理（宁可提示，不静默）。
 */
export function worstSourceTone(freshness: FreshnessLike | null, status: SourceStatusLike | null): SourceTone {
  const rankOf = (value: string | null): number =>
    value ? (SOURCE_SEVERITY as readonly string[]).indexOf(value) : -1
  const statusRank = rankOf(status)
  if (statusRank >= 0) return WORST_TONE[SOURCE_SEVERITY[statusRank]]
  if (status && status in SOURCE_STATUS_EXTRA) return SOURCE_STATUS_EXTRA[status as SourceStatusExtension].tone
  const freshnessRank = rankOf(freshness)
  if (freshnessRank >= 0) return WORST_TONE[SOURCE_SEVERITY[freshnessRank]]
  return status === 'ok' ? 'ok' : 'warn'
}
