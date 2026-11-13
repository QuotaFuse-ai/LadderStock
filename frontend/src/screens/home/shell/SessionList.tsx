import { MessageSquarePlus, Plus } from 'lucide-react'

import type { SessionSummary } from '../../../api/conversation'
import { formatInboxTime } from '../../../lib/format'
import { cn } from '../../../lib/cn'
import { Button, EmptyState, Skeleton } from '../../../components/ui'

/**
 * 会话列表（QUOT-582 / F3）：标题 + 时间 + 一行预览，数据来自阶段 C 的
 * `GET /api/conversation/sessions`（`turns` 是轮数，预览就写它，不臆造正文摘要）。
 *
 * 只做「选哪一条 / 新建一条」，改名在 `ChatPane` 的标题行；窄屏由 `HomeShell`
 * 塞进抽屉，本组件不知道自己在哪。
 */

export function sessionPreview(summary: SessionSummary): string {
  const turns = Math.max(0, summary.turns)
  return turns === 0 ? '还没有提问' : `${turns} 轮对话`
}

export function sessionTitle(summary: SessionSummary): string {
  return summary.title.trim() || '未命名对话'
}

export interface SessionListProps {
  sessions: SessionSummary[]
  status: 'loading' | 'ready' | 'error'
  activeId: string | null
  onSelect: (sessionId: string) => void
  onNew: () => void
  className?: string
}

export function SessionList({
  sessions,
  status,
  activeId,
  onSelect,
  onNew,
  className,
}: SessionListProps) {
  return (
    <nav aria-label="对话会话" className={cn('flex min-h-0 flex-col gap-2', className)}>
      <div className="flex items-center justify-between gap-2 px-1">
        <span className="text-caption font-medium text-text-secondary">最近对话</span>
        <Button
          size="md"
          variant="ghost"
          onClick={onNew}
          leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
        >
          新对话
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {status === 'loading' ? (
          <div className="flex flex-col gap-2 px-1 py-2" aria-busy="true" aria-live="polite">
            <span className="sr-only">正在加载对话列表…</span>
            {[0, 1, 2].map((index) => (
              <div key={index} className="flex flex-col gap-1.5">
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-2.5 w-1/2" />
              </div>
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <EmptyState
            compact
            icon={<MessageSquarePlus aria-hidden="true" />}
            title="还没有对话"
            description="在右边问一句试试：说股票名或代码，助手会据此给出结论和可跳转的页面。"
          />
        ) : (
          <ul className="flex flex-col gap-half">
            {sessions.map((session) => {
              const selected = session.session_id === activeId
              return (
                <li key={session.session_id}>
                  <button
                    type="button"
                    aria-current={selected ? 'true' : undefined}
                    onClick={() => onSelect(session.session_id)}
                    className={cn(
                      'flex w-full flex-col gap-half rounded border px-2 py-1.5 text-left transition-colors duration-fast ease-ldst',
                      selected
                        ? 'border-border-strong bg-surface-3'
                        : 'border-transparent hover:border-border hover:bg-surface-2',
                    )}
                  >
                    <span className={cn('truncate text-body', selected ? 'text-text' : 'text-text-secondary')}>
                      {sessionTitle(session)}
                    </span>
                    <span className="flex items-center gap-1.5 text-11px text-text-muted">
                      <span className="ldst-num shrink-0">{formatInboxTime(session.updated_at)}</span>
                      <span aria-hidden="true">·</span>
                      <span className="truncate">{sessionPreview(session)}</span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </nav>
  )
}
