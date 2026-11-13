// @vitest-environment jsdom
/**
 * QUOT-590：首页 ask 入口收敛回归测。
 *
 * 根因：顶栏「问 AI」在 `/` 打开全局 `AiDock` 抽屉，而抽屉里的研究聊天与
 * desk「研究股票」页签是**同一颗 `HomeResearchChat` 的第二实例**。
 *
 * 目标态（能力零回退）：
 * - home 上点「问 AI」**不开抽屉**，改为聚焦当前模式的既有输入框：
 *   desk → 切「研究股票」页签 + 聚焦底部唯一输入框；chat → 聚焦 composer；
 *   只聚焦、不预填、不自动发送；
 * - 非 home 页问 AI 行为完全不变（仍开抽屉）；
 * - 重复点击仍能再次聚焦（seq 递增，不是布尔翻转）。
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
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

import App from '../src/App'
import { HOME_MODE_STORAGE_KEY } from '../src/lib/homeMode'
import TopbarTools from '../src/components/TopbarTools'

function stubMatchMedia(): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

function renderApp(initialPath = '/'): void {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App />
    </MemoryRouter>,
  )
}

function topbar(): HTMLElement {
  const element = document.querySelector<HTMLElement>('header.workspace-topbar')
  if (!element) throw new Error('顶栏没有渲染')
  return element
}

function askAiInTopbar(): HTMLElement {
  // 首页上按钮文案是「聚焦研究 / 对话输入」，非 home 是「问 AI」
  return (
    within(topbar()).queryByRole('button', { name: '聚焦研究 / 对话输入' }) ??
    within(topbar()).getByRole('button', { name: '问 AI' })
  )
}

beforeEach(() => {
  window.localStorage.clear()
  stubMatchMedia()
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('首页「问 AI」聚焦既有面、不开抽屉', () => {
  it('desk 模式：切「研究股票」页签并聚焦底部唯一输入框，不预填、不发请求', async () => {
    window.localStorage.setItem(HOME_MODE_STORAGE_KEY, 'desk')
    renderApp()
    const button = askAiInTopbar()

    // 文案改为聚焦语义，不再是「打开抽屉」
    expect(button.getAttribute('aria-label')).toBe('聚焦研究 / 对话输入')
    expect(button.getAttribute('aria-haspopup')).toBeNull()

    fireEvent.click(button)

    const input = await waitFor(() => {
      const box = screen.getByRole('textbox', { name: '搜索或提问' }) as HTMLTextAreaElement
      expect(document.activeElement).toBe(box)
      return box
    })
    // 只聚焦不预填
    expect(input.value).toBe('')
    const tab = screen.getByRole('tab', { selected: true })
    expect(tab.textContent).toContain('研究股票')
    // 不产生抽屉里的研究助手
    expect(screen.queryByRole('heading', { name: /研究助手/ })).toBeNull()
    // 不自动发送：`?ai=1` 深链没出现（抽屉会经 buildDockView 预填），研究面也没有任何外发
    expect(window.location.search).not.toContain('ai=1')
  })

  it('chat 模式：聚焦对话 composer，不开抽屉', async () => {
    renderApp()
    fireEvent.click(askAiInTopbar())

    const composer = await waitFor(() => {
      const box = screen.getByRole('textbox', { name: '向对话提问' }) as HTMLTextAreaElement
      expect(document.activeElement).toBe(box)
      return box
    })
    expect(composer.value).toBe('')
    expect(screen.queryByRole('heading', { name: /研究助手/ })).toBeNull()
  })

  it('重复点击仍能再次聚焦（seq 递增，不是一次性开关）', async () => {
    renderApp()
    fireEvent.click(askAiInTopbar())
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: '向对话提问' })))

    // 点别处把焦点拿走，再点一次问 AI：仍要聚焦回来
    ;(document.activeElement as HTMLElement).blur()
    expect(document.activeElement).not.toBe(screen.getByRole('textbox', { name: '向对话提问' }))
    fireEvent.click(askAiInTopbar())
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: '向对话提问' })))
    expect(screen.queryByRole('heading', { name: /研究助手/ })).toBeNull()
  })

  it('chat 模式聚焦后切到研究台再点：按当前模式分流，聚焦 desk 研究面', async () => {
    renderApp()
    fireEvent.click(askAiInTopbar())
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: '向对话提问' })))

    // 切到研究台
    fireEvent.click(screen.getByRole('radio', { name: '研究台' }))
    fireEvent.click(askAiInTopbar())

    await waitFor(() => {
      const box = screen.getByRole('textbox', { name: '搜索或提问' }) as HTMLTextAreaElement
      expect(document.activeElement).toBe(box)
    })
    expect(screen.getByRole('tab', { selected: true }).textContent).toContain('研究股票')
  })
})

describe('非 home 页：问 AI 行为完全不变', () => {
  it('/market 点问 AI 仍打开全局抽屉（文案与 hasPopup 不变）', () => {
    renderApp('/market')
    const button = askAiInTopbar()

    expect(button.getAttribute('aria-label')).toBe('问 AI')
    expect(button.getAttribute('aria-haspopup')).toBe('dialog')

    fireEvent.click(button)
    expect(screen.getByRole('heading', { name: /研究助手 · 市场/ })).toBeTruthy()
  })
})

describe('TopbarTools homeAskFocus 文案', () => {
  it('homeAskFocus=false 恢复默认「问 AI」文案', () => {
    render(<TopbarTools onOpenAi={() => {}} onAskAi={() => {}} />)
    expect(screen.getByRole('button', { name: '问 AI' }).getAttribute('aria-haspopup')).toBe('dialog')
  })
})
