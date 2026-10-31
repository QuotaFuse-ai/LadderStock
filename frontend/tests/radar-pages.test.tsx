// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CalendarScreen from '../src/screens/radar/CalendarScreen'
import NewsScreen from '../src/screens/radar/NewsScreen'
import PoolScreen from '../src/screens/radar/PoolScreen'
import PositionsScreen from '../src/screens/radar/PositionsScreen'

/**
 * R10 四页：五态齐备 + 每页的核心交互（SPEC §8 验收项）。
 * 演示源 `?demo=1` 固定 2026-10 的样例；强制态走 `?state=`。
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/news" element={<NewsScreen />} />
        <Route path="/calendar" element={<CalendarScreen />} />
        <Route path="/pool" element={<PoolScreen />} />
        <Route path="/positions" element={<PositionsScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

function stubNotFound() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })),
  )
}

const PAGES = [
  {
    path: '/news',
    title: '资讯雷达',
    empty: '窗口内没有资讯',
    error: '资讯流取数失败',
    partial: '部分资讯栏目降级',
  },
  {
    path: '/calendar',
    title: '事件日历',
    empty: '这个月没有可用事件',
    error: '事件日历取数失败',
    partial: '部分小节降级',
  },
  {
    path: '/pool',
    title: '候选池',
    empty: '候选池未生成',
    error: '候选池取数失败',
    partial: '部分输入数据集缺失',
  },
  {
    path: '/positions',
    title: '我的持仓',
    empty: '还没有持仓',
    error: '持仓取数失败',
    partial: '部分持仓未取到价格',
  },
] as const

describe('四页五态', () => {
  for (const page of PAGES) {
    it(`${page.title}：normal / loading / empty / error / partial / offline 各一条`, async () => {
      const normal = renderAt(`${page.path}?demo=1`)
      expect(await screen.findByRole('heading', { name: page.title, level: 1 })).toBeTruthy()
      expect(screen.queryByText(/演示态：/)).toBeNull()
      normal.unmount()

      const loading = renderAt(`${page.path}?demo=1&state=loading`)
      expect(await waitFor(() => document.querySelector('[aria-busy="true"]'))).toBeTruthy()
      expect(screen.getByText(`演示态：加载中（?state=loading）`)).toBeTruthy()
      loading.unmount()

      const empty = renderAt(`${page.path}?demo=1&state=empty`)
      expect(await screen.findByText(page.empty)).toBeTruthy()
      empty.unmount()

      const error = renderAt(`${page.path}?demo=1&state=error`)
      expect(await screen.findByText(page.error)).toBeTruthy()
      error.unmount()

      const partial = renderAt(`${page.path}?demo=1&state=partial`)
      expect(await screen.findByText(page.partial)).toBeTruthy()
      partial.unmount()

      const offline = renderAt(`${page.path}?demo=1&state=offline`)
      expect((await screen.findAllByText('本机服务未连接')).length).toBeGreaterThan(0)
      offline.unmount()
    })
  }
})

describe('资讯雷达 /news', () => {
  it('三栏结构、已抽取事件标记与原文链接', async () => {
    renderAt('/news?demo=1')

    expect(await screen.findByRole('heading', { name: '来源筛选' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '时间流' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '详情' })).toBeTruthy()
    expect(screen.getAllByText('已抽取事件')).toHaveLength(2)

    const link = screen.getByRole('link', { name: /原文链接/ })
    expect(link.getAttribute('href')).toBe('https://www.cls.cn/detail/demo-6')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('点选条目后展示证据与合并来源', async () => {
    renderAt('/news?demo=1')
    fireEvent.click(await screen.findByText('央行开展 5000 亿元 MLF 操作，中标利率维持不变'))

    expect(await screen.findByText(/证据：当日开展 5000 亿元/)).toBeTruthy()
    expect(screen.getByText(/抽取器/)).toBeTruthy()

    const merged = screen.getByRole('link', { name: '央行加量续作 MLF，净投放 2000 亿元' })
    expect(merged.getAttribute('href')).toContain('wallstreetcn.com')
    expect(screen.getAllByText('政策发布').length).toBeGreaterThan(0)
  })

  it('代码 / 行业 / 来源过滤只作用于当前窗口', async () => {
    const code = renderAt('/news?demo=1')
    const codeInput = await screen.findByLabelText(/^代码/)
    fireEvent.change(codeInput, { target: { value: '600519' } })
    fireEvent.blur(codeInput)
    await waitFor(() => expect(screen.queryByText(/宁德时代/)).toBeNull())
    expect(screen.getAllByText('贵州茅台：2026 年第三季度报告').length).toBeGreaterThan(0)
    code.unmount()

    const industry = renderAt('/news?demo=1')
    const industryInput = await screen.findByLabelText(/^行业/)
    fireEvent.change(industryInput, { target: { value: '食品饮料' } })
    fireEvent.blur(industryInput)
    await waitFor(() => expect(screen.queryByText(/央行开展 5000 亿元/)).toBeNull())
    expect(screen.getAllByText('贵州茅台：2026 年第三季度报告').length).toBeGreaterThan(0)
    industry.unmount()

    renderAt('/news?demo=1')
    fireEvent.click(await screen.findByLabelText('财联社快讯'))
    await waitFor(() => expect(screen.queryByText(/沪深两市成交额/)).toBeNull())
    expect(screen.getByText('宁德时代：关于签订重大供货框架协议的公告')).toBeTruthy()
  })

  it('partial 态点名降级栏目，其余条目照常渲染', async () => {
    renderAt('/news?demo=1&state=partial')
    expect(await screen.findByText('部分资讯栏目降级')).toBeTruthy()
    expect(screen.getByText(/研究报告：研报上游 502/)).toBeTruthy()
    expect(screen.getByText('贵州茅台：2026 年第三季度报告')).toBeTruthy()
  })

  it('接口 404：空态写「接口未接入」', async () => {
    stubNotFound()
    renderAt('/news')
    expect(await screen.findByText('接口未接入')).toBeTruthy()
    expect(screen.getByText(/GET \/api\/news/)).toBeTruthy()
  })
})

describe('事件日历 /calendar', () => {
  it('月历定位到 10-28，当日面板列出解禁与规则抽取事件', async () => {
    renderAt('/calendar?demo=1')
    expect(await screen.findByRole('heading', { name: '事件日历' })).toBeTruthy()

    const grid = await screen.findByRole('group', { name: '2026-10 月历' })
    fireEvent.click(within(grid).getByRole('button', { name: /2026-10-28/ }))

    expect(await screen.findByText('限售解禁：定增机构配售股份')).toBeTruthy()
    expect(screen.getByText('贵州茅台：2026 年第一次临时股东大会决议公告')).toBeTruthy()
    expect(screen.getByText('非日历数据源')).toBeTruthy()
    expect(screen.getByRole('link', { name: /当日资讯/ }).getAttribute('href')).toBe(
      '/news?date=2026-10-28&demo=1',
    )
  })

  it('no_source 小节写明原因与上游补法', async () => {
    renderAt('/calendar?demo=1')
    expect(
      await screen.findByText('上游没有「财报预约披露时间表」工具，无法给出确切披露日期。'),
    ).toBeTruthy()
    expect(
      screen.getByText(
        '上游需求：TickHub-CN 增加 disclosure_schedule 数据集（预约披露日 + 变更标记）。',
      ),
    ).toBeTruthy()
    expect(screen.getAllByText('暂无数据源')).toHaveLength(3)
  })

  it('empty 态仍有五个小节的「暂无数据源」说明，不画空白格', async () => {
    renderAt('/calendar?demo=1&state=empty')
    expect(await screen.findByText('这个月没有可用事件')).toBeTruthy()
    expect(screen.getAllByText('暂无数据源')).toHaveLength(5)
  })

  it('接口 404：空态写「接口未接入」', async () => {
    stubNotFound()
    renderAt('/calendar')
    expect(await screen.findByText('接口未接入')).toBeTruthy()
    expect(screen.getByText(/GET \/api\/events/)).toBeTruthy()
  })
})

describe('候选池 /pool', () => {
  it('排序只改展示顺序，展开看分项依据但不重算总分', async () => {
    renderAt('/pool?demo=1')
    expect(await screen.findByRole('heading', { name: '候选池' })).toBeTruthy()
    expect(await screen.findByText('甲科技')).toBeTruthy()

    const table = screen.getByRole('table')
    const dataRows = () => within(table).getAllByRole('row').slice(1)
    expect(within(dataRows()[0]).getByText('600001')).toBeTruthy()

    fireEvent.click(within(table).getByRole('button', { name: /总分/ }))
    await waitFor(() => expect(within(dataRows()[0]).getByText('600005')).toBeTruthy())

    const row = within(table).getAllByRole('row').find((item) => within(item).queryByText('600001'))
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: '展开' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '甲科技 600001' })).toBeTruthy()
    for (const label of ['强度', '地位', '资金', '题材', '换手']) {
      expect(within(dialog).getAllByText(label).length).toBeGreaterThan(0)
    }
    expect(within(dialog).getByText('30.0%')).toBeTruthy()
    expect(within(dialog).getByText(/score = Σ/)).toBeTruthy()
    expect(within(dialog).getByText('五个分项齐全')).toBeTruthy()
    expect(within(dialog).getByText(/页面不重算/)).toBeTruthy()
  })

  it('partial 态：缺失分项写「无数据（未核实）」，抽屉点名缺项', async () => {
    renderAt('/pool?demo=1&state=partial')
    expect(await screen.findByText('部分输入数据集缺失')).toBeTruthy()
    expect(screen.getByText(/缺：fund_flow/)).toBeTruthy()
    expect(screen.getAllByText('无数据（未核实）').length).toBeGreaterThan(0)

    const table = screen.getByRole('table')
    const row = within(table).getAllByRole('row').find((item) => within(item).queryByText('600003'))
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: '展开' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getAllByText('无数据（未核实）').length).toBeGreaterThan(0)
    expect(within(dialog).getByText('有分项没有数据')).toBeTruthy()
    expect(within(dialog).getByText('缺 换手，不计入分母')).toBeTruthy()
  })

  it('显式点击才导出本地 JSON，文件名带交易日', async () => {
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: revokeObjectURL })
    let download = ''
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        download = this.download
      })

    try {
      renderAt('/pool?demo=1')
      fireEvent.click(await screen.findByRole('button', { name: /导出本地文件/ }))
      expect(createObjectURL).toHaveBeenCalledTimes(1)
      expect(click).toHaveBeenCalledTimes(1)
      expect(download).toBe('ladderstock-pool-2026-10-30.json')
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock')
    } finally {
      click.mockRestore()
      delete (URL as unknown as Record<string, unknown>).createObjectURL
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL
    }
  })

  it('接口 404：空态写「接口未接入」', async () => {
    stubNotFound()
    renderAt('/pool')
    expect(await screen.findByText('接口未接入')).toBeTruthy()
    expect(screen.getByText(/GET \/api\/pool/)).toBeTruthy()
  })
})

describe('我的持仓 /positions', () => {
  it('隐私提示常驻，盈亏 / 集中度 / 行业分布来自载荷', async () => {
    renderAt('/positions?demo=1')
    expect(await screen.findByRole('heading', { name: '我的持仓' })).toBeTruthy()
    expect(screen.getByText('数据只在本机，不上传、不进 AI')).toBeTruthy()

    expect(await screen.findByText('3.50 万')).toBeTruthy()
    expect(screen.getByText('+2,700.00')).toBeTruthy()
    expect(screen.getByText('43.0%')).toBeTruthy()

    const table = screen.getByRole('table')
    for (const code of ['600000', '600519', '300750']) {
      expect(within(table).getAllByText(code).length).toBeGreaterThan(0)
    }
    expect(screen.getByRole('heading', { name: '集中度' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '行业分布' })).toBeTruthy()
  })

  it('新增持仓走抽屉，成功后出现新行并提示本机写入', async () => {
    renderAt('/positions?demo=1')
    fireEvent.click(await screen.findByRole('button', { name: /新增持仓/ }))

    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/^代码/), { target: { value: '600036' } })
    fireEvent.change(within(dialog).getByLabelText(/^成本价/), { target: { value: '35' } })
    fireEvent.change(within(dialog).getByLabelText(/^数量/), { target: { value: '1000' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '新增持仓' }))

    expect((await screen.findAllByText('600036')).length).toBeGreaterThan(0)
    expect(screen.getByText('已新增 600036（本机 SQLite）')).toBeTruthy()
  })

  it('重复代码按 upsert 覆盖（与后端 POST /api/positions 同一条写契约）', async () => {
    renderAt('/positions?demo=1')
    fireEvent.click(await screen.findByRole('button', { name: /新增持仓/ }))

    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/^代码/), { target: { value: '600000' } })
    fireEvent.change(within(dialog).getByLabelText(/^成本价/), { target: { value: '10' } })
    fireEvent.change(within(dialog).getByLabelText(/^数量/), { target: { value: '1000' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '新增持仓' }))

    expect(await screen.findByText('已新增 600000（本机 SQLite）')).toBeTruthy()
    expect(screen.queryByText('HTTP 409')).toBeNull()
    expect(screen.getAllByText('600000').length).toBeGreaterThan(0)
  })

  it('删除走确认对话框，删除后行不再出现', async () => {
    renderAt('/positions?demo=1')
    fireEvent.click(await screen.findByRole('button', { name: '删除 600000' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('删除 600000？')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(screen.queryByText('600000')).toBeNull())
    expect(screen.getByText('已删除 600000（本机 SQLite）')).toBeTruthy()
  })

  it('partial 态：缺价行写「—」并给出原因，不补 0', async () => {
    renderAt('/positions?demo=1&state=partial')
    expect(await screen.findByText('部分持仓未取到价格')).toBeTruthy()
    expect(screen.getByTitle('未取到当日收盘价')).toBeTruthy()
    expect(screen.getByTitle('未取到价格')).toBeTruthy()
  })

  it('empty 态保留隐私提示与新增入口', async () => {
    renderAt('/positions?demo=1&state=empty')
    expect(await screen.findByText('还没有持仓')).toBeTruthy()
    expect(screen.getByText('数据只在本机，不上传、不进 AI')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /新增持仓/ }).length).toBeGreaterThan(0)
  })

  it('接口 404：空态写「接口未接入」并保留导入兜底', async () => {
    stubNotFound()
    renderAt('/positions')
    expect(await screen.findByText('接口未接入')).toBeTruthy()
    expect(screen.getByText(/GET \/api\/positions/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /导入本地 JSON/ })).toBeTruthy()
  })
})
