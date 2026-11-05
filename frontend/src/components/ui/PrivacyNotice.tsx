import { Lock } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * 隐私边界说明条（DEC-033 / SPEC-HOME-V2 §5.4）。
 *
 * 原来是 `radar/PositionsScreen.tsx` 的屏内私有件；首页「我的持仓」要同一份说明时，
 * 复制第二份文案就会让「同一句隐私承诺在两个页面上长得不一样」（§1-7「一份实现」、R-25 同源）。
 * 提到 `components/ui/` 是组件归位，不是新组件语义。
 *
 * 文案只在真实边界内说话：本机 SQLite、只读展示、无同步 / 分享入口。
 * 取数失败或降级时它**不消失**——隐私承诺不随数据状态改变（SPEC-HOME-V2 §5.3）。
 */
export interface PrivacyNoticeProps {
  className?: string
  /** 说明对象（持仓 / 自选）；默认「持仓」，旧调用点文案不变。 */
  subject?: string
  /** 本机存储的 CLI 入口，只作为文案里的代码记号。 */
  storeCommand?: string
}

export function PrivacyNotice({
  className,
  subject = '持仓',
  storeCommand = 'ldst position',
}: PrivacyNoticeProps) {
  return (
    <div
      role="note"
      aria-label="隐私说明"
      className={cn('flex items-start gap-3 rounded border border-accent bg-accent-soft px-3 py-2', className)}
    >
      <Lock className="mt-half size-4 shrink-0 text-accent" aria-hidden="true" />
      <div className="min-w-0 text-body leading-body">
        <p className="font-semibold text-text">数据只在本机，不上传、不进 AI</p>
        <p className="text-text-secondary">
          {subject}保存在本机 SQLite（<code className="ldst-num">{storeCommand}</code>），本页只在内存里展示本次请求结果；
          不写 localStorage / IndexedDB，不进 URL、日志或 AI 上下文，也没有同步 / 分享入口。导出只在你显式运行 CLI 时发生。
        </p>
      </div>
    </div>
  )
}
