import { useCallback, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  type WeeklyFacts,
  type WeeklyReport,
  type WeeklyTrend,
} from '../api'
import { AiErrorNotice } from '../components/kit'
import { useAsync } from '../lib/useAsync'
import { useForcedState, resolveState, type PageState } from '../lib/pageState'
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
  ErrorBlock,
  HBarList,
  MetricCard,
  SectionCard,
  Sparkline,
  SourceBadges,
  type Column,
  type SectionTone,
} from '../components/ui'

/**
 * 周度复盘 `/weekly`（R11 / QUOT-149）：复用收盘复盘的报告式版式。
 *
 * 两层内容严格分开：`facts` 是程序聚合的周度事实包（本页不重算任何数字），
 * `sections` 是 AI 叙述（失败段落由后端补模板，`mode=template` 时给降级提示）。
 * 缺失值一律 `—` + 原因，不补 0；没有产物时走空态而不是 404。
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

/** 趋势值 + 变化箭头：`62.8（52.5 → 45.8，−6.7）`，缺失分项写 `—`。 */
function trendNote(trend: WeeklyTrend | undefined, unit = ''): string {
  const value = trend ?? EMPTY_TREND
  const first = value.first === null ? MISSING : `${value.first}${unit}`
  const last = value.last === null ? MISSING : `${value.last}${unit}`
  const change = value.change === null ? MISSING : `${value.change > 0 ? '+' : ''}${value.change}${unit}`
  return `周内 ${first} → ${last} · 变化 ${change} · ${value.samples} 天有读数`
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

export default function WeeklyScreen() {
  const forced = useForcedState()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()

  const week = (() => {
    const raw = params.get('week')
    return raw && parseWeek(raw) ? raw.toUpperCase() : defaultWeek()
  })()

  const request = useAsync(
    (signal) => api.weekly(week, signal),
    [week],
  )
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
  const subtitle = usable
    ? `${week} · ${range?.start ?? MISSING} ~ ${range?.end ?? MISSING} · ${formatInt(sessionDays.length)} 个交易日有数据`
    : `${week} 周度复盘`

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
      </div>
    )
  }

  const sentimentColumns: Array<Column<SentimentDayRow>> = [
    { key: 'date', header: '日期', render: (row) => shortDate(row.date) },
    {
      key: 'temperature',
      header: '温度',
      align: 'right',
      render: (row) => formatOneDecimal(row.temperature),
      sort: (a, b) => (a.temperature ?? -1) - (b.temperature ?? -1),
    },
    {
      key: 'promotion',
      header: '晋级率',
      align: 'right',
      render: (row) => formatRatePct(row.promotion_rate).text,
    },
    { key: 'seal', header: '封板率', align: 'right', render: (row) => formatRatePct(row.seal_rate).text },
    { key: 'limit', header: '涨停', align: 'right', render: (row) => formatInt(row.limit_up_count) },
    { key: 'broken', header: '炸板', align: 'right', render: (row) => formatInt(row.broken_count) },
    { key: 'board', header: '最高板', align: 'right', render: (row) => formatBoards(row.max_board) },
    {
      key: 'gap',
      header: '断层',
      align: 'right',
      render: (row) => (row.gap_levels.length ? row.gap_levels.join('、') : MISSING),
    },
    {
      key: 'source',
      header: '来源',
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

      <div className="grid grid-cols-1 gap-4 min-[1024px]:grid-cols-4">
        <MetricCard
          label="周情绪温度均值"
          hero
          badge={<SourceBadges envelope={envelope} />}
          value={formatOneDecimal(number(sentiment?.temperature?.avg))}
          note={trendNote(sentiment?.temperature)}
          icon={
            temperatureSeries.length > 1 ? (
              <Sparkline values={temperatureSeries} ariaLabel={`${week} 每日情绪温度走势`} />
            ) : undefined
          }
        />
        <MetricCard
          label="周晋级率均值"
          badge={<SourceBadges envelope={envelope} />}
          value={formatRatePct(number(sentiment?.promotion_rate?.avg)).text}
          note={trendNote(sentiment?.promotion_rate, '%')}
        />
        <MetricCard
          label="周封板率均值"
          badge={<SourceBadges envelope={envelope} />}
          value={formatRatePct(number(sentiment?.seal_rate?.avg)).text}
          note={`涨停合计 ${formatInt(number(sentiment?.limit_up_total))} · 炸板 ${formatInt(number(sentiment?.broken_total))} · 断层 ${formatInt(number(sentiment?.gap_days))} 天`}
        />
        <MetricCard
          label="连板高度"
          badge={<SourceBadges envelope={envelope} />}
          value={formatBoards(number(ladder?.peak_height))}
          note={trendNote(ladder?.height)}
        />
      </div>

      {headline.length > 0 && (
        <section className="rounded border border-border bg-surface-2 px-4 py-3" aria-label="周报概览">
          <div className="mb-1 flex items-center gap-2">
            <Badge tone="info">程序直出概览</Badge>
            <span className="text-caption text-text-muted">
              模型 {report?.ai?.model ?? '未启用'} · 生成于 {formatTime(text(report?.generated_at))}
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
          density="compact"
          empty={<>本周没有可用单日指标。</>}
        />
      </SectionCard>

      <div className="grid gap-4 min-[1100px]:grid-cols-2">
        <SectionCard
          title="涨跌分布"
          tone="neutral"
          subtitle={
            breadth?.available
              ? `样本 ${formatInt(number(breadth.samples))} · 上涨占比 ${formatRatePct(number(breadth.up_share_pct)).text}`
              : (text(breadth?.reason) ?? '无读数')
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
            <p className="text-body leading-body text-text-muted">{text(breadth?.reason) ?? MISSING}</p>
          )}
        </SectionCard>

        <SectionCard
          title="主线题材持续天数"
          tone="neutral"
          subtitle={
            themes?.available
              ? `每日涨幅前 ${formatInt(number(themes.window))} 名为一个窗口 · 观察 ${formatInt(number(themes.observed))} 个题材`
              : (text(themes?.reason) ?? '无读数')
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
            <p className="text-body leading-body text-text-muted">{text(themes?.reason) ?? MISSING}</p>
          )}
        </SectionCard>
      </div>

      <div className="grid gap-4 min-[1100px]:grid-cols-2">
        <SectionCard title="本周龙头（当日最高板合并）" tone="neutral">
          <ul className="flex flex-col gap-1.5">
            {(ladder?.leaders ?? []).map((leader) => (
              <li key={`${leader.code}-${leader.date}`} className="flex items-baseline justify-between gap-2">
                <span className="text-body text-text">
                  <span className="ldst-num mr-2 text-caption text-text-muted">{leader.code}</span>
                  {leader.name ?? MISSING}
                </span>
                <span className="ldst-num text-body text-text-secondary">
                  {formatBoards(number(leader.boards))} · {shortDate(leader.date)}
                </span>
              </li>
            ))}
            {(ladder?.leaders ?? []).length === 0 && (
              <li className="text-body text-text-muted">涨停池里没有可解析的连板数据。</li>
            )}
          </ul>
        </SectionCard>

        <SectionCard
          title="待验证项结算"
          tone="neutral"
          subtitle={
            validation?.available
              ? `条件 ${formatInt(number(validation.conditions))} 条 · 已结算 ${formatInt(number(validation.settled))}` +
                `（通过 ${formatInt(number(validation.passed))} / 未通过 ${formatInt(number(validation.failed))}` +
                ` / 判定不出 ${formatInt(number(validation.unknown))}）· 待结算 ${formatInt(number(validation.pending))}` +
                ` · 通过率 ${formatRatePct(number(validation.pass_rate)).text}`
              : (text(validation?.reason) ?? '无')
          }
        >
          <ul className="flex flex-col gap-1.5">
            {(validation?.items ?? []).slice(0, 8).map((item) => (
              <li key={`${item.generated_date}-${item.id ?? item.text}`} className="text-caption text-text-secondary">
                <span className="ldst-num mr-2 text-text-muted">{shortDate(item.generated_date)}</span>
                {item.text ?? MISSING} → {item.settled ? (item.passed === true ? '通过' : item.passed === false ? '未通过' : '判定不出') : '未结算'}
              </li>
            ))}
            {(validation?.items ?? []).length === 0 && (
              <li className="text-body text-text-muted">{text(validation?.reason) ?? MISSING}</li>
            )}
          </ul>
        </SectionCard>
      </div>

      <p className="text-caption text-text-muted">
        指标口径：{Object.entries(facts?.caliber ?? {}).slice(0, 3).map(([key, value]) => `${key}：${value}`).join(' · ') || MISSING}
      </p>
      <p className="text-caption text-text-muted">
        数据来源：{facts?.source_detail || MISSING}
        {coverage && coverage.days_with_checkpoint
          ? ` · 每日复盘 AI 完成度 ${formatInt(number(coverage.sections_done))}/${formatInt(number(coverage.sections_expected))}（降级 ${formatInt(number(coverage.sections_degraded))}、失败 ${formatInt(number(coverage.sections_failed))}）`
          : ''}
        {` · checkpoint：${text(report?.checkpoint) ?? MISSING}`}
      </p>
    </div>
  )
}
