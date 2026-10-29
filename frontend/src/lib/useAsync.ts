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
  const [nonce, setNonce] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  useEffect(() => {
    if (!enabled) {
      setStatus('loading')
      return
    }
    const controller = new AbortController()
    let active = true
    setStatus('loading')
    setError(null)
    loaderRef
      .current(controller.signal)
      .then((value) => {
        if (!active) return
        setData(value)
        setStatus('ready')
      })
      .catch((cause: unknown) => {
        if (!active || controller.signal.aborted) return
        setError(cause)
        setStatus('error')
      })
    return () => {
      active = false
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, nonce, ...deps])

  const reload = useCallback(() => setNonce((value) => value + 1), [])
  return { status, data, error, reload }
}
