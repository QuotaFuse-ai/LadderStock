import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, RefreshCw } from 'lucide-react'

import {
  applyPool,
  applyPoolCodes,
  fetchBackfillConfig,
  fetchPool,
  fetchProgress,
  fetchSyncConfig,
  type BackfillPayload,
  type FeedRun,
  type PoolPayload,
  type ProgressDomain,
  type ProgressPayload,
  type ProgressStatus,
  type SyncConfigPayload,
  type SyncDatasetRow,
} from '../api/config'
import {
  fetchScheduleJobs,
  runScheduleJob,
  toggleScheduleJob,
  type ScheduleJob,
  type ScheduleJobsPayload,
} from '../api/schedule'
import { PageHeader } from '../components/PageShell'
import CoveragePanel from '../components/config/CoveragePanel'
import { Button, Card, CardBody, CardHeader, Tabs } from '../components/ui'

/**
 * 运行配置（`/config`）。
 *
 * 这一页存在的唯一理由：`schedule.yaml` 里那 22 条、`sync.yaml` 里那 85 只代码池、
 * `backfill.yaml` 里的回填窗口，此前只存在于文件里，用户在界面上一个都看不到。
 *
 * 三条边界：
 * - **只读展示 + 低风险写**：开关任务、立即跑一次（后端已有闸门）；改触发时间、
 *   改代码池一律不给按钮——那两件事必须走文件，否则页面会变成第二个真相源。
 * - **水位从磁盘来**：`latest_day` 是扫 `data/archive/YYYY/MM/DD/` 得到的，
 *   没落过分区就显示「从未入库」，不用今天顶替、也不用「按需拉过」冒充「在同步」。
 * - **后台任务不点按钮**：快照 / 同步 / 摘要这类高频任务只读展示（它们按表跑，
 *   手动点火会和定时轮次抢同一个锁）。
 */

type Tab = 'progress' | 'schedule' | 'sync' | 'backfill' | 'coverage'

const TABS: ReadonlyArray<{ value: Tab; label: string }> = [
  { value: 'progress', label: '采集进度' },
  { value: 'schedule', label: '定时任务' },
  { value: 'sync', label: '数据同步' },
  { value: 'backfill', label: '历史回填' },
  { value: 'coverage', label: '数据覆盖' },
]

const RUN_STATUS: Record<string, string> = {
  ok: '成功',
  success: '成功',
  done: '成功',
  failed: '失败',
  error: '失败',
  running: '运行中',
  skipped: '跳过',
  degraded: '降级',
}

function runLabel(job: ScheduleJob): { text: string; tone: string } {
  const state = job.last_run
  if (!state) return { text: '还没跑过', tone: 'text-text-muted' }
  const status = String(state.status || '').toLowerCase()
  const label = RUN_STATUS[status] ?? state.status ?? '未知'
  const tone =
    status === 'failed' || status === 'error'
      ? 'text-rose-600'
      : status === 'running'
        ? 'text-amber-600'
        : status === 'ok' || status === 'success' || status === 'done'
          ? 'text-emerald-600'
          : 'text-text-muted'
  const at = state.finished_at ?? state.started_at
  return { text: at ? `${label} · ${at.slice(0, 16).replace('T', ' ')}` : label, tone }
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

function ScheduleTab({
  payload,
  onRefresh,
}: {
  payload: ScheduleJobsPayload | null
  onRefresh: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const run = async (id: string) => {
    setBusy(id)
    setNotice(null)
    try {
      const result = await runScheduleJob(id)
      setNotice(result.started ? `${id} 已开始执行，结果看「上次跑」` : `${id} 未执行：${result.detail ?? result.status}`)
      onRefresh()
    } catch (error) {
      setNotice(`${id} 执行失败：${String(error)}`)
    } finally {
      setBusy(null)
    }
  }

  const toggle = async (job: ScheduleJob) => {
    setBusy(job.id)
    setNotice(null)
    try {
      await toggleScheduleJob(job.id, !job.enabled)
      onRefresh()
    } catch (error) {
      setNotice(`${job.id} 开关失败：${String(error)}`)
    } finally {
      setBusy(null)
    }
  }

  const jobs = payload?.jobs ?? []
  const background = payload?.background ?? []
  const enabled = jobs.filter((job) => job.enabled).length

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <NumberBlock label="可操作任务" value={String(jobs.length)} hint="带开关与「跑一次」" />
        <NumberBlock label="已启用" value={String(enabled)} hint={`停用 ${jobs.length - enabled}`} />
        <NumberBlock label="后台高频任务" value={String(background.length)} hint="按表跑，不给手动点火" />
        <NumberBlock label="跨轮目标" value={String(payload?.plans?.items?.length ?? 0)} hint="你亲口交待的长期任务" />
      </div>

      {notice ? <p className="text-caption text-text-muted">{notice}</p> : null}

      <Card>
        <CardHeader title="定时任务" subtitle="来自 config/schedule.yaml；值以运行时状态为准" />
        <CardBody>
          <div className="grid grid-cols-12 gap-2 border-b border-border pb-2 text-caption text-text-muted">
            <span className="col-span-4">任务</span>
            <span className="col-span-2">触发</span>
            <span className="col-span-3">上次跑</span>
            <span className="col-span-3 text-right">操作</span>
          </div>
          {jobs.map((job) => {
            const state = runLabel(job)
            return (
              <div key={job.id} className="grid grid-cols-12 items-center gap-2 border-b border-border py-2 text-body last:border-b-0">
                <div className="col-span-4 flex flex-col">
                  <span className="font-medium text-text">{job.name}</span>
                  <span className="text-caption text-text-muted">{job.kind_label || job.kind}</span>
                </div>
                <span className="col-span-2 ldst-num text-text-muted">{job.trigger}</span>
                <span className={`col-span-3 text-caption ${state.tone}`}>{state.text}</span>
                <div className="col-span-3 flex items-center justify-end gap-2">
                  <Button size="md" variant="ghost" disabled={busy === job.id} onClick={() => void toggle(job)}>
                    {job.enabled ? '停用' : '启用'}
                  </Button>
                  <Button size="md" disabled={busy === job.id} onClick={() => void run(job.id)}>
                    跑一次
                  </Button>
                </div>
              </div>
            )
          })}
          {jobs.length === 0 ? <p className="py-3 text-caption text-text-muted">没读到任务表（检查 config/schedule.yaml）。</p> : null}
        </CardBody>
      </Card>

      {background.length > 0 ? (
        <Card>
          <CardHeader title="后台高频任务" subtitle="快照 / 同步 / 摘要按表跑，这里只读" />
          <CardBody>
            {background.map((job) => {
              const state = runLabel(job)
              return (
                <div key={job.id} className="flex items-center justify-between border-b border-border py-2 text-body last:border-b-0">
                  <div className="flex flex-col">
                    <span className="text-text">{job.name}</span>
                    <span className="text-caption text-text-muted">{job.kind_label || job.kind}{job.session_bound ? ' · 仅交易时段' : ''}</span>
                  </div>
                  <div className="flex flex-col items-end">
                    <span className="ldst-num text-caption text-text-muted">{job.trigger}</span>
                    <span className={`text-caption ${state.tone}`}>{state.text}</span>
                  </div>
                </div>
              )
            })}
          </CardBody>
        </Card>
      ) : null}

      {payload?.warnings?.length ? (
        <Card>
          <CardBody>
            {payload.warnings.map((warning) => (
              <p key={warning} className="text-caption text-amber-600">
                {warning}
              </p>
            ))}
          </CardBody>
        </Card>
      ) : null}
    </div>
  )
}

function statusOf(row: SyncDatasetRow): { text: string; tone: string } {
  if (!row.latest_day) return { text: '从未入库', tone: 'text-rose-600' }
  if (row.today_ready) return { text: '今日已入库', tone: 'text-emerald-600' }
  const days = row.stale_days
  if (days !== null && days > 3) return { text: `断档 ${days} 天`, tone: 'text-rose-600' }
  if (days !== null && days > 1) return { text: `滞后 ${days} 天`, tone: 'text-amber-600' }
  return { text: 'T-1', tone: 'text-text-muted' }
}

function SyncTab({ payload }: { payload: SyncConfigPayload | null }) {
  if (!payload) return <p className="text-caption text-text-muted">读取中…</p>
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <NumberBlock label="配置里数据集" value={String(payload.count)} hint="config/sync.yaml" />
        <NumberBlock label="有日分区" value={String(payload.covered)} hint={`${payload.count - payload.covered} 个从未入库`} />
        <NumberBlock label="断档 / 滞后" value={String(payload.stale)} hint="滞后超过 1 个交易日" />
        <NumberBlock label="代码池" value={String(payload.base_pool)} hint="逐只数据集的实际票数" />
      </div>

      <p className="text-caption text-text-muted">
        代码池决定广度：现在 {payload.base_pool} 只，全 A 约 5,400 只。要开全市场，改 sync.yaml 的 codes 并启用盘后包备源——本页只展示，不提供改代码池的按钮。
      </p>

      <Card>
        <CardHeader title="数据集与水位" subtitle={`${payload.today} · 最新分区来自 data/archive 磁盘扫描`} />
        <CardBody>
          <div className="grid grid-cols-12 gap-2 border-b border-border pb-2 text-caption text-text-muted">
            <span className="col-span-3">数据集</span>
            <span className="col-span-4">主源 / 备源</span>
            <span className="col-span-2 text-right">代码池</span>
            <span className="col-span-1 text-right">最新分区</span>
            <span className="col-span-2 text-right">状态</span>
          </div>
          {payload.datasets.map((row) => {
            const status = statusOf(row)
            return (
              <div key={row.name} className="grid grid-cols-12 items-center gap-2 border-b border-border py-2 text-body last:border-b-0">
                <span className="col-span-3 font-medium text-text">{row.name}</span>
                <div className="col-span-4 flex flex-col">
                  <span className="text-caption text-text">{row.primary || '—'}</span>
                  <span className="text-caption text-text-muted">
                    {row.fallback.length ? `备源 ${row.fallback.join(' / ')}` : '无备源'}
                    {row.codes_from ? ` · 池来自 ${row.codes_from}` : ''}
                  </span>
                </div>
                <span className="col-span-2 ldst-num text-right text-text-muted">{row.codes || '—'}</span>
                <span className="col-span-1 ldst-num text-right text-text-muted">{row.latest_day ?? '—'}</span>
                <span className={`col-span-2 text-right text-caption ${status.tone}`}>{status.text}</span>
              </div>
            )
          })}
        </CardBody>
      </Card>
    </div>
  )
}

const DOMAIN_BADGE: Record<ProgressStatus, { text: string; tone: string; bar: string }> = {
  ok: { text: '正常', tone: 'text-emerald-600', bar: 'bg-emerald-500' },
  partial: { text: '未盖满', tone: 'text-amber-600', bar: 'bg-amber-500' },
  never: { text: '从未入库', tone: 'text-rose-600', bar: 'bg-rose-500' },
  blocked: { text: '被挡住', tone: 'text-rose-600', bar: 'bg-rose-500' },
  lost: { text: '采到未落', tone: 'text-rose-600', bar: 'bg-rose-500' },
  ondemand: { text: '按需', tone: 'text-text-muted', bar: 'bg-slate-400' },
}

const RUN_BADGE: Record<string, { text: string; tone: string }> = {
  ok: { text: '成功', tone: 'text-emerald-600' },
  degraded: { text: '降级', tone: 'text-amber-600' },
  failed: { text: '失败', tone: 'text-rose-600' },
  skipped: { text: '跳过', tone: 'text-text-muted' },
  running: { text: '运行中', tone: 'text-amber-600' },
}

/** 采集器那一格的状态：ok / failed / empty / skipped / degraded */
const CELL_BADGE: Record<string, { glyph: string; tone: string; title: string }> = {
  ok: { glyph: '✓', tone: 'text-emerald-600', title: '取到数' },
  degraded: { glyph: '△', tone: 'text-amber-600', title: '取到但降级' },
  failed: { glyph: '✕', tone: 'text-rose-600', title: '链路不可用' },
  empty: { glyph: '∅', tone: 'text-text-muted', title: '上游 0 条' },
  skipped: { glyph: '–', tone: 'text-text-muted', title: '前置条件不满足，主动跳过' },
}

/** 数据集 ID → 中文名。这是「数据集」的展示名，不是 source_status 的徽标标签
 *  （后者唯一实现在 components/ui/SourceBadges.tsx，QUOT-320 §5.5）。 */
const DATASET_LABEL: Record<string, string> = {
  announcement_cninfo: '公告·巨潮',
  announcement_szse: '公告·深交所',
  research_report: '研报',
  news_eastmoney_stock: '东财个股新闻',
  news_cctv: '央视新闻',
  flash_cls: '财联社电报',
  flash_wallstreetcn: '华尔街见闻',
  flash_eastmoney: '东财快讯',
}

function CoverageBar({ row }: { row: ProgressDomain }) {
  if (row.coverage === null) {
    return <span className="text-caption text-text-muted">当日口径</span>
  }
  const badge = DOMAIN_BADGE[row.status]
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-border">
        <div className={`h-full rounded-full ${badge.bar}`} style={{ width: `${Math.min(row.coverage * 100, 100)}%` }} />
      </div>
      <span className="ldst-num text-caption text-text-muted">{(row.coverage * 100).toFixed(1)}%</span>
    </div>
  )
}

function PoolCard({
  pool,
  progress,
  onApplied,
}: {
  pool: PoolPayload | null
  progress: ProgressPayload | null
  onApplied: () => void
}) {
  const [selected, setSelected] = useState<number | null | 'current'>('current')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [custom, setCustom] = useState('')

  if (!pool) return null
  const target = selected === 'current' ? pool.size : selected
  const selectedChoice = pool.choices.find((choice) => choice.size === target)
  const changed = target !== pool.size
  const universe = pool.universe
  const customRaw = custom.split(/[\s,，;；]+/).filter(Boolean)
  const customCodes = [...new Set(customRaw.filter((item) => /^\d{6}$/.test(item)))]

  const apply = async () => {
    if (!confirmed) {
      setConfirmed(true)
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const result = await applyPool(target)
      setNotice(`已写入 ${result.codes_written ?? target} 只（备份 ${result.backup ?? '—'}）；没有触发采集。`)
      setConfirmed(false)
      setSelected('current')
      onApplied()
    } catch (error) {
      setNotice(`写入失败：${String(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const applyCustom = async () => {
    if (!customCodes.length) {
      setNotice('没识别到合法的 6 位代码')
      return
    }
    if (!confirmed) {
      setConfirmed(true)
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const result = await applyPoolCodes(customCodes)
      setNotice(`已按你给的 ${result.codes_written ?? customCodes.length} 只写入（备份 ${result.backup ?? '—'}）；没有触发采集。`)
      setConfirmed(false)
      setCustom('')
      setSelected('current')
      onApplied()
    } catch (error) {
      setNotice(`写入失败：${String(error)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader
        title="代码池（覆盖率分母）"
        subtitle="85 不是系统上限，是当初手写在 config/sync.yaml 里的一份观察池；这里可以改"
      />
      <CardBody>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <NumberBlock label="当前池" value={String(pool.size)} hint={`${pool.file} · ${pool.updated_at?.slice(0, 16).replace('T', ' ') ?? '—'}`} />
          <NumberBlock
            label="标的字典"
            value={universe.symbols !== null ? universe.symbols.toLocaleString() : '—'}
            hint={universe.active !== null ? `活跃 ${universe.active.toLocaleString()} 只 · ${universe.as_of ?? '—'}` : (universe.error ?? '读不到字典')}
          />
          <NumberBlock label="共用这份池" value={String(pool.anchor_users)} hint="个数据集走 *base_pool 锚点" />
          <NumberBlock
            label="当前覆盖率分母"
            value={progress ? String(progress.base_pool) : '—'}
            hint="域进度里的目标值就是它"
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {pool.choices.map((choice) => {
            const active =
              choice.size === null
                ? selected === null
                : selected === 'current'
                  ? choice.size === pool.size
                  : selected === choice.size
            return (
              <Button
                key={choice.label}
                size="md"
                variant={active ? 'primary' : 'ghost'}
                onClick={() => {
                  setSelected(choice.size === null ? null : choice.size)
                  setConfirmed(false)
                  setNotice(null)
                }}
              >
                {choice.label}
              </Button>
            )
          })}
          {selectedChoice ? <span className="text-caption text-text-muted">{selectedChoice.note}</span> : null}
        </div>

        {changed ? (
          <p className="mt-3 text-caption text-amber-600">
            将从 {pool.size} 只改为 {target ?? universe.active ?? '全部活跃'} 只；写入前自动备份 sync.yaml。
            只是改配置，不会开始采集——真要采数请到「定时任务」点「跑一次」，或等下一轮定时。
          </p>
        ) : (
          <p className="mt-3 text-caption text-text-muted">当前就是这份池子，选别的档位才会改文件。</p>
        )}

        {pool.warning ? <p className="mt-2 text-caption text-rose-600">{pool.warning}</p> : null}

        <div className="mt-4 flex flex-col gap-2 border-t border-border pt-3">
          <label className="text-caption text-text-muted" htmlFor="pool-custom">
            或者指定一批代码（6 位，逗号/空格/换行分隔；不填名称也能写）
          </label>
          <div className="flex items-center gap-2">
            <input
              id="pool-custom"
              value={custom}
              onChange={(event) => {
                setCustom(event.target.value)
                setConfirmed(false)
                setNotice(null)
              }}
              placeholder="600519, 000858, 601318"
              className="min-w-0 flex-1 rounded-md border border-border bg-transparent px-3 py-2 text-body text-text placeholder:text-text-muted"
            />
            <Button size="md" disabled={busy || !customCodes.length} onClick={() => void applyCustom()}>
              {confirmed && customCodes.length ? '确认写入' : '按这批写入'}
            </Button>
          </div>
          {customRaw.length ? (
            <span className="text-caption text-text-muted">
              识别到 {customRaw.length} 个片段，其中合法 6 位代码去重后 {customCodes.length} 只
              {customRaw.length - customCodes.length > 0 ? `（${customRaw.length - customCodes.length} 个片段被忽略）` : ''}
            </span>
          ) : null}
        </div>

        <div className="mt-3 flex items-center gap-3">
          <Button size="md" disabled={!changed || busy} onClick={() => void apply()}>
            {confirmed ? '确认写入（会改 sync.yaml）' : '应用'}
          </Button>
          {confirmed ? (
            <Button size="md" variant="ghost" onClick={() => setConfirmed(false)}>
              取消
            </Button>
          ) : null}
          <span className="text-caption text-text-muted">{pool.limits.note}</span>
        </div>

        {notice ? <p className="mt-2 text-caption text-text">{notice}</p> : null}
        <p className="mt-2 text-caption text-text-muted">
          池子前几只：{pool.codes_preview.slice(0, 8).join('、') || '—'}
          {pool.size > 8 ? ` …共 ${pool.size} 只` : ''}
        </p>
      </CardBody>
    </Card>
  )
}

function ProgressTab({
  payload,
  pool,
  onApplied,
}: {
  payload: ProgressPayload | null
  pool: PoolPayload | null
  onApplied: () => void
}) {
  if (!payload) return <p className="text-caption text-text-muted">读取中…</p>
  const gaps = payload.domains.filter((row) => row.status === 'never' || row.status === 'blocked' || row.status === 'lost')
  const sources = payload.matrix[0]?.cells.map((cell) => cell.dataset) ?? []

  return (
    <div className="flex flex-col gap-4">
      <PoolCard pool={pool} progress={payload} onApplied={onApplied} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <NumberBlock label="正常" value={String(payload.counts.ok)} hint="目标已覆盖、时点在窗口内" />
        <NumberBlock label="未盖满" value={String(payload.counts.partial)} hint="有数但没盖满票池" />
        <NumberBlock label="缺口" value={String(payload.counts.broken)} hint="从未入库 / 被挡 / 采到未落" />
        <NumberBlock label="代码池" value={String(payload.base_pool)} hint={`覆盖率分母（全 A 约 5,400）`} />
      </div>

      {payload.duckdb_error ? (
        <p className="text-caption text-rose-600">查询层没打开：{payload.duckdb_error}（其它区块照常显示）</p>
      ) : null}

      {gaps.length > 0 ? (
        <Card>
          <CardHeader title={`${gaps.length} 处缺口，按原因分开说`} subtitle="采不到和采到了没落库是两件事，修法也不同" />
          <CardBody>
            {gaps.map((row) => (
              <div key={row.key} className="flex flex-col gap-1 border-b border-border py-2 last:border-b-0">
                <div className="flex items-center gap-2">
                  <span className={`text-caption ${DOMAIN_BADGE[row.status].tone}`}>{DOMAIN_BADGE[row.status].text}</span>
                  <span className="text-body font-medium text-text">{row.label}</span>
                  <span className="text-caption text-text-muted">{row.unit === '条' ? `${row.got} 条` : ''}</span>
                </div>
                <span className="text-caption text-text-muted">{row.detail}</span>
              </div>
            ))}
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title="域进度"
          subtitle={`${payload.today} · 分母是代码池 ${payload.base_pool} 只，不是全市场；公告域 = 池 ∪ 当日异动`}
        />
        <CardBody>
          <div className="grid grid-cols-12 gap-2 border-b border-border pb-2 text-caption text-text-muted">
            <span className="col-span-3">数据域</span>
            <span className="col-span-3">覆盖</span>
            <span className="col-span-2 text-right">实到 / 目标</span>
            <span className="col-span-2 text-right">最新</span>
            <span className="col-span-2 text-right">状态</span>
          </div>
          {payload.domains.map((row) => {
            const badge = DOMAIN_BADGE[row.status]
            return (
              <div key={row.key} className="border-b border-border py-2 last:border-b-0">
                <div className="grid grid-cols-12 items-center gap-2 text-body">
                  <div className="col-span-3 flex flex-col">
                    <span className="font-medium text-text">{row.label}</span>
                    <span className="text-caption text-text-muted">{row.cadence}</span>
                  </div>
                  <div className="col-span-3">
                    <CoverageBar row={row} />
                  </div>
                  <span className="col-span-2 ldst-num text-right text-text-muted">
                    {row.key === 'announcement' && row.denominator
                      ? `${row.covered ?? '—'} / ${row.denominator} 只`
                      : row.target > 0
                        ? `${row.got} / ${row.target}`
                        : row.rows !== null
                          ? `${row.rows} 行`
                          : '—'}
                  </span>
                  <span className="col-span-2 ldst-num text-right text-text-muted">
                    {row.latest ?? '—'}
                    {row.status !== 'ondemand' && row.lag_days !== null && row.lag_days > 1 ? ` ·${row.lag_days}天` : ''}
                  </span>
                  <span className={`col-span-2 text-right text-caption ${badge.tone}`}>{badge.text}</span>
                </div>
                {row.key === 'announcement' && row.denominator ? (
                  <p className="mt-1 text-caption text-text-muted" data-testid="ann-candidates">
                    {[
                      `分母 = 池 ${row.pool_size} ∪ 池外异动 ${row.extra_total ?? 0} = ${row.denominator}`,
                      row.candidates_filtered === false
                        ? '前置筛已关闭，按全池逐票'
                        : row.candidates_fallback
                          ? `${row.candidates_fallback}，本轮已回退全池`
                          : row.strategy_skipped !== null && row.strategy_skipped !== undefined
                            ? `只请求异动票 ${row.requested ?? 0} 只，采过 ${row.covered}${
                                row.requested_missing ? `（${row.requested_missing} 只没取回）` : ''
                              } · 按策略未采 ${row.strategy_skipped} 只（不算缺口）`
                            : '还没跑过前置筛采集，分母是预告',
                    ].join('；')}
                  </p>
                ) : null}
                {row.key === 'announcement' && row.movers_top && row.movers_top.length > 0 ? (
                  <p className="mt-0.5 text-caption text-text-muted" data-testid="ann-movers">
                    {`本轮会请求的异动票（${row.movers_total ?? row.movers_top.length} 只）：${row.movers_top
                      .map((item) =>
                        item.pct === null || item.pct === undefined
                          ? item.code
                          : `${item.code} ${item.pct > 0 ? '+' : ''}${item.pct}%`,
                      )
                      .join('、')}${(row.movers_total ?? 0) > row.movers_top.length ? ' …' : ''}`}
                  </p>
                ) : null}
                {row.status !== 'ok' || (row.key === 'announcement' && row.candidates_note) ? (
                  <p className="mt-1 text-caption text-text-muted">{row.detail}</p>
                ) : null}
              </div>
            )
          })}
        </CardBody>
      </Card>

      {payload.runs.length > 0 ? (
        <Card>
          <CardHeader
            title="任务级最近结果"
            subtitle="data/schedule_runs/*.json；跳过要带原因，否则一屏红色会让人以为全挂了"
          />
          <CardBody>
            {payload.runs.map((run) => {
              const badge = RUN_BADGE[run.status] ?? { text: run.status, tone: 'text-text-muted' }
              return (
                <div key={run.job_id} className="border-b border-border py-2 last:border-b-0">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`text-caption ${badge.tone}`}>{badge.text}</span>
                      <span className="text-body font-medium text-text">{run.job_id}</span>
                    </div>
                    <span className="ldst-num text-caption text-text-muted">
                      {run.age_hours !== null ? `${run.age_hours.toFixed(1)} 小时前` : run.started_at.slice(0, 16)}
                    </span>
                  </div>
                  {run.detail ? <p className="mt-1 text-caption text-text-muted">{run.detail}</p> : null}
                  {run.sources_unavailable && run.sources_unavailable.length > 0 ? (
                    <p className="mt-1 break-all text-caption text-text-muted" data-testid={`run-sources-${run.job_id}`}>
                      不可用源（{run.sources_unavailable_total ?? run.sources_unavailable.length}）：{run.sources_unavailable
                        .map((item) =>
                          item.status && item.status !== 'unavailable' ? `${item.id}(${item.status})` : item.id,
                        )
                        .join('、')}
                      {(run.sources_unavailable_total ?? 0) > run.sources_unavailable.length
                        ? ` …（仅列前 ${run.sources_unavailable.length}）`
                        : ''}
                    </p>
                  ) : null}
                </div>
              )
            })}
          </CardBody>
        </Card>
      ) : null}

      {payload.matrix.length > 0 ? (
        <Card>
          <CardHeader title="采集器逐源状态" subtitle="news.sqlite 的 news_runs；这是「为什么是 0」的唯一证据" />
          <CardBody>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-caption">
                <thead>
                  <tr className="border-b border-border text-text-muted">
                    <th className="whitespace-nowrap py-2 pr-3 text-left font-normal">运行</th>
                    {sources.map((source) => (
                      <th key={source} className="whitespace-nowrap px-2 py-2 text-center font-normal">
                        {DATASET_LABEL[source] ?? source}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {payload.matrix.map((run: FeedRun) => (
                    <tr key={run.at} className="border-b border-border last:border-b-0">
                      <td className="whitespace-nowrap py-2 pr-3 text-text-muted">
                        {run.at.slice(5, 16).replace('T', ' ')}
                        <span className="ml-2">入 {run.stored ?? 0}</span>
                      </td>
                      {sources.map((source) => {
                        const cell = run.cells.find((item) => item.dataset === source)
                        if (!cell) {
                          return (
                            <td key={source} className="px-2 py-2 text-center text-text-muted">
                              ·
                            </td>
                          )
                        }
                        const badge = CELL_BADGE[cell.status] ?? { glyph: '?', tone: 'text-text-muted', title: cell.status }
                        return (
                          <td key={source} className="px-2 py-2 text-center" title={`${badge.title}${cell.detail ? `：${cell.detail}` : ''}`}>
                            <span className={`${badge.tone} font-medium`}>{badge.glyph}</span>
                            <span className="ml-1 ldst-num text-text-muted">{cell.items ?? 0}</span>
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-caption text-text-muted">
              ✓ 取到数 · △ 取到但降级 · ✕ 链路不可用 · ∅ 上游 0 条 · – 前置条件不满足主动跳过。鼠标悬停看原话。
            </p>
          </CardBody>
        </Card>
      ) : null}

      <p className="text-caption text-text-muted">{payload.note}</p>
    </div>
  )
}

function FieldRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 text-body last:border-b-0">
      <span className="text-text-muted">{label}</span>
      <span className="text-text">{value}</span>
    </div>
  )
}

function BackfillTab({ payload }: { payload: BackfillPayload | null }) {
  if (!payload) return <p className="text-caption text-text-muted">读取中…</p>
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader title="历史回填口径" subtitle="来自 config/backfill.yaml" />
        <CardBody>
          <FieldRow label="取数源" value={payload.source ?? '—'} />
          <FieldRow label="阶段" value={payload.stage ?? '—'} />
          <FieldRow label="窗口" value={`${payload.start ?? '—'} → ${payload.end || '基准最新交易日'}`} />
          <FieldRow label="目标年数" value={payload.target_years !== null ? `${payload.target_years} 年` : '—'} />
          <FieldRow label="票池" value={`${payload.universe} 只`} />
          <FieldRow label="限速" value={payload.throttle_ms !== null ? `${Math.round(payload.throttle_ms / 1000)} 秒 / 只` : '—'} />
          <FieldRow label="缺口率阈值" value={payload.gap_rate_threshold !== null ? `${(payload.gap_rate_threshold * 100).toFixed(0)}%` : '—'} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="最近一次缺口报表" subtitle="data/backfill-reports（每次运行落一个子目录）" />
        <CardBody>
          {payload.latest_report ? (
            <>
              <FieldRow label="目录" value={payload.latest_report.name} />
              {payload.latest_report.files.map((file) => (
                <p key={file} className="py-1 text-caption text-text-muted">
                  {file}
                </p>
              ))}
            </>
          ) : (
            <p className="text-caption text-text-muted">还没有回填报表——跑一次回填任务（右下「本页数据同步」里可触发）才会生成。</p>
          )}
        </CardBody>
      </Card>

      <p className="text-caption text-text-muted">
        逐只回填的实际速率受上游限速约束：5,400 只 × {(payload.throttle_ms ?? 0) / 1000} 秒 ≈{' '}
        {(((payload.throttle_ms ?? 0) / 1000) * 5400 / 3600).toFixed(0)} 小时。全市场只能走盘后包，不走这条链路。
      </p>
    </div>
  )
}

export default function ConfigScreen() {
  // tab 从 URL 读：`/config?tab=coverage` 要能直达，否则截图/分享链接永远落在「采集进度」
  const [searchParams, setSearchParams] = useSearchParams()
  const urlTab = searchParams.get('tab')
  const initialTab = TABS.some((item) => item.value === urlTab) ? (urlTab as Tab) : 'progress'
  const [tab, setTabRaw] = useState<Tab>(initialTab)
  const setTab = useCallback(
    (next: Tab) => {
      setTabRaw(next)
      setSearchParams({ tab: next }, { replace: true })
    },
    [setSearchParams],
  )
  const [progress, setProgress] = useState<ProgressPayload | null>(null)
  const [pool, setPool] = useState<PoolPayload | null>(null)
  const [schedule, setSchedule] = useState<ScheduleJobsPayload | null>(null)
  const [sync, setSync] = useState<SyncConfigPayload | null>(null)
  const [backfill, setBackfill] = useState<BackfillPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    // 四路并行：串行等待会让「采集进度」页打开后要等十几秒才出数
    const [s, y, b, p, pl] = await Promise.allSettled([
      fetchScheduleJobs(),
      fetchSyncConfig(),
      fetchBackfillConfig(),
      fetchProgress(),
      fetchPool(),
    ])
    const failed: string[] = []
    if (s.status === 'fulfilled') setSchedule(s.value)
    else failed.push(`定时任务：${String(s.reason)}`)
    if (y.status === 'fulfilled') setSync(y.value)
    else failed.push(`数据同步：${String(y.reason)}`)
    if (b.status === 'fulfilled') setBackfill(b.value)
    else failed.push(`历史回填：${String(b.reason)}`)
    if (p.status === 'fulfilled') setProgress(p.value)
    else failed.push(`采集进度：${String(p.reason)}`)
    if (pl.status === 'fulfilled') setPool(pl.value)
    else failed.push(`代码池：${String(pl.reason)}`)
    if (failed.length) setError(failed.join('；'))
    setLoading(false)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 浏览器前进/后退或手改地址栏时跟着走，不把 tab 锁在首次进入的那一页
  useEffect(() => {
    setTabRaw(TABS.some((item) => item.value === urlTab) ? (urlTab as Tab) : 'progress')
  }, [urlTab])

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="运行配置"
        subtitle="采集进度、定时任务、同步池、历史回填——只读自磁盘与 config/*.yaml，改配置仍走文件"
        actions={
          <Button variant="ghost" onClick={() => void load()} disabled={loading}>
            <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
            刷新
          </Button>
        }
      />

      <Tabs tabs={TABS} value={tab} onChange={setTab} ariaLabel="运行配置分页" />

      {error ? <p className="text-caption text-rose-600">读取失败：{error}</p> : null}

      {tab === 'progress' ? <ProgressTab payload={progress} pool={pool} onApplied={() => void load()} /> : null}
      {tab === 'schedule' ? <ScheduleTab payload={schedule} onRefresh={() => void load()} /> : null}
      {tab === 'sync' ? <SyncTab payload={sync} /> : null}
      {tab === 'backfill' ? <BackfillTab payload={backfill} /> : null}
      {tab === 'coverage' ? <CoveragePanel /> : null}

      <p className="flex items-center gap-1 text-caption text-text-muted">
        <Database className="h-3 w-3" aria-hidden />
        进度与水位都是只读快照（分区扫磁盘、运行记录读 sqlite 与 schedule_runs）；开关任务与「跑一次」会写 data/schedule_runs/。
      </p>
    </div>
  )
}
