/**
 * `watch_settings` 分片：区间校验、落库、重启恢复、坏分片拒绝（DEC-037）。
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it } from 'vitest'

import { KV_STORE, closeStateDatabase, idbDelete, idbGet, idbSet } from '../persist/indexeddb'
import {
  DEFAULT_COOLDOWN_SEC,
  DEFAULT_DETECT_OPEN,
  DEFAULT_THRESHOLD_PCT,
  isValidCooldownSec,
  isValidThresholdPct,
  parseWatchSettings,
  useWatchSettings,
} from '../stores/watchSettings'

interface Persisted<T> {
  state: T
  version: number
}

async function waitFlush(ms = 60): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

function resetMemory(): void {
  useWatchSettings.setState({
    thresholdPct: DEFAULT_THRESHOLD_PCT,
    cooldownSec: DEFAULT_COOLDOWN_SEC,
    detectOpen: DEFAULT_DETECT_OPEN,
    updatedAt: null,
  })
}

function seed(state: Record<string, unknown>): Promise<unknown> {
  return idbSet(KV_STORE, 'watch_settings', JSON.stringify({ version: 2, state }))
}

beforeEach(async () => {
  resetMemory()
  await waitFlush()
  await closeStateDatabase()
  await idbDelete(KV_STORE, 'watch_settings')
})

describe('区间校验', () => {
  it('涨幅阈值只接受 1.0–10.0（含边界）', () => {
    expect(isValidThresholdPct(1)).toBe(true)
    expect(isValidThresholdPct(3.5)).toBe(true)
    expect(isValidThresholdPct(10)).toBe(true)
    expect(isValidThresholdPct(0.9)).toBe(false)
    expect(isValidThresholdPct(10.1)).toBe(false)
    expect(isValidThresholdPct(Number.NaN)).toBe(false)
    expect(isValidThresholdPct(Number.POSITIVE_INFINITY)).toBe(false)
  })

  it('冷却时间只接受 0–600（含边界）', () => {
    expect(isValidCooldownSec(0)).toBe(true)
    expect(isValidCooldownSec(600)).toBe(true)
    expect(isValidCooldownSec(-1)).toBe(false)
    expect(isValidCooldownSec(601)).toBe(false)
    expect(isValidCooldownSec(Number.NaN)).toBe(false)
  })

  it('严格解析：任一字段缺失/越界就整条拒绝', () => {
    expect(parseWatchSettings({ thresholdPct: 4, cooldownSec: 60, detectOpen: true })).toEqual({
      thresholdPct: 4,
      cooldownSec: 60,
      detectOpen: true,
    })
    expect(parseWatchSettings({ thresholdPct: 11, cooldownSec: 60, detectOpen: true })).toBeNull()
    expect(parseWatchSettings({ thresholdPct: 4, cooldownSec: 601, detectOpen: true })).toBeNull()
    expect(parseWatchSettings({ thresholdPct: 4, cooldownSec: 60 })).toBeNull()
    expect(parseWatchSettings({ thresholdPct: '4', cooldownSec: 60, detectOpen: true })).toBeNull()
    expect(parseWatchSettings(null)).toBeNull()
  })
})

describe('写入与拒绝', () => {
  it('合法值写入内存并带 updatedAt', () => {
    expect(useWatchSettings.getState().setThresholdPct(4.5)).toBe(true)
    expect(useWatchSettings.getState().setCooldownSec(180)).toBe(true)
    useWatchSettings.getState().setDetectOpen(false)

    const state = useWatchSettings.getState()
    expect(state).toMatchObject({ thresholdPct: 4.5, cooldownSec: 180, detectOpen: false })
    expect(state.updatedAt).not.toBeNull()
  })

  it('越界值被拒绝：内存与分片都保持上一个合法值', async () => {
    useWatchSettings.getState().setThresholdPct(4.5)
    await waitFlush()

    expect(useWatchSettings.getState().setThresholdPct(12)).toBe(false)
    expect(useWatchSettings.getState().setThresholdPct(0.5)).toBe(false)
    expect(useWatchSettings.getState().setCooldownSec(900)).toBe(false)
    await waitFlush()

    expect(useWatchSettings.getState()).toMatchObject({ thresholdPct: 4.5, cooldownSec: 60 })
    const raw = await idbGet<string>(KV_STORE, 'watch_settings')
    const shard = JSON.parse(raw as string) as Persisted<{ thresholdPct: number; cooldownSec: number }>
    expect(shard.state.thresholdPct).toBe(4.5)
    expect(shard.state.cooldownSec).toBe(60)
  })
})

describe('刷新 / 重启读回', () => {
  it('rehydrate 从分片恢复三个字段与更新时间', async () => {
    await seed({
      thresholdPct: 7,
      cooldownSec: 180,
      detectOpen: false,
      updatedAt: '2026-11-01T01:00:00.000Z',
    })

    await useWatchSettings.persist.rehydrate()

    expect(useWatchSettings.getState()).toMatchObject({
      thresholdPct: 7,
      cooldownSec: 180,
      detectOpen: false,
      updatedAt: '2026-11-01T01:00:00.000Z',
    })
  })

  it('落库的越界值不会被当成合法设置读回（整片回到默认）', async () => {
    await seed({
      thresholdPct: 99,
      cooldownSec: 180,
      detectOpen: false,
      updatedAt: '2026-11-01T01:00:00.000Z',
    })

    await useWatchSettings.persist.rehydrate()

    expect(useWatchSettings.getState()).toMatchObject({
      thresholdPct: DEFAULT_THRESHOLD_PCT,
      cooldownSec: DEFAULT_COOLDOWN_SEC,
      detectOpen: DEFAULT_DETECT_OPEN,
    })
  })
})
