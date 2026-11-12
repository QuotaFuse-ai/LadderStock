// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ResearchDetailScreen from '../src/screens/research/ResearchDetailScreen'
import ResearchReportScreen from '../src/screens/research/ResearchReportScreen'

/**
 * uPlot 画在 `<canvas>` 上，jsdom 没有 2D 上下文（同 market-demo.test 的先例），
 * 概览 tab 的走势图换成记录入参的替身，这里只断言页面结构不断言画布。
 */
vi.mock('../src/charts/Chart', () => ({
  LineChart: ({ points, ariaLabel }: { points?: unknown[]; ariaLabel?: string }) => (
    <div data-testid="line-chart" data-points={points?.length ?? 0} aria-label={ariaLabel} />
  ),
  BarChart: ({ points, ariaLabel }: { points?: unknown[]; ariaLabel?: string }) => (
    <div data-testid="bar-chart" data-points={points?.length ?? 0} aria-label={ariaLabel} />
  ),
}))

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
  it('概览默认：走势 + 财报卡直接出，深度研究要点 tab 才看', async () => {
    renderRoute('/research/600000?demo=1')

    // 默认概览：走势块与财报卡直接出（走势本体由 KlineCard 自取 /kline，这里只看块在）
    expect(await screen.findByRole('tab', { name: '概览' })).toBeTruthy()
    expect(await screen.findByRole('heading', { name: '走势' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '财报前瞻与复盘' })).toBeTruthy()

    // 切到深度研究：六段内容照旧（回归）
    fireEvent.click(screen.getByRole('tab', { name: '深度研究' }))
    expect(await screen.findByRole('heading', { name: '公司画像' })).toBeTruthy()
    expect(screen.getByText('股份制银行')).toBeTruthy()
    expect(screen.getByText('公司金融、零售金融、资金业务')).toBeTruthy()
    expect(screen.getByText('上海国际集团有限公司')).toBeTruthy()
    expect(screen.getAllByText(/1,203\.6 亿/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/净息差持续收窄，利息净收入承压/).length).toBeGreaterThan(0)
    expect(screen.getByText('PE 近五年分位')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: /研究报告/ }).length).toBeGreaterThan(0)
  })

  it('五态可只靠 URL 演示（深度研究 tab）', async () => {
    renderRoute('/research/600000?demo=1&state=empty&tab=research')
    expect(await screen.findByText(/六段深度产物/)).toBeTruthy()
    expect(screen.getByText(/演示态：空态/)).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=offline&tab=research')
    expect(await screen.findByText('离线：暂时读不到研究产物')).toBeTruthy()
    expect(screen.getByText(/演示态：离线/)).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=error&tab=research')
    expect(await screen.findByText(/没有真实错误/)).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=loading&tab=research')
    expect(await screen.findByText('正在加载…')).toBeTruthy()
    cleanup()

    renderRoute('/research/600000?demo=1&state=partial&tab=research')
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

describe('首次研究（QUOT-561）：没有产物也能从页面起跑', () => {
  it('空态报告页给「开始研究」；演示样本不触发起跑', async () => {
    renderRoute('/research/600000/report?demo=1&variant=empty')

    expect(await screen.findByText('六段产物还没有生成')).toBeTruthy()
    expect(screen.getByText(/点「开始研究」从第一段跑起/)).toBeTruthy()
    const start = screen.getByRole('button', { name: '开始研究' })
    expect((start as HTMLButtonElement).disabled).toBe(false)
  })

  it('空态详情页给「去报告页开始研究」，指向报告页', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        return new Response(JSON.stringify({ detail: '600000 还没有任何研究记录' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
    try {
      renderRoute('/research/600000?tab=research')
      expect(await screen.findByText('这只还没跑过研究')).toBeTruthy()
      const link = screen.getByRole('link', { name: '去报告页开始研究' })
      expect(link.getAttribute('href')).toMatch(/^\/research\/600000\/report(?:\?|$)/)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('点「开始研究」提交 POST /api/research/{code}/run，从失败段起跑', async () => {
    const calls: Array<{ method: string; url: string; body: unknown }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const url = String(input)
        calls.push({ method: init.method ?? 'GET', url, body: init.body ? JSON.parse(String(init.body)) : null })
        if (url.includes('/api/research/600000/run')) {
          return new Response(JSON.stringify({ job_id: 'job-1', status: 'queued' }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        if (url.includes('/api/research/jobs/job-1')) {
          return new Response(JSON.stringify({ job_id: 'job-1', status: 'running', step: '公司画像', progress: 0.2 }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        // 状态接口：没有任何产物 → 空态（真实 404，不回落演示）
        return new Response(JSON.stringify({ detail: '600000 还没有任何研究记录' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
    try {
      renderRoute('/research/600000/report')
      fireEvent.click(await screen.findByRole('button', { name: '开始研究' }))

      await waitFor(() => expect(calls.some((call) => call.url.includes('/api/research/600000/run'))).toBe(true))
      const posted = calls.find((call) => call.url.includes('/api/research/600000/run'))
      expect(posted?.method).toBe('POST')
      expect(posted?.body).toEqual({ date: expect.any(String), only: 'report' })
      await waitFor(() => expect(calls.some((call) => call.url.includes('/api/research/jobs/job-1'))).toBe(true), {
        timeout: 5000,
      })
    } finally {
      vi.unstubAllGlobals()
    }
  }, 10000)
})
