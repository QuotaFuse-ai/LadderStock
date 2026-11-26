import { useState } from 'react'
import { Play, Plus, X } from 'lucide-react'

import { Button } from '../../../components/ui'

/**
 * F2 计划卡：提问后先列一份「要查什么」，用户删掉不要的、补上想查的，再点开始。
 *
 * 只做前端编辑，不预判能不能查——服务端拿到的就是这几行字，做不到的步骤会在
 * 回答里如实说没查到。卡片默认收起追加输入，避免一上来就是一张表单。
 */

export interface PlanCardProps {
  question: string
  steps: string[]
  maxSteps: number
  onStart: (steps: string[]) => void
  onSkip: () => void
  onCancel: () => void
}

export function PlanCard({ question, steps, maxSteps, onStart, onSkip, onCancel }: PlanCardProps) {
  const [edited, setEdited] = useState<string[]>(steps)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')

  const canAdd = edited.length < maxSteps

  const addStep = () => {
    const title = draft.trim().slice(0, 24)
    if (!title || edited.includes(title)) {
      setDraft('')
      return
    }
    setEdited((prev) => (prev.length >= maxSteps ? prev : [...prev, title]))
    setDraft('')
    setAdding(false)
  }

  return (
    <section
      aria-label="研究计划"
      data-testid="plan-card"
      className="flex flex-col gap-2 rounded-2xl border border-border-strong bg-surface p-3"
    >
      <header className="flex items-center justify-between gap-2">
        <p className="text-13px font-medium text-text">这一轮打算这么查</p>
        <button
          type="button"
          onClick={onCancel}
          aria-label="取消计划"
          className="rounded-full p-1 text-text-muted transition-colors hover:text-text"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      </header>

      <p className="line-clamp-2 text-11px text-text-muted">{question}</p>

      <ol className="flex flex-col gap-1">
        {edited.map((title, index) => (
          <li
            key={`${title}-${index}`}
            className="flex items-center justify-between gap-2 rounded-lg bg-surface-3 px-2 py-1.5"
          >
            <span className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 text-11px text-text-muted">{index + 1}</span>
              <span className="truncate text-13px text-text">{title}</span>
            </span>
            <button
              type="button"
              onClick={() => setEdited((prev) => prev.filter((_, i) => i !== index))}
              aria-label={`移除「${title}」`}
              className="shrink-0 rounded-full p-1 text-text-muted transition-colors hover:text-text"
            >
              <X className="size-3" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ol>

      {adding ? (
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={draft}
            maxLength={24}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                addStep()
              }
            }}
            placeholder="再查一项，比如「股东户数」"
            aria-label="追加一步"
            className="min-w-0 flex-1 rounded-lg border border-border-strong bg-surface px-2 py-1 text-13px text-text"
          />
          <Button type="button" variant="secondary" onClick={addStep} disabled={!draft.trim()}>
            加上
          </Button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          disabled={!canAdd}
          className="inline-flex items-center gap-1 self-start rounded-pill border border-border px-2 py-1 text-11px text-text-secondary transition-colors hover:border-border-strong disabled:opacity-50"
        >
          <Plus className="size-3" aria-hidden="true" />
          再加一步
        </button>
      )}

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onSkip}
          className="text-11px text-text-muted underline-offset-2 transition-colors hover:text-text hover:underline"
        >
          不用计划，直接问
        </button>
        <Button
          type="button"
          variant="primary"
         
          onClick={() => onStart(edited)}
          disabled={edited.length === 0}
        >
          <Play className="mr-1 size-3" aria-hidden="true" />
          开始研究
        </Button>
      </div>
    </section>
  )
}
