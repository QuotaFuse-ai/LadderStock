import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, RefreshCw } from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  isEndpointMissing,
  type LadderEnvelope,
  type LadderLevel,
  type LadderResult,
  type MarketHistoryPoint,
  type MarketHistoryPayload,
  type MarketThemePayload,
  type MarketThemeRow,
  type SentimentResult,
} from '../api'
import { useAsync } from '../lib/useAsync'
import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import {
  MISSING,
  formatInt,
  formatOneDecimal,
  formatRatePct,
  formatSignedInt,
  formatTime,
  isFiniteNumber,
  localDateIso,
  shortDate,
} from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import { BarChart, LineChart, type LinePoint } from '../charts/Chart'
import {
  Badge,
  Banner,
  Button,
  DataTable,
  EmptyState,
  ErrorBlock,
  Field,
  HBarList,
  Input,
  MetricCard,
  SectionCard,
  Segmented,
  type Column,
} from '../components/ui'

/**
 * 市场（P-02 / DESIGN-SPEC §4.3「分段，不是三个子页」）。
 *
 * 三段共用**一个交易日选择器**：情绪温度（CH-01）/ 连板梯队（CH-02）/ 题材轮动（CH-03），
 * 用 `?tab=sentiment|ladder|theme` 深链，切换分段只换内容、不动交易日。
 *
 * 数据口径（QUOT-198 契约）：
 * - CH-01 ← `/api/sentiment`（当日单点）+ `/api/market/history`（近 N 日序列，**后端缺口 Q172-8**）；
 * - CH-02 ← `/api/ladder`（已上线）；
 * - CH-03 ← `/api/market/theme`（**后端缺口 Q172-8**）。
 *
 * 三条硬规则：没有数据就显示「数据未同步」并给下一步（`ldst sync --date …`），
 * **不补 0、不画假线、不出现 NaN / undefined**；404 只代表「该段暂未接入」，不是页面故障。
 */

type MarketTab = 'sentiment' | 'ladder' | 'theme'

const TAB_OPTIONS: ReadonlyArray<{ value: MarketTab; label: string }> = [
  { value: 'sentiment', label: '情绪温度' },
  { value: 'ladder', label: '连板梯队' },
  { value: 'theme', label: '题材轮动' },
]

const TAB_LABEL: Record<MarketTab, string> = {
  sentiment: '情绪温度',
  ladder: '连板梯队',
  theme: '题材轮动',
}

/** 折线窗口：与 `/api/market/history?window=` 保持一致 */
const HISTORY_WINDOW = 20
/** CH-03 只画前 8 条（其余进表格，§6.3） */
const THEME_TOP = 8
/** §6.1：市场页折线高度 320 */
const CHART_HEIGHT = 320

/** `?state=error` 演示态：没有真实 error 对象，用固定文案，禁止把 undefined 拼进去 */
const DEMO_ERROR = {
  title: '演示态：取数失败',
  detail: '这是 §8 的强制错误态。真实失败时这里会给出接口地址与 HTTP 状态码，并保留「重试」。',
  code: null,
} as const

const FRESHNESS_LABEL: Record<LadderEnvelope<unknown>['freshness'], string> = {
  realtime: '实时',
  cached: '缓存',
  archive: '归档',
}

type EnvelopeMeta = Pick<
  LadderEnvelope<unknown>,
  'freshness' | 'source_status' | 'source_detail' | 'data_as_of' | 'fetched_at'
>

/** 指标卡 / 卡片右上角溯源徽标：freshness · source_status，hover 出明细。 */
function SourceBadge({ envelope }: { envelope: EnvelopeMeta | null }) {
  if (!envelope) return null
  const tone = envelope.source_status === 'ok' ? 'ok' : envelope.source_status === 'partial' ? 'warn' : 'danger'
  const title = [
    `来源：${envelope.source_detail}`,
    `数据时间：${envelope.data_as_of ?? '未知'}`,
    `取数：${formatTime(envelope.fetched_at)}`,
  ].join('\n')
  return (
    <Badge tone={tone} title={title}>
      {FRESHNESS_LABEL[envelope.freshness]}
    </Badge>
  )
}

/** 卡片底部的一行来源与口径（§4.2 L2）。 */
function SourceLine({ envelope, extra }: { envelope: EnvelopeMeta | null; extra: string }) {
  if (!envelope) {
    return <p className="text-caption leading-body text-text-muted">{extra}</p>
  }
  return (
    <p className="text-caption leading-body text-text-muted">
      来源 {envelope.source_detail} · 数据时间 {envelope.data_as_of ?? '未知'} · 取数 {formatTime(envelope.fetched_at)} · {extra}
    </p>
  )
}

/**
 * 统一的「数据未同步」空态：结论 → 原因 → 下一步命令 → 重新检查。
 * 三段共用，保证五态演示与真实缺数据时长一个样。
 */
function NotSynced({
  tradeDate,
  reason,
  detail,
  onRetry,
}: {
  tradeDate: string
  reason: string | null
  detail?: string
  onRetry: () => void
}) {
  return (
    <EmptyState
      icon={<Database aria-hidden="true" />}
      title="数据未同步"
      description={
        <>
          {reason ?? '这一段需要的数据还没有同步到本机。'}
          {detail ? ` ${detail}` : ''}
        </>
      }
      action={
        <div className="flex flex-col items-center gap-2">
          <code className="ldst-num rounded border border-border bg-surface-2 px-2 py-1 text-caption text-text-secondary">
            ldst sync --date {tradeDate}
          </code>
          <Button variant="secondary" onClick={onRetry}>
            重新检查
          </Button>
        </div>
      }
    />
  )
}

const LADDER_COLUMNS: Array<Column<LadderLevel>> = [
  {
    key: 'boards',
    header: '板数',
    width: '88px',
    sort: (a, b) => a.boards - b.boards,
    render: (row) => <span className="ldst-num text-text">{formatInt(row.boards)}</span>,
  },
  {
    key: 'count',
    header: '家数',
    align: 'right',
    width: '80px',
    sort: (a, b) => a.count - b.count,
    render: (row) => <span className="ldst-num text-text">{formatInt(row.count)}</span>,
  },
  {
    key: 'stocks',
    header: '代表股',
    render: (row) => (
      <span className="block truncate text-text-secondary" title={row.stocks.map((stock) => stock.name).join('、')}>
        {row.stocks.length > 0 ? row.stocks.slice(0, 3).map((stock) => stock.name).join('、') : MISSING}
      </span>
    ),
  },
  {
    key: 'promotion',
    header: '晋级率',
    align: 'right',
    width: '96px',
    // 后端 `/api/ladder` 只给按板位的家数，没有「该板位明日晋级」的统计口径，一律 `—` 并说明原因
    render: () => (
      <span className="ldst-num text-text-muted" title="后端未提供按板位的晋级率，不臆测">
        {MISSING}
      </span>
    ),
  },
]

const HISTORY_COLUMNS: Array<Column<MarketHistoryPoint>> = [
  {
    key: 'date',
    header: '交易日',
    width: '112px',
    render: (row) => <span className="ldst-num text-text-secondary">{row.date}</span>,
  },
  {
    key: 'temperature',
    header: '情绪温度',
    align: 'right',
    width: '96px',
    render: (row) => (
      <span className="ldst-num text-text">
        {isFiniteNumber(row.temperature) ? formatInt(row.temperature) : MISSING}
      </span>
    ),
  },
]

function ThemeColumns(): Array<Column<MarketThemeRow>> {
  return [
    {
      key: 'theme',
      header: '题材',
      render: (row) => <span className="truncate font-medium text-text">{row.theme}</span>,
    },
    {
      key: 'strength',
      header: '强度',
      align: 'right',
      width: '88px',
      sort: (a, b) => a.strength - b.strength,
      render: (row) => (
        <span className="ldst-num text-text">
          {isFiniteNumber(row.strength) ? formatOneDecimal(row.strength) : MISSING}
        </span>
      ),
    },
    {
      key: 'limitUp',
      header: '涨停家数',
      align: 'right',
      width: '96px',
      render: (row) => (
        <span className="ldst-num text-text-secondary">
          {row.limit_up_count === null ? MISSING : formatInt(row.limit_up_count)}
        </span>
      ),
    },
    {
      key: 'leader',
      header: '龙头',
      width: '128px',
      render: (row) => <span className="truncate text-text-secondary">{row.leader ?? MISSING}</span>,
    },
    {
      key: 'delta',
      header: '较前日',
      align: 'right',
      width: '96px',
      render: (row) => {
        const delta = formatSignedInt(row.delta)
        return (
          <span className={delta.missing ? 'ldst-num text-text-muted' : 'ldst-num text-text-secondary'}>
            {delta.text}
          </span>
        )
      },
    },
  ]
}

export default function MarketScreen() {
  const forced = useForcedState()
  const [params, setParams] = useSearchParams()

  const tradeDate = params.get('date') ?? localDateIso()
  const tabParam = params.get('tab')
  const tab: MarketTab = tabParam === 'ladder' || tabParam === 'theme' ? tabParam : 'sentiment'

  // 分段切换要能回退 → push 一条历史；改日期用 replace，避免每次输入都堆历史
  const setTab = (next: MarketTab) => {
    const paramsNext = new URLSearchParams(params)
    paramsNext.set('tab', next)
    setParams(paramsNext)
  }
  const setDate = (next: string) => {
    const paramsNext = new URLSearchParams(params)
    if (next) paramsNext.set('date', next)
    else paramsNext.delete('date')
    setParams(paramsNext, { replace: true })
  }

  const sentiment = useAsync((signal) => api.sentiment(tradeDate, signal), [tradeDate])
  const ladder = useAsync((signal) => api.ladder(tradeDate, signal), [tradeDate])
  // 缺口端点：404 视为「这一段没有数据源」，把错误吞成 null，不让整屏变红
  const history = useAsync(
    (signal) =>
      api.marketHistory(tradeDate, HISTORY_WINDOW, signal).catch((error: unknown) => {
        if (isEndpointMissing(error)) return null
        throw error
      }),
    [tradeDate],
  )
  const theme = useAsync(
    (signal) =>
      api.marketTheme(tradeDate, signal).catch((error: unknown) => {
        if (isEndpointMissing(error)) return null
        throw error
      }),
    [tradeDate],
  )

  const requests = [sentiment, ladder, history, theme]
  const reloadAll = () => requests.forEach((request) => request.reload())
  const busy = requests.some((request) => request.status === 'loading')

  const sent: SentimentResult | null = sentiment.data?.payload.sentiment ?? null
  const ladderResult: LadderResult | null = ladder.data?.payload.ladder ?? null
  const historyPayload: MarketHistoryPayload | null = history.data?.payload ?? null
  const themePayload: MarketThemePayload | null = theme.data?.payload ?? null

  const derived = useMemo<PageState>(() => {
    const statuses = requests.map((request) => request.status)
    if (statuses.every((status) => status === 'loading')) return 'loading'
    const errors = requests.map((request) => request.error).filter(Boolean)
    if (errors.length === statuses.length) {
      return errors.every((error) => isApiError(error) && error.kind === 'not-connected') ? 'offline' : 'error'
    }
    if (errors.length > 0) return 'partial'
    if (statuses.some((status) => status !== 'ready')) return 'loading'
    const anyData =
      (sent?.available ?? false) ||
      (ladderResult?.ladder.length ?? 0) > 0 ||
      (historyPayload?.available ?? false) ||
      (themePayload?.available ?? false)
    return anyData ? 'normal' : 'empty'
  }, [sentiment.status, ladder.status, history.status, theme.status, sent, ladderResult, historyPayload, themePayload]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'
  // 强制 `?state=loading` 仍然渲染空态卡片（验收要求五态下三段都是「数据未同步」），只有真实加载才走骨架
  const showSkeleton = derived === 'loading' && forced === null

  // ---- CH-01 序列 ----
  const historyRows = useMemo<MarketHistoryPoint[]>(() => {
    if (!usable || !historyPayload || !historyPayload.available) return []
    const points = Array.isArray(historyPayload.points) ? historyPayload.points : []
    return points.filter((point) => Boolean(point) && typeof point.date === 'string').slice(-HISTORY_WINDOW)
  }, [usable, historyPayload])

  const currentTemperature = usable ? (sent?.temperature ?? null) : null
  const hasHistory = historyRows.length > 0
  const linePoints = useMemo<LinePoint[]>(() => {
    if (hasHistory) {
      return historyRows.map((point) => ({
        label: shortDate(point.date),
        value: isFiniteNumber(point.temperature) ? point.temperature : null,
      }))
    }
    if (currentTemperature === null) return []
    return [{ label: shortDate(tradeDate), value: currentTemperature }]
  }, [hasHistory, historyRows, currentTemperature, tradeDate])

  // ---- CH-02 梯队：按板数升序（1 板 → 最高板） ----
  const levels = useMemo<LadderLevel[]>(() => {
    if (!usable || !ladderResult) return []
    const list = Array.isArray(ladderResult.ladder) ? ladderResult.ladder : []
    return [...list]
      .filter((level) => level && isFiniteNumber(level.boards) && isFiniteNumber(level.count))
      .sort((a, b) => a.boards - b.boards)
  }, [usable, ladderResult])

  // ---- CH-03 题材：强度降序，前 8 进横条 ----
  const themes = useMemo<MarketThemeRow[]>(() => {
    if (!usable || !themePayload || !themePayload.available) return []
    const list = Array.isArray(themePayload.themes) ? themePayload.themes : []
    return [...list]
      .filter((row) => row && typeof row.theme === 'string' && isFiniteNumber(row.strength))
      .sort((a, b) => b.strength - a.strength)
  }, [usable, themePayload])
  const topThemes = themes.slice(0, THEME_TOP)

  const themeColumns = useMemo(() => ThemeColumns(), [])

  // 三段的「有没有数据 + 为什么没有」，右栏与当前段共用同一份结论
  const segments = useMemo(
    () => [
      {
        key: 'sentiment' as MarketTab,
        ready: linePoints.length > 0,
        label: hasHistory ? `近 ${formatInt(historyRows.length)} 日序列` : linePoints.length > 0 ? '仅当日单点' : null,
        reason:
          linePoints.length > 0
            ? null
            : usable
              ? (sent?.reason ?? '情绪指标未生成')
              : forced === 'offline'
                ? '本地服务未连接'
                : '数据未同步',
      },
      {
        key: 'ladder' as MarketTab,
        ready: levels.length > 0,
        label: levels.length > 0 ? `${formatInt(levels.length)} 档` : null,
        reason:
          levels.length > 0
            ? null
            : usable
              ? (ladderResult?.reason ?? '涨停池没有可解析的连板数据')
              : forced === 'offline'
                ? '本地服务未连接'
                : '数据未同步',
      },
      {
        key: 'theme' as MarketTab,
        ready: themes.length > 0,
        label: themes.length > 0 ? `前 ${formatInt(Math.min(themes.length, THEME_TOP))} 条` : null,
        reason:
          themes.length > 0
            ? null
            : !usable
              ? forced === 'offline'
                ? '本地服务未连接'
                : '数据未同步'
              : themePayload === null
                ? '后端暂无 /api/market/theme（Q172-8 未上线）'
                : (themePayload.reason ?? '题材轮动数据未同步'),
      },
    ],
    [hasHistory, historyRows.length, linePoints.length, levels.length, themes.length, usable, sent, ladderResult, themePayload, forced],
  )

  const gapLevels = usable ? (sent?.gap_levels ?? null) : null
  const firstError = requests.map((request) => request.error).find(Boolean)
  const summary = useMemo(() => {
    if (state === 'offline') return `${tradeDate} · 本地服务未连接`
    if (state === 'error') return `${tradeDate} · 取数失败`
    if (state === 'empty') return `${tradeDate} · 尚未同步`
    if (state === 'loading') return `${tradeDate} · 取数中`
    return `${tradeDate} · ${TAB_LABEL[tab]}`
  }, [state, tradeDate, tab])

  const header = (
    <PageHeader
      title="市场"
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
          刷新
        </Button>
      }
    />
  )

  if (showSkeleton) {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={3} rows={6} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'offline' && <OfflineBanner onRetry={reloadAll} />}
      {state === 'error' && (
        // 真实失败用真实原因；`?state=error` 演示态没有 error 对象，绝不能把 undefined 拼进文案
        <ErrorBlock variant="banner" {...(firstError ? describeApiError(firstError) : DEMO_ERROR)} onRetry={reloadAll} />
      )}
      {state === 'partial' && (
        <ErrorBlock
          variant="banner"
          title="部分数据源不可用"
          detail={`${requests.filter((request) => request.status === 'error').length} 个数据源取数失败；能取到的分段照常展示，缺失分段以「数据未同步」标注并给出同步入口。`}
          onRetry={reloadAll}
          retryLabel="重试"
        />
      )}
      {state === 'empty' && tab !== 'theme' && (
        <Banner tone="info" title={`${tradeDate} 尚未同步行情数据`}>
          先执行 <code className="ldst-num">ldst sync --date {tradeDate}</code> 生成涨停池，再点「刷新」；本页不会用 0 或假数据填空。
        </Banner>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <Field label="交易日" htmlFor="market-date" className="w-44">
          <Input
            id="market-date"
            type="date"
            value={tradeDate}
            onChange={(event) => setDate(event.target.value)}
          />
        </Field>
        <Segmented
          ariaLabel="市场分段"
          options={TAB_OPTIONS}
          value={tab}
          onChange={setTab}
        />
        <div className="grow" />
        <span className="text-caption text-text-muted">三段共用同一个交易日</span>
      </div>

      <div className="grid grid-cols-1 gap-4 desk:grid-cols-12">
        <div className="flex flex-col gap-4 desk:col-span-8">
          {tab === 'sentiment' && (
            <>
              <div className="grid grid-cols-1 gap-3 min-[640px]:grid-cols-3">
                <MetricCard
                  label="情绪温度"
                  hero
                  badge={<SourceBadge envelope={usable ? sentiment.data : null} />}
                  value={currentTemperature === null ? MISSING : formatInt(currentTemperature)}
                  note={
                    currentTemperature === null
                      ? (usable ? (sent?.reason ?? '未生成') : '数据未同步')
                      : `50 为中性 · 涨停 ${formatInt(sent?.limit_up_count)} 家`
                  }
                />
                <MetricCard
                  label="晋级率"
                  badge={<SourceBadge envelope={usable ? sentiment.data : null} />}
                  value={formatRatePct(usable ? sent?.promotion_rate : null).text}
                  note={
                    usable && isFiniteNumber(sent?.promotion_rate)
                      ? `昨日涨停 ${formatInt(sent?.yesterday_limit_up_count)} 家`
                      : '数据未同步'
                  }
                />
                <MetricCard
                  label="梯队断层"
                  badge={<SourceBadge envelope={usable ? sentiment.data : null} />}
                  value={gapLevels === null ? MISSING : formatInt(gapLevels.length)}
                  unit={gapLevels === null ? undefined : '档'}
                  note={
                    gapLevels === null
                      ? '数据未同步'
                      : `最高 ${formatInt(sent?.max_board)} 板 · ${sent?.gap ? '存在断层' : '无断层'}`
                  }
                />
              </div>

              <SectionCard
                title="情绪温度（CH-01）"
                subtitle="0–100，50 为中性；灰色区间带为 40–60"
                tone={linePoints.length > 0 ? 'ok' : 'neutral'}
                actions={
                  <>
                    <SourceBadge envelope={usable ? (history.data ?? sentiment.data) : null} />
                    <Badge tone={hasHistory ? 'info' : 'warn'}>{hasHistory ? `近 ${formatInt(historyRows.length)} 日` : '单点'}</Badge>
                  </>
                }
              >
                {linePoints.length === 0 ? (
                  <NotSynced
                    tradeDate={tradeDate}
                    reason={usable ? (sent?.reason ?? '情绪指标未生成') : '数据未同步'}
                    detail="情绪温度需要当日涨停池；缺失时不画线、不补 0。"
                    onRetry={reloadAll}
                  />
                ) : (
                  <div className="flex flex-col gap-3">
                    {!hasHistory && (
                      <Banner tone="info" title="近 20 日序列未同步">
                        后端暂无 <code className="ldst-num">/api/market/history</code>（Q172-8 未上线），先展示当日单点；序列上线后自动改画折线。
                      </Banner>
                    )}
                    <LineChart
                      points={linePoints}
                      height={CHART_HEIGHT}
                      band={{ lower: 40, upper: 60 }}
                      threshold={{ value: 50, label: '中性 50' }}
                      ariaLabel={`情绪温度走势，${linePoints.length} 个数据点，50 为中性线`}
                    />
                    <DataTable<MarketHistoryPoint>
                      columns={HISTORY_COLUMNS}
                      rows={hasHistory ? historyRows : []}
                      rowKey={(row) => row.date}
                      caption={
                        hasHistory
                          ? `情绪温度近 ${historyRows.length} 日序列（按日期升序）`
                          : '情绪温度当日单点（历史序列未同步）'
                      }
                      density="compact"
                      maxHeight="240px"
                      empty={<p className="text-caption text-text-muted">历史序列未同步，只有当日单点，故不出表。</p>}
                    />
                    <SourceLine envelope={history.data ?? sentiment.data} extra="情绪温度 = 涨停/炸板/晋级等程序指标合成，非模型生成" />
                  </div>
                )}
              </SectionCard>
            </>
          )}

          {tab === 'ladder' && (
            <SectionCard
              title="连板梯队（CH-02）"
              subtitle="按板数升序：1 板 → 最高板；柱高为家数"
              tone={levels.length > 0 ? 'ok' : 'neutral'}
              actions={
                <>
                  <SourceBadge envelope={usable ? ladder.data : null} />
                  {levels.length > 0 && <Badge tone="info">最高 {formatInt(ladderResult?.height)} 板</Badge>}
                </>
              }
            >
              {levels.length === 0 ? (
                <NotSynced
                  tradeDate={tradeDate}
                  reason={usable ? (ladderResult?.reason ?? '涨停池没有可解析的连板数据') : '数据未同步'}
                  detail="梯队来自当日涨停池，缺失时不画柱、不补 0。"
                  onRetry={reloadAll}
                />
              ) : (
                <div className="flex flex-col gap-3">
                  <BarChart
                    points={levels.map((level) => ({ label: `${formatInt(level.boards)} 板`, value: level.count }))}
                    height={CHART_HEIGHT}
                    ariaLabel={`连板梯队柱状图，${levels.length} 个板位，最高 ${formatInt(ladderResult?.height)} 板`}
                  />
                  <DataTable<LadderLevel>
                    columns={LADDER_COLUMNS}
                    rows={levels}
                    rowKey={(row) => String(row.boards)}
                    caption={`连板梯队明细（${tradeDate}），按板数升序；晋级率口径后端未提供，显示 ${MISSING}`}
                    density="compact"
                    initialSort={{ key: 'boards', dir: 'asc' }}
                  />
                  <SourceLine envelope={ladder.data} extra="板数由涨停池逐日累计；未知板位单列在后端未知项" />
                </div>
              )}
            </SectionCard>
          )}

          {tab === 'theme' && (
            <SectionCard
              title="题材轮动（CH-03）"
              subtitle={`强度降序，横条只画前 ${THEME_TOP} 条，其余进表格`}
              tone={themes.length > 0 ? 'ok' : 'neutral'}
              actions={<SourceBadge envelope={usable ? theme.data : null} />}
            >
              {themes.length === 0 ? (
                <NotSynced
                  tradeDate={tradeDate}
                  reason={
                    usable
                      ? themePayload === null
                        ? '后端暂无 /api/market/theme（Q172-8 未上线），题材强度暂无可信来源'
                        : (themePayload.reason ?? '题材轮动数据未同步')
                      : '数据未同步'
                  }
                  detail="题材强度需 QuotaFuse Wire 的 theme_news 汇总，未接入时不编造榜单。"
                  onRetry={reloadAll}
                />
              ) : (
                <div className="flex flex-col gap-3">
                  <HBarList
                    items={topThemes.map((row) => ({ label: row.theme, value: row.strength }))}
                    format={(value) => formatOneDecimal(value)}
                  />
                  <DataTable<MarketThemeRow>
                    columns={themeColumns}
                    rows={themes}
                    rowKey={(row) => row.theme}
                    caption={`题材强度明细（${tradeDate}），按强度降序`}
                    density="compact"
                    maxHeight="320px"
                    initialSort={{ key: 'strength', dir: 'desc' }}
                  />
                  <SourceLine envelope={theme.data} extra="强度为 0–100 的相对分，仅在同一天内可比" />
                </div>
              )}
            </SectionCard>
          )}
        </div>

        <aside className="flex flex-col gap-4 desk:col-span-4">
          <SectionCard title="三段数据接入" subtitle="本屏只画有数据的一段" bodyClassName="p-0">
            <ul className="divide-y divide-border">
              {segments.map((segment) => (
                <li key={segment.key} className="flex flex-col gap-1 px-4 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => setTab(segment.key)}
                      aria-current={segment.key === tab ? 'true' : undefined}
                      className={
                        segment.key === tab
                          ? 'rounded-xs text-body font-medium text-text underline underline-offset-2'
                          : 'rounded-xs text-body text-text-secondary hover:text-text hover:underline hover:underline-offset-2'
                      }
                    >
                      {TAB_LABEL[segment.key]}
                    </button>
                    <Badge tone={segment.ready ? 'ok' : 'warn'}>{segment.ready ? '已同步' : '数据未同步'}</Badge>
                  </div>
                  <p className="text-caption leading-body text-text-muted">
                    {segment.ready ? segment.label : segment.reason}
                  </p>
                </li>
              ))}
            </ul>
          </SectionCard>

          <SectionCard title="同步与口径" subtitle="缺数据时先同步，再刷新">
            <div className="flex flex-col gap-3 text-caption leading-body text-text-secondary">
              <p>
                统一同步入口：
                <code className="ldst-num ml-1 rounded border border-border bg-surface-2 px-1.5 py-0.5">
                  ldst sync --date {tradeDate}
                </code>
              </p>
              <ul className="flex list-disc flex-col gap-1 pl-4">
                <li>情绪温度 0–100，50 中性；区间带 40–60 只做视觉参考，不是交易信号。</li>
                <li>梯队按板数升序，柱高为家数；末位未知板位不计入柱图。</li>
                <li>
                  题材强度为当日相对分（0–100），只同日可比；数据源 QuotaFuse Wire，接口
                  <code className="ldst-num mx-1">/api/market/theme</code>
                  未上线前该段固定「数据未同步」。
                </li>
                <li>缺失一律 {MISSING} + 原因，绝不补 0、不画假线。</li>
              </ul>
            </div>
          </SectionCard>
        </aside>
      </div>
    </div>
  )
}
