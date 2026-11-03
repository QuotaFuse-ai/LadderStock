import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { CircleCheck, CircleX, Loader2, Play, SquareTerminal } from 'lucide-react'

import { api, describeApiError, type JobView, type PageCommand } from '../api'
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
 * 页面顶部的一键命令条（QUOT-200）：按路由列出本页对应的 `ldst` 命令，
 * 直接复用控制面的 Job 队列（`/control/commands`），与设置页的启停任务同一套
 * 线程池与日志口径。
 *
 * 控制面未开启时整条不渲染——按钮失效不能挡住页面本身的数据展示。
 * 同一时刻只允许一条页面命令在跑，后端 409 兜底，这里只做按钮禁用。
 */
export default function PageCommandBar() {
  const { pathname } = useLocation()
  // 演示态约定「全程不碰网络」：控制面清单也不请求，整条不渲染
  const demo = useDemoEnabled()
  const [commands, setCommands] = useState<PageCommand[]>([])
  const [job, setJob] = useState<JobView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  useEffect(() => {
    setJob(command?.last_job ?? null)
    setError(null)
  }, [command?.key, pathname])

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

  if (demo || !command) return null

  const running = Boolean(activeId)
  const argv = `ldst ${command.argv.join(' ')}`
  const log = job && job.log_tail.length > 0 ? job.log_tail : null

  const run = async (): Promise<void> => {
    setBusy(true)
    setError(null)
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
    <section
      aria-label="本页命令"
      className="mb-4 flex flex-col gap-2 rounded border border-border bg-surface px-3 py-2"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="inline-flex items-center gap-1 text-caption text-text-muted">
          <SquareTerminal className="size-4" aria-hidden="true" />
          {command.label}
        </span>
        <code className="ldst-num truncate rounded-xs bg-sunken px-2 py-0.5 text-caption text-text-secondary">
          {argv}
        </code>
        <div className="ml-auto flex items-center gap-2">
          {job && job.status !== 'queued' && (
            <Badge tone={job.status === 'ok' ? 'ok' : job.status === 'failed' ? 'danger' : 'neutral'} title={job.error ?? job.step}>
              {statusLabel(job)}
            </Badge>
          )}
          {job?.status === 'queued' && <Badge tone="neutral">{statusLabel(job)}</Badge>}
          <Button
            variant="secondary"
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
      </div>

      {error && (
        <p className="flex items-start gap-1 text-caption text-danger">
          <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}

      {job?.status === 'failed' && job.error && (
        <p className="flex items-start gap-1 text-caption text-danger">
          <CircleX className="mt-half size-3.5 shrink-0" aria-hidden="true" />
          {job.error}
        </p>
      )}

      {log && (
        <details className="group" open={running}>
          <summary className="cursor-pointer select-none text-caption text-text-muted">
            {running ? '执行输出' : '上次输出'}（{log.length} 行）
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded-xs bg-sunken p-2 text-caption leading-body text-text-secondary">
            {log.join('\n')}
          </pre>
        </details>
      )}

      {job?.status === 'ok' && (
        <p className="flex items-center gap-1 text-caption text-text-muted">
          <CircleCheck className="size-3.5" aria-hidden="true" />
          已完成，数据已落盘
        </p>
      )}
    </section>
  )
}
