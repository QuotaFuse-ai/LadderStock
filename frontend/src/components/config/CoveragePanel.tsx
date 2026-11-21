import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'

import {
  fetchCoverageBoard,
  fetchCoveragePlan,
  runCoverageBackfill,
  type CoverageBackfill,
  type CoverageBoard,
  type CoveragePlan,
  type CoverageRow,
} from '../../api/coverage'
import { Button, Card, CardBody, CardHeader } from '../ui'

/**
 * F12 数据覆盖看板（挂在 `/config` 的「数据覆盖」页）。
 *
 * 三件事必须一眼看到：
 * 1. **已注册 ≠ 已落盘**：`sync.yaml` 声明 34 个，`DATASET_CATALOG` 目录 62 个，
 *    真正有行的是第三个数字——三个数分开显示，不合并成一个「覆盖 54%」；
 * 2. **每个缺口的原话**：行数 0 是「从未入库」，有行但最新日期旧是「更新过旧」，
 *    逐票但池里只盖一半是「池内覆盖不全」——三种修法不同，不能糊成「异常」；
 * 3. **补采是两段式**：点「补采」先出计划（会请求哪个源、多少只），再确认才真跑；
 *    计划里超上限直接拒绝（后端 400 原话照显示），前端不自己截断。
 *
 * 事件型数据集（龙虎榜、研报、回购等）池内覆盖低是正常的，页面写「事件型」而不是标红。
 */

const STATE_TONE: Record<string, string> = {
  ok: 'text-emerald-600',
  partial: 'text-amber-600',
  stale: 'text-amber-600',
  empty: 'text-rose-600',
  never: 'text-rose-600',
  unregistered: 'text-text-muted',
}

/** 状态列只有一格宽：标签必须短，完整说法放 title 与行下 reason */
const STATE_SHORT: Record<string, string> = {
  ok: '正常',
  partial: '未盖满',
  stale: '过旧',
  empty: '零行',
  never: '未入库',
  unregistered: '未接入',
}

function NumberBlock({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border p-3">
      <span className="text-caption text-text-muted">{label}</span>
      <span className="ldst-num text-title font-medium text-text">{value}</span>
      {hint ? <span className="text-caption text-text-muted">{hint}</span> : null}
    </div>
  )
}

function coverageText(row: CoverageRow): string {
  if (!row.pool_relevant) return '—'
  if (row.coverage === null) return '—'
  return `${row.pool_covered}/${row.pool_codes}（${(row.coverage * 100).toFixed(1)}%）`
}

function RowLine({
  row,
  labels,
  onPlan,
  busy,
}: {
  row: CoverageRow
  labels: Record<string, string>
  onPlan: (row: CoverageRow) => void
  busy: boolean
}) {
  const label = labels[row.state] ?? row.state
  const tone = STATE_TONE[row.state] ?? 'text-text-muted'
  const registeredHint = row.registered ? '' : ' · 未注册'
  return (
    <div className="border-b border-border py-2 last:border-b-0" data-testid={`coverage-row-${row.dataset}`}>
      <div className="grid grid-cols-12 items-center gap-2 text-body">
        <div className="col-span-4 flex flex-col">
          <span className="font-medium text-text">{row.dataset}</span>
          <span className="text-caption text-text-muted">
            {row.primary || '—'}
            {registeredHint}
          </span>
        </div>
        <span className="col-span-2 ldst-num text-right text-text-muted">
          {row.rows.toLocaleString()} 行
        </span>
        <span className="col-span-2 ldst-num text-right text-text-muted">{row.latest ?? '—'}</span>
        <span className="col-span-2 ldst-num text-right text-text-muted">{coverageText(row)}</span>
        <span className={`col-span-1 text-right text-caption ${tone}`} title={label}>
          {STATE_SHORT[row.state] ?? label}
        </span>
        <div className="col-span-1 flex justify-end">
          {row.registered ? (
            <Button size="md" variant="ghost" disabled={busy} onClick={() => onPlan(row)}>
              补采
            </Button>
          ) : (
            <span className="text-caption text-text-muted" title="没在 sync.yaml 注册主源，补采无处可去">
              未注册
            </span>
          )}
        </div>
      </div>
      {row.reason ? <p className="mt-1 text-caption text-text-muted">{row.reason}</p> : null}
    </div>
  )
}

function PlanBox({
  plan,
  result,
  busy,
  onConfirm,
  onCancel,
}: {
  plan: CoveragePlan
  result: CoverageBackfill | null
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Card>
      <CardHeader
        title={`补采 ${plan.dataset}`}
        subtitle="先看计划：这一批会请求哪个源、多少只；确认后才真跑"
      />
      <CardBody>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <NumberBlock label="主源" value={plan.primary || '—'} />
          <NumberBlock label="备源" value={plan.fallback.length ? plan.fallback.join(' / ') : '无'} />
          <NumberBlock
            label="本次代码数"
            value={plan.per_code ? String(plan.code_count) : '不按票'}
            hint={`单批上限 ${plan.max_codes}`}
          />
          <NumberBlock label="请求规模" value={plan.requests_hint} />
        </div>
        {plan.per_code && plan.codes.length ? (
          <p className="mt-3 text-caption text-text-muted">
            代码：{plan.codes.slice(0, 12).join('、')}
            {plan.codes.length > 12 ? ` …（共 ${plan.codes.length} 只）` : ''}
          </p>
        ) : null}

        {result ? (
          <div className="mt-3 border-t border-border pt-3" data-testid="coverage-result">
            <p className="text-body text-text">
              <span className={result.status === 'failed' ? 'text-rose-600' : 'text-emerald-600'}>
                {result.status}
              </span>
              {' · '}
              {result.detail || result.hint || '（无 detail）'}
            </p>
            {result.status !== 'planned' ? (
              <p className="mt-1 text-caption text-text-muted">
                新入库 {result.rows_stored ?? '—'} 行 · 表内合计 {result.total_rows ?? '—'}
                {result.elapsed_ms ? ` · 耗时 ${result.elapsed_ms} ms` : ''}
                {result.source ? ` · 源 ${result.source}` : ''}
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="mt-3 flex items-center gap-2">
          <Button size="md" disabled={busy || result?.status === 'planned'} onClick={onConfirm}>
            确认补采（会真发请求）
          </Button>
          <Button size="md" variant="ghost" disabled={busy} onClick={onCancel}>
            关闭
          </Button>
          <span className="text-caption text-text-muted">
            只会跑这一个数据集、一批次；不做全量、不并发压源。
          </span>
        </div>
      </CardBody>
    </Card>
  )
}

export default function CoveragePanel() {
  const [board, setBoard] = useState<CoverageBoard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [plan, setPlan] = useState<CoveragePlan | null>(null)
  const [result, setResult] = useState<CoverageBackfill | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setBoard(await fetchCoverageBoard())
    } catch (exc) {
      setError(String(exc))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const rows = useMemo(() => {
    if (!board) return []
    return showAll ? board.datasets : board.gaps
  }, [board, showAll])

  const openPlan = async (row: CoverageRow) => {
    setBusy(true)
    setNotice(null)
    setResult(null)
    try {
      setPlan(await fetchCoveragePlan(row.dataset))
    } catch (exc) {
      setPlan(null)
      setNotice(`${row.dataset} 补不了：${String(exc)}`)
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    if (!plan) return
    setBusy(true)
    setNotice(null)
    try {
      const outcome = await runCoverageBackfill(plan.dataset, { execute: true })
      setResult(outcome)
      setNotice(
        outcome.status === 'ok' || outcome.status === 'degraded'
          ? `${plan.dataset} 补采完成，重新扫一遍看板`
          : `${plan.dataset} 补采结果：${outcome.status}`,
      )
      await load()
    } catch (exc) {
      setNotice(`${plan.dataset} 补采失败：${String(exc)}`)
    } finally {
      setBusy(false)
    }
  }

  if (!board) {
    return (
      <p className="text-caption text-text-muted">
        {error ? `读取失败：${error}` : '读取中…（现场扫 62 张表，约 1 秒）'}
      </p>
    )
  }

  const counts = board.summary.counts
  const unmet = board.summary.needs_attention

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <NumberBlock
          label="数据集目录"
          value={String(board.summary.total)}
          hint={`sync.yaml 声明 ${board.declared} 个`}
        />
        <NumberBlock label="正常" value={String(board.summary.ok)} hint="有行、够新、池内盖满" />
        <NumberBlock
          label="需要处理"
          value={String(unmet)}
          hint={`缺口 ${unmet} 个 · 未接入 ${board.summary.unregistered} 个不计入`}
        />
        <NumberBlock
          label="代码池"
          value={String(board.pool.size)}
          hint={`覆盖率分母 · 生成 ${board.generated_at.slice(11, 19)}`}
        />
      </div>

      <p className="text-caption text-text-muted">
        {Object.entries(counts)
          .filter(([, value]) => value > 0)
          .map(([state, value]) => `${board.labels[state] ?? state} ${value}`)
          .join(' · ')}
        {' · '}耗时 {board.elapsed_ms} ms
      </p>

      {notice ? <p className="text-caption text-text">{notice}</p> : null}
      {plan ? (
        <PlanBox
          plan={plan}
          result={result}
          busy={busy}
          onConfirm={() => void confirm()}
          onCancel={() => {
            setPlan(null)
            setResult(null)
          }}
        />
      ) : null}

      <Card>
        <CardHeader
          title={showAll ? `全部 ${board.datasets.length} 个数据集` : `${board.gaps.length} 个缺口，按严重度排序`}
          subtitle="行数 / 最新日期 / 池内覆盖都从本机 DuckDB 现算；事件型数据集覆盖低属正常"
          actions={
            <div className="flex items-center gap-2">
              <Button size="md" variant="ghost" onClick={() => setShowAll((value) => !value)}>
                {showAll ? '只看缺口' : '看全部'}
              </Button>
              <Button size="md" variant="ghost" disabled={loading} onClick={() => void load()}>
                <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
                重扫
              </Button>
            </div>
          }
        />
        <CardBody>
          <div className="grid grid-cols-12 gap-2 border-b border-border pb-2 text-caption text-text-muted">
            <span className="col-span-4">数据集 / 主源</span>
            <span className="col-span-2 text-right">行数</span>
            <span className="col-span-2 text-right">最新</span>
            <span className="col-span-2 text-right">池内覆盖</span>
            <span className="col-span-1 text-right">状态</span>
            <span className="col-span-1 text-right">操作</span>
          </div>
          {rows.map((row) => (
            <RowLine
              key={row.dataset}
              row={row}
              labels={board.labels}
              onPlan={(item) => void openPlan(item)}
              busy={busy}
            />
          ))}
          {rows.length === 0 ? (
            <p className="py-3 text-caption text-text-muted">没有缺口——62 个数据集都有行、够新、池内盖满。</p>
          ) : null}
        </CardBody>
      </Card>

      <p className="text-caption text-text-muted">
        补采走 `cli/sync.py` 的真实通道：provider_registry 的限流 / 冷却 / 降级都在那层，本页只负责「选哪个、多少只、跑不跑」。
        单批 ≤100 只，超了后端直接拒绝并提示分批。
      </p>
    </div>
  )
}
