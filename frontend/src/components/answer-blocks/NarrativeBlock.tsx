import { cn } from '../../lib/cn'

import type { NarrativeBlockPayload } from './types'
import { OUTER_MARK } from './BlockShell'

/**
 * `narrative` 块：**对话正文**（没调工具的那一轮，整段回答原样展示）。
 *
 * 为什么单独成块而不是借 `conclusion`：`conclusion` 的语义是「首句当结论、剥数字、
 * 正文 4 行折叠」。问候 / 能力说明 / 概念解释没有「首句即结论」这种结构，套上去
 * 只会把一条完整回答切成「结论·中性」+「补充说明（截断 4 行）」两截，还丢掉后面
 * 的内容——这正是用户看到的「先正常、再闪成骨架」。
 *
 * 本块**不重算、不摘要、不截断**：`text` 原样按行渲染，只做两件纯排版的事——
 * 保留空行、把连续的分点行（`- ` / `* ` / `1. ` …）并成一个列表。任何字符都不丢。
 */

const LIST_MARK_RE = /^\s*(?:[-*•·]|\d+[.、)])\s+/

type Line = { kind: 'text'; text: string } | { kind: 'bullet'; text: string } | { kind: 'blank' }

function splitLines(raw: string): Line[] {
  return raw.split(/\r?\n/).map((line) => {
    if (line.trim() === '') return { kind: 'blank' } as Line
    if (LIST_MARK_RE.test(line)) return { kind: 'bullet', text: line.replace(LIST_MARK_RE, '') } as Line
    return { kind: 'text', text: line } as Line
  })
}

/** 按行成组：连续 bullet 合并成一个列表，其余单行成段。空行只用来断组。 */
function groupLines(lines: Line[]): Array<{ kind: 'list'; items: string[] } | { kind: 'text'; text: string }> {
  const groups: Array<{ kind: 'list'; items: string[] } | { kind: 'text'; text: string }> = []
  let bullets: string[] | null = null
  for (const line of lines) {
    if (line.kind === 'bullet') {
      bullets = bullets ?? []
      bullets.push(line.text)
      continue
    }
    if (bullets) {
      groups.push({ kind: 'list', items: bullets })
      bullets = null
    }
    if (line.kind === 'text' && line.text.trim() !== '') {
      groups.push({ kind: 'text', text: line.text })
    }
  }
  if (bullets) groups.push({ kind: 'list', items: bullets })
  return groups
}

export function NarrativeBlock({ payload }: { payload: NarrativeBlockPayload }) {
  const groups = groupLines(splitLines(payload.text))

  return (
    <section data-answer-block="narrative" className={cn(OUTER_MARK, 'flex flex-col gap-2')}>
      {groups.map((group, index) =>
        group.kind === 'list' ? (
          <ul key={`list-${index}`} className="flex list-disc flex-col gap-1 pl-5 text-body leading-body text-text">
            {group.items.map((item, itemIndex) => (
              <li key={`item-${itemIndex}`} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <p key={`text-${index}`} className="break-words text-body leading-body text-text">
            {group.text}
          </p>
        ),
      )}
    </section>
  )
}
