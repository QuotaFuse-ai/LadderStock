import { useCallback, useEffect, useRef, useState } from 'react'
import { CalendarClock, Loader2, Play, RefreshCw } from 'lucide-react'

import { ApiError } from '../../../api'
import {
  loadEventStudy,
  type EventStudy,
  type EventStudyEvent,
  type EventStudyLoader,
  type EventStudySample,
  type EventStudyType,
} from '../../../api/research'
import { cn } from '../../../lib/cn'
import {
  MISSING,
  TONE_TEXT_CLASS,
  countOrMissing,
  formatChangePct,
  formatMoney,
} from '../../../lib/format'
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  SectionCard,
  type Column,
} from '../../ui'

/**
 * 区块 G：事件回看（QUOT-451）。
 *
 * 数据只来自 `GET /api/research/{code}/event-study`（QUOT-446）：六份本地数据集
 * 对齐本地 000300 交易日历，每个事件给「事件日前 N 个交易日 → 后 M 个交易日」的
 * 窗口收益与超额。页面只展示接口算好的数：
 * - 均值只在 `sample.status === 'ok'` 时出现，样本不足如实写「不给平均」；
 * - 沪深300 基准缺失（`data_source_status = missing`）时拒绝出平均并说明缺什么；
 * - 增持 / 减持由同一份 `holder_trades` 按 `detail.direction` 拆成两枚 chip，
 *   接口只按「增持减持」合计给样本，拆方向后不自己求平均。
 *
 * 硬规则：缺数一律 `—`；0 条是真实读数，与「未接入」分开显示；不补 0、不插值。
 */

export interface EventSectionProps {
  code: string | null
  date: string
  className?: string
  /** 取数通道：默认走 HTTP，组件自证页与测试可注入替身 */
  loader?: EventStudyLoader
}

type Direction = '增持' | '减持'

interface Chip {
  key: string
  label: string
  dataset: string
  direction?: Direction
  dateLabel: string
}

/** 七枚 chip 的顺序照需求：业绩预告 / 股东增持 / 股东减持 / 回购 / 调研 / 龙虎榜 / 解禁。 */
export const EVENT_CHIPS: Chip[] = [
  { key: 'earnings_forecast', label: '业绩预告', dataset: 'earnings_preview', dateLabel: '公告日' },
  { key: 'holder_increase', label: '股东增持', dataset: 'holder_trades', direction: '增持', dateLabel: '公告日' },
  { key: 'holder_decrease', label: '股东减持', dataset: 'holder_trades', direction: '减持', dateLabel: '公告日' },
  { key: 'buyback', label: '回购方案', dataset: 'share_buyback', dateLabel: '首次公告日' },
  { key: 'survey', label: '机构调研', dataset: 'institution_survey', dateLabel: '公告日' },
  { key: 'dragon_tiger', label: '龙虎榜', dataset: 'dragon_tiger', dateLabel: '上榜日' },
  { key: 'lockup', label: '限售解禁', dataset: 'lockup_calendar', dateLabel: '解禁日' },
]

type ChipStats =
  | { kind: 'loading' }
  | { kind: 'unlinked' }
  | { kind: 'linked'; type: EventStudyType; events: EventStudyEvent[]; count: number | null }

const FIRST_ROWS = 10

const describe = (error: unknown): string =>
  error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error)

/** 方向字段缺失的事件不计入增持 / 减持任一侧，页面会说明还差多少条。 */
const directionOf = (event: EventStudyEvent): Direction | null => {
  const value = event.detail.direction
  return value === '增持' || value === '减持' ? value : null
}

const shortText = (value: unknown): string | null => {
  if (typeof value === 'string' && value.trim() !== '') return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

const sharesText = (value: unknown): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return `${Math.abs(value).toFixed(2)} 万股`
}

/** 上游占比原值可能是 12 位小数，页面只留 4 位，不改变原意。 */
const pctText = (value: unknown): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return String(Number(value.toFixed(4)))
}

/** 事件列：只挑该数据集真实有的字段拼一句，不补不猜。 */
function eventSummary(dataset: string, detail: Record<string, unknown>): string {
  const parts: string[] = []
  if (dataset === 'holder_trades') {
    const direction = shortText(detail.direction)
    if (direction) parts.push(direction)
    parts.push(shortText(detail.holder) ?? '股东未注明')
    const shares = sharesText(detail.change_shares_10k)
    if (shares) parts.push(shares)
    const ratio = pctText(detail.change_pct_total)
    if (ratio) parts.push(`占总股本 ${ratio}%`)
  } else if (dataset === 'dragon_tiger') {
    parts.push(shortText(detail.reason) ?? '上榜')
    const net = detail.net_amount
    if (typeof net === 'number' && Number.isFinite(net)) parts.push(`净额 ${formatMoney(net)}`)
  } else if (dataset === 'earnings_preview') {
    const kind = shortText(detail.forecast_type)
    const indicator = shortText(detail.indicator)
    if (kind) parts.push(kind)
    if (indicator) parts.push(indicator)
  } else if (dataset === 'institution_survey') {
    parts.push(shortText(detail.institution) ?? '机构调研')
    const receivers = shortText(detail.receivers)
    if (receivers) parts.push(`接待 ${receivers}`)
  } else if (dataset === 'share_buyback') {
    parts.push(shortText(detail.buyback_type) ?? '回购')
    const amount = detail.amount
    if (typeof amount === 'number' && Number.isFinite(amount)) parts.push(formatMoney(amount))
  } else if (dataset === 'lockup_calendar') {
    parts.push(shortText(detail.type) ?? '限售解禁')
    const shares = sharesText(detail.shares_10k)
    if (shares) parts.push(`解禁 ${shares}`)
    const ratio = pctText(detail.ratio_pct)
    if (ratio) parts.push(`占 ${ratio}%`)
  }
  if (parts.length === 0) {
    for (const value of Object.values(detail)) {
      const text = shortText(value)
      if (text) parts.push(text)
      if (parts.length >= 3) break
    }
  }
  return parts.join(' · ') || '—'
}

function Pct({ value }: { value: number | null }) {
  const change = formatChangePct(value === null ? null : value * 100)
  return (
    <span className={cn('ldst-num', TONE_TEXT_CLASS[change.tone])} title={change.missing ? '窗口不可算' : undefined}>
      {change.text}
    </span>
  )
}

function statsOf(chip: Chip, study: EventStudy | null): ChipStats {
  if (!study) return { kind: 'loading' }
  const type = study.eventTypes.find((item) => item.dataset === chip.dataset)
  if (!type) return { kind: 'unlinked' }
  if (!chip.direction) return { kind: 'linked', type, events: type.events, count: type.count }
  const events = type.events.filter((event) => directionOf(event) === chip.direction)
  return { kind: 'linked', type, events, count: events.length }
}

function countText(stats: ChipStats): string {
  if (stats.kind === 'loading') return '读取中'
  if (stats.kind === 'unlinked') return '未接入'
  if (stats.count === null) return MISSING
  return `${stats.count} 次`
}

/** 样本区：只有接口给了 `ok` 才出均值；其余如实说明为什么没有。 */
function SampleBlock({
  stats,
  sample,
  benchmarkMissing,
  splitNote,
}: {
  stats: Extract<ChipStats, { kind: 'linked' }>
  sample: EventStudySample
  benchmarkMissing: boolean
  splitNote: string | null
}) {
  if (benchmarkMissing) {
    return (
      <div className="rounded-xs border border-warn bg-warn-soft px-3 py-2 text-caption leading-body text-text-secondary">
        <b className="text-warn">沪深300 基准缺失，拒绝出平均。</b>
        {` 本地 index_daily 没有可用的 000300 交易日历，窗口两端对不齐，按口径不给均值；这不是「0 次」，也不是「0%」。`}
        {sample.reason && <span className="mt-0.5 block">{sample.reason}</span>}
      </div>
    )
  }
  if (sample.status !== 'ok') {
    const available = countOrMissing(sample.windowAvailable)
    const required = countOrMissing(sample.required)
    return (
      <div className="rounded-xs border border-border bg-surface-2 px-3 py-2 text-caption leading-body text-text-secondary">
        <b className="text-text">
          {stats.count === 0 ? '该类型 0 次，无可回看事件。' : `样本不足 ${available} 次，不给平均。`}
        </b>
        {` 口径：次数 ≥ ${required} 才给平均。`}
        {sample.reason && <span className="mt-0.5 block text-text-muted">{sample.reason}</span>}
        {splitNote && <span className="mt-0.5 block text-text-muted">{splitNote}</span>}
      </div>
    )
  }
  return (
    <div className="rounded-xs border border-border bg-surface-2 px-3 py-2 text-caption leading-body text-text-secondary">
      <b className="text-text">平均（接口现算，样本 {countOrMissing(sample.windowAvailable)} 次）</b>
      <div className="mt-1 flex flex-wrap gap-x-5 gap-y-1">
        <span>
          个股 <Pct value={sample.meanStockReturn} />
        </span>
        <span>
          沪深300 <Pct value={sample.meanBenchmarkReturn} />
        </span>
        <span>
          超额 <Pct value={sample.meanExcessReturn} />
        </span>
      </div>
      {splitNote && <span className="mt-0.5 block text-text-muted">{splitNote}</span>}
    </div>
  )
}

export function EventSection({ code, date, className, loader = loadEventStudy }: EventSectionProps) {
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  const [picked, setPicked] = useState<string>(EVENT_CHIPS[0].key)
  const [summary, setSummary] = useState<{ code: string; data: EventStudy } | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const summaryCtl = useRef<AbortController | null>(null)

  const [detail, setDetail] = useState<{ key: string; data: EventStudy } | null>(null)
  const [detailLoading, setDetailLoading] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<{ key: string; message: string } | null>(null)
  const detailCtl = useRef<AbortController | null>(null)
  const [showAll, setShowAll] = useState(false)

  const loadSummary = useCallback(() => {
    summaryCtl.current?.abort()
    if (!code) {
      setSummary(null)
      setSummaryError(null)
      setSummaryLoading(false)
      return
    }
    const controller = new AbortController()
    summaryCtl.current = controller
    setSummaryLoading(true)
    setSummaryError(null)
    loaderRef
      .current({ symbol: code }, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setSummary({ code, data })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setSummary(null)
        setSummaryError(describe(error))
      })
      .finally(() => {
        if (!controller.signal.aborted) setSummaryLoading(false)
      })
  }, [code])

  useEffect(() => {
    loadSummary()
    return () => summaryCtl.current?.abort()
  }, [loadSummary])

  useEffect(
    () => () => {
      detailCtl.current?.abort()
    },
    [],
  )

  // 换股票：上一次的明细作废，等用户对新标的重跑
  useEffect(() => {
    detailCtl.current?.abort()
    setDetail(null)
    setDetailError(null)
    setDetailLoading(null)
    setShowAll(false)
  }, [code])

  const study = summary && summary.code === code ? summary.data : null
  const chip = EVENT_CHIPS.find((item) => item.key === picked) ?? EVENT_CHIPS[0]
  const stats = statsOf(chip, study)
  const detailKey = `${code ?? ''}|${picked}`
  const detailData = detail && detail.key === detailKey ? detail.data : null
  const detailType = detailData
    ? (detailData.eventTypes.find((item) => item.dataset === chip.dataset) ?? null)
    : null
  const detailEvents = detailType
    ? chip.direction
      ? detailType.events.filter((event) => directionOf(event) === chip.direction)
      : detailType.events
    : []
  const detailBusy = detailLoading === detailKey
  const detailFailure = detailError && detailError.key === detailKey ? detailError.message : null

  // 基准缺失只看 000300 有没有日线：没事件时后端也会给 missing，不能混为一谈
  const benchmarkMissing =
    study !== null && (study.tradingDays === 0 || study.benchmarkRows === 0)
  const detailBenchmarkMissing =
    detailData !== null &&
    (detailData.tradingDays === 0 || detailData.benchmarkRows === 0)
  const pre = study?.windowBefore ?? null
  const post = study?.windowAfter ?? null
  const benchmark = study?.benchmark ?? '000300'
  const required = stats.kind === 'linked' ? stats.type.sample.required : null

  // 拆方向后确实没有方向字段的记录才提示（两枚 chip 合起来本就等于数据集总数）
  const undirected =
    chip.direction && stats.kind === 'linked'
      ? stats.type.events.filter((event) => directionOf(event) === null).length
      : 0
  const splitNote =
    chip.direction && stats.kind === 'linked'
      ? `接口按「${stats.type.label}」合计给样本（${countOrMissing(stats.type.sample.windowAvailable)} 次可算）${
          stats.type.sample.status === 'ok' ? '；均值是合计口径，拆方向后不单独出平均' : ''
        }。`
      : null

  const run = () => {
    if (!code || stats.kind !== 'linked') return
    detailCtl.current?.abort()
    const controller = new AbortController()
    detailCtl.current = controller
    const key = detailKey
    setDetailLoading(key)
    setDetailError(null)
    setShowAll(false)
    loaderRef
      .current({ symbol: code, sources: [chip.dataset] }, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setDetail({ key, data })
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setDetailError({ key, message: describe(error) })
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(null)
      })
  }

  const columns: Array<Column<EventStudyEvent & { rowKey: string }>> = [
    {
      key: 'date',
      header: chip.dateLabel,
      width: '112px',
      numeric: true,
      className: 'whitespace-nowrap',
      render: (row) => row.date,
    },
    {
      key: 'event',
      header: '事件',
      truncate: true,
      render: (row) => eventSummary(chip.dataset, row.detail),
      cellTitle: (row) => eventSummary(chip.dataset, row.detail),
    },
    {
      key: 'stock',
      header: '窗口收益',
      width: '104px',
      numeric: true,
      render: (row) => <Pct value={row.window.stockReturn} />,
      cellTitle: (row) => (row.window.stockReturn === null ? '窗口不可算' : ''),
    },
    {
      key: 'benchmark',
      header: '基准收益',
      width: '104px',
      numeric: true,
      render: (row) => <Pct value={row.window.benchmarkReturn} />,
      cellTitle: (row) => (row.window.benchmarkReturn === null ? '窗口不可算' : ''),
    },
    {
      key: 'excess',
      header: '超额',
      width: '104px',
      numeric: true,
      render: (row) => <Pct value={row.window.excessReturn} />,
      cellTitle: (row) => (row.window.excessReturn === null ? '窗口不可算' : ''),
    },
  ]

  const rows = detailEvents
    .map((event, index) => ({ ...event, rowKey: `${event.date}#${index}` }))
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))

  const visibleRows = showAll ? rows : rows.slice(0, FIRST_ROWS)
  const uncomputable = detailEvents.filter((event) => event.window.excessReturn === null).length

  const statusBadge = () => {
    if (!code) return <Badge tone="neutral">未选标的</Badge>
    if (summaryLoading) return <Badge tone="info">读取中</Badge>
    if (summaryError) return <Badge tone="danger">取数失败</Badge>
    if (!study) return <Badge tone="neutral">缺数</Badge>
    if (benchmarkMissing) return <Badge tone="warn">基准缺失</Badge>
    if (study.dataSourceStatus === 'partial') return <Badge tone="warn">部分可算</Badge>
    return <Badge tone="ok">已接入</Badge>
  }

  return (
    <SectionCard
      className={className}
      title="事件回看"
      subtitle={
        study && pre !== null && post !== null
          ? `${study.name ?? study.code} · 事件前 ${pre} → 后 ${post} 个交易日 · 基准沪深300（${benchmark}）`
          : '事件窗口相对沪深300 的累计超额（窗口参数以接口回显为准）'
      }
      actions={statusBadge()}
    >
      {!code && (
        <EmptyState
          compact
          icon={<CalendarClock aria-hidden="true" />}
          title="还没有选标的"
          description="在上方搜索框选中一只 A 股后，这里按七个事件类型回看窗口超额收益。"
        />
      )}

      {code && summaryError && (
        <div role="alert" className="mb-3 rounded-xs border border-danger bg-danger-soft px-3 py-2 text-body text-text">
          <p>事件回看取数失败：{summaryError}</p>
          <Button className="mt-2" variant="secondary" onClick={loadSummary} leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}>
            重试
          </Button>
        </div>
      )}

      {code && summaryLoading && !summary && (
        <p aria-busy="true" className="text-body text-text-muted">
          正在读取 {code} 的事件记录与 000300 交易日历…
        </p>
      )}

      {code && study && (
        <>
          {/* 口径必须上屏，且全部取自接口回显的参数（前 5 / 后 20 / ≥ 8） */}
          <p className="mb-3 rounded-xs border border-border bg-surface-2 px-3 py-2 text-caption leading-body text-text-secondary">
            口径：事件前 {pre === null ? MISSING : pre} 个交易日到后 {post === null ? MISSING : post} 个交易日
            相对沪深300 的累计超额收益，次数 ≥ {required === null ? MISSING : required} 才给平均。
            {study.formula ?? '窗口收益 = close[end] / close[start] - 1；超额 = 个股 - 000300'}。
            缺失一律 {MISSING}；不插值、不补 0。
          </p>

          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div role="group" aria-label="事件类型" className="flex min-w-0 flex-wrap gap-1 rounded border border-border p-half">
              {EVENT_CHIPS.map((item) => {
                const itemStats = statsOf(item, study)
                const selected = item.key === picked
                const unlinked = itemStats.kind === 'unlinked'
                const zero = itemStats.kind === 'linked' && itemStats.count === 0
                return (
                  <button
                    key={item.key}
                    type="button"
                    aria-pressed={selected}
                    disabled={unlinked}
                    title={
                      unlinked
                        ? '接口未返回该事件类型（未接入）'
                        : zero && itemStats.kind === 'linked'
                          ? item.direction && itemStats.type.count !== 0
                            ? `${item.label}：0 条带「${item.direction}」方向的记录（数据集共 ${itemStats.type.count} 条）`
                            : (itemStats.type.sample.reason ?? '上游对该代码返回 0 条')
                          : undefined
                    }
                    onClick={() => {
                      setPicked(item.key)
                      setShowAll(false)
                    }}
                    className={cn(
                      'inline-flex items-center gap-1.5 rounded-xs px-2.5 py-1 text-caption transition-colors duration-fast ease-ldst',
                      selected ? 'bg-accent-soft text-text' : 'text-text-secondary hover:bg-surface-3 hover:text-text',
                      unlinked && 'cursor-not-allowed opacity-50',
                    )}
                  >
                    <span>{item.label}</span>
                    <span className="ldst-num text-[11px] text-text-muted">{countText(itemStats)}</span>
                  </button>
                )
              })}
            </div>
            <Button
              className="ml-auto"
              variant="secondary"
              onClick={run}
              loading={detailBusy}
              disabled={stats.kind !== 'linked' || stats.count === 0}
              leadingIcon={
                detailData ? <RefreshCw className="size-3.5" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />
              }
            >
              {detailData ? '重新回看' : '开始回看'}
            </Button>
          </div>

          {stats.kind === 'unlinked' && (
            <p className="text-caption text-text-muted">
              该类型接口未返回（未接入），页面不会把「没接入」写成 0 次。
            </p>
          )}

          {stats.kind === 'linked' && stats.count === 0 && (
            <EmptyState
              compact
              icon={<CalendarClock aria-hidden="true" />}
              title={`${chip.label}：0 次`}
              description={stats.type.sample.reason ?? '接口对该代码返回 0 条事件'}
            />
          )}

          {stats.kind === 'linked' && stats.count !== 0 && undirected > 0 && (
            <p className="mb-2 text-caption text-text-muted">
              另有 {undirected} 条「{stats.type.label}」记录没有方向字段，未计入增持 / 减持。
            </p>
          )}

          {detailBusy && (
            <p role="status" className="mb-2 inline-flex items-center gap-1.5 text-body text-text-muted">
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              正在回看 {chip.label} 的窗口收益…
            </p>
          )}

          {detailFailure && (
            <p role="alert" className="mb-2 rounded-xs border border-danger bg-danger-soft px-3 py-2 text-body text-text">
              这次回看没做成：{detailFailure}
            </p>
          )}

          {detailData && !detailBusy && !detailFailure && !detailType && (
            <p className="text-caption text-text-muted">接口没有返回 {chip.label} 的明细（未接入）。</p>
          )}

          {detailData && detailType && (
            <div className="mt-2">
              <SampleBlock
                stats={{ kind: 'linked', type: detailType, events: detailEvents, count: detailEvents.length }}
                sample={detailType.sample}
                benchmarkMissing={detailBenchmarkMissing}
                splitNote={splitNote}
              />

              <div className="mt-3">
              <DataTable
                density="compact"
                columns={columns}
                rows={visibleRows}
                rowKey={(row) => row.rowKey}
                caption={`${chip.label}逐次结果（新的在前，共 ${detailEvents.length} 次）`}
                empty={
                  <span className="text-caption text-text-muted">
                    {chip.label} 在这只股票上没有可回看的事件。
                  </span>
                }
                expandable={{
                  label: '展开复算明细',
                  render: (row) => (
                    <div className="space-y-1 px-3 py-2 text-caption leading-body text-text-secondary">
                      {row.window.reason ? (
                        <p className="text-warn">窗口未算出来：{row.window.reason}</p>
                      ) : (
                        <>
                          <p className="ldst-num">
                            起 {row.window.start ?? MISSING} 收 {row.window.stockStartClose ?? MISSING}
                            {row.window.start && row.window.end ? ' → ' : '；'}
                            止 {row.window.end ?? MISSING} 收 {row.window.stockEndClose ?? MISSING}
                            {' ｜ '}沪深300 起 {row.window.benchmarkStartClose ?? MISSING} → 止{' '}
                            {row.window.benchmarkEndClose ?? MISSING}
                          </p>
                          <p className="ldst-num">
                            个股 = {row.window.stockEndClose ?? MISSING} / {row.window.stockStartClose ?? MISSING} − 1 ={' '}
                            <Pct value={row.window.stockReturn} />；基准 ={' '}
                            {row.window.benchmarkEndClose ?? MISSING} / {row.window.benchmarkStartClose ?? MISSING} − 1 ={' '}
                            <Pct value={row.window.benchmarkReturn} />；超额 = 个股 − 基准 = <Pct value={row.window.excessReturn} />
                          </p>
                        </>
                      )}
                      <p className="text-text-muted">口径：{detailData.formula ?? '窗口收益 = close[end] / close[start] - 1；超额 = 个股 - 000300'}</p>
                    </div>
                  ),
                }}
              />

              {rows.length > FIRST_ROWS && (
                <button
                  type="button"
                  onClick={() => setShowAll((value) => !value)}
                  className="mt-1 text-caption text-accent hover:underline"
                >
                  {showAll ? '收起' : `显示全部 ${rows.length} 次`}
                </button>
              )}
              {uncomputable > 0 && (
                <p className="mt-1 text-caption text-text-muted">
                  其中 {uncomputable} 次窗口不可算（基准覆盖不足 / 个股缺窗口边界收盘 / 停牌），已在明细里标注，未插值。
                </p>
              )}
              </div>
            </div>
          )}

          <p className="mt-3 space-y-0.5 border-t border-border pt-2 text-caption leading-body text-text-muted">
            <span className="block">
              来源：本地 daily_k（{study.name ?? study.code}）+ index_daily（沪深300 {benchmark}），
              基准截止 {study.dataAsOf ?? MISSING}
              {study.fetchedAt ? ` · 取数 ${study.fetchedAt.slice(0, 16).replace('T', ' ')}` : ''}；
              交易日历 {countOrMissing(study.tradingDays)} 天。
            </span>
            <span className="block">
              研究日 {date} 不参与窗口截断：event-study 没有 date 参数，按本地最新交易日回放。
              只描述过去，不预测，不构成买卖提示。
            </span>
          </p>
        </>
      )}
    </SectionCard>
  )
}
