import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays, Database } from 'lucide-react'

import {
  isRadarEndpointMissing,
  TIMELINE_KIND_LABEL,
  TIMELINE_KIND_ORDER,
  type TimelineDays,
  type TimelineEvent,
  type TimelineKind,
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
  Segmented,
  SourceStatusBadge,
  type BadgeTone,
  type SegmentedOption,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { readCalendarDays, saveCalendarDays } from '../../lib/calendarPrefs'
import { formatTime } from '../../lib/format'
import { useDemoEnabled, useTimelineView } from './useRadarView'

/**
 * QUOT-436 事件时间轴（`/calendar`）。
 *
 * 只排来源写出来的日期：自选 + 持仓的未来 7 / 30 / 90 天解禁、业绩预告、
 * 原文写了日期的公告抽取。没有数据源的类别（财报预约披露日 / 股东大会 /
 * 政策发布窗口）只出现在「没查到的」里——不估算、不拿相邻日期补、不给买卖建议。
 */

const DAY_OPTIONS: readonly SegmentedOption<'7' | '30' | '90'>[] = [
  { value: '7', label: '7 天' },
  { value: '30', label: '30 天' },
  { value: '90', label: '90 天' },
]

const KIND_TONE: Record<TimelineKind, BadgeTone> = {
  lockup: 'warn',
  earnings: 'info',
  disclosure: 'neutral',
}

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const

function readDays(): '7' | '30' | '90' {
  return String(readCalendarDays()) as '7' | '30' | '90'
}

/** 「10月12日 周一 · 4 天后」；基准日是后端窗口起点（北京时间日期），不拿浏览器时区猜。 */
export function dayLabel(date: string, today: string): { main: string; rel: string } {
  const d = new Date(`${date}T00:00:00Z`)
  const t = new Date(`${today}T00:00:00Z`)
  if (Number.isNaN(d.getTime()) || Number.isNaN(t.getTime())) return { main: date, rel: '' }
  const diff = Math.round((d.getTime() - t.getTime()) / 86_400_000)
  const rel = diff === 0 ? '今天' : diff === 1 ? '明天' : diff > 1 ? `${diff} 天后` : `${-diff} 天前`
  return { main: `${d.getUTCMonth() + 1}月${d.getUTCDate()}日 ${WEEK[d.getUTCDay()]}`, rel }
}

/** 后端已按日期升序；这里只把同一天收拢成一组，不重排。 */
export function groupByDate(events: TimelineEvent[]): Array<{ date: string; items: TimelineEvent[] }> {
  const groups: Array<{ date: string; items: TimelineEvent[] }> = []
  for (const event of events) {
    const last = groups.at(-1)
    if (last && last.date === event.date) last.items.push(event)
    else groups.push({ date: event.date, items: [event] })
  }
  return groups
}

function EventRow({ event }: { event: TimelineEvent }) {
  return (
    <li className="flex flex-col gap-1 rounded border border-border bg-surface-2 p-2 desk:flex-row desk:items-start desk:gap-3">
      <Badge tone={KIND_TONE[event.kind]} className="w-fit shrink-0">
        {TIMELINE_KIND_LABEL[event.kind]}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-baseline gap-2 text-body text-text">
          <span className="font-medium">{event.name || event.symbol}</span>
          {event.name && <span className="ldst-num text-caption text-text-secondary">{event.symbol}</span>}
          {event.estimated && <Badge tone="warn">预估</Badge>}
          <span className="min-w-0 flex-1">{event.title}</span>
        </p>
        {event.detail.length > 0 && (
          <p className="mt-0.5 text-caption leading-body text-text-secondary">
            {event.detail.join(' · ')}
          </p>
        )}
      </div>
      <span className="shrink-0 text-caption text-text-muted desk:text-right">
        <code className="ldst-num">{event.source}</code>
        {event.fetched_at ? (
          <span className="block">{formatTime(event.fetched_at)} 取</span>
        ) : (
          <span className="block">取数时刻未记录</span>
        )}
      </span>
    </li>
  )
}

function OverdueCard({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) return null
  return (
    <Card>
      <CardHeader
        title={`已经过了日子、还没处理（${events.length}）`}
        subtitle="这些日子的来源事件仍然在；本仓没有「已处理」标记，不替用户判断"
      />
      <ul className="flex flex-col gap-2 p-4">
        {events.map((event) => (
          <li
            key={`${event.date}-${event.kind}-${event.symbol}-${event.ref}`}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-body"
          >
            <span className="ldst-num text-caption text-warn">{event.date}</span>
            <Badge tone={KIND_TONE[event.kind]}>{TIMELINE_KIND_LABEL[event.kind]}</Badge>
            <span className="font-medium text-text">{event.name || event.symbol}</span>
            <span className="text-text">{event.title}</span>
            {event.detail.length > 0 && (
              <span className="text-caption text-text-secondary">{event.detail.join(' · ')}</span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function GapsCard({ gaps }: { gaps: string[] }) {
  if (gaps.length === 0) return null
  return (
    <Card>
      <CardHeader
        title={`没查到的（${gaps.length}）`}
        subtitle="时间轴没有不代表真的没有；这些来源这次没取到或本来就没有数据源"
      />
      <ul className="flex flex-col gap-2 p-4">
        {gaps.map((gap) => (
          <li key={gap} className="text-caption leading-body text-text-secondary">
            {gap}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function KindFilter({
  events,
  excluded,
  onToggle,
}: {
  events: TimelineEvent[]
  excluded: Set<TimelineKind>
  onToggle: (kind: TimelineKind) => void
}) {
  const counts = useMemo(() => {
    const result = new Map<TimelineKind, number>()
    for (const event of events) result.set(event.kind, (result.get(event.kind) ?? 0) + 1)
    return result
  }, [events])
  const present = TIMELINE_KIND_ORDER.filter((kind) => (counts.get(kind) ?? 0) > 0)
  if (present.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-2 text-caption">
      <span className="text-text-muted">显示：</span>
      {present.map((kind) => {
        const active = !excluded.has(kind)
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={active}
            onClick={() => onToggle(kind)}
            className={cn(
              'inline-flex h-6 items-center gap-1 rounded-pill border px-3 font-medium transition-colors duration-fast ease-ldst',
              active
                ? 'border-accent bg-accent-soft text-accent'
                : 'border-border text-text-secondary hover:text-text',
            )}
          >
            {TIMELINE_KIND_LABEL[kind]}
            <span className="ldst-num">{counts.get(kind) ?? 0}</span>
          </button>
        )
      })}
    </div>
  )
}

export default function CalendarScreen() {
  const demo = useDemoEnabled()
  const [daysKey, setDaysKey] = useState<'7' | '30' | '90'>(readDays)
  const [excluded, setExcluded] = useState<Set<TimelineKind>>(() => new Set())

  const days = Number(daysKey) as TimelineDays
  const view = useTimelineView(days)
  const { forced, state, payload, error, reload } = view

  const groups = useMemo(() => {
    const visible = (payload?.events ?? []).filter((event) => !excluded.has(event.kind))
    return groupByDate(visible)
  }, [payload, excluded])

  const toggleKind = (kind: TimelineKind) => {
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
  }

  const header = (
    <PageHeader
      title="事件日历"
      subtitle={
        payload
          ? `${payload.window.from} 至 ${payload.window.to}（北京时间）· 自选和持仓共 ${payload.symbols.length} 只 · 数据时点 ${payload.generated_at.slice(0, 16).replace('T', ' ')}`
          : '自选和持仓接下来的公司事件；只列来源给出的日期，不打分、不给买卖建议'
      }
      badge={
        <div className="flex flex-wrap items-center gap-2">
          <DemoStateBadge state={forced} />
          {payload && <SourceStatusBadge status={payload.data_source_status} />}
        </div>
      }
      refresh={{ onClick: reload, disabled: state === 'loading' }}
      actions={
        <Segmented
          options={DAY_OPTIONS}
          value={daysKey}
          ariaLabel="看多少天"
          size="sm"
          onChange={(next) => {
            setDaysKey(next)
            saveCalendarDays(Number(next) as TimelineDays)
          }}
        />
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
            description="启动 ldst run 后自动接入 /api/events/timeline；未连接时时间轴不显示任何估算日期。"
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
          title="事件时间轴取数失败"
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
            title={
              endpointMissing
                ? '接口未接入'
                : payload
                  ? '还没有自选或持仓'
                  : '没有可用的时间轴'
            }
            description={
              endpointMissing ? (
                <>
                  页面已按 <code className="ldst-num">GET /api/events/timeline</code> 契约实现；
                  后端提供该只读接口后自动接入，当前不估算任何日期。
                </>
              ) : (
                <>
                  时间轴只排自选与持仓的公司事件。先去加几只自选，回来就能看到解禁、
                  业绩预告与公告里写明的日期；没查到的来源会在下面逐条说明。
                </>
              )
            }
            action={
              endpointMissing ? (
                <Button variant="secondary" onClick={reload}>刷新</Button>
              ) : (
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <Link
                    to="/my-stocks?tab=watch"
                    className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text transition-colors duration-fast ease-ldst hover:bg-surface-3"
                  >
                    去加自选
                  </Link>
                  <Button variant="secondary" onClick={reload}>刷新</Button>
                </div>
              )
            }
          />
        </Card>
        <GapsCard gaps={payload?.gaps ?? []} />
        {demo && <p className="text-caption text-text-muted">当前是演示数据（?demo=1）。</p>}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="warn" title="部分来源取数失败">
          失败来源已写进「没查到的」，本轮没有补任何日期。
        </Banner>
      )}

      <KindFilter events={payload.events} excluded={excluded} onToggle={toggleKind} />

      <OverdueCard events={payload.overdue} />

      <Card>
        <CardHeader
          title="时间轴"
          subtitle={`${payload.window.days} 天 · 只列来源给出的日期，同一天按来源顺序排列`}
        />
        <div className="flex flex-col gap-4 p-4">
          {groups.length === 0 ? (
            <p className="py-6 text-center text-body text-text-muted">
              {payload.events.length === 0
                ? `这 ${payload.window.days} 天里没有查到事件。`
                : '选中的类型在窗口内没有事件。'}{' '}
              {payload.gaps.length > 0 && '下面有没取到的来源，不代表那几只没有事件。'}
            </p>
          ) : (
            <ol className="flex flex-col gap-5">
              {groups.map((group) => {
                const label = dayLabel(group.date, payload.window.from)
                return (
                  <li key={group.date}>
                    <p className="mb-2 flex items-baseline gap-2 text-body">
                      <span className="font-medium text-text">{label.main}</span>
                      <span
                        className={cn(
                          'text-caption',
                          label.rel === '今天' || label.rel === '明天'
                            ? 'text-accent'
                            : 'text-text-muted',
                        )}
                      >
                        {label.rel}
                      </span>
                    </p>
                    <ul className="flex flex-col gap-2">
                      {group.items.map((event) => (
                        <EventRow
                          key={`${event.kind}-${event.symbol}-${event.ref}-${event.title}`}
                          event={event}
                        />
                      ))}
                    </ul>
                  </li>
                )
              })}
            </ol>
          )}
        </div>
      </Card>

      <GapsCard gaps={payload.gaps} />
    </div>
  )
}
