import type { NoticeBlockPayload } from './types'
import { OUTER_MARK } from './BlockShell'
import { cn } from '../../lib/cn'
import { AiErrorNotice, type AiNoticeKind } from '../kit/AiErrorNotice'

/**
 * `notice` 块（任务书 §6.2）：**直接用** `AiErrorNotice`，这里只做两件小事——
 *
 * 1. 把载荷里的 `kind` 过一遍白名单：服务端多给一个前端不认识的 kind 时，
 *    退到 `internal`（泛化失败）而不是整块白屏。
 * 2. 透传重试 / 状态回调；整轮失败时告诉面板「这就是全部结果」，不摆「重试该块」。
 *
 * 文案（`message` / `section`）原样透传，不拼不改——§7 红线 4 同样要求叙述文本里没有数字，
 * 前端更不能替后端补一句带数字的说明。
 */

export const NOTICE_KINDS: readonly AiNoticeKind[] = [
  'network',
  'quota',
  'format',
  'truncated',
  'internal',
  'degraded',
  'missing_data',
]

export function asNoticeKind(kind: string): AiNoticeKind {
  return (NOTICE_KINDS as readonly string[]).includes(kind) ? (kind as AiNoticeKind) : 'internal'
}

export interface NoticeBlockProps {
  payload: NoticeBlockPayload
  wholeRoundFailure?: boolean
  onRetry?: () => void
  onOpenStatus?: () => void
}

export function NoticeBlock({ payload, wholeRoundFailure = false, onRetry, onOpenStatus }: NoticeBlockProps) {
  return (
    <div data-answer-block="notice" className={cn(OUTER_MARK, 'p-0')}>
      <AiErrorNotice
        kind={asNoticeKind(payload.kind)}
        section={payload.section}
        message={payload.message}
        wholeRoundFailure={wholeRoundFailure}
        onRetry={onRetry}
        onOpenStatus={onOpenStatus}
        className="border-0 bg-transparent"
      />
    </div>
  )
}
