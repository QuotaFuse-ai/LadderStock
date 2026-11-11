import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  api,
  describeApiError,
  isApiError,
  type SupervisorEventType,
  type SupervisorHealthEvent,
} from '../api'

/**
 * peer 级健康事件订阅（QUOT-556 / S5）：全局弹窗的取数层。
 *
 * 数据只来自 `GET /api/supervisor/events?since=<seq>` 轮询（DEC-080 对接建议，
 * 不用 SSE）：后端只发跃迁（crash / offline / port_conflict / needs_human /
 * recovered），坏事件入「活跃表」，`recovered` 把对应模块移出——弹窗自动关闭。
 *
 * 两条口径：
 * - **首拍只对齐不弹**：页面打开前环形缓冲里的历史事件（用户没见过现场）不打扰，
 *   只记 `latest` 作基线；之后每一拍只消费「会话内新到」的事件。
 * - **通道失败不弹全局错误**：事件通道的断连不该在所有页面顶上刷屏；设置页
 *   「子模块」分区有完整的状态与错误展示。`configured=false`（总线未接线）或
 *   端点 404（后端未合入）→ 停止轮询，其余错误下一拍再试。
 *
 * 状态机是纯函数（`nextAlerts` / `pickActive`），单测不依赖 React。
 */

export const PEER_HEALTH_POLL_MS = 4000

/** 会触发弹窗的坏事件；`recovered` 只用于关窗。 */
export const BAD_EVENTS: ReadonlySet<SupervisorEventType> = new Set([
  'crash',
  'offline',
  'port_conflict',
  'needs_human',
])

/** 活跃坏事件表：module → 该模块最新一条坏事件（同模块新事件覆盖旧事件）。 */
export type PeerAlerts = Record<string, SupervisorHealthEvent>

/** 用户手动关闭记录：module → 已关的最大 seq（同一条不再弹；新 seq 仍会弹）。 */
export type PeerDismissed = Record<string, number>

/** 纯状态机：消费一批按 seq 升序的新事件（recovered 清表、坏事件覆盖入表）。 */
export function nextAlerts(
  alerts: PeerAlerts,
  incoming: readonly SupervisorHealthEvent[],
): PeerAlerts {
  let next = alerts
  for (const item of incoming) {
    if (item.event === 'recovered') {
      if (next[item.module] !== undefined) {
        const { [item.module]: _gone, ...rest } = next
        next = rest
      }
      continue
    }
    if (!BAD_EVENTS.has(item.event)) continue
    const current = next[item.module]
    if (current && current.seq >= item.seq) continue
    next = { ...next, [item.module]: item }
  }
  return next
}

export function dismissAlert(dismissed: PeerDismissed, event: SupervisorHealthEvent): PeerDismissed {
  const current = dismissed[event.module] ?? 0
  if (current >= event.seq) return dismissed
  return { ...dismissed, [event.module]: event.seq }
}

/** 当前该弹的事件：未被手动关闭的坏事件里 seq 最新的一条；都关了 → null。 */
export function pickActive(
  alerts: PeerAlerts,
  dismissed: PeerDismissed,
): SupervisorHealthEvent | null {
  const items = Object.values(alerts).filter((item) => item.seq > (dismissed[item.module] ?? 0))
  if (items.length === 0) return null
  return items.reduce((latest, item) => (item.seq > latest.seq ? item : latest))
}

export interface PeerHealthApi {
  /** 当前弹窗展示的事件（最新一条未关闭的坏事件）；null = 不弹 */
  active: SupervisorHealthEvent | null
  /** 未关闭的坏事件数（含 active）：多个模块同时坏时弹窗上给个总数 */
  pendingCount: number
  /** 确认重启进行中：主按钮 loading、防重复点击 */
  restarting: boolean
  /** 重启失败原因（弹窗内如实展示，不静默） */
  restartError: string | null
  /** 弹窗主操作：`POST /supervisor/restart {modules:[..], confirm:true}`。 */
  confirmRestart: () => Promise<void>
  /** 「稍后再说」：关掉当前事件（同一条不再弹，新事件仍会弹） */
  dismiss: () => void
}

interface Options {
  intervalMs?: number
  /** 测试注入；缺省走 api.supervisor.events */
  fetchEvents?: (since: number) => Promise<{
    configured: boolean
    latest: number
    events: SupervisorHealthEvent[]
  }>
  /** 测试注入；缺省走 api.supervisor.restart */
  restartModule?: (module: string) => Promise<{ ok: boolean; error: string | null }>
}

async function restartViaApi(module: string): Promise<{ ok: boolean; error: string | null }> {
  const payload = await api.supervisor.restart({ modules: [module], confirm: true })
  const result = payload.modules?.[module]
  return { ok: Boolean(payload.ok && result?.ok), error: result?.error ?? null }
}

export function usePeerHealth(options: Options = {}): PeerHealthApi {
  const intervalMs = options.intervalMs ?? PEER_HEALTH_POLL_MS
  const fetchEvents = options.fetchEvents ?? ((since: number) => api.supervisor.events(since))
  const restartImpl = options.restartModule ?? restartViaApi

  const [alerts, setAlerts] = useState<PeerAlerts>({})
  const [dismissed, setDismissed] = useState<PeerDismissed>({})
  const [restarting, setRestarting] = useState(false)
  const [restartError, setRestartError] = useState<string | null>(null)
  /** 总线未接线 / 端点 404：通道不可用，停止轮询 */
  const [stopped, setStopped] = useState(false)

  const sinceRef = useRef(0)
  const alignedRef = useRef(false)
  const restartingRef = useRef(false)

  const active = useMemo(() => pickActive(alerts, dismissed), [alerts, dismissed])
  const pendingCount = useMemo(
    () => Object.values(alerts).filter((item) => item.seq > (dismissed[item.module] ?? 0)).length,
    [alerts, dismissed],
  )

  useEffect(() => {
    if (stopped) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const clear = () => {
      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }
    }

    const schedule = () => {
      if (cancelled) return
      timer = setTimeout(() => void tick(), intervalMs)
    }

    const tick = async () => {
      if (cancelled) return
      if (typeof document !== 'undefined' && document.hidden) return
      try {
        const payload = await fetchEvents(sinceRef.current)
        if (cancelled) return
        if (!payload.configured) {
          setStopped(true)
          return // 通道未接线：不再自拍
        }
        if (!alignedRef.current) {
          // 首拍只对齐基线：历史事件不弹（页面没开着时发生的坏不打扰）
          alignedRef.current = true
          sinceRef.current = payload.latest
        } else {
          sinceRef.current = payload.latest
          if (payload.events.length > 0) setAlerts((prev) => nextAlerts(prev, payload.events))
        }
        schedule()
        return
      } catch (error) {
        // 404 = 后端还没有这个端点：与 configured=false 同等待遇，停轮询不刷屏
        if (isApiError(error) && error.kind === 'http' && error.status === 404) {
          setStopped(true)
          return
        }
      }
      // 其余错误（网络抖动 / 服务重启中）下一拍再试
      schedule()
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
  }, [fetchEvents, intervalMs, stopped])

  const confirmRestart = useCallback(async () => {
    if (!active || restartingRef.current) return
    restartingRef.current = true
    setRestarting(true)
    setRestartError(null)
    try {
      const result = await restartImpl(active.module)
      if (result.ok) {
        // 已确认处理：关掉弹窗；随后到达的 recovered 不会再弹。
        // 若该模块之后又坏，新 seq 的事件照常弹。
        setDismissed((prev) => dismissAlert(prev, active))
      } else {
        setRestartError(result.error ?? '重启未成功（后端未返回原因）')
      }
    } catch (error) {
      setRestartError(describeApiError(error).detail)
    } finally {
      restartingRef.current = false
      setRestarting(false)
    }
  }, [active, restartImpl])

  const dismiss = useCallback(() => {
    if (active) setDismissed((prev) => dismissAlert(prev, active))
  }, [active])

  return { active, pendingCount, restarting, restartError, confirmRestart, dismiss }
}

export default usePeerHealth
