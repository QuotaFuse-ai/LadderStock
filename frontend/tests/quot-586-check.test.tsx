// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// jsdom 没有 canvas / ResizeObserver，研究台图表组件挂载会炸，桩掉 uPlot 即可
vi.mock('uplot', () => ({
  default: class {
    constructor(_opts: unknown, _data: unknown, el: HTMLElement) { void el }
    destroy() {}
    setSize() {}
    setData() {}
  },
}))

import { MemoryRouter } from 'react-router-dom'
import App from '../src/App'
import { HOME_MODE_STORAGE_KEY } from '../src/lib/homeMode'

/**
 * QUOT-586：对话/研究台两区互斥显示。
 * 根因是 `hidden` 属性被 `.flex` 覆盖；修法是显式 class 切换 + 全局 `[hidden]{display:none!important}` 兜底。
 */

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
})

describe('QUOT-586 模式互斥', () => {
  it('desk 模式：对话区 hidden 且类名为 hidden（不带 flex）', async () => {
    window.localStorage.setItem(HOME_MODE_STORAGE_KEY, 'desk')
    render(<MemoryRouter><App /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('composer')).toBeTruthy())
    const chatRegion = screen.getByTestId('composer').closest('div[hidden]')
    expect(chatRegion).toBeTruthy()
    expect(chatRegion!.className).toContain('hidden')
    expect(chatRegion!.className).not.toMatch(/(^|\s)flex(\s|$)/)
  })

  it('chat 模式：对话区类名含 flex 不含 hidden', async () => {
    window.localStorage.setItem(HOME_MODE_STORAGE_KEY, 'chat')
    render(<MemoryRouter><App /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('composer')).toBeTruthy())
    const chatRegion = screen.getByTestId('composer').closest('div')
    expect(chatRegion!.className).toMatch(/(^|\s)flex(\s|$)/)
    expect(chatRegion!.className).not.toContain('hidden')
  })
})
