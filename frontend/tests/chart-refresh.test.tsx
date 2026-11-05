// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  FALLBACK_REFRESH_SEC,
  REFRESH_STORAGE_KEY,
  SECOND_LEVEL_TIERS,
  availableTiers,
  defaultRefreshSec,
  readRefreshSec,
  useAutoRefresh,
  useChartRefresh,
  writeRefreshSec,
} from '../src/lib/chartRefresh'

/**
 * QUOT-407：图表刷新频率口径。
 *
 * 三条红线：
 * 1. 默认档位是 15s，且下拉只出现 15 / 30 / 60；
 * 2. 秒级档位（5 / 1）保留在代码里但默认不暴露；
 * 3. 自动刷新按所选秒数触发，页面在后台时跳过。
 */

afterEach(() => {
  window.localStorage.clear()
  vi.useRealTimers()
})

describe('档位与默认值', () => {
  it('默认只暴露 15 / 30 / 60', () => {
    expect(availableTiers(false)).toEqual([15, 30, 60])
    expect(availableTiers(true)).toEqual([1, 5, 15, 30, 60])
    expect(SECOND_LEVEL_TIERS).toEqual([5, 1])
  })

  it('默认档是 15s', () => {
    expect(defaultRefreshSec(false)).toBe(FALLBACK_REFRESH_SEC)
    expect(FALLBACK_REFRESH_SEC).toBe(15)
  })

  it('localStorage 里的非法档位回落到默认值', () => {
    window.localStorage.setItem(REFRESH_STORAGE_KEY, '7')
    expect(readRefreshSec(availableTiers(false))).toBe(15)
  })

  it('选中档位会持久化', () => {
    writeRefreshSec(30)
    expect(readRefreshSec(availableTiers(false))).toBe(30)
  })
})

describe('useChartRefresh', () => {
  it('默认 15s，切换后持久化并拒绝未开放档位', () => {
    const { result } = renderHook(() => useChartRefresh())
    expect(result.current.seconds).toBe(15)
    expect(result.current.options.map((option) => option.value)).toEqual(['15', '30', '60'])

    act(() => result.current.setSeconds(60))
    expect(result.current.seconds).toBe(60)
    expect(window.localStorage.getItem(REFRESH_STORAGE_KEY)).toBe('60')

    act(() => result.current.setSeconds(1))
    expect(result.current.seconds).toBe(60)
  })
})

describe('useAutoRefresh', () => {
  it('按所选秒数触发，未到点不触发', () => {
    vi.useFakeTimers()
    const tick = vi.fn()
    renderHook(() => useAutoRefresh(tick, 15))

    act(() => vi.advanceTimersByTime(14_000))
    expect(tick).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(1_000))
    expect(tick).toHaveBeenCalledTimes(1)

    act(() => vi.advanceTimersByTime(15_000))
    expect(tick).toHaveBeenCalledTimes(2)
  })

  it('页面在后台时跳过这一拍', () => {
    vi.useFakeTimers()
    const tick = vi.fn()
    renderHook(() => useAutoRefresh(tick, 15))

    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    act(() => vi.advanceTimersByTime(15_000))
    expect(tick).not.toHaveBeenCalled()

    hidden.mockReturnValue('visible')
    act(() => vi.advanceTimersByTime(15_000))
    expect(tick).toHaveBeenCalledTimes(1)
    hidden.mockRestore()
  })

  it('关闭时不计时', () => {
    vi.useFakeTimers()
    const tick = vi.fn()
    renderHook(() => useAutoRefresh(tick, 15, false))
    act(() => vi.advanceTimersByTime(60_000))
    expect(tick).not.toHaveBeenCalled()
  })
})
