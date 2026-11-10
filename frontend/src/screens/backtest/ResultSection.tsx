import { useMemo } from 'react'
import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'

import type { BacktestCostTier, BacktestRunCard, BacktestSegment } from '../../api/backtest'
import { EChart } from '../../charts/EChart'
import { useChartPalette } from '../../charts/cssVar'
import {
  Badge,
  DataTable,
  MetricCard,
  SectionCard,
  type Column,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING } from '../../lib/format'
import {
  amountText,
  buildEquityOption,
  commissionTierLabel,
  countText,
  equityAriaLabel,
  gatePassedCount,
  gateStatusText,
  metricDisplay,
  METRIC_META,
  percentText,
  SEGMENT_METRICS,
  segmentMetricDisplay,
} from './runCard'

/**
 * 结果区（QUOT-534）：指标卡 / 权益曲线 / 三档成本 / 校验门。
 *
 * 所有数字都直接读运行卡：指标卡用后端 `metrics_display`，曲线用 `equity_curve` 原始点，
 * 三档对比用 `gate.cost_tiers` 原值。前端不累计、不归一、不重算（红线）。
 */

function GateBlock({ card }: { card: BacktestRunCard }) {
  const { gate } = card
  return (
    <div
      role={gate.passed ? 'status' : 'alert'}
      className={cn(
        'rounded border px-4 py-3',
        gate.passed ? 'border-ok bg-ok-soft' : 'border-danger bg-danger-soft',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {gate.passed ? (
          <CheckCircle2 className="size-4 text-ok" aria-hidden="true" />
        ) : (
          <XCircle className="size-4 text-danger" aria-hidden="true" />
        )}
        <p
          className={cn(
            'text-subtitle font-semibold',
            gate.passed ? 'text-ok' : 'text-danger',
          )}
        >
          校验门{gateStatusText(gate)}
        </p>
        <Badge tone={gate.passed ? 'ok' : 'danger'}>{gatePassedCount(gate)} 项通过</Badge>
        <span className="ldst-num ml-auto text-caption text-text-muted">
          门槛 calc_id {card.calc_ids.gate ?? MISSING}
        </span>
      </div>

      {!gate.passed && gate.red_reasons.length > 0 && (
        <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-body font-medium text-danger">
          {gate.red_reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {gate.warnings.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 rounded border border-warn bg-surface px-3 py-2 text-caption text-text-secondary">
          {gate.warnings.map((warning) => (
            <li key={warning} className="flex gap-1.5">
              <AlertTriangle className="mt-half size-3.5 shrink-0 text-warn" aria-hidden="true" />
              {warning}
            </li>
          ))}
        </ul>
      )}

      {gate.checks.length > 0 && (
        <ul className="mt-3 flex flex-col divide-y divide-border rounded border border-border bg-surface">
          {gate.checks.map((check) => (
            <li key={check.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <Badge tone={check.status === 'pass' ? 'ok' : 'danger'}>
                {check.status === 'pass' ? '通过' : '未通过'}
              </Badge>
              <span className="text-body text-text">{check.label || check.key}</span>
              <span className="min-w-0 flex-1 text-caption text-text-secondary">
                {check.reason || (check.status === 'pass' ? '检查通过' : MISSING)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function MetricsGrid({ card }: { card: BacktestRunCard }) {
  return (
    <SectionCard
      title="核心指标"
      subtitle={`${METRIC_META.length} 项 · 数值与展示串均来自运行卡 metrics_display / metrics_calc_ids`}
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {METRIC_META.map((meta) => (
          <MetricCard
            key={meta.key}
            label={meta.label}
            value={metricDisplay(card, meta.key)}
            note={
              <>
                calc_id <span className="ldst-num">{card.metrics_calc_ids[meta.key] ?? MISSING}</span>
              </>
            }
          />
        ))}
      </div>
    </SectionCard>
  )
}

function EquityPanel({ card }: { card: BacktestRunCard }) {
  const palette = useChartPalette()
  const option = useMemo(() => buildEquityOption(card, palette), [card, palette])
  const lastBenchmark = card.benchmark.curve[card.benchmark.curve.length - 1]

  return (
    <SectionCard
      title="权益曲线"
      subtitle={`运行卡原始序列 · ${card.equity_curve.length} 个交易日 · 不做累计 / 归一 / 插值`}
      actions={
        <Badge tone="neutral" title={card.calc_ids.run_card ?? undefined}>
          卡 calc_id <span className="ldst-num">{card.calc_ids.run_card ?? MISSING}</span>
        </Badge>
      }
    >
      {option ? (
        <EChart option={option} height={320} ariaLabel={equityAriaLabel(card)} />
      ) : (
        <p className="py-6 text-center text-body text-text-muted">运行卡没有可绘制的权益曲线点（数组为空）。</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-2 text-caption text-text-muted">
        <span>基准：{card.benchmark.name ?? MISSING}</span>
        <span className="ldst-num">
          基准累计 {percentText(card.benchmark.cumulative_return)}
          {lastBenchmark ? ` · 期末权益 ${amountText(lastBenchmark.equity)}` : ''}
        </span>
        <span className="ldst-num">基准 calc_id {card.calc_ids.benchmark ?? MISSING}</span>
      </div>
      {card.benchmark.note && <p className="mt-1 text-caption leading-body text-text-muted">{card.benchmark.note}</p>}
    </SectionCard>
  )
}

const COST_TIER_COLUMNS: Array<Column<BacktestCostTier>> = [
  {
    key: 'commission',
    header: '佣金档位',
    width: '112px',
    numeric: true,
    render: (row) => (
      <span title={row.commission === null ? undefined : String(row.commission)}>
        {commissionTierLabel(row.commission)}
      </span>
    ),
  },
  {
    key: 'cumulative_return',
    header: '累计收益',
    numeric: true,
    render: (row) => <span className="ldst-num">{percentText(row.cumulative_return)}</span>,
  },
  {
    key: 'annualized_return',
    header: '年化收益',
    numeric: true,
    render: (row) => <span className="ldst-num">{percentText(row.annualized_return)}</span>,
  },
  {
    key: 'max_drawdown',
    header: '最大回撤',
    numeric: true,
    render: (row) => <span className="ldst-num">{percentText(row.max_drawdown)}</span>,
  },
  {
    key: 'trade_count',
    header: '完成回合',
    numeric: true,
    render: (row) => <span className="ldst-num">{countText(row.trade_count)}</span>,
  },
  {
    key: 'final_equity',
    header: '期末权益',
    numeric: true,
    render: (row) => <span className="ldst-num">{amountText(row.final_equity)}</span>,
  },
]

function CostTiersCard({ card }: { card: BacktestRunCard }) {
  return (
    <SectionCard
      title="三档成本敏感性"
      subtitle={`${card.gate.cost_tiers.length} 档 · 只替换佣金率，滑点 / 印花税固定为本次生效配置`}
    >
      <DataTable
        columns={COST_TIER_COLUMNS}
        rows={card.gate.cost_tiers}
        rowKey={(row) => `${row.commission ?? 'na'}`}
        caption="数值取自运行卡 gate.cost_tiers；行序与后端一致，前端不排序、不补档。"
        empty={<p className="py-2 text-caption text-text-muted">运行卡没有三档成本数据。</p>}
      />
    </SectionCard>
  )
}

const SEGMENT_COLUMNS: Array<Column<BacktestSegment>> = [
  {
    key: 'label',
    header: '分段',
    width: '96px',
    render: (segment) => (
      <span title={segment.data_fp ?? undefined}>
        <span className="text-text">{segment.label || MISSING}</span>
        <span className="ldst-num block text-caption text-text-muted">
          {segment.start ?? MISSING} ~ {segment.end ?? MISSING}
        </span>
      </span>
    ),
  },
  {
    key: 'bars',
    header: '交易日',
    width: '88px',
    numeric: true,
    render: (segment) => <span className="ldst-num">{countText(segment.bars)}</span>,
  },
  ...SEGMENT_METRICS.map(
    (key): Column<BacktestSegment> => ({
      key,
      header: METRIC_META.find((meta) => meta.key === key)?.label ?? key,
      numeric: true,
      render: (segment) => (
        <span className="ldst-num" title={segment.metrics_calc_ids[key] ?? undefined}>
          {segmentMetricDisplay(segment, key)}
        </span>
      ),
    }),
  ),
]

function SegmentsCard({ card }: { card: BacktestRunCard }) {
  return (
    <SectionCard
      title="样本内 / 样本外"
      subtitle="按时间顺序切分（不随机）；分段指标由运行卡 gate.segments 给出，悬浮可看各自 calc_id"
    >
      <DataTable
        columns={SEGMENT_COLUMNS}
        rows={card.gate.segments}
        rowKey={(segment) => segment.label || segment.data_fp || 'segment'}
        caption="分段区间、交易日与指标均原样来自运行卡；本页不重跑切分。"
        empty={<p className="py-2 text-caption text-text-muted">运行卡没有分段数据。</p>}
      />
    </SectionCard>
  )
}

export function ResultSection({ card }: { card: BacktestRunCard }) {
  return (
    <section className="flex flex-col gap-4" aria-label="结果区">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-caption font-semibold text-text-secondary">结果区</h2>
        <Badge tone="neutral">只读运行卡 · 不重算</Badge>
        <span className="ldst-num text-caption text-text-muted">run_id {card.run_id}</span>
      </div>
      <GateBlock card={card} />
      <MetricsGrid card={card} />
      <EquityPanel card={card} />
      <CostTiersCard card={card} />
      <SegmentsCard card={card} />
    </section>
  )
}
