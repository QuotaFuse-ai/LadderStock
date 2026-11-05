import { FileClock } from 'lucide-react'

import type { ResearchOverview } from '../../../api/research'
import { MISSING, formatMoney } from '../../../lib/format'
import { Badge, EmptyState, MetricCard, SectionCard } from '../../ui'

/**
 * 区块 F：财报前瞻与复盘（QUOT-448 只落容器与挂载点）。
 *
 * props 契约（后续 QUOT-450 只改本文件）：
 * - `overview`：概览载荷；`financials` 段即当前唯一可用的本机财务指标缓存；
 * - `code`：规范代码，空态文案用。
 *
 * 参考库的完整区块（一致预期 / 过去 6 次财报 / 统计句 / 没取到的）依赖
 * QUOT-446 的 `/api/research/{code}/earnings-brief`，由 QUOT-450 接入。
 * 这里只保留 QUOT-400 已交付的财务指标卡，不新增取数、不编数字。
 */

export interface EarningsSectionProps {
  overview: ResearchOverview | null
  code: string | null
  className?: string
}

export function EarningsSection({ overview, code, className }: EarningsSectionProps) {
  const { financials, reason } = overview?.financials ?? { financials: null, reason: null }
  const metrics: Array<{ label: string; value: number | null; unit?: string }> = financials
    ? [
        { label: 'EPS', value: financials.eps, unit: '元' },
        { label: '营业收入', value: financials.revenue },
        { label: '归母净利润', value: financials.net_profit },
        { label: 'ROE', value: financials.roe, unit: '%' },
        { label: '营收同比', value: financials.revenue_yoy, unit: '%' },
        { label: '净利润同比', value: financials.profit_yoy, unit: '%' },
      ]
    : []

  return (
    <SectionCard
      className={className}
      title="财报前瞻与复盘"
      subtitle={
        financials
          ? `${financials.report_name ?? '最近一期'}（${financials.report_date ?? '日期缺失'}）原值`
          : '最近一期财报'
      }
      actions={<Badge tone={financials ? 'ok' : 'neutral'}>{financials ? '有缓存' : '缺数'}</Badge>}
    >
      {!overview ? (
        <div aria-busy="true" className="flex h-24 items-center justify-center text-caption text-text-muted">
          正在读取 {code ?? '该标的'} 的财务指标缓存…
        </div>
      ) : !financials ? (
        <EmptyState
          compact
          icon={<FileClock aria-hidden="true" />}
          title="本地无财务指标缓存"
          description={reason ?? undefined}
        />
      ) : (
        <div className="grid grid-cols-2 gap-3 rail:grid-cols-3 desk:grid-cols-3">
          {metrics.map(({ label, value, unit }) => (
            <MetricCard
              key={label}
              label={label}
              value={value === null ? MISSING : formatMoney(value)}
              unit={unit && value !== null ? unit : undefined}
              note={value === null ? '上游未提供' : undefined}
            />
          ))}
        </div>
      )}
      <p className="mt-3 border-t border-border pt-2 text-caption leading-body text-text-muted">
        口径：东财财务指标原值，缺失显示 —，不补 0。一致预期、过去 6 次财报明细与「没取到的」
        {' 由「财报前瞻与复盘」后续接入（依赖 /api/research/{code}/earnings-brief）。'}
      </p>
    </SectionCard>
  )
}
