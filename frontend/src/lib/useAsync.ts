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
  /** 静默重取：已有数据时不回 loading、不清屏，失败也保留上一次成功的数据（定时刷新用）。 */
  refresh: () => void
  /** 静默重取进行中；首屏加载时为 false。 */
  refreshing: boolean
  /** 最近一次成功取数的本地时刻（毫秒时间戳）；用于展示「更新于」。 */
  updatedAt: number | null
}

export function useAsync<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean } = {},
): AsyncResult<T> {
  const enabled = options.enabled ?? true
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>(enabled ? 'loading' : 'loading')
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [nonce, setNonce] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const dataRef = useRef<T | null>(null)
  /** 下一次 effect 是否走静默路径：只有 `refresh()` 置位，`reload()` 和 deps 变化都清掉。 */
  const silentRef = useRef(false)

  useEffect(() => {
    if (!enabled) {
      setStatus('loading')
      return
    }
    const silent = silentRef.current && dataRef.current !== null
    silentRef.current = false
    const controller = new AbortController()
    let active = true
    if (silent) {
      setRefreshing(true)
    } else {
      setStatus('loading')
      setError(null)
    }
    loaderRef
      .current(controller.signal)
      .then((value) => {
        if (!active) return
        dataRef.current = value
        setData(value)
        setUpdatedAt(Date.now())
        setStatus('ready')
        setError(null)
      })
      .catch((cause: unknown) => {
        if (!active || controller.signal.aborted) return
        // 静默重取失败不推翻已上屏的数据：保留旧值等下一拍，用户仍可手动「刷新」重试。
        if (silent) return
        setError(cause)
        setStatus('error')
      })
      .finally(() => {
        if (active) setRefreshing(false)
      })
    return () => {
      active = false
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, nonce, ...deps])

  const reload = useCallback(() => {
    silentRef.current = false
    setNonce((value) => value + 1)
  }, [])
  const refresh = useCallback(() => {
    silentRef.current = true
    setNonce((value) => value + 1)
  }, [])
  return { status, data, error, reload, refresh, refreshing, updatedAt }
}
