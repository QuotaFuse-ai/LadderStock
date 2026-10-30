import { useState } from 'react'

import {
  describeFinanceCofferError,
  fetchStructuredMetrics,
  VALUATION_BASIS_LABEL,
  type StructuredMetrics,
  type ValuationBasis,
} from '../../api/financecoffer'
import { MISSING } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'
import {
  Badge,
  Banner,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  EmptyState,
  ErrorBlock,
  MetricCard,
  Segmented,
  Skeleton,
} from '../ui'

/**
 * 估值指标面板（PE / PB / ROE）。
 *
 * 三件事必须同时成立，缺一不算对：
 * 1. 数值只来自响应里**所选口径**的字段，缺失就是 null →「暂无此数据」，不补 0、不用另一口径顶；
 * 2. 界面展示响应回显的 `basis`（实际生效）与 `default_basis`（服务端默认），不是只看用户点选值；
 * 3. 请求未构建的口径（`basis_not_built`）单独文案，并且同屏不出现任何数值。
 *
 * 本组件不计算指标、不缓存跨口径结果；每次切换都带 `valuation_basis` 重新取数。
 */

export type ValuationLoader = (
  symbol: string,
  basis: ValuationBasis,
  signal: AbortSignal,
) => Promise<StructuredMetrics>

export interface ValuationBasisPanelProps {
  symbol: string | null | undefined
  /** 默认 `snapshot`（与接口契约一致）；`reference` 只作为用户显式切换。 */
  initialBasis?: ValuationBasis
  /** 注入取数函数（测试 / 其他通道）；缺省走 FinanceCoffer 只读 REST。 */
  loader?: ValuationLoader
  title?: string
  className?: string
}

const REASON_TEXT: Record<string, string> = {
  basis_not_built: '该口径未构建',
  no_price: '无有效报价',
  no_earnings: '无净利润数据',
  no_deducted_earnings: '无单季扣非净利润',
  no_prior_cumulative: '无上期累计数据',
  unsupported_report_period: '报告期不支持',
  non_positive_earnings: '净利润非正，不计算',
  no_market_cap: '无总市值',
  no_equity: '无净资产数据',
  no_vendor_pb: '厂商未提供 PB',
  equity_unavailable: '无净资产数据',
  not_in_reference_basis: '该口径不产出 ROE',
}

const PE_SOURCE_TEXT: Record<string, string> = {
  'quote.pe_ttm': '厂商 TTM PE',
  'market_cap/net_profit': '总市值 ÷ 归母净利润',
  deducted_annualized: '单季扣非 × 4 年化',
}

const QUARTER_BASIS_TEXT: Record<string, string> = {
  single_quarter_deducted_x4: '单季扣非净利润 × 4 年化',
}

function reasonText(reason: string | null): string {
  if (!reason) return '服务端未给出原因'
  return REASON_TEXT[reason] ?? `原因代码 ${reason}`
}

function missingNote(reason: string | null): string {
  return `暂无此数据 · ${reasonText(reason)}`
}

function fixed2(value: number): string {
  return value.toFixed(2)
}

/** FC 约定 ROE 用小数（0.152 = 15.2%），界面统一换算成百分比。 */
function percent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function peNote(metrics: StructuredMetrics): string | undefined {
  if (metrics.pe === null) return undefined
  const source = metrics.pe_source ? PE_SOURCE_TEXT[metrics.pe_source] ?? metrics.pe_source : null
  return source ?? '服务端计算'
}

function Footprint({ metrics }: { metrics: StructuredMetrics }) {
  const quarter = metrics.quarter_basis
    ? QUARTER_BASIS_TEXT[metrics.quarter_basis] ?? metrics.quarter_basis
    : MISSING
  return (
    <CardFooter>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>价格日 {metrics.price_date ?? MISSING}</span>
        <span>收盘 {metrics.price_close ?? MISSING}</span>
        <span>报告期 {metrics.report_date ?? MISSING}</span>
        <span>年化方式 {quarter}</span>
        <span>来源 FinanceCoffer · 服务端计算</span>
      </div>
    </CardFooter>
  )
}

function LoadingMetrics() {
  return (
    <div className="grid grid-cols-1 gap-4 min-[1024px]:grid-cols-3" aria-busy="true">
      {[0, 1, 2].map((index) => (
        <div key={index} className="h-metric rounded border border-border bg-surface p-4 shadow-card">
          <div className="w-16">
            <Skeleton />
          </div>
          <div className="mt-5 w-24">
            <Skeleton />
          </div>
          <div className="mt-3">
            <Skeleton />
          </div>
        </div>
      ))}
      <p className="sr-only">估值指标取数中</p>
    </div>
  )
}

export function ValuationBasisPanel({
  symbol,
  initialBasis = 'snapshot',
  loader,
  title = '估值指标',
  className,
}: ValuationBasisPanelProps) {
  const [basis, setBasis] = useState<ValuationBasis>(initialBasis)
  const load: ValuationLoader = loader ?? ((code, selected, signal) =>
    fetchStructuredMetrics(code, selected, { signal }))
  const requestKey = symbol ? `${symbol}|${basis}` : ''

  const { status, data, error, reload } = useAsync<{ key: string; metrics: StructuredMetrics }>(
    async (signal) => ({
      key: `${symbol}|${basis}`,
      metrics: await load(symbol as string, basis, signal),
    }),
    [symbol, basis],
    { enabled: Boolean(symbol) },
  )

  // useAsync 在切换后的首次渲染可能还带着上一次的数据；key 不匹配就按加载中处理。
  const metrics = status === 'ready' && data?.key === requestKey ? data.metrics : null
  const pending =
    Boolean(symbol) && (status === 'loading' || (status === 'ready' && data?.key !== requestKey))

  const selectedNotBuilt = metrics
    ? !metrics.available_bases.includes(basis) ||
      metrics.pe_reason === 'basis_not_built' ||
      metrics.pb_reason === 'basis_not_built' ||
      metrics.roe_reason === 'basis_not_built'
    : false
  const showValues = metrics !== null && !selectedNotBuilt
  const pe = showValues ? metrics.pe : null
  const pb = showValues ? metrics.pb : null
  const roe = showValues ? metrics.roe : null
  const peReason = selectedNotBuilt ? 'basis_not_built' : metrics?.pe_reason ?? null
  const pbReason = selectedNotBuilt ? 'basis_not_built' : metrics?.pb_reason ?? null
  const roeReason = selectedNotBuilt ? 'basis_not_built' : metrics?.roe_reason ?? null

  return (
    <Card className={className}>
      <CardHeader
        title={title}
        subtitle={
          symbol
            ? `${symbol} · PE / PB / ROE 由 FinanceCoffer 服务端按所选口径计算`
            : 'PE / PB / ROE 由 FinanceCoffer 服务端计算'
        }
        actions={
          <Segmented
            ariaLabel="估值口径"
            size="sm"
            options={[
              { value: 'snapshot', label: VALUATION_BASIS_LABEL.snapshot },
              { value: 'reference', label: VALUATION_BASIS_LABEL.reference },
            ]}
            value={basis}
            onChange={setBasis}
          />
        }
      />
      <CardBody className="flex flex-col gap-4">
        {!symbol && (
          <EmptyState
            compact
            title="未选择标的"
            description="选择一只标的后展示 PE / PB / ROE；数值由 FinanceCoffer 服务端计算，界面只做展示。"
          />
        )}

        {symbol && pending && <LoadingMetrics />}

        {symbol && status === 'error' && (
          <ErrorBlock
            {...describeFinanceCofferError(error)}
            onRetry={reload}
            retryLabel="重新取数"
          />
        )}

        {metrics && (
          <>
            <div className="flex flex-wrap items-center gap-2" role="status" aria-live="polite">
              <Badge tone="info">生效口径：{VALUATION_BASIS_LABEL[metrics.basis]}</Badge>
              <Badge
                tone="neutral"
                title="构建期默认口径（服务端 FC_METRICS_PROFILE），页面切换不改变它"
              >
                服务端默认：{VALUATION_BASIS_LABEL[metrics.default_basis]}
              </Badge>
              {metrics.available_bases.length > 0 && (
                <span className="text-caption text-text-muted">
                  已构建：{metrics.available_bases.join(' / ')}
                </span>
              )}
            </div>

            {selectedNotBuilt ? (
              <Banner tone="warn" title="所选口径未构建">
                服务端尚未构建 {VALUATION_BASIS_LABEL[basis]}，本次不展示任何数值，
                也不会回落到其他口径。
              </Banner>
            ) : (
              metrics.basis !== basis && (
                <Banner tone="warn" title={`服务端生效口径为 ${VALUATION_BASIS_LABEL[metrics.basis]}`}>
                  你选择的是 {VALUATION_BASIS_LABEL[basis]}；数值按响应回显的实际口径展示，
                  避免「看着是点选值、算的是另一套」。
                </Banner>
              )
            )}

            <div
              className="grid grid-cols-1 gap-4 min-[1024px]:grid-cols-3"
              role="group"
              aria-label="估值指标"
            >
              <MetricCard
                label="PE 市盈率"
                value={pe === null ? MISSING : fixed2(pe)}
                note={pe === null ? missingNote(peReason) : peNote(metrics)}
              />
              <MetricCard
                label="PB 市净率"
                value={pb === null ? MISSING : fixed2(pb)}
                note={pb === null ? missingNote(pbReason) : undefined}
              />
              <MetricCard
                label="ROE 净资产收益率"
                value={roe === null ? MISSING : percent(roe)}
                note={roe === null ? missingNote(roeReason) : undefined}
              />
            </div>
          </>
        )}
      </CardBody>
      {metrics && <Footprint metrics={metrics} />}
    </Card>
  )
}
