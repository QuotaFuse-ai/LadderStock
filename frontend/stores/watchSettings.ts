/**
 * `watchSettings` 分片（IndexedDB key: `watch_settings`，DEC-037）。
 *
 * 盯盘阈值是用户偏好，不是行情快照：没有 TTL，刷新 / 重启 / 多窗口共用同一份。
 * 越界值在入口处拒绝——落库、恢复、跨窗口同步三条路径都不放过非法值。
 * 多窗口同步走 BroadcastChannel：IndexedDB 不产生 `storage` 事件。
 */

import { createPersistedStore } from '../persist/persistedStore'

export const DEFAULT_THRESHOLD_PCT = 3.0
export const THRESHOLD_MIN = 1.0
export const THRESHOLD_MAX = 10.0
export const DEFAULT_COOLDOWN_SEC = 60
export const COOLDOWN_MIN_SEC = 0
export const COOLDOWN_MAX_SEC = 600
export const DEFAULT_DETECT_OPEN = true

export const WATCH_SETTINGS_CHANNEL = 'ldst-watch-settings'
export const WATCH_SETTINGS_MESSAGE = 'watch_settings'

export interface WatchSettingsValues {
  thresholdPct: number
  cooldownSec: number
  detectOpen: boolean
}

export interface WatchSettingsState extends WatchSettingsValues {
  updatedAt: string | null
  /** 越界值返回 false 且不写入；返回值供调用方决定是否提示错误 */
  setThresholdPct: (value: number) => boolean
  setCooldownSec: (value: number) => boolean
  setDetectOpen: (value: boolean) => void
}

export function isValidThresholdPct(value: number): boolean {
  return Number.isFinite(value) && value >= THRESHOLD_MIN && value <= THRESHOLD_MAX
}

export function isValidCooldownSec(value: number): boolean {
  return Number.isFinite(value) && value >= COOLDOWN_MIN_SEC && value <= COOLDOWN_MAX_SEC
}

/** 严格解析：任一字段缺失或越界就整片拒绝，不拿默认值顶替非法输入。 */
export function parseWatchSettings(input: unknown): WatchSettingsValues | null {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) return null
  const record = input as Record<string, unknown>
  const { thresholdPct, cooldownSec, detectOpen } = record
  if (typeof thresholdPct !== 'number' || !isValidThresholdPct(thresholdPct)) return null
  if (typeof cooldownSec !== 'number' || !isValidCooldownSec(cooldownSec)) return null
  if (typeof detectOpen !== 'boolean') return null
  return { thresholdPct, cooldownSec, detectOpen }
}

export const useWatchSettings = createPersistedStore<WatchSettingsState>(
  'watchSettings',
  (set) => ({
    thresholdPct: DEFAULT_THRESHOLD_PCT,
    cooldownSec: DEFAULT_COOLDOWN_SEC,
    detectOpen: DEFAULT_DETECT_OPEN,
    updatedAt: null,
    setThresholdPct: (value) => {
      if (!isValidThresholdPct(value)) return false
      set({ thresholdPct: value, updatedAt: new Date().toISOString() })
      return true
    },
    setCooldownSec: (value) => {
      if (!isValidCooldownSec(value)) return false
      set({ cooldownSec: value, updatedAt: new Date().toISOString() })
      return true
    },
    setDetectOpen: (value) => set({ detectOpen: value, updatedAt: new Date().toISOString() }),
  }),
  {
    sanitize: (persisted) => {
      const values = parseWatchSettings(persisted)
      if (!values) return null
      const updatedAt = (persisted as Record<string, unknown>).updatedAt
      return { ...values, updatedAt: typeof updatedAt === 'string' ? updatedAt : null }
    },
  },
)

function settingsOf(state: WatchSettingsValues): WatchSettingsValues {
  return {
    thresholdPct: state.thresholdPct,
    cooldownSec: state.cooldownSec,
    detectOpen: state.detectOpen,
  }
}

function sameSettings(a: WatchSettingsValues, b: WatchSettingsValues): boolean {
  return (
    a.thresholdPct === b.thresholdPct &&
    a.cooldownSec === b.cooldownSec &&
    a.detectOpen === b.detectOpen
  )
}

/** 防止「应用远端设置」再被订阅器广播回去。 */
let applyingRemote = false

/**
 * 应用另一个窗口广播的设置。非法值整条丢弃；与当前值相同则忽略，
 * 避免两个窗口互相回推形成回声。
 */
export function applyRemoteWatchSettings(payload: unknown): boolean {
  if (typeof payload !== 'object' || payload === null) return false
  const record = payload as Record<string, unknown>
  if (record.type !== WATCH_SETTINGS_MESSAGE) return false
  const next = parseWatchSettings(record.values)
  if (!next) return false
  if (sameSettings(settingsOf(useWatchSettings.getState()), next)) return false
  applyingRemote = true
  try {
    useWatchSettings.setState({
      ...next,
      updatedAt: typeof record.updatedAt === 'string' ? record.updatedAt : new Date().toISOString(),
    })
  } finally {
    applyingRemote = false
  }
  return true
}

function openSyncChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null
  try {
    return new BroadcastChannel(WATCH_SETTINGS_CHANNEL)
  } catch {
    // 无 BroadcastChannel 的宿主（老 WebView）只是失去跨窗口实时同步，设置仍然落盘
    return null
  }
}

const syncChannel = openSyncChannel()

syncChannel?.addEventListener('message', (event) => {
  applyRemoteWatchSettings(event.data)
})

useWatchSettings.subscribe((state, previous) => {
  if (!syncChannel || applyingRemote) return
  const next = settingsOf(state)
  if (sameSettings(next, settingsOf(previous))) return
  syncChannel.postMessage({
    type: WATCH_SETTINGS_MESSAGE,
    values: next,
    updatedAt: state.updatedAt,
  })
})
