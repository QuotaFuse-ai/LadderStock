// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  MASK,
  PRIVACY_STORAGE_KEY,
  hideIf,
  setPrivacyMode,
  usePrivacyMode,
} from '../src/lib/privacy'

/**
 * 隐私模式 store（QUOT-442 / DEC-062 §4）。
 *
 * 硬口径：只遮屏幕显示；`—`（没有数）不遮；百分比与公开行情不在本模块的消费范围。
 */
beforeEach(() => {
  window.localStorage.clear()
  setPrivacyMode(false)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('hideIf', () => {
  it('关闭时原样返回', () => {
    expect(hideIf(false, '12,345.67')).toBe('12,345.67')
    expect(hideIf(false, MASK)).toBe(MASK)
  })

  it('开启时遮金额 / 数量', () => {
    expect(hideIf(true, '12,345.67')).toBe(MASK)
    expect(hideIf(true, '1.24 万')).toBe(MASK)
    expect(hideIf(true, '300')).toBe(MASK)
  })

  it('缺失值 `—` 不遮：遮了反而像有数', () => {
    expect(hideIf(true, '—')).toBe('—')
  })
})

describe('store 持久化与订阅', () => {
  it('toggle 写 localStorage，并广播给订阅者', () => {
    const { result } = renderHook(() => usePrivacyMode())
    expect(result.current.on).toBe(false)

    act(() => result.current.toggle())

    expect(result.current.on).toBe(true)
    expect(window.localStorage.getItem(PRIVACY_STORAGE_KEY)).toBe('on')

    act(() => result.current.toggle())
    expect(result.current.on).toBe(false)
    expect(window.localStorage.getItem(PRIVACY_STORAGE_KEY)).toBe('off')
  })

  it('读取存储失败按关闭处理，写入失败不抛', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })

    expect(() => setPrivacyMode(true)).not.toThrow()
    expect(window.localStorage.getItem(PRIVACY_STORAGE_KEY)).not.toBe('on')
  })
})
