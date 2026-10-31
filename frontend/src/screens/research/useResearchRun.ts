import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  ResearchJob,
  ResearchJobStatus,
  ResearchRunHandle,
  ResearchSource,
  ResearchStageName,
} from '../../api/research'

const POLL_MS = 1000

function handleStatus(value: string): ResearchJobStatus {
  return value === 'queued' || value === 'running' || value === 'ok' || value === 'degraded' || value === 'failed'
    ? value
    : 'queued'
}

export interface ResearchRunController {
  /** 正在重跑的段落；没有任务时为 null */
  stage: ResearchStageName | null
  progress: number
  step: string | null
  error: unknown
  running: boolean
  run: (stage: ResearchStageName) => void
}

/**
 * 「重试本段」：`research_run --only <stage>` 的 HTTP 形态（POST /api/research/{code}/run），
 * 之后 1s 轮询任务，直到 ok / degraded / failed。轮询节奏与设置页的任务模型一致。
 *
 * 单次轮询失败直接终止并抛出错误——重试任务本身失败时，继续轮询只会掩盖问题。
 */
export function useResearchRun(
  source: ResearchSource,
  code: string | null,
  date: string,
  onSettled: () => void,
): ResearchRunController {
  const [job, setJob] = useState<ResearchJob | null>(null)
  const [error, setError] = useState<unknown>(null)

  const sourceRef = useRef(source)
  const codeRef = useRef(code)
  const dateRef = useRef(date)
  const settledRef = useRef(onSettled)
  sourceRef.current = source
  codeRef.current = code
  dateRef.current = date
  settledRef.current = onSettled

  const running = job !== null
  const jobId = job?.id ?? null

  useEffect(() => {
    if (!jobId) return
    let active = true
    const timer = window.setInterval(() => {
      sourceRef.current
        .job(jobId)
        .then((next) => {
          if (!active) return
          if (next.status === 'ok' || next.status === 'degraded') {
            setJob(null)
            settledRef.current()
          } else if (next.status === 'failed') {
            setJob(null)
            setError(new Error(next.error ?? '研究任务失败，原因未记录'))
          } else {
            setJob(next)
          }
        })
        .catch((cause: unknown) => {
          if (!active) return
          setJob(null)
          setError(cause)
        })
    }, POLL_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [jobId])

  const run = useCallback((stage: ResearchStageName) => {
    const target = codeRef.current
    if (!target || jobId) return
    setError(null)
    void sourceRef.current
      .run(target, dateRef.current, stage)
      .then((handle: ResearchRunHandle) => {
        setJob({
          id: handle.job_id,
          status: handleStatus(handle.status),
          stage,
          progress: 0,
          step: '已创建任务',
        })
      })
      .catch((cause: unknown) => {
        setJob(null)
        setError(cause)
      })
  }, [jobId])

  return {
    stage: job?.stage ?? null,
    progress: job?.progress ?? 0,
    step: job?.step ?? null,
    error,
    running,
    run,
  }
}
