import { useCallback, useMemo, useState } from 'react'

import { Banner, Button, DataTable, EmptyState, MetricCard, SkeletonText, type Column } from '../../components/ui'
import { describeApiError } from '../../api'
import { loadDebate, loadDebateStatus, startSenateAnalysis, type DebateView } from '../../api/debate'
import { useAsync } from '../../lib/useAsync'

/**
 * F7 多空对照 tab：读 StockSenate 已落盘的一轮合议，给出「多空对照 + 门控结论」。
 *
 * 三条硬口径（与后端 `research/debate.py` 一致）：
 *
 * 1. **门控优先**：质量分 / 有效论点不足时，只出「为什么不够格」，绝不用空数组冒充辩论结论；
 * 2. **写反向不写反**：`bull_rebuttals` 是"多头反驳空头"，`bear_rebuttals` 是"空头反驳多头"，
 *    表格里的来源角色必须照这个方向标注；
 * 3. **不触发分钟级动作**：本 tab 只读；要跑新合议走「起一轮合议」按钮（后台任务），
 *    页面明确告知"小时级/分钟级"，不假装同步返回结果。
 */

interface ClashRow {
  key: string
  side: string
  point: string
  source: string
}

const CLASH_COLUMNS: Array<Column<ClashRow>> = [
  { key: 'side', header: '立场', width: 'w-24', render: (row) => row.side },
  { key: 'point', header: '要点', render: (row) => <span className="text-body leading-body">{row.point}</span> },
  {
    key: 'source',
    header: '来源角色',
    width: 'w-40',
    render: (row) => <span className="text-caption text-text-secondary">{row.source}</span>,
  },
]

function clashRows(view: DebateView): ClashRow[] {
  const rows: ClashRow[] = []
  const push = (side: string, point: string, source: string) => {
    rows.push({ key: `${side}-${rows.length}`, side, point, source })
  }
  for (const point of view.bull.points) push('多头', point, '研究员·多头')
  for (const point of view.bear.points) push('空头', point, '研究员·空头')
  // 方向别写反：bear_rebuttals = 空头反驳多头
  for (const point of view.bearRebuttals) push('空方反驳', point, '空头 → 多头')
  for (const point of view.bullRebuttals) push('多方反驳', point, '多头 → 空头')
  return rows
}

function formatPercent(value: number | null): string {
  if (value === null) return '—'
  return `${Math.round(value * 100)}%`
}

function GateBanner({ view }: { view: DebateView }) {
  const gate = view.gate
  const detail = [
    `质量 ${gate.score === null ? '缺失' : gate.score.toFixed(1)} / 阈值 ${gate.threshold}`,
    `多头 ${gate.bull_points} · 空头 ${gate.bear_points}（各需 ≥ ${gate.min_points}）`,
  ].join('；')
  if (gate.passed) {
    return (
      <Banner tone="ok" title="门控通过：多空论点与交锋都够格">
        {detail}
      </Banner>
    )
  }
  return (
    <Banner tone="warn" title="这份报告不够格出多空对照">
      <span className="block text-caption text-text-secondary">{detail}</span>
      {gate.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-caption text-text-secondary">
          {gate.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </Banner>
  )
}

function MetricRow({ view }: { view: DebateView }) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <MetricCard label="裁决" value={view.ratingLabel ?? '—'} unit={view.action ?? undefined} />
      <MetricCard label="视野" value={view.horizon && view.horizon !== 'unknown' ? view.horizon : '—'} unit={`置信 ${formatPercent(view.confidence)}`} />
      <MetricCard label="参与者" value={String(view.participants)} unit={`失败 ${view.failedRoles.length}`} />
    </div>
  )
}

export default function DebateTab({ code, date }: { code: string | null; date: string }) {
  const [starting, setStarting] = useState(false)
  const [startNote, setStartNote] = useState<string | null>(null)

  // 先按当前研究日期取；那天没合议报告就回退到最近一份（研究日期与合议日期常差一天）
  const debateRequest = useAsync<DebateView | null>(
    async (signal) => {
      if (!code) return null
      const exact = await loadDebate(code, date, signal)
      if (exact) return exact
      return loadDebate(code, null, signal)
    },
    [code, date],
    { enabled: Boolean(code) },
  )

  const statusRequest = useAsync(
    (signal) => loadDebateStatus(signal),
    [],
    { enabled: true },
  )

  const start = useCallback(async () => {
    if (!code) return
    setStarting(true)
    setStartNote(null)
    try {
      const { taskId } = await startSenateAnalysis(code, date)
      setStartNote(
        taskId
          ? `已提交后台合议（任务 ${taskId}）。一轮真实合议是分钟级，跑完点「刷新」就能读到新报告。`
          : '已提交后台合议。一轮真实合议是分钟级，跑完点「刷新」就能读到新报告。',
      )
    } catch (error) {
      setStartNote(describeApiError(error).detail)
    } finally {
      setStarting(false)
    }
  }, [code, date])

  const rows = useMemo(() => (debateRequest.data ? clashRows(debateRequest.data) : []), [debateRequest.data])
  const view = debateRequest.data

  const refreshAll = () => {
    debateRequest.reload()
    statusRequest.reload()
  }

  if (!code) {
    return <EmptyState title="先选一只标的" description="多空对照挂在个股上：从页头搜索换股后回来。" />
  }

  if (debateRequest.status === 'loading') {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <SkeletonText lines={3} />
        <SkeletonText lines={6} />
      </div>
    )
  }

  if (debateRequest.status === 'error') {
    const info = describeApiError(debateRequest.error)
    return (
      <Banner tone="danger" title={info.title} action={<Button variant="secondary" onClick={refreshAll}>重试</Button>}>
        {info.detail}
      </Banner>
    )
  }

  if (!view) {
    const availability = statusRequest.data
    return (
      <EmptyState
        title={`${code} 还没有多空合议报告`}
        description={
          availability
            ? availability.homeExists
              ? `StockSenate 已就位（${availability.reports} 份历史报告）。起一轮会在后台跑完再落报告；跑完回本页刷新即可读到。`
              : '本机没找到 StockSenate（合议模块未安装或路径不对），暂时无法起新合议；可以先看六段深度研究。'
            : '正在确认合议模块是否就位…'
        }
        action={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" onClick={start} loading={starting} disabled={!availability?.homeExists}>
              起一轮合议
            </Button>
            <Button variant="secondary" onClick={refreshAll}>
              刷新
            </Button>
          </div>
        }
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <GateBanner view={view} />
      {startNote && <Banner tone="info">{startNote}</Banner>}
      <MetricRow view={view} />

      <DataTable<ClashRow>
        columns={CLASH_COLUMNS}
        rows={rows}
        rowKey={(row) => row.key}
        caption={
          rows.length > 0
            ? `${view.code ?? code} · ${view.date ?? date} · 多空对照（${rows.length} 条）`
            : '多空对照'
        }
        empty={
          <span className="text-body text-text-secondary">
            本份报告没有任何可用论点与反驳：各角色结构化输出校验失败，见下方「失败角色」。
          </span>
        }
      />

      {view.flipConditions.length > 0 && (
        <div className="rounded-card border border-line bg-surface p-4">
          <p className="text-subtitle font-medium text-text">翻转条件</p>
          <ul className="mt-2 list-disc pl-4 text-body leading-body text-text-secondary">
            {view.flipConditions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {view.failedRoles.length > 0 && (
        <div className="rounded-card border border-line bg-surface p-4">
          <p className="text-subtitle font-medium text-text">失败角色（{view.failedRoles.length}）</p>
          <ul className="mt-2 flex flex-col gap-1 text-caption text-text-secondary">
            {view.failedRoles.map((item) => (
              <li key={item.role}>
                <span className="text-text">{item.role}</span>：{item.reason || '未给原因'}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 text-caption text-text-secondary">
        <span>
          报告 {view.runId ?? '—'} · 生成于 {view.generatedAt ?? '—'}
        </span>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={refreshAll}>
            刷新
          </Button>
          <Button variant="secondary" onClick={start} loading={starting}>
            再跑一轮合议
          </Button>
        </div>
      </div>
      {view.blocksError && <Banner tone="warn" title="块装配失败">{view.blocksError}</Banner>}
    </div>
  )
}
