import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, Download, ListChecks, RefreshCw } from 'lucide-react'

import {
  isRadarEndpointMissing,
  POOL_PART_LABEL,
  POOL_PART_ORDER,
  POOL_PART_WEIGHT_LABEL,
  type CandidatePoolPayload,
  type PoolCandidate,
  type PoolPart,
} from '../../api/radar'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Card,
  DataTable,
  Drawer,
  EmptyState,
  ErrorBlock,
  MetricCard,
  SourceStatusBadge,
  type Column,
} from '../../components/ui'
import { formatBoards, formatInt, formatMoney, formatOneDecimal, localDateIso, MISSING } from '../../lib/format'
import { withParams } from '../industry/links'
import { DEMO_POOL_DATE } from './demo'
import { useDemoEnabled, usePoolView } from './useRadarView'

/**
 * P-R10-03 候选池（`/pool`）。
 *
 * 分数由后端按固定权重算出（SPEC-CANDIDATE-POOL §3），页面只做展示排序与依据展开，
 * **不重算总分**；分项 `null` 写「无数据（未核实）」，不补 0，也不从别的分项推。
 *
 * 导出只在用户显式点击时发生：Blob → 本地下载，不经过任何网络。
 */

function compareNumber(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return a - b
}

/** 权重缺失显示 `—`，不显示 `0.0%`——缺失不是零（UI-SPEC §8）。 */
function weightPct(value: number | null | undefined): string {
  return value === null || value === undefined ? MISSING : `${formatOneDecimal(value * 100)}%`
}

function PartCell({ value, compact = false }: { value: number | null; compact?: boolean }) {
  if (value === null) {
    if (compact) {
      return (
        <span className="text-text-muted" title="无数据（未核实）：该分项没有数据，不补 0">
          —<span className="sr-only">无数据（未核实）</span>
        </span>
      )
    }
    return <span className="text-caption text-text-muted">无数据（未核实）</span>
  }
  return <span className="ldst-num text-body text-text">{formatOneDecimal(value)}</span>
}

function rawFor(part: PoolPart, candidate: PoolCandidate, height: number | null): string {
  const raw = candidate.raw
  if (part === 'strength') {
    return `${formatBoards(raw.boards)} · 候选集高度 ${formatBoards(height)}`
  }
  if (part === 'standing') {
    return height === null ? MISSING : `相对高度 ${formatBoards(raw.boards)} / ${formatBoards(height)}`
  }
  if (part === 'capital') {
    return raw.net_inflow === null ? MISSING : `候选集内净流入 ${formatMoney(raw.net_inflow)}`
  }
  if (part === 'theme') {
    const tags = raw.theme_tags.length > 0 ? raw.theme_tags.join(' / ') : '无题材标签'
    const change = raw.theme_board_change_pct === null ? MISSING : `${formatOneDecimal(raw.theme_board_change_pct)}%`
    return `${tags} · 板块涨幅 ${change}`
  }
  return raw.turnover_rate === null ? MISSING : `换手率 ${formatOneDecimal(raw.turnover_rate)}%`
}

function DownloadButton({ payload }: { payload: CandidatePoolPayload }) {
  const onExport = () => {
    const file = {
      exported_at: new Date().toISOString(),
      local_only: true,
      note: '候选池本地导出；不含持仓、不上传。',
      payload,
    }
    const blob = new Blob([JSON.stringify(file, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `ladderstock-pool-${payload.date || 'latest'}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }
  return (
    <Button variant="secondary" onClick={onExport} leadingIcon={<Download className="size-3.5" aria-hidden="true" />}>
      导出本地文件
    </Button>
  )
}

function partColumns(
  payload: CandidatePoolPayload,
  candidate: PoolCandidate,
  weightSum: number | null,
): Array<Column<PoolPart>> {
  return [
    {
      key: 'part',
      header: '分项',
      // 不给 width：吃掉剩余宽度，超长分项名走省略号
      truncate: true,
      cellTitle: (part) => `${POOL_PART_LABEL[part]}（${POOL_PART_WEIGHT_LABEL[part]}）`,
      render: (part) => (
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-text">{POOL_PART_LABEL[part]}</span>
          <span className="truncate text-caption text-text-muted">{POOL_PART_WEIGHT_LABEL[part]}</span>
        </span>
      ),
      summary: () => <span className="font-medium text-text">合计</span>,
    },
    {
      key: 'weight',
      header: '权重',
      numeric: true,
      width: '88px',
      render: (part) => <span className={payload.weights[part] == null ? 'text-text-muted' : undefined}>{weightPct(payload.weights[part])}</span>,
      summary: () => weightPct(weightSum),
    },
    {
      key: 'score',
      header: '得分',
      numeric: true,
      width: '88px',
      render: (part) => <PartCell value={candidate.parts[part]} compact />,
      summary: () =>
        candidate.score === null ? (
          <span className="text-text-muted">{MISSING}</span>
        ) : (
          <span className="font-semibold">{formatOneDecimal(candidate.score)}</span>
        ),
    },
    {
      key: 'raw',
      header: '原始输入',
      numeric: true,
      // 内容是「换手率 12.3%」「相对高度 3 / 8」这类混合文本，缺失走 MISSING
      truncate: true,
      cellTitle: (part) => rawFor(part, candidate, payload.height),
      render: (part) => (
        <span className="text-caption leading-body text-text-secondary">{rawFor(part, candidate, payload.height)}</span>
      ),
      summary: () => (
        <span className="text-caption text-text-muted">
          {candidate.absent_parts.length > 0
            ? `缺 ${candidate.absent_parts.map((part) => POOL_PART_LABEL[part] ?? part).join('、')}，不计入分母`
            : '五个分项齐全'}
        </span>
      ),
    },
  ]
}

function BreakdownDrawer({
  candidate,
  payload,
  onClose,
}: {
  candidate: PoolCandidate | null
  payload: CandidatePoolPayload
  onClose: () => void
}) {
  /** 任一分项权重没给，合计就只是部分之和——写 `—`，不把缺数加总成真实量级。 */
  const weightValues = POOL_PART_ORDER.map((part) => payload.weights[part] ?? null)
  const weightSum = weightValues.every((value): value is number => value !== null)
    ? weightValues.reduce((sum, value) => sum + value, 0)
    : null
  return (
    <Drawer
      open={candidate !== null}
      onClose={onClose}
      width="lg"
      title={candidate ? `${candidate.name} ${candidate.code}` : '计算依据'}
      subtitle={candidate ? `总分 ${candidate.score === null ? MISSING : formatOneDecimal(candidate.score)}` : undefined}
    >
      {candidate && (
        <div className="flex flex-col gap-4">
          <DataTable
            columns={partColumns(payload, candidate, weightSum)}
            rows={[...POOL_PART_ORDER]}
            rowKey={(part) => part}
            caption="分项得分与权重（后端载荷原值，页面不重算）"
            density="compact"
            stickyFirstColumn={false}
          />

          {candidate.absent_parts.length > 0 && (
            <Banner tone="warn" title="有分项没有数据">
              {candidate.absent_parts.map((part) => POOL_PART_LABEL[part] ?? part).join('、')}
              缺少数据，按口径不计入分母；页面不补 0，也不重新算总分。
            </Banner>
          )}

          <section className="flex flex-col gap-1">
            <h3 className="text-caption font-medium text-text-secondary">公式（后端口径）</h3>
            <pre className="whitespace-pre-wrap rounded border border-border bg-sunken p-3 text-caption leading-body text-text">
              {payload.formula || '后端未提供公式文本。'}
            </pre>
          </section>

          <p className="text-caption leading-body text-text-muted">
            数据时点 {payload.data_as_of ?? payload.date ?? MISSING} · 来源 {payload.source_detail || MISSING}。
            分数由后端计算，页面只展示依据；导出与查看都在本机完成。
          </p>
        </div>
      )}
    </Drawer>
  )
}

export default function PoolScreen() {
  const [params] = useSearchParams()
  const demo = useDemoEnabled()
  const date = params.get('date') ?? (demo ? DEMO_POOL_DATE : localDateIso())
  const [selected, setSelected] = useState<PoolCandidate | null>(null)

  const view = usePoolView({ date })
  const { forced, state, payload, error, reload } = view

  const columns = useMemo<Array<Column<PoolCandidate>>>(() => {
    const partColumns: Array<Column<PoolCandidate>> = POOL_PART_ORDER.map((part) => ({
      key: part,
      header: POOL_PART_LABEL[part],
      align: 'right',
      width: '96px',
      sort: (a, b) => compareNumber(a.parts[part], b.parts[part]),
      render: (row) => <PartCell value={row.parts[part]} compact />,
    }))
    return [
      {
        key: 'name',
        header: '代码 / 名称',
        width: '148px',
        sort: (a, b) => a.code.localeCompare(b.code),
        render: (row) => (
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="ldst-num text-body text-text">{row.code}</span>
            <span className="truncate text-caption text-text-secondary">{row.name}</span>
          </span>
        ),
      },
      {
        key: 'score',
        header: '总分',
        align: 'right',
        width: '88px',
        sort: (a, b) => compareNumber(a.score, b.score),
        render: (row) => (
          <span className="ldst-num font-semibold text-text">
            {row.score === null ? MISSING : formatOneDecimal(row.score)}
          </span>
        ),
      },
      ...partColumns,
      {
        key: 'absent',
        header: '缺失分项',
        align: 'center',
        width: '120px',
        render: (row) =>
          row.absent_parts.length === 0 ? (
            <span className="text-caption text-text-muted">齐全</span>
          ) : (
            <Badge tone="warn">{row.absent_parts.map((part) => POOL_PART_LABEL[part] ?? part).join('、')}</Badge>
          ),
      },
      {
        key: 'action',
        header: '依据',
        align: 'right',
        width: '72px',
        render: (row) => (
          <Button
            variant="ghost"
            onClick={(event) => {
              event.stopPropagation()
              setSelected(row)
            }}
          >
            展开
          </Button>
        ),
      },
    ]
  }, [])

  const header = (
    <PageHeader
      title="候选池"
      subtitle={`${date} · 高度 ${formatBoards(payload?.height)} · ${formatInt(payload?.candidate_count)} 只候选 · 来源 ${
        payload?.source_detail || MISSING
      } · 数据时点 ${payload?.data_as_of ?? MISSING}`}
      badge={
        <div className="flex flex-wrap items-center gap-2">
          <DemoStateBadge state={forced} />
          {payload && <SourceStatusBadge status={payload.data_source_status} detail={payload.reason} />}
        </div>
      }
      actions={
        <div className="flex items-center gap-2">
          {payload && payload.available && <DownloadButton payload={payload} />}
          <Button
            variant="primary"
            onClick={reload}
            disabled={state === 'loading'}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          >
            刷新
          </Button>
        </div>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={3} rows={7} />
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
            icon={<ListChecks aria-hidden="true" />}
            title="本地服务未连接"
            description="启动 ldst run 后自动接入 /api/pool；未连接时不展示任何候选。"
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
          title="候选池取数失败"
          detail={error instanceof Error ? error.message : '未知错误'}
          onRetry={reload}
        />
      </div>
    )
  }

  if (state === 'empty' || !payload) {
    const endpointMissing = payload === null && isRadarEndpointMissing(error)
    return (
      <div className="flex flex-col gap-5">
        {header}
        <Card>
          <EmptyState
            icon={<Database aria-hidden="true" />}
            title={endpointMissing ? '接口未接入' : '候选池未生成'}
            description={
              endpointMissing ? (
                <>
                  页面已按 <code className="ldst-num">GET /api/pool</code> 契约实现；后端提供该只读接口后自动接入，
                  当前不伪造候选。
                </>
              ) : (
                <>
                  {payload?.reason ?? '本机缺少生成候选池所需的输入数据集。'}
                  {payload && payload.missing.length > 0 && (
                    <> 缺：{payload.missing.join('、')}。</>
                  )}{' '}
                  先运行 <code className="ldst-num">ldst pool</code> 生成本地结果，再点「刷新」。
                </>
              )
            }
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button variant="secondary" onClick={reload}>刷新</Button>
                {!demo && (
                  <a
                    href={withParams('/pool', { demo: '1', state: forced })}
                    className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text transition-colors duration-fast ease-ldst hover:bg-surface-3"
                  >
                    查看演示数据
                  </a>
                )}
              </div>
            }
          />
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="warn" title="部分输入数据集缺失">
          {payload.missing.length > 0 ? `缺：${payload.missing.join('、')}。` : ''}
          {payload.reason ?? '部分分项无法计算'}。缺失分项显示「无数据（未核实）」，不补 0、不重算总分。
        </Banner>
      )}

      <div className="grid grid-cols-3 gap-4">
        <MetricCard
          label="候选数"
          hero
          value={payload.candidate_count}
          unit="只"
          note={`涨停池 ${payload.counts.limit_pool_rows} 行 · 去重 ${payload.counts.dropped_duplicates} 行`}
        />
        <MetricCard
          label="最高连板"
          value={formatBoards(payload.height)}
          note="候选集高度（用于强度与地位分项）"
        />
        <MetricCard
          label="缺失数据集"
          value={payload.missing.length}
          unit="个"
          note={payload.missing.length > 0 ? payload.missing.join('、') : '五个输入数据集齐全'}
        />
      </div>

      <DataTable<PoolCandidate>
        columns={columns}
        rows={payload.candidates}
        rowKey={(row) => row.code}
        caption={`${payload.date} 候选池（${payload.candidates.length} 只）；点击「展开」看分项依据，分数由后端计算`}
        density="compact"
        maxHeight="calc(100vh - 360px)"
        onRowClick={(row) => setSelected(row)}
        empty={<EmptyState compact title="没有候选" description="换一个交易日，或先运行 ldst pool。" />}
      />

      <p className="text-caption leading-body text-text-muted">
        口径：候选分项与总分全部取自后端（SPEC-CANDIDATE-POOL），页面排序只改变展示顺序；
        「—」表示无数据（未核实），不补 0。
        导出按钮生成的是本地 JSON 文件，不经过网络；文件里不含持仓与任何隐私数据。
      </p>

      <BreakdownDrawer candidate={selected} payload={payload} onClose={() => setSelected(null)} />
    </div>
  )
}
