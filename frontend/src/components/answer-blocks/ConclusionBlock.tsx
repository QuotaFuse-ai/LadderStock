import { useMemo } from 'react'
import { CircleAlert, CircleCheck, MinusCircle, TriangleAlert } from 'lucide-react'

import type { ConclusionTone, ConclusionBlockPayload } from './types'
import { PlainConclusionCard } from '../research/PlainConclusionCard'
import type { PlainConclusion } from '../../api/plainConclusion'

/**
 * `conclusion` 块：包一层 `PlainConclusionCard`（任务书 §6.2 / 阶段 E）。
 *
 * 映射规则（前端不重算、不补写）：
 * - `text`   → `plain.what.text`
 * - `confidence` → `plain.confidence`；载荷没给就不给置信度条（整块走降级态）
 * - `tone`   → 只在块标题上用一个文字标记。`plain-v1` 没有方向字段，
 *   涨跌语义色只允许作用于数字（§8.1），所以这里**不上色**，改用图标 + 文字
 * - 证据一律留空：整轮结论的出处由末尾 `citation` 块承担
 *
 * **`neutral: true` 是必须的**：`parsePlainConclusion` 会把「非中性且无证据」的论断丢掉，
 * 包出来的载荷必须能过同一套解析。`confidence` 缺失时 `PlainConclusionCard` 的解析
 * 也会把 `what` 丢掉，所以缺字段时**不伪造 0.5**，直接给 `null` 走它自己的降级态。
 */

const TONE_LABEL: Record<ConclusionTone, { text: string; icon: typeof MinusCircle }> = {
  neutral: { text: '中性', icon: MinusCircle },
  positive: { text: '偏多', icon: CircleCheck },
  negative: { text: '偏空', icon: CircleAlert },
  warning: { text: '需谨慎', icon: TriangleAlert },
}

/** 把块载荷折成 `plain-v1` 结构；`confidence` 缺失返回 `null` 交给卡片显示降级态。 */
export function toPlainConclusion(payload: ConclusionBlockPayload): PlainConclusion | null {
  if (payload.confidence === null) return null
  return {
    version: 'plain-v1',
    what: { text: payload.text, evidence: [], neutral: true },
    why: [],
    risks: [],
    confidence: payload.confidence,
    confidence_note: '',
    glossary: [],
    risk_flags: [],
    degraded: false,
    omitted_claims: 0,
    redline_blocked: 0,
  }
}

export function ConclusionBlock({ payload }: { payload: ConclusionBlockPayload }) {
  const plain = useMemo(() => toPlainConclusion(payload), [payload])
  const tone = TONE_LABEL[payload.tone]
  const Icon = tone.icon

  return (
    <div data-answer-block="conclusion" className="flex flex-col gap-2">
      <p className="flex items-center gap-1 text-caption text-text-muted">
        <Icon aria-hidden="true" className="size-3.5" />
        结论 · {tone.text}
      </p>
      <PlainConclusionCard plain={plain} disclaimer={null} />
    </div>
  )
}
