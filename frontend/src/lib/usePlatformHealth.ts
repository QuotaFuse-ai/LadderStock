import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 平台健康探测（QUOT-443 / DEC-063）。
 *
 * 数据只来自独立看门狗 `GET {platform}/platform/health`，2s 一拍：
 * - 连续 **2 次**请求失败才判红（单次抖动维持原色，不闪）；
 * - 看门狗明确回 `status=offline` 时立即红——主服务已死是事实，不是抖动；
 * - `db_ok=false` 或 `status=degraded` → 黄；`ok` → 绿。
 *
 * 为什么是 2s 而不是 5s：验收要求杀掉主进程后 **≤5s 转红**，而看门狗每次请求都
 * 实时探活主服务（连接挂起时最多等 2s）。5s 间隔 + 2s 探测超时实测 7.1s 才转红，
 * 2s 间隔把最坏情况压到 ~4s。
 *
 * 页面隐藏（`visibilitychange`）时暂停轮询，回到前台立刻补一拍。
 * 健康响应只有状态字段，不含任何持仓 / 金额 / 日志内容。
 */

export type PlatformLight = 'unknown' | 'ok' | 'degraded' | 'offline'

export interface PlatformHealth {
  status: 'ok' | 'degraded' | 'offline'
  uptime_sec: number | null
  version: string | null
  db_ok: boolean | null
  checked_at: string | null
  api: {
    status?: string
    uptime_sec?: number
    version?: string
    db_ok?: boolean
  } | null
  watchdog?: { uptime_sec?: number; version?: string } | null
}

export const PLATFORM_POLL_MS = 2000
export const PLATFORM_RESTART_TIMEOUT_MS = 90_000
export const FAILURES_BEFORE_RED = 2
/** 自愈恢复兜底（QUOT-589 重修）：POST 不可达时看门狗自愈，放宽轮询上限。 */
export const SELF_HEAL_RECOVERY_TIMEOUT_MS = 600_000

/**
 * 恒同源（QUOT-589 重修）：健康探测与重启一律走本 origin 的 `/platform/*`，
 * 由主服务自己提供（无独立看门狗端口，全机只留 5950/5951）。
 * 旧实现按 `import.meta.env.DEV` 猜端口，built dist 落在 5951 之类的非 5950
 * 端口时会跨源直打 prod 看门狗 5952——被 CORS 挡住误报「服务离线」，
 * 「重启平台」还会错杀正式服 5950。前端不再猜端口。
 */
export function platformBaseUrl(): string {
  return ''
}

export interface ProbeResult {
  ok: boolean
  body: PlatformHealth | null
}

export interface LightState {
  light: PlatformLight
  failures: number
}

/** 纯状态机：单测直接覆盖，不依赖 React。 */
export function nextLightState(prev: LightState, probe: ProbeResult): LightState {
  if (!probe.ok || probe.body === null) {
    const failures = prev.failures + 1
    return {
      failures,
      light: failures >= FAILURES_BEFORE_RED ? 'offline' : prev.light,
    }
  }
  const body = probe.body
  if (body.status === 'offline') return { light: 'offline', failures: 0 }
  if (body.status === 'degraded' || body.db_ok === false) {
    return { light: 'degraded', failures: 0 }
  }
  return { light: 'ok', failures: 0 }
}

export function lightLabel(light: PlatformLight): string {
  switch (light) {
    case 'ok':
      return '服务正常'
    case 'degraded':
      return '服务降级'
    case 'offline':
      return '服务离线'
    default:
      return '状态探测中'
  }
}

export function formatUptime(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.floor(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  if (minutes > 0) return `${minutes} 分 ${secs} 秒`
  return `${secs} 秒`
}

interface Options {
  baseUrl?: string
  intervalMs?: number
  restartPollMs?: number
  restartTimeoutMs?: number
  fetchImpl?: typeof fetch
  reload?: () => void
}

export interface PlatformHealthApi {
  light: PlatformLight
  health: PlatformHealth | null
  restarting: boolean
  restartError: string | null
  /** 二次确认后由 UI 调用；成功恢复会自动刷新页面。 */
  restart: () => Promise<void>
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function usePlatformHealth(options: Options = {}): PlatformHealthApi {
  const baseUrl = options.baseUrl ?? platformBaseUrl()
  const intervalMs = options.intervalMs ?? PLATFORM_POLL_MS
  const restartPollMs = options.restartPollMs ?? 2000
  const restartTimeoutMs = options.restartTimeoutMs ?? PLATFORM_RESTART_TIMEOUT_MS
  const fetchImpl = options.fetchImpl ?? globalThis.fetch
  const reload = options.reload ?? (() => window.location.reload())

  const [state, setState] = useState<LightState>({ light: 'unknown', failures: 0 })
  const [health, setHealth] = useState<PlatformHealth | null>(null)
  const [restarting, setRestarting] = useState(false)
  const [restartError, setRestartError] = useState<string | null>(null)
  const restartingRef = useRef(false)

  const probeOnce = useCallback(async (): Promise<ProbeResult> => {
    try {
      const response = await fetchImpl(`${baseUrl}/platform/health`, { cache: 'no-store' })
      if (!response.ok) return { ok: false, body: null }
      const body = (await response.json()) as PlatformHealth
      return { ok: true, body }
    } catch {
      return { ok: false, body: null }
    }
  }, [baseUrl, fetchImpl])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const clear = () => {
      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }
    }

    const tick = async () => {
      if (cancelled) return
      if (typeof document !== 'undefined' && document.hidden) return
      const probe = await probeOnce()
      if (cancelled) return
      if (typeof document !== 'undefined' && document.hidden) return
      setHealth(probe.body)
      setState((prev) => nextLightState(prev, probe))
      timer = setTimeout(tick, intervalMs)
    }

    const onVisibility = () => {
      clear()
      if (typeof document !== 'undefined' && !document.hidden) void tick()
    }

    void tick()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelled = true
      clear()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [probeOnce, intervalMs])

  const restart = useCallback(async () => {
    if (restartingRef.current) return
    restartingRef.current = true
    setRestarting(true)
    setRestartError(null)
    // QUOT-589 重修：POST 打不通（fetch 抛错，连接层失败）说明主服务已死，
    // 无端口看门狗正在自愈——不是错误，不停轮询直到恢复（有界兜底见下）；
    // HTTP 明确回错（409/503 等）才是「重启没发出去」，按原有超时兜底提示。
    let requestError: string | null = null
    let unreachable = false
    try {
      const response = await fetchImpl(`${baseUrl}/platform/restart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      const body = (await response.json().catch(() => null)) as
        | { ok?: boolean; reason?: string; detail?: string }
        | null
      if (!response.ok || !body?.ok) {
        requestError =
          body?.reason ?? body?.detail ?? `重启请求失败（HTTP ${response.status}）`
      }
    } catch {
      unreachable = true
    }

    // 自愈模式下轮询没有固定截止：自愈间隔 + 重启 + 探活可能超过 90s。
    // 但无限轮询会卡住 UI 状态，用放宽的兜底（10 分钟）防死循环。
    const deadline =
      Date.now() + (unreachable ? SELF_HEAL_RECOVERY_TIMEOUT_MS : restartTimeoutMs)
    try {
      while (Date.now() < deadline) {
        await sleep(restartPollMs)
        const probe = await probeOnce()
        if (probe.ok && probe.body?.status === 'ok') {
          reload()
          return
        }
      }
      const windowMs =
        unreachable ? SELF_HEAL_RECOVERY_TIMEOUT_MS : restartTimeoutMs
      const timeout = `等了 ${Math.round(windowMs / 1000)} 秒服务还没恢复：请稍后再试一次「重启平台」；若反复失败，可能是本机网络或磁盘出了问题`
      setRestartError(requestError ? `${requestError}；${timeout}` : timeout)
    } finally {
      restartingRef.current = false
      setRestarting(false)
    }
  }, [
    baseUrl,
    fetchImpl,
    probeOnce,
    reload,
    restartPollMs,
    restartTimeoutMs,
  ])

  return {
    light: state.light,
    health,
    restarting,
    restartError,
    restart,
  }
}

export default usePlatformHealth
