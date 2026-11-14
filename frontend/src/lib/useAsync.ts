import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

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
   *
   * 返回 `true` = 这一拍取数成功、`false` = 失败（失败原因照常进 `refreshError` / `failures`）。
   * **不 reject**：`refresh` 常被 fire-and-forget 调用（定时器、事件回调），抛出未处理的
   * 拒绝会污染全局；退避只需要成败信号，不需要异常。
   */
  refresh: (options?: { manual?: boolean }) => Promise<boolean>
  /** 静默重取进行中；首屏加载时为 false。 */
  refreshing: boolean
  /** 手动刷新最近一次失败的原因；下一次成功取数后清空。 */
  refreshError: unknown
  /** 最近一次成功取数的本地时刻（毫秒时间戳）；用于展示「更新于」。 */
  updatedAt: number | null
  /** 连续失败次数（一次成功即归零）。轮询退避与「重试」提示读它，不需要屏侧自己数。 */
  failures: number
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
  const [failures, setFailures] = useState(0)

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

  const runRef = useRef<(mode: RunMode) => Promise<boolean>>(() => Promise.resolve(true))

  const run = useCallback((mode: RunMode): Promise<boolean> => {
    if (!enabledRef.current) return Promise.resolve(false)
    if (inFlightRef.current) {
      // 慢响应：abort 会把这拍和上一拍一起废掉，于是排队等当前请求结束再补一拍。
      if (queuedRef.current !== 'load') queuedRef.current = mode
      return Promise.resolve(false)
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

    // 返回 Promise<boolean> 让轮询 hook 判这一拍成败（退避用）；屏侧原有调用忽略返回值不受影响。
    return loaderRef
      .current(controller.signal)
      .then((value): boolean => {
        if (seq !== seqRef.current) return false
        dataRef.current = value
        setData(value)
        setUpdatedAt(Date.now())
        setStatus('ready')
        setError(null)
        setRefreshError(null)
        setFailures(0)
        return true
      })
      .catch((cause: unknown): boolean => {
        if (seq !== seqRef.current || controller.signal.aborted) return false
        setFailures((n) => n + 1)
        if (mode === 'load') {
          setError(cause)
          setStatus('error')
          return false
        }
        if (mode === 'manual') setRefreshError(cause)
        // 自动重取失败保留上一帧数据，等下一拍；用户仍可手动「刷新」重试。
        return false
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
        if (next) void runRef.current(next)
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
    void runRef.current('load')
  }, [])
  const refresh = useCallback((options?: { manual?: boolean }) => {
    return runRef.current(options?.manual ? 'manual' : 'auto')
  }, [])
  // 对象字面量每次渲染都是新引用；下游（useHomeSource 的 views / PublishTodoMovers 的 effect 依赖）
  // 把整个结果对象当依赖时会被迫每拍重算，一旦再回写 state 就成发布死循环（QUOT-590 CI 挂死）。
  // 用 useMemo 让字段未变时结果身份稳定。
  return useMemo(
    () => ({ status, data, error, reload, refresh, refreshing, refreshError, updatedAt, failures }),
    [status, data, error, reload, refresh, refreshing, refreshError, updatedAt, failures],
  )
}
