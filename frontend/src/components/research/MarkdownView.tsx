import { Fragment } from 'react'

import type { CitationIndex } from '../../lib/citation'
import { parseInline, parseMarkdown, type InlineToken } from '../../lib/citation'
import { cn } from '../../lib/cn'
import { NUMBER_CHIP } from './bits'

/**
 * 研究报告正文渲染（SPEC-RESEARCH-PROVENANCE §3）。
 *
 * 只认后端渲染器会写的语法；`[n]` 角标渲染成按钮并打开来源抽屉。
 * `〔未核实〕` 用 warn 语义色（不是新色板），并带文字说明，不靠颜色单独表达。
 */

/**
 * 行内标记的统一外壳（角标 / 〔未核实〕 / 无来源编号三兄弟共用）。
 *
 * 原来角标是 `align-super`：16px 高的方块压到基线以上，把 14px/1.6 的行盒顶到 ~31px，
 * 同一段里「有角标的行」比「没角标的行」高出一截，正文节奏被打断。
 * 现在贴着基线走（16px < 行盒上半部 15.2px + 下半部），行高不再随角标抖动，
 * 层级靠「更小一号 + 底色方块」表达，不靠上标位移。
 */
const MARK = 'mx-half align-baseline'

export interface MarkdownViewProps {
  markdown: string
  index: CitationIndex
  onCitation: (n: number) => void
  className?: string
}

function Inline({
  text,
  index,
  onCitation,
}: {
  text: string
  index: CitationIndex
  onCitation: (n: number) => void
}) {
  const tokens: InlineToken[] = parseInline(text)
  return (
    <>
      {tokens.map((token, position) => {
        const key = `${position}-${token.type}`
        if (token.type === 'text') return <Fragment key={key}>{token.text}</Fragment>
        if (token.type === 'strong') {
          return (
            <strong key={key} className="font-semibold text-text">
              {token.text}
            </strong>
          )
        }
        if (token.type === 'code') {
          return (
            <code key={key} className="ldst-num rounded-xs bg-surface-3 px-1 text-caption text-text">
              {token.text}
            </code>
          )
        }
        if (token.type === 'unverified') {
          return (
            <span
              key={key}
              className={cn(
                NUMBER_CHIP,
                MARK,
                'bg-warn-soft text-warn',
              )}
              title="未核实：该数字没有可追溯来源，不作为审计依据"
            >
              〔未核实〕
            </span>
          )
        }
        const source = index.byNumber.get(token.n)
        if (!source) {
          return (
            <span
              key={key}
              className={cn(NUMBER_CHIP, MARK, 'ldst-num text-text-muted')}
              title="该编号没有已核实的来源"
            >
              [{token.n}]
            </span>
          )
        }
        return (
          <button
            key={key}
            type="button"
            onClick={() => onCitation(token.n)}
            aria-label={`查看来源 ${token.n}：${source.dataset}`}
            className={cn(
              NUMBER_CHIP,
              MARK,
              'ldst-num bg-info-soft text-info underline-offset-2 transition-colors duration-fast ease-ldst hover:underline',
            )}
          >
            {token.n}
          </button>
        )
      })}
    </>
  )
}

const HEADING: Record<1 | 2 | 3, string> = {
  1: 'mt-6 text-section font-semibold text-text first:mt-0',
  2: 'mt-5 text-subtitle font-semibold text-text first:mt-0',
  3: 'mt-4 text-body font-semibold text-text first:mt-0',
}

export function MarkdownView({ markdown, index, onCitation, className }: MarkdownViewProps) {
  const blocks = parseMarkdown(markdown)
  return (
    <div className={cn('flex min-w-0 flex-col gap-3 break-words', className)}>
      {blocks.map((block, position) => {
        const key = `${position}-${block.kind}`
        if (block.kind === 'heading') {
          const Tag = `h${block.level + 1}` as 'h2' | 'h3' | 'h4'
          return (
            <Tag key={key} className={HEADING[block.level]}>
              <Inline text={block.text} index={index} onCitation={onCitation} />
            </Tag>
          )
        }
        if (block.kind === 'list') {
          return (
            <ul key={key} className="flex max-w-measure list-disc flex-col gap-1 pl-5 text-body leading-body text-text-secondary">
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`} className="break-words">
                  <Inline text={item} index={index} onCitation={onCitation} />
                </li>
              ))}
            </ul>
          )
        }
        if (block.kind === 'quote') {
          return (
            <blockquote
              key={key}
              className="max-w-measure rounded border-l-2 border-warn bg-warn-soft px-3 py-2 text-body leading-body text-text-secondary"
            >
              <Inline text={block.text} index={index} onCitation={onCitation} />
            </blockquote>
          )
        }
        if (block.kind === 'rule') {
          return <hr key={key} className="border-t border-border" />
        }
        return (
          <p key={key} className="max-w-measure break-words text-body leading-body text-text-secondary">
            <Inline text={block.text} index={index} onCitation={onCitation} />
          </p>
        )
      })}
    </div>
  )
}
