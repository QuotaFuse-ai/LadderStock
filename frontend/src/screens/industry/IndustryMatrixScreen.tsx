import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Database, RefreshCw, Search } from 'lucide-react'

import {
  DIMENSION_TITLE,
  INDUSTRY_DIMENSIONS,
  isIndustryEndpointMissing,
  type IndustryDimension,
  type IndustrySummaryRow,
  type IndustryListPayload,
} from '../../api/industry'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  EmptyState,
  ErrorBlock,
  Field,
  Input,
  Segmented,
  Select,
  type Column,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING } from '../../lib/format'
import { compareMetric, compactMetric, DIMENSION_METRICS, formatMetric, type MetricKind } from './metrics'
import { levelHeatStyle, rankRatio, signedHeatStyle, MISSING_CELL_TITLE } from './heat'
import { industryHref } from './links'
import { useIndustryListView } from './useIndustryView'

/**
 * P-R9-01 产业矩阵（`/industry`）。
 *
 * 行=SW 一级行业，列=当前维度的全部承诺指标；底色只表示「同列相对位置」
 * （DEC-028），不生成新指标、不判断好坏。缺失单元格固定「无数据（未核实）」。
 *
 * 五态：`?state=loading|empty|error|partial|offline`；`?demo=1` 为显式演示源。
 * 接口未接入（HTTP 404）→ 空态「接口未接入」，不是整屏报错。
 */

type IndustryFilter = 'all' | 'data' | 'unbound'

const DIMENSION_OPTIONS = INDUSTRY_DIMENSIONS.map((dimension) => ({
  value: dimension,
  label: DIMENSION_TITLE[dimension],
}))

const FILTER_OPTIONS: ReadonlyArray<{ value: IndustryFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'data', label: '仅有数据' },
  { value: 'unbound', label: '未绑定' },
]

const STATUS_LABEL: Record<IndustrySummaryRow['data_source_status'], string> = {
  ok: '完整',
  partial: '部分',
  missing: '缺失',
}

const STATUS_TONE: Record<IndustrySummaryRow['data_source_status'], 'ok' | 'warn' | 'danger'> = {
  ok: 'ok',
  partial: 'warn',
  missing: 'danger',
}

function isDimension(value: string | null): value is IndustryDimension {
  return value !== null && (INDUSTRY_DIMENSIONS as readonly string[]).includes(value)
}

function asFilter(value: string | null): IndustryFilter {
  return value === 'data' || value === 'unbound' ? value : 'all'
}

function HeatSwatches() {
  return (
    <span className="inline-flex items-center gap-0.5" aria-hidden="true">
      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
        <span
          key={ratio}
          className="size-4 rounded-xs border border-border"
          style={levelHeatStyle(ratio)}
        />
      ))}
    </span>
  )
}

function MetricCell({
  row,
  path,
  label,
  kind,
  columnValues,
  asOf,
}: {
  row: IndustrySummaryRow
  path: string
  label: string
  kind: MetricKind
  columnValues: Array<number | null>
  asOf: string | null
}) {
  const value = row.metrics[path] ?? null
  if (value === null) {
    return (
      <span
        className="inline-flex items-center gap-1 text-caption text-text-muted"
        title={`${label}：${MISSING_CELL_TITLE}${asOf ? `（数据时点 ${asOf}）` : ''}`}
      >
        <span aria-hidden="true">{MISSING}</span>
        <span>{MISSING_CELL_TITLE}</span>
      </span>
    )
  }
  const formatted = formatMetric({ path, label, kind }, value)
  const toneClass =
    formatted.tone === 'up' ? 'text-up' : formatted.tone === 'down' ? 'text-down' : 'text-text'
  const style =
    kind === 'money' || kind === 'pctSigned'
      ? signedHeatStyle(value)
      : levelHeatStyle(rankRatio(columnValues, value))
  return (
    <span
      className={cn('ldst-num inline-flex w-full items-center justify-end rounded-xs px-2 py-1', toneClass)}
      style={style}
      aria-label={`${label} ${formatted.text}${asOf ? `，数据时点 ${asOf}` : ''}；底色为同列相对位置`}
      title={`${label} · ${compactMetric({ path, label, kind }, value)}${asOf ? ` · 数据时点 ${asOf}` : ''}`}
    >
      {formatted.text}
    </span>
  )
}

function MatrixEmpty({
  payload,
  demo,
  endpointMissing,
  reload,
}: {
  payload: IndustryListPayload | null
  demo: boolean
  endpointMissing: boolean
  reload: () => void
}) {
  return (
    <Card>
      <EmptyState
        icon={<Database aria-hidden="true" />}
        title={endpointMissing ? '接口未接入' : '本机暂无产业数据'}
        description={
          endpointMissing ? (
            <>
              页面已按 <code className="ldst-num">GET /api/industry</code> 契约实现；后端提供该只读接口后自动接入，
              当前不伪造任何行业行。
            </>
          ) : (
            <>
              {payload?.reason ?? '六个输入数据集在本机缓存里都没有可用记录，矩阵不出伪行。'}
              。先运行 <code className="ldst-num">ldst industry list</code> 生成本地快照，再点「刷新」。
            </>
          )
        }
        action={
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button variant="secondary" onClick={reload}>
              刷新
            </Button>
            {!demo && (
              <a
                href="/industry?demo=1"
                className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text hover:bg-surface-3"
              >
                查看演示数据
              </a>
            )}
          </div>
        }
      />
    </Card>
  )
}

export default function IndustryMatrixScreen() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const view = useIndustryListView()
  const { demo, forced, state, payload, error, reload } = view

  const dimension: IndustryDimension = isDimension(params.get('dim')) ? (params.get('dim') as IndustryDimension) : 'prosperity'
  const filter = asFilter(params.get('filter'))
  const query = params.get('q') ?? ''

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const rows = payload?.industries ?? []

  const visible = useMemo(() => {
    const term = query.trim()
    return rows.filter((row) => {
      if (filter === 'data' && !row.available) return false
      if (filter === 'unbound' && row.bound) return false
      if (!term) return true
      return row.name.includes(term) || (row.l1_code ?? '').includes(term)
    })
  }, [rows, filter, query])

  const columnValues = useMemo(() => {
    const out: Record<string, Array<number | null>> = {}
    for (const spec of DIMENSION_METRICS[dimension]) {
      out[spec.path] = visible.map((row) => row.metrics[spec.path] ?? null)
    }
    return out
  }, [visible, dimension])

  const columns = useMemo<Array<Column<IndustrySummaryRow>>>(() => {
    const asOf = payload?.as_of[dimension] ?? null
    return [
      {
        key: 'name',
        header: '行业',
        width: '176px',
        sort: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
        render: (row) => (
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="truncate font-medium text-text">{row.name}</span>
            <span className="ldst-num truncate text-caption text-text-muted">
              {row.l1_code ?? '未绑定'} · {row.constituent_count === null ? '成分未知' : `${row.constituent_count} 只`}
            </span>
          </span>
        ),
      },
      ...DIMENSION_METRICS[dimension].map((spec) => ({
        key: spec.path,
        align: 'right' as const,
        width: spec.kind === 'money' ? '136px' : '110px',
        header: (
          <span className="flex flex-col items-end leading-tight">
            <span>{spec.label}</span>
            <span className="text-caption font-normal text-text-muted">{asOf ?? '时点未知'}</span>
          </span>
        ),
        sort: (a: IndustrySummaryRow, b: IndustrySummaryRow) =>
          compareMetric(a.metrics[spec.path] ?? null, b.metrics[spec.path] ?? null),
        render: (row: IndustrySummaryRow) => (
          <MetricCell
            row={row}
            path={spec.path}
            label={spec.label}
            kind={spec.kind}
            columnValues={columnValues[spec.path] ?? []}
            asOf={row.as_of[dimension] ?? asOf}
          />
        ),
      })),
      {
        key: 'status',
        header: '状态',
        align: 'center',
        width: '96px',
        render: (row) => (
          <span className="inline-flex flex-col items-center gap-0.5">
            <Badge tone={STATUS_TONE[row.data_source_status]}>{STATUS_LABEL[row.data_source_status]}</Badge>
            {!row.bound && <span className="text-caption text-text-muted">未绑定</span>}
          </span>
        ),
      },
    ]
  }, [dimension, payload, columnValues])

  const partialRows = rows.filter((row) => row.data_source_status !== 'ok')

  const header = (
    <PageHeader
      title="产业研究"
      subtitle={`SW 一级 · ${rows.length} 个行业 · 数据时间 ${payload?.data_as_of ?? MISSING}`}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <Button
          variant="primary"
          onClick={reload}
          disabled={state === 'loading'}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          刷新
        </Button>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={5} rows={8} />
      </div>
    )
  }

  if (state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <OfflineBanner onRetry={reload} />
        <Card>
          <EmptyState
            icon={<Database aria-hidden="true" />}
            title="本地服务未连接"
            description="启动 ldst run 后，/api/industry 与本页自动连上；当前不伪造任何行业行。"
            action={<Button variant="secondary" onClick={reload}>重新检查</Button>}
          />
        </Card>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock
          variant="banner"
          title="产业矩阵取数失败"
          detail={error instanceof Error ? error.message : '未知错误'}
          onRetry={reload}
        />
      </div>
    )
  }

  if (state === 'empty') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <MatrixEmpty
          payload={payload}
          demo={demo}
          endpointMissing={payload === null && isIndustryEndpointMissing(error)}
          reload={reload}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="warn" title="部分行业维度缺失">
          {partialRows.length} 个行业的维度不完整（如未绑定申万代码、缓存为空）。缺失单元格显示「无数据（未核实）」，
          不按 0 参与比较，也不从别的口径补位。
        </Banner>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <Segmented
          ariaLabel="切换产业维度"
          options={DIMENSION_OPTIONS}
          value={dimension}
          onChange={(next) => setParam('dim', next)}
        />
        <Field label="筛选" htmlFor="industry-filter" className="w-32">
          <Select
            id="industry-filter"
            value={filter}
            onChange={(event) => setParam('filter', event.target.value === 'all' ? null : event.target.value)}
          >
            {FILTER_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="搜索行业" htmlFor="industry-search" className="w-56">
          <Input
            id="industry-search"
            value={query}
            placeholder="名称 / 申万代码"
            leadingIcon={<Search className="size-3.5" aria-hidden="true" />}
            onChange={(event) => setParam('q', event.target.value || null)}
          />
        </Field>
        <div className="grow" />
        <span className="text-caption text-text-muted">
          {visible.length} / {rows.length} 个行业 · 点击行进入详情
        </span>
      </div>

      <DataTable<IndustrySummaryRow>
        columns={columns}
        rows={visible}
        rowKey={(row) => row.name}
        caption={`${DIMENSION_TITLE[dimension]}维度矩阵（${visible.length} 个行业）；底色为当前列相对位置，仅用于比较`}
        density="compact"
        maxHeight="calc(100vh - 280px)"
        onRowClick={(row) => navigate(industryHref(row.name, { demo, state: forced }))}
        empty={
          <EmptyState
            compact
            title="没有匹配的行业"
            description="换个关键词，或把筛选切回「全部」。"
          />
        }
      />

      <Card>
        <CardHeader title="读图与口径" subtitle="底色只做同列比较，不改变数值，也不作为交易信号" />
        <CardBody className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-caption leading-body text-text-secondary">
            <span className="inline-flex items-center gap-2">
              <HeatSwatches />
              单列由低到高（4% → 52% 强调色）
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block size-4 rounded-xs" style={signedHeatStyle(1)} aria-hidden="true" />
              <span className="inline-block size-4 rounded-xs" style={signedHeatStyle(-1)} aria-hidden="true" />
              红涨绿跌列（金额 / 涨跌 / 同比）
            </span>
            <span>
              缺失单元格固定「{MISSING_CELL_TITLE}」，不补 0；分位为窗口内经验分位（0–100%），不插值。
            </span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-caption text-text-muted">
            {INDUSTRY_DIMENSIONS.map((name) => (
              <span key={name} className="ldst-num">
                {DIMENSION_TITLE[name]} 时点 {payload?.as_of[name] ?? MISSING}
              </span>
            ))}
            <span className="ldst-num">
              窗口 资金 {payload?.windows.fund_days ?? MISSING} 日 · 估值 {payload?.windows.valuation_lookback_days ?? MISSING} 日 ·
              换手 {payload?.windows.turnover_lookback_days ?? MISSING} 日 · 动量 {(payload?.windows.momentum_windows ?? []).join('/') || MISSING} 日
            </span>
          </div>
          {payload?.source_detail && (
            <p className="text-caption leading-body text-text-muted">来源：{payload.source_detail}</p>
          )}
        </CardBody>
      </Card>
    </div>
  )
}
