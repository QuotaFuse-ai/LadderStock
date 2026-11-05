// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Layout from '../src/components/Layout'
import { navTitleFor } from '../src/lib/nav'
import { setTheme } from '../src/lib/prefs'
import { PRIVACY_STORAGE_KEY, setPrivacyMode } from '../src/lib/privacy'
import { THEME_STORAGE_KEY } from '../src/lib/theme'

/**
 * 顶栏（QUOT-442 / DEC-062）：面包屑 / 隐私开合 / 深浅色 / 移动端抽屉。
 *
 * 抽屉断点 `<768px` 由 JS 判断（汉堡必须「不渲染」而不是 CSS 隐藏），
 * 所以这里用 `matchMedia` 桩控制视口，而不是改 jsdom 的窗口宽度。
 */

type MediaOptions = { mobile?: boolean; prefersDark?: boolean }

function stubMatchMedia({ mobile = false, prefersDark = false }: MediaOptions = {}): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches:
      (query === '(max-width: 767px)' && mobile) ||
      (query === '(prefers-color-scheme: dark)' && prefersDark),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

function renderLayout(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<div data-testid="page" />} />
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

describe('顶栏面包屑', () => {
  it('`/positions` 显示所属一级栏目「我的股票」', () => {
    renderLayout('/positions')
    expect(within(topbar()).getByText('我的股票')).toBeTruthy()
    expect(within(topbar()).getByText('工作空间 /')).toBeTruthy()
  })

  it('未知路径回退到「工作空间」', () => {
    renderLayout('/nope')
    const bar = topbar()
    expect(within(bar).getByText('工作空间')).toBeTruthy()
  })

  it('顶栏路径与页内大标题是两层：面包屑不替代 PageHeader', () => {
    renderLayout('/positions')
    expect(within(topbar()).queryByRole('heading')).toBeNull()
  })

  it('navTitleFor 只看 pathname，子路径归到最长前缀', () => {
    expect(navTitleFor('/positions')).toBe('我的股票')
    expect(navTitleFor('/market?tab=theme')).toBe('市场')
    expect(navTitleFor('/review/ai')).toBe('复盘')
    expect(navTitleFor('/research/600519')).toBe('个股深挖')
    expect(navTitleFor('/')).toBe('首页')
    expect(navTitleFor('/nope')).toBe('工作空间')
  })
})

describe('顶栏隐私开关', () => {
  it('开合时切图标与 aria-pressed，并写本机存储', () => {
    renderLayout('/positions')
    const button = within(topbar()).getByRole('button', { name: '打开隐私模式' })
    expect(button.getAttribute('aria-pressed')).toBe('false')
    expect(button.querySelector('svg')?.getAttribute('class')).toContain('lucide-eye')

    fireEvent.click(button)

    const open = within(topbar()).getByRole('button', { name: '关闭隐私模式' })
    expect(open.getAttribute('aria-pressed')).toBe('true')
    expect(open.querySelector('svg')?.getAttribute('class')).toContain('lucide-eye-off')
    expect(window.localStorage.getItem(PRIVACY_STORAGE_KEY)).toBe('on')
  })
})

describe('顶栏深浅色开关', () => {
  it('浅色下点击切到深色，按钮语义跟着解析值走', () => {
    renderLayout('/')
    const button = within(topbar()).getByRole('button', { name: '切换为深色' })
    fireEvent.click(button)

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(document.documentElement.getAttribute('data-ldst-theme')).toBe('dark')
    expect(within(topbar()).getByRole('button', { name: '切换为浅色' })).toBeTruthy()
  })

  it('当前是 system 时按解析值切到反色（系统深色 → 浅色）', () => {
    stubMatchMedia({ prefersDark: true })
    setTheme('system')
    renderLayout('/')

    const button = within(topbar()).getByRole('button', { name: '切换为浅色' })
    fireEvent.click(button)
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
  })
})

describe('移动端抽屉（<768px）', () => {
  it('窄屏渲染汉堡并让侧栏让位，宽屏不渲染汉堡', () => {
    stubMatchMedia({ mobile: true })
    const narrow = renderLayout('/')
    expect(screen.getByRole('button', { name: '打开导航' })).toBeTruthy()
    // jsdom 不套 Tailwind：直接看类名，确认窄屏侧栏让位给抽屉
    expect(document.querySelector('aside')?.className).toContain('hidden')
    narrow.unmount()

    stubMatchMedia({ mobile: false })
    renderLayout('/')
    expect(screen.queryByRole('button', { name: '打开导航' })).toBeNull()
    expect(document.querySelector('aside')?.className).toContain('flex')
  })

  it('打开后 aria-expanded/aria-controls 正确、body 滚动锁定、抽屉里是同一份导航', () => {
    stubMatchMedia({ mobile: true })
    renderLayout('/')

    const trigger = screen.getByRole('button', { name: '打开导航' })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(trigger.getAttribute('aria-controls')).toBe('mobile-nav-drawer')

    fireEvent.click(trigger)

    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(document.body.style.overflow).toBe('hidden')

    const dialog = screen.getByRole('dialog', { name: '导航菜单' })
    expect(dialog.getAttribute('id')).toBe('mobile-nav-drawer')
    expect(within(dialog).getByRole('link', { name: /我的持仓/ })).toBeTruthy()
    expect(within(dialog).getByRole('link', { name: /设置/ })).toBeTruthy()
  })

  it('Escape 关闭并把焦点还给汉堡', () => {
    stubMatchMedia({ mobile: true })
    renderLayout('/')

    const trigger = screen.getByRole('button', { name: '打开导航' })
    fireEvent.click(trigger)
    expect(screen.getByRole('dialog', { name: '导航菜单' })).toBeTruthy()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog', { name: '导航菜单' })).toBeNull()
    expect(document.activeElement).toBe(trigger)
    expect(document.body.style.overflow).toBe('')
  })

  it('点遮罩关闭', () => {
    stubMatchMedia({ mobile: true })
    renderLayout('/')
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }))

    const dialog = screen.getByRole('dialog', { name: '导航菜单' })
    const scrim = dialog.previousElementSibling
    if (!(scrim instanceof HTMLElement)) throw new Error('遮罩层没有渲染')
    fireEvent.click(scrim)

    expect(screen.queryByRole('dialog', { name: '导航菜单' })).toBeNull()
  })

  it('换页后抽屉自动关闭', () => {
    stubMatchMedia({ mobile: true })
    renderLayout('/')

    fireEvent.click(screen.getByRole('button', { name: '打开导航' }))
    const dialog = screen.getByRole('dialog', { name: '导航菜单' })
    fireEvent.click(within(dialog).getByRole('link', { name: /我的持仓/ }))

    expect(screen.queryByRole('dialog', { name: '导航菜单' })).toBeNull()
    expect(within(topbar()).getByText('我的股票')).toBeTruthy()
  })
})
