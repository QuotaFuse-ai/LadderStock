import { useCallback, useMemo, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'

import { describeApiError, isApiError, type WeeklyFacts, type WeeklyReport, type WeeklyTrend } from '../api'
import { AiErrorNotice } from '../components/kit'
import { resolveState, type PageState } from '../lib/pageState'
import {
  MISSING,
  formatBoards,
  formatInt,
  formatOneDecimal,
  formatRatePct,
  formatTime,
  shortDate,
} from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  DataTable,
  EmptyState,
  ErrorBlock,
  HBarList,
  MetricCard,
  SectionCard,
  Sparkline,
  SourceBadges,
  type Column,
  type SectionTone,
} from '../components/ui'
import { useDensity } from '../lib/prefs'
import { useWeeklyView } from './weekly/useWeeklyView'

/**
 * 周度复盘 `/weekly`（R11 / QUOT-149）：复用收盘复盘的报告式版式。
 *
 * 两层内容严格分开：`facts` 是程序聚合的周度事实包（本页不重算任何数字），
 * `sections` 是 AI 叙述（失败段落由后端补模板，`mode=template` 时给降级提示）。
 * 缺失值一律 `—` + 原因，不补 0；没有产物时走空态而不是 404。
 *
 * 演示源（`?demo=1` 或设置页「虚拟数据」）只替换 `/api/weekly` 这一条读接口，
 * 事实基线与 `/review`、`/limit-up` 是同一份市场事实；真实请求失败绝不回落到 `weekly/demo.ts`。
 */

const SECTION_STATUS: Record<string, { label: string; tone: SectionTone; badge: 'ok' | 'warn' | 'danger' | 'info' | 'neutral' }> = {
  ok: { label: '已完成', tone: 'ok', badge: 'ok' },
  degraded: { label: '降级完成', tone: 'warn', badge: 'warn' },
  failed: { label: '失败', tone: 'danger', badge: 'danger' },
  pending: { label: '待生成', tone: 'neutral', badge: 'neutral' },
}

const MODE_LABEL: Record<string, string> = {
  structured: '结构化输出',
  parsed: '解析式输出',
  text: '纯文本降级',
  template: '模板降级',
}

/** `facts.caliber` 的后端 key → 中文指标名（§11.0「后端 key 不直显」；未知 key 不展示）。 */
const CALIBER_LABEL: Record<string, string> = {
  temperature: '情绪温度',
  promotion_rate: '晋级率',
  ladder_height: '连板高度',
  breadth: '涨跌分布',
  theme_streak: '主线题材',
  validation: '待验证项',
  ai_coverage: '复盘完成度',
}

// ------------------------------------------------------------------ ISO 周标签

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

/** 与后端 `weekly/week.py` 同一口径：ISO-8601 周历，周一为一周之始。 */
export function isoWeekLabel(day: Date): string {
  const isoDay = (day.getDay() + 6) % 7 + 1
  const thursday = new Date(day.getFullYear(), day.getMonth(), day.getDate() + (4 - isoDay))
  const yearStart = new Date(thursday.getFullYear(), 0, 1)
  const week = Math.ceil(((thursday.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
  return `${thursday.getFullYear()}-W${pad2(week)}`
}

const WEEK_PATTERN = /^(\d{4})-W(\d{2})$/

function parseWeek(label: string): { year: number; week: number } | null {
  const matched = WEEK_PATTERN.exec(label.toUpperCase())
  if (!matched) return null
  const year = Number(matched[1])
  const week = Number(matched[2])
  return week >= 1 && week <= 53 ? { year, week } : null
}

/** 上一周 / 下一周标签：跨年由 ISO 周历自己处理，这里只做 ±7 天再归一。 */
export function shiftWeek(label: string, direction: -1 | 1): string {
  const parsed = parseWeek(label)
  if (!parsed) return label
  // 该 ISO 周的周一（ISO 年 1 月 4 日一定落在 W01 那一周里）
  const jan4 = new Date(parsed.year, 0, 4)
  const monday = new Date(jan4.getFullYear(), 0, jan4.getDate() - ((jan4.getDay() + 6) % 7))
  const target = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + (parsed.week - 1 + direction) * 7)
  return isoWeekLabel(target)
}

function defaultWeek(now = new Date()): string {
  return isoWeekLabel(now)
}

// ------------------------------------------------------------------- 取值辅助

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

const EMPTY_TREND: WeeklyTrend = { first: null, last: null, change: null, avg: null, samples: 0 }

/** 只取页面用得上的字段，结构不符就当没有（不猜、不补默认值）。 */
function asFacts(value: unknown): WeeklyFacts | null {
  return value && typeof value === 'object' ? (value as WeeklyFacts) : null
}

interface Section {
  name: string
  title: string
  status: string
  mode: string
  model: string | null
  degraded: boolean
  narrative: string[]
  notes: string[]
}

function asSections(value: unknown): Section[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const record = item as Record<string, unknown>
    const name = text(record.name)
    if (!name) return []
    return [
      {
        name,
        title: text(record.title) ?? name,
        status: text(record.status) ?? 'pending',
        mode: text(record.mode) ?? '',
        model: text(record.model),
        degraded: record.degraded === true,
        narrative: asStringList(record.narrative),
        notes: asStringList(record.notes),
      },
    ]
  })
}

/** 趋势值 + 变化箭头：`62.8（52.5 → 45.8，−6.7）`，缺失分项写 `—`；数字一律 `.ldst-num`（§7.2）。 */
function trendNote(trend: WeeklyTrend | undefined, unit = ''): ReactNode {
  const value = trend ?? EMPTY_TREND
  const num = (item: number | null): ReactNode =>
    item === null ? MISSING : <span className="ldst-num">{`${item}${unit}`}</span>
  const change: ReactNode =
    value.change === null ? (
      MISSING
    ) : (
      <span className="ldst-num">{`${value.change > 0 ? '+' : ''}${value.change}${unit}`}</span>
    )
  return (
    <>
      周内 {num(value.first)} → {num(value.last)} · 变化 {change} ·{' '}
      <span className="ldst-num">{value.samples}</span> 天
    </>
  )
}

interface SentimentDayRow {
  date: string
  temperature: number | null
  promotion_rate: number | null
  seal_rate: number | null
  limit_up_count: number | null
  broken_count: number | null
  max_board: number | null
  gap_levels: number[]
  source: string | null
}

/**
 * 「这一段没有读数」：结论 + 原因，走 C-17 EmptyState 而不是手写小字（§11.7）。
 * 不补 0、不编造榜单。
 */
function NoReading({
  title = '本段没有可用读数',
  reason,
  compact = false,
}: {
  title?: string
  reason: string
  compact?: boolean
}) {
  return <EmptyState compact={compact} title={title} description={reason} />
}

export default function WeeklyScreen() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  // 行高档位只由用户的密度偏好决定（§11.18「密度只改行高」），屏内不写死
  const density = useDensity()

  const week = (() => {
    const raw = params.get('week')
    return raw && parseWeek(raw) ? raw.toUpperCase() : defaultWeek()
  })()

  const view = useWeeklyView({ week })
  const forced = view.forced
  const demo = view.demo
  const request = view.request
  const reload = useCallback(() => request.reload(), [request.reload]) // eslint-disable-line react-hooks/exhaustive-deps

  const envelope = request.data ?? null
  const report: WeeklyReport | null = request.data?.payload.weekly ?? null
  const facts = report ? asFacts(report.facts) : null

  const derived = useMemo<PageState>(() => {
    if (request.status === 'loading') return 'loading'
    if (request.error) {
      return isApiError(request.error) && request.error.kind === 'not-connected' ? 'offline' : 'error'
    }
    if (report === null) return 'empty'
    return report.available === false ? 'empty' : 'normal'
  }, [request.status, request.error, report])
  const state = resolveState(forced, derived)

  const usable = state === 'normal'
  const sections = usable ? asSections(report?.sections) : []
  const failures = usable ? (report?.ai_failures ?? []) : []
  const headline = usable ? asStringList(report?.headline) : []
  const sessionDays = usable ? (facts?.session_days ?? []) : []
  const missingDays = usable ? (facts?.missing_days ?? []) : []
  const sentiment = facts?.sentiment
  const ladder = facts?.ladder
  const breadth = facts?.breadth
  const themes = facts?.themes
  const validation = facts?.validation
  const coverage = facts?.ai_coverage

  const temperatureSeries = (sentiment?.series ?? [])
    .map((row) => number(row.temperature))
    .filter((value): value is number => value !== null)

  const rows: SentimentDayRow[] = (sentiment?.series ?? []).map((row) => ({
    date: row.date,
    temperature: number(row.temperature),
    promotion_rate: number(row.promotion_rate),
    seal_rate: number(row.seal_rate),
    limit_up_count: number(row.limit_up_count),
    broken_count: number(row.broken_count),
    max_board: number(row.max_board),
    gap_levels: Array.isArray(row.gap_levels) ? row.gap_levels : [],
    source: text(row.source),
  }))

  const goWeek = (next: string) => {
    const merged = new URLSearchParams(params)
    merged.set('week', next)
    setParams(merged, { replace: true })
  }

  const range = facts?.range ?? report?.range ?? undefined
  // 周标签以载荷为准（演示周报只有 2026-W44 一份），避免「标题一周 / 正文另一周」的错位
  const displayWeek = usable ? text(report?.week) ?? week : week
  const demoSuffix = demo ? ' · 演示数据（非真实行情）' : ''
  const subtitle = usable
    ? `${displayWeek} · ${range?.start ?? MISSING} ~ ${range?.end ?? MISSING} · ${formatInt(sessionDays.length)} 个交易日有数据${demoSuffix}`
    : `${displayWeek} 周度复盘${demoSuffix}`

  const header = (
    <PageHeader
      title="周度复盘"
      subtitle={subtitle}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <>
          <Button
            variant="secondary"
            aria-label="上一周"
            onClick={() => goWeek(shiftWeek(week, -1))}
            leadingIcon={<ChevronLeft className="size-3.5" aria-hidden="true" />}
          >
            上周
          </Button>
          <Button
            variant="secondary"
            aria-label="下一周"
            onClick={() => goWeek(shiftWeek(week, 1))}
            leadingIcon={<ChevronRight className="size-3.5" aria-hidden="true" />}
          >
            下周
          </Button>
          <Button
            variant="secondary"
            loading={request.status === 'loading' && state !== 'loading'}
            disabled={state === 'loading'}
            onClick={reload}
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
        <ScreenSkeleton metrics={4} rows={5} />
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock {...describeApiError(request.error)} onRetry={reload} />
      </div>
    )
  }

  /**
   * 指标卡阵（§11.7：empty 分支也要出卡阵，与其它屏同构，不得只剩标题 + 一条横幅）。
   * `usable` 为假时数值一律 `—` + 空因，不补 0。
   */
  const metricGrid = (
    <div className="grid grid-cols-2 gap-4 desk:grid-cols-4">
      <MetricCard
        label="温度均值"
        hero
        badge={<SourceBadges envelope={usable ? envelope : null} />}
        value={formatOneDecimal(usable ? number(sentiment?.temperature?.avg) : null)}
        note={usable ? trendNote(sentiment?.temperature) : '数据未生成'}
        icon={
          temperatureSeries.length > 1 ? (
            <Sparkline
              values={temperatureSeries}
              width={44}
              ariaLabel={`${displayWeek} 每日情绪温度走势`}
            />
          ) : undefined
        }
      />
      <MetricCard
        label="晋级率均值"
        badge={<SourceBadges envelope={usable ? envelope : null} />}
        value={usable ? formatRatePct(number(sentiment?.promotion_rate?.avg)).text : MISSING}
        note={usable ? trendNote(sentiment?.promotion_rate, '%') : '数据未生成'}
      />
      <MetricCard
        label="封板率均值"
        badge={<SourceBadges envelope={usable ? envelope : null} />}
        value={usable ? formatRatePct(number(sentiment?.seal_rate?.avg)).text : MISSING}
        note={
          usable ? (
            <>
              涨停合计 <span className="ldst-num">{formatInt(number(sentiment?.limit_up_total))}</span> · 炸板{' '}
              <span className="ldst-num">{formatInt(number(sentiment?.broken_total))}</span> · 断层{' '}
              <span className="ldst-num">{formatInt(number(sentiment?.gap_days))}</span> 天
            </>
          ) : (
            '数据未生成'
          )
        }
      />
      <MetricCard
        label="连板高度"
        badge={<SourceBadges envelope={usable ? envelope : null} />}
        value={usable ? formatBoards(number(ladder?.peak_height)) : MISSING}
        note={usable ? trendNote(ladder?.height) : '数据未生成'}
      />
    </div>
  )

  if (state === 'offline' || state === 'empty') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        {state === 'offline' && <OfflineBanner onRetry={reload} />}
        {state === 'empty' && (
          <ErrorBlock
            variant="banner"
            title={`${week} 还没有可用周报`}
            detail={
              text(report?.reason) ??
              `本机没有 ${week} 的周报产物。先跑 ldst sync 与每日复盘，再执行 ldst weekly --week ${week} 生成。`
            }
            onRetry={reload}
            retryLabel="重新检查"
          />
        )}
        {metricGrid}
      </div>
    )
  }

  const sentimentColumns: Array<Column<SentimentDayRow>> = [
    {
      key: 'date',
      header: '日期',
      width: '88px',
      render: (row) => <span className="ldst-num">{shortDate(row.date)}</span>,
    },
    {
      key: 'temperature',
      header: '温度',
      width: '88px',
      numeric: true,
      render: (row) => formatOneDecimal(row.temperature),
      sort: (a, b) => (a.temperature ?? -1) - (b.temperature ?? -1),
    },
    {
      key: 'promotion',
      header: '晋级率',
      width: '112px',
      numeric: true,
      render: (row) => formatRatePct(row.promotion_rate).text,
    },
    {
      key: 'seal',
      header: '封板率',
      width: '112px',
      numeric: true,
      render: (row) => formatRatePct(row.seal_rate).text,
    },
    { key: 'limit', header: '涨停', width: '88px', numeric: true, render: (row) => formatInt(row.limit_up_count) },
    { key: 'broken', header: '炸板', width: '88px', numeric: true, render: (row) => formatInt(row.broken_count) },
    { key: 'board', header: '最高板', width: '88px', numeric: true, render: (row) => formatBoards(row.max_board) },
    {
      key: 'gap',
      header: '断层',
      width: '112px',
      numeric: true,
      render: (row) => (row.gap_levels.length ? row.gap_levels.join('、') : MISSING),
    },
    {
      key: 'source',
      header: '来源',
      width: '112px',
      render: (row) => (row.source === 'artifact' ? '当日复盘' : row.source === 'recomputed' ? '补算' : MISSING),
    },
  ]

  return (
    <div className="flex flex-col gap-5">
      {header}

      {report?.ai?.degraded === true && (
        <Banner tone="warn" title="部分段落 AI 降级">
          事实包与指标不受影响；降级段落由程序从事实包直出要点（模板），可在设置里配置模型后重跑。
        </Banner>
      )}

      {metricGrid}

      {headline.length > 0 && (
        <section className="rounded border border-border bg-surface-2 px-4 py-3" aria-label="周报概览">
          <div className="mb-1 flex items-center gap-2">
            <Badge tone="info">程序直出概览</Badge>
            <span className="text-caption text-text-muted">
              模型 {report?.ai?.model ?? '未启用'} · 生成于{' '}
              <span className="ldst-num">{formatTime(text(report?.generated_at))}</span>
            </span>
          </div>
          <ul className="flex flex-col gap-1">
            {headline.map((line) => (
              <li key={line} className="text-body leading-body text-text-secondary">
                {line}
              </li>
            ))}
          </ul>
        </section>
      )}

      {missingDays.length > 0 && (
        <Banner tone="info" title={`${formatInt(missingDays.length)} 个工作日没有可用指标`}>
          {missingDays
            .map((day) => `${shortDate(day.date)}：${day.reason}`)
            .join('；')}
        </Banner>
      )}

      <div className="flex flex-col gap-4">
        {sections.map((section, index) => {
          const meta = SECTION_STATUS[section.status] ?? SECTION_STATUS.pending
          return (
            <SectionCard
              key={section.name}
              tone={meta.tone}
              title={
                <span>
                  <span className="ldst-num mr-2 text-caption text-text-muted">{index + 1}</span>
                  {section.title}
                </span>
              }
              subtitle={`${MODE_LABEL[section.mode] ?? section.mode ?? '未生成'} · 模型 ${section.model ?? '未启用'}`}
              actions={
                <>
                  <Badge tone={meta.badge}>{meta.label}</Badge>
                  {section.degraded && <Badge tone="warn">降级</Badge>}
                </>
              }
            >
              <div className="flex flex-col gap-2">
                {section.mode === 'template' && (
                  <AiErrorNotice
                    kind="degraded"
                    section={section.title}
                    message="本段 AI 叙述未生成，以下为程序从事实包直出的要点。"
                    onOpenStatus={() => navigate('/settings?tab=ai')}
                  />
                )}
                {section.narrative.length > 0 ? (
                  section.narrative.map((line) => (
                    <p key={line} className="text-body leading-body text-text-secondary">
                      {line}
                    </p>
                  ))
                ) : (
                  <p className="text-body leading-body text-text-muted">该段没有内容（待生成）。</p>
                )}
                {section.notes.length > 0 && (
                  <p className="border-t border-border pt-2 text-caption text-text-muted">
                    {section.notes.join(' ')}
                  </p>
                )}
              </div>
            </SectionCard>
          )
        })}
      </div>

      {failures.length > 0 && (
        <Banner tone="danger" title={`${formatInt(failures.length)} 段 AI 生成失败`}>
          {failures.map((failure) => `${failure.section}：${failure.message}`).join('；')}
        </Banner>
      )}

      <SectionCard title="每日序列（程序聚合，非模型输出）" tone="neutral">
        <DataTable
          columns={sentimentColumns}
          rows={rows}
          rowKey={(row) => row.date}
          density={density}
          empty={
            <EmptyState
              compact
              title="本周没有可用单日指标"
              description="事实包里 `sentiment.series` 为空：该周的每日复盘产物缺失，先跑 ldst sync 再重跑 ldst weekly。"
            />
          }
        />
      </SectionCard>

      <div className="grid gap-4 desk:grid-cols-2">
        <SectionCard
          title="涨跌分布"
          tone="neutral"
          subtitle={
            breadth?.available ? (
              <>
                样本 <span className="ldst-num">{formatInt(number(breadth.samples))}</span> · 上涨占比{' '}
                <span className="ldst-num">{formatRatePct(number(breadth.up_share_pct)).text}</span>
              </>
            ) : (
              (text(breadth?.reason) ?? '无读数')
            )
          }
        >
          {breadth?.available ? (
            <HBarList
              items={(breadth.distribution ?? []).map((band) => ({
                label: band.band,
                value: number(band.count),
                hint: band.share_pct === null || band.share_pct === undefined ? undefined : `${band.share_pct}%`,
              }))}
              unit=" 只"
            />
          ) : (
            <NoReading reason={text(breadth?.reason) ?? '涨跌分布需要当日全市场逐笔涨幅，事实包里没有该段读数。'} />
          )}
        </SectionCard>

        <SectionCard
          title="主线题材持续天数"
          tone="neutral"
          subtitle={
            themes?.available ? (
              <>
                每日涨幅前 <span className="ldst-num">{formatInt(number(themes.window))}</span> 名为一个窗口 · 观察{' '}
                <span className="ldst-num">{formatInt(number(themes.observed))}</span> 个题材
              </>
            ) : (
              (text(themes?.reason) ?? '无读数')
            )
          }
        >
          {themes?.available ? (
            <HBarList
              items={(themes.themes ?? []).map((theme) => ({
                label: theme.name,
                value: number(theme.streak_days),
                hint: `入窗 ${theme.days_on_top} 天 · 均涨幅 ${theme.avg_change_pct ?? MISSING}%`,
              }))}
              unit=" 天"
            />
          ) : (
            <NoReading reason={text(themes?.reason) ?? '题材轮动数据源（QuotaFuse Wire theme_news）未接入，本页不编造榜单。'} />
          )}
        </SectionCard>
      </div>

      <div className="grid gap-4 desk:grid-cols-2">
        <SectionCard title="本周龙头（当日最高板合并）" tone="neutral">
          <ul className="flex flex-col gap-1">
            {(ladder?.leaders ?? []).map((leader) => {
              const detail = `${leader.code} ${leader.name ?? MISSING} · ${formatBoards(number(leader.boards))} · ${shortDate(leader.date)}`
              const row = (
                <>
                  <span className="min-w-0 truncate text-body text-text" title={detail}>
                    <span className="ldst-num mr-2 text-caption text-text-muted">{leader.code}</span>
                    {leader.name ?? MISSING}
                  </span>
                  <span className="ldst-num shrink-0 text-caption text-text-secondary">
                    {formatBoards(number(leader.boards))} · {shortDate(leader.date)}
                  </span>
                </>
              )
              return leader.code ? (
                <li key={`${leader.code}-${leader.date}`}>
                  <Link
                    to={`/research/${leader.code}`}
                    className="flex min-h-row items-center justify-between gap-2 rounded-xs px-1 transition-colors duration-fast ease-ldst hover:bg-surface-3 hover:text-text"
                  >
                    {row}
                  </Link>
                </li>
              ) : (
                <li key={`${leader.name}-${leader.date}`} className="flex min-h-row items-center justify-between gap-2 px-1">
                  {row}
                </li>
              )
            })}
            {(ladder?.leaders ?? []).length === 0 && (
              <li>
                <NoReading compact title="涨停池里没有可解析的连板数据" reason="连板高度来自当日涨停池；未同步时这一段为空。" />
              </li>
            )}
          </ul>
        </SectionCard>

        <SectionCard
          title="待验证项结算"
          tone="neutral"
          subtitle={
            validation?.available ? (
              <>
                条件 <span className="ldst-num">{formatInt(number(validation.conditions))}</span> 条 · 已结算{' '}
                <span className="ldst-num">{formatInt(number(validation.settled))}</span>（通过{' '}
                <span className="ldst-num">{formatInt(number(validation.passed))}</span> / 未通过{' '}
                <span className="ldst-num">{formatInt(number(validation.failed))}</span> / 判定不出{' '}
                <span className="ldst-num">{formatInt(number(validation.unknown))}</span>）· 待结算{' '}
                <span className="ldst-num">{formatInt(number(validation.pending))}</span> · 通过率{' '}
                <span className="ldst-num">{formatRatePct(number(validation.pass_rate)).text}</span>
              </>
            ) : (
              (text(validation?.reason) ?? '无')
            )
          }
        >
          <ul className="flex flex-col gap-1">
            {(validation?.items ?? []).slice(0, 8).map((item) => (
              <li
                key={`${item.generated_date}-${item.id ?? item.text}`}
                className="flex min-h-row items-baseline gap-2 text-caption text-text-secondary"
              >
                <span className="ldst-num shrink-0 text-text-muted">{shortDate(item.generated_date)}</span>
                <span className="min-w-0 flex-1 truncate" title={item.text ?? MISSING}>
                  {item.text ?? MISSING} →{' '}
                  {item.settled
                    ? item.passed === true
                      ? '通过'
                      : item.passed === false
                        ? '未通过'
                        : '判定不出'
                    : '未结算'}
                </span>
              </li>
            ))}
            {(validation?.items ?? []).length === 0 && (
              <li>
                <NoReading compact title="本周没有待结算的验证条件" reason={text(validation?.reason) ?? '验证草稿为空时该段没有读数。'} />
              </li>
            )}
          </ul>
        </SectionCard>
      </div>

      <p className="text-caption text-text-muted">
        指标口径：
        {(() => {
          // 后端 key 不直显（§11.0 / §11.7）：已知 key 用中文指标名，未知 key 只保留口径正文，不藏内容
          const lines = Object.entries(facts?.caliber ?? {})
            .map(([key, value]) => (CALIBER_LABEL[key] ? `${CALIBER_LABEL[key]}：${value}` : `${value}`))
            .slice(0, 3)
          return lines.length > 0 ? lines.join(' · ') : MISSING
        })()}
      </p>
      <p className="text-caption text-text-muted">
        数据来源：<span className="break-all">{facts?.source_detail || MISSING}</span>
        {coverage && coverage.days_with_checkpoint ? (
          <>
            {' '}
            · 每日复盘 AI 完成度{' '}
            <span className="ldst-num">
              {formatInt(number(coverage.sections_done))}/{formatInt(number(coverage.sections_expected))}
            </span>
            （降级 <span className="ldst-num">{formatInt(number(coverage.sections_degraded))}</span>、失败{' '}
            <span className="ldst-num">{formatInt(number(coverage.sections_failed))}</span>）
          </>
        ) : (
          ''
        )}
        {' '}
        · 校验点：<span className="ldst-num">{text(report?.checkpoint) ?? MISSING}</span>
      </p>
    </div>
  )
}
