// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HomeSearchTrigger } from '../src/components/search/HomeSearchTrigger'
import { SearchPanel } from '../src/components/search/SearchPanel'
import type { SearchSource } from '../src/lib/searchCore'
import { HomeSearch } from '../src/screens/home/HomeSearch'
import { useHomeSearch } from '../src/screens/home/useHomeSearch'

/**
 * QUOT-357：首页搜索入口 + 聚合搜索面板。
 *
 * 取数一律通过 `lib/searchCore` 的公开入参 `sources` 注入（本文件顶部的假源）：
 * **不 mock `fetch`、不改数据层接口形状**（触发条硬约束——动形状会打红 QUOT-356 的 27 条用例）。
 * 用例钉的全是触发条点名的行为：打开与焦点、门限、三组结果、↑↓ 与 Enter、Esc 回焦点、
 * 「未核实」标、单组「该组暂不可用」、关键词出口跳 `/news?q=`、面板内无演示水印。
 *
 * 三条环境口径（照本仓既有测试的写法，不引 jest-dom）：
 * - 节流 250ms 走假计时器，`flush()` 里推过窗口并把 `runSearch` 的微任务链排干；
 * - 断言用 `getAttribute` / `textContent`，`waitFor` 在假计时器下不推进计时器，所以不用它。
 */

const PANEL_ID = 'test-search-panel'

const LADDER: SearchSource = {
  id: 'limit_ladder',
  group: 'stocks',
  label: '连板天梯',
  async load({ query }) {
    const table = [
      { code: '600000', name: '浦发银行' },
      { code: '002028', name: '思源电气' },
    ]
    return table
      .filter((row) => query.text && (row.name.includes(query.text) || row.code.includes(query.text)))
      .map((row) => ({ key: row.code, tag: '个股' as const, code: row.code, name: row.name, detail: null, score: 80 }))
  },
}

const INDUSTRY: SearchSource = {
  id: 'industry_list',
  group: 'themes',
  label: '行业清单',
  intents: ['name', 'keyword'],
  async load({ query }) {
    const table = [
      { name: '机器人', code: 'L10' },
      { name: '电力设备', code: 'L8' },
    ]
    return table
      .filter((row) => query.text && row.name.includes(query.text))
      .map((row) => ({
        key: row.name,
        tag: '行业' as const,
        code: null,
        name: row.name,
        detail: `L1 ${row.code}`,
        score: 80,
      }))
  },
}

const NEWS: SearchSource = {
  id: 'news_stream',
  group: 'news',
  label: '资讯雷达',
  async load({ query }) {
    const table = [
      { id: 'n1', title: '机器人产业链景气回升', source: '财联社', codes: ['600000'] },
      { id: 'n2', title: '电力设备出口数据', source: '证券时报', codes: ['002028'] },
    ]
    return table
      .filter(
        (row) =>
          query.text && (row.title.includes(query.text) || row.codes.some((code) => code.includes(query.text))),
      )
      .map((row) => ({
        key: row.id,
        tag: '资讯' as const,
        code: row.codes[0] ?? null,
        name: row.title,
        detail: row.source,
        score: 60,
      }))
  },
}

/**
 * 代码直达：与 `searchCore` 的 `code_direct` 源同一份口径（`verified: false` + `name: null`），
 * 用来拍「未核实」那一档——本机数据源没给名称时不许拿代码顶替。
 */
const CODE_DIRECT: SearchSource = {
  id: 'code_direct',
  group: 'stocks',
  label: '代码直达',
  intents: ['code'],
  async load({ query, intent }) {
    if (intent !== 'code' || !query.code) return []
    return [
      {
        key: query.code,
        tag: '个股' as const,
        code: query.code,
        name: null,
        detail: '名称未在本机数据源接入',
        score: 100,
        verified: false,
      },
    ]
  },
}

/** 资讯整组挂掉：拍 §8.6「该组暂不可用」+ 另两组照常可跳。 */
const NEWS_DOWN: SearchSource = {
  id: 'news_stream',
  group: 'news',
  label: '资讯雷达',
  async load() {
    throw new Error('connection refused')
  },
}

/** 与 `screens/home/HomeSearch.tsx` 同一套装配，只把取数源换成假源。 */
function TestHomeSearch({ sources, landed }: { sources: readonly SearchSource[]; landed: string[] }) {
  const { open, model, actions, openPanel } = useHomeSearch({
    sources,
    onNavigate: (href) => landed.push(href),
  })
  return (
    <>
      <HomeSearchTrigger expanded={open} panelId={PANEL_ID} onOpen={openPanel} />
      <SearchPanel panelId={PANEL_ID} model={model} actions={actions} />
    </>
  )
}

function renderHome(sources: readonly SearchSource[]) {
  const landed: string[] = []
  render(
    <MemoryRouter initialEntries={['/']}>
      <TestHomeSearch sources={sources} landed={landed} />
    </MemoryRouter>,
  )
  return landed
}

/** 推过假计时器并把 `runSearch` 的微任务链排干（`waitFor` 在假计时器下不会推进）。 */
async function flush(ms = 300) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
    for (let i = 0; i < 12; i += 1) await Promise.resolve()
  })
}

const trigger = () => screen.getByRole('button', { name: /搜索个股/ })
const input = () => screen.getByRole('combobox') as HTMLInputElement
const options = () => screen.getAllByRole('option')

async function openAndType(text: string) {
  fireEvent.click(trigger())
  await flush(20)
  fireEvent.change(input(), { target: { value: text } })
  await flush()
  return input()
}

describe('首页搜索入口（QUOT-357）', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('入口是按钮不是输入框，点击才打开面板（§4.3 / §4.5）', () => {
    renderHome([LADDER, INDUSTRY, NEWS])
    expect(trigger().tagName).toBe('BUTTON')
    expect(trigger().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(trigger())
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(trigger().getAttribute('aria-expanded')).toBe('true')
  })

  it('Tab 焦点经过入口不弹遮罩（§4.5）', async () => {
    renderHome([LADDER, INDUSTRY, NEWS])
    await act(async () => {
      trigger().focus()
    })
    expect(document.activeElement).toBe(trigger())
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('打开时焦点进输入框，Esc 关闭后焦点回入口（§7.1 / §5.4）', async () => {
    renderHome([LADDER, INDUSTRY, NEWS])
    // 先 focus 再 click：真浏览器里点击本身会把焦点留在按钮上，jsdom 的 `fireEvent.click` 不会，
    // 于是 `useModalChrome` 记录的「关闭后回哪去」会退化成 body，这一条就测不到回归焦点
    const entry = trigger()
    entry.focus()
    fireEvent.click(entry)
    await flush(20)
    expect(document.activeElement).toBe(input())

    fireEvent.keyDown(input(), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(trigger())
  })

  it('门限未过不发请求（§8.3：数字满 3 位 / 其余满 2 字符）', async () => {
    const calls: string[] = []
    const spy: SearchSource = {
      id: 'spy',
      group: 'stocks',
      label: '探针',
      async load({ query }) {
        calls.push(query.text)
        return []
      },
    }
    renderHome([spy])
    await openAndType('6')
    expect(screen.getByText(/继续输入 2 个字再搜/)).toBeTruthy()
    expect(calls).toEqual([])
    // `queryAllByRole` 而非 `getAllByRole`：这里要拍的就是「一行都没有」，
    // `getAll*` 在空集上直接抛错，会把「没发请求」这条判据变成「查不到元素」
    expect(screen.queryAllByRole('option').length).toBe(0)

    fireEvent.change(input(), { target: { value: '600000' } })
    await flush()
    expect(calls).toEqual(['600000'])
  })

  it('代码直达：跳 /research/600519，未核实的名字标「未核实」，名称不拿代码顶替（§6.4）', async () => {
    // 600519 不在假天梯表里 → 只有 `code_direct` 一路出结果，才会保留 `verified: false`
    // （同键命中会被数据层按「任一源核实过即算核实」合并掉）
    const landed = renderHome([CODE_DIRECT, LADDER, INDUSTRY, NEWS])
    await openAndType('600519')
    const first = options()[0]
    expect(first.textContent).toContain('600519')
    expect(first.textContent).toContain('未核实')
    expect(first.textContent).toContain('名称未接入')

    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(landed[0]).toBe('/research/600519')
  })

  it('名称命中当日池内标的 → /research/:code', async () => {
    const landed = renderHome([LADDER, INDUSTRY, NEWS])
    await openAndType('浦发')
    expect(options().some((row) => row.textContent?.includes('浦发银行'))).toBe(true)

    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(landed[0]).toBe('/research/600000')
  })

  it('↑↓ 移动高亮、到头即停不循环，`aria-activedescendant` 始终指真实行（§7.2）', async () => {
    renderHome([LADDER, INDUSTRY, NEWS])
    await openAndType('机器人')
    const count = options().length
    expect(count).toBeGreaterThan(1)
    expect(options()[0].getAttribute('aria-selected')).toBe('true')

    fireEvent.keyDown(input(), { key: 'ArrowDown' })
    expect(options()[1].getAttribute('aria-selected')).toBe('true')

    for (let i = 0; i < count + 2; i += 1) fireEvent.keyDown(input(), { key: 'ArrowDown' })
    const rows = options()
    expect(rows[count - 1].getAttribute('aria-selected')).toBe('true')
    expect(input().getAttribute('aria-activedescendant')).toBe(rows[count - 1].id)
    // 反向到底同样即停
    for (let i = 0; i < count + 2; i += 1) fireEvent.keyDown(input(), { key: 'ArrowUp' })
    expect(options()[0].getAttribute('aria-selected')).toBe('true')
  })

  it('行业 / 题材行 → /industry/:name', async () => {
    const landed = renderHome([LADDER, INDUSTRY, NEWS])
    await openAndType('机器人')
    const theme = options().find((row) => row.textContent?.includes('行业'))
    expect(theme).toBeTruthy()
    fireEvent.mouseDown(theme as never)
    expect(landed[0]).toBe('/industry/%E6%9C%BA%E5%99%A8%E4%BA%BA')
  })

  it('单组整块失败：该组「该组暂不可用」，另两组照常可选（§8.6）', async () => {
    const landed = renderHome([LADDER, INDUSTRY, NEWS_DOWN])
    await openAndType('银行')
    expect(options().length).toBeGreaterThan(0)
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('该组暂不可用（结果没取到）')
    // 只剩失败组的错误结论不许把面板拖进 §8.7 的整块失败态
    expect(dialog.textContent).not.toContain('重试全部')

    const stock = options().find((row) => row.textContent?.includes('600000'))
    expect(stock).toBeTruthy()
    fireEvent.mouseDown(stock as never)
    expect(landed[0]).toBe('/research/600000')
  })

  it('无结果态：定稿文案 + 关键词出口跳 /news?q=（§8.5 / §3.4）', async () => {
    const landed = renderHome([LADDER, INDUSTRY, NEWS])
    await openAndType('不存在xyz')
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('今日数据里没有匹配「不存在xyz」')
    expect(dialog.textContent).toContain('没匹配不等于不存在')

    fireEvent.click(screen.getByRole('button', { name: /在资讯雷达中搜索/ }))
    expect(landed[0]).toBe('/news?q=%E4%B8%8D%E5%AD%98%E5%9C%A8xyz')
  })

  it('空态出引导与最近搜索 chips；有结果时面板内无演示水印（§8.2 / §6.7）', async () => {
    window.localStorage.setItem('ldst.home-search.recent', JSON.stringify(['电力', '600000']))
    renderHome([LADDER, INDUSTRY, NEWS])
    fireEvent.click(trigger())
    await flush(20)
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('可以搜这三类')
    expect(dialog.textContent).toContain('电力')
    // §5.5：空输入时清除 ✕ 不出现
    expect(screen.queryByRole('button', { name: '清除输入' })).toBeNull()

    fireEvent.change(input(), { target: { value: '机器人' } })
    await flush()
    expect(options().length).toBeGreaterThan(0)
    expect(screen.getByRole('dialog').textContent).not.toContain('演示')
    expect(screen.getByRole('button', { name: '清除输入' })).toBeTruthy()
  })

  it('选中结果后写入最近搜索（§14-D4：只存本机）', async () => {
    const landed = renderHome([LADDER, INDUSTRY, NEWS])
    await openAndType('浦发')
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(landed[0]).toBe('/research/600000')
    expect(JSON.parse(window.localStorage.getItem('ldst.home-search.recent') ?? '[]')).toEqual(['浦发'])
  })

  /**
   * 走真实 `HomeSearch`（不注入假源）：测装配件自己的深链契约——`?search=` 必须把词填进
   * 输入框，且参数被消费撤掉之后输入框**不能**被清空。取数在此打真接口（jsdom 里必失败），
   * 所以不断言结果，只断关键词留存。
   */
  it('`?search=` 深链带着关键词打开面板，撤参数不撤输入（分享入口与视觉巡检）', async () => {
    render(
      <MemoryRouter initialEntries={['/?search=%E6%9C%BA%E5%99%A8%E4%BA%BA']}>
        <Routes>
          <Route path="/" element={<HomeSearch />} />
          <Route path="*" element={<div />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(input().value).toBe('机器人')

    await flush(400)
    expect(input().value).toBe('机器人')
  })
})
