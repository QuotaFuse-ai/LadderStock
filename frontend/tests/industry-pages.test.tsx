// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import IndustryDetailScreen from '../src/screens/industry/IndustryDetailScreen'
import IndustryMatrixScreen from '../src/screens/industry/IndustryMatrixScreen'
import SignalsScreen from '../src/screens/signals/SignalsScreen'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/industry" element={<IndustryMatrixScreen />} />
        <Route path="/industry/:name" element={<IndustryDetailScreen />} />
        <Route path="/signals" element={<SignalsScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

function stubNotFound() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'Not Found' }),
    })) as unknown as typeof fetch,
  )
}

describe('产业矩阵 /industry', () => {
  it('演示源渲染 13 个行业，缺列或切换维度后表头跟着变', async () => {
    renderAt('/industry?demo=1')
    expect(await screen.findByRole('heading', { name: '产业研究' })).toBeTruthy()
    expect(await screen.findByText('食品饮料')).toBeTruthy()
    expect(screen.getByText('电子')).toBeTruthy()
    expect(screen.getByText('营收同比')).toBeTruthy()
    expect(screen.queryByText('PE 分位')).toBeNull()

    fireEvent.click(screen.getByRole('radio', { name: '估值' }))
    expect(await screen.findByText('PE 分位')).toBeTruthy()
    expect(screen.getByText('PB 分位')).toBeTruthy()
    expect(screen.queryByText('营收同比')).toBeNull()
  })

  it('搜索与「仅有数据」筛选生效，未绑定行业被筛掉', async () => {
    renderAt('/industry?demo=1&state=partial')
    await screen.findByText('房地产')

    fireEvent.change(screen.getByLabelText('筛选'), { target: { value: 'unbound' } })
    expect(await screen.findByText('综合')).toBeTruthy()
    expect(screen.queryByText('房地产')).toBeNull()

    fireEvent.change(screen.getByLabelText('筛选'), { target: { value: 'data' } })
    await waitFor(() => expect(screen.queryByText('综合')).toBeNull())
    expect(screen.getByText('食品饮料')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('搜索行业'), { target: { value: '银行' } })
    expect(await screen.findByText('银行')).toBeTruthy()
    expect(screen.queryByText('食品饮料')).toBeNull()
  })

  it('partial 态：显示缺失说明，缺值单元格写「无数据（未核实）」而不是 0', async () => {
    renderAt('/industry?demo=1&state=partial')
    expect(await screen.findByText('部分行业维度缺失')).toBeTruthy()
    const missing = screen.getAllByText('无数据（未核实）')
    expect(missing.length).toBeGreaterThan(0)
    expect(screen.queryByText('0.00')).toBeNull()
  })

  it('接口 404：空态写「接口未接入」并保留重试', async () => {
    stubNotFound()
    renderAt('/industry')
    expect(await screen.findByText('接口未接入')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: '刷新' }).length).toBeGreaterThan(0)
  })

  it('loading 强制态出骨架屏', async () => {
    renderAt('/industry?demo=1&state=loading')
    const busy = await waitFor(() => document.querySelector('[aria-busy="true"]'))
    expect(busy).toBeTruthy()
  })

  it('点击行业行进入详情', async () => {
    renderAt('/industry?demo=1')
    fireEvent.click(await screen.findByText('食品饮料'))
    expect(await screen.findByRole('heading', { name: '食品饮料' })).toBeTruthy()
    expect(await screen.findByText('成分股')).toBeTruthy()
  })
})

describe('单行业 /industry/:name', () => {
  it('五维卡片 + 历史分位 sparkline + 成分股 + 相关研报', async () => {
    renderAt('/industry/%E9%A3%9F%E5%93%81%E9%A5%AE%E6%96%99?demo=1')
    expect(await screen.findByRole('heading', { name: '食品饮料' })).toBeTruthy()
    expect(screen.getAllByText('景气').length).toBeGreaterThan(0)
    expect(screen.getAllByText('拥挤度').length).toBeGreaterThan(0)

    const sparkline = screen.getAllByRole('img').find((node) => node.getAttribute('aria-label')?.includes('历史序列'))
    expect(sparkline).toBeTruthy()

    expect(await screen.findByText('贵州茅台')).toBeTruthy()
    expect(screen.getByText('白酒行业 2026Q3 跟踪：动销边际改善，龙头估值处历史低位')).toBeTruthy()
    expect(screen.getByText(/R5 快照/)).toBeTruthy()
  })

  it('partial 态：估值缺数据、成分与资讯为空态，且不补 0', async () => {
    renderAt('/industry/%E9%A3%9F%E5%93%81%E9%A5%AE%E6%96%99?demo=1&state=partial')
    expect(await screen.findByText('部分维度无数据（未核实）')).toBeTruthy()
    expect(await screen.findByText('逐股成分未接入')).toBeTruthy()
    expect(screen.getByText('暂无关联资讯')).toBeTruthy()
    expect(screen.getAllByText('无数据（未核实）').length).toBeGreaterThan(0)
  })

  it('未知行业：给出候选而不是假装有数据', async () => {
    renderAt('/industry/%E4%B8%8D%E5%AD%98%E5%9C%A8?demo=1')
    expect(await screen.findByText(/没有「不存在」这个行业/)).toBeTruthy()
    expect(screen.getByRole('link', { name: '食品饮料' })).toBeTruthy()
  })
})

describe('产业信号 /signals', () => {
  it('命中计数、规则说明与证据抽屉（字段 + 时点 + 数据集）', async () => {
    renderAt('/signals?demo=1')
    expect(await screen.findByRole('heading', { name: '产业信号' })).toBeTruthy()
    expect(await screen.findByText('命中信号')).toBeTruthy()
    expect(screen.getAllByText('资金流入 + 估值低位').length).toBeGreaterThan(0)

    const buttons = await screen.findAllByRole('button', { name: '查看证据' })
    fireEvent.click(buttons[0])

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/证据链 · /)).toBeTruthy()
    expect(within(dialog).getByText('capital.constituents.main_net_inflow_nd')).toBeTruthy()
    expect(within(dialog).getAllByText('2026-10-30').length).toBeGreaterThan(0)
    expect(within(dialog).getByText('fund_flow')).toBeTruthy()
    expect(within(dialog).getByText(/判定由后端执行/)).toBeTruthy()
  })

  it('方向筛选只留偏空信号', async () => {
    renderAt('/signals?demo=1')
    expect(await screen.findAllByRole('link', { name: '食品饮料' })).toBeTruthy()
    expect(screen.queryByRole('link', { name: '电子' })).toBeTruthy()

    fireEvent.click(screen.getByRole('radio', { name: '偏空' }))
    await waitFor(() => expect(screen.queryByRole('link', { name: '食品饮料' })).toBeNull())
    expect(screen.getByRole('link', { name: '电子' })).toBeTruthy()
    expect(screen.getByRole('link', { name: '计算机' })).toBeTruthy()
  })

  it('partial 态提示被丢弃的证据链条数', async () => {
    renderAt('/signals?demo=1&state=partial')
    expect(await screen.findByText('部分信号证据链不完整')).toBeTruthy()
    expect(screen.getByText(/1 条信号缺少字段、时点或数据集/)).toBeTruthy()
  })

  it('接口 404：空态写「接口未接入」并保留规则说明入口', async () => {
    stubNotFound()
    renderAt('/signals')
    expect(await screen.findByText('接口未接入')).toBeTruthy()
    expect(screen.getByText(/GET \/api\/signals/)).toBeTruthy()
  })
})
