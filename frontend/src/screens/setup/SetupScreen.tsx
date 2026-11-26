/**
 * 首次配置 / 子模块安装向导（普通用户入口）。
 *
 * 面向「刚下载便携包的人」：他不知道 TickHub / Senate 是什么，也不该知道。
 * 页面只回答三件事——装没装、要多少地方、点一下就装。
 *
 * 数据来自 `/api/setup/status`；安装期间每 1.5 秒轮询 `/api/setup/job`，
 * 总进度按体积加权（PG 848MB 的推进比 5MB 的 ProxyFleet 慢，这是对的）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  describePeerState,
  getJob,
  getModelJob,
  getModelStatus,
  getStatus,
  startInstall,
  startModelFetch,
  type ModelJob,
  type ModelStatus,
  type SetupJob,
  type SetupPeer,
  type SetupStatus,
} from '../../api/setup'
import { PageHeader } from '../../components/PageShell'
import { Badge } from '../../components/ui/Badge'
import { Banner } from '../../components/ui/Feedback'
import { Button } from '../../components/ui/Button'
import { cn } from '../../lib/cn'

function percent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(value, 1)) * 100)}%`
}

function PeerBar({ pct, tone }: { pct: number; tone: 'run' | 'ok' | 'fail' }) {
  const color =
    tone === 'fail' ? 'bg-danger' : tone === 'ok' ? 'bg-success' : 'bg-accent'
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-border">
      <div
        className={cn('h-full transition-[width] duration-500', color)}
        style={{ width: percent(pct) }}
      />
    </div>
  )
}

function PeerRow({
  peer,
  jobPeer,
  checked,
  disabled,
  onToggle,
}: {
  peer: SetupPeer
  jobPeer?: { pct: number; message: string; ok: boolean | null }
  checked: boolean
  disabled: boolean
  onToggle: () => void
}) {
  const tone = jobPeer?.ok === true ? 'ok' : jobPeer?.ok === false ? 'fail' : 'run'
  const pct = jobPeer?.pct ?? (peer.exists ? 1 : 0)
  return (
    <div className="flex flex-col gap-2 border-b border-border px-3 py-3 last:border-b-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <input
          type="checkbox"
          className="size-4 shrink-0"
          checked={checked}
          disabled={disabled}
          onChange={onToggle}
          aria-label={`安装 ${peer.label}`}
        />
        <span className="min-w-0 text-body font-medium text-text">{peer.label}</span>
        <Badge tone={peer.exists ? 'ok' : 'warn'}>{describePeerState(peer.state)}</Badge>
        <span className="ldst-num text-caption text-text-secondary">{peer.size_mb} MB</span>
        {peer.kind === 'bundle' && <Badge tone="neutral">较慢</Badge>}
        <span className="ldst-num ml-auto text-caption text-text-secondary">{percent(pct)}</span>
      </div>
      <p className="text-caption text-text-secondary">{peer.note}</p>
      {(jobPeer || !peer.exists) && <PeerBar pct={pct} tone={tone} />}
      {jobPeer?.message && (
        <p className="ldst-num text-caption text-text-tertiary">{jobPeer.message}</p>
      )}
    </div>
  )
}

/** 嵌入模型卡片：便携包默认不带权重，第一次用记忆时在这里下载。 */
function ModelCard({
  status,
  busy,
  onFetch,
}: {
  status: ModelStatus | null
  busy: boolean
  onFetch: () => void
}) {
  const job = status?.job ?? null
  const running = job?.status === 'running'
  const ready = status?.ready ?? false
  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-body font-medium text-text">记忆嵌入模型</span>
        <Badge tone={ready ? 'ok' : 'warn'}>{ready ? '已就绪' : '未下载'}</Badge>
        <span className="ldst-num text-caption text-text-secondary">
          {status?.size_mb ?? 0} MB
        </span>
        <span className="ldst-num ml-auto text-caption text-text-secondary">
          {percent(job?.pct ?? (ready ? 1 : 0))}
        </span>
      </div>
      <p className="text-caption text-text-secondary">
        安装包不预置这份权重，省下的体积留给运行时。不下载也能看看板与行情，
        只是「记忆」不可用；下载与校验都在本机完成。
      </p>
      {(running || !ready) && <PeerBar pct={job?.pct ?? 0} tone={job?.status === 'failed' ? 'fail' : ready ? 'ok' : 'run'} />}
      {job?.message && <p className="ldst-num text-caption text-text-tertiary">{job.message}</p>}
      {job?.error && <p className="text-caption text-danger">{job.error}</p>}
      {!ready && (
        <div>
          <Button variant="primary" onClick={onFetch} disabled={busy || running}>
            {running ? '正在下载…' : `下载模型（${status?.size_mb ?? 0} MB）`}
          </Button>
        </div>
      )}
      {status?.models_dir && (
        <p className="ldst-num break-all text-caption text-text-tertiary">{status.models_dir}</p>
      )}
    </div>
  )
}

export default function SetupScreen() {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [job, setJob] = useState<SetupJob | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)
  const [modelBusy, setModelBusy] = useState(false)
  const timer = useRef<number | null>(null)
  const modelTimer = useRef<number | null>(null)

  const loadModel = useCallback(async () => {
    try {
      setModelStatus(await getModelStatus())
    } catch {
      // 模型状态读不到不该挡住子模块安装页
    }
  }, [])

  useEffect(() => {
    void loadModel()
  }, [loadModel])

  useEffect(() => {
    const job: ModelJob | null = modelStatus?.job ?? null
    if (!job || job.status !== 'running') return undefined
    modelTimer.current = window.setInterval(async () => {
      try {
        const response = await getModelJob()
        const next = response.job
        if (!next) return
        setModelStatus((prev) => (prev ? { ...prev, job: next } : prev))
        if (next.status !== 'running') {
          setModelBusy(false)
          void loadModel()
        }
      } catch {
        // 轮询失败下一次继续
      }
    }, 1500) as unknown as number
    return () => {
      if (modelTimer.current !== null) window.clearInterval(modelTimer.current)
      modelTimer.current = null
    }
  }, [modelStatus, loadModel])

  const fetchModel = async () => {
    if (modelBusy) return
    setModelBusy(true)
    try {
      const response = await startModelFetch()
      if (!response.ok) {
        setMessage(response.detail || '启动模型下载失败')
        setModelBusy(false)
        return
      }
      await loadModel()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
      setModelBusy(false)
    }
  }

  const load = useCallback(async () => {
    try {
      const next = await getStatus()
      setStatus(next)
      setJob(next.job)
      // 默认全选：普通用户不知道该不该装，缺一个都可能让某个功能静默不可用
      setSelected((prev) => {
        if (prev.size > 0) return prev
        return new Set(next.peers.filter((item) => !item.exists).map((item) => item.key))
      })
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!job || job.status !== 'running') return undefined
    timer.current = window.setInterval(async () => {
      try {
        const response = await getJob()
        if (response.job) {
          setJob(response.job)
          if (response.job.status !== 'running') {
            setBusy(false)
            void load()
          }
        }
      } catch {
        // 轮询失败不打断页面，下一次继续
      }
    }, 1500) as unknown as number
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current)
      timer.current = null
    }
  }, [job, load])

  const peers = status?.peers ?? []
  const selectedSize = useMemo(() => {
    return peers.filter((item) => selected.has(item.key)).reduce((sum, item) => sum + item.size_mb, 0)
  }, [peers, selected])

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const install = async () => {
    if (busy) return
    setBusy(true)
    setMessage('')
    try {
      const response = await startInstall(Array.from(selected), true)
      if (!response.ok) {
        setMessage(response.detail || '启动安装失败')
        setBusy(false)
        return
      }
      const next = await getJob()
      setJob(next.job)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
      setBusy(false)
    }
  }

  const running = job?.status === 'running'
  const overall = job?.overall ?? 0

  return (
    <section className="flex flex-col gap-5">
      <PageHeader
        title="首次配置"
        subtitle="检测并安装本机缺少的子模块；装完自动可用，不需要懂 Python"
        refresh={{ onClick: () => void load(), disabled: running }}
        actions={
          <Button
            variant="primary"
            onClick={() => void install()}
            disabled={busy || running || selected.size === 0}
          >
            {running ? '正在安装…' : '开始安装'}
          </Button>
        }
      />

      {(status?.missing_count ?? 0) > 0 && (
        <Banner
          tone="warn"
          title={`发现 ${status?.missing_count} 项未安装`}
          action={undefined}
        >
          <p className="max-w-measure">
            这些是行情、增强包、辩论等功能的依赖。全部勾选后点「开始安装」，
            安装期间可以离开本页，进度会在后台继续。
          </p>
        </Banner>
      )}

      {job && (
        <div className="flex flex-col gap-2 rounded border border-border bg-surface p-3">
          <div className="flex flex-wrap items-center gap-x-3">
            <span className="text-body font-medium text-text">
              {running ? '安装中' : job.status === 'done' ? '安装完成' : '安装失败'}
            </span>
            {job.current_peer && (
              <span className="ldst-num text-caption text-text-secondary">
                正在处理：{job.current_peer}
              </span>
            )}
            <span className="ldst-num ml-auto text-caption text-text-secondary">
              {percent(overall)}
            </span>
          </div>
          <PeerBar pct={overall} tone={job.status === 'failed' ? 'fail' : job.status === 'done' ? 'ok' : 'run'} />
          {job.error && <p className="text-caption text-danger">{job.error}</p>}
          {job.status === 'done' && (
            <p className="text-caption text-text-secondary">
              全部选定模块已就位。若服务未自动可用，回到设置页点「启动全部」或重启平台。
            </p>
          )}
        </div>
      )}

      {message && <p className="text-caption text-danger">{message}</p>}

      <div className="grid gap-4 desk:grid-cols-3">
        <div className="rounded border border-border bg-surface p-3">
          <p className="text-caption text-text-secondary">安装位置</p>
          <p className="ldst-num break-all text-caption text-text">{status?.modules_root || '—'}</p>
        </div>
        <div className="rounded border border-border bg-surface p-3">
          <p className="text-caption text-text-secondary">本次占用</p>
          <p className="ldst-num text-body font-medium text-text">
            {selectedSize} MB
            <span className="ml-2 text-caption text-text-secondary">
              / 全选 {(status?.total_mb ?? 0)} MB
            </span>
          </p>
        </div>
        <div className="rounded border border-border bg-surface p-3">
          <p className="text-caption text-text-secondary">Python 来源</p>
          <p className="text-caption text-text">{status?.interpreter_origin || '—'}</p>
        </div>
      </div>

      <ModelCard status={modelStatus} busy={modelBusy} onFetch={() => void fetchModel()} />

      <div className="rounded border border-border bg-surface">
        {peers.length === 0 ? (
          <div className="px-3 py-6 text-caption text-text-secondary">正在读取安装状态…</div>
        ) : (
          peers.map((peer) => (
            <PeerRow
              key={peer.key}
              peer={peer}
              jobPeer={job?.peers?.[peer.key]}
              checked={selected.has(peer.key)}
              disabled={running}
              onToggle={() => toggle(peer.key)}
            />
          ))
        )}
      </div>

      {status && (
        <p className="text-caption text-text-tertiary">
          软件源：{status.mirrors.slice(0, 3).join(' / ')}（按顺序自动重试，不会要求你访问 GitHub）
        </p>
      )}
    </section>
  )
}
