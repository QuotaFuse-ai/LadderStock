import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { CircleCheck, CircleX, Loader2, Play, SquareTerminal } from 'lucide-react'

import { api, describeApiError, type JobView, type PageCommand } from '../api'
import { cn } from '../lib/cn'
import { useDemoEnabled } from '../lib/pageState'
import { Badge, Button } from './ui'

const ACTIVE = new Set(['queued', 'running'])

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

/**
 * 「运行本页命令」（QUOT-200 建，QUOT-457 收进顶栏）。
 *
 * 按路由列出本页对应的 `ldst` 命令，复用控制面的 Job 队列（`/control/commands`），
 * 与设置页的启停任务同一套线程池与日志口径。
 *
 * 形状：顶栏一颗图标按钮 + 点击展开的面板。命令的 argv、上次结果与日志尾部都在面板里，
 * 不在页面上占一行。控制面未开启或本页没有命令时整颗不渲染——按钮失效不能挡住页面数据。
 * 同一时刻只允许一条页面命令在跑，后端 409 兜底，这里只做按钮禁用。
 */
export default function PageCommandBar() {
  const { pathname } = useLocation()
  // 演示态约定「全程不碰网络」：控制面清单也不请求，整颗不渲染
  const demo = useDemoEnabled()
  const [commands, setCommands] = useState<PageCommand[]>([])
  const [job, setJob] = useState<JobView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
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
  const command = useMemo(() => commands.find((item) => item.route === pathname), [commands, pathname])

  // 只在路由态真正变化（切页 / 换命令）时收起面板并复位 job。
  // commands 首次到达会让 command?.key 从 undefined 变为实际值，不能据此关面板——
  // 否则用户刚点开就会被这次 effect 立刻收起（CI 慢机上 commands 迟到，稳定复现）。
  useEffect(() => {
    const routeKey = `${pathname}|${command?.key ?? ''}`
    if (routeKeyRef.current !== null && routeKeyRef.current !== routeKey) setOpen(false)
    routeKeyRef.current = routeKey
    setJob(command?.last_job ?? null)
    setError(null)
  }, [pathname, command?.key])

  const activeId = job && ACTIVE.has(job.status) ? job.id : null

  useEffect(() => {
    if (!activeId) return
    const timer = window.setInterval(() => {
      void api.control
        .job(activeId)
        .then((next) => {
          setJob(next)
          if (!ACTIVE.has(next.status)) void refresh()
        })
        .catch(() => {
          /* 单次轮询失败不终止任务，下一拍再试 */
        })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [activeId, refresh])

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

  if (demo || !command) return null

  const running = Boolean(activeId)
  const argv = `ldst ${command.argv.join(' ')}`
  const log = job && job.log_tail.length > 0 ? job.log_tail : null

  const run = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    setOpen(true)
    try {
      const handle = await api.control.runCommand(command.key)
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
      setError(describeApiError(cause).detail)
    } finally {
      setBusy(false)
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
        title={`${command.label} · ${argv}`}
        className={cn(
          'relative rounded p-1.5 transition-colors duration-fast hover:bg-surface-3',
          job?.status === 'failed' ? 'text-danger' : running ? 'text-accent' : 'text-text-muted hover:text-text',
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
          className="absolute right-0 top-full z-overlay mt-2 w-dialog max-w-popover rounded border border-border bg-surface p-3 shadow-overlay"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-body font-medium text-text">{command.label}</p>
              <code className="ldst-num mt-1 block truncate rounded-xs bg-sunken px-2 py-0.5 text-caption text-text-secondary">
                {argv}
              </code>
            </div>
            <Button
              variant="secondary"
              className="shrink-0"
              leadingIcon={
                running ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Play className="size-4" aria-hidden="true" />
                )
              }
              loading={busy}
              disabled={running}
              disabledReason={running ? '已有页面命令在执行' : undefined}
              onClick={() => void run()}
            >
              {running ? '执行中' : '运行'}
            </Button>
          </div>

          {job && (
            <div className="mt-2 flex items-center gap-2">
              <Badge
                tone={job.status === 'ok' ? 'ok' : job.status === 'failed' ? 'danger' : 'neutral'}
                title={job.error ?? job.step}
              >
                {statusLabel(job)}
              </Badge>
            </div>
          )}

          {error && (
            <p className="mt-2 flex items-start gap-1 text-caption text-danger">
              <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
              {error}
            </p>
          )}

          {job?.status === 'failed' && job.error && (
            <p className="mt-2 flex items-start gap-1 text-caption text-danger">
              <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
              {job.error}
            </p>
          )}

          {log && (
            <details className="group mt-2" open={running}>
              <summary className="cursor-pointer select-none text-caption text-text-muted">
                {running ? '执行输出' : '上次输出'}（{log.length} 行）
              </summary>
              <pre className="mt-1 max-h-64 overflow-auto rounded-xs bg-sunken p-2 text-caption leading-body text-text-secondary">
                {log.join('\n')}
              </pre>
            </details>
          )}

          {job?.status === 'ok' && (
            <p className="mt-2 flex items-center gap-1 text-caption text-text-muted">
              <CircleCheck className="size-3.5" aria-hidden="true" />
              已完成，数据已落盘
            </p>
          )}
        </div>
      )}
    </div>
  )
}
