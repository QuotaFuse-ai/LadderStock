import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Database, Newspaper, RefreshCw } from 'lucide-react'

import {
  DIMENSION_TITLE,
  INDUSTRY_DIMENSIONS,
  type ConstituentRow,
  type IndustryDetailPayload,
  type IndustryDimension,
  type RelatedItem,
} from '../../api/industry'
import { DemoStateBadge, OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Card,
  CardHeader,
  DataTable,
  EmptyState,
  ErrorBlock,
  SectionCard,
  Skeleton,
  SkeletonRows,
  Sparkline,
  SourceStatusBadge,
  type Column,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import {
  MISSING,
  formatChangePct,
  formatDate,
  formatInt,
  formatMoney,
  formatTime,
  isFiniteNumber,
  shortDate,
} from '../../lib/format'
import {
  compareMetric,
  DIMENSION_HEADLINE,
  DIMENSION_METRICS,
  dimensionValues,
  formatMetric,
  percentileValue,
} from './metrics'
import { MISSING_CELL_TITLE } from './heat'
import { LINK_BUTTON_CLASS, LINK_TEXT_CLASS, dimensionTitleOf } from './labels'
import { CardNote, EndpointMissingEmpty, KeyValueList, MissingNote } from './bits'
import { withParams } from './links'
import { useIndustryDetailView } from './useIndustryView'

/**
 * P-R9-02 单行业（`/industry/:name`）。
 *
 * 五维仪表（分位用 0–100% 刻度条，历史序列用 Sparkline）+ 成分股表 +
 * 相关研报/资讯（R6 `news_stream`，未交付前是带出口的空态）+ 口径与溯源。
 *
 * 数值一律来自 R5 快照；缺失显示「无数据（未核实）」并给原因，不补 0。
 * 「数据完整度」的标签与色序不在本屏定义，一律走同源徽标 `components/ui/SourceBadges`（§5.5）；
 * 演示水印归 `PageShell.tsx` 的 `DemoStateBadge`（R-18 / QUOT-320）。
 */

/** §5.2：`partial` 的悬浮必须列出缺失项，只给总数不算达标。 */
function missingSummaryOf(payload: IndustryDetailPayload): string | null {
  if (payload.reason) return payload.reason
  if (payload.missing.length === 0) return null
  const head = payload.missing.slice(0, 6).join('、')
  return `缺 ${formatInt(payload.missing.length)} 个字段：${head}${payload.missing.length > 6 ? '…' : ''}`
}

const HISTORY_KEY: Record<IndustryDimension, 'pe_percentile' | 'pb_percentile' | 'turnover_percentile' | 'change_pct_20d' | null> = {
  prosperity: null,
  capital: null,
  valuation: 'pe_percentile',
  momentum: 'change_pct_20d',
  crowding: 'turnover_percentile',
}

/** 分位条指针：用 `-translate-x-1/2` 居中到百分比处，不需要半个指针半径的魔法数。 */
const GAUGE_POINTER_CLASS =
  'absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-pill border border-accent bg-surface'

function PercentileGauge({ value, label }: { value: number | null; label: string }) {
  const ratio = percentileValue(value)
  if (ratio === null) {
    return <MissingNote label={label} reason="分位需要窗口内有可比样本" />
  }
  const percent = Math.round(ratio * 100)
  return (
    <div
      className="flex flex-col gap-1"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-valuetext={`${label} ${percent}%，0% 为窗口最低，100% 为最高`}
      aria-label={label}
    >
      <div className="relative h-2 rounded-pill bg-surface-3">
        <span className="absolute inset-y-0 left-0 rounded-pill bg-accent" style={{ width: `${percent}%` }} />
        <span className={GAUGE_POINTER_CLASS} style={{ left: `${percent}%` }} />
      </div>
      <div className="ldst-num flex justify-between text-caption text-text-muted">
        <span>0%</span>
        <span>100%</span>
      </div>
    </div>
  )
}

function DimensionCard({
  dimension,
  payload,
}: {
  dimension: IndustryDimension
  payload: IndustryDetailPayload
}) {
  const block = payload.dimensions[dimension]
  const specs = DIMENSION_METRICS[dimension]
  const values = dimensionValues(dimension, payload.dimensions)
  const headlineSpec = specs.find((spec) => spec.path === DIMENSION_HEADLINE[dimension]) ?? specs[0]
  const headlineValue = values[headlineSpec.path] ?? null
  const formatted = formatMetric(headlineSpec, headlineValue)
  const secondary = specs.filter((spec) => spec.path !== headlineSpec.path)
  const historyKey = HISTORY_KEY[dimension]
  const series = historyKey ? payload.history[historyKey] : []
  const isPercentile = headlineSpec.kind === 'ratioPct'
  const unverifiedCount = specs.filter((spec) => {
    const entry = payload.provenance.values[spec.path]
    return values[spec.path] === null || !entry || entry.unverified
  }).length
  const changeTone =
    dimension === 'momentum' && series.length >= 2
      ? series[series.length - 1].value >= series[0].value
        ? 'up'
        : 'down'
      : 'flat'

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        title={DIMENSION_TITLE[dimension]}
        subtitle={unverifiedCount === 0 ? '溯源完整' : `${unverifiedCount} 个字段未核实`}
        actions={
          <Badge tone={block.available ? 'ok' : 'warn'}>{block.available ? '有数据' : '缺数据'}</Badge>
        }
      />
      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex flex-col gap-half">
          <p className="text-caption text-text-secondary">{headlineSpec.label}</p>
          <p
            className={cn(
              'ldst-num text-page font-semibold leading-tight',
              formatted.tone === 'up' ? 'text-up' : formatted.tone === 'down' ? 'text-down' : 'text-text',
            )}
          >
            {formatted.missing ? MISSING : formatted.text}
          </p>
          {formatted.missing && !isPercentile && <MissingNote label={headlineSpec.label} />}
        </div>

        {isPercentile && <PercentileGauge value={headlineValue} label={`${headlineSpec.label}分位`} />}

        {historyKey ? (
          series.length >= 2 ? (
            <div className="flex items-center justify-between gap-2">
              <Sparkline
                values={series.map((point) => point.value)}
                tone={changeTone}
                ariaLabel={`${DIMENSION_TITLE[dimension]}历史序列，${series.length} 个点，${formatDate(series[0].date)} 至 ${
                  formatDate(series[series.length - 1].date)
                }`}
                width={120}
              />
              <span
                className="ldst-num shrink-0 text-caption text-text-muted"
                title={`末点 ${formatDate(series[series.length - 1].date)}`}
              >
                {shortDate(series[series.length - 1].date)}
              </span>
            </div>
          ) : (
            <p className="text-caption text-text-muted" title={MISSING_CELL_TITLE}>
              历史序列{MISSING}（窗口内不足 2 个可比样本）
            </p>
          )
        ) : (
          <p className="text-caption text-text-muted" title="R5 快照未对该维度提供历史序列，非取数失败">
            本维度不提供历史序列（R5 未接入）
          </p>
        )}

        <dl className="mt-auto flex flex-col gap-1 border-t border-border pt-2">
          {secondary.map((spec) => {
            const item = formatMetric(spec, values[spec.path] ?? null)
            const toneClass =
              item.tone === 'up' ? 'text-up' : item.tone === 'down' ? 'text-down' : 'text-text'
            return (
              <div key={spec.path} className="flex min-w-0 items-baseline justify-between gap-2">
                <dt className="min-w-0 shrink-0 text-caption text-text-secondary" title={spec.path}>
                  {spec.label}
                </dt>
                <dd
                  className={cn('ldst-num min-w-0 shrink truncate text-caption', item.missing ? 'text-text-muted' : toneClass)}
                  title={item.missing ? MISSING_CELL_TITLE : spec.path}
                >
                  {item.missing ? MISSING : item.text}
                </dd>
              </div>
            )
          })}
        </dl>

        {!block.available && block.reason && (
          <p className="text-caption leading-body text-text-muted">{block.reason}</p>
        )}
      </div>
    </Card>
  )
}

function PctCell({ value }: { value: number | null }) {
  if (!isFiniteNumber(value)) {
    return (
      <span className="ldst-num text-caption text-text-muted" title={MISSING_CELL_TITLE}>
        {MISSING}
      </span>
    )
  }
  const signed = formatChangePct(value)
  return (
    <span
      className={cn(
        'ldst-num',
        signed.tone === 'up' ? 'text-up' : signed.tone === 'down' ? 'text-down' : 'text-text-secondary',
      )}
    >
      {signed.text}
    </span>
  )
}

const CONSTITUENT_COLUMNS: Array<Column<ConstituentRow>> = [
  {
    key: 'stock',
    header: '标的',
    width: '176px',
    sort: (a, b) => a.code.localeCompare(b.code),
    render: (row) => (
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="min-w-0 truncate text-text" title={row.name ?? '名称无数据'}>{row.name ?? '名称无数据'}</span>
        <span className="ldst-num min-w-0 truncate text-caption text-text-muted" title={`代码 ${row.code}`}>
          {row.code}
        </span>
      </span>
    ),
  },
  {
    key: 'pe',
    header: 'PE(TTM)',
    align: 'right',
    width: '112px',
    sort: (a, b) => compareMetric(a.pe_ttm, b.pe_ttm),
    render: (row) => (
      <span className={cn('ldst-num', row.pe_ttm === null ? 'text-text-muted' : 'text-text')}>
        {row.pe_ttm === null ? MISSING : `${row.pe_ttm.toFixed(2)} 倍`}
      </span>
    ),
  },
  {
    key: 'pb',
    header: 'PB',
    align: 'right',
    width: '88px',
    sort: (a, b) => compareMetric(a.pb, b.pb),
    render: (row) => (
      <span className={cn('ldst-num', row.pb === null ? 'text-text-muted' : 'text-text')}>
        {row.pb === null ? MISSING : `${row.pb.toFixed(2)} 倍`}
      </span>
    ),
  },
  {
    key: 'turnover',
    header: '换手率',
    align: 'right',
    width: '88px',
    sort: (a, b) => compareMetric(a.turnover_rate, b.turnover_rate),
    render: (row) => (
      <span className={cn('ldst-num', row.turnover_rate === null ? 'text-text-muted' : 'text-text-secondary')}>
        {row.turnover_rate === null ? MISSING : `${row.turnover_rate.toFixed(2)}%`}
      </span>
    ),
  },
  {
    key: 'revenue',
    header: '营收同比',
    align: 'right',
    width: '112px',
    sort: (a, b) => compareMetric(a.revenue_yoy, b.revenue_yoy),
    render: (row) => <PctCell value={row.revenue_yoy} />,
  },
  {
    key: 'profit',
    header: '净利同比',
    align: 'right',
    width: '112px',
    sort: (a, b) => compareMetric(a.profit_yoy, b.profit_yoy),
    render: (row) => <PctCell value={row.profit_yoy} />,
  },
  {
    key: 'mainNet',
    header: '主力净流入(5日)',
    align: 'right',
    width: '144px',
    sort: (a, b) => compareMetric(a.main_net_nd, b.main_net_nd),
    render: (row) => (
      <span
        className={cn(
          'ldst-num',
          row.main_net_nd === null ? 'text-text-muted' : row.main_net_nd > 0 ? 'text-up' : row.main_net_nd < 0 ? 'text-down' : 'text-text-secondary',
        )}
      >
        {row.main_net_nd === null ? MISSING : formatMoney(row.main_net_nd)}
      </span>
    ),
  },
]

function RelatedRow({ item }: { item: RelatedItem }) {
  const shown = item.related_codes.slice(0, 4)
  const hidden = item.related_codes.length - shown.length
  const title = (
    <p className="line-clamp-2 text-body leading-body text-text" title={item.title}>
      {item.title}
    </p>
  )
  return (
    <li className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-2">
        <Badge tone={item.kind === 'report' ? 'info' : 'neutral'}>{item.kind === 'report' ? '研报' : '资讯'}</Badge>
        <span className="ldst-num text-caption text-text-muted">{item.as_of ?? MISSING}</span>
      </div>
      {item.url ? (
        <a
          href={item.url}
          target="_blank"
          rel="noreferrer noopener"
          className="leading-body text-text underline-offset-2 transition-colors duration-fast ease-ldst hover:text-accent-hover hover:underline"
          title={item.title}
        >
          {title}
        </a>
      ) : (
        title
      )}
      <p className="min-w-0 truncate text-caption text-text-muted" title={item.related_codes.join('、') || undefined}>
        {item.source ?? '来源无数据'}
        {item.related_codes.length > 0 &&
          ` · 关联 ${shown.join('、')}${hidden > 0 ? `（另有 ${formatInt(hidden)} 个代码）` : ''}`}
      </p>
    </li>
  )
}

function DetailEmpty({
  name,
  payload,
  demo,
  reload,
}: {
  name: string | null
  payload: IndustryDetailPayload | null
  demo: boolean
  reload: () => void
}) {
  if (!name) {
    return (
      <Card>
        <EmptyState
          icon={<Database aria-hidden="true" />}
          title="行业名不合法"
          description="行业名不能为空，且不超过 40 个字符。"
          action={
            <Link to={withParams('/industry', { demo: demo ? '1' : null })} className={LINK_TEXT_CLASS}>
              返回产业矩阵
            </Link>
          }
        />
      </Card>
    )
  }
  if (payload && payload.candidates.length > 0) {
    return (
      <Card>
        <EmptyState
          icon={<Database aria-hidden="true" />}
          title={`没有「${payload.requested_industry ?? name}」这个行业`}
          description="行业按 SW 一级清单匹配；下面是快照给出的候选，点击直接进入。"
          action={
            <div className="flex flex-wrap justify-center gap-2">
              {payload.candidates.map((candidate) => (
                <Link
                  key={candidate}
                  to={withParams(`/industry/${encodeURIComponent(candidate)}`, { demo: demo ? '1' : null })}
                  className={LINK_BUTTON_CLASS}
                >
                  {candidate}
                </Link>
              ))}
            </div>
          }
        />
      </Card>
    )
  }
  if (payload) {
    const dims = INDUSTRY_DIMENSIONS.filter((dimension) => payload.unavailable_dimensions.includes(dimension))
    return (
      <Card>
        <EmptyState
          icon={<Database aria-hidden="true" />}
          title="五维均无数据（未核实）"
          description={
            <>
              {payload.reason ?? '六个输入数据集没有可用记录，字段全部显式 null。'}
              {dims.length > 0 && ` 缺失维度：${dims.map((dimension) => DIMENSION_TITLE[dimension]).join('、')}。`}
              先运行 <code className="ldst-num">ldst industry show {payload.meta.name}</code> 检查本地缓存。
            </>
          }
          action={<Button variant="secondary" onClick={reload}>刷新</Button>}
        />
      </Card>
    )
  }
  return (
    <EndpointMissingEmpty
      endpoint={'GET /api/industry/{name}'}
      onReload={reload}
      extra={
        <Link to={withParams('/industry', { demo: demo ? '1' : null })} className={LINK_BUTTON_CLASS}>
          返回产业矩阵
        </Link>
      }
    />
  )
}

/**
 * 加载骨架：复刻正文两栏（左：2 列 × 5 张维度卡 + 成分股表；右：3 张侧栏卡）。
 *
 * `ScreenSkeleton` 的卡阵写死在 `PageShell.tsx:84`，表达不了左右分栏（清单 M-9，
 * 需要它补 `layout` 插槽）；这里先用组件层 C-18 的 `Skeleton` / `SkeletonRows` 拼同构骨架。
 */
function DetailSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 desk:grid-cols-12" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载行业快照…</span>
      <div className="flex flex-col gap-4 desk:col-span-8">
        <div className="grid grid-cols-2 gap-3">
          {INDUSTRY_DIMENSIONS.map((dimension) => (
            <div key={dimension} className="flex flex-col gap-3 rounded border border-border bg-surface p-4">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-7 w-28" />
              <SkeletonRows rows={2} slots={['34%', '28%']} className="border-t border-border pt-2" />
            </div>
          ))}
        </div>
        <div className="rounded border border-border bg-surface">
          <div className="flex h-row items-center gap-3 border-b border-border bg-surface-2 px-3">
            <Skeleton className="w-32" />
            <Skeleton className="w-16" />
            <Skeleton className="w-14" />
            <Skeleton className="w-16" />
            <Skeleton className="w-20" />
          </div>
          <SkeletonRows rows={6} slots={['24%', '12%', '10%', '12%', '14%', '14%']} />
        </div>
      </div>
      <aside className="flex flex-col gap-4 desk:col-span-4">
        {[0, 1, 2].map((index) => (
          <div key={index} className="flex flex-col gap-3 rounded border border-border bg-surface p-4">
            <Skeleton className="h-4 w-24" />
            <SkeletonRows rows={index === 1 ? 5 : 3} slots={['28%', '46%']} />
          </div>
        ))}
      </aside>
    </div>
  )
}

export default function IndustryDetailScreen() {
  const params = useParams<{ name: string }>()
  // react-router 已解码一次；这里只做长度校验，避免二次 decode 抛错
  const name = params.name && params.name.length <= 40 ? params.name : null
  const view = useIndustryDetailView(name)
  const { demo, forced, state, payload, error, reload } = view

  const backLink = withParams('/industry', { demo: demo ? '1' : null, state: forced })

  const header = (
    <PageHeader
      title={payload?.meta.name ?? name ?? '行业详情'}
      subtitle={
        payload ? (
          <span>
            <span className="ldst-num">{payload.meta.l1_code ?? '未绑定申万一级'}</span> · 成分股{' '}
            <span className="ldst-num">{formatInt(payload.membership.constituent_count)}</span> 只
          </span>
        ) : (
          '单行业五维快照'
        )
      }
      badge={
        <>
          <DemoStateBadge state={forced} />
          {payload && (
            <SourceStatusBadge status={payload.data_source_status} detail={missingSummaryOf(payload)} />
          )}
          {payload && !payload.meta.bound && <Badge tone="neutral">未绑定</Badge>}
        </>
      }
      actions={
        <>
          <Link to={backLink} className={LINK_BUTTON_CLASS}>
            <ArrowLeft className="size-3.5" aria-hidden="true" />
            产业矩阵
          </Link>
          <Button
            variant="primary"
            onClick={reload}
            disabled={state === 'loading'}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          >
            刷新
          </Button>
        </>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <DetailSkeleton />
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
            description="启动 ldst run 后自动接入；未连接时不展示任何快照。"
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
          title="行业快照取数失败"
          detail={error instanceof Error ? error.message : '未知错误'}
          onRetry={reload}
        />
      </div>
    )
  }

  if (state === 'empty' || !payload) {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <DetailEmpty name={name} payload={payload} demo={demo} reload={reload} />
      </div>
    )
  }

  const unavailable = INDUSTRY_DIMENSIONS.filter((dimension) => payload.unavailable_dimensions.includes(dimension))
  const relatedEmpty = payload.related.length === 0

  return (
    <div className="flex flex-col gap-5">
      {header}

      {unavailable.length > 0 && (
        <Banner tone="warn" title="部分维度无数据（未核实）">
          {unavailable.map((dimension) => DIMENSION_TITLE[dimension]).join('、')} 缺少本地缓存：
          {payload.dimensions[unavailable[0]].reason ?? '接口未给出原因。'}其余维度照常展示，缺失字段不补 0。
        </Banner>
      )}

      <div className="grid grid-cols-1 gap-4 desk:grid-cols-12">
        <div className="flex flex-col gap-4 desk:col-span-8">
          <div className="grid grid-cols-2 gap-3">
            {INDUSTRY_DIMENSIONS.map((dimension) => (
              <DimensionCard key={dimension} dimension={dimension} payload={payload} />
            ))}
          </div>

          <SectionCard
            title="成分股"
            subtitle={
              payload.membership.constituent_count === null ? (
                <MissingNote label="成分数量" />
              ) : (
                <span>
                  申万成分 <span className="ldst-num">{formatInt(payload.membership.constituent_count)}</span> 只 ·
                  分类时点 <span className="ldst-num">{payload.membership.as_of ?? MISSING}</span>
                </span>
              )
            }
            actions={
              <Badge tone={payload.constituents.length > 0 ? 'info' : 'warn'}>
                {formatInt(payload.constituents.length)} 行
              </Badge>
            }
            bodyClassName="p-0"
          >
            <DataTable<ConstituentRow>
              columns={CONSTITUENT_COLUMNS}
              rows={payload.constituents}
              rowKey={(row) => row.code}
              caption="逐股快照来自 R5 成分缓存；缺失单元格为「无数据（未核实）」，不按 0 处理"
              density="default"
              maxHeight="360px"
              initialSort={{ key: 'mainNet', dir: 'desc' }}
              empty={
                <EmptyState
                  compact
                  title="逐股成分未接入"
                  description={
                    payload.membership.reason ??
                    'R5 快照未返回 constituents；接口提供后自动出表，前端不自行拼股票池。'
                  }
                  action={
                    <Button variant="secondary" onClick={reload}>
                      重新取数
                    </Button>
                  }
                />
              }
            />
          </SectionCard>
        </div>

        <aside className="flex flex-col gap-4 desk:col-span-4">
          <SectionCard
            title="相关研报与资讯"
            subtitle="R6 news_stream 关联到本行业"
            actions={<Badge tone={relatedEmpty ? 'neutral' : 'info'}>{formatInt(payload.related.length)} 条</Badge>}
            bodyClassName={relatedEmpty ? 'p-0' : undefined}
          >
            {relatedEmpty ? (
              <EmptyState
                compact
                icon={<Newspaper aria-hidden="true" />}
                title="暂无关联资讯"
                description="R6 news_stream 尚未交付；接口上线后这里展示该行业研报与资讯。前端不抓取外部内容（DEC-006）。"
                action={
                  <Button variant="secondary" onClick={reload}>
                    重新取数
                  </Button>
                }
              />
            ) : (
              <ul className="divide-y divide-border">
                {payload.related.map((item, index) => (
                  <RelatedRow key={`${item.kind}-${index}-${item.title}`} item={item} />
                ))}
              </ul>
            )}
          </SectionCard>

          <SectionCard title="口径与成分" subtitle="来自 R5 快照，不在此页重算">
            <KeyValueList
              items={[
                {
                  key: 'l1',
                  label: '申万一级代码',
                  value: payload.meta.l1_code ?? '未绑定',
                  title: payload.meta.l1_code ?? '该行业未绑定申万一级代码',
                },
                { key: 'caliber', label: '口径', numeric: false, value: payload.meta.caliber ?? MISSING, title: payload.meta.caliber ?? MISSING_CELL_TITLE },
                {
                  key: 'count',
                  label: '成分股 / 分类代码',
                  value: `${formatInt(payload.membership.constituent_count)} / ${formatInt(payload.membership.classified_codes)}`,
                },
                {
                  key: 'board',
                  label: '板块口径',
                  numeric: false,
                  value: payload.meta.board_name ?? '未配置（同名≠同成分）',
                  title: payload.meta.board_name ?? '未配置板块口径：同名板块不等于同成分',
                },
                {
                  key: 'anchors',
                  label: '锚点',
                  numeric: false,
                  value: payload.meta.anchors.length > 0 ? payload.meta.anchors.join('、') : MISSING,
                  title: payload.meta.anchors.length > 0 ? payload.meta.anchors.join('、') : MISSING_CELL_TITLE,
                },
              ]}
            />
            {payload.membership.reason && <CardNote>{payload.membership.reason}</CardNote>}
          </SectionCard>

          <SectionCard
            title="溯源与缺口"
            subtitle={
              <span>
                <span className="ldst-num">{formatInt(payload.provenance.sources.length)}</span> 个数据源 ·{' '}
                <span className="ldst-num">{formatInt(payload.missing.length)}</span> 个字段无数据
              </span>
            }
            tone={payload.data_source_status === 'ok' ? 'ok' : 'danger'}
          >
            {payload.provenance.sources.length === 0 ? (
              <EmptyState
                compact
                icon={<Database aria-hidden="true" />}
                title="本维度无数据源"
                description="R5 快照没有返回任何来源清单；下面的缺口徽标只按后端给的完整度字段画，不代表已核实。"
              />
            ) : (
              <ul className="flex min-w-0 flex-col gap-1 text-caption leading-body">
                {payload.provenance.sources.map((source) => (
                  <li key={source.id} className="flex min-w-0 items-baseline justify-between gap-2">
                    <span className="min-w-0 truncate text-text-secondary" title={`数据源 id：${source.id}`}>
                      {source.dataset}
                    </span>
                    <span
                      className="ldst-num shrink-0 text-text-muted"
                      title={`取数时间 ${formatDate(source.retrieved_at)}`}
                    >
                      {formatTime(source.retrieved_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-2 flex flex-wrap gap-1 border-t border-border pt-2">
              {INDUSTRY_DIMENSIONS.map((dimension) => (
                <Badge
                  key={dimension}
                  tone={payload.unavailable_dimensions.includes(dimension) ? 'warn' : 'ok'}
                  title={payload.dimensions[dimension].reason ?? undefined}
                >
                  {DIMENSION_TITLE[dimension]}
                </Badge>
              ))}
            </div>
            {Object.keys(payload.formulas).length > 0 && (
              <div className="mt-2 flex flex-col gap-1 border-t border-border pt-2 text-caption leading-body text-text-muted">
                {Object.entries(payload.formulas).map(([dimension, formula]) => (
                  <p key={dimension} className="line-clamp-2" title={`${dimension}：${formula}`}>
                    <span className="text-text-secondary">{dimensionTitleOf(dimension)}</span>：{formula}
                  </p>
                ))}
              </div>
            )}
            {payload.source_detail && <CardNote>{payload.source_detail}</CardNote>}
          </SectionCard>
        </aside>
      </div>
    </div>
  )
}
