// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Pagination } from '../src/components/ui'
import { isUsableCatalogItem, readCatalog } from '../src/lib/skills'
import SkillsIndexScreen from '../src/screens/skills/SkillsIndexScreen'

/**
 * QUOT-530：工具总览分页 + 「只看可用」过滤。
 *
 * 三条验收口径：
 * - 70 条 → 10 条/页 × 7 页，页码落进 `?page=`，深链刷新保持；
 * - 切分组 / 切过滤时回到第 1 页，不留在空白页；
 * - 「只看可用」只留 可用 / 部分可用 / 已在页面（45 条），未接通等未完成态被排除。
 */

const CATALOG = readCatalog()

function pageButtonNumbers(): number[] {
  return screen
    .queryAllByText(/^#\d+$/)
    .map((node) => Number.parseInt((node.textContent ?? '').slice(1), 10))
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="search">{location.search}</output>
}

function renderSkills(entry = '/skills') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <SkillsIndexScreen />
      <LocationProbe />
    </MemoryRouter>,
  )
}

function paginationNav() {
  return screen.getByRole('navigation', { name: '研究方向分页' })
}

afterEach(cleanup)

describe('Pagination · 原语', () => {
  it('页码全量铺开，当前页有 aria-current 与加粗双通道，边界按钮禁用', () => {
    const onChange = vi.fn()
    render(<Pagination page={1} pageCount={7} total={70} pageSize={10} onPageChange={onChange} />)

    const nav = screen.getByRole('navigation', { name: '分页' })
    expect(nav.textContent).toContain('第 1 / 7 页')
    expect(nav.textContent).toContain('共 70 条 · 每页 10 条')

    expect(screen.getAllByRole('button', { name: /^第 \d+ 页$/ })).toHaveLength(7)
    const current = screen.getByRole('button', { name: '第 1 页' })
    expect(current.getAttribute('aria-current')).toBe('page')
    expect(current.className).toContain('font-semibold')

    expect((screen.getByRole('button', { name: '上一页' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(
      false,
    )

    fireEvent.click(screen.getByRole('button', { name: '第 4 页' }))
    expect(onChange).toHaveBeenLastCalledWith(4)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(onChange).toHaveBeenLastCalledWith(2)
  })

  it('末页：下一页禁用，上一页可回退', () => {
    const onChange = vi.fn()
    render(<Pagination page={7} pageCount={7} onPageChange={onChange} />)

    expect(screen.getByRole('button', { name: '第 7 页' }).getAttribute('aria-current')).toBe('page')
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '上一页' }))
    expect(onChange).toHaveBeenLastCalledWith(6)
  })

  it('页数多时首尾 + 当前页窗口，中间用省略号收口', () => {
    const onChange = vi.fn()
    render(<Pagination page={10} pageCount={20} onPageChange={onChange} />)

    expect(screen.getByRole('button', { name: '第 10 页' }).getAttribute('aria-current')).toBe(
      'page',
    )
    for (const n of [1, 8, 9, 10, 11, 12, 20]) {
      expect(screen.getByRole('button', { name: `第 ${n} 页` })).toBeTruthy()
    }
    expect(screen.queryByRole('button', { name: '第 2 页' })).toBeNull()
    expect(screen.getAllByText('…')).toHaveLength(2)
  })

  it('越界页码收敛到边界，不会调出第 0 页或第 N+1 页', () => {
    const onChange = vi.fn()
    render(<Pagination page={99} pageCount={7} onPageChange={onChange} />)

    expect(screen.getByRole('button', { name: '第 7 页' }).getAttribute('aria-current')).toBe('page')
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(true)
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('SkillsIndexScreen · 分页与过滤', () => {
  it('默认 10 条/页，70 条落成 7 页，摘要给出总数与每页条数', () => {
    renderSkills()

    expect(CATALOG.items).toHaveLength(70)
    expect(pageButtonNumbers()).toEqual(CATALOG.items.slice(0, 10).map((item) => item.id))
    expect(paginationNav().textContent).toContain('第 1 / 7 页')
    expect(paginationNav().textContent).toContain('共 70 条 · 每页 10 条')
    expect(screen.getByTestId('search').textContent).toBe('')
  })

  it('下一页把页码写进 URL，并渲染第 11–20 条', () => {
    renderSkills()

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))

    expect(screen.getByTestId('search').textContent).toBe('?page=2')
    expect(pageButtonNumbers()).toEqual(CATALOG.items.slice(10, 20).map((item) => item.id))
    expect(paginationNav().textContent).toContain('第 2 / 7 页')
  })

  it('`?page=7` 深链直接落在末页，刷新语义一致', () => {
    renderSkills('/skills?page=7')

    expect(pageButtonNumbers()).toEqual(CATALOG.items.slice(60, 70).map((item) => item.id))
    expect(paginationNav().textContent).toContain('第 7 / 7 页')
    expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('越界页码在 URL 里就地收敛，地址栏与页面显示同一页', () => {
    renderSkills('/skills?page=99')

    expect(screen.getByTestId('search').textContent).toBe('?page=7')
    expect(pageButtonNumbers()).toEqual(CATALOG.items.slice(60, 70).map((item) => item.id))
  })

  it('切分组回到第 1 页，页码参数被移除', () => {
    renderSkills('/skills?page=2')
    const groupKey = CATALOG.groups[0].key

    fireEvent.click(screen.getAllByRole('tab')[1])

    const search = screen.getByTestId('search').textContent ?? ''
    expect(search).toContain('group=')
    expect(search).not.toContain('page=')
    expect(paginationNav().textContent).toContain('第 1 / 2 页')

    const expected = CATALOG.items.filter((item) => item.group === groupKey).slice(0, 10)
    expect(pageButtonNumbers()).toEqual(expected.map((item) => item.id))
  })

  it('只看可用：排除未接通 / 待建 / 本阶段不做，45 条落成 5 页', () => {
    renderSkills()
    const usable = CATALOG.items.filter(isUsableCatalogItem)
    expect(usable).toHaveLength(45)

    fireEvent.click(screen.getByRole('switch'))

    expect(screen.getByTestId('search').textContent).toBe('?usable=1')
    const list = screen.getByRole('list', { name: '研究方向' })
    expect(within(list).queryByText('未接通')).toBeNull()
    expect(within(list).queryByText('待建')).toBeNull()
    expect(within(list).queryByText('本阶段不做')).toBeNull()
    expect(pageButtonNumbers()).toEqual(usable.slice(0, 10).map((item) => item.id))
    expect(paginationNav().textContent).toContain('共 45 条 · 每页 10 条')
    expect(paginationNav().textContent).toContain('第 1 / 5 页')

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(pageButtonNumbers()).toEqual(usable.slice(10, 20).map((item) => item.id))
  })

  it('切过滤同样回到第 1 页', () => {
    renderSkills('/skills?page=3')

    fireEvent.click(screen.getByRole('switch'))

    expect(screen.getByTestId('search').textContent).toBe('?usable=1')
    expect(paginationNav().textContent).toContain('第 1 / 5 页')
  })
})
