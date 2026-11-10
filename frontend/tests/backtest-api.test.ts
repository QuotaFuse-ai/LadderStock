import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import {
  assertRunId,
  backtestCardPath,
  backtestExportPath,
  backtestRunsPath,
  fetchBacktestCard,
  fetchBacktestRuns,
  isBacktestEndpointMissing,
  parseRunCard,
  parseRunsPayload,
} from '../src/api/backtest'
import { runCardFixture, runsPayloadFixture } from './fixtures/backtestRunCard'

/**
 * QUOT-534 运行卡契约测试。
 *
 * 解析层只收敛类型，不改数值：原值、展示串、calc_id 三条通道各走各的。
 * 路径构造只允许 `bt_` 前缀的 run_id；导出链接必须直达后端 attachment 端点。
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

  it('非 JSON 响应按解析失败处理', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>oops</html>')))
    const error = await fetchBacktestCard('bt_2e6bab12').catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).kind).toBe('parse')
    expect(isBacktestEndpointMissing(error)).toBe(false)
  })
})
