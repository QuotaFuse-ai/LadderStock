import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Database, Newspaper, RefreshCw } from 'lucide-react'

import {
  DIMENSION_TITLE,
  INDUSTRY_DIMENSIONS,
  type ConstituentRow,
  type DataSourceStatus,
  type IndustryDetailPayload,
  type IndustryDimension,
  type RelatedItem,
} from '../../api/industry'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
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
  Sparkline,
  type Column,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING, formatChangePct, formatInt, formatMoney, formatTime, isFiniteNumber } from '../../lib/format'
import {
  compareMetric,
  DIMENSION_HEADLINE,
  DIMENSION_METRICS,
  dimensionValues,
  formatMetric,
  percentileValue,
} from './metrics'
import { MISSING_CELL_TITLE } from './heat'
import { withParams } from './links'
import { useIndustryDetailView } from './useIndustryView'

/**
 * P-R9-02 单行业（`/industry/:name`）。
 *
 * 五维仪表（分位用 0–100% 刻度条，历史序列用 Sparkline）+ 成分股表 +
 * 相关研报/资讯（R6 `news_stream`，未交付前是带出口的空态）+ 口径与溯源。
 *
 * 数值一律来自 R5 快照；缺失显示「无数据（未核实）」并给原因，不补 0。
 */

const STATUS_LABEL: Record<DataSourceStatus, string> = {
  ok: '数据完整',
  partial: '部分缺失',
  missing: '无数据',
}

const STATUS_TONE: Record<DataSourceStatus, 'ok' | 'warn' | 'danger'> = {
  ok: 'ok',
  partial: 'warn',
  missing: 'danger',
}

const HISTORY_KEY: Record<IndustryDimension, 'pe_percentile' | 'pb_percentile' | 'turnover_percentile' | 'change_pct_20d' | null> = {
  prosperity: null,
  capital: null,
  valuation: 'pe_percentile',
  momentum: 'change_pct_20d',
  crowding: 'turnover_percentile',
}

function PercentileGauge({ value, label }: { value: number | null; label: string }) {
  const ratio = percentileValue(value)
  if (ratio === null) {
    return (
      <p className="text-caption text-text-muted" title={MISSING_CELL_TITLE}>
        无数据（未核实）
      </p>
    )
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
        <span
          className="absolute top-1/2 size-2 -translate-y-1/2 rounded-pill border border-accent bg-surface"
          style={{ left: `calc(${percent}% - 4px)` }}
        />
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
        <div className="flex flex-col gap-0.5">
          <p className="text-caption text-text-secondary">{headlineSpec.label}</p>
          <p
            className={cn(
              'ldst-num text-page font-semibold leading-tight',
              formatted.tone === 'up' ? 'text-up' : formatted.tone === 'down' ? 'text-down' : 'text-text',
            )}
          >
            {formatted.missing ? MISSING : formatted.text}
          </p>
          {formatted.missing && !isPercentile && (
            <p className="text-caption text-text-muted" title={MISSING_CELL_TITLE}>
              无数据（未核实）
            </p>
          )}
        </div>

        {isPercentile && <PercentileGauge value={headlineValue} label={`${headlineSpec.label}分位`} />}

        {historyKey && series.length >= 2 && (
          <div className="flex items-center justify-between gap-2">
            <Sparkline
              values={series.map((point) => point.value)}
              tone={changeTone}
              ariaLabel={`${DIMENSION_TITLE[dimension]}历史序列，${series.length} 个点，${series[0].date} 至 ${series[series.length - 1].date}`}
              width={120}
            />
            <span className="ldst-num shrink-0 text-caption text-text-muted">
              {series[series.length - 1].date}
            </span>
          </div>
        )}

        <dl className="mt-auto flex flex-col gap-1 border-t border-border pt-2">
          {secondary.map((spec) => {
            const item = formatMetric(spec, values[spec.path] ?? null)
            const toneClass =
              item.tone === 'up' ? 'text-up' : item.tone === 'down' ? 'text-down' : 'text-text'
            return (
              <div key={spec.path} className="flex items-baseline justify-between gap-2">
                <dt className="min-w-0 truncate text-caption text-text-secondary" title={spec.path}>
                  {spec.label}
                </dt>
                <dd
                  className={cn('ldst-num shrink-0 text-caption', item.missing ? 'text-text-muted' : toneClass)}
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
    width: '168px',
    sort: (a, b) => a.code.localeCompare(b.code),
    render: (row) => (
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="truncate text-text">{row.name ?? '名称未知'}</span>
        <span className="ldst-num truncate text-caption text-text-muted">{row.code}</span>
      </span>
    ),
  },
  {
    key: 'pe',
    header: 'PE(TTM)',
    align: 'right',
    width: '96px',
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
    width: '92px',
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
    width: '104px',
    sort: (a, b) => compareMetric(a.revenue_yoy, b.revenue_yoy),
    render: (row) => <PctCell value={row.revenue_yoy} />,
  },
  {
    key: 'profit',
    header: '净利同比',
    align: 'right',
    width: '104px',
    sort: (a, b) => compareMetric(a.profit_yoy, b.profit_yoy),
    render: (row) => <PctCell value={row.profit_yoy} />,
  },
  {
    key: 'mainNet',
    header: '主力净流入(5日)',
    align: 'right',
    width: '148px',
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
          className="text-body leading-body text-text underline-offset-2 hover:text-accent-hover hover:underline"
        >
          {item.title}
        </a>
      ) : (
        <p className="text-body leading-body text-text">{item.title}</p>
      )}
      <p className="text-caption text-text-muted">
        {item.source ?? '来源未知'}
        {item.related_codes.length > 0 && ` · 关联 ${item.related_codes.slice(0, 4).join('、')}`}
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
          action={<Link to="/industry" className="text-body text-accent hover:underline">返回产业矩阵</Link>}
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
                  className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text hover:bg-surface-3"
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
              {payload.reason ?? '六个输入数据集没有可用记录，字段全部显式 null'}
              。{dims.length > 0 && `缺失维度：${dims.map((dimension) => DIMENSION_TITLE[dimension]).join('、')}。`}
              先运行 <code className="ldst-num">ldst industry show {payload.meta.name}</code> 检查本地缓存。
            </>
          }
          action={<Button variant="secondary" onClick={reload}>刷新</Button>}
        />
      </Card>
    )
  }
  return (
    <Card>
      <EmptyState
        icon={<Database aria-hidden="true" />}
        title="接口未接入"
        description={
          <>
            页面已按 <code className="ldst-num">GET /api/industry/&#123;name&#125;</code> 契约实现；
            后端提供该只读接口后自动接入，当前不伪造快照。
          </>
        }
        action={<Button variant="secondary" onClick={reload}>重试</Button>}
      />
    </Card>
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
        payload
          ? `${payload.meta.l1_code ?? '未绑定'} · 成分股 ${payload.membership.constituent_count ?? MISSING} 只 · ${STATUS_LABEL[payload.data_source_status]}`
          : '单行业五维快照'
      }
      badge={
        <>
          <DemoStateBadge state={forced} />
          {payload && <Badge tone={STATUS_TONE[payload.data_source_status]}>{STATUS_LABEL[payload.data_source_status]}</Badge>}
          {payload && !payload.meta.bound && <Badge tone="neutral">未绑定</Badge>}
        </>
      }
      actions={
        <>
          <Link
            to={backLink}
            className="inline-flex h-control items-center gap-1 rounded border border-border-strong bg-surface px-3 text-body text-text hover:bg-surface-3"
          >
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
        <ScreenSkeleton metrics={5} rows={6} />
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
          {payload.dimensions[unavailable[0]].reason ?? '接口未给出原因'}。其余维度照常展示，缺失字段不补 0。
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
              payload.membership.constituent_count === null
                ? '成分数量未知'
                : `申万成分 ${payload.membership.constituent_count} 只 · 分类时点 ${payload.membership.as_of ?? MISSING}`
            }
            actions={<Badge tone={payload.constituents.length > 0 ? 'info' : 'warn'}>{payload.constituents.length} 行</Badge>}
            bodyClassName="p-0"
          >
            <DataTable<ConstituentRow>
              columns={CONSTITUENT_COLUMNS}
              rows={payload.constituents}
              rowKey={(row) => row.code}
              caption="逐股快照来自 R5 成分缓存；缺失单元格为「无数据（未核实）」，不按 0 处理"
              density="compact"
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
                />
              }
            />
          </SectionCard>
        </div>

        <aside className="flex flex-col gap-4 desk:col-span-4">
          <SectionCard
            title="相关研报与资讯"
            subtitle="R6 news_stream 关联到本行业"
            actions={<Badge tone={relatedEmpty ? 'neutral' : 'info'}>{payload.related.length} 条</Badge>}
            bodyClassName={relatedEmpty ? 'p-0' : 'px-4 py-2'}
          >
            {relatedEmpty ? (
              <EmptyState
                compact
                icon={<Newspaper aria-hidden="true" />}
                title="暂无关联资讯"
                description="R6 news_stream 尚未交付；接口上线后这里展示该行业研报与资讯。前端不抓取外部内容（DEC-006）。"
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
            <dl className="flex flex-col gap-1.5 text-caption leading-body">
              <div className="flex justify-between gap-3">
                <dt className="text-text-secondary">申万一级代码</dt>
                <dd className="ldst-num text-text">{payload.meta.l1_code ?? '未绑定'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-text-secondary">口径</dt>
                <dd className="text-text">{payload.meta.caliber ?? MISSING}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-text-secondary">成分股 / 分类代码</dt>
                <dd className="ldst-num text-text">
                  {formatInt(payload.membership.constituent_count)} / {formatInt(payload.membership.classified_codes)}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-text-secondary">板块口径</dt>
                <dd className="text-right text-text">{payload.meta.board_name ?? '未配置（同名≠同成分）'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-text-secondary">锚点</dt>
                <dd className="text-right text-text">{payload.meta.anchors.length > 0 ? payload.meta.anchors.join('、') : MISSING}</dd>
              </div>
            </dl>
            {payload.membership.reason && (
              <p className="mt-2 border-t border-border pt-2 text-caption leading-body text-text-muted">
                {payload.membership.reason}
              </p>
            )}
          </SectionCard>

          <SectionCard
            title="溯源与缺口"
            subtitle={`${payload.provenance.sources.length} 个数据源 · ${payload.missing.length} 个字段无数据`}
            tone={payload.data_source_status === 'ok' ? 'ok' : payload.data_source_status === 'partial' ? 'warn' : 'danger'}
          >
            <ul className="flex flex-col gap-1.5 text-caption leading-body">
              {payload.provenance.sources.map((source) => (
                <li key={source.id} className="flex items-baseline justify-between gap-2">
                  <span className="ldst-num truncate text-text-secondary" title={source.id}>
                    {source.dataset}
                  </span>
                  <span className="ldst-num shrink-0 text-text-muted">{formatTime(source.retrieved_at)}</span>
                </li>
              ))}
              {payload.provenance.sources.length === 0 && (
                <li className="text-text-muted">未返回数据源清单（不可视为已核实）</li>
              )}
            </ul>
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
                  <p key={dimension}>
                    <span className="text-text-secondary">{dimension}</span>：{formula}
                  </p>
                ))}
              </div>
            )}
            {payload.source_detail && (
              <p className="mt-2 text-caption leading-body text-text-muted">{payload.source_detail}</p>
            )}
          </SectionCard>
        </aside>
      </div>
    </div>
  )
}
