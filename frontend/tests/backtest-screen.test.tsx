// @vitest-environment jsdom
/**
 * QUOT-534 策略回测页：结果区 + 证据区按运行卡渲染，参数区只搭骨架。
 *
 * 关键红线都在这里钉住：
 * - 指标展示串直接取 `metrics_display`（夹具里故意与 `metrics × 100` 不同，页面必须显示后端串）；
 * - 参数区不塞策略常量、不预填默认值、不发 POST；
 * - 证据区下载链接直达后端导出端点，run_id / 数据指纹 / 每指标 calc_id 都在页面上；
 * - 端点 404 时按「未接入」呈现，不编造数据。
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BacktestScreen from '../src/screens/backtest/BacktestScreen'
import {
  runCardFixture,
  runsPayloadFixture,
  secondRunCardFixture,
} from './fixtures/backtestRunCard'

// jsdom 没有画布：EChart 换成同构占位，断言只关心 aria 等价文本是否由运行卡生成
vi.mock('../src/charts/EChart', () => ({
  EChart: ({ ariaLabel }: { ariaLabel?: string }) => <div data-testid="echart">{ariaLabel ?? 'chart'}</div>,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

interface FetchOverrides {
  runs?: () => Response
  card?: (runId: string) => Response
}

/** 按 URL 分流的只读桩：列表 / 单卡各走各的，其它路径一律 404。 */
function stubBacktestFetch(overrides: FetchOverrides = {}) {
  const fetchMock = vi.fn(async (input: unknown) => {
    const url = String(input)
    if (url.startsWith('/api/backtest/runs?')) {
      return overrides.runs ? overrides.runs() : jsonResponse(runsPayloadFixture)
    }
    const match = url.match(/^\/api\/backtest\/runs\/(bt_[0-9a-z_]+)$/)
    if (match) {
      if (overrides.card) return overrides.card(match[1])
      return jsonResponse(match[1] === 'bt_7dc1b51c' ? secondRunCardFixture : runCardFixture)
    }
    return jsonResponse({ detail: 'Not Found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/backtest" element={<BacktestScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

async function waitForCard() {
  await waitFor(() => expect(screen.getByText('核心指标')).toBeTruthy())
}

describe('结果区', () => {
  it('指标值取后端展示串，不在前端用原值重算', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()

    const paramsForm = screen.getByRole('form', { name: /回测参数/ })
    expect(within(paramsForm).queryByText(/69\.29%/)).toBeNull()
    // 夹具原值 ×100 两位小数 = 69.29%，展示串是 69.30%；页面必须以后者为准
    expect(screen.getAllByText('69.30%').length).toBeGreaterThan(0)
    expect(screen.queryByText('69.29%')).toBeNull()
    // 原值仍在证据区原样出现
    expect(screen.getAllByText('0.6929346601266777').length).toBeGreaterThan(0)
  })

  it('校验门未通过时红因直接铺在结果区，并标出通过项计数', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()

    expect(screen.getAllByText('校验门未通过').length).toBeGreaterThan(0)
    expect(screen.getByText('2/3 项通过')).toBeTruthy()
    expect(screen.getAllByText(/完整回合仅 18 次 < 阈值 20/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/不在成本档位/).length).toBeGreaterThan(0)
    const alerts = screen.getAllByRole('alert')
    expect(alerts.some((alert) => alert.textContent?.includes('校验门未通过'))).toBe(true)
  })

  it('成本三档与分段表的数值来自运行卡，档位标签只是换单位', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()

    expect(screen.getByText('万2')).toBeTruthy()
    expect(screen.getByText('万5')).toBeTruthy()
    expect(screen.getByText('千1')).toBeTruthy()
    // 最高成本档累计收益 0.6467132337916035 → 64.67%
    expect(screen.getByText('64.67%')).toBeTruthy()
    expect(screen.getByText('样本内')).toBeTruthy()
    expect(screen.getByText('样本外')).toBeTruthy()
  })
})

describe('参数区骨架', () => {
  it('策略下拉只有等待态，没有前端硬编码的策略清单', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()

    const paramsForm = screen.getByRole('form', { name: /回测参数/ })
    const selects = within(paramsForm).getAllByRole('combobox')
    expect(selects).toHaveLength(1)
    expect(within(selects[0]).getAllByRole('option').map((option) => option.textContent)).toEqual([
      '等待后端策略清单接口…',
    ])
    expect(within(paramsForm).queryByRole('option', { name: /breakout|ma_cross/ })).toBeNull()
    // 标的按 M1 规格默认 600000，但输入全部禁用
    expect((within(paramsForm).getByLabelText('标的') as HTMLInputElement).value).toBe('600000')
    expect((within(paramsForm).getByRole('button', { name: '运行回测' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    expect(screen.getByText('QUOT-535 合并后接线')).toBeTruthy()
    expect(screen.getAllByText(/QUOT-535/).length).toBeGreaterThan(0)
  })

  it('页面只发 GET，不发任何 POST', async () => {
    const fetchMock = stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()
    expect(fetchMock).toHaveBeenCalled()
    for (const [, init] of fetchMock.mock.calls as unknown as Array<[string, RequestInit]>) {
      expect(init.method).toBe('GET')
    }
  })
})

describe('证据区', () => {
  it('run_id、数据指纹、每指标 calc_id 与下载链接都在页面上', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()

    expect(screen.getByRole('region', { name: '证据区' })).toBeTruthy()
    expect(screen.getAllByText('bt_2e6bab12').length).toBeGreaterThan(0)
    expect(screen.getAllByText('641:2024-03-14:2026-11-05:ace34e2664625754').length).toBeGreaterThan(0)
    // 11 个主指标的 calc_id 都进溯源表
    for (const calcId of Object.values(runCardFixture.metrics_calc_ids)) {
      expect(screen.getAllByText(calcId).length).toBeGreaterThan(0)
    }
    expect(screen.getAllByText('c_73ef8b31').length).toBeGreaterThan(0)

    const json = screen.getByRole('link', { name: /运行卡 JSON/ })
    expect(json.getAttribute('href')).toBe('/api/backtest/runs/bt_2e6bab12/export.json')
    const markdown = screen.getByRole('link', { name: /运行卡 Markdown/ })
    expect(markdown.getAttribute('href')).toBe('/api/backtest/runs/bt_2e6bab12/export.md')

    expect(screen.getAllByText('bt_2e6bab12.json').length).toBeGreaterThan(0)
  })
})

describe('运行卡选择', () => {
  it('默认取后端 latest，切换下拉后重新取对应运行卡', async () => {
    const fetchMock = stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain('/api/backtest/runs/bt_2e6bab12')

    fireEvent.change(screen.getByLabelText('运行卡'), { target: { value: 'bt_7dc1b51c' } })
    await waitFor(() =>
      expect(fetchMock.mock.calls.map(([url]) => url)).toContain('/api/backtest/runs/bt_7dc1b51c'),
    )
    await waitFor(() => expect(screen.getAllByText('校验门通过').length).toBeGreaterThan(0))
    expect(screen.getAllByText('bt_7dc1b51c').length).toBeGreaterThan(0)
  })

  it('`?run=` 深链直接打开指定运行卡，不落到 latest', async () => {
    const fetchMock = stubBacktestFetch()
    renderAt('/backtest?run=bt_7dc1b51c')
    await waitFor(() => expect(screen.getAllByText('校验门通过').length).toBeGreaterThan(0))
    const urls = fetchMock.mock.calls.map(([url]) => url)
    expect(urls).toContain('/api/backtest/runs/bt_7dc1b51c')
    expect(urls).not.toContain('/api/backtest/runs/bt_2e6bab12')
  })
})

describe('接口缺失 / 空态', () => {
  it('列表 404 时按「回测接口尚未接入」呈现，不展示任何替代数据', async () => {
    stubBacktestFetch({ runs: () => jsonResponse({ detail: 'Not Found' }, 404) })
    renderAt('/backtest')
    await waitFor(() => expect(screen.getByText('回测接口尚未接入')).toBeTruthy())
    expect(screen.queryByText('核心指标')).toBeNull()
    expect(screen.queryByText('证据区')).toBeNull()
  })

  it('没有运行卡时给出 CLI 出口，且不再请求单卡', async () => {
    const fetchMock = stubBacktestFetch({
      runs: () => jsonResponse({ root: '/tmp', count: 0, latest: null, runs: [] }),
    })
    renderAt('/backtest')
    await waitFor(() => expect(screen.getByText('还没有回测运行卡')).toBeTruthy())
    expect(screen.getAllByText(/ldst backtest run/).length).toBeGreaterThan(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
