// @vitest-environment jsdom
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ResearchSymbolBar } from '../src/components/research/ResearchSymbolBar'
import { ResearchSymbolSearch } from '../src/components/research/ResearchSymbolSearch'

/**
 * QUOT-448 顶部标的搜索：三种写法（代码 / 中文名 / 拼音）都走同一个
 * `/api/symbols/search`，并覆盖空态、无结果、失败三态与键盘可达。
 *
 * 拼音一条是**已知缺口回归**：后端没有 pypinyin 时 `pinyin: null`、`pfyh`
 * 查不到，前端必须明说「拼音检索尚未接入」，不能悄悄当成「没有这只股票」。
 */

const { symbolsSearch, fetchWatchlist, addWatch } = vi.hoisted(() => ({
  symbolsSearch: vi.fn(),
  fetchWatchlist: vi.fn(),
  addWatch: vi.fn(),
}))

vi.mock('../src/api', () => ({ api: { symbolsSearch } }))

vi.mock('../src/api/radar', () => ({ fetchWatchlist, addWatch }))

function envelope(query: string, matches: unknown[]) {
  return {
    domain: 'sentiment',
    topic: 'symbol_search',
    payload: { query, limit: 10, count: matches.length, matches, data_source_status: 'ok' },
  }
}

const PFB = {
  symbol: '600000',
  name: '浦发银行',
  market: 'SH',
  kind: 'stock',
  board: '主板',
  pinyin: null,
}

function Harness({ onPick }: { onPick?: (code: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <ResearchSymbolSearch
      value={value}
      onChange={setValue}
      onPick={(match) => onPick?.(match.symbol)}
      onEnter={() => onPick?.('ENTER')}
    />
  )
}

beforeEach(() => {
  symbolsSearch.mockReset()
  fetchWatchlist.mockReset()
  addWatch.mockReset()
  fetchWatchlist.mockResolvedValue({
    local_only: true,
    store: 'local_sqlite',
    available: true,
    reason: null,
    watch_count: 0,
    data_source_status: 'ok',
    price_source: 'local',
    price_as_of: null,
    source_detail: null,
    items: [],
    freshness: null,
    source_status: null,
    data_as_of: null,
    fetched_at: null,
  })
  addWatch.mockResolvedValue({ ok: true, local_only: true, store: 'local_sqlite' })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('顶部标的搜索（QUOT-448）', () => {
  it('输入 6 位代码命中后，下拉可选，回车进入同一页', async () => {
    symbolsSearch.mockResolvedValue(envelope('600001', [{ ...PFB, symbol: '600001' }]))
    const onPick = vi.fn()
    render(<Harness onPick={onPick} />)

    const input = screen.getByRole('combobox', { name: /股票代码/ })
    expect(input.getAttribute('aria-expanded')).toBe('false')

    // 真实路径是「聚焦后键入」；fireEvent.change 不会替我们聚焦，下拉只在聚焦时展开
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '600001' } })
    const option = await screen.findByRole('option')
    expect(option.textContent).toContain('浦发银行')
    expect(option.textContent).toContain('600001')
    expect(input.getAttribute('aria-expanded')).toBe('true')

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onPick).toHaveBeenCalledWith('600001')
  })

  it('中文名同样命中；上下键移动高亮，aria-activedescendant 跟着走', async () => {
    symbolsSearch.mockResolvedValue(
      envelope('银行', [
        { ...PFB, symbol: '600002' },
        { ...PFB, symbol: '600003', name: '招商银行' },
      ]),
    )
    const onPick = vi.fn()
    render(<Harness onPick={onPick} />)

    const input = screen.getByRole('combobox', { name: /股票代码/ })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '银行' } })
    await screen.findAllByRole('option')

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    const second = screen.getAllByRole('option')[1]
    expect(input.getAttribute('aria-activedescendant')).toBe(second.id)

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onPick).toHaveBeenCalledWith('600003')
  })

  it('拼音查不到时说清是「拼音未接入」，而不是「没有这只股票」', async () => {
    symbolsSearch.mockResolvedValue(envelope('pfyh', []))
    render(<Harness />)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'pfyh' } })
    const hint = await screen.findByText(/拼音检索尚未接入/)
    expect(hint.textContent).toContain('pypinyin')
    expect(hint.textContent).toContain('中文名称')
  })

  it('接口失败给 role=alert，并提示仍可直接输代码', async () => {
    symbolsSearch.mockRejectedValue(new Error('回环连接被拒绝'))
    render(<Harness />)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: '600004' } })
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('名称搜索暂时不通')
    expect(alert.textContent).toContain('可以直接输入 6 位代码')
  })

  it('空输入不发请求', async () => {
    render(<Harness />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '   ' } })
    await new Promise((resolve) => setTimeout(resolve, 400))
    expect(symbolsSearch).not.toHaveBeenCalled()
  })
})

describe('顶部搜索条（QUOT-448）', () => {
  it('市场识别：A 股三段可用，港股 / 美股置灰标未接入', async () => {
    render(<ResearchSymbolBar code="600000" name="浦发银行" onOpen={() => undefined} />)
    await waitFor(() => expect(fetchWatchlist).toHaveBeenCalled())

    // 「沪」同时出现在当前标的的市场 Badge 与市场范围行里，按 title 精确定位范围行
    for (const label of ['沪', '深', '北']) {
      expect(screen.getByTitle(`${label}：本机数据面支持`)).toBeTruthy()
    }
    const hk = screen.getByTitle('本机数据面只有 A 股，港股行情未接入')
    expect(hk.getAttribute('aria-disabled')).toBe('true')
    expect(hk.textContent).toContain('未接入')
    const us = screen.getByTitle('本机数据面只有 A 股，美股行情未接入')
    expect(us.getAttribute('aria-disabled')).toBe('true')
    expect(screen.getByText('只做 A 股：港股 / 美股不假装支持')).toBeTruthy()
  })

  it('已在自选时按钮禁用；多空辩论入口标「待接入」且不可点', async () => {
    fetchWatchlist.mockResolvedValue({
      local_only: true,
      store: 'local_sqlite',
      available: true,
      reason: null,
      watch_count: 1,
      data_source_status: 'ok',
      price_source: 'local',
      price_as_of: null,
      source_detail: null,
      items: [
        {
          code: '600000',
          added_at: null,
          available: true,
          name: '浦发银行',
          market: 'SH',
          latest_price: null,
          change: null,
          change_pct: null,
          prev_close: null,
          price_as_of: null,
          price_status: 'unavailable',
          price_reason: null,
        },
      ],
      freshness: null,
      source_status: null,
      data_as_of: null,
      fetched_at: null,
    })

    render(<ResearchSymbolBar code="600000" name="浦发银行" onOpen={() => undefined} />)
    const inWatch = await screen.findByRole('button', { name: /已在自选/ })
    expect((inWatch as HTMLButtonElement).disabled).toBe(true)

    const debate = screen.getByText('多空辩论').closest('button')
    expect(debate?.getAttribute('aria-disabled')).toBe('true')
    expect(debate?.title).toContain('尚未接入')
  })

  it('非法输入只报错不跳转', async () => {
    const onOpen = vi.fn()
    render(<ResearchSymbolBar code={null} name={null} onOpen={onOpen} />)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: '6000' } })
    fireEvent.click(screen.getByRole('button', { name: /看这只/ }))

    expect(onOpen).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toContain('6 位 A 股代码')
  })

  it('合法代码换只时把代码交给页面路由', async () => {
    const onOpen = vi.fn()
    render(<ResearchSymbolBar code="600000" name="浦发银行" onOpen={onOpen} />)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: '000001.SZ' } })
    fireEvent.click(screen.getByRole('button', { name: /看这只/ }))

    expect(onOpen).toHaveBeenCalledWith('000001.SZ')
  })
})
