import { useCallback, useEffect, useRef, useState } from 'react'
import { CloudDownload, CircleX, Loader2, RefreshCw } from 'lucide-react'

import { api, type JobView, type PageCommand } from '../../api'
import { cn } from '../../lib/cn'
import {
  describeCommandSubmitError,
  describeJobFailure,
  hasPartialSourceFailure,
  isJobActive,
  jobStatusText,
} from '../../lib/controlCommand'
import { useDemoEnabled } from '../../lib/pageState'
import { Badge, Button, Checkbox, Drawer } from '../../components/ui'

/** `/industry` 当前数据覆盖：只数接口给的 `data_source_status`，不重算指标。 */
export interface IndustryCoverage {
  total: number
  partial: number
  missing: number
}

const SYNC_KEY = 'industry-sync'

function CoverageRow({
  label,
  before,
  current,
}: {
  label: string
  before: number | null
  current: number
}) {
  const delta = before === null ? null : current - before
  return (
    <div className="flex items-baseline justify-between gap-3 border-t border-border py-1 first:border-t-0">
      <span className="text-caption text-text-secondary">{label}</span>
      <span className="ldst-num text-caption text-text">
        {before === null ? `${current} 个` : `${before} → ${current} 个`}
        {delta !== null && delta !== 0 && (
          <span className={cn('ml-2', delta < 0 ? 'text-ok' : 'text-danger')}>
            {delta < 0 ? `减少 ${Math.abs(delta)}` : `增加 ${delta}`}
          </span>
        )}
      </span>
    </div>
  )
}

/**
 * `/industry` 卡头的「同步补齐」入口（QUOT-538）。
 *
 * 与「重算行业快照」是两条命令：那条只读缓存重组、零网络；这条联网拉缺失数据、
 * 成本非 0、有条数上限。契约（QUOT-533）把成本/范围原文放在 `note`，这里原样渲染；
 * 进度/结果/错误一律读 `GET /api/control/jobs/{id}`，前端不重算指标。
 *
 * 演示态不渲染（页面约定全程不碰网络），控制面 404（未启用）也不渲染。
 */
export default function IndustrySyncPanel({
  coverage,
  onCompleted,
}: {
  coverage: IndustryCoverage | null
  onCompleted: () => void
}) {
  const demo = useDemoEnabled()
  const [command, setCommand] = useState<PageCommand | null>(null)
  const [open, setOpen] = useState(false)
  const [job, setJob] = useState<JobView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [baseline, setBaseline] = useState<IndustryCoverage | null>(null)
  const completedIdRef = useRef<string | null>(null)

  const loadCommand = useCallback(async () => {
    try {
      const payload = await api.control.commands()
      const found = payload.commands.find((item) => item.key === SYNC_KEY) ?? null
      setCommand(found)
      return found
    } catch {
      setCommand(null)
      return null
    }
  }, [])

  useEffect(() => {
    if (!demo) void loadCommand()
  }, [demo, loadCommand])

  const active = isJobActive(job)
  const activeId = active ? job?.id ?? null : null

  // onCompleted 可能是每次渲染新建的函数；用 ref 持有最新值，避免轮询定时器被重置
  const onCompletedRef = useRef(onCompleted)
  useEffect(() => {
    onCompletedRef.current = onCompleted
  })

  // 同一个 job 只通知父页刷新一次。轮询拿到结果时与 setJob 同批调用，
  // 父页的新 coverage 和成功态在同一次提交里出现，不会先闪一帧旧覆盖。
  const notifyCompleted = useCallback((id: string) => {
    if (completedIdRef.current === id) return
    completedIdRef.current = id
    onCompletedRef.current()
  }, [])

  useEffect(() => {
    if (!activeId) return
    const timer = window.setInterval(() => {
      void api.control
        .job(activeId)
        .then((next) => {
          setJob(next)
          if (next.status === 'ok') notifyCompleted(next.id)
          if (!isJobActive(next)) void loadCommand()
        })
        .catch(() => {
          /* 单次轮询失败不终止任务，下一拍再试 */
        })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [activeId, loadCommand, notifyCompleted])

  // 任务从别的入口启动时，打开抽屉先同步一次最近状态
  useEffect(() => {
    if (!open || demo) return
    void loadCommand().then((found) => {
      if (found?.last_job) setJob((prev) => (isJobActive(prev) ? prev : found.last_job))
    })
  }, [open, demo, loadCommand])

  // 打开抽屉时从 last_job 接管一个已完成任务，也补一次父页刷新
  useEffect(() => {
    if (job?.status === 'ok') notifyCompleted(job.id)
  }, [job, notifyCompleted])

  if (demo || !command) return null

  const start = async (): Promise<void> => {
    if (!acknowledged) return
    setBusy(true)
    setError(null)
    try {
      const handle = await api.control.runCommand(SYNC_KEY)
      setBaseline(coverage)
      setAcknowledged(false)
      setJob({
        id: handle.job_id,
        peer: 'ldst',
        action: 'command',
        status: 'queued',
        step: '已排队，等待后端线程池',
        progress: 0,
        started_at: new Date().toISOString(),
        ended_at: null,
        log_tail: [],
        error: null,
      })
    } catch (cause) {
      setError(describeCommandSubmitError(cause))
    } finally {
      setBusy(false)
    }
  }

  const failure = job?.status === 'failed' ? describeJobFailure(job) : null
  const log = job && job.log_tail.length > 0 ? job.log_tail : null
  const succeeded = job?.status === 'ok'
  const partial = succeeded && hasPartialSourceFailure(job)

  return (
    <>
      <Button
        variant="secondary"
        leadingIcon={<CloudDownload className="size-4" aria-hidden="true" />}
        title="联网拉取缺失数据（成本非 0、有条数上限）；与只读的「重算行业快照」不同"
        onClick={() => setOpen(true)}
      >
        同步补齐
      </Button>

      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title="同步补齐行业数据"
        subtitle="联网拉取缺失数据 · 显式触发，不会自动执行"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              关闭
            </Button>
            <Button
              variant="primary"
              leadingIcon={
                active ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <CloudDownload className="size-4" aria-hidden="true" />
                )
              }
              loading={busy}
              disabled={active || !acknowledged}
              disabledReason={active ? '同步进行中，一次只允许一个任务' : undefined}
              onClick={() => void start()}
            >
              {active ? '同步中' : succeeded || failure ? '再次同步' : '开始同步'}
            </Button>
          </div>
        }
      >
        <div className="flex flex-col gap-4">
          <p className="rounded border border-border bg-surface-2 p-3 text-body leading-body text-text-secondary">
            重算行业快照 = 用已有数据重新整理，<strong className="text-text">不联网</strong>；
            同步补齐 = 联网拉取缺失数据，<strong className="text-text">有条数上限</strong>。
          </p>

          <section className="flex flex-col gap-1">
            <h3 className="text-body font-medium text-text">同步范围与成本</h3>
            <p className="text-caption leading-body text-text-secondary">
              {command.note ??
                '真实联网同步：按已启用行业的锚点股回补缺失数据，单数据集有条数上限，不做全市场拉取。范围与成本以控制面返回为准。'}
            </p>
          </section>

          {job && (
            <section className="flex flex-col gap-2 rounded border border-border bg-surface-2 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  tone={job.status === 'ok' ? 'ok' : job.status === 'failed' ? 'danger' : 'info'}
                  title={job.error ?? job.step}
                >
                  {jobStatusText(job)}
                </Badge>
                {active && (
                  <span
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(job.progress * 100)}
                    aria-label="同步进度"
                    className="h-1 w-32 overflow-hidden rounded-pill bg-surface-3"
                  >
                    <span
                      className="block h-full rounded-pill bg-accent transition-all duration-base"
                      style={{ width: `${Math.min(100, Math.max(0, job.progress * 100))}%` }}
                    />
                  </span>
                )}
                <span className="min-w-0 flex-1 truncate text-caption text-text-secondary">
                  {job.step || '—'}
                </span>
              </div>

              {active && (
                <p className="text-caption text-text-muted">
                  一次只允许一个页面命令。任务在后台线程池执行，可以关掉抽屉，稍后回到本页查看结果。
                </p>
              )}

              {succeeded && (
                <div className="flex flex-col gap-1">
                  <p className="flex items-center gap-1 text-caption text-text">
                    {partial
                      ? '同步完成，但部分数据源失败（详见输出）。'
                      : '同步完成，数据已落盘。'}
                  </p>
                  {coverage ? (
                    <div className="mt-1 rounded-xs bg-surface p-2">
                      <p className="mb-1 text-caption text-text-muted">
                        {baseline
                          ? '覆盖对比（同步前 → 同步后，只数后端给的状态桶）'
                          : '当前覆盖（同步前基线未记录）'}
                      </p>
                      <CoverageRow label="行业总数" before={baseline?.total ?? null} current={coverage.total} />
                      <CoverageRow label="部分缺失" before={baseline?.partial ?? null} current={coverage.partial} />
                      <CoverageRow label="未取到" before={baseline?.missing ?? null} current={coverage.missing} />
                    </div>
                  ) : (
                    <p className="text-caption text-text-muted">同步已完成；本页数据未加载，无法展示覆盖对比。</p>
                  )}
                  <Button
                    variant="ghost"
                    leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
                    onClick={onCompleted}
                  >
                    重新加载本页数据
                  </Button>
                </div>
              )}

              {failure && (
                <p className="flex items-start gap-1 text-caption text-danger">
                  <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
                  <span>
                    {failure.conclusion}
                    {failure.detail && (
                      <span className="mt-1 block text-text-muted">{failure.detail}</span>
                    )}
                  </span>
                </p>
              )}

              {error && (
                <p className="flex items-start gap-1 text-caption text-danger">
                  <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
                  {error}
                </p>
              )}

              {log && (
                <details open={active}>
                  <summary className="cursor-pointer select-none text-caption text-text-muted">
                    {active ? '执行输出' : '上次输出'}（{log.length} 行）
                  </summary>
                  <pre className="mt-1 max-h-56 overflow-auto rounded-xs bg-sunken p-2 text-caption leading-body text-text-secondary">
                    {log.join('\n')}
                  </pre>
                </details>
              )}
            </section>
          )}

          {!job && error && (
            <p className="flex items-start gap-1 text-caption text-danger">
              <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
              {error}
            </p>
          )}

          <section className="rounded border border-border-strong bg-surface-2 p-3">
            <Checkbox checked={acknowledged} onChange={setAcknowledged} disabled={active}>
              我了解「同步补齐」会发起真实联网请求、产生非 0 成本，且单数据集有条数上限（不做全市场拉取）。
            </Checkbox>
          </section>
        </div>
      </Drawer>
    </>
  )
}
