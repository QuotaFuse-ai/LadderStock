import type { ReviewAiFailure } from '../../api'
import type { SectionTone } from '../../components/ui'
import type { ReviewSectionKey } from '../../../stores/reviewDraft'

/**
 * 复盘三屏的同源解析层（QUOT-344-S2 / DEC-047）。
 *
 * `/review`（数据面）与 `/review/ai`（模型面）读的是**同一份** `/api/review` 产物：
 * `review/<date>.json` 里 `sections[].metrics` 由程序直出、`sections[].narrative` 由
 * `ai/orchestrator.py` 写入，两屏各取一半，所以解析只能有一处实现——否则两屏对同一天
 * 会读出不同的段落数、状态与缺失口径。
 *
 * `review` 是 `Record<string, unknown>`：逐字段防御性取值，结构不符就当没有，不猜、不补默认值。
 */

/** 五段固定顺序里 payload 用的段名（草稿分片键 `dragonTiger` 对应 `dragon_tiger`）。 */
export const SECTION_PAYLOAD_NAME: Record<ReviewSectionKey, string> = {
  sentiment: 'sentiment',
  capital: 'capital',
  theme: 'theme',
  dragonTiger: 'dragon_tiger',
  leader: 'leader',
}

/** payload 段名 → 草稿分片键（定位、按段重试都要来回映射）。 */
export const PAYLOAD_SECTION_NAME: Record<string, ReviewSectionKey> = {
  sentiment: 'sentiment',
  capital: 'capital',
  theme: 'theme',
  dragon_tiger: 'dragonTiger',
  leader: 'leader',
}

/** 段状态 → 左侧 3px 竖条 + 文字徽标双重编码（UI-SPEC §4.5）。 */
export const SECTION_STATUS: Record<
  string,
  { label: string; tone: SectionTone; badge: 'ok' | 'warn' | 'danger' | 'info' | 'neutral' }
> = {
  pending: { label: '待生成', tone: 'neutral', badge: 'neutral' },
  running: { label: '生成中', tone: 'info', badge: 'info' },
  ok: { label: '已完成', tone: 'ok', badge: 'ok' },
  degraded: { label: '降级完成', tone: 'warn', badge: 'warn' },
  failed: { label: '失败', tone: 'danger', badge: 'danger' },
}

/**
 * §5.4「可重试」列：`truncated` 原样重试必然再截断、`internal` 未归类，两档都不给「重试」主按钮。
 * 与 `kit/AiErrorNotice.tsx` 的 `META[kind].retryable` 同口径；该表目前没对外导出，
 * 已作为规范缺口提给共享层（屏侧不应各自维护一份重试性判定）。
 */
const NON_RETRYABLE_KINDS: readonly string[] = ['truncated', 'internal']

export function isRetryableFailure(kind: string): boolean {
  return !NON_RETRYABLE_KINDS.includes(kind)
}

/** 页面级汇总用的短名（UI-SPEC §5.4「5 段中 2 段失败（网络 / 截断）」）。 */
const FAILURE_KIND_LABEL: Record<string, string> = {
  network: '网络',
  quota: '额度',
  format: '格式',
  truncated: '截断',
  internal: '未归类',
}

export function summariseFailureKinds(failures: ReviewAiFailure[]): string {
  return [...new Set(failures.map((failure) => FAILURE_KIND_LABEL[failure.kind] ?? failure.kind))].join(
    ' / ',
  )
}

export interface ReviewSectionView {
  name: string
  title: string
  status: string
  mode: string | null
  /** 生成该段的模型名；`null` = 没有模型跑过这一段（未接入，不是取数失败） */
  model: string | null
  degraded: boolean
  narrative: string[]
  notes: string[]
  /** 程序直出指标（`review/pipeline.collect_metrics`），数据面唯一的内容来源 */
  metrics: Record<string, unknown>
}

export interface ReviewHeaderView {
  generatedAt: string | null
  /** `pipeline._headline()` 的程序直出摘要——不是 AI 产物，所以留在数据面 */
  headline: string[]
  aiModel: string | null
  aiDegraded: boolean
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function asMetrics(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
}

export function parseSections(review: Record<string, unknown> | null): ReviewSectionView[] {
  if (!review || !Array.isArray(review.sections)) return []
  return review.sections.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const record = item as Record<string, unknown>
    const name = typeof record.name === 'string' ? record.name : null
    if (!name) return []
    return [
      {
        name,
        title: typeof record.title === 'string' ? record.title : name,
        status: typeof record.status === 'string' ? record.status : 'pending',
        mode: typeof record.mode === 'string' ? record.mode : null,
        model: typeof record.model === 'string' ? record.model : null,
        degraded: record.degraded === true,
        narrative: asStringList(record.narrative),
        notes: asStringList(record.notes),
        metrics: asMetrics(record.metrics),
      },
    ]
  })
}

export function parseHeader(review: Record<string, unknown> | null): ReviewHeaderView {
  if (!review) return { generatedAt: null, headline: [], aiModel: null, aiDegraded: false }
  const ai = review.ai && typeof review.ai === 'object' ? (review.ai as Record<string, unknown>) : null
  return {
    generatedAt: typeof review.generated_at === 'string' ? review.generated_at : null,
    headline: asStringList(review.headline),
    aiModel: ai && typeof ai.model === 'string' ? ai.model : null,
    aiDegraded: ai?.degraded === true,
  }
}

export interface SectionTally {
  failed: number
  degraded: number
  /** 有 AI 正文的段数（`narrative` 非空） */
  narrated: number
}

export function tallySections(sections: ReviewSectionView[]): SectionTally {
  return sections.reduce<SectionTally>(
    (tally, section) => {
      if (section.status === 'failed') tally.failed += 1
      if (section.status === 'degraded') tally.degraded += 1
      if (section.narrative.length > 0) tally.narrated += 1
      return tally
    },
    { failed: 0, degraded: 0, narrated: 0 },
  )
}

/**
 * AI复盘屏的三-case 判定（QUOT-344-S2 的四态口径核心，验收看这个）。
 *
 * 不允许把「模型未配」显示成「数据缺失」，也不允许反过来：
 * - `no-artifact`：当天 `review/<date>.json` 根本不存在（`data_source_status=missing` 或正文为 null）
 *   → 引导先跑 `ldst review`，这是**数据**没生成，与 AI 无关；
 * - `model-unconfigured`：产物在、五段程序指标都在，但没有一个段落有模型跑过（`ai.model` 与逐段
 *   `model` 全为 `null`、段级 `degraded`、且没有 `ai_failures` 条目）
 *   → `ai/orchestrator.py:_generate` 的未配置分支就是这个形状，引导「接入 AI」；
 * - `has-narrative`：跑过模型，逐段按 `status` / `ai_failures[]` 标注（含段级失败与降级）。
 *
 * 有 `ai_failures` 就归到 `has-narrative`：那时模型配过、请求发出去过，失败是段级的，
 * 该给的是「重试该段」而不是「接入 AI」。
 */
export type AiReviewCase = 'no-artifact' | 'model-unconfigured' | 'has-narrative'

export function classifyAiCase(input: {
  hasArtifact: boolean
  header: ReviewHeaderView
  sections: ReviewSectionView[]
  failures: ReviewAiFailure[]
}): AiReviewCase {
  if (!input.hasArtifact) return 'no-artifact'
  const { sections, failures, header } = input
  if (sections.length === 0 || failures.length > 0) return 'has-narrative'
  const noModelRan =
    header.aiModel === null &&
    sections.every((section) => section.model === null && section.status !== 'ok')
  return noModelRan ? 'model-unconfigured' : 'has-narrative'
}
