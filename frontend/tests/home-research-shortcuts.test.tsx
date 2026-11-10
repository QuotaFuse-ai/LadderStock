// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { HomeResearchShortcuts } from '../src/screens/home/HomeResearchShortcuts'

/**
 * QUOT-531 首页 B 块「研究工具快捷入口」。
 *
 * 钉的是页面真的给出的东西：4 条链接、落点只有 `/skills/:id` 与已承载页面、
 * 点一下真跳转、「换一批」重抽、重新挂载换一批。数据来自真实
 * `content/skills/catalog.json`（不 mock 清单）。
 */

function makeRng(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function renderShortcuts(seed: number) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<HomeResearchShortcuts random={makeRng(seed)} />} />
        <Route path="/skills/:id" element={<div>条目详情页</div>} />
        <Route path="/limit-up" element={<div>涨停样本页</div>} />
        <Route path="/watch" element={<div>盘中异动页</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function shortcutLinks(): HTMLAnchorElement[] {
  const section = screen.getByRole('region', { name: '研究工具快捷入口' })
  return Array.from(section.querySelectorAll('a')) as HTMLAnchorElement[]
}

afterEach(cleanup)

describe('HomeResearchShortcuts（QUOT-531）', () => {
  it('渲染 4 条快捷入口，落点只有 /skills/:id 或已承载页面', () => {
    renderShortcuts(1)

    expect(screen.getByRole('heading', { name: '研究工具快捷入口' })).toBeTruthy()
    const links = shortcutLinks()
    expect(links).toHaveLength(4)
    for (const link of links) {
      const href = link.getAttribute('href') ?? ''
      expect(href).toMatch(/^(\/skills\/\d+|\/limit-up|\/watch)$/)
      expect(link.getAttribute('aria-label')).toBeTruthy()
    }
    // 一组最多一条：4 条卡片的副标题（分组）互不相同
    const groups = links.map((link) => link.querySelectorAll('span')[2]?.textContent ?? '')
    expect(new Set(groups).size).toBe(4)
  })

  it('换一批：点一下重新抽 4 条', () => {
    renderShortcuts(42)
    const before = shortcutLinks().map((link) => link.getAttribute('href'))

    fireEvent.click(screen.getByRole('button', { name: '换一批' }))

    const after = shortcutLinks().map((link) => link.getAttribute('href'))
    expect(after).toHaveLength(4)
    expect(after).not.toEqual(before)
  })

  it('点快捷入口直达对应页面', () => {
    renderShortcuts(9)
    const first = shortcutLinks()[0]
    const href = first.getAttribute('href') ?? ''

    fireEvent.click(first)

    if (href.startsWith('/skills/')) {
      expect(screen.getByText('条目详情页')).toBeTruthy()
    } else if (href === '/limit-up') {
      expect(screen.getByText('涨停样本页')).toBeTruthy()
    } else {
      expect(href).toBe('/watch')
      expect(screen.getByText('盘中异动页')).toBeTruthy()
    }
  })

  it('重新挂载（刷新首页）换一批', () => {
    const first = renderShortcuts(5)
    const before = shortcutLinks().map((link) => link.getAttribute('href'))
    first.unmount()

    renderShortcuts(6)
    const after = shortcutLinks().map((link) => link.getAttribute('href'))
    expect(after).not.toEqual(before)
  })
})
