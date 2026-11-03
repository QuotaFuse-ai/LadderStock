import { Lock } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * 隐私说明条：`/positions` 与首页「我的持仓」共用同一份文案（SPEC-HOME-V2 §5.4 / R-25）。
 *
 * 两屏各写一份的结果必然是口径漂移——首页说「不上传」、持仓页说「只在内存里」，读者
 * 无法判断哪条是真的。这里只保留一处。
 */
export function PrivacyNotice({ className }: { className?: string }) {
  return (
    <div
      role="note"
      aria-label="隐私说明"
      className={cn(
        'flex items-start gap-3 rounded border border-accent bg-accent-soft px-3 py-2',
        className,
      )}
    >
      <Lock className="mt-half size-4 shrink-0 text-accent" aria-hidden="true" />
      <div className="min-w-0 text-body leading-body">
        <p className="font-semibold text-text">数据只在本机，不上传、不进 AI</p>
        <p className="text-text-secondary">
          持仓保存在本机 SQLite（<code className="ldst-num">ldst position</code>），本页只在内存里展示本次请求结果；
          不写 localStorage / IndexedDB，不进 URL、日志或 AI 上下文，也没有同步 / 分享入口。导出只在你显式运行 CLI 时发生。
        </p>
      </div>
    </div>
  )
}
