import { NotebookPen } from 'lucide-react'

import { Badge, EmptyState, SectionCard } from '../../ui'

/**
 * 区块 H：我的论点与判断（QUOT-448 只落容器与挂载点）。
 *
 * props 契约（后续 QUOT-452 只改本文件）：
 * - `code`：规范代码，论点按标的落库；
 * - `name`：展示名称，空态文案用；
 * - `className`：页面给的栅格类。
 *
 * 参考库在这里给「可写论点 + 到期必判的裁决点 + 空态引导」，
 * 本地持久化与辩论裁决由 QUOT-452 接入。本单不写存储、不伪造空态数据。
 */

export interface ThesisSectionProps {
  code: string | null
  name: string | null
  className?: string
}

export function ThesisSection({ code, name, className }: ThesisSectionProps) {
  return (
    <SectionCard
      className={className}
      title="我的论点与判断"
      subtitle="写下判断，到期回看它对不对（本机持久化）"
      actions={<Badge tone="warn">待接入</Badge>}
    >
      <EmptyState
        compact
        icon={<NotebookPen aria-hidden="true" />}
        title={`${name ?? code ?? '该标的'} 还没有论点记录`}
        description="论点录入、到期日与裁决点由「我的论点与判断」后续接入；在接入前本区块不写入任何本地记录，也不展示示例论点。"
      />
    </SectionCard>
  )
}
