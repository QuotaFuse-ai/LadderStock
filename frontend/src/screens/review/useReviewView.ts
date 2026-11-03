import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  api,
  type LadderEnvelope,
  type ReviewPayload,
  type SentimentResult,
} from '../../api'
import { useDemoEnabled, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync, type AsyncResult } from '../../lib/useAsync'
import { demoReviewSource, type ReviewDemoVariant } from './demo'

/**
 * 复盘两屏的取数 / 状态推导（与页面 DOM 解耦，便于测试）。
 *
 * QUOT-344-S2 / DEC-049 把复盘拆成数据面与模型面两个屏，两屏共用这一层：
 * - `useReviewView` → 每日复盘 `/review`：`/api/review`（程序指标）+ `/api/sentiment`（三张指标卡）
 * - `useAiReviewView` → AI复盘 `/review/ai`：只有 `/api/review`（AI 段落与逐段状态）
 *
 * 状态优先级沿用 `pageState.ts`：error > offline > partial > loading > empty > normal。
 * 演示源的唯一入口是 `useDemoEnabled()`（URL `?demo=1` 或设置页开关）；
 * **真实请求 404 / 断连一律不回落到 `demo.ts`**，页面进空态 / 离线态。
 */

export interface ReviewQuery {
  date: string
}

/** 复盘页需要两条读接口：复盘正文（AI 五段）+ 程序指标（三张指标卡）。 */
export interface ReviewSource {
  loadReview: (query: ReviewQuery, signal?: AbortSignal) => Promise<LadderEnvelope<ReviewPayload>>
  loadSentiment: (
    query: ReviewQuery,
    signal?: AbortSignal,
  ) => Promise<LadderEnvelope<{ sentiment: SentimentResult }>>
}

export const httpReviewSource: ReviewSource = {
  loadReview: (query, signal) => api.review(query.date, signal),
  loadSentiment: (query, signal) => api.sentiment(query.date, signal),
}

const VARIANTS: readonly ReviewDemoVariant[] = ['full', 'partial', 'empty']

function isDemoVariant(value: unknown): value is ReviewDemoVariant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

/** §5.6 的演示开关判定只有一处真源（`lib/pageState.ts`）；这里保留屏侧惯用名。 */
export function useReviewDemoEnabled(): boolean {
  return useDemoEnabled()
}

function useDemoVariant(): ReviewDemoVariant {
  const [params] = useSearchParams()
  const forced = useForcedState()
  const explicit = params.get('variant')
  if (isDemoVariant(explicit)) return explicit
  if (forced === 'partial') return 'partial'
  if (forced === 'empty') return 'empty'
  return 'full'
}

/**
 * 演示源 / 真实源的唯一选择点：两个屏都用它，`?demo=1` 与设置开关的行为因此不可能分叉。
 */
function useReviewSource(): {
  demo: boolean
  variant: ReviewDemoVariant
  forced: PageState | null
  source: ReviewSource
} {
  const demo = useReviewDemoEnabled()
  const variant = useDemoVariant()
  const forced = useForcedState()
  const source = useMemo<ReviewSource>(
    () => (demo ? demoReviewSource(variant) : httpReviewSource),
    [demo, variant],
  )
  return { demo, variant, forced, source }
}

export interface ReviewView {
  demo: boolean
  variant: ReviewDemoVariant
  forced: PageState | null
  review: AsyncResult<LadderEnvelope<ReviewPayload>>
  sentiment: AsyncResult<LadderEnvelope<{ sentiment: SentimentResult }>>
  reloadAll: () => void
}

/** 每日复盘（数据面）：复盘正文里的程序指标 + 三张指标卡的程序情绪读数。 */
export function useReviewView(query: ReviewQuery): ReviewView {
  const { demo, variant, forced, source } = useReviewSource()

  const review = useAsync((signal) => source.loadReview({ date: query.date }, signal), [
    source,
    query.date,
  ])
  const sentiment = useAsync((signal) => source.loadSentiment({ date: query.date }, signal), [
    source,
    query.date,
  ])
  const reloadAll = useMemo(
    () => () => {
      review.reload()
      sentiment.reload()
    },
    // 只跟随日期与数据源变化；reload 本身是稳定引用
    [source, query.date], // eslint-disable-line react-hooks/exhaustive-deps
  )

  return { demo, variant, forced, review, sentiment, reloadAll }
}

export interface AiReviewView {
  demo: boolean
  variant: ReviewDemoVariant
  forced: PageState | null
  review: AsyncResult<LadderEnvelope<ReviewPayload>>
  reloadAll: () => void
}

/**
 * AI复盘（模型面）只读 `/api/review` 一条：AI 段落、段级 `status`、`ai_failures[]`、
 * `ai.model` / `ai.degraded` 全在同一份产物里。三张指标卡走 `/api/sentiment`，那是数据面的事，
 * 这一屏不发这个请求——不然两屏会各自维护一份「程序指标」的读法。
 */
export function useAiReviewView(query: ReviewQuery): AiReviewView {
  const { demo, variant, forced, source } = useReviewSource()

  const review = useAsync((signal) => source.loadReview({ date: query.date }, signal), [
    source,
    query.date,
  ])
  const reloadAll = useMemo(
    () => () => {
      review.reload()
    },
    // 只跟随日期与数据源变化；reload 本身是稳定引用
    [source, query.date], // eslint-disable-line react-hooks/exhaustive-deps
  )

  return { demo, variant, forced, review, reloadAll }
}
