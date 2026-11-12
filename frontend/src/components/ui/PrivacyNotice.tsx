import { Lock } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * 隐私边界说明条（DEC-033 / SPEC-HOME-V2 §5.4；QUOT-559 去命令行）。
 *
 * 原来是 `radar/PositionsScreen.tsx` 的屏内私有件；首页「我的持仓」要同一份说明时，
 * 复制第二份文案就会让「同一句隐私承诺在两个页面上长得不一样」（§1-7「一份实现」、R-25 同源）。
 * 提到 `components/ui/` 是组件归位，不是新组件语义。
 *
 * 文案只在真实边界内说话：本机数据库、只读展示、无同步 / 分享入口。
 * QUOT-559 后不再出现 CLI 命令记号——「存在哪」说成本机文件，不再引用命令行。
 * 取数失败或降级时它**不消失**——隐私承诺不随数据状态改变（SPEC-HOME-V2 §5.3）。
 */
export interface PrivacyNoticeProps {
  className?: string
  /** 说明对象（持仓 / 自选）；默认「持仓」，旧调用点文案不变。 */
  subject?: string
  /** 已废弃（QUOT-559 去 CLI）：仅为旧调用点保留占位，不再渲染。 */
  storeCommand?: string
}

const NOTICE_TITLE = '数据只在本机，不上传、不进 AI'

/** 说明正文：纯文本版与组件渲染用同一份字符串，不另抄第二份文案。 */
function detailText(subject: string) {
  return `${subject}只保存在你电脑上的本地文件里，本页只在内存里展示本次请求结果；不写浏览器缓存，不进网址、日志或 AI 上下文，也没有同步 / 分享入口。需要导出时你自己在页面上点导出，才会有文件生成。`
}

/**
 * 纯文本版完整口径（QUOT-552：首页卡头「仅本机」徽标的 hover title 用）。
 * 与下方组件渲染自同一份字符串，不另抄第二份文案。
 */
export function privacyNoticeText(subject = '持仓', _storeCommand?: string): string {
  return `${NOTICE_TITLE}。${detailText(subject)}`
}

export function PrivacyNotice({ className, subject = '持仓' }: PrivacyNoticeProps) {
  return (
    <div
      role="note"
      aria-label="隐私说明"
      className={cn('flex items-start gap-3 rounded border border-accent bg-accent-soft px-3 py-2', className)}
    >
      <Lock className="mt-half size-4 shrink-0 text-accent" aria-hidden="true" />
      <div className="min-w-0 text-body leading-body">
        <p className="font-semibold text-text">{NOTICE_TITLE}</p>
        <p className="text-text-secondary">{detailText(subject)}</p>
      </div>
    </div>
  )
}
