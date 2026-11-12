import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FlaskConical } from 'lucide-react'

import {
  fetchBacktestCard,
  fetchBacktestRuns,
  isBacktestEndpointMissing,
  type BacktestRunCard,
  type BacktestRunSummary,
} from '../../api/backtest'
import { PageHeader } from '../../components/PageShell'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBlock,
  Field,
  SectionCard,
  Select,
  Skeleton,
  SkeletonRows,
} from '../../components/ui'
import { useAsync } from '../../lib/useAsync'
import { MISSING } from '../../lib/format'
import { EvidenceSection } from './EvidenceSection'
import { ParamsSection } from './ParamsSection'
import { ResultSection } from './ResultSection'

/**
 * 策略回测（`/backtest`，QUOT-534）。
 *
 * 三区：参数区（QUOT-535 触发面）/ 结果区 / 证据区。
 * 参数区提交参数并轮询任务，拿到 `run_id` 后本屏切换到该运行卡；
 * 结果区的指标、曲线、成本档位与校验门**全部**读运行卡，前端不重算任何数值。
 * `?run=bt_xxxxxxxx` 可深链到指定运行卡，缺省取后端给出的 `latest`。
 */

function runLabel(run: BacktestRunSummary): string {
  const gate = run.gate_passed === null ? '门槛未知' : run.gate_passed ? '门槛通过' : '门槛未通过'
  return `${run.run_id} · ${run.symbol ?? MISSING} · ${run.period?.start ?? MISSING}~${run.period?.end ?? MISSING} · ${gate}`
}

function errorDetail(error: unknown): string {
  return error instanceof Error ? error.message : '未知错误'
}

function CardSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在读取运行卡…</span>
      <Card className="p-4">
        <SkeletonRows rows={3} slots={['24%', '18%', '40%']} />
      </Card>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} className="h-metric" />
        ))}
      </div>
      <Card className="p-4">
        <div style={{ height: 320 }}>
          <Skeleton className="h-full" />
        </div>
      </Card>
    </div>
  )
}

export default function BacktestScreen() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('run')

  const runs = useAsync((signal) => fetchBacktestRuns(signal, 100), [])
  const summaries = runs.data?.runs ?? []
  const fallbackRunId = runs.data?.latest ?? summaries[0]?.run_id ?? null
  const runId = requested ?? fallbackRunId

  const card = useAsync(
    (signal) => fetchBacktestCard(runId as string, signal),
    [runId],
    { enabled: runId !== null },
  )

  const selectRun = (nextRunId: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('run', nextRunId)
    setSearchParams(next, { replace: true })
  }

  const resetToLatest = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('run')
    setSearchParams(next, { replace: true })
  }

  const refresh = () => {
    runs.reload()
    if (runId !== null) card.reload()
  }

  // 参数区跑完一张新卡：地址栏切到它，列表同步刷新；结果区随 runId 依赖自动重取。
  const handleSettled = (nextRunId: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('run', nextRunId)
    setSearchParams(next, { replace: true })
    runs.reload()
  }

  const runsMissing = runs.status === 'error' && isBacktestEndpointMissing(runs.error)
  const cardMissing = card.status === 'error' && isBacktestEndpointMissing(card.error)
  const busy = runs.status === 'loading' || card.status === 'loading'

  const cardData: BacktestRunCard | null = card.status === 'ready' ? card.data : null

  const subtitle: ReactNode = cardData ? (
    <span>
      <span className="ldst-num">{cardData.symbol}</span> · <span className="ldst-num">{cardData.strategy}</span> ·{' '}
      <span className="ldst-num">
        {cardData.period.start ?? MISSING} ~ {cardData.period.end ?? MISSING}
      </span>{' '}
      · <span className="ldst-num">{cardData.period.trading_days ?? MISSING}</span> 交易日
    </span>
  ) : (
    <span>只读运行卡 · 前端不重算 · 参数区可触发回测</span>
  )

  const runPicker =
    summaries.length > 0 ? (
      <SectionCard
        title="选择运行卡"
        subtitle={`${summaries.length} 张 · GET /api/backtest/runs`}
        actions={<Badge tone="neutral">新的在前</Badge>}
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <Field label="运行卡" htmlFor="backtest-run" hint="列表由后端落盘产物生成，前端不维护运行清单">
            <Select
              id="backtest-run"
              value={runId ?? ''}
              onChange={(event) => selectRun(event.target.value)}
            >
              {runId !== null && !summaries.some((run) => run.run_id === runId) && (
                <option value={runId}>{runId}（深链）</option>
              )}
              {summaries.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {runLabel(run)}
                </option>
              ))}
            </Select>
          </Field>
          {requested !== null && (
            <Button variant="ghost" onClick={resetToLatest}>
              回到最新
            </Button>
          )}
        </div>
      </SectionCard>
    ) : null

  let body: ReactNode
  if (runId === null) {
    if (runs.status === 'loading') {
      body = (
        <Card className="p-4">
          <SkeletonRows rows={5} slots={['18%', '22%', '14%', '20%', '16%']} />
        </Card>
      )
    } else if (runsMissing) {
      body = (
        <Card>
          <EmptyState
            icon={<FlaskConical aria-hidden="true" />}
            title="回测接口尚未接入"
            description={
              <>
                本机只读接口 <code className="ldst-num">GET /api/backtest/runs</code> 未上线或旧版后端返回 404。
                升级后端后点「刷新」；页面不会编造运行卡。
              </>
            }
            action={
              <Button variant="secondary" onClick={refresh}>
                刷新
              </Button>
            }
          />
        </Card>
      )
    } else if (runs.status === 'error') {
      body = (
        <ErrorBlock
          variant="banner"
          title="运行卡列表读取失败"
          detail={errorDetail(runs.error)}
          onRetry={refresh}
        />
      )
    } else {
      body = (
        <Card>
          <EmptyState
            icon={<FlaskConical aria-hidden="true" />}
            title="还没有回测运行卡"
            description={
              <>
                本机数据目录还没有落盘产物。在上方参数区提交一次回测，运行卡生成后点「刷新」。
              </>
            }
            action={
              <Button variant="secondary" onClick={refresh}>
                刷新
              </Button>
            }
          />
        </Card>
      )
    }
  } else if (card.status === 'loading') {
    body = <CardSkeleton />
  } else if (cardMissing) {
    body = (
      <Card>
        <EmptyState
          icon={<FlaskConical aria-hidden="true" />}
          title="回测接口尚未接入"
          description={
            <>
              运行卡接口 <code className="ldst-num">GET /api/backtest/runs/&#123;run_id&#125;</code> 暂不可用，
              本页不展示任何替代数据。
            </>
          }
          action={
            <Button variant="secondary" onClick={refresh}>
              刷新
            </Button>
          }
        />
      </Card>
    )
  } else if (card.status === 'error' || !card.data) {
    body = (
      <ErrorBlock
        variant="banner"
        title={`运行卡 ${runId} 读取失败`}
        detail={errorDetail(card.error)}
        onRetry={refresh}
      />
    )
  } else {
    body = (
      <>
        <ResultSection card={card.data} />
        <EvidenceSection card={card.data} />
      </>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="策略回测"
        subtitle={subtitle}
        badge={
          cardData && (
            <Badge tone={cardData.gate.passed ? 'ok' : 'danger'}>
              校验门{cardData.gate.passed ? '通过' : '未通过'}
            </Badge>
          )
        }
        refresh={{ onClick: refresh, loading: busy, disabled: busy }}
      />
      <ParamsSection defaultPeriod={cardData?.period ?? null} onSettled={handleSettled} />
      {runPicker}
      {body}
    </div>
  )
}
