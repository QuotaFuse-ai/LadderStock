import { GitCompareArrows, Share2 } from 'lucide-react'

import { Badge, EmptyState, SectionCard } from '../../ui'

/**
 * 区块 I：报告对比 + 分享卡（QUOT-448 只落容器与挂载点）。
 *
 * props 契约（后续 QUOT-453 只改本文件）：
 * - `code`：规范代码；
 * - `reportHref`：既有研究报告页地址；对比与分享都以「已完成的研究运行」为输入；
 * - `className`：页面给的栅格类。
 *
 * 两个区块在参考库里是同一张研究报告卡上的两处入口（`ReportCompare` +
 * `RunCompareCard` + `ShareCardPanel`），所以合成一个文件、两个挂载点。
 * 本单不实现对比算法，也不生成分享图；未接入如实标「待接入」。
 */

export interface CompareSectionProps {
  code: string | null
  reportHref: string | null
  className?: string
}

export function CompareSection({ code, reportHref, className }: CompareSectionProps) {
  return (
    <div className={className}>
      <SectionCard
        title="报告对比"
        subtitle="同一只股票两次研究的关键数据逐条对齐"
        actions={<Badge tone="warn">待接入</Badge>}
      >
        <EmptyState
          compact
          icon={<GitCompareArrows aria-hidden="true" />}
          title={`${code ?? '该标的'} 的报告对比尚未接入`}
          description="「和上次比变了什么」与「各次研究过程对比」由 QUOT-453 接入；本单只留位置，不编对比结果。"
        />
      </SectionCard>

      <SectionCard
        className="mt-4"
        title="分享卡"
        subtitle="只给通过最终校验的报告生成分享图"
        actions={<Badge tone="warn">待接入</Badge>}
      >
        <EmptyState
          compact
          icon={<Share2 aria-hidden="true" />}
          title="分享卡尚未接入"
          description={
            reportHref
              ? '分享卡生成由 QUOT-453 接入，依赖报告通过最终校验；在此之前不导出任何图片。'
              : '先在页头选一只股票并跑出研究报告，再谈分享。'
          }
        />
      </SectionCard>
    </div>
  )
}
