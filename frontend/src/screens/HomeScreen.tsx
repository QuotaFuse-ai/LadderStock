import { useMemo } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'

import {
  describeApiError,
  isApiError,
  isEndpointMissing,
  type IndicesPayload,
  type WatchlistPayload,
} from '../api'
import type { PositionsPayload } from '../api/radar'
import { useAsync } from '../lib/useAsync'
import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import { MISSING, formatChangePct, formatTime, localDateIso } from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorBlock,
  SectionCard,
  SignedNumber,
  StatusDot,
  type SourceBadgeMeta,
} from '../components/ui'
import { useDensity } from '../lib/prefs'
import { useReviewDraft } from '../../stores/reviewDraft'
import { DEMO_TRADE_DATE } from './home/demo'
import { HomeIndexStrip } from './home/HomeIndexStrip'
import { HomePositionsCard } from './home/HomePositionsCard'
import { HomeSearch } from './home/HomeSearch'
import { HomeWatchlistCard } from './home/HomeWatchlistCard'
import { useHomeDemoEnabled, useHomeSource } from './home/useHomeSource'

/**
 * 首页（SPEC-HOME-V2 / QUOT-381）。
 *
 * 开盘前 5 分钟的工作台：大盘在哪（A 指数条）→ 今天研究什么（B 研究入口）→
 * 自己的票（C 自选 / D 持仓）→ 待办与最近异动（E/F）。
 *
 * 五路只读取数：`/api/indices` `/api/watchlist` `/api/positions` `/api/watch/snapshot`
 * `/api/validation`。任何一路缺失都显示 `—` + 原因，**不补 0、不编造**；
 * 接口 404 是块级「未接入」，不把整屏打成错误。演示数据走 `home/useHomeSource`，
 * 与真实通道互斥，**真实请求失败不回落到演示数据**（§5.6）。
 * 五态演示统一走 `?state=empty|loading|error|partial|offline`（§8）。
 */

interface Todo {
  tone: 'warn' | 'danger' | 'accent'
  tag: string
  title: string
  to: string
  action: string
}

function goodEnvelope(
  envelope: { freshness: string | null; source_status: string | null; source_detail: string | null; data_as_of: string | null } | null,
): SourceBadgeMeta | null {
  if (!envelope) return null
  return {
    freshness: envelope.freshness,
    source_status: envelope.source_status,
    source_detail: envelope.source_detail,
    data_as_of: envelope.data_as_of,
  }
}

export default function HomeScreen() {
  const forced = useForcedState()
  const navigate = useNavigate()
  // 行高档位只由用户的密度偏好决定（§11.18「密度只改行高」）：屏内不写死，
  // 否则「设置 → 表格密度」在这张屏上会是静默失效的。
  const density = useDensity()
  const [params] = useSearchParams()
  const demoEnabled = useHomeDemoEnabled()
  // 演示源讲的是 `watch/demo.ts` 同一个交易日的 facts：请求日期跟随它，`?date=` 优先。
  const tradeDate = params.get('date') || (demoEnabled ? DEMO_TRADE_DATE : localDateIso())
  const { source } = useHomeSource(tradeDate)

  const indices = useAsync((signal) => source.indices(signal), [source])
  const watchlist = useAsync((signal) => source.watchlist(signal), [source])
  const positions = useAsync((signal) => source.positions(tradeDate, signal), [source, tradeDate])
  const watch = useAsync((signal) => source.watchSnapshot(signal), [source])
  const validation = useAsync((signal) => source.validation(tradeDate, signal), [source, tradeDate])

  const requests = [indices, watchlist, positions, watch, validation]
  const reloadAll = () => requests.forEach((request) => request.reload())

  const indicesPayload: IndicesPayload | null = indices.data?.payload ?? null
  const watchlistPayload: WatchlistPayload | null = watchlist.data ?? null
  const positionsPayload: PositionsPayload | null = positions.data ?? null
  const watchRows = watch.data?.payload.rows ?? []
  const conditions = validation.data?.payload.conditions ?? []
  const reconcile = validation.data?.payload.reconcile ?? null

  const reviewSections = useReviewDraft((state) => state.sections)
  const reviewChars = useMemo(
    () => Object.values(reviewSections).reduce((total, text) => total + text.trim().length, 0),
    [reviewSections],
  )

  const allLoading = requests.every((request) => request.status === 'loading')
  const allFailed = requests.every((request) => request.status === 'error')
  const allDisconnected =
    allFailed && requests.every((request) => isApiError(request.error) && request.error.kind === 'not-connected')
  // 404 是「该块接口未接入」，不是整屏错误；只有真正的取数失败才参与 partial 判定。
  const hardFailures = requests.filter(
    (request) => request.status === 'error' && !isEndpointMissing(request.error),
  )
  // 五路全是 404（后端还没交付这些读接口）时整屏仍是工作台，由各个块自己说「未接入」。
  const onlyMissingEndpoints =
    allFailed && requests.every((request) => isEndpointMissing(request.error))
  const allReady = requests.every((request) => request.status === 'ready')
  const isEmpty =
    allReady &&
    (indicesPayload?.items.length ?? 0) === 0 &&
    (watchlistPayload?.items.length ?? 0) === 0 &&
    (positionsPayload?.position_count ?? 0) === 0 &&
    watchRows.length === 0 &&
    conditions.length === 0

  const derived: PageState = allLoading
    ? 'loading'
    : allDisconnected
      ? 'offline'
      : allFailed && !onlyMissingEndpoints
        ? 'error'
        : hardFailures.length > 0
          ? 'partial'
          : allReady
            ? isEmpty
              ? 'empty'
              : 'normal'
            : 'normal'

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'
  const offline = state === 'offline'
  const busy = requests.some((request) => request.status === 'loading')

  // 强制态下数据一律按「不可用」处理，保证骨架之外的强制态不依赖后端。
  const viewWatchlist = usable ? watchlistPayload : null
  const viewPositions = usable ? positionsPayload : null
  const viewWatch = usable ? watchRows : []
  const viewConditions = usable ? conditions : []

  // forced=offline 时让块走自己的离线条；forced=partial 时让块走块级失败条，成功内容保留。
  const blockStatus = (status: 'loading' | 'ready' | 'error'): 'loading' | 'ready' | 'error' =>
    state === 'offline' ? 'ready' : state === 'partial' ? 'error' : status

  const movers = useMemo(
    () =>
      [...viewWatch]
        .filter((row) => row.change_pct !== null)
        .sort((a, b) => Math.abs(b.change_pct ?? 0) - Math.abs(a.change_pct ?? 0))
        .slice(0, 3),
    [viewWatch],
  )

  const metas = useMemo<SourceBadgeMeta[]>(() => {
    const list: SourceBadgeMeta[] = []
    const indexMeta = goodEnvelope(indices.data)
    if (indexMeta) list.push(indexMeta)
    if (watchlistPayload) {
      list.push({
        freshness: watchlistPayload.as_of ? 'cached' : null,
        source_status: watchlistPayload.data_source_status ?? null,
        source_detail: watchlistPayload.price_source ?? null,
        data_as_of: watchlistPayload.as_of ?? null,
      })
    }
    if (positionsPayload) {
      list.push({
        freshness: positionsPayload.as_of ? 'cached' : null,
        source_status: positionsPayload.data_source_status,
        source_detail: positionsPayload.price_source,
        data_as_of: positionsPayload.as_of,
      })
    }
    const watchMeta = goodEnvelope(watch.data)
    if (watchMeta) list.push(watchMeta)
    const validationMeta = goodEnvelope(validation.data)
    if (validationMeta) list.push(validationMeta)
    return list
  }, [indices.data, watchlistPayload, positionsPayload, watch.data, validation.data])

  const degraded = metas.filter((meta) => meta.source_status !== 'ok')
  const unavailable = requests.filter(
    (request) => request.status === 'error' && isEndpointMissing(request.error),
  ).length
  const latestFetch =
    [indices.data?.fetched_at, watch.data?.fetched_at, validation.data?.fetched_at]
      .filter((value): value is string => !!value)
      .sort()
      .pop() ?? null

  const summary =
    state === 'loading'
      ? '读取中…'
      : state === 'offline'
        ? '本地服务未连接'
        : state === 'error'
          ? '取数失败'
          : state === 'empty'
            ? `${tradeDate} · 今日无数据`
            : `${unavailable > 0 ? '部分数据未接入' : degraded.length > 0 ? '部分数据降级' : '数据完整'} · 更新于 ${formatTime(latestFetch)}`

  const todos = useMemo<Todo[]>(() => {
    const list: Todo[] = []
    if (usable && viewConditions.length > 0 && !reconcile) {
      list.push({
        tone: 'warn',
        tag: '待对账',
        title: `${viewConditions.length} 条验证条件等待收盘对账`,
        to: '/validate',
        action: '去验证',
      })
    }
    if (usable && reviewChars === 0) {
      list.push({
        tone: 'accent',
        tag: '待写',
        title: '今日复盘草稿还是空的',
        to: '/review',
        action: '去复盘',
      })
    }
    if (usable && degraded.length > 0) {
      list.push({
        tone: 'danger',
        tag: '数据降级',
        title: degraded[0].source_detail || '部分数据源降级，指标可能不完整',
        to: '/settings',
        action: '看数据源',
      })
    }
    return list.slice(0, 3)
  }, [usable, viewConditions.length, reconcile, reviewChars, degraded])

  const failedLabels = useMemo(() => {
    const labels: string[] = []
    if (indices.status === 'error' && !isEndpointMissing(indices.error)) labels.push('指数条')
    if (watchlist.status === 'error' && !isEndpointMissing(watchlist.error)) labels.push('自选')
    if (positions.status === 'error' && !isEndpointMissing(positions.error)) labels.push('持仓')
    if (watch.status === 'error' && !isEndpointMissing(watch.error)) labels.push('盯盘快照')
    if (validation.status === 'error' && !isEndpointMissing(validation.error)) labels.push('验证条件')
    return labels
  }, [indices.status, indices.error, watchlist.status, watchlist.error, positions.status, positions.error, watch.status, watch.error, validation.status, validation.error])

  const header = (
    <PageHeader
      title="首页"
      subtitle={summary}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <Button
          variant="primary"
          loading={busy && state !== 'loading'}
          disabled={state === 'loading'}
          onClick={reloadAll}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          刷新全部
        </Button>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenSkeleton metrics={2} rows={8} />
      </div>
    )
  }

  if (state === 'error') {
    const firstError = requests.map((request) => request.error).find(Boolean)
    const info = describeApiError(firstError)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ErrorBlock {...info} onRetry={reloadAll} />
      </div>
    )
  }

  const indexStrip = (
    <HomeIndexStrip
      envelope={usable ? indices.data : null}
      status={blockStatus(indices.status)}
      error={usable ? indices.error : null}
      offline={offline}
      onRetry={indices.reload}
    />
  )

  if (state === 'empty') {
    return (
      <div className="flex flex-col gap-4">
        {header}
        {indexStrip}
        <HomeSearch />
        <EmptyState
          title={`${tradeDate} 没有可用数据`}
          description="指数、自选、持仓、盯盘快照、验证条件五路都是空的。先启动本机服务，再同步当日行情。"
          action={
            <div className="flex flex-col items-center gap-2">
              <code className="ldst-num rounded border border-border bg-surface-2 px-2 py-1 text-caption text-text-secondary">
                ldst sync --date {tradeDate}
              </code>
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button
                  variant="secondary"
                  onClick={reloadAll}
                  leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
                >
                  重新检查
                </Button>
                <Button variant="ghost" onClick={() => navigate('/settings?tab=peers')}>
                  看数据源
                </Button>
              </div>
            </div>
          }
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {header}

      {offline && <OfflineBanner onRetry={reloadAll} />}
      {state === 'partial' && (
        <ErrorBlock
          variant="banner"
          title="部分数据源不可用"
          detail={`${failedLabels.length > 0 ? failedLabels.join('、') : '部分数据'} 取数失败；其余内容照常展示，缺失处显示 — 与原因。`}
          onRetry={reloadAll}
          retryLabel="重试该段"
        />
      )}

      {indexStrip}

      <HomeSearch />

      <HomeWatchlistCard
        payload={viewWatchlist}
        status={blockStatus(watchlist.status)}
        error={usable ? watchlist.error : null}
        offline={offline}
        onRetry={watchlist.reload}
        density={density}
      />

      <HomePositionsCard
        payload={viewPositions}
        status={blockStatus(positions.status)}
        error={usable ? positions.error : null}
        offline={offline}
        onRetry={positions.reload}
        density={density}
      />

      <div className="grid gap-4 desk:grid-cols-12">
        {todos.length > 0 && (
          <Card className="desk:col-span-7">
            <CardHeader title="待办" subtitle={<><span className="ldst-num">{todos.length}</span> 条需要处理</>} />
            <ul>
              {todos.map((todo) => (
                <li key={todo.tag + todo.title} className="border-b border-border last:border-b-0">
                  <Link
                    to={todo.to}
                    className="flex min-h-row items-center gap-3 px-4 transition-colors duration-fast ease-ldst hover:bg-surface-3"
                  >
                    <StatusDot tone={todo.tone}>{todo.tag}</StatusDot>
                    <span className="min-w-0 flex-1 truncate text-body text-text" title={todo.title}>
                      {todo.title}
                    </span>
                    <span className="shrink-0 text-caption text-text-secondary">{todo.action} →</span>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        )}

        <SectionCard
          className="desk:col-span-5"
          title="最近异动"
          subtitle={viewWatch.length > 0 ? '按涨跌幅绝对值取前 3' : '快照不可用'}
          bodyClassName="p-4"
        >
          {movers.length === 0 ? (
            <EmptyState compact title="还没有异动信号" description="盯盘快照为空或已过期，去盯盘页取数。" />
          ) : (
            <ul className="flex flex-col gap-1">
              {movers.map((row) => {
                const change = formatChangePct(row.change_pct)
                return (
                  <li
                    key={row.code}
                    className="flex min-h-row items-center justify-between gap-2 rounded-xs px-1"
                  >
                    <span
                      className="min-w-0 truncate text-body text-text"
                      title={`${row.code} ${row.name ?? MISSING}`}
                    >
                      <span className="ldst-num mr-2 text-caption text-text-muted">{row.code}</span>
                      {row.name ?? MISSING}
                    </span>
                    <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} className="text-body" />
                  </li>
                )
              })}
            </ul>
          )}
          <p className="mt-3 border-t border-border pt-2 text-caption text-text-muted">
            口径：快照涨跌幅绝对值排序；仅供扫读，不构成建议。
          </p>
        </SectionCard>
      </div>

      <p className="text-caption leading-body text-text-muted">
        指数与自选为行情快照，不是逐笔实时；持仓口径见「我的持仓」页。
        {todos.length === 0 && ' 今日无待办。'}
      </p>
    </div>
  )
}