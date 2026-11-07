/**
 * 带计数与指数退避的静默轮询（QUOT-470）。
 *
 * 为什么不用裸 `setInterval`（`chartRefresh.ts:useAutoRefresh` / `PoolScreen` 的写法）：
 * 首页指数条要遵守 QUOT-407/QUOT-465 的节拍结论——15s 档在商业出口池下可撑，但
 * **单 IP 连续高频无结论**，所以前端必须把「发了多少次」摆在明面上，并且连续失败时
 * 指数退避（不空转刷屏）。裸 `setInterval` 两者都做不到：它既不知道这一拍成功没有，
 * 也不会因为失败放慢。
 *
 * 自链式 `setTimeout`：每一拍结束后才排下一拍，退避系数按当前连续失败数取；
 * 页面在后台（`document.hidden`）时跳过这一拍，不把上游额度花在看不见的标签页上。
 */

import { useEffect, useRef, useState } from 'react'

/** 指数退避封顶（毫秒）：免得盘中断网时把轮询拖成一小时一次。 */
export const MAX_BACKOFF_MS = 120_000

export interface PollingOptions {
  /** 基础节拍（毫秒）；`<= 0` 视作不轮询。 */
  intervalMs: number
  /** 是否启用（休市 / 演示 / 强制态时不轮询）。 */
  enabled: boolean
  /**
   * 一拍要做的事。判成败有两种写法：返回 `false`（`useAsync.refresh` 的成功信号，
   * 不抛异常）或抛异常（自定义 tick）。两者都算失败，都会让退避计数 +1。
   */
  onTick: () => void | boolean | Promise<void | boolean>
}

export interface PollingState {
  /** 已发出的请求计数（成功 + 失败）。验收口径「2 分钟 ≈ 8 次」读的就是它。 */
  ticks: number
  /** 连续失败次数（一拍成功即归零），供退避与「重试」提示取用。 */
  failures: number
}

/** 第 n 次连续失败后的等待：interval × 2^n，封顶 `MAX_BACKOFF_MS`。 */
export function backoffDelay(intervalMs: number, failures: number): number {
  if (failures <= 0) return intervalMs
  return Math.min(intervalMs * 2 ** failures, MAX_BACKOFF_MS)
}

/**
 * 静默轮询：`onTick` 走 ref，重渲染不重置计时；`enabled` 由调用方按交易时段 / 演示态给。
 * 返回 `{ ticks, failures }`，屏侧据此显示「每 N 秒刷新（已请求 M 次）」并在退避时给「重试」。
 */
export function usePolling({ intervalMs, enabled, onTick }: PollingOptions): PollingState {
  const saved = useRef(onTick)
  saved.current = onTick
  const [ticks, setTicks] = useState(0)
  const [failures, setFailures] = useState(0)

  useEffect(() => {
    if (!enabled || !Number.isFinite(intervalMs) || intervalMs <= 0) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let consecutive = 0

    const schedule = (delayMs: number) => {
      timer = setTimeout(run, delayMs)
    }

    const run = async () => {
      if (cancelled) return
      // 后台标签页跳过这一拍：不计入计数、不消耗上游额度，等回到前台再按基础节拍继续。
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        schedule(intervalMs)
        return
      }
      // 成败信号：`false` 或抛异常都算这一拍失败（`useAsync.refresh` 用返回值，不用异常）。
      let ok = true
      try {
        if ((await saved.current()) === false) ok = false
      } catch {
        ok = false
      }
      if (cancelled) return
      setTicks((count) => count + 1)
      consecutive = ok ? 0 : consecutive + 1
      setFailures(consecutive)
      schedule(backoffDelay(intervalMs, consecutive))
    }

    schedule(intervalMs)
    return () => {
      cancelled = true
      if (timer !== null) clearTimeout(timer)
    }
  }, [enabled, intervalMs])

  return { ticks, failures }
}
