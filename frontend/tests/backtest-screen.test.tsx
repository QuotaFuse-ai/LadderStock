// @vitest-environment jsdom
/**
 * QUOT-534 策略回测页：参数区触发回测 + 结果区/证据区只读运行卡。
 *
 * 关键红线都在这里钉住：
 * - 指标展示串直接取 `metrics_display`（夹具里故意与 `metrics × 100` 不同，页面必须显示后端串）；
 * - 参数区的策略/参数 schema/成本默认值全部来自 `GET /strategies`（夹具用 `demo_*` 名字，
 *   页面若写死任何内置策略名会立刻失败）；
 * - 提交把成本字段原样透传（不做万分比换算），轮询成功后自动载入新运行卡；
 * - 后端 422/任务失败的结构化原因直接铺在页面上；
 * - 端点 404 时按「未接入」呈现，不编造数据。
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BacktestScreen from '../src/screens/backtest/BacktestScreen'
import { succeededJobFixture, strategiesFixture } from './fixtures/backtestStrategies'
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

/** 本次测试里所有 `POST /api/backtest/run` 的请求体（按发生顺序）。 */
const submittedBodies: Array<Record<string, unknown>> = []

interface FetchOverrides {
  runs?: () => Response
  card?: (runId: string) => Response
  strategies?: () => Response
  submit?: (body: Record<string, unknown>) => Response
  job?: (jobId: string) => Response
}

/** 按 URL 分流的桩：读接口走运行卡夹具，触发面走 QUOT-535 夹具，其它路径 404。 */
function stubBacktestFetch(overrides: FetchOverrides = {}) {
  submittedBodies.length = 0
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/backtest/strategies') {
      return overrides.strategies ? overrides.strategies() : jsonResponse(strategiesFixture)
    }
    if (url === '/api/backtest/run' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      submittedBodies.push(body)
      return overrides.submit
        ? overrides.submit(body)
        : jsonResponse({ job_id: succeededJobFixture.job_id, status: 'queued' }, 202)
    }
    if (url.startsWith('/api/backtest/jobs/')) {
      const jobId = url.split('/').pop() ?? ''
      return overrides.job ? overrides.job(jobId) : jsonResponse(succeededJobFixture)
    }
    if (url.startsWith('/api/backtest/runs?')) {
      return overrides.runs ? overrides.runs() : jsonResponse(runsPayloadFixture)
    }
    const match = url.match(/^\/api\/backtest\/runs\/(bt_[0-9a-z_]+)$/)
    if (match) {
      if (overrides.card) return overrides.card(match[1])
      if (match[1] === 'bt_9f00c0de') {
        return jsonResponse({ ...secondRunCardFixture, run_id: 'bt_9f00c0de' })
      }
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

describe('参数区', () => {
  async function formReady() {
    const form = screen.getByRole('form', { name: /回测参数/ })
    await waitFor(() =>
      expect((within(form).getByRole('combobox', { name: /^策略/ }) as HTMLSelectElement).value).toBe('demo_cross'),
    )
    return form
  }

  it('策略与参数 schema 全部来自后端清单，页面没有内置策略常量', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()
    const form = await formReady()

    const strategy = within(form).getByRole('combobox', { name: /^策略/ }) as HTMLSelectElement
    expect(within(strategy).getAllByRole('option').map((option) => (option as HTMLOptionElement).value)).toEqual([
      'demo_cross',
      'demo_break',
      'demo_threshold',
    ])
    // 参数按 schema 渲染：label / type / default / min / max 都是后端给的
    const short = within(form).getByLabelText(/^短均线窗口/) as HTMLInputElement
    expect(short.type).toBe('number')
    expect(short.value).toBe('5')
    expect(short.min).toBe('1')
    expect(short.max).toBe('250')
    expect(short.step).toBe('1')
    expect((within(form).getByLabelText(/^长均线窗口/) as HTMLInputElement).value).toBe('20')
    // 标的来自 symbol_default；日期初值来自最新运行卡区间
    expect((within(form).getByLabelText(/^标的/) as HTMLInputElement).value).toBe('600000')
    expect((within(form).getByLabelText(/^开始日期/) as HTMLInputElement).value).toBe('2024-03-14')
    expect((within(form).getByLabelText(/^结束日期/) as HTMLInputElement).value).toBe('2026-11-05')
    expect(within(form).getByRole('button', { name: /运行回测/ })).toBeTruthy()
    expect(screen.queryByText(/QUOT-535 合并后接线/)).toBeNull()
  })

  it('切换策略后参数换成该策略的 schema 与后端默认值', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()
    const form = await formReady()

    fireEvent.change(within(form).getByRole('combobox', { name: /^策略/ }), { target: { value: 'demo_break' } })
    expect(within(form).getByLabelText(/^通道窗口/)).toBeTruthy()
    expect(within(form).queryByLabelText(/^短均线窗口/)).toBeNull()

    fireEvent.change(within(form).getByRole('combobox', { name: /^策略/ }), { target: { value: 'demo_threshold' } })
    const threshold = within(form).getByLabelText(/^触发阈值/) as HTMLInputElement
    expect(threshold.value).toBe('0.01')
    expect(threshold.step).toBe('any')
    expect(threshold.min).toBe('-0.5')
    expect(threshold.max).toBe('0.5')
  })

  it('券商三档预设来自后端 config_defaults，选中后只填佣金率与最低佣金', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()
    const form = await formReady()

    const preset = within(form).getByRole('combobox', { name: /^成本预设/ }) as HTMLSelectElement
    expect(within(preset).getAllByRole('option').map((option) => (option as HTMLOptionElement).value)).toEqual([
      'wan09_free5_001',
      'wan085_free5_1',
      'wan065_min5',
      'custom',
    ])
    // 默认配置命中第一档
    expect(preset.value).toBe('wan09_free5_001')

    fireEvent.change(preset, { target: { value: 'wan065_min5' } })
    expect((within(form).getByLabelText(/^佣金率/) as HTMLInputElement).value).toBe('0.65')
    expect((within(form).getByLabelText(/^单笔最低佣金/) as HTMLInputElement).value).toBe('5')
    // 过户费 / 印花税 / 滑点仍取后端默认值，不随预设变
    expect((within(form).getByLabelText(/^过户费/) as HTMLInputElement).value).toBe('0.1')
    expect((within(form).getByLabelText(/^印花税/) as HTMLInputElement).value).toBe('5')
    expect((within(form).getByLabelText(/^滑点/) as HTMLInputElement).value).toBe('0.001')

    // 手改佣金率即离开预设档，避免下拉标签与数值不一致
    fireEvent.change(within(form).getByLabelText(/^佣金率/), {
      target: { value: '1.2' },
    })
    expect((within(form).getByRole('combobox', { name: /^成本预设/ }) as HTMLSelectElement).value).toBe('custom')
  })

  it('提交把表单值原样透传，轮询成功后自动载入新运行卡', async () => {
    const fetchMock = stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()
    const form = await formReady()

    fireEvent.change(within(form).getByRole('combobox', { name: /^成本预设/ }), { target: { value: 'wan065_min5' } })
    fireEvent.click(within(form).getByRole('button', { name: /运行回测/ }))

    await waitFor(() => expect(submittedBodies).toHaveLength(1))
    expect(submittedBodies[0]).toEqual({
      strategy: 'demo_cross',
      strategy_params: { short_window: 5, long_window: 20 },
      symbol: '600000',
      start: '2024-03-14',
      end: '2026-11-05',
      // 成本字段按用户选择原样透传（万分量纲），前端不做换算
      commission_rate_wan: 0.65,
      commission_floor_yuan: 5,
      transfer_fee_wan: 0.1,
      stamp_duty_wan: 5,
      slippage: 0.001,
    })
    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect((postCall?.[1]?.headers as Record<string, string>)['X-LDST-Client']).toBe('1')

    // 轮询任务 → ok → 自动切到新 run_id 并载入运行卡
    await waitFor(() =>
      expect(fetchMock.mock.calls.map(([url]) => url)).toContain(
        `/api/backtest/jobs/${succeededJobFixture.job_id}`,
      ),
    )
    await waitFor(() =>
      expect(fetchMock.mock.calls.map(([url]) => url)).toContain('/api/backtest/runs/bt_9f00c0de'),
    )
    await waitFor(() => expect(screen.getByText('回测完成')).toBeTruthy())
    expect(screen.getAllByText('bt_9f00c0de').length).toBeGreaterThan(0)
  })

  it('任务失败时按退出码给出中文分类与后端原因', async () => {
    stubBacktestFetch({
      job: () =>
        jsonResponse({
          ...succeededJobFixture,
          status: 'failed',
          run_id: null,
          gate_passed: null,
          exit_code: 4,
          error: '日 K 缺失：000001 在 2024-03-14~2026-11-05 没有数据',
        }),
    })
    renderAt('/backtest')
    await waitForCard()
    const form = await formReady()

    fireEvent.click(within(form).getByRole('button', { name: /运行回测/ }))
    await waitFor(() => expect(screen.getByText('退出码 4 · 数据缺失')).toBeTruthy())
    expect(screen.getByText(/日 K 缺失：000001/)).toBeTruthy()
  })

  it('后端 422 的结构化原因直接铺在页面上', async () => {
    stubBacktestFetch({
      submit: () =>
        jsonResponse(
          {
            detail: [
              {
                loc: ['body', 'start'],
                msg: 'start 必须是 YYYY-MM-DD',
                type: 'value_error',
              },
            ],
          },
          422,
        ),
    })
    renderAt('/backtest')
    await waitForCard()
    const form = await formReady()

    fireEvent.click(within(form).getByRole('button', { name: /运行回测/ }))
    await waitFor(() => expect(screen.getByText(/start：start 必须是 YYYY-MM-DD/)).toBeTruthy())
    expect(document.body.textContent).toContain('HTTP 422')
  })

  it('开始日期晚于结束日期时本地拦截，不发 POST', async () => {
    stubBacktestFetch()
    renderAt('/backtest')
    await waitForCard()
    const form = await formReady()

    fireEvent.change(within(form).getByLabelText(/^结束日期/), { target: { value: '2024-01-01' } })
    fireEvent.click(within(form).getByRole('button', { name: /运行回测/ }))
    await waitFor(() => expect(screen.getByText('开始日期不能晚于结束日期')).toBeTruthy())
    expect(submittedBodies).toHaveLength(0)
  })

  it('策略清单 404 时明确「未接入」，不回退到前端常量', async () => {
    stubBacktestFetch({ strategies: () => jsonResponse({ detail: 'Not Found' }, 404) })
    renderAt('/backtest')
    await waitForCard()
    await waitFor(() => expect(screen.getByText('策略清单接口未接入')).toBeTruthy())
    const form = screen.getByRole('form', { name: /回测参数/ })
    const controls = within(form).getAllByRole('combobox') as HTMLSelectElement[]
    for (const control of controls) expect(control.disabled).toBe(true)
    expect(within(form).getByRole('combobox', { name: /^策略/ }).querySelectorAll('option')).toHaveLength(1)
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

  it('没有运行卡时给出参数区出口（不印 CLI 命令），且不再请求单卡', async () => {
    const fetchMock = stubBacktestFetch({
      runs: () => jsonResponse({ root: '/tmp', count: 0, latest: null, runs: [] }),
    })
    renderAt('/backtest')
    await waitFor(() => expect(screen.getByText('还没有回测运行卡')).toBeTruthy())
    // QUOT-559：空态引导页内参数区，不再印 ldst backtest run 类命令
    expect(screen.getAllByText(/提交一次回测/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/ldst backtest/)).toBeNull()
    const urls = fetchMock.mock.calls.map(([url]) => String(url))
    expect(urls.some((url) => url.startsWith('/api/backtest/runs/'))).toBe(false)
    // 参数区照常可用：策略清单仍来自后端
    await waitFor(() =>
      expect((screen.getByRole('combobox', { name: /^策略/ }) as HTMLSelectElement).value).toBe('demo_cross'),
    )
  })
})
