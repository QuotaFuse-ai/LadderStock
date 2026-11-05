import { FileText } from 'lucide-react'

import { Badge, EmptyState, SectionCard } from '../../ui'
import { LinkButton } from '../bits'

/**
 * 区块：研究报告（QUOT-448 落容器；正文仍在既有报告页）。
 *
 * props 契约：
 * - `code`：规范代码；
 * - `href`：研究报告页地址（`/research/:code/report?...`，由页面按当前取证参数拼好）；
 * - `stages`：六段合计 / 已落盘段数，用于副标题；缺省不猜。
 *
 * 本单不把报告正文搬进页面，只保留六段产物入口 —— 入口不许静默消失。
 */

export interface ReportSectionProps {
  code: string | null
  href: string | null
  stages?: { total: number; ready: number } | null
  className?: string
}

export function ReportSection({ code, href, stages, className }: ReportSectionProps) {
  const subtitle = stages
    ? `六段产物：已落盘 ${stages.ready} / ${stages.total}`
    : '六段产物与证据链'
  return (
    <SectionCard
      className={className}
      title="研究报告"
      subtitle={subtitle}
      actions={href ? undefined : <Badge tone="neutral">未选中标的</Badge>}
    >
      <EmptyState
        compact
        icon={<FileText aria-hidden="true" />}
        title={href ? `${code ?? '该标的'} 的六阶段研究报告` : '先在上面选一只股票'}
        description="正文、引用角标与「未核实」标记仍在研究报告页展示；本页只保留入口，不重复渲染一份可能过期的报告。"
        action={
          href ? (
            <LinkButton to={href} leadingIcon={<FileText className="size-3.5" aria-hidden="true" />}>
              打开研究报告
            </LinkButton>
          ) : undefined
        }
      />
    </SectionCard>
  )
}
