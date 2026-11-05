import { useMemo } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'

import { describeApiError, isApiError } from '../api'
import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import {
  MISSING,
  formatChangePct,
  formatTime,
  localDateIso,
} from '../lib/format'
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
import { draftCharCount, selectSlot, useReviewDraft } from '../../stores/reviewDraft'
import { DEMO_TRADE_DATE } from './home/demo'
import { HomeIndexStrip } from './home/HomeIndexStrip'
import { HomePositionsCard } from './home/HomePositionsCard'
import { HomeSearch } from './home/HomeSearch'
import { HomeWatchlistCard } from './home/HomeWatchlistCard'
import { useHomeDemoEnabled, useHomeBlocks } from './home/useHomeSource'

/**
 * 首页（SPEC-HOME-V2 v1.0 / QUOT-381 S3 接线）。
 *
 * 从「收盘后的市场体检报告」改成「开盘前 5 分钟的工作台」，四块纵向满宽：
 * A 大盘与全球指数 → B 今天，想研究什么 → C 我的自选 → D 我的持仓；E 待办 / F 最近异动并排下移。
 * 情绪温度 / 连板梯队 Top 10 / 题材强度 Top 5 移出首页（信息不丢，都在 `/market` 对应 tab）；
 * 屏体只负责装配与五路状态合成，块内取数与缺数口径各自收在 `screens/home/` 的块件里。
 *
 * 五态演示统一走 `?state=empty|loading|error|partial|offline`；
 * 演示数据走 `home/useHomeSource`，与真实通道互斥，**真实请求失败不回落到演示数据**（§5.6）。
 */

/** 溯源徽标只需要 envelope 的公共字段（§5.5 单一实现）；平面响应（自选）四键恒为 null。 */
interface EnvelopeMeta extends SourceBadgeMeta {
  fetched_at: string | null
}

interface Todo {
  tone: 'warn' | 'danger' | 'accent'
  tag: string
  title: string
  to: string
  action: string
}

export default function HomeScreen() {
  const forced = useForcedState()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const demoEnabled = useHomeDemoEnabled()
  // 演示源讲的是同一个交易日的 facts：请求日期跟随它，否则「2026-11-01 的演示数据」这种
  // 自相矛盾的标题会写在首屏上。`?date=` 优先。
  const tradeDate = params.get('date') || (demoEnabled ? DEMO_TRADE_DATE : localDateIso())
  const { views, requests, reloadAll } = useHomeBlocks(tradeDate)

  const indexView = views.indices
  const watchlistView = views.watchlist
  const positionsView = views.positions
  const watchView = views.watch
  const validationView = views.validation

  const watchRows = (watchView.state === 'normal' || watchView.state === 'partial' ? watchView.data?.payload.rows : null) ?? []
  const conditions =
    (validationView.state === 'normal' || validationView.state === 'partial'
      ? validationView.data?.payload.conditions
      : null) ?? []
  const reconcile =
    (validationView.state === 'normal' || validationView.state === 'partial'
      ? validationView.data?.payload.reconcile
      : null) ?? null

  const reviewSections = useReviewDraft((state) => selectSlot(state, tradeDate).sections)
  const reviewChars = useMemo(
    () => draftCharCount(reviewSections),
    [reviewSections],
  )

  // 整屏六态：五路合成（§7）。块级态留在块件里，这里只决定骨架 / 离线条 / 空态。
  const derived = useMemo<PageState>(() => {
    const statuses = requests.map((request) => request.status)
    if (statuses.every((status) => status === 'loading')) return 'loading'
    const errors = requests.map((request) => request.error).filter(Boolean)
    if (errors.length === statuses.length) {
      return errors.every((error) => isApiError(error) && error.kind === 'not-connected') ? 'offline' : 'error'
    }
    if (errors.length > 0) return 'partial'
    if (statuses.every((status) => status === 'ready')) {
      const allEmpty = [indexView, watchlistView, positionsView, watchView, validationView].every(
        (view) => view.state === 'empty' || view.state === 'offline',
      )
      return allEmpty ? 'empty' : 'normal'
    }
    return 'loading'
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requests, indexView.state, watchlistView.state, positionsView.state, watchView.state, validationView.state])

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'
  const busy = requests.some((request) => request.status === 'loading')
  const offline = state === 'offline'

  const movers = useMemo(
    () =>
      [...watchRows]
        .filter((row) => row.change_pct !== null)
        .sort((a, b) => Math.abs(b.change_pct ?? 0) - Math.abs(a.change_pct ?? 0))
        .slice(0, 3),
    [watchRows],
  )

  // 各路信封的 payload 类型不同，只取溯源需要的公共字段
  const envelopes = useMemo<EnvelopeMeta[]>(() => {
    // 五份 payload 的形状不同（平面 / 信封），这里只按「可能带溯源键」读，缺键即 null
    const list = [indexView.data, watchlistView.data, positionsView.data, watchView.data, validationView.data] as Array<Partial<EnvelopeMeta>>
    return list.flatMap((item) =>
      item
        ? [{
            freshness: item.freshness ?? null,
            source_status: item.source_status ?? null,
            source_detail: item.source_detail ?? null,
            data_as_of: item.data_as_of ?? null,
            fetched_at: item.fetched_at ?? null,
          }]
        : [],
    )
  }, [indexView.data, watchlistView.data, positionsView.data, watchView.data, validationView.data])
  const degraded = envelopes.filter((envelope) => envelope.source_status !== null && envelope.source_status !== 'ok')
  const latestFetch = envelopes
    .map((envelope) => envelope.fetched_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .pop()

  const summary =
    state === 'loading'
      ? '读取中…'
      : state === 'offline'
        ? '本地服务未连接'
        : state === 'error'
          ? '取数失败'
          : state === 'empty'
            ? `${tradeDate} · 今日无数据`
            : `${degraded.length > 0 ? '部分数据降级' : '数据完整'} · 更新于 ${formatTime(latestFetch ?? null)}`

  const todos = useMemo<Todo[]>(() => {
    const list: Todo[] = []
    if (usable && conditions.length > 0 && !reconcile) {
      list.push({
        tone: 'warn',
        tag: '待对账',
        title: `${conditions.length} 条验证条件等待收盘对账`,
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
  }, [usable, conditions.length, reconcile, reviewChars, degraded])

  const failedLabels = useMemo(() => {
    const labels: string[] = []
    if (indexView.state === 'error') labels.push('大盘指数')
    if (watchlistView.state === 'error') labels.push('我的自选')
    if (positionsView.state === 'error') labels.push('我的持仓')
    if (watchView.state === 'error') labels.push('盯盘快照')
    if (validationView.state === 'error') labels.push('验证条件')
    return labels
  }, [indexView.state, watchlistView.state, positionsView.state, watchView.state, validationView.state])

  const header = (
    <PageHeader
      title="首页"
      subtitle={summary}
      badge={<DemoStateBadge state={forced} />}
      refresh={{
        onClick: reloadAll,
        label: '刷新全部',
        loading: busy && state !== 'loading',
        disabled: state === 'loading',
      }}
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-4">
        {header}
        {/* 首页已无 5 卡阵：骨架形状跟着真实版式走（§7 / §11.0） */}
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

  return (
    <div className="flex flex-col gap-4">
      {header}

      {offline && <OfflineBanner onRetry={reloadAll} />}
      {state === 'partial' && failedLabels.length > 0 && (
        <ErrorBlock
          variant="banner"
          title="部分数据源不可用"
          detail={`${failedLabels.join('、')} 取数失败；其余块照常展示，缺失处以 ${MISSING} 标注。`}
          onRetry={reloadAll}
          retryLabel="重试该段"
        />
      )}

      {state === 'empty' && (
        <EmptyState
          title={`${tradeDate} 没有可用数据`}
          description="五路（指数 / 自选 / 持仓 / 盯盘快照 / 验证条件）都是空的。到行情页用「拉取行情数据」同步当日数据，或先确认本机服务已启动。"
          action={
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Button variant="primary" onClick={() => navigate('/market')}>
                去行情页同步
              </Button>
              <Button variant="secondary" onClick={reloadAll} leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}>
                重新检查
              </Button>
              <Button variant="ghost" onClick={() => navigate('/settings?tab=peers')}>
                看数据源
              </Button>
            </div>
          }
        />
      )}

      {/* A/B/C/D 四块各自满宽纵向堆叠（§1.1：持仓六列下限 824px，并排必然叠字） */}
      <HomeIndexStrip view={indexView} />
      <HomeSearch />
      <HomeWatchlistCard view={watchlistView} />
      <HomePositionsCard view={positionsView} />

      {/* 只有 E/F 并排：7 / 5 */}
      <div className="grid gap-4 desk:grid-cols-12">
        <div className="flex flex-col gap-4 desk:col-span-7">
          {todos.length > 0 && (
            <Card>
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
        </div>

        <div className="desk:col-span-5">
          <SectionCard
            title="最近异动"
            subtitle={watchRows.length > 0 ? '按涨跌幅绝对值取前 3' : '快照不可用'}
            bodyClassName="p-4"
          >
            {movers.length === 0 ? (
              <EmptyState compact title="还没有异动信号" description="盯盘快照为空或已过期，去盯盘页取数。" />
            ) : (
              <ul className="flex flex-col gap-1">
                {movers.map((row) => {
                  const change = formatChangePct(row.change_pct)
                  return (
                    <li key={row.code} className="flex min-h-row items-center justify-between gap-2 rounded-xs px-1">
                      <span className="min-w-0 truncate text-body text-text" title={`${row.code} ${row.name ?? MISSING}`}>
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
      </div>

      <p className="text-caption leading-body text-text-muted">
        指标口径：指数与自选为行情快照，不是逐笔实时；持仓成本法移动加权平均（DEC-038），当日盈亏不含分红。
        {todos.length === 0 && ' 今日无待办。'}
      </p>
    </div>
  )
}
