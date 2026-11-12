// @vitest-environment jsdom
/**
 * QUOT-552 首页一页工作台。
 *
 * 钉住本次改版的四件新事（旧能力的回归由既有用例继续钉，不重复）：
 * 1. **回车路由**（`routeHomeQuery` + 面板装配）：问句 → AI 对话、整串代码 → /research/:code、
 *    名称 / 关键词 → 既有搜索面板；路由判定复用 searchIntent，不写第二份解析；
 * 2. **三页签 + 唯一输入框**：快捷入口 / 研究股票 / 搜索历史常驻挂载，切页签不丢对话；
 * 3. **待办异动合并表**：类型列区分、待办在前异动在后、两颗 ⟳ 各刷各的端点（QUOT-521 不变量）；
 * 4. **紧凑摘要 + 徽标常驻**：持仓 3 列 + 合计 + 毛额·未扣费，自选 3 列；
 *    隐私徽标在加载中 / 失败态也不消失（DEC-033 / §5.3）。
 */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LadderEnvelope, ValidationPayload, WatchSnapshotPayload, WatchSnapshotRow } from '../src/api'
import { parsePositions, parseWatchlist, type PositionsPayload, type WatchlistPayload } from '../src/api/radar'
import { PrivacyBadge } from '../src/components/ui'
import { HomePositionsCard } from '../src/screens/home/HomePositionsCard'
import { HomeSearch } from '../src/screens/home/HomeSearch'
import { HomeTodoMoversCard, type HomeTodo } from '../src/screens/home/HomeTodoMoversCard'
import { HomeWatchlistCard } from '../src/screens/home/HomeWatchlistCard'
import type { HomeAssistantBundle } from '../src/screens/home/homeAssistant'
import { routeHomeQuery } from '../src/screens/home/homeQueryRoute'
import type { BlockView } from '../src/screens/home/useHomeSource'

const EMPTY_ASSISTANT: HomeAssistantBundle = {
  context: {
    page: '首页',
    date: '2026-11-06',
    market: null,
    watchlist: null,
    watch: null,
    validation: null,
    unavailable: [],
  },
  sources: [],
}

function view<T>(state: BlockView<T>['state'], data: T | null, onRefresh = () => {}): BlockView<T> {
  return {
    state,
    data,
    error: state === 'error' ? new Error('取数失败') : null,
    offline: state === 'offline',
    endpointMissing: false,
    onRetry: () => {},
    onRefresh,
    refreshing: false,
  }
}

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => jsonOk({ reply: '上证指数收跌 1.10%。', model: 'test', fetched_at: '2026-11-06T16:20:00Z' })),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('routeHomeQuery（回车路由判定）', () => {
  it('空输入不路由', () => {
    expect(routeHomeQuery('')).toEqual({ kind: 'empty' })
    expect(routeHomeQuery('   ')).toEqual({ kind: 'empty' })
  })

  it('整串代码（含市场前后缀写法）→ 个股深挖直达', () => {
    expect(routeHomeQuery('600519')).toEqual({ kind: 'research', code: '600519', href: '/research/600519' })
    expect(routeHomeQuery('sh600000')).toEqual({ kind: 'research', code: '600000', href: '/research/600000' })
    expect(routeHomeQuery('600000.SH')).toEqual({ kind: 'research', code: '600000', href: '/research/600000' })
  })

  it('问句（问号 / 疑问词）→ AI 研究对话；夹着代码的问句也是问句', () => {
    // normalizeQuery 走 NFKC：全角「？」归一成半角「?」，与检索层同一份口径
    expect(routeHomeQuery('今天大盘怎么样？')).toEqual({ kind: 'assistant', question: '今天大盘怎么样?' })
    expect(routeHomeQuery('帮我分析贵州茅台')).toEqual({ kind: 'assistant', question: '帮我分析贵州茅台' })
    expect(routeHomeQuery('600519 怎么样')).toEqual({ kind: 'assistant', question: '600519 怎么样' })
  })

  it('名称 / 关键词 → 聚合搜索面板（命中哪边由数据决定）', () => {
    expect(routeHomeQuery('浦发')).toEqual({ kind: 'search', text: '浦发' })
    expect(routeHomeQuery('机器人')).toEqual({ kind: 'search', text: '机器人' })
    expect(routeHomeQuery('AI')).toEqual({ kind: 'search', text: 'AI' })
  })
})

function renderPanel(initial = '/') {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/" element={<HomeSearch assistant={EMPTY_ASSISTANT} date="2026-11-06" />} />
        <Route path="/research/:code" element={<div>已到达个股深挖</div>} />
        <Route path="*" element={<div>已跳转</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

const bottomInput = () => screen.getByLabelText('搜索或提问') as HTMLInputElement

function typeAndEnter(text: string) {
  fireEvent.change(bottomInput(), { target: { value: text } })
  fireEvent.keyDown(bottomInput(), { key: 'Enter' })
}

describe('研究面板：三页签 + 唯一输入框（QUOT-552 目标 2/3）', () => {
  it('默认快捷入口页签，三页签都在，全卡只有一个输入框', () => {
    renderPanel()
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['快捷入口', '研究股票', '搜索历史'])
    expect(tabs[0].getAttribute('aria-selected')).toBe('true')
    // 嵌入态对话不再自带 textarea：全卡唯一输入框就是底部这一个
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.queryByLabelText('向研究助手提问')).toBeNull()
    // 快捷入口仍是 catalog 抽 4 条 + 换一批
    expect(screen.getByRole('button', { name: '换一批' })).toBeTruthy()
  })

  it('问句回车：切到研究股票页签、走 assistant 通道、输入框清空', async () => {
    renderPanel()
    typeAndEnter('今天大盘怎么样？')

    expect(screen.getByRole('tab', { name: /研究股票/ }).getAttribute('aria-selected')).toBe('true')
    expect(bottomInput().value).toBe('')
    expect(await screen.findByText('上证指数收跌 1.10%。')).toBeTruthy()
    const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/api/research/home/assistant'))
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body)).question).toBe('今天大盘怎么样?')
  })

  it('代码回车：直达 /research/:code，并按用户输入写最近搜索', async () => {
    renderPanel()
    typeAndEnter('600519')

    expect(await screen.findByText('已到达个股深挖')).toBeTruthy()
    expect(JSON.parse(window.localStorage.getItem('ldst.home-search.recent') ?? '[]')).toEqual(['600519'])
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/assistant'))).toBe(false)
  })

  it('名称回车：打开既有搜索面板（模态），词已填好', () => {
    renderPanel()
    typeAndEnter('机器人')

    const dialog = screen.getByRole('dialog')
    expect((within(dialog).getByRole('combobox') as HTMLInputElement).value).toBe('机器人')
    // 面板仍是那个 overlay：底部输入框不是第二套结果面板
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
  })

  it('输入法组合期间的回车不路由', () => {
    renderPanel()
    fireEvent.change(bottomInput(), { target: { value: '今天大盘怎么样？' } })
    fireEvent.keyDown(bottomInput(), { key: 'Enter', isComposing: true })
    expect(screen.getByRole('tab', { name: '快捷入口' }).getAttribute('aria-selected')).toBe('true')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('起步问题点一下只填底部输入框，不自动发请求', () => {
    renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: '研究股票' }))
    fireEvent.click(screen.getByRole('button', { name: '今天大盘和自选有哪些值得注意的变化？' }))
    expect(bottomInput().value).toBe('今天大盘和自选有哪些值得注意的变化？')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('搜索历史页签复用 model.recent：点 chip 打开面板带词', () => {
    window.localStorage.setItem('ldst.home-search.recent', JSON.stringify(['电力', '600000']))
    renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: '搜索历史' }))
    fireEvent.click(screen.getByRole('button', { name: '电力' }))
    const dialog = screen.getByRole('dialog')
    expect((within(dialog).getByRole('combobox') as HTMLInputElement).value).toBe('电力')
  })

  it('切页签不丢对话：回答常驻挂载，切走再切回还在', async () => {
    renderPanel()
    typeAndEnter('今天大盘怎么样？')
    expect(await screen.findByText('上证指数收跌 1.10%。')).toBeTruthy()

    // 常驻挂载 = 文本留在 DOM 里，只是所在页签被 hidden；不是卸载重建
    fireEvent.click(screen.getByRole('tab', { name: '快捷入口' }))
    expect(screen.getByText('上证指数收跌 1.10%。').closest('[hidden]')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: /研究股票/ }))
    expect(screen.getByText('上证指数收跌 1.10%。').closest('[hidden]')).toBeNull()
  })

  it('隐私徽标常驻卡头：点击展开全文，Esc 收起且焦点回徽标', () => {
    renderPanel()
    const badge = screen.getByRole('button', { name: '隐私说明：仅本机' })
    fireEvent.click(badge)
    const popover = screen.getByRole('dialog', { name: '隐私说明' })
    expect(popover.textContent).toContain('私有数据不出本机')
    expect(popover.textContent).toContain('持仓的成本、数量、盈亏一律不进 AI 上下文')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: '隐私说明' })).toBeNull()
    expect(document.activeElement).toBe(badge)
  })
})

const TODOS: HomeTodo[] = [
  { tone: 'warn', tag: '待对账', title: '2 条验证条件等待收盘对账', to: '/validate', action: '去验证' },
  { tone: 'info', tag: '待写', title: '今日复盘草稿还是空的', to: '/review', action: '去复盘' },
]

const MOVERS: WatchSnapshotRow[] = [
  { code: '300308', name: '中际旭创', limit_up_count: 1, change_pct: 9.98, status: 'limit_up' },
  { code: '600519', name: '贵州茅台', limit_up_count: 0, change_pct: -3.21, status: 'ok' },
]

function renderMerged(
  todos: HomeTodo[] = TODOS,
  movers: WatchSnapshotRow[] = MOVERS,
  onValidationRefresh = () => {},
  onWatchRefresh = () => {},
) {
  return render(
    <MemoryRouter>
      <HomeTodoMoversCard
        todos={todos}
        movers={movers}
        validation={view<LadderEnvelope<ValidationPayload>>('normal', null, onValidationRefresh)}
        watch={view<LadderEnvelope<WatchSnapshotPayload>>('normal', null, onWatchRefresh)}
      />
    </MemoryRouter>,
  )
}

describe('待办与异动合并表（QUOT-552 目标 1）', () => {
  it('一张表：类型列区分四类，待办在前、异动在后', () => {
    renderMerged()
    const table = screen.getByRole('table')
    const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
    expect(headers).toEqual(['类型', '事项', '涨跌 / 去向'])

    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(4)
    expect(rows[0].textContent).toContain('待对账')
    expect(rows[0].textContent).toContain('去验证')
    expect(rows[1].textContent).toContain('待写')
    expect(rows[2].textContent).toContain('异动')
    expect(rows[2].textContent).toContain('中际旭创')
    expect(rows[2].textContent).toContain('+9.98%')
    expect(rows[3].textContent).toContain('-3.21%')
    // 异动行点击直达个股深挖
    expect(within(rows[2]).getByRole('link').getAttribute('href')).toBe('/research/300308')
  })

  it('两颗 ⟳ 常驻且各刷各的：刷新待办不碰盯盘快照，反之亦然', () => {
    const onValidationRefresh = vi.fn()
    const onWatchRefresh = vi.fn()
    renderMerged(TODOS, MOVERS, onValidationRefresh, onWatchRefresh)

    fireEvent.click(screen.getByRole('button', { name: '刷新待办' }))
    expect(onValidationRefresh).toHaveBeenCalledTimes(1)
    expect(onWatchRefresh).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '刷新最近异动' }))
    expect(onWatchRefresh).toHaveBeenCalledTimes(1)
    expect(onValidationRefresh).toHaveBeenCalledTimes(1)
  })

  it('无待办无异动：明确空态，两颗 ⟳ 仍在', () => {
    renderMerged([], [])
    expect(screen.getByText('今日无待办')).toBeTruthy()
    expect(screen.getByRole('button', { name: '刷新待办' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '刷新最近异动' })).toBeTruthy()
  })
})

const POSITIONS_RAW = {
  local_only: true,
  store: 'local_sqlite',
  data_source_status: 'ok',
  as_of: '2026-11-06T15:00:00',
  price_source: 'daily_k',
  position_count: 2,
  valuation: {
    total_cost: 120000,
    market_value: 126300,
    priced_cost: 120000,
    unpriced_cost: 0,
    coverage_pct: 100,
    unrealized_pnl: 6300,
    unrealized_pnl_pct: 5.25,
  },
  items: [
    {
      code: '600000',
      name: '浦发银行',
      cost: 10,
      qty: 10000,
      entry_date: '2026-10-01',
      industry: '银行',
      note: '',
      latest_price: 10.63,
      market_value: 106300,
      unrealized_pnl: 6300,
      unrealized_pnl_pct: 6.3,
    },
    {
      code: '600519',
      cost: 100,
      qty: 200,
      entry_date: '2026-10-01',
      industry: '白酒',
      note: '',
      latest_price: 100,
      market_value: 20000,
      unrealized_pnl: 0,
      unrealized_pnl_pct: 0,
    },
  ],
  concentration: { available: false, reason: null, basis: 'cost' },
  sector_distribution: { available: false, reason: null, basis: 'cost' },
}

describe('首页持仓紧凑摘要（QUOT-552 目标 5/7）', () => {
  it('只有 3 列 + 合计行；毛额·未扣费与仅本机徽标常驻；完整持仓直达 /positions', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<HomePositionsCard view={view('normal', parsePositions(POSITIONS_RAW))} />} />
          <Route path="/positions" element={<div>已到达完整持仓</div>} />
        </Routes>
      </MemoryRouter>,
    )

    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['名称', '市值', '持仓盈亏'])
    // 全 6 列只留在 /positions：首页不出现收益率 / 当日盈亏列
    expect(screen.queryByRole('columnheader', { name: '收益率' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: '当日盈亏' })).toBeNull()

    expect(within(table).getByText('合计')).toBeTruthy()
    expect(within(table).getAllByText('126,300.00').length).toBeGreaterThan(0)
    expect(within(table).getAllByText('+6,300.00').length).toBeGreaterThan(0)
    expect(screen.getByText('毛额·未扣费')).toBeTruthy()
    expect(screen.getByRole('button', { name: '隐私说明：仅本机' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '完整持仓 →' }))
    expect(await screen.findByText('已到达完整持仓')).toBeTruthy()
  })

  it('加载中 / 失败态徽标也不消失（隐私承诺不随数据状态消失）', () => {
    const { unmount } = render(
      <MemoryRouter>
        <HomePositionsCard view={view<PositionsPayload>('loading', null)} />
      </MemoryRouter>,
    )
    expect(screen.getByText('毛额·未扣费')).toBeTruthy()
    expect(screen.getByRole('button', { name: '隐私说明：仅本机' })).toBeTruthy()
    unmount()

    render(
      <MemoryRouter>
        <HomePositionsCard view={view<PositionsPayload>('error', null)} />
      </MemoryRouter>,
    )
    expect(screen.getByText('毛额·未扣费')).toBeTruthy()
    expect(screen.getByRole('button', { name: '隐私说明：仅本机' })).toBeTruthy()
  })
})

const WATCHLIST_RAW = {
  local_only: true,
  store: 'local_sqlite',
  available: true,
  reason: null,
  watch_count: 1,
  data_source_status: 'ok',
  price_source: 'spot_quote',
  price_as_of: '2026-11-06T15:00:00',
  source_detail: null,
  items: [
    {
      code: '600000',
      added_at: '2026-11-01T09:30:00+08:00',
      available: true,
      name: '浦发银行',
      market: 'sh',
      latest_price: 10.63,
      change: 0.2,
      change_pct: 1.92,
      prev_close: 10.43,
      price_as_of: '2026-11-06T15:00:00',
      price_status: 'ok',
      price_reason: null,
      pe_ttm: 5.1,
      pb: 0.5,
      turnover_pct: 0.8,
      metrics_reason: null,
    },
  ],
}

describe('首页自选紧凑摘要（QUOT-552 目标 5/6）', () => {
  it('只有 3 列；隐私徽标常驻；管理自选直达 /watchlist', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<HomeWatchlistCard view={view('normal', parseWatchlist(WATCHLIST_RAW))} />} />
          <Route path="/watchlist" element={<div>已到达我的自选</div>} />
        </Routes>
      </MemoryRouter>,
    )

    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['名称', '现价', '涨跌'])
    expect(screen.queryByRole('columnheader', { name: /市盈率/ })).toBeNull()
    expect(screen.getByRole('button', { name: '隐私说明：仅本机' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '管理自选 →' }))
    expect(await screen.findByText('已到达我的自选')).toBeTruthy()
  })

  it('失败态徽标常驻，且 hover 口径与 PrivacyNotice 同源', () => {
    render(
      <MemoryRouter>
        <HomeWatchlistCard view={view<WatchlistPayload>('error', null)} />
      </MemoryRouter>,
    )
    const badge = screen.getByRole('button', { name: '隐私说明：仅本机' })
    expect(badge.getAttribute('title')).toContain('数据只在本机，不上传、不进 AI')
    expect(badge.getAttribute('title')).toContain('不写浏览器缓存')
  })
})

describe('PrivacyBadge 弹层（QUOT-552 目标 6）', () => {
  it('默认渲染 PrivacyNotice 本体：存储口径全文在弹层里，不丢信息', () => {
    render(<PrivacyBadge subject="持仓" />)
    fireEvent.click(screen.getByRole('button', { name: '隐私说明：仅本机' }))
    const popover = screen.getByRole('dialog', { name: '隐私说明' })
    expect(popover.textContent).toContain('数据只在本机，不上传、不进 AI')
    expect(popover.textContent).toContain('只在内存里展示本次请求结果')
    expect(popover.textContent).toContain('不写浏览器缓存')
  })

  it('点外部收起，不锁页面滚动（非模态）', () => {
    render(
      <div>
        <PrivacyBadge />
        <button type="button">外部</button>
      </div>,
    )
    fireEvent.click(screen.getByRole('button', { name: '隐私说明：仅本机' }))
    expect(screen.getByRole('dialog', { name: '隐私说明' })).toBeTruthy()
    fireEvent.pointerDown(screen.getByRole('button', { name: '外部' }))
    expect(screen.queryByRole('dialog', { name: '隐私说明' })).toBeNull()
  })
})