import { useRef } from 'react'
import { FileClock, RefreshCw } from 'lucide-react'

import { describeResearchError, type ResearchOverview } from '../../../api/research'
import {
  httpEarningsBriefLoader,
  type EarningsBriefLoader,
  type EarningsBriefResult,
  type EarningsPeriod,
} from '../../../api/earningsBrief'
import {
  EARNINGS_INTRO,
  actualText,
  consensusYearOf,
  dispersionText,
  epsText,
  forecastText,
  gapLines,
  nextConsensusYearOf,
  pctText,
  rangeText,
  reactionSentence,
  sourceNote,
} from '../../../lib/earningsBrief'
import { MISSING, countOrMissing, formatTime, isFiniteNumber } from '../../../lib/format'
import { useAsync } from '../../../lib/useAsync'
import { useDemoEnabled } from '../../../lib/pageState'
import {
  Badge,
  Banner,
  Button,
  DataTable,
  EmptyState,
  SectionCard,
  type Column,
} from '../../ui'

/**
 * 区块 F：财报前瞻与复盘（QUOT-450）。
 *
 * 数据：`GET /api/research/{code}/earnings-brief?periods=6`（QUOT-446 契约）。
 * - 一致预期（EPS 均值 / 区间 / 覆盖机构数 / 最高 ÷ 最低）、各报告期实际值、
 *   同报告期业绩预告照接口返回展示，只做单位与千分位换算；
 * - 发布日 / 反应日 / 当天涨跌当前无源，一律 `—`，逐条写进「没取到的」；
 * - 统计句要求后端算好回传，本组件只展示 `stats.with_reaction` 得出的样本结论，
 *   **不做平均、不做除法**；
 * - 演示模式不读真实接口（与"演示源绝不自动兜底"同一口径）。
 *
 * props 契约与 QUOT-448 的槽位保持一致；`loader` 只给单测注入替身。
 */
export interface EarningsSectionProps {
  overview: ResearchOverview | null
  code: string | null
  className?: string
  loader?: EarningsBriefLoader
}

const BOX = 'rounded border border-border bg-surface-2 px-3 py-2.5'

function columns(): Array<Column<EarningsPeriod>> {
  return [
    {
      key: 'period',
      header: '报告期',
      width: '150px',
      truncate: true,
      cellTitle: (row) => `${row.reportDate}${row.reportName ? ` ${row.reportName}` : ''}`,
      render: (row) => (
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="ldst-num shrink-0">{row.reportDate}</span>
          {row.reportName && (
            <span className="truncate text-caption text-text-muted">{row.reportName}</span>
          )}
        </span>
      ),
    },
    {
      key: 'publish',
      header: '发布日',
      width: '104px',
      render: (row) => (
        <span className="ldst-num" title={row.publishDate ? undefined : (row.publishDateReason ?? undefined)}>
          {row.publishDate ?? MISSING}
        </span>
      ),
    },
    {
      key: 'reaction',
      header: '反应日',
      width: '104px',
      render: (row) => (
        <span className="ldst-num" title={row.reactionDate ? undefined : (row.reactionDateReason ?? undefined)}>
          {row.reactionDate ?? MISSING}
        </span>
      ),
    },
    {
      key: 'move',
      header: '当天涨跌',
      width: '96px',
      numeric: true,
      render: (row) => (
        <span title={row.reactionChangePct === null ? (row.reactionChangePctReason ?? undefined) : undefined}>
          {isFiniteNumber(row.reactionChangePct) ? pctText(row.reactionChangePct) : MISSING}
        </span>
      ),
    },
    {
      key: 'actual',
      header: '实际',
      truncate: true,
      cellTitle: (row) => actualText(row),
      render: (row) => actualText(row),
    },
    {
      key: 'forecast',
      header: '预期',
      truncate: true,
      cellTitle: (row) => (row.forecast ? forecastText(row.forecast) : (row.forecastReason ?? MISSING)),
      render: (row) => (
        <span className={row.forecast ? undefined : 'text-text-muted'}>
          {row.forecast ? forecastText(row.forecast) : MISSING}
        </span>
      ),
    },
  ]
}

function ConsensusBox({ data }: { data: EarningsBriefResult }) {
  const snapshot = data.consensus
  if (!snapshot) {
    return (
      <div className={BOX}>
        <p className="text-caption text-text-muted">一致预期</p>
        <p className="ldst-num text-title font-semibold text-text-secondary">{MISSING}</p>
        <p className="mt-1 text-caption leading-body text-text-muted">
          {data.consensusReason ?? '本地没有这只代码的一致预期快照'}
        </p>
      </div>
    )
  }
  const current = consensusYearOf(snapshot)
  const next = nextConsensusYearOf(snapshot)
  const dispersion = current ? dispersionText(current.min, current.max) : null
  return (
    <>
      <div className={BOX}>
        <p className="text-caption text-text-muted">一致预期 · {current?.year ?? snapshot.currentYear ?? MISSING}E</p>
        <p className="ldst-num text-title font-semibold text-text">{epsText(current?.mean ?? snapshot.epsCurrentYear)}</p>
        <p className="mt-1 text-caption text-text-secondary">
          区间 <span className="ldst-num">{rangeText(current?.min ?? null, current?.max ?? null)}</span>
          {' · '}
          <span className="ldst-num">{countOrMissing(current?.analystCount ?? snapshot.analystCount)}</span> 家覆盖
        </p>
        <p className="text-caption text-text-muted">
          最高 ÷ 最低 <span className="ldst-num">{dispersion ?? MISSING}</span>
          {dispersion ? '（越大分歧越大）' : ''}
        </p>
      </div>
      <div className={BOX}>
        <p className="text-caption text-text-muted">
          下一财年 · {next?.year ?? MISSING}E
        </p>
        <p className="ldst-num text-title font-semibold text-text">{epsText(next?.mean ?? snapshot.epsNextYear)}</p>
        <p className="mt-1 text-caption text-text-secondary">
          区间 <span className="ldst-num">{rangeText(next?.min ?? null, next?.max ?? null)}</span>
          {' · '}
          <span className="ldst-num">{countOrMissing(next?.analystCount ?? null)}</span> 家覆盖
        </p>
        <p className="text-caption text-text-muted">快照 {snapshot.asOf ?? MISSING}</p>
      </div>
      <div className={BOX}>
        <p className="text-caption text-text-muted">最新一期实际（{data.stats.latestReportDate ?? MISSING}）</p>
        <p className="ldst-num text-title font-semibold text-text">
          {epsText(data.stats.latestReportEps)}
        </p>
        <p className="mt-1 text-caption text-text-secondary">EPS · 报告期累计口径</p>
        <p className="text-caption text-text-muted">
          营收与归母净利见下表「实际」列
        </p>
      </div>
    </>
  )
}

export function EarningsSection({ code, className, loader }: EarningsSectionProps) {
  const demo = useDemoEnabled()
  const loaderRef = useRef<EarningsBriefLoader>(loader ?? httpEarningsBriefLoader)
  loaderRef.current = loader ?? httpEarningsBriefLoader

  const request = useAsync<EarningsBriefResult>(
    (signal) => loaderRef.current(code as string, signal),
    [code],
    { enabled: Boolean(code) && !demo },
  )
  const data = request.data
  const error = request.status === 'error' ? request.error : null
  const empty = data ? data.earnings.length === 0 && data.consensus === null : false

  const subtitle = data
    ? `${data.name ?? code ?? ''} · 最近 ${data.periods} 期${data.stats.latestReportDate ? ` · 最新报告期 ${data.stats.latestReportDate}` : ''}`
    : demo
      ? '演示模式'
      : '只读本机缓存'

  const actions = (
    <>
      {data && <Badge tone={data.dataSourceStatus === 'ok' ? 'ok' : 'warn'}>{data.dataSourceStatus === 'ok' ? '本机缓存' : '缺数'}</Badge>}
      <Button
        variant="ghost"
        className="h-7 px-2 text-caption"
        onClick={request.reload}
        loading={request.refreshing}
        disabled={demo || !code}
        disabledReason={demo ? '演示模式不读真实接口' : !code ? '缺少证券代码' : undefined}
        aria-label="重新取财报数据"
      >
        {!request.refreshing && <RefreshCw className="size-3.5" aria-hidden="true" />} 重新取
      </Button>
    </>
  )

  return (
    <SectionCard className={className} title="财报前瞻与复盘" subtitle={subtitle} actions={actions}>
      {demo ? (
        <EmptyState
          compact
          icon={<FileClock aria-hidden="true" />}
          title="演示模式不读取真实财报明细"
          description="本卡的数据只来自本机缓存（一致预期 / 财务指标 / 业绩预告）。关掉设置页的虚拟数据开关后，会读取真实接口；这里不拿演示数字冒充。"
        />
      ) : !code ? (
        <EmptyState
          compact
          icon={<FileClock aria-hidden="true" />}
          title="缺少证券代码"
          description="路径应为 /research/600000（6 位数字，可带 .SH / .SZ / .BJ 后缀）。"
        />
      ) : request.status === 'loading' && !data ? (
        <div aria-busy="true" className="flex h-24 items-center justify-center text-caption text-text-muted">
          正在读取 {code} 的一致预期、财报明细与业绩预告…
        </div>
      ) : (
        <>
          <p className="mb-3 text-caption leading-body text-text-secondary">{EARNINGS_INTRO}</p>

          {error && (
            <Banner
              tone="danger"
              title="财报数据读取失败"
              className="mb-3"
              action={
                <Button variant="secondary" onClick={request.reload}>
                  重试
                </Button>
              }
            >
              {describeResearchError(error).detail}
            </Banner>
          )}

          {!data && !error && (
            <EmptyState
              compact
              icon={<FileClock aria-hidden="true" />}
              title="还没有读到财报数据"
              description="本机接口未返回内容；点「重试」再读一次。"
              action={
                <Button variant="secondary" onClick={request.reload}>
                  重试
                </Button>
              }
            />
          )}

          {data && empty && (
            <EmptyState
              compact
              icon={<FileClock aria-hidden="true" />}
              title={`${data.name ?? code} 暂无财报与一致预期`}
              description={
                data.earningsReason
                ?? data.consensusReason
                ?? '本机缓存里既没有这只代码的财报报告期，也没有一致预期快照；不补 0、不编数。'
              }
            />
          )}

          {data && !empty && (
            <>
              <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
                <ConsensusBox data={data} />
              </div>

              {data.earnings.length > 0 && (
                <DataTable<EarningsPeriod>
                  columns={columns()}
                  rows={data.earnings}
                  rowKey={(row) => row.reportDate}
                  density="compact"
                  caption={`按报告期倒序 · 最近 ${data.earnings.length} 期 · 实际值为报告期累计口径`}
                  empty="本地没有这只代码的报告期记录"
                />
              )}

              <p className="mt-3 text-body leading-body text-text">
                {reactionSentence(data.stats)}
              </p>

              <div className="mt-4">
                <h4 className="mb-1 text-subtitle font-semibold text-text">没取到的</h4>
                <ul className="space-y-1 text-caption leading-body text-text-secondary">
                  {gapLines(data).map((gap) => (
                    <li key={gap}>{gap}</li>
                  ))}
                </ul>
              </div>

              <p className="mt-3 border-t border-border pt-2 text-caption leading-body text-text-muted">
                {sourceNote(data, formatTime(data.fetchedAt))}
              </p>
            </>
          )}
        </>
      )}
    </SectionCard>
  )
}
