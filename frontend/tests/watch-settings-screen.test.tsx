// @vitest-environment jsdom
/**
 * 盯盘阈值设置（DEC-037）：界面写入 → 落 IndexedDB → 新会话读回；
 * 越界输入被拒绝；跨窗口写入能反映到本屏。
 */

import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import WatchScreen from '../src/screens/WatchScreen'
import { KV_STORE, closeStateDatabase, idbDelete, idbGet, idbSet } from '../persist/indexeddb'
import {
  DEFAULT_COOLDOWN_SEC,
  DEFAULT_DETECT_OPEN,
  DEFAULT_THRESHOLD_PCT,
  applyRemoteWatchSettings,
  useWatchSettings,
} from '../stores/watchSettings'

interface Persisted<T> {
  state: T
  version: number
}

async function waitFlush(ms = 80): Promise<void> {
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

function renderScreen() {
  return render(
    <MemoryRouter>
      <WatchScreen />
    </MemoryRouter>,
  )
}

/** 等请求落到离线态、阈值卡片渲染出来（加载骨架里没有输入框）。 */
async function openScreen(): Promise<void> {
  await screen.findByLabelText('涨幅阈值（%）')
}

function thresholdInput(): HTMLInputElement {
  return screen.getByLabelText('涨幅阈值（%）') as HTMLInputElement
}

beforeEach(async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
  resetMemory()
  await waitFlush()
  await closeStateDatabase()
  await idbDelete(KV_STORE, 'watch_settings')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('盯盘阈值持久化', () => {
  it('写入 → 落库 → 新会话读回（刷新 / 重启）', async () => {
    const first = renderScreen()
    await openScreen()
    expect(thresholdInput().value).toBe('3.0')

    fireEvent.change(thresholdInput(), { target: { value: '4.5' } })
    fireEvent.blur(thresholdInput())

    await waitFor(() => expect(useWatchSettings.getState().thresholdPct).toBe(4.5))
    await waitFlush()

    const raw = await idbGet<string>(KV_STORE, 'watch_settings')
    expect(raw).toBeTruthy()
    const shard = JSON.parse(raw as string) as Persisted<Record<string, unknown>>
    expect(shard.version).toBe(2)
    expect(shard.state).toMatchObject({
      thresholdPct: 4.5,
      cooldownSec: DEFAULT_COOLDOWN_SEC,
      detectOpen: DEFAULT_DETECT_OPEN,
    })

    // 模拟关掉窗口后重新打开：内存清空，只剩 IndexedDB 里的分片
    first.unmount()
    resetMemory()
    await waitFlush()
    await idbSet(KV_STORE, 'watch_settings', raw as string)
    await useWatchSettings.persist.rehydrate()

    renderScreen()
    await openScreen()
    expect(thresholdInput().value).toBe('4.5')
  })

  it('越界输入：红边 + 行内提示，不写分片，失焦回到已保存值', async () => {
    useWatchSettings.getState().setThresholdPct(4.5)
    await waitFlush()

    renderScreen()
    await openScreen()

    fireEvent.change(thresholdInput(), { target: { value: '12' } })

    expect(screen.getByText(/阈值需在 1\.0–10\.0 之间/)).toBeTruthy()
    expect(thresholdInput().getAttribute('aria-invalid')).toBe('true')
    expect(useWatchSettings.getState().thresholdPct).toBe(4.5)

    await waitFlush()
    const raw = await idbGet<string>(KV_STORE, 'watch_settings')
    const shard = JSON.parse(raw as string) as Persisted<{ thresholdPct: number }>
    expect(shard.state.thresholdPct).toBe(4.5)

    fireEvent.blur(thresholdInput())
    expect(thresholdInput().value).toBe('4.5')
    expect(thresholdInput().getAttribute('aria-invalid')).toBeNull()
  })

  it('另一个窗口写入后本屏立即读到同一份设置', async () => {
    renderScreen()
    await openScreen()

    expect(
      applyRemoteWatchSettings({
        type: 'watch_settings',
        values: { thresholdPct: 5.5, cooldownSec: 120, detectOpen: false },
        updatedAt: '2026-11-01T02:00:00.000Z',
      }),
    ).toBe(true)

    await waitFor(() => expect(thresholdInput().value).toBe('5.5'))
    expect(useWatchSettings.getState()).toMatchObject({
      thresholdPct: 5.5,
      cooldownSec: 120,
      detectOpen: false,
    })
  })

  it('卡片不再声称「本次会话有效」', async () => {
    renderScreen()
    await openScreen()

    expect(screen.queryByText('本次会话有效')).toBeNull()
    expect(screen.queryByText(/阈值不落盘/)).toBeNull()
    expect(screen.getByText(/刷新 \/ 重启 \/ 多窗口共用/)).toBeTruthy()
  })
})
