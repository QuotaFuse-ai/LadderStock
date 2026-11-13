// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  DEFAULT_HOME_MODE,
  HOME_MODE_STORAGE_KEY,
  readHomeMode,
  useHomeMode,
  writeHomeMode,
} from '../src/lib/homeMode'

/**
 * QUOT-582 / F5：首页模式持久化。
 *
 * 三条口径：
 * 1. 默认对话模式；写进去的值能读回来（刷新 `/` 落回上次那个模式）；
 * 2. 篡改 / 旧值不认识 → 回默认，不抛；
 * 3. 隐私模式下 `localStorage` 读写都会抛：读取降级为默认、写入仍在本会话生效，**都不抛**。
 *
 * 模块内有会话级 `memoryMode`（隐私模式写入的兜底），所以隐私模式一条放在最前，
 * 它后面再断「读不到存储就回默认」才不会被前一条写入的历史值带偏。
 */

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

describe('首页模式持久化（F5）', () => {
  it('localStorage 读写抛错时：写入不抛且本会话生效，读取不抛', () => {
    const getItem = window.localStorage.getItem.bind(window.localStorage)
    const setItem = window.localStorage.setItem.bind(window.localStorage)
    expect(readHomeMode()).toBe('chat')

    window.localStorage.getItem = () => {
      throw new Error('SecurityError: 隐私模式')
    }
    window.localStorage.setItem = () => {
      throw new Error('SecurityError: 隐私模式')
    }
    try {
      expect(() => writeHomeMode('desk')).not.toThrow()
      // 写不进存储，但本会话立刻生效（否则切换器点了没反应）
      expect(readHomeMode()).toBe('desk')
    } finally {
      window.localStorage.getItem = getItem
      window.localStorage.setItem = setItem
    }
    // 存储恢复后能写进去，被正常读到
    writeHomeMode('chat')
    expect(readHomeMode()).toBe('chat')
  })

  it('没有存过时默认对话模式', () => {
    expect(DEFAULT_HOME_MODE).toBe('chat')
    // 上一条恢复了存储并写了 chat；清掉存储后走默认
    window.localStorage.clear()
    expect(readHomeMode()).toBe('chat')
  })

  it('写进去再读回来是同一个模式', () => {
    writeHomeMode('desk')
    expect(window.localStorage.getItem(HOME_MODE_STORAGE_KEY)).toBe('desk')
    expect(readHomeMode()).toBe('desk')

    writeHomeMode('chat')
    expect(readHomeMode()).toBe('chat')
  })

  it('存储里的值不认识时回默认，不抛', () => {
    window.localStorage.setItem(HOME_MODE_STORAGE_KEY, 'whatever')
    expect(readHomeMode()).toBe('chat')
  })

  it('hook 读写与 localStorage 同步，并跟随 storage 事件跨标签更新', () => {
    const { result } = renderHook(() => useHomeMode())
    expect(result.current[0]).toBe('chat')

    act(() => result.current[1]('desk'))
    expect(result.current[0]).toBe('desk')
    expect(window.localStorage.getItem(HOME_MODE_STORAGE_KEY)).toBe('desk')

    // 另一个标签页改了值：storage 事件只发给别的标签，这里手动派发同一个形状
    act(() => {
      window.localStorage.setItem(HOME_MODE_STORAGE_KEY, 'chat')
      window.dispatchEvent(
        new StorageEvent('storage', { key: HOME_MODE_STORAGE_KEY, newValue: 'chat' }),
      )
    })
    expect(result.current[0]).toBe('chat')
  })
})
