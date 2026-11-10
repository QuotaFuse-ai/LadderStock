import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import {
  assertRunId,
  backtestCardPath,
  backtestExportPath,
  backtestJobPath,
  backtestRunsPath,
  backtestStrategiesPath,
  backtestSubmitPath,
  fetchBacktestCard,
  fetchBacktestJob,
  fetchBacktestRuns,
  fetchBacktestStrategies,
  isBacktestEndpointMissing,
  parseJob,
  parseRunAck,
  parseRunCard,
  parseRunsPayload,
  parseStrategiesPayload,
  submitBacktestRun,
  type BacktestRunRequest,
} from '../src/api/backtest'
import { strategiesFixture, succeededJobFixture } from './fixtures/backtestStrategies'
import { runCardFixture, runsPayloadFixture } from './fixtures/backtestRunCard'

/**
 * QUOT-534 / QUOT-535 回测契约测试。
 *
 * 只读面：原值、展示串、calc_id 三条通道各走各的，路径只允许 `bt_` 前缀的 run_id。
 * 触发面：策略清单/成本默认值原样收敛；提交体按用户输入原样透传（不做万分比换算）；
 * 400 字符串 detail 与 422 数组 detail 都要能读成一句中文原因。
 */

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('parseRunCard', () => {
  it('数值原样保留，展示串独立保留，不用原值覆盖展示串', () => {
    const card = parseRunCard(runCardFixture)
    expect(card.run_id).toBe('bt_2e6bab12')
    expect(card.metrics.cumulative_return).toBe(0.6929346601266777)
    expect(card.metrics_display.cumulative_return).toBe('69.30%')
    expect(card.metrics_calc_ids.cumulative_return).toBe('c_5f5eeb0b')
    expect(card.final_equity).toBe(1692934.6601266777)
    expect(card.data.fingerprint).toBe('641:2024-03-14:2026-11-05:ace34e2664625754')
    expect(card.period).toEqual({ start: '2024-03-14', end: '2026-11-05', trading_days: 252 })
    expect(card.calc_ids.gate).toBe('c_73ef8b31')
    expect(card.artifacts).toEqual({
      json: 'bt_2e6bab12.json',
      markdown: 'bt_2e6bab12.md',
      html: 'bt_2e6bab12.html',
    })
  })

  it('校验门、红因、警告、检查项、分段、成本三档都解析出来', () => {
    const card = parseRunCard(runCardFixture)
    expect(card.gate.passed).toBe(false)
    expect(card.gate.red_reasons).toHaveLength(1)
    expect(card.gate.red_reasons[0]).toContain('最小交易次数')
    expect(card.gate.warnings[0]).toContain('不在成本档位')
    expect(card.gate.checks.map((check) => check.status)).toEqual(['fail', 'pass', 'pass'])
    expect(card.gate.segments.map((segment) => segment.label)).toEqual(['样本内', '样本外'])
    expect(card.gate.cost_tiers.map((tier) => tier.commission)).toEqual([0.0002, 0.0005, 0.001])
    expect(card.gate.cost_tiers[2].final_equity).toBe(1646713.2337916035)
  })

  it('缺 run_id / gate.passed / strategy 直接解析失败，不给默认值', () => {
    expect(() => parseRunCard({ ...runCardFixture, run_id: undefined })).toThrowError(/run_id/)
    expect(() => parseRunCard({ ...runCardFixture, gate: undefined })).toThrowError(/gate/)
    expect(() => parseRunCard({ ...runCardFixture, strategy: undefined })).toThrowError(/strategy/)
    expect(() => parseRunCard(null)).toThrowError(/不是对象/)
  })

  it('可选字段缺失时保持 null / 空数组，不补 0', () => {
    const minimal = {
      run_id: 'bt_deadbeef',
      strategy: 'breakout',
      gate: { passed: true },
    }
    const card = parseRunCard(minimal)
    expect(card.final_equity).toBeNull()
    expect(card.data.fingerprint).toBeNull()
    expect(card.period.start).toBeNull()
    expect(card.equity_curve).toEqual([])
    expect(card.gate.checks).toEqual([])
    expect(card.gate.cost_tiers).toEqual([])
    expect(card.metrics).toEqual({})
  })
})

describe('parseRunsPayload', () => {
  it('count / latest / runs 原样收敛', () => {
    const payload = parseRunsPayload(runsPayloadFixture)
    expect(payload.count).toBe(2)
    expect(payload.latest).toBe('bt_2e6bab12')
    expect(payload.runs.map((run) => run.run_id)).toEqual(['bt_2e6bab12', 'bt_7dc1b51c'])
    expect(payload.runs[0].gate_passed).toBe(false)
    expect(payload.runs[1].gate_passed).toBe(true)
  })

  it('缺 runs 时返回空数组，count 退回数组长度', () => {
    const payload = parseRunsPayload({ root: '/tmp' })
    expect(payload.runs).toEqual([])
    expect(payload.count).toBe(0)
    expect(payload.latest).toBeNull()
  })
})

describe('parseStrategiesPayload', () => {
  it('策略清单、参数 schema、成本默认值与三档预设原样收敛', () => {
    const payload = parseStrategiesPayload(strategiesFixture)
    expect(payload.symbol_default).toBe('600000')
    expect(payload.strategies.map((strategy) => strategy.name)).toEqual([
      'demo_cross',
      'demo_break',
      'demo_threshold',
    ])
    expect(payload.strategies[0].params[0]).toEqual({
      key: 'short_window',
      label: '短均线窗口',
      type: 'int',
      default: 5,
      min: 1,
      max: 250,
    })
    expect(payload.config_defaults.commission_rate_wan).toBe(0.9)
    expect(payload.config_defaults.commission_floor_yuan).toBe(0.01)
    expect(payload.config_defaults.transfer_fee_wan).toBe(0.1)
    expect(payload.config_defaults.stamp_duty_wan).toBe(5)
    expect(payload.config_defaults.slippage).toBe(0.001)
    expect(payload.config_defaults.cost_tiers).toEqual([0.0002, 0.0005, 0.001])
    expect(payload.config_defaults.presets.map((preset) => preset.key)).toEqual([
      'wan09_free5_001',
      'wan085_free5_1',
      'wan065_min5',
    ])
  })

  it('缺 strategies / config_defaults 直接解析失败，不编清单', () => {
    expect(() => parseStrategiesPayload({})).toThrowError(/strategies/)
    expect(() => parseStrategiesPayload({ strategies: [] })).toThrowError(/config_defaults/)
    expect(() => parseStrategiesPayload(null)).toThrowError(/不是对象/)
  })
})

describe('parseJob / parseRunAck', () => {
  it('任务字段原样收敛', () => {
    const job = parseJob(succeededJobFixture)
    expect(job).toMatchObject({
      status: 'ok',
      run_id: 'bt_9f00c0de',
      gate_passed: true,
      exit_code: 0,
      error: null,
    })
    expect(parseRunAck({ job_id: succeededJobFixture.job_id, status: 'queued' })).toEqual({
      job_id: succeededJobFixture.job_id,
      status: 'queued',
    })
  })

  it('缺 job_id 视为解析失败', () => {
    expect(() => parseJob({ status: 'ok' })).toThrowError(/job_id/)
    expect(() => parseRunAck({ status: 'queued' })).toThrowError(/job_id/)
  })
})

describe('路径构造', () => {
  it('列表 limit 收敛到 1–200', () => {
    expect(backtestRunsPath()).toBe('/api/backtest/runs?limit=50')
    expect(backtestRunsPath(0)).toBe('/api/backtest/runs?limit=1')
    expect(backtestRunsPath(999)).toBe('/api/backtest/runs?limit=200')
    expect(backtestRunsPath(12.9)).toBe('/api/backtest/runs?limit=12')
  })

  it('run_id 只接受 bt_ 前缀', () => {
    expect(backtestCardPath('bt_2e6bab12')).toBe('/api/backtest/runs/bt_2e6bab12')
    expect(assertRunId('bt_ab_12')).toBe('bt_ab_12')
    for (const bad of ['', 'bt_', '../../etc', 'bt_2E6BAB12', ' c_a9ea1bd5']) {
      expect(() => assertRunId(bad)).toThrowError(ApiError)
    }
  })

  it('导出链接直达后端 attachment 端点', () => {
    expect(backtestExportPath('bt_2e6bab12', 'json')).toBe(
      '/api/backtest/runs/bt_2e6bab12/export.json',
    )
    expect(backtestExportPath('bt_2e6bab12', 'md')).toBe('/api/backtest/runs/bt_2e6bab12/export.md')
  })

  it('触发面路径固定，job_id 做白名单校验', () => {
    expect(backtestStrategiesPath()).toBe('/api/backtest/strategies')
    expect(backtestSubmitPath()).toBe('/api/backtest/run')
    expect(backtestJobPath(succeededJobFixture.job_id)).toBe(
      `/api/backtest/jobs/${succeededJobFixture.job_id}`,
    )
    for (const bad of ['', '../jobs', 'not-a-job', 'a1b2']) {
      expect(() => backtestJobPath(bad)).toThrowError(ApiError)
    }
  })
})

describe('传输', () => {
  it('GET 运行卡走 no-store，且不附带任何写请求', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      return new Response(JSON.stringify(runCardFixture))
    })
    vi.stubGlobal('fetch', fetchMock)

    const card = await fetchBacktestCard('bt_2e6bab12')
    expect(card.run_id).toBe('bt_2e6bab12')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, init] = fetchMock.mock.calls[0]
    expect(path).toBe('/api/backtest/runs/bt_2e6bab12')
    expect(init?.method).toBe('GET')
    expect(init?.cache).toBe('no-store')
  })

  it('列表请求带 limit', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      return new Response(JSON.stringify(runsPayloadFixture))
    })
    vi.stubGlobal('fetch', fetchMock)

    const payload = await fetchBacktestRuns(undefined, 5)
    expect(payload.count).toBe(2)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/backtest/runs?limit=5')
  })

  it('404 识别为「接口未接入」，不是解析失败', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Not Found"}', { status: 404 })))
    const error = await fetchBacktestCard('bt_2e6bab12').catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
    expect(isBacktestEndpointMissing(error)).toBe(true)
  })

  it('策略清单按 GET 取回，且走 no-store', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      return new Response(JSON.stringify(strategiesFixture))
    })
    vi.stubGlobal('fetch', fetchMock)
    const payload = await fetchBacktestStrategies()
    expect(payload.strategies).toHaveLength(3)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/backtest/strategies')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('GET')
    expect(fetchMock.mock.calls[0][1]?.cache).toBe('no-store')
  })

  it('提交只 POST 一次，请求体原样透传并带来源证据头', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      return new Response(JSON.stringify({ job_id: succeededJobFixture.job_id, status: 'queued' }), {
        status: 202,
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    const body: BacktestRunRequest = {
      strategy: 'demo_cross',
      strategy_params: { short_window: 5, long_window: 20 },
      symbol: '600000',
      start: '2024-03-14',
      end: '2026-11-05',
      commission_rate_wan: 0.65,
      commission_floor_yuan: 5,
      transfer_fee_wan: 0.1,
      stamp_duty_wan: 5,
      slippage: 0.001,
    }
    const ack = await submitBacktestRun(body)
    expect(ack).toEqual({ job_id: succeededJobFixture.job_id, status: 'queued' })
    const [path, init] = fetchMock.mock.calls[0]
    expect(path).toBe('/api/backtest/run')
    expect(init?.method).toBe('POST')
    expect((init?.headers as Record<string, string>)['X-LDST-Client']).toBe('1')
    expect(JSON.parse(String(init?.body))).toEqual(body)
  })

  it('400 字符串 detail 直接进 ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: '未知策略（内置：ma_cross, momentum, breakout）' }), {
            status: 400,
          }),
      ),
    )
    const error = await submitBacktestRun({
      strategy: 'nope',
      strategy_params: {},
      symbol: '600000',
      start: '2024-03-14',
      end: '2026-11-05',
      commission_rate_wan: 0.9,
      commission_floor_yuan: 0.01,
      transfer_fee_wan: 0.1,
      stamp_duty_wan: 5,
      slippage: 0.001,
    }).catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(400)
    expect((error as ApiError).detail).toContain('未知策略')
  })

  it('422 数组 detail 收敛成「字段：原因」', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              detail: [
                { loc: ['body', 'start'], msg: 'start 必须是 YYYY-MM-DD', type: 'value_error' },
                { loc: ['body', 'symbol'], msg: 'field required', type: 'value_error' },
              ],
            }),
            { status: 422 },
          ),
      ),
    )
    const error = await fetchBacktestJob(succeededJobFixture.job_id).catch((cause: unknown) => cause)
    expect((error as ApiError).detail).toBe('start：start 必须是 YYYY-MM-DD；symbol：field required')
    expect((error as ApiError).message).toContain('HTTP 422')
  })

  it('任务轮询解析 status / run_id / exit_code', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(succeededJobFixture))))
    const job = await fetchBacktestJob(succeededJobFixture.job_id)
    expect(job.status).toBe('ok')
    expect(job.run_id).toBe('bt_9f00c0de')
    expect(job.exit_code).toBe(0)
  })

  it('非 JSON 响应按解析失败处理', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>oops</html>')))
    const error = await fetchBacktestCard('bt_2e6bab12').catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).kind).toBe('parse')
    expect(isBacktestEndpointMissing(error)).toBe(false)
  })
})
