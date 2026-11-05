/**
 * 图表自动刷新频率（QUOT-407）。
 *
 * 口径（2026-11-05 老板拍板）：
 * - 当前阶段只有 15s / 30s / 60s 三档，**默认 15s**；
 * - 秒级档（5s / 1s）保留定义但默认不出现在下拉里，等商业化 IP 代理池就位后
 *   用 `VITE_LDST_CHART_REFRESH_SECOND_LEVEL=1` 放开；
 * - 默认档可用 `VITE_LDST_CHART_REFRESH_SEC` 覆盖（只接受已开放的档位，非法值回落到 15s）。
 *
 * 这只决定**界面多久重新读一次数据**。真正的上游请求频率仍由 TickHub 的端点闸门和
 * 缓存 TTL 决定（当前 25s 闸门 / 60s TTL），把界面调到 15s 不会让上游请求变成 15s 一次。
 */

import { useEffect, useRef, useState } from 'react'

/** 已开放的档位，升序。 */
export const CHART_REFRESH_TIERS = [15, 30, 60] as const
/** 秒级档位：保留，默认不暴露。 */
export const SECOND_LEVEL_TIERS = [5, 1] as const

export const REFRESH_STORAGE_KEY = 'ldst.chart.refreshSec'
export const FALLBACK_REFRESH_SEC = 15

function readSecondLevelEnabled(): boolean {
  const raw = import.meta.env.VITE_LDST_CHART_REFRESH_SECOND_LEVEL as string | undefined
  return String(raw ?? '').trim().toLowerCase() === '1' || String(raw ?? '').trim().toLowerCase() === 'true'
}

/** 下拉里实际可选的档位（秒级只在商业代理池就位后打开）。 */
export function availableTiers(secondLevelEnabled = readSecondLevelEnabled()): number[] {
  const tiers = secondLevelEnabled ? [...SECOND_LEVEL_TIERS, ...CHART_REFRESH_TIERS] : [...CHART_REFRESH_TIERS]
  return [...tiers].sort((a, b) => a - b)
}

/** 默认档：env 指定且在可用档位内才采纳，否则 15s。 */
export function defaultRefreshSec(secondLevelEnabled = readSecondLevelEnabled()): number {
  const raw = Number(import.meta.env.VITE_LDST_CHART_REFRESH_SEC ?? '')
  const tiers = availableTiers(secondLevelEnabled)
  return Number.isFinite(raw) && tiers.includes(raw) ? raw : FALLBACK_REFRESH_SEC
}

export function readRefreshSec(tiers: number[] = availableTiers()): number {
  try {
    const raw = Number(window.localStorage.getItem(REFRESH_STORAGE_KEY))
    if (Number.isFinite(raw) && tiers.includes(raw)) return raw
  } catch {
    /* 隐私模式禁用 localStorage：按默认档处理 */
  }
  return defaultRefreshSec()
}

export function writeRefreshSec(seconds: number): void {
  try {
    window.localStorage.setItem(REFRESH_STORAGE_KEY, String(seconds))
  } catch {
    /* 存不下就只在本次会话生效 */
  }
}

export interface ChartRefreshState {
  seconds: number
  options: Array<{ value: string; label: string }>
  setSeconds: (seconds: number) => void
}

/** 频率偏好：下拉与自动刷新共用一份状态。 */
export function useChartRefresh(): ChartRefreshState {
  const tiers = availableTiers()
  const [seconds, setState] = useState<number>(() => readRefreshSec(tiers))
  const setSeconds = (next: number) => {
    if (!tiers.includes(next)) return
    setState(next)
    writeRefreshSec(next)
  }
  return {
    seconds,
    options: tiers.map((tier) => ({ value: String(tier), label: `${tier}s` })),
    setSeconds,
  }
}

/**
 * 按秒数定时回调；页面在后台时跳过这一拍（`document.hidden`），
 * 避免把上游额度花在看不见的标签页上。回调走 ref，重渲染不会重置计时。
 */
export function useAutoRefresh(
  callback: () => void,
  seconds: number,
  enabled = true,
): void {
  const saved = useRef(callback)
  saved.current = callback
  useEffect(() => {
    if (!enabled || !Number.isFinite(seconds) || seconds <= 0) return
    const timer = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      saved.current()
    }, seconds * 1000)
    return () => window.clearInterval(timer)
  }, [enabled, seconds])
}
