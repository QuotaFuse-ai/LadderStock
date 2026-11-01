// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DemoStateBadge } from '../src/components/PageShell'
import { DEMO_DATA_STORAGE_KEY, setDemoPref } from '../src/lib/pageState'
import LimitUpScreen from '../src/screens/limitup/LimitUpScreen'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * 设置页「虚拟数据」开关（QUOT-200 提速项）：
 * - 开启后所有屏走演示源，等同于 `?demo=1`，但不依赖 URL；
 * - 关闭后只走真实通道；若 URL 还挂着 `?demo=1`，关闭动作要一起摘掉。
 */

afterEach(() => {
  cleanup()
  setDemoPref(false)
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function stubOffline() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new TypeError('offline')
    }),
  )
}

describe('虚拟数据开关', () => {
  it('开关开启：不写 URL 也渲染演示数据，水印标注来源是设置开关', async () => {
    stubOffline()
    setDemoPref(true)

    render(
      <MemoryRouter initialEntries={['/limit-up']}>
        <Routes>
          <Route path="/limit-up" element={<LimitUpScreen />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: '涨停样本', level: 1 })).toBeTruthy()
    expect(screen.getByText('演示数据 · 设置开关')).toBeTruthy()
    expect(screen.queryByText('演示数据 · ?demo=1')).toBeNull()
    expect(withinTableCodes()).toContain('002594')
  })

  it('设置页关闭开关时摘掉 URL 的 ?demo=1，页面立即回到真实通道', async () => {
    stubOffline()

    render(
      <MemoryRouter initialEntries={['/settings?tab=about&demo=1']}>
        <SettingsScreen />
      </MemoryRouter>,
    )

    expect(screen.getByText('演示数据 · ?demo=1')).toBeTruthy()
    const toggle = await screen.findByRole('switch')
    expect(toggle.getAttribute('aria-checked')).toBe('true')

    fireEvent.click(toggle)

    await waitFor(() => {
      expect(screen.queryByText(/演示数据 ·/)).toBeNull()
    })
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(window.localStorage.getItem(DEMO_DATA_STORAGE_KEY)).toBe('0')
  })

  it('开关状态落 localStorage，刷新后仍生效（不需 URL）', () => {
    setDemoPref(true)
    expect(window.localStorage.getItem(DEMO_DATA_STORAGE_KEY)).toBe('1')

    render(
      <MemoryRouter initialEntries={['/x']}>
        <DemoStateBadge state={null} />
      </MemoryRouter>,
    )
    expect(screen.getByText('演示数据 · 设置开关')).toBeTruthy()
  })
})

function withinTableCodes(): string {
  return document.querySelector('table')?.textContent ?? ''
}
