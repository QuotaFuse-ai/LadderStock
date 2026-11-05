import { ChartLine } from 'lucide-react'

import { describeResearchError, type ResearchOverview } from '../../api/research'
import { Button, EmptyState, ErrorBlock, SectionCard, SkeletonText } from '../../components/ui'
import { EarningsSection } from '../../components/research/sections/EarningsSection'
import { TrendSection } from '../../components/research/sections/TrendSection'

/**
 * 个股深挖「概览」标签（QUOT-400 / QUOT-448）。
 *
 * 本页只负责概览自己依赖缓存的两块：行情条 + 走势 K 线 → 财报前瞻与复盘。
 * 页脚的事件回看 / 论点判断 / 研究报告 / 报告对比由 `ResearchDetailScreen` 的
 * `pageTail` 统一挂载，两个 tab 共用，保证整页块序不随 tab 变化。
 * 各区块自成一个组件文件（后续任务只改自己那一个）：
 * - 行情条 / 走势：`TrendSection`（QUOT-449 接 `KlineCard`）
 * - 财报前瞻：`EarningsSection`（QUOT-450）
 * - 事件回看：`EventSection`（QUOT-451）
 * - 论点判断：`ThesisSection`（QUOT-452）
 *
 * 数据只来自 `/api/research/{code}/overview`；缺失显示 `—` + 原因，不补 0。
 */

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载概览…</span>
      <SectionCard title="行情快照" subtitle="加载中">
        <SkeletonText lines={2} />
      </SectionCard>
      <SectionCard title="走势" subtitle="加载中">
        <SkeletonText lines={6} />
      </SectionCard>
      <SectionCard title="财报前瞻与复盘" subtitle="加载中">
        <SkeletonText lines={3} />
      </SectionCard>
    </div>
  )
}

export default function OverviewTab({
  overview,
  code,
  error,
  requesting,
  reload,
}: {
  overview: ResearchOverview | null
  code: string | null
  error: unknown
  requesting: boolean
  reload: () => void
}) {
  if (error !== null && error !== undefined) {
    const info = describeResearchError(error)
    return (
      <div className="flex flex-col gap-4">
        <ErrorBlock {...info} onRetry={reload} />
      </div>
    )
  }
  if (!overview) {
    return <OverviewSkeleton />
  }
  if (overview.dataSourceStatus === 'missing') {
    return (
      <div className="flex flex-col gap-4">
        <EmptyState
          icon={<ChartLine aria-hidden="true" />}
          title="概览数据缺失"
          description={
            overview.kline.reason ??
            `本地缓存里没有 ${code ?? overview.code} 的日线数据（该标的行情尚未同步），当前不会伪造任何数据；可在行情页同步后点「重试」。`
          }
          action={
            <Button variant="secondary" onClick={reload} loading={requesting}>
              重试
            </Button>
          }
        />
        <EarningsSection overview={overview} code={code} />
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <TrendSection overview={overview} code={code} />
      <EarningsSection overview={overview} code={code} />
      <p className="text-caption leading-body text-text-muted">
        口径：概览只读本机缓存（daily_k / spot_quote / finance_indicators），不依赖六段研究产物；
        缺数显示 — + 原因，不补 0、不联网取数。
      </p>
    </div>
  )
}
