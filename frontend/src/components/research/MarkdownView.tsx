import { Fragment } from 'react'

import type { CitationIndex } from '../../lib/citation'
import { parseInline, parseMarkdown, type InlineToken } from '../../lib/citation'
import { cn } from '../../lib/cn'

/**
 * 研究报告正文渲染（SPEC-RESEARCH-PROVENANCE §3）。
 *
 * 只认后端渲染器会写的语法；`[n]` 角标渲染成按钮并打开来源抽屉。
 * `〔未核实〕` 用 warn 语义色（不是新色板），并带文字说明，不靠颜色单独表达。
 */

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
              className="mx-0.5 inline-flex items-center rounded-xs bg-warn-soft px-1 text-caption font-medium text-warn"
              title="未核实：该数字没有可追溯来源，不作为审计依据"
            >
              〔未核实〕
            </span>
          )
        }
        const source = index.byNumber.get(token.n)
        if (!source) {
          return (
            <span key={key} className="ldst-num text-caption text-text-muted" title="该编号没有已核实的来源">
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
            className="ldst-num mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-xs bg-info-soft px-1 align-super text-caption font-medium text-info hover:underline"
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
            <ul key={key} className="flex list-disc flex-col gap-1 pl-5 text-body leading-body text-text-secondary">
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>
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
              className="rounded border-l-2 border-warn bg-warn-soft px-3 py-2 text-body leading-body text-text-secondary"
            >
              <Inline text={block.text} index={index} onCitation={onCitation} />
            </blockquote>
          )
        }
        if (block.kind === 'rule') {
          return <hr key={key} className="border-t border-border" />
        }
        return (
          <p key={key} className="text-body leading-body text-text-secondary [overflow-wrap:anywhere]">
            <Inline text={block.text} index={index} onCitation={onCitation} />
          </p>
        )
      })}
    </div>
  )
}
