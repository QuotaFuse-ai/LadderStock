import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import {
  describeFinanceCofferError,
  fetchStructuredMetrics,
  parseStructuredMetrics,
  resolveFinanceCofferBase,
} from '../src/api/financecoffer'

const SAMPLE = {
  basis: 'reference',
  default_basis: 'snapshot',
  available_bases: ['snapshot', 'reference'],
  pe: 10.0,
  pe_source: 'deducted_annualized',
  pe_reason: null,
  pe_cross: 15.25,
  pb: 1.2,
  pb_reason: null,
  roe: null,
  roe_reason: 'equity_unavailable',
  price_date: '2025-06-30',
  price_close: '10.5000',
  report_date: '2026-09-30',
  quarter_basis: 'single_quarter_deducted_x4',
}

function stubFetch(payload: unknown, status = 200): Array<{ url: string; init: RequestInit }> {
  const calls: Array<{ url: string; init: RequestInit }> = []
  vi.stubGlobal('fetch', (input: string, init: RequestInit = {}) => {
    calls.push({ url: String(input), init })
    return Promise.resolve(
      new Response(JSON.stringify(payload), {
        status,
        headers: { 'content-type': 'application/json' },
      }),
    )
  })
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FinanceCoffer 读侧客户端', () => {
  it('解析响应：回显 basis / default_basis / available_bases，null 与原因码原样保留', () => {
    const metrics = parseStructuredMetrics(
      {
        ...SAMPLE,
        pe: '10.0',
        pb: '1.2',
        pe_cross: '15.25',
      },
      '600158',
    )

    expect(metrics.symbol).toBe('600158')
    expect(metrics.basis).toBe('reference')
    expect(metrics.default_basis).toBe('snapshot')
    expect(metrics.available_bases).toEqual(['snapshot', 'reference'])
    expect(metrics.pe).toBe(10)
    expect(metrics.pb).toBe(1.2)
    expect(metrics.roe).toBeNull()
    expect(metrics.roe_reason).toBe('equity_unavailable')
    expect(metrics.pe_cross).toBe(15.25)
    expect(metrics.quarter_basis).toBe('single_quarter_deducted_x4')
  })

  it('请求 URL 带 valuation_basis，且基础地址可覆盖、尾斜杠归一', async () => {
    const calls = stubFetch(SAMPLE)

    const metrics = await fetchStructuredMetrics('600158', 'reference', {
      baseUrl: 'http://127.0.0.1:8001/',
    })

    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe(
      'http://127.0.0.1:8001/v1/structured/600158?valuation_basis=reference',
    )
    expect(calls[0].init.method).toBe('GET')
    expect(calls[0].init.headers).toEqual({ accept: 'application/json' })
    expect(metrics.basis).toBe('reference')
  })

  it('标的代码会做 URL 编码', async () => {
    const calls = stubFetch(SAMPLE)
    await fetchStructuredMetrics('600158 SH', 'snapshot', { baseUrl: '' })
    expect(calls[0].url).toBe('/fc-api/v1/structured/600158%20SH?valuation_basis=snapshot')
  })

  it('HTTP 错误保留状态码与 detail', async () => {
    stubFetch({ detail: 'symbol not found' }, 404)

    await expect(
      fetchStructuredMetrics('600158', 'snapshot', { baseUrl: 'http://fc' }),
    ).rejects.toMatchObject({
      name: 'ApiError',
      kind: 'http',
      status: 404,
      detail: 'symbol not found',
    })
  })

  it('缺 basis / available_bases 是契约错误，不猜默认值', async () => {
    stubFetch({ ...SAMPLE, basis: undefined })
    await expect(
      fetchStructuredMetrics('600158', 'snapshot', { baseUrl: 'http://fc' }),
    ).rejects.toMatchObject({ kind: 'parse' })

    stubFetch({ ...SAMPLE, available_bases: undefined })
    await expect(
      fetchStructuredMetrics('600158', 'snapshot', { baseUrl: 'http://fc' }),
    ).rejects.toMatchObject({ kind: 'parse' })
  })

  it('请求未构建的口径：basis_not_built 保留，数值仍是 null', () => {
    const metrics = parseStructuredMetrics(
      {
        ...SAMPLE,
        basis: 'reference',
        available_bases: ['snapshot'],
        pe: null,
        pe_source: null,
        pe_reason: 'basis_not_built',
        pe_cross: null,
        pb: null,
        pb_reason: 'basis_not_built',
        roe: null,
        roe_reason: 'basis_not_built',
      },
      '600158',
    )

    expect(metrics.available_bases).toEqual(['snapshot'])
    expect(metrics.pe).toBeNull()
    expect(metrics.pe_reason).toBe('basis_not_built')
    expect(metrics.pb).toBeNull()
    expect(metrics.roe).toBeNull()
  })

  it('网络不可达归为 not-connected，不返回空数据', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('fetch failed')))

    const error = await fetchStructuredMetrics('600158', 'snapshot', {
      baseUrl: 'http://fc',
    }).catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).kind).toBe('not-connected')
    expect(describeFinanceCofferError(error).title).toBe('FinanceCoffer 未接入')
  })

  it('基础地址默认同源 /fc-api', () => {
    expect(resolveFinanceCofferBase()).toBe('/fc-api')
    expect(resolveFinanceCofferBase('http://127.0.0.1:8001///')).toBe('http://127.0.0.1:8001')
  })
})
