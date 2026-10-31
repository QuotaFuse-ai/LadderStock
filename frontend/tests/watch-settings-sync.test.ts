/**
 * 多窗口同步（DEC-037）：BroadcastChannel 广播合法设置、忽略非法/重复消息。
 *
 * 用假频道在建店前替换全局 BroadcastChannel，验证的是 store 的接线而不是浏览器实现。
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = []

  readonly name: string
  readonly posted: unknown[] = []
  private readonly listeners = new Set<(event: MessageEvent) => void>()

  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.instances.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    if (type === 'message') this.listeners.add(listener)
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void): void {
    if (type === 'message') this.listeners.delete(listener)
  }

  postMessage(data: unknown): void {
    this.posted.push(data)
  }

  close(): void {
    this.listeners.clear()
  }

  emit(data: unknown): void {
    for (const listener of this.listeners) listener({ data } as MessageEvent)
  }
}

vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)

const { WATCH_SETTINGS_MESSAGE, applyRemoteWatchSettings, useWatchSettings } = await import(
  '../stores/watchSettings'
)

function channel(): FakeBroadcastChannel {
  const first = FakeBroadcastChannel.instances[0]
  if (!first) throw new Error('watchSettings 没有打开 BroadcastChannel')
  return first
}

beforeEach(() => {
  useWatchSettings.setState({
    thresholdPct: 3,
    cooldownSec: 60,
    detectOpen: true,
    updatedAt: null,
  })
  channel().posted.length = 0
})

describe('多窗口同步', () => {
  it('合法改动广播给其他窗口', () => {
    expect(useWatchSettings.getState().setThresholdPct(4.5)).toBe(true)

    expect(channel().posted).toHaveLength(1)
    expect(channel().posted[0]).toEqual({
      type: WATCH_SETTINGS_MESSAGE,
      values: { thresholdPct: 4.5, cooldownSec: 60, detectOpen: true },
      updatedAt: expect.any(String),
    })
  })

  it('越界值不广播也不进内存', () => {
    expect(useWatchSettings.getState().setThresholdPct(12)).toBe(false)
    expect(useWatchSettings.getState().setCooldownSec(601)).toBe(false)
    expect(channel().posted).toHaveLength(0)
    expect(useWatchSettings.getState()).toMatchObject({ thresholdPct: 3, cooldownSec: 60 })
  })

  it('收到另一窗口的合法设置后更新，且不回推', () => {
    channel().emit({
      type: WATCH_SETTINGS_MESSAGE,
      values: { thresholdPct: 7.5, cooldownSec: 180, detectOpen: false },
      updatedAt: '2026-11-01T02:00:00.000Z',
    })

    expect(useWatchSettings.getState()).toMatchObject({
      thresholdPct: 7.5,
      cooldownSec: 180,
      detectOpen: false,
      updatedAt: '2026-11-01T02:00:00.000Z',
    })
    expect(channel().posted).toHaveLength(0)
  })

  it('远端越界值被整条丢弃', () => {
    expect(
      applyRemoteWatchSettings({
        type: WATCH_SETTINGS_MESSAGE,
        values: { thresholdPct: 99, cooldownSec: 180, detectOpen: false },
      }),
    ).toBe(false)
    expect(useWatchSettings.getState()).toMatchObject({ thresholdPct: 3, cooldownSec: 60 })
    expect(channel().posted).toHaveLength(0)
  })

  it('远端值与本机一致时忽略（防回声）', () => {
    expect(
      applyRemoteWatchSettings({
        type: WATCH_SETTINGS_MESSAGE,
        values: { thresholdPct: 3, cooldownSec: 60, detectOpen: true },
      }),
    ).toBe(false)
    expect(channel().posted).toHaveLength(0)
  })
})
