// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Layout from '../src/components/Layout'
import { ResearchAiDock } from '../src/components/research/sections/AiDock'
import { useRegisterAssistantObject } from '../src/lib/assistant/shell'
import { setPrivacyMode } from '../src/lib/privacy'
import { setTheme } from '../src/lib/prefs'

/**
 * QUOT-475：全站常驻的「问 AI」入口。
 *
 * 硬约束都在这条链上验：
 * - 顶栏主按钮按当前路由绑定技能，打开抽屉即预填整段开场；
 * - `unconnected` 包标题带「（未接通）」且发送被拦，不伪造回答；
 * - 窄屏顶栏不渲染主按钮，入口挪到 `MobileNavDrawer` 底部；
 * - `/research/:code` 页内按钮打开的仍是 **Layout 挂载的那一份**抽屉（全站只有一个）。
 */

function stubMatchMedia({ mobile = false }: { mobile?: boolean } = {}): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(max-width: 767px)' && mobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

function renderLayout(initialPath: string, page = <div data-testid="page" />) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={page} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function topbar(): HTMLElement {
  const element = document.querySelector<HTMLElement>('header.workspace-topbar')
  if (!element) throw new Error('顶栏没有渲染')
  return element
}

function askAiInTopbar(): HTMLElement {
  return within(topbar()).getByRole('button', { name: '问 AI' })
}

/** 打开抽屉后输入框里应当已经是一整段可发送的开场 */
function draftValue(): string {
  const box = screen.getByLabelText('向研究助手提问') as HTMLTextAreaElement
  return box.value
}

beforeEach(() => {
  window.localStorage.clear()
  setPrivacyMode(false)
  setTheme('light')
  stubMatchMedia()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('顶栏「问 AI」入口', () => {
  it('桌面宽度下按当前页绑定打开抽屉，并预填整段开场', () => {
    renderLayout('/market')

    fireEvent.click(askAiInTopbar())

    expect(
      screen.getByRole('heading', { name: '研究助手 · 市场 · 盘面复盘' }),
    ).toBeTruthy()
    const draft = draftValue()
    expect(draft.startsWith('我们来复盘今天的盘面。')).toBe(true)
    expect(draft).toContain('第 1 步，情绪总览：')
    // 不自动发送：只有「复制」和「发送」，没有请求
    expect(screen.getByRole('button', { name: '复制开场' })).toBeTruthy()
    expect((screen.getByRole('button', { name: '发送' }) as HTMLButtonElement).disabled).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('/my-stocks 绑定持仓体检', () => {
    renderLayout('/my-stocks')
    fireEvent.click(askAiInTopbar())
    expect(
      screen.getByRole('heading', { name: '研究助手 · 我的股票 · 持仓体检' }),
    ).toBeTruthy()
  })

  it('/news 是未接通包：标题带「（未接通）」，发送被拦但可复制', () => {
    renderLayout('/news')
    fireEvent.click(askAiInTopbar())

    expect(
      screen.getByRole('heading', { name: '研究助手 · 市场 · 产业链资讯（未接通）' }),
    ).toBeTruthy()
    expect(screen.getByText('产业链资讯未接通')).toBeTruthy()
    expect(screen.getByText(/资讯源未接通/)).toBeTruthy()
    expect((screen.getByRole('button', { name: '发送' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('button', { name: '复制开场' })).toBeTruthy()
  })

  it('首页不绑定包：只写页面名，输入框为空', () => {
    renderLayout('/')
    fireEvent.click(askAiInTopbar())

    expect(screen.getByRole('heading', { name: '研究助手 · 首页' })).toBeTruthy()
    expect(draftValue()).toBe('')
  })

  it('Escape 关闭抽屉', () => {
    renderLayout('/market')
    fireEvent.click(askAiInTopbar())
    expect(screen.getByRole('heading', { name: /研究助手 · 市场/ })).toBeTruthy()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('heading', { name: /研究助手 · 市场/ })).toBeNull()
    expect(document.body.style.overflow).toBe('')
  })
})

describe('窄屏：入口移到移动端抽屉', () => {
  it('顶栏不渲染主按钮，导航抽屉底部有一项「问 AI」', () => {
    stubMatchMedia({ mobile: true })
    renderLayout('/my-stocks')

    expect(within(topbar()).queryByRole('button', { name: '问 AI' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '打开导航' }))
    const dialog = screen.getByRole('dialog', { name: '导航菜单' })
    fireEvent.click(within(dialog).getByRole('button', { name: '问 AI' }))

    expect(screen.queryByRole('dialog', { name: '导航菜单' })).toBeNull()
    expect(
      screen.getByRole('heading', { name: '研究助手 · 我的股票 · 持仓体检' }),
    ).toBeTruthy()
  })
})

describe('页内入口与顶栏共用同一份抽屉', () => {
  function ResearchPage() {
    useRegisterAssistantObject({
      code: '600519',
      name: '贵州茅台',
      date: '2026-11-06',
      tab: 'overview',
    })
    return (
      <ResearchAiDock
        code="600519"
        name="贵州茅台"
        scope={{ tab: 'overview', date: '2026-11-06' }}
        pageKey="/research/600519"
      />
    )
  }

  beforeEach(() => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
  })

  it('页内「研究助手」只打开 Layout 的那一份（不产生第二个抽屉）', () => {
    renderLayout('/research/600519', <ResearchPage />)

    fireEvent.click(screen.getByRole('button', { name: '研究助手' }))

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(
      screen.getByRole('heading', { name: '研究助手 · 个股深挖 · 个股全景' }),
    ).toBeTruthy()
    expect(draftValue()).toContain('「贵州茅台（600519）」')
  })

  it('顶栏「问 AI」带上页内登记的标的，标题仍是这页绑定的包', () => {
    renderLayout('/research/600519', <ResearchPage />)

    fireEvent.click(askAiInTopbar())

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(
      screen.getByRole('heading', { name: '研究助手 · 个股深挖 · 个股全景' }),
    ).toBeTruthy()
    expect(draftValue()).toContain('「贵州茅台（600519）」')
  })
})
