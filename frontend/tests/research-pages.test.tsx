// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import ResearchDetailScreen from '../src/screens/research/ResearchDetailScreen'
import ResearchReportScreen from '../src/screens/research/ResearchReportScreen'

afterEach(cleanup)

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/research/:code" element={<ResearchDetailScreen />} />
        <Route path="/research/:code/report" element={<ResearchReportScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('个股深挖页', () => {
  it('展示公司画像、财务、风险与估值分位', async () => {
    renderRoute('/research/600000?demo=1')

    expect(await screen.findByRole('heading', { name: '浦发银行' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '公司画像' })).toBeTruthy()
    expect(screen.getByText('股份制银行')).toBeTruthy()
    expect(screen.getByText('公司金融、零售金融、资金业务')).toBeTruthy()
    expect(screen.getByText('上海国际集团有限公司')).toBeTruthy()
    expect(screen.getAllByText(/1,203\.6 亿/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/净息差持续收窄，利息净收入承压/).length).toBeGreaterThan(0)
    expect(screen.getByText('PE 近五年分位')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: /研究报告/ }).length).toBeGreaterThan(0)
  })

  it('五态可只靠 URL 演示', async () => {
    renderRoute('/research/600000?demo=1&state=empty')
    expect(await screen.findByText(/先运行 ldst research/)).toBeTruthy()
    expect(screen.getByText(/演示态：空态/)).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=offline')
    expect(await screen.findByText('离线：暂时读不到研究产物')).toBeTruthy()
    expect(screen.getByText(/演示态：离线/)).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=error')
    expect(await screen.findByText(/没有真实错误/)).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=loading')
    expect(await screen.findByText('正在加载…')).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=partial')
    expect(await screen.findByText('2 个段落未完成')).toBeTruthy()
    expect(screen.getByText(/演示态：部分失败/)).toBeTruthy()
  })

  it('非法代码给出明确出口', async () => {
    renderRoute('/research/abc')
    expect(await screen.findByText('证券代码不合法')).toBeTruthy()
  })
})

describe('六阶段研究报告页', () => {
  it('角标打开来源抽屉，Esc 关闭', async () => {
    renderRoute('/research/600000/report?demo=1&stage=financial')

    expect(await screen.findByRole('heading', { name: '2. 财务' })).toBeTruthy()
    expect(screen.getAllByText(/1,203\.6 亿元/).length).toBeGreaterThan(0)

    const badges = await screen.findAllByRole('button', { name: /查看来源 1/ })
    fireEvent.click(badges[0])

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('来源 · 财务')).toBeTruthy()
    expect(within(dialog).getAllByText('/v1/financials/600000').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('as_of').length).toBeGreaterThan(0)

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('全部无来源时每个数字都标〔未核实〕，抽屉诚实说明零来源', async () => {
    renderRoute('/research/600000/report?demo=1&variant=all-unverified&stage=company')

    const marks = await screen.findAllByText('〔未核实〕')
    expect(marks.length).toBeGreaterThan(2)
    expect(marks[0].className).toContain('text-warn')
    // 没有已核实来源 → 正文不出现可点的角标
    expect(screen.queryAllByRole('button', { name: /查看来源/ })).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '来源（0）' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('本段没有引用来源')).toBeTruthy()
  })

  it('失败段「重试本段」→ 轮询 → 成功回填', async () => {
    renderRoute('/research/600000/report?demo=1&variant=partial&stage=valuation')

    expect(await screen.findByText('本段生成失败')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '重试本段' }))

    expect(await screen.findByText(/正在重跑「估值」/)).toBeTruthy()
    await waitFor(() => expect(screen.queryByText(/正在重跑「估值」/)).toBeNull(), {
      timeout: 5000,
    })
    expect(await screen.findAllByText(/按 FinanceCoffer 快照口径/)).not.toHaveLength(0)
    expect(await screen.findByRole('button', { name: '来源（2）' })).toBeTruthy()
  }, 10000)

  it('左侧阶段导航可切换，并保留 URL 参数', async () => {
    renderRoute('/research/600000/report?demo=1&stage=company')
    expect(await screen.findByRole('heading', { name: '1. 公司画像' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /风险/ }))
    expect(await screen.findByRole('heading', { name: '5. 风险' })).toBeTruthy()
  })
})
