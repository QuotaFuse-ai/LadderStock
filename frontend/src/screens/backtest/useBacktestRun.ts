import { useCallback, useEffect, useRef, useState } from 'react'

import {
  fetchBacktestJob,
  submitBacktestRun,
  type BacktestJob,
  type BacktestRunRequest,
} from '../../api/backtest'

/** 任务进度轮询节拍：与研究面 `useResearchRun` 同口径（1s）。 */
const POLL_MS = 1000

export type BacktestRunPhase = 'idle' | 'submitting' | 'running' | 'settled' | 'failed'

export interface BacktestRunController {
  phase: BacktestRunPhase
  jobId: string | null
  job: BacktestJob | null
  /** 提交失败 / 任务失败 / 轮询失败的原因（原始异常，文案由页面整理）。 */
  error: unknown
  /** 任务成功结束时的 run_id；结果区据此载入运行卡。 */
  runId: string | null
  submit: (request: BacktestRunRequest) => void
  reset: () => void
}

/**
 * 「运行回测」：`POST /api/backtest/run` → 1s 轮询 `GET /api/backtest/jobs/{job_id}`。
 *
 * - `status=ok`：任务结束，把 `run_id` 交给页面去载入运行卡；`gate_passed=false`
 *   （退出码 8）也是 `ok`，结果区照常显示「校验门未通过」。
 * - `status=failed`：把后端 `error` 交给页面，页面按 `exit_code` 给出中文分类。
 * - 轮询请求本身失败：停止轮询并如实报错，不继续空转。
 *
 * 任务 id 由后端生成且唯一，因此新提交天然换 effect；不需要手动清计时器。
 */
export function useBacktestRun(onSettled: (runId: string) => void): BacktestRunController {
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<BacktestJob | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [phase, setPhase] = useState<BacktestRunPhase>('idle')
  const [runId, setRunId] = useState<string | null>(null)

  const settledRef = useRef(onSettled)
  settledRef.current = onSettled
  const inFlightRef = useRef(false)

  useEffect(() => {
    if (jobId === null) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = () => {
      void fetchBacktestJob(jobId)
        .then((next) => {
          if (!active) return
          setJob(next)
          if (next.status === 'ok') {
            if (next.run_id === null) {
              setPhase('failed')
              setError(new Error('任务已完成但后端没有给出 run_id'))
              return
            }
            setRunId(next.run_id)
            setPhase('settled')
            settledRef.current(next.run_id)
            return
          }
          if (next.status === 'failed') {
            setPhase('failed')
            setError(new Error(next.error ?? '回测任务失败，后端未给出原因'))
            return
          }
          timer = setTimeout(tick, POLL_MS)
        })
        .catch((cause: unknown) => {
          if (!active) return
          setPhase('failed')
          setError(cause)
        })
    }

    tick()
    return () => {
      active = false
      if (timer !== null) clearTimeout(timer)
    }
  }, [jobId])

  const submit = useCallback((request: BacktestRunRequest) => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    setPhase('submitting')
    setError(null)
    setJob(null)
    setRunId(null)
    setJobId(null)
    void submitBacktestRun(request)
      .then((ack) => {
        setJobId(ack.job_id)
        setPhase('running')
      })
      .catch((cause: unknown) => {
        setPhase('failed')
        setError(cause)
      })
      .finally(() => {
        inFlightRef.current = false
      })
  }, [])

  const reset = useCallback(() => {
    setPhase('idle')
    setJobId(null)
    setJob(null)
    setError(null)
    setRunId(null)
  }, [])

  return { phase, jobId, job, error, runId, submit, reset }
}

/** 后端退出码口径（QUOT-535）：0 过门 / 8 未过门 / 2 参数 / 3 配置 / 4 数据。 */
export function exitCodeText(exitCode: number | null): string {
  switch (exitCode) {
    case 0:
      return '退出码 0 · 校验门通过'
    case 8:
      return '退出码 8 · 校验门未通过'
    case 2:
      return '退出码 2 · 参数错误'
    case 3:
      return '退出码 3 · 配置错误'
    case 4:
      return '退出码 4 · 数据缺失'
    default:
      return exitCode === null ? '退出码未知' : `退出码 ${exitCode}`
  }
}

/** 任务状态的中文文案（queued / running 之外的状态由调用方处理）。 */
export function jobStatusText(status: BacktestJob['status']): string {
  switch (status) {
    case 'queued':
      return '已排队'
    case 'running':
      return '运行中'
    case 'ok':
      return '已完成'
    case 'failed':
      return '失败'
  }
}
