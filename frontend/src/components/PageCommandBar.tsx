import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { CircleCheck, CircleX, Loader2, Play, SquareTerminal } from 'lucide-react'

import { api, type JobView, type PageCommand } from '../api'
import { cn } from '../lib/cn'
import {
  describeCommandSubmitError,
  describeJobFailure,
  hasPartialSourceFailure,
  isJobActive,
} from '../lib/controlCommand'
import { useDemoEnabled } from '../lib/pageState'
import { Badge, Button } from './ui'

function timeLabel(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

function statusLabel(job: JobView): string {
  if (job.status === 'queued') return '已排队'
  if (job.status === 'running') return job.step || '执行中'
  const when = timeLabel(job.ended_at ?? job.started_at)
  return `${job.status === 'ok' ? '上次成功' : '上次失败'}${when ? ` · ${when}` : ''}`
}

type JobMap = Record<string, JobView | null>

/**
 * 「运行本页命令」（QUOT-200 建，QUOT-457 收进顶栏，QUOT-538 支持同路由多条）。
 *
 * 按路由列出本页对应的 `ldst` 命令，复用控制面的 Job 队列（`/control/commands`），
 * 与设置页的启停任务同一套线程池与日志口径。
 *
 * 同一条路由可以有多条语义不同的命令（`/industry`：只读「重算行业快照」+ 联网
 * 「同步补齐行业数据」）。契约（QUOT-533）要求**全部列出、各自独立展示任务状态**，
 * 不能只取第一条——否则联网入口会被只读命令挡住。
 *
 * 带 `note`（成本/范围说明）的命令是显式付费动作，点「运行」先出二次确认，
 * 不默认自动执行。同一时刻只允许一条页面命令在跑，后端 409 兜底，这里只做按钮禁用。
 */
export default function PageCommandBar() {
  const { pathname } = useLocation()
  // 演示态约定「全程不碰网络」：控制面清单也不请求，整颗不渲染
  const demo = useDemoEnabled()
  const [commands, setCommands] = useState<PageCommand[]>([])
  const [jobs, setJobs] = useState<JobMap>({})
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [confirmKey, setConfirmKey] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const routeKeyRef = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const payload = await api.control.commands()
      setCommands(payload.commands)
    } catch {
      setCommands([])
    }
  }, [])

  useEffect(() => {
    if (!demo) void refresh()
  }, [demo, refresh])

  // 路由精确匹配：`/market?tab=` 的四个视图共用一条命令，`/review/ai` 不与 `/review` 混淆
  const routeCommands = useMemo(
    () => commands.filter((item) => item.route === pathname),
    [commands, pathname],
  )

  // 打开面板时重取一次：任务可能是在页面头上的同步入口里启动的，顶栏不能一直显示旧快照
  useEffect(() => {
    if (open && !demo) void refresh()
  }, [open, demo, refresh])

  // 路由变化 / 命令清单变化时，把每条命令的上次任务快照落到各自的槽位；
  // 正在执行的任务不被旧快照覆盖。
  useEffect(() => {
    setJobs((prev) => {
      const next: JobMap = {}
      for (const item of routeCommands) {
        const existing = prev[item.key]
        next[item.key] = isJobActive(existing) ? existing : item.last_job
      }
      return next
    })
    setErrors({})
    setConfirmKey(null)
  }, [routeCommands])

  // 只在路由态真正变化时收起面板。
  // commands 首次到达会让 routeCommands 从空变非空，不能据此关面板——
  // 否则用户刚点开就会被这次 effect 立刻收起（CI 慢机上命令迟到，稳定复现）。
  const routeLabel = routeCommands.map((item) => item.key).join('|')
  useEffect(() => {
    if (routeKeyRef.current !== null && routeKeyRef.current !== routeLabel) setOpen(false)
    routeKeyRef.current = routeLabel
  }, [routeLabel])

  const activeEntry = Object.entries(jobs).find(([, job]) => isJobActive(job))
  const activeKey = activeEntry?.[0] ?? null
  const activeId = activeEntry?.[1]?.id ?? null

  useEffect(() => {
    if (!activeKey || !activeId) return
    const timer = window.setInterval(() => {
      void api.control
        .job(activeId)
        .then((next) => {
          setJobs((prev) => ({ ...prev, [activeKey]: next }))
          if (!isJobActive(next)) void refresh()
        })
        .catch(() => {
          /* 单次轮询失败不终止任务，下一拍再试 */
        })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [activeKey, activeId, refresh])

  // 面板是浮层：点外面与 Esc 都收起（与 Drawer / ConfirmDialog 的键盘口径一致）
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (demo || routeCommands.length === 0) return null

  const running = Boolean(activeKey)
  const failed = Object.values(jobs).some((job) => job?.status === 'failed')

  const run = async (command: PageCommand): Promise<void> => {
    setBusyKey(command.key)
    setErrors((prev) => ({ ...prev, [command.key]: '' }))
    setConfirmKey(null)
    setOpen(true)
    try {
      const handle = await api.control.runCommand(command.key)
      setJobs((prev) => ({
        ...prev,
        [command.key]: {
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
        },
      }))
    } catch (cause) {
      setErrors((prev) => ({ ...prev, [command.key]: describeCommandSubmitError(cause) }))
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="page-command-panel"
        aria-label="本页命令"
        title={`本页 ${routeCommands.length} 条命令`}
        className={cn(
          'relative rounded p-1.5 transition-colors duration-fast hover:bg-surface-3',
          failed ? 'text-danger' : running ? 'text-accent' : 'text-text-muted hover:text-text',
        )}
      >
        {running ? (
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        ) : (
          <SquareTerminal className="size-4" strokeWidth={1.75} aria-hidden="true" />
        )}
      </button>

      {open && (
        <div
          id="page-command-panel"
          role="dialog"
          aria-label="本页命令"
          className="absolute right-0 top-full z-overlay mt-2 max-h-[75vh] w-dialog max-w-popover overflow-auto rounded border border-border bg-surface p-3 shadow-overlay"
        >
          <div className="flex flex-col gap-3">
            {routeCommands.map((command) => (
              <CommandCard
                key={command.key}
                command={command}
                job={jobs[command.key] ?? null}
                running={running}
                busy={busyKey === command.key}
                error={errors[command.key] || null}
                confirming={confirmKey === command.key}
                onAskRun={() => {
                  if (command.note) setConfirmKey(command.key)
                  else void run(command)
                }}
                onConfirm={() => void run(command)}
                onCancel={() => setConfirmKey(null)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function CommandCard({
  command,
  job,
  running,
  busy,
  error,
  confirming,
  onAskRun,
  onConfirm,
  onCancel,
}: {
  command: PageCommand
  job: JobView | null
  running: boolean
  busy: boolean
  error: string | null
  confirming: boolean
  onAskRun: () => void
  onConfirm: () => void
  onCancel: () => void
}) {
  const active = isJobActive(job)
  const argv = `ldst ${command.argv.join(' ')}`
  const log = job && job.log_tail.length > 0 ? job.log_tail : null
  const failure = job?.status === 'failed' ? describeJobFailure(job) : null

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3 first:border-t-0 first:pt-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-body font-medium text-text">{command.label}</p>
          <code className="ldst-num mt-1 block truncate rounded-xs bg-sunken px-2 py-0.5 text-caption text-text-secondary">
            {argv}
          </code>
        </div>
        {confirming ? null : (
          <Button
            variant="secondary"
            className="shrink-0"
            leadingIcon={
              active ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Play className="size-4" aria-hidden="true" />
              )
            }
            loading={busy}
            disabled={running}
            disabledReason={running && !active ? '已有页面命令在执行' : undefined}
            onClick={onAskRun}
          >
            {active ? '执行中' : '运行'}
          </Button>
        )}
      </div>

      {command.note && (
        <p className="rounded-xs bg-warn-soft px-2 py-1 text-caption leading-body text-text-secondary">
          {command.note}
        </p>
      )}

      {confirming && (
        <div className="rounded border border-border-strong bg-surface-2 p-2">
          <p className="text-caption leading-body text-text-secondary">
            这是真实联网同步（成本非 0），会真正发起上游请求。确认现在开始？
          </p>
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" onClick={onCancel}>
              取消
            </Button>
            <Button variant="primary" onClick={onConfirm}>
              确认同步
            </Button>
          </div>
        </div>
      )}

      {job && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            tone={job.status === 'ok' ? 'ok' : job.status === 'failed' ? 'danger' : 'neutral'}
            title={job.error ?? job.step}
          >
            {statusLabel(job)}
          </Badge>
          {active && (
            <span
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(job.progress * 100)}
              aria-label={`${command.label}进度`}
              className="h-1 w-24 overflow-hidden rounded-pill bg-surface-3"
            >
              <span
                className="block h-full rounded-pill bg-accent transition-all duration-base"
                style={{ width: `${Math.min(100, Math.max(0, job.progress * 100))}%` }}
              />
            </span>
          )}
        </div>
      )}

      {error && (
        <p className="flex items-start gap-1 text-caption text-danger">
          <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}

      {failure && (
        <p className="flex items-start gap-1 text-caption text-danger">
          <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
          <span>
            {failure.conclusion}
            {failure.detail && <span className="mt-1 block text-text-muted">{failure.detail}</span>}
          </span>
        </p>
      )}

      {log && (
        <details className="group" open={active}>
          <summary className="cursor-pointer select-none text-caption text-text-muted">
            {active ? '执行输出' : '上次输出'}（{log.length} 行）
          </summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded-xs bg-sunken p-2 text-caption leading-body text-text-secondary">
            {log.join('\n')}
          </pre>
        </details>
      )}

      {job?.status === 'ok' && (
        <p className="flex items-center gap-1 text-caption text-text-muted">
          <CircleCheck className="size-3.5" aria-hidden="true" />
          {hasPartialSourceFailure(job) ? '已完成，部分数据源失败（详见输出）' : '已完成，数据已落盘'}
        </p>
      )}
    </div>
  )
}
