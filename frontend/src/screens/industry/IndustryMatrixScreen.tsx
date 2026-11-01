import { useMemo, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Database, RefreshCw, Search } from 'lucide-react'

import {
  DIMENSION_TITLE,
  INDUSTRY_DIMENSIONS,
  isIndustryEndpointMissing,
  type IndustryDimension,
  type IndustrySummaryRow,
  type IndustryListPayload,
} from '../../api/industry'
import { DemoStateBadge, OfflineBanner, PageHeader } from '../../components/PageShell'
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
  Skeleton,
  SkeletonRows,
  type Column,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { formatInt, MISSING } from '../../lib/format'
import { compareMetric, compactMetric, DIMENSION_METRICS, formatMetric, type MetricKind } from './metrics'
import { HEAT_MAX_PERCENT, HEAT_MIN_PERCENT, levelHeatStyle, rankRatio, signedHeatStyle, MISSING_CELL_TITLE } from './heat'
import { CardNote, EndpointMissingEmpty, KeyValueList, MissingNote } from './bits'
import { LINK_BUTTON_CLASS } from './labels'
import { SourceStatusBadge } from './sourceBadges'
import { industryHref, withParams } from './links'
import { useIndustryListView } from './useIndustryView'

/**
 * P-R9-01 产业矩阵（`/industry`）。
 *
 * 行=SW 一级行业，列=当前维度的全部承诺指标；底色只表示「同列相对位置」
 * （DEC-028），不生成新指标、不判断好坏。缺失单元格固定「无数据（未核实）」。
 *
 * 五态：`?state=loading|empty|error|partial|offline`；`?demo=1` 为显式演示源。
 * 接口未接入（HTTP 404）→ 空态「接口未接入」，不是整屏报错。
 *
 * 状态列一律走 `./sourceBadges` 的同源徽标（§5.5 只允许一份实现，屏侧不得自带 label map）；
 * 演示水印归 `PageShell.tsx` 的 `DemoStateBadge`（R-18 / QUOT-320），本屏不重复画。
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

/** 状态列的悬浮说明：只解释本屏口径，标签与色序由同源徽标给出。 */
const STATUS_HINT: Record<IndustrySummaryRow['data_source_status'], string> = {
  ok: '本维度承诺指标全部有值',
  partial: '本维度部分指标无数据，缺失格不上色',
  missing: '本维度全部指标无数据',
}

function isDimension(value: string | null): value is IndustryDimension {
  return value !== null && (INDUSTRY_DIMENSIONS as readonly string[]).includes(value)
}

function asFilter(value: string | null): IndustryFilter {
  return value === 'data' || value === 'unbound' ? value : 'all'
}

function HeatSwatches() {
  return (
    <span className="inline-flex items-center gap-half" aria-hidden="true">
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
    return <MissingNote label={label} reason={asOf ? `数据时点 ${asOf}` : undefined} />
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
      className={cn('ldst-num inline-flex w-full items-center justify-end py-2', toneClass)}
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
  demoHref,
  reload,
}: {
  payload: IndustryListPayload | null
  demo: boolean
  endpointMissing: boolean
  demoHref: string
  reload: () => void
}) {
  const demoEntry = !demo && (
    <Link to={demoHref} className={LINK_BUTTON_CLASS}>
      查看演示数据
    </Link>
  )
  if (endpointMissing) {
    return <EndpointMissingEmpty endpoint="GET /api/industry" onReload={reload} extra={demoEntry} />
  }
  return (
    <Card>
      <EmptyState
        icon={<Database aria-hidden="true" />}
        title="本机暂无产业数据"
        description={
          <>
            {payload?.reason ?? '六个输入数据集在本机缓存里都没有可用记录，矩阵不出伪行。'}
            先运行 <code className="ldst-num">ldst industry list</code> 生成本地快照，再点「刷新」。
          </>
        }
        action={
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button variant="secondary" onClick={reload}>
              刷新
            </Button>
            {demoEntry}
          </div>
        }
      />
    </Card>
  )
}

/**
 * 加载骨架：复刻正文三段（工具栏行 → 矩阵表 → 图例卡），不排不存在的指标卡。
 *
 * `ScreenSkeleton` 的卡阵写死在 `PageShell.tsx:84`，无法表达本屏结构（清单 M-9，
 * 需要它补 `layout` 插槽）。这里先用组件层 C-18 的 `Skeleton` / `SkeletonRows` 拼同构骨架，
 * M-9 落地后可换回公共件。
 */
function MatrixSkeleton() {
  const slots = ['w-44', 'w-24', 'w-28', 'w-24', 'w-20', 'w-16']
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载产业矩阵…</span>
      <div className="flex flex-wrap items-center gap-3">
        <Skeleton className="h-control w-72" />
        <Skeleton className="h-control w-32" />
        <Skeleton className="h-control w-56" />
      </div>
      <div className="rounded border border-border bg-surface">
        <div className="flex h-row items-center gap-3 border-b border-border bg-surface-2 px-3">
          {slots.map((width, index) => (
            <Skeleton key={index} className={width} />
          ))}
        </div>
        <SkeletonRows rows={8} slots={['34%', '16%', '16%', '16%', '12%', '6%']} />
      </div>
      <div className="rounded border border-border bg-surface p-4">
        <Skeleton className="mb-3 h-4 w-40" />
        <SkeletonRows rows={3} slots={['18%', '44%', '12%']} />
      </div>
    </div>
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
  /** 「没取到」不等于「市场上没有行业」：payload 缺失时行业数写 `—`（UI-SPEC §8）。 */
  const rowCount: number | null = payload === null ? null : rows.length

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
            <Link
              to={industryHref(row.name, { demo, state: forced })}
              className="min-w-0 truncate font-medium text-text transition-colors duration-fast ease-ldst hover:text-accent focus-visible:text-accent"
              title={row.name}
            >
              {row.name}
            </Link>
            <span
              className="ldst-num min-w-0 truncate text-caption text-text-muted"
              title={`${row.l1_code ?? '未绑定申万一级代码'} · ${
                row.constituent_count === null ? '成分数量无数据' : `${formatInt(row.constituent_count)} 只成分股`
              }`}
            >
              {row.l1_code ?? '未绑定'} ·{' '}
              {row.constituent_count === null ? '成分无数据' : `${formatInt(row.constituent_count)} 只`}
            </span>
          </span>
        ),
      },
      ...DIMENSION_METRICS[dimension].map((spec) => ({
        key: spec.path,
        align: 'right' as const,
        width: spec.kind === 'money' ? '144px' : '112px',
        header: (
          <span className="flex flex-col items-end leading-tight">
            <span>{spec.label}</span>
            <span className="ldst-num text-caption font-normal text-text-muted" title={`${spec.label}的数据时点`}>
              {asOf ?? MISSING}
            </span>
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
        width: '88px',
        render: (row) => (
          <span className="inline-flex flex-col items-center gap-half">
            <SourceStatusBadge status={row.data_source_status} detail={STATUS_HINT[row.data_source_status]} />
            {!row.bound && (
              <Badge tone="neutral" title="未绑定申万一级代码，与板块口径不同名同成分">
                未绑定
              </Badge>
            )}
          </span>
        ),
      },
    ]
  }, [dimension, payload, columnValues, demo, forced])

  const partialRows = rows.filter((row) => row.data_source_status !== 'ok')
  /** §5.2：`partial` 必须同时列出缺的是哪一档、几个，不能只给一个总数。 */
  const partialBreakdown: ReactNode[] = []
  for (const status of ['partial', 'missing'] as const) {
    const count = partialRows.filter((row) => row.data_source_status === status).length
    if (count === 0) continue
    partialBreakdown.push(
      <span key={status} className="inline-flex items-center gap-half">
        <SourceStatusBadge status={status} />
        <span className="ldst-num">{formatInt(count)} 个</span>
      </span>,
    )
  }

  /** 演示态跳转保留当前维度 / 筛选 / 搜索 / 强制态，刷新后可恢复（§1 原则 2）。 */
  const demoHref = withParams('/industry', {
    demo: true,
    dim: dimension,
    filter: filter === 'all' ? null : filter,
    q: query || null,
    state: forced,
  })

  const header = (
    <PageHeader
      title="产业研究"
      subtitle={
        <span>
          SW 一级 · <span className="ldst-num">{`${formatInt(rowCount)} 个行业`}</span> · 数据时间{' '}
          <span className="ldst-num">{payload?.data_as_of ?? MISSING}</span>
        </span>
      }
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
        <MatrixSkeleton />
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
          demoHref={demoHref}
          reload={reload}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="danger" title="部分行业维度缺失">
          <span className="mb-1 flex flex-wrap items-center gap-2">{partialBreakdown}</span>
          缺失单元格显示「{MISSING_CELL_TITLE}」，不按 0 参与比较，也不从别的口径补位。
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
        <span className="ldst-num text-caption text-text-muted">
          {formatInt(visible.length)} / {formatInt(rows.length)} 个行业 · 点击行或行业名进入详情
        </span>
      </div>

      <DataTable<IndustrySummaryRow>
        columns={columns}
        rows={visible}
        rowKey={(row) => row.name}
        caption={`${DIMENSION_TITLE[dimension]}维度矩阵（${formatInt(visible.length)} 个行业）；底色为当前列相对位置，仅用于比较`}
        density="default"
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
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-caption leading-body text-text-secondary">
            <span className="inline-flex items-center gap-2">
              <HeatSwatches />
              单列由低到高（{HEAT_MIN_PERCENT}% → {HEAT_MAX_PERCENT}% 强调色）
            </span>
            <span className="inline-flex items-center gap-half">
              <span className="inline-block size-4 rounded-xs" style={signedHeatStyle(1)} aria-hidden="true" />
              <span className="inline-block size-4 rounded-xs" style={signedHeatStyle(-1)} aria-hidden="true" />
              <span className="ml-1">红涨绿跌列（金额 / 涨跌 / 同比）</span>
            </span>
            <span>
              缺失单元格固定「{MISSING_CELL_TITLE}」，不上色、不补 0；分位是同列经验分位，并列取并列组的中位位置，
              全等取 0.5，空列为「{MISSING}」。
            </span>
          </div>
          <KeyValueList
            items={[
              ...INDUSTRY_DIMENSIONS.map((name) => ({
                key: name,
                label: `${DIMENSION_TITLE[name]} 时点`,
                value: payload?.as_of[name] ?? MISSING,
                title: `${DIMENSION_TITLE[name]} 的数据时点；${MISSING_CELL_TITLE}用「${MISSING}」`,
              })),
              {
                key: 'window',
                label: '计算窗口',
                numeric: false,
                value: `资金 ${formatInt(payload?.windows.fund_days)} 日 · 估值 ${formatInt(
                  payload?.windows.valuation_lookback_days,
                )} 日 · 换手 ${formatInt(payload?.windows.turnover_lookback_days)} 日 · 动量 ${
                  (payload?.windows.momentum_windows ?? []).join('/') || MISSING
                } 日`,
              },
            ]}
          />
          {payload?.source_detail && (
            <CardNote>来源：{payload.source_detail}</CardNote>
          )}
        </CardBody>
      </Card>
    </div>
  )
}
