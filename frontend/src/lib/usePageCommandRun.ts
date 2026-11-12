import { useCallback, useEffect, useRef, useState } from 'react'

import { api, describeApiError, type JobView } from '../api'
import { isJobActive } from './controlCommand'

/**
 * 页内一键动作（QUOT-561）：把「提交控制面白名单命令 → 轮询任务 → 报结果」收成一处，
 * 供页内按钮（收盘对账、通知链路自检）复用，口径与顶栏「本页数据同步」同一条队列。
 *
 * 只用后端返回的事实：任务状态、`error`、日志尾；本文件不解释业务数据、不补默认值。
 */

/** 任务结束后的读法：成功（含部分降级）还是失败，失败原因原样给出。 */
export interface PageCommandOutcome {
  job: JobView
  ok: boolean
  detail: string
}

export interface PageCommandRun {
  job: JobView | null
  running: boolean
  /** 提交失败（409 已有任务在跑 / 连不上 / 其它 HTTP 错误），白话原因 */
  submitError: string | null
  outcome: PageCommandOutcome | null
  run: () => Promise<void>
  reset: () => void
}

/**
 * 逐命令的「跑完之后怎么说」：后端任务日志里的事实（例如通知是否真的送达）
 * 只在这里翻译成一句白话，不改任务状态、不补成功、不补数字。
 */
export interface PageCommandCopy {
  okDetail: (job: JobView) => string
}

function outcomeOf(job: JobView, copy: PageCommandCopy): PageCommandOutcome {
  const ok = job.status === 'ok'
  const detail = ok ? copy.okDetail(job) : (job.error?.trim() || '任务失败，具体原因见任务日志。')
  return { job, ok, detail }
}

const POLL_MS = 1500

/** 部分成功（退出码 8/9 仍算任务成功）只写在日志尾里，按后端那句原话升级成提示。 */
const PARTIAL_MARKER = '部分数据源失败或 AI 降级'

/** 控制面一次只跑一个任务：409 的白话说法，不必让用户读原始 detail。 */
function submitCopy(error: unknown): string {
  const info = describeApiError(error)
  if (info.code === 'HTTP 409') {
    return '已经有一个任务在跑：等它结束再点一次（顶栏能看到进度）。'
  }
  return info.detail
}

const DEFAULT_COPY: PageCommandCopy = {
  okDetail: (job) =>
    job.log_tail.some((line) => line.includes(PARTIAL_MARKER))
      ? '任务完成，但部分数据源失败或 AI 降级：结果已落盘，缺失部分如实标出。'
      : '任务完成。',
}

/** 通知自检：退出码 4 也算跑完，但「没送到」必须说出来，不能显示成单纯的成功。 */
export const NOTIFY_COPY: PageCommandCopy = {
  okDetail: (job) => {
    if (job.log_tail.some((line) => line.includes('通知已送达本机渠道'))) {
      return '测试通知已送到本机渠道；真的看到了这条消息，就说明通知链路可用。'
    }
    if (job.log_tail.some((line) => line.includes('通知没有送达'))) {
      return '通知没有送达：本机通知渠道不可用，或有桌面通知后端缺失。通知链路本身没坏，只是这一轮没送到；原因见任务记录。'
    }
    return DEFAULT_COPY.okDetail(job)
  },
}

export function usePageCommandRun(
  key: string,
  options: { copy?: PageCommandCopy } = {},
): PageCommandRun {
  const copy = options.copy ?? DEFAULT_COPY
  const copyRef = useRef(copy)
  copyRef.current = copy
  const [job, setJob] = useState<JobView | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<PageCommandOutcome | null>(null)
  const [busy, setBusy] = useState(false)
  const timerRef = useRef<number | null>(null)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      if (timerRef.current !== null) window.clearInterval(timerRef.current)
    }
  }, [])

  const poll = useCallback((jobId: string) => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current)
    timerRef.current = window.setInterval(() => {
      void api.control
        .job(jobId)
        .then((next) => {
          if (!aliveRef.current) return
          setJob(next)
          if (!isJobActive(next)) {
            if (timerRef.current !== null) window.clearInterval(timerRef.current)
            timerRef.current = null
            setOutcome(outcomeOf(next, copyRef.current))
            setBusy(false)
          }
        })
        .catch(() => {
          // 轮询失败只停轮询，不把上一次的成功/失败结论改成别的
          if (timerRef.current !== null) window.clearInterval(timerRef.current)
          timerRef.current = null
          if (aliveRef.current) {
            setBusy(false)
            setSubmitError('任务状态暂时读不到：本机服务可能断开了，稍后点「刷新」看最近一次结果。')
          }
        })
    }, POLL_MS)
  }, [])

  const run = useCallback(async () => {
    if (busy) return
    setBusy(true)
    setSubmitError(null)
    setOutcome(null)
    try {
      const ack = await api.control.runCommand(key)
      if (!aliveRef.current) return
      setJob({
        id: ack.job_id,
        peer: 'ldst',
        action: 'command',
        status: 'queued',
        step: '已排队',
        progress: 0,
        started_at: new Date().toISOString(),
        ended_at: null,
        log_tail: [],
        error: null,
      })
      poll(ack.job_id)
    } catch (error) {
      if (!aliveRef.current) return
      setBusy(false)
      setSubmitError(submitCopy(error))
    }
  }, [busy, key, poll])

  const reset = useCallback(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current)
    timerRef.current = null
    setJob(null)
    setOutcome(null)
    setSubmitError(null)
    setBusy(false)
  }, [])

  return { job, running: busy || isJobActive(job), submitError, outcome, run, reset }
}
