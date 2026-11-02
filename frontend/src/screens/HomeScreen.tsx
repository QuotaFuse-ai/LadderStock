import { useMemo } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'

import {
  describeApiError,
  isApiError,
  type LadderEnvelope,
  type LadderStock,
  type SentimentResult,
} from '../api'
import { useAsync } from '../lib/useAsync'
import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import {
  MISSING,
  formatBoards,
  formatChangePct,
  formatInt,
  formatRatePct,
  formatTime,
  localDateIso,
} from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Button,
  Card,
  CardHeader,
  DataTable,
  EmptyState,
  ErrorBlock,
  FreshnessBadge,
  MetricCard,
  SectionCard,
  SignedNumber,
  SourceBadges,
  SourceStatusBadge,
  StatusDot,
  type Column,
  type SourceBadgeMeta,
} from '../components/ui'
import { useDensity } from '../lib/prefs'
import { useReviewDraft } from '../../stores/reviewDraft'
import { useValidationDraft } from '../../stores/validationDraft'
import { DEMO_TRADE_DATE } from './home/demo'
import { useHomeDemoEnabled, useHomeSource } from './home/useHomeSource'

/**
 * 首页（P-01 / DESIGN-SPEC §4.2）。
 *
 * 3 秒内回答「今天值不值得动手」：5 个指标卡 + 情绪温度条 + 待办 + 连板 Top 10。
 * 数据聚合 `/api/sentiment` `/api/ladder` `/api/watch/snapshot` `/api/validation`；
 * 任何一路缺失都显示 `—` + 原因，**不补 0、不编造**。
 * 五态演示统一走 `?state=empty|loading|error|partial|offline`（§8）；
 * 演示数据走 `home/useHomeSource`，与真实通道互斥，**真实请求失败不回落到演示数据**（§5.6）。
 */

type Envelope = LadderEnvelope<unknown>

/** 溯源徽标只需要 envelope 的公共字段（§5.5 单一实现），四路 payload 类型不同也能放在同一数组里。 */
type EnvelopeMeta = SourceBadgeMeta & Pick<Envelope, 'fetched_at'>

/** 五段区间带的透明度分层（同一 `chart.band` 单色，不换色相 —— §6 CH-01）。 */
const BAND_STEPS = [
  { label: '冰点', opacity: 0.6 },
  { label: '低迷', opacity: 0.75 },
  { label: '中性', opacity: 1 },
  { label: '活跃', opacity: 0.75 },
  { label: '过热', opacity: 0.6 },
]

/** 顶部 8px 情绪温度条：五段单色透明度分层 + 当前位置指针（§4.2 L0）。 */
function TemperatureBar({ value, label }: { value: number | null; label: string }) {
  const pct = value === null ? null : Math.min(100, Math.max(0, value))
  return (
    <div className="flex flex-col gap-1">
      <div
        role="img"
        aria-label={value === null ? `情绪温度缺失（${label}）` : `情绪温度 ${value}，0 到 100，50 为中性`}
        className="relative h-2 w-full overflow-hidden rounded-pill border border-border bg-surface-2"
      >
        <div className="flex h-full w-full" aria-hidden="true">
          {BAND_STEPS.map((band) => (
            <span key={band.label} className="h-full flex-1 bg-chart-band" style={{ opacity: band.opacity }} />
          ))}
        </div>
        {pct !== null && (
          <span
            aria-hidden="true"
            className="absolute inset-y-0 w-half -translate-x-1/2 rounded-pill bg-text transition-all duration-fast ease-ldst"
            style={{ left: `${pct}%` }}
          />
        )}
      </div>
      <div className="flex items-center justify-between text-caption text-text-muted">
        <span>0 冰点</span>
        <span>50 中性</span>
        <span>100 过热</span>
      </div>
    </div>
  )
}

/** HHMMSS 字符串 / 时间戳 → `09:30:00`；拿不到就 `—`（不猜）。 */
function formatSeal(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return MISSING
  const text = String(value)
  if (/^\d{6}$/.test(text)) return `${text.slice(0, 2)}:${text.slice(2, 4)}:${text.slice(4, 6)}`
  if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(text)) return text
  if (/^\d{4}-\d{2}-\d{2}T/.test(text)) return formatTime(text)
  return MISSING
}

interface LadderRow {
  code: string
  name: string
  boards: number
  firstSeal: string | number | null
}

const LADDER_COLUMNS: Array<Column<LadderRow>> = [
  {
    key: 'code',
    header: '代码',
    width: '88px',
    render: (row) => <span className="ldst-num text-text-secondary">{row.code || MISSING}</span>,
  },
  { key: 'name', header: '名称', truncate: true, render: (row) => <span className="font-medium">{row.name || MISSING}</span> },
  {
    key: 'boards',
    header: '连板',
    width: '88px',
    numeric: true,
    sort: (a, b) => a.boards - b.boards,
    render: (row) => <span className="font-semibold text-up">{formatBoards(row.boards)}</span>,
  },
  {
    key: 'seal',
    header: '首封',
    width: '144px',
    numeric: true,
    render: (row) => <span className="text-text-secondary">{formatSeal(row.firstSeal)}</span>,
  },
]

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
  // 行高档位只由用户的密度偏好决定（§11.18「密度只改行高」）：屏内不写死，
  // 否则「设置 → 表格密度」在这张屏上会是静默失效的。
  const density = useDensity()
  const [params] = useSearchParams()
  const demoEnabled = useHomeDemoEnabled()
  // 演示源讲的是 `limitup/demo.ts` 同一个交易日的 facts：请求日期跟随它，
  // 否则「2026-11-01 的演示涨停池」这种自相矛盾的标题会写在首屏上。`?date=` 优先。
  const tradeDate = params.get('date') || (demoEnabled ? DEMO_TRADE_DATE : localDateIso())
  const { source } = useHomeSource(tradeDate)

  const sentiment = useAsync((signal) => source.sentiment(tradeDate, signal), [source, tradeDate])
  const ladder = useAsync((signal) => source.ladder(tradeDate, signal), [source, tradeDate])
  const watch = useAsync((signal) => source.watchSnapshot(signal), [source, tradeDate])
  const validation = useAsync((signal) => source.validation(tradeDate, signal), [source, tradeDate])

  const requests = [sentiment, ladder, watch, validation]
  const reloadAll = () => requests.forEach((request) => request.reload())

  const sent: SentimentResult | null = sentiment.data?.payload.sentiment ?? null
  const ladderPayload = ladder.data?.payload.ladder ?? null
  const watchRows = watch.data?.payload.rows ?? []
  const conditions = validation.data?.payload.conditions ?? []
  const reconcile = validation.data?.payload.reconcile ?? null

  const draftConditions = useValidationDraft((state) => state.conditions)
  const reviewSections = useReviewDraft((state) => state.sections)
  const reviewChars = useMemo(
    () => Object.values(reviewSections).reduce((total, text) => total + text.trim().length, 0),
    [reviewSections],
  )

  const isEmpty = !sent?.available && !ladderPayload?.available && watchRows.length === 0 && conditions.length === 0
  const derived = useMemo<PageState>(() => {
    const statuses = requests.map((request) => request.status)
    if (statuses.every((status) => status === 'loading')) return 'loading'
    const errors = requests.map((request) => request.error).filter(Boolean)
    if (errors.length === statuses.length) {
      return errors.every((error) => isApiError(error) && error.kind === 'not-connected') ? 'offline' : 'error'
    }
    if (errors.length > 0) return 'partial'
    if (statuses.every((status) => status === 'ready')) return isEmpty ? 'empty' : 'normal'
    return 'loading'
  }, [sentiment.status, ladder.status, watch.status, validation.status, isEmpty]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'
  const busy = requests.some((request) => request.status === 'loading')

  // 五态下数据一律按「不可用」处理，保证骨架之外的强制态不依赖后端
  const viewSent = usable ? sent : null
  const viewLadder = usable ? ladderPayload : null
  const viewWatch = usable ? watchRows : []
  const viewConditions = usable ? conditions : []

  const temperature = viewSent?.temperature ?? null
  const maxBoard = viewSent?.max_board ?? viewLadder?.height ?? null
  const offline = state === 'offline'

  const ladderRows = useMemo<LadderRow[]>(() => {
    if (!viewLadder?.ladder) return []
    return viewLadder.ladder
      .flatMap((level) => level.stocks.map((stock: LadderStock) => ({
        code: stock.code,
        name: stock.name,
        boards: level.boards,
        firstSeal: stock.first_seal_time,
      })))
      .sort((a, b) => b.boards - a.boards)
      .slice(0, 10)
  }, [viewLadder])

  const confirmedCount = useMemo(() => {
    const codes = new Set(viewConditions.map((condition) => condition.code).filter(Boolean))
    return draftConditions.filter((condition) => condition.confirmed && (codes.size === 0 || codes.has(condition.code))).length
  }, [draftConditions, viewConditions])

  const movers = useMemo(
    () =>
      [...viewWatch]
        .filter((row) => row.change_pct !== null)
        .sort((a, b) => Math.abs(b.change_pct ?? 0) - Math.abs(a.change_pct ?? 0))
        .slice(0, 3),
    [viewWatch],
  )

  // 四路 envelope 的 payload 类型各不相同，只取溯源需要的公共三条字段
  const envelopes = useMemo<EnvelopeMeta[]>(() => {
    const list = [sentiment.data, ladder.data, watch.data, validation.data]
    return list.flatMap((item) =>
      item
        ? [{
            freshness: item.freshness,
            source_status: item.source_status,
            source_detail: item.source_detail,
            data_as_of: item.data_as_of,
            fetched_at: item.fetched_at,
          }]
        : [],
    )
  }, [sentiment.data, ladder.data, watch.data, validation.data])
  const degraded = envelopes.filter((envelope) => envelope.source_status !== 'ok')
  const latestFetch = envelopes
    .map((envelope) => envelope.fetched_at)
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
    if (sentiment.status === 'error') labels.push('情绪温度')
    if (ladder.status === 'error') labels.push('连板天梯')
    if (watch.status === 'error') labels.push('盯盘快照')
    if (validation.status === 'error') labels.push('验证条件')
    return labels
  }, [sentiment.status, ladder.status, watch.status, validation.status])

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
        <ScreenSkeleton metrics={5} rows={8} />
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

  if (state === 'empty') {
    // §11.5：空态是「今日无数据 + 引导」，不是把整屏渲染成一组「—」
    return (
      <div className="flex flex-col gap-4">
        {header}
        <EmptyState
          title={`${tradeDate} 没有可用数据`}
          description="情绪温度、连板天梯、盯盘快照、验证条件四路都是空的。先启动本机服务，再同步当日涨停池。"
          action={
            <div className="flex flex-col items-center gap-2">
              <code className="ldst-num rounded border border-border bg-surface-2 px-2 py-1 text-caption text-text-secondary">
                ldst sync --date {tradeDate}
              </code>
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button variant="secondary" onClick={reloadAll} leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}>
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

      {state === 'offline' && <OfflineBanner onRetry={reloadAll} />}
      {state === 'partial' && (
        <ErrorBlock
          variant="banner"
          title="部分数据源不可用"
          detail={`${failedLabels.join('、')} 取数失败；其余指标照常展示，缺失处以 — 标注。`}
          onRetry={reloadAll}
          retryLabel="重试该段"
        />
      )}

      <TemperatureBar
        value={temperature}
        label={offline ? '本地服务未连接' : viewSent?.reason ?? '该交易日无情绪数据'}
      />

      <div className="grid grid-cols-2 gap-4 rail:grid-cols-3 desk:grid-cols-5">
        <MetricCard
          label="情绪温度"
          badge={<SourceBadges envelope={sentiment.data} />}
          value={formatInt(temperature)}
          note={
            temperature !== null ? (
              <>
                50 为中性 · 涨停 <span className="ldst-num">{formatInt(viewSent?.limit_up_count)}</span> 家
              </>
            ) : (
              (viewSent?.reason ?? (offline ? '本地服务未连接' : '暂无情绪数据'))
            )
          }
        />
        <MetricCard
          label="最高连板"
          badge={<SourceBadges envelope={sentiment.data ?? ladder.data} />}
          value={formatBoards(maxBoard)}
          note={
            maxBoard !== null ? (
              <>
                断层 <span className="ldst-num">{formatInt(viewLadder?.gap_levels.length ?? viewSent?.gap_levels.length)}</span>{' '}
                档
              </>
            ) : (
              (viewLadder?.reason ?? viewSent?.reason ?? (offline ? '本地服务未连接' : '暂无连板数据'))
            )
          }
        />
        <MetricCard
          label="主线题材"
          badge={
            <span className="inline-flex items-center gap-1">
              <FreshnessBadge freshness={null} asOf={tradeDate} sourceDetail="本屏没有题材 / 板块数据源" />
              <SourceStatusBadge status="missing" detail="本屏没有题材 / 板块数据源" />
            </span>
          }
          value={MISSING}
          note="本地无板块/题材数据（先运行 ldst sync）"
        />
        <MetricCard
          label="盯盘异动"
          badge={<SourceBadges envelope={watch.data} />}
          value={viewWatch.length > 0 ? <>{formatInt(viewWatch.length)} 条</> : MISSING}
          note={
            viewWatch.length > 0 ? (
              <>
                快照 <span className="ldst-num">{formatTime(watch.data?.fetched_at)}</span>
              </>
            ) : offline ? (
              '本地服务未连接'
            ) : (
              '本机没有可用快照'
            )
          }
        />
        <MetricCard
          label="待验证"
          badge={<SourceBadges envelope={validation.data} />}
          value={viewConditions.length > 0 ? <>{formatInt(viewConditions.length)} 项</> : MISSING}
          note={
            viewConditions.length > 0 ? (
              <>
                <span className="ldst-num">{formatInt(confirmedCount)}</span> 项已确认
              </>
            ) : offline ? (
              '本地服务未连接'
            ) : (
              '还没有验证条件'
            )
          }
        />
      </div>

      <div className="grid gap-4 desk:grid-cols-12">
        <SectionCard
          className="desk:col-span-8"
          title="今日连板梯队 Top 10"
          subtitle={
            viewLadder?.available ? (
              <>
                <span className="ldst-num">{tradeDate}</span> · 高度{' '}
                <span className="ldst-num">{formatBoards(viewLadder.height)}</span> · 共{' '}
                <span className="ldst-num">{formatInt(viewLadder.count)}</span> 只
              </>
            ) : (
              <>
                交易日 <span className="ldst-num">{tradeDate}</span>
              </>
            )
          }
          bodyClassName="p-0"
        >
          <DataTable<LadderRow>
            columns={LADDER_COLUMNS}
            rows={ladderRows}
            rowKey={(row) => `${row.code}-${row.boards}`}
            caption={`连板梯队（${tradeDate}），按板数降序取前 10`}
            density={density}
            maxHeight="420px"
            empty={
              <EmptyState
                compact
                title="还没有连板数据"
                description={
                  viewLadder?.reason ??
                  (offline ? '本地服务未连接，先运行 ldst run。' : '该交易日没有涨停池数据，先运行 ldst sync 再回来。')
                }
              />
            }
          />
        </SectionCard>

        {/* 首屏层级（§11.5）：待办与主面板同高排在右栏，梯队表不再被整块推到折叠线以下 */}
        <div className="flex flex-col gap-4 desk:col-span-4">
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

          <SectionCard
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

          <SectionCard title="题材强度 Top 5" subtitle="本地无题材数据集" bodyClassName="p-4">
            <EmptyState
              compact
              title="还没有题材数据"
              description="情绪温度与连板天梯可用；题材轮动需要先接入板块/概念行情（ldst sync）。"
            />
          </SectionCard>
        </div>
      </div>

      <p className="text-caption leading-body text-text-muted">
        指标口径：情绪温度 0–100（50 中性）；晋级率{' '}
        <span className="ldst-num">{formatRatePct(viewSent?.promotion_rate).text}</span>；一线源见各卡右上徽标。
      </p>
    </div>
  )
}
