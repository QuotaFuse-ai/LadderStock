import { CalendarClock } from 'lucide-react'

import { Badge, EmptyState, SectionCard } from '../../ui'

/**
 * 区块 G：事件回看（QUOT-448 只落容器与挂载点）。
 *
 * props 契约（后续 QUOT-451 只改本文件）：
 * - `code`：规范代码，事件序列按它取；
 * - `date`：当前研究日（`YYYY-MM-DD`），事件窗口以此为右界；
 * - `className`：页面给的栅格类。
 *
 * 数据面由 QUOT-446 的 `GET /api/research/{code}/event-study` 提供
 * （7 类事件 + 相对沪深300 的窗口超额）。数据未就绪前这里只挂空壳，
 * 不写死任何事件条数或收益数字，避免把「没接入」说成「0 次」。
 */

export interface EventSectionProps {
  code: string | null
  date: string
  className?: string
}

export function EventSection({ code, date, className }: EventSectionProps) {
  return (
    <SectionCard
      className={className}
      title="事件回看"
      subtitle="事件前 5 个交易日 → 后 20 个交易日，相对沪深300 的累计超额"
      actions={<Badge tone="warn">待接入</Badge>}
    >
      <EmptyState
        compact
        icon={<CalendarClock aria-hidden="true" />}
        title={`${code ?? '该标的'} 的事件回看尚未接入`}
        description={`7 类事件（业绩预告 / 股东增持 / 股东减持 / 回购方案 / 机构调研 / 龙虎榜 / 限售解禁）与 ${date} 口径的超额收益，等待 event-study 接口上线后在本区块展示；样本不足 8 次时不给平均。`}
      />
    </SectionCard>
  )
}
