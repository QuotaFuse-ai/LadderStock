import { CircleAlert, CircleCheck, MinusCircle, TriangleAlert } from 'lucide-react'

import type { ConclusionTone, ConclusionBlockPayload } from './types'
import { OUTER_MARK } from './BlockShell'

/**
 * `conclusion` 块：对话专用的轻量结论卡（QUOT-604）。
 *
 * 为什么不套 `PlainConclusionCard`：那也是研究详情页的白话结论卡，按 `plain-v1`
 * 三段式（这是什么 / 为什么 / 风险点）加逐条证据渲染；对话块的载荷只有
 * `text / tone / confidence`，套上去必然缺段缺证据，只会降级。
 *
 * 映射规则（前端不重算、不补写）：
 * - `text`       → 正文原样展示，**恒有值**（后端 `strip_numbers(first_sentence(...))`）
 * - `confidence` → 有就给置信度条；没给（AI 不评级，后端刻意省略）就不画，附一行说明，
 *                  绝不补造 0.5 之类的数字
 * - `tone`       → 只作标题上的文字标记；涨跌语义色只作用于数字（§8.1），文本不上色
 */

const TONE_LABEL: Record<ConclusionTone, { text: string; icon: typeof MinusCircle }> = {
  neutral: { text: '中性', icon: MinusCircle },
  positive: { text: '偏多', icon: CircleCheck },
  negative: { text: '偏空', icon: CircleAlert },
  warning: { text: '需谨慎', icon: TriangleAlert },
}

/** 置信度按载荷数值展示，不评级；小数点后最多 4 位是格式化，不是重算。 */
function confidenceText(value: number): string {
  return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
}

export function ConclusionBlock({ payload }: { payload: ConclusionBlockPayload }) {
  const tone = TONE_LABEL[payload.tone]
  const Icon = tone.icon
  const confidence = payload.confidence

  return (
    <section data-answer-block="conclusion" className={`${OUTER_MARK} flex flex-col gap-2`}>
      <p className="flex items-center gap-1 text-caption text-text-muted">
        <Icon aria-hidden="true" className="size-3.5" />
        结论 · {tone.text}
      </p>
      <p className="break-words text-body leading-body text-text">{payload.text}</p>
      {confidence === null ? (
        <p className="text-caption leading-caption text-text-muted">本轮不提供置信度评级</p>
      ) : (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-caption text-text-secondary">置信度</span>
            <span className="ldst-num text-body font-semibold text-text">{confidenceText(confidence)}</span>
            <span className="text-caption text-text-muted">载荷原值 · 取值 0~1</span>
          </div>
          <div
            role="img"
            aria-label={`置信度 ${confidenceText(confidence)}（取值 0~1）`}
            className="h-1.5 w-full overflow-hidden rounded-pill bg-flat-soft"
          >
            <div className="h-full rounded-pill bg-accent" style={{ width: `${confidence * 100}%` }} />
          </div>
        </div>
      )}
    </section>
  )
}
