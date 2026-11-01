import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, CalendarDays, ChevronLeft, ChevronRight, Database, RefreshCw } from 'lucide-react'

import {
  CALENDAR_SECTION_LABEL,
  CALENDAR_SECTION_ORDER,
  isRadarEndpointMissing,
  type CalendarEntry,
  type CalendarExtractedEvent,
  type CalendarSection,
  type EventCalendarPayload,
} from '../../api/radar'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorBlock,
  IconButton,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { localDateIso, MISSING } from '../../lib/format'
import { withParams } from '../industry/links'
import { SourceStatusBadge } from '../industry/sourceBadges'
import { DEMO_MONTH } from './demo'
import { useCalendarView, useDemoEnabled } from './useRadarView'

/**
 * P-R10-02 事件日历（`/calendar`）。
 *
 * 月历只做「哪天有已知事件」的定位；当日面板按小节列出条目，
 * 并给出跳到当日资讯（`/news?date=`）的入口。
 *
 * `status="no_source"` 的小节必须显式画成「暂无数据源 + 怎么补」（reason / ask），
 * 不画空白格、不估算日期——这是 R6 日历口径的硬要求。
 */

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'] as const

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function shiftMonth(month: string, delta: number): string {
  const [year, index] = month.split('-').map(Number)
  if (!year || !index) return month
  const next = new Date(year, index - 1 + delta, 1)
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}`
}

function todayMonth(): string {
  return localDateIso().slice(0, 7)
}

interface DayCell {
  date: string | null
  day: number | null
  inMonth: boolean
}

function buildMonth(month: string): DayCell[] {
  const [year, index] = month.split('-').map(Number)
  if (!year || !index) return []
  const first = new Date(year, index - 1, 1)
  const offset = (first.getDay() + 6) % 7
  const total = new Date(year, index, 0).getDate()
  const cells: DayCell[] = []
  for (let i = 0; i < offset; i += 1) cells.push({ date: null, day: null, inMonth: false })
  for (let day = 1; day <= total; day += 1) {
    cells.push({ date: `${month}-${pad(day)}`, day, inMonth: true })
  }
  while (cells.length % 7 !== 0) cells.push({ date: null, day: null, inMonth: false })
  return cells
}

interface DayData {
  entries: CalendarEntry[]
  events: CalendarExtractedEvent[]
}

function groupByDay(payload: EventCalendarPayload | null): Map<string, DayData> {
  const map = new Map<string, DayData>()
  const bucket = (date: string): DayData => {
    const current = map.get(date) ?? { entries: [], events: [] }
    map.set(date, current)
    return current
  }
  for (const section of payload?.sections ?? []) {
    for (const entry of section.entries) bucket(entry.date).entries.push(entry)
  }
  for (const event of payload?.extracted_events ?? []) bucket(event.date).events.push(event)
  return map
}

function sectionShort(key: string): string {
  if (key === 'lockup_expiry') return '解禁'
  if (key === 'earnings_preview') return '预告'
  if (key === 'shareholder_meeting') return '股东大会'
  if (key === 'policy_window') return '政策'
  if (key === 'earnings_disclosure') return '披露'
  return key
}

function MonthGrid({
  month,
  days,
  byDay,
  selected,
  onSelect,
}: {
  month: string
  days: DayCell[]
  byDay: Map<string, DayData>
  selected: string | null
  onSelect: (date: string) => void
}) {
  const today = localDateIso()
  return (
    <Card>
      <CardHeader title={`${month.slice(0, 4)} 年 ${Number(month.slice(5))} 月`} subtitle="周一开始 · 点一天看当日事件" />
      <div className="p-3">
        <div className="grid grid-cols-7 gap-1" aria-hidden="true">
          {WEEKDAYS.map((label) => (
            <div key={label} className="pb-1 text-center text-caption text-text-muted">
              {label}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1" role="group" aria-label={`${month} 月历`}>
          {days.map((cell, index) => {
            if (!cell.date || cell.day === null) {
              return <div key={`empty-${index}`} className="min-h-[76px] rounded border border-transparent" />
            }
            const data = byDay.get(cell.date)
            const count = (data?.entries.length ?? 0) + (data?.events.length ?? 0)
            const active = cell.date === selected
            const chips = [
              ...(data?.entries ?? []).map((entry) => sectionShort(entry.section)),
              ...(data?.events ?? []).map(() => '事件'),
            ]
            const unique = [...new Set(chips)]
            return (
              <button
                key={cell.date}
                type="button"
                aria-pressed={active}
                aria-label={`${cell.date}${count > 0 ? `，${count} 个条目` : '，没有已知事件'}`}
                onClick={() => onSelect(cell.date as string)}
                className={cn(
                  'flex min-h-[76px] flex-col items-start gap-1 rounded border p-1.5 text-left transition-colors duration-fast',
                  active
                    ? 'border-accent bg-accent-soft'
                    : 'border-border bg-surface hover:bg-surface-3',
                  cell.date === today && !active && 'border-border-strong',
                )}
              >
                <span className="flex w-full items-baseline justify-between">
                  <span className={cn('ldst-num text-body', active ? 'text-accent' : 'text-text')}>{cell.day}</span>
                  {count > 0 && (
                    <span className="ldst-num text-caption text-text-secondary">{count}</span>
                  )}
                </span>
                {unique.length > 0 && (
                  <span className="flex flex-wrap gap-0.5">
                    {unique.slice(0, 2).map((label) => (
                      <span
                        key={label}
                        className="rounded-xs bg-surface-3 px-1 text-caption leading-tight text-text-secondary"
                      >
                        {label}
                      </span>
                    ))}
                    {unique.length > 2 && (
                      <span className="text-caption text-text-muted">+{unique.length - 2}</span>
                    )}
                  </span>
                )}
                {cell.date === today && (
                  <span className="mt-auto text-caption text-text-muted">今天</span>
                )}
              </button>
            )
          })}
        </div>
      </div>
    </Card>
  )
}

function DayPanel({
  day,
  data,
  demo,
  forced,
}: {
  day: string
  data: DayData | undefined
  demo: boolean
  forced: string | null
}) {
  const entries = data?.entries ?? []
  const events = data?.events ?? []
  const newsHref = withParams('/news', { date: day, demo: demo ? '1' : null, state: forced })
  const grouped = new Map<string, CalendarEntry[]>()
  for (const entry of entries) {
    const bucket = grouped.get(entry.section) ?? []
    bucket.push(entry)
    grouped.set(entry.section, bucket)
  }

  return (
    <Card className="flex flex-col">
      <CardHeader
        title={day}
        subtitle={`${entries.length} 个日历条目 · ${events.length} 条规则抽取`}
        actions={
          <Link
            to={newsHref}
            className="inline-flex h-control items-center gap-1 rounded border border-border-strong bg-surface px-2 text-body text-text hover:bg-surface-3"
          >
            当日资讯
            <ArrowRight className="size-3.5" aria-hidden="true" />
          </Link>
        }
      />
      <div className="flex flex-col gap-3 p-4">
        {entries.length === 0 && events.length === 0 ? (
          <EmptyState
            compact
            title="这一天没有已知事件"
            description="日历只收录有数据源的条目；没有数据的日期不估算、不用相邻日期填充。"
          />
        ) : (
          <>
            {[...grouped.entries()].map(([key, rows]) => (
              <section key={key} className="flex flex-col gap-1">
                <h3 className="text-caption font-medium text-text-secondary">
                  {CALENDAR_SECTION_LABEL[key] ?? rows[0].section ?? key}
                </h3>
                <ul className="flex flex-col gap-1">
                  {rows.map((entry) => (
                    <li key={entry.id} className="rounded border border-border bg-surface-2 px-2 py-1.5">
                      <p className="flex flex-wrap items-center gap-2 text-body text-text">
                        {entry.code && <span className="ldst-num text-text-secondary">{entry.code}</span>}
                        <span>{entry.label || '日历条目'}</span>
                      </p>
                      {entry.detail && (
                        <p className="text-caption leading-body text-text-secondary">{entry.detail}</p>
                      )}
                      <p className="text-caption text-text-muted">
                        数据集 <code className="ldst-num">{entry.source || MISSING}</code>
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            ))}

            {events.length > 0 && (
              <section className="flex flex-col gap-1">
                <h3 className="flex items-center gap-2 text-caption font-medium text-text-secondary">
                  规则抽取事件
                  <Badge tone="info">非日历数据源</Badge>
                </h3>
                <ul className="flex flex-col gap-1">
                  {events.map((event) => (
                    <li key={event.id} className="rounded border border-border bg-surface-2 px-2 py-1.5">
                      <p className="flex flex-wrap items-center gap-2 text-body text-text">
                        <Badge tone="neutral">{event.label || event.type}</Badge>
                        {event.codes.map((code) => (
                          <span key={code} className="ldst-num text-text-secondary">
                            {code}
                          </span>
                        ))}
                        <span>{event.title}</span>
                      </p>
                      <p className="text-caption leading-body text-text-secondary">证据：{event.evidence.snippet || MISSING}</p>
                      <p className="text-caption text-text-muted">
                        {event.note || '规则从正文抽取，非日历数据源'} · 依据 {event.date_basis || MISSING}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}

        <Link
          to={newsHref}
          className="mt-1 inline-flex items-center gap-1 self-start text-caption font-medium text-accent underline underline-offset-2"
        >
          在资讯雷达里查看 {day} 的资讯
          <ArrowRight className="size-3" aria-hidden="true" />
        </Link>
      </div>
    </Card>
  )
}

function SectionsCard({ sections }: { sections: CalendarSection[] }) {
  return (
    <Card>
      <CardHeader title="小节数据源" subtitle="五个小节的固定顺序 · 没数据源的小节写明原因与补法" />
      <div className="grid grid-cols-1 gap-3 p-4 desk:grid-cols-2">
        {sections.map((section) => (
          <div key={section.key} className="flex flex-col gap-1 rounded border border-border bg-surface-2 p-3">
            <p className="flex flex-wrap items-center gap-2">
              <span className="text-body font-medium text-text">{section.label}</span>
              {section.status === 'sourced' ? (
                <Badge tone={section.degraded ? 'warn' : 'ok'}>
                  {section.degraded ? '有源（降级）' : `有源 · ${section.entries.length} 条`}
                </Badge>
              ) : (
                <Badge tone="flat">暂无数据源</Badge>
              )}
            </p>
            {section.status === 'sourced' ? (
              <>
                <p className="text-caption text-text-secondary">
                  数据集 <code className="ldst-num">{section.data_source || MISSING}</code>
                  {section.tool && (
                    <>
                      {' '}
                      · 工具 <code className="ldst-num">{section.tool}</code>
                    </>
                  )}
                </p>
                {section.degraded && <p className="text-caption text-warn">{section.note || '上游降级'}</p>}
              </>
            ) : (
              <>
                <p className="text-caption leading-body text-text-secondary">{section.reason || '暂无数据源。'}</p>
                <p className="text-caption leading-body text-text-muted">{section.ask || '需要上游提供结构化数据集。'}</p>
              </>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

export default function CalendarScreen() {
  const [params, setParams] = useSearchParams()
  const demo = useDemoEnabled()
  const month = params.get('month') ?? (demo ? DEMO_MONTH : todayMonth())

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const view = useCalendarView({ month, codes: [] })
  const { forced, state, payload, error, reload } = view

  const days = useMemo(() => buildMonth(month), [month])
  const byDay = useMemo(() => groupByDay(payload), [payload])

  const daysWithData = useMemo(
    () => [...byDay.keys()].filter((date) => date.startsWith(month)).sort(),
    [byDay, month],
  )
  const day = params.get('day') ?? daysWithData[0] ?? `${month}-01`

  const header = (
    <PageHeader
      title="事件日历"
      subtitle={`${month} · ${payload?.sections.filter((section) => section.status === 'sourced').length ?? 0} / ${
        payload?.sections.length ?? CALENDAR_SECTION_ORDER.length
      } 个小节有源 · 规则抽取 ${payload?.extracted_events.length ?? 0} 条`}
      badge={
        <div className="flex flex-wrap items-center gap-2">
          <DemoStateBadge state={forced} />
          {payload && <SourceStatusBadge status={payload.data_source_status} detail={payload.reason} />}
        </div>
      }
      actions={
        <div className="flex items-center gap-2">
          <IconButton
            label="上个月"
            icon={<ChevronLeft className="size-4" aria-hidden="true" />}
            onClick={() => {
              const next = new URLSearchParams(params)
              next.set('month', shiftMonth(month, -1))
              next.delete('day')
              setParams(next, { replace: true })
            }}
          />
          <Button variant="secondary" onClick={() => setParam('month', todayMonth())}>
            本月
          </Button>
          <IconButton
            label="下个月"
            icon={<ChevronRight className="size-4" aria-hidden="true" />}
            onClick={() => {
              const next = new URLSearchParams(params)
              next.set('month', shiftMonth(month, 1))
              next.delete('day')
              setParams(next, { replace: true })
            }}
          />
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
        <ScreenSkeleton metrics={0} rows={7} />
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
            icon={<CalendarDays aria-hidden="true" />}
            title="本地服务未连接"
            description="启动 ldst run 后自动接入 /api/events；未连接时日历不显示任何估算日期。"
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
          title="事件日历取数失败"
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
            title={endpointMissing ? '接口未接入' : '这个月没有可用事件'}
            description={
              endpointMissing ? (
                <>
                  页面已按 <code className="ldst-num">GET /api/events</code> 契约实现；后端提供该只读接口后自动接入，
                  当前不估算任何日期。
                </>
              ) : (
                <>
                  {payload?.reason ?? '五个小节都没有数据源，也没有规则抽取事件。'}
                  。先运行 <code className="ldst-num">ldst events --month {month}</code> 生成本地结果，再点「刷新」。
                </>
              )
            }
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button variant="secondary" onClick={reload}>刷新</Button>
                {!demo && (
                  <a
                    href={withParams('/calendar', { demo: '1', state: forced })}
                    className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text hover:bg-surface-3"
                  >
                    查看演示数据
                  </a>
                )}
              </div>
            }
          />
        </Card>
        {payload && <SectionsCard sections={payload.sections} />}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="warn" title="部分小节降级">
          {payload.reason ?? '有数据源的小节返回了降级结果'}。降级小节仍在原位展示，不补造日期。
        </Banner>
      )}

      <div className="grid grid-cols-[minmax(0,1fr)_360px] items-start gap-4 desk:grid-cols-[minmax(0,1fr)_400px]">
        <MonthGrid
          month={month}
          days={days}
          byDay={byDay}
          selected={day}
          onSelect={(date) => setParam('day', date)}
        />
        <DayPanel day={day} data={byDay.get(day)} demo={demo} forced={forced} />
      </div>

      <SectionsCard sections={payload.sections} />
    </div>
  )
}
