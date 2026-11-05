import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 极简异步取数 hook：只服务于页面渲染（loading / ready / error），不做缓存。
 *
 * 为什么不用 SWR/React Query：整个看板一共 7 个只读接口 + 控制面，
 * 引入缓存库会多 ~10KB gzip 并带来不一致的失效策略；这里只需要「取一次、能重试」。
 */

export interface AsyncResult<T> {
  status: 'loading' | 'ready' | 'error'
  data: T | null
  error: unknown
  reload: () => void
  /**
   * 静默重取：已有数据时不回 loading、不清屏，失败也保留上一次成功的数据（定时刷新用）。
   * 传 `{ manual: true }` 表示用户手动点「刷新」，失败会写进 `refreshError` 供页面提示。
   */
  refresh: (options?: { manual?: boolean }) => void
  /** 静默重取进行中；首屏加载时为 false。 */
  refreshing: boolean
  /** 手动刷新最近一次失败的原因；下一次成功取数后清空。 */
  refreshError: unknown
  /** 最近一次成功取数的本地时刻（毫秒时间戳）；用于展示「更新于」。 */
  updatedAt: number | null
}

type RunMode = 'load' | 'auto' | 'manual'

export function useAsync<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean } = {},
): AsyncResult<T> {
  const enabled = options.enabled ?? true
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<unknown>(null)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)

  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const dataRef = useRef<T | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const inFlightRef = useRef(false)
  /** 取消/替换信号：旧请求 settle 时不再写状态。 */
  const seqRef = useRef(0)
  /** 在途期间到点的下一拍，等当前请求结束再发（不打断慢请求）。 */
  const queuedRef = useRef<RunMode | null>(null)

  const runRef = useRef<(mode: RunMode) => void>(() => {})

  const run = useCallback((mode: RunMode) => {
    if (!enabledRef.current) return
    if (inFlightRef.current) {
      // 慢响应：abort 会把这拍和上一拍一起废掉，于是排队等当前请求结束再补一拍。
      if (queuedRef.current !== 'load') queuedRef.current = mode
      return
    }

    const seq = ++seqRef.current
    const controller = new AbortController()
    controllerRef.current = controller
    inFlightRef.current = true

    const silent = mode !== 'load' && dataRef.current !== null
    if (silent) {
      setRefreshing(true)
    } else {
      setStatus('loading')
      setError(null)
      setRefreshing(false)
    }

    loaderRef
      .current(controller.signal)
      .then((value) => {
        if (seq !== seqRef.current) return
        dataRef.current = value
        setData(value)
        setUpdatedAt(Date.now())
        setStatus('ready')
        setError(null)
        setRefreshError(null)
      })
      .catch((cause: unknown) => {
        if (seq !== seqRef.current || controller.signal.aborted) return
        if (mode === 'load') {
          setError(cause)
          setStatus('error')
          return
        }
        if (mode === 'manual') setRefreshError(cause)
        // 自动重取失败保留上一帧数据，等下一拍；用户仍可手动「刷新」重试。
      })
      .finally(() => {
        // 被 deps 变化 / 卸载取消的请求：新请求已经接管状态，这里不写。
        if (controllerRef.current !== controller) return
        controllerRef.current = null
        inFlightRef.current = false
        if (seq !== seqRef.current) return
        setRefreshing(false)
        const next = queuedRef.current
        queuedRef.current = null
        if (next) runRef.current(next)
      })
  }, [])
  runRef.current = run

  useEffect(() => {
    if (!enabled) {
      seqRef.current += 1
      controllerRef.current?.abort()
      controllerRef.current = null
      inFlightRef.current = false
      queuedRef.current = null
      setStatus('loading')
      return
    }
    runRef.current('load')
    return () => {
      // deps 变化 / 卸载：放弃旧请求，并清掉在途标记，避免新请求被当成「在途」而跳过。
      seqRef.current += 1
      controllerRef.current?.abort()
      controllerRef.current = null
      inFlightRef.current = false
      queuedRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps])

  const reload = useCallback(() => {
    runRef.current('load')
  }, [])
  const refresh = useCallback((options?: { manual?: boolean }) => {
    runRef.current(options?.manual ? 'manual' : 'auto')
  }, [])
  return { status, data, error, reload, refresh, refreshing, refreshError, updatedAt }
}
