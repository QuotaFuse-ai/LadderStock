import { ArrowUpRight, FileText } from 'lucide-react'

import type { CitationBlockPayload } from './types'
import { BlockShell } from './BlockShell'
import { cn } from '../../lib/cn'

/**
 * `citation` 块（任务书 §6.2 / §8.2「新建」）：来源行，整行可点跳原文。
 *
 * 【据名判】不混用 `lib/shareCard.ts`：那是把本页数据画成分享卡的导出器，
 * 不是来源清单；来源块的数据源是后端 `context.sources`。
 *
 * 后端 `validate_citation` 只强制 `name` + `fetched_at`；`url` / `symbol` / `as_of`
 * 都可缺省。缺链接的来源照常列出但不可点（不伪造 href），`symbol` / `as_of`
 * 缺就用 `—` 诚实显示（不猜代码、不猜日期）。
 * 有链接时整行是一个 `<a>`：键盘可 Tab、Enter 可开，并在新窗口打开（`rel="noreferrer"`），
 * 不把用户从当前对话里带走。
 */

function orDash(value: string | null): string {
  return value ?? '—'
}

const ROW_CLASS =
  'group flex items-center gap-2 rounded-xs px-2 py-1.5 text-caption transition-colors duration-fast ease-ldst'

export function CitationBlock({ payload }: { payload: CitationBlockPayload }) {
  return (
    <BlockShell type="citation" title="来源" meta={<span className="ldst-num">{payload.items.length} 条</span>}>
      <ul className="flex flex-col gap-1">
        {payload.items.map((item, index) => {
          const body = (
            <>
              <FileText aria-hidden="true" className="size-3.5 shrink-0 text-text-muted" />
              <span className="min-w-0 flex-1 truncate">
                <span
                  className={cn(
                    'font-medium',
                    item.url ? 'text-accent group-hover:text-accent-hover group-hover:underline' : 'text-text',
                  )}
                >
                  {item.name}
                </span>
                <span className="text-text-muted">
                  {' · '}
                  <span className="ldst-num">{orDash(item.symbol)}</span>
                  {' · 数据日 '}
                  <span className="ldst-num">{orDash(item.as_of)}</span>
                  {' · 抓取于 '}
                  <span className="ldst-num">{item.fetched_at}</span>
                </span>
              </span>
            </>
          )
          return (
            <li key={`${item.url ?? item.name}-${index}`}>
              {item.url ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className={cn(ROW_CLASS, 'hover:bg-surface-3')}
                >
                  {body}
                  <ArrowUpRight aria-hidden="true" className="size-3.5 shrink-0 text-text-muted" />
                  <span className="sr-only">（在新窗口打开原文）</span>
                </a>
              ) : (
                <div className={ROW_CLASS} title="该来源没有提供原文链接">
                  {body}
                  <span className="shrink-0 text-text-muted">无链接</span>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </BlockShell>
  )
}
