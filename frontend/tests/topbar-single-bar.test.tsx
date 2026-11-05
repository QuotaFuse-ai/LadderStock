// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import Layout from '../src/components/Layout'
import { PageHeader } from '../src/components/PageShell'

/**
 * QUOT-457 / DEC-063：全平台只留一条横栏。
 *
 * 三条验收口径：
 * 1. 页面自己不再渲染 header（标题条 / 导航条都取消），内容区也没有命令条；
 * 2. 「刷新」从顶栏可达，且全屏只有一颗；
 * 3. 页面仍有 h1（对读屏可见、不占版面），次级操作留在内容区。
 */

function DemoPage() {
  return (
    <PageHeader
      title="我的持仓"
      subtitle="2026-11-05 · 3 只"
      refresh={{ onClick: () => {} }}
      actions={
        <button type="button">
          新增持仓
        </button>
      }
    />
  )
}

function renderShell(path = '/positions') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<DemoPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

const topbar = () => document.querySelector<HTMLElement>('header.workspace-topbar') as HTMLElement
const main = () => document.getElementById('main') as HTMLElement

afterEach(cleanup)

describe('单顶栏结构', () => {
  it('全屏只有一个 header，内容区没有页头与命令条', () => {
    renderShell()
    expect(document.querySelectorAll('header')).toHaveLength(1)
    expect(main().querySelector('header')).toBeNull()
    expect(main().querySelector('.page-shell-header')).toBeNull()
    expect(within(main()).queryByRole('region', { name: '本页命令' })).toBeNull()
  })

  it('刷新收进顶栏，内容区不再有第二颗', () => {
    renderShell()
    expect(within(topbar()).getAllByRole('button', { name: '刷新' })).toHaveLength(1)
    expect(within(main()).queryByRole('button', { name: '刷新' })).toBeNull()
  })

  it('标题是交给读屏的 h1，不占版面；顶栏不冒充标题', () => {
    renderShell()
    const heading = screen.getByRole('heading', { name: '我的持仓', level: 1 })
    expect(heading.className).toContain('sr-only')
    expect(within(topbar()).queryByRole('heading')).toBeNull()
  })

  it('次级操作留在内容区', () => {
    renderShell()
    expect(within(main()).getByRole('button', { name: '新增持仓' })).toBeTruthy()
    expect(within(topbar()).queryByRole('button', { name: '新增持仓' })).toBeNull()
  })
})
