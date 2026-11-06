// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MASK, setPrivacyMode } from '../src/lib/privacy'
import { XrayTab } from '../src/screens/my-stocks/XrayTab'
import { THRESHOLDS_KEY, findBreaches, parseThresholds } from '../src/screens/my-stocks/xrayView'

/**
 * QUOT-437 X 光子页（前端侧）：
 * - 权重 / 集中度 / 行业 / 相关性全部照后端载荷展示，缺失是 `—` 不是 0；
 * - 缺现价的持仓不进分母，单独列进缺口；
 * - 行业只用标注，没标的归「未标注」；
 * - 提醒线存 `ldst.portfolio.xray.thresholds`，超线横幅只说哪几项超线；
 * - 接口 404 时进错误态并可重试，不伪造一份体检。
 */

vi.mock('../src/charts/EChart', () => ({
  EChart: ({ ariaLabel }: { ariaLabel?: string }) => <div data-testid="echart">{ariaLabel ?? 'chart'}</div>,
}))

/** 后端 wire 形状：相关系数嵌在 `metrics.corr` 里 */
const CORR = {
  available: true,
  reason: null,
  max_pair: { pair: ['600000', '000001'], corr: 0.87, samples: 42 },
  avg_abs_corr: 0.87,
  pairs: [{ pair: ['600000', '000001'], corr: 0.87, samples: 42 }],
  pairs_count: 1,
  min_days: 20,
  window: { first: '2026-09-01', last: '2026-11-04', days: 42 },
}

const XRAY = {
  local_only: true,
  store: 'local_sqlite',
  price_lookup: { dataset: 'daily_k', date: '2026-11-04', reason: null },
  as_of: '2026-11-04',
  total_value: 2000,
  items: [
    { symbol: '600000', name: '浦发银行', market_value: 1200, weight: 0.6 },
    { symbol: '000001', name: '平安银行', market_value: 800, weight: 0.4 },
    { symbol: '300750', name: '宁德时代', market_value: null, weight: null },
  ],
  top1: 0.6,
  top3: 1,
  top5: 1,
  industry_buckets: [
    { industry: '银行', labeled: true, market_value: 1200, weight: 0.6, symbols: ['600000'] },
    { industry: '未标注', labeled: false, market_value: 800, weight: 0.4, symbols: ['000001'] },
  ],
  industry_labeled_count: 1,
  industry_unlabeled_count: 1,
  metrics: { corr: CORR },
  gaps: [
    {
      symbol: '300750',
      name: '宁德时代',
      affects: 'weight',
      reason: '本机 daily_k 缓存里没有这只的收盘价（停牌或未同步），没算进权重分母',
    },
  ],
  notes: ['权重按现价市值算；缺价的持仓不进分母，单独列在缺口里'],
}

function stub(handler: (url: string, init?: RequestInit) => unknown) {
  const calls: Array<{ url: string; body?: unknown }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      const result = handler(url, init)
      return new Response(JSON.stringify(result), { status: 200 })
    }),
  )
  return calls
}

beforeEach(() => {
  window.localStorage.clear()
  setPrivacyMode(false)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('X 光子页', () => {
  it('展示后端给的权重、行业与相关性；缺价的进缺口而不是 0', async () => {
    stub(() => XRAY)
    render(<XrayTab refreshKey={0} />)

    expect((await screen.findAllByText('60.0%')).length).toBeGreaterThan(0)
    // 缺价的那只：权重列 `—`，且原因原样展示
    expect(await screen.findByText(/没算进权重分母/)).toBeTruthy()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    // 行业只用标注：未标注单独一桶，不并进别的行业
    expect(screen.getByText('未标注')).toBeTruthy()
    expect(screen.getByText('银行')).toBeTruthy()
    // 相关性：最强的一对，数字来自后端
    expect(screen.getByText('0.87')).toBeTruthy()
    expect(screen.getAllByTestId('echart').length).toBeGreaterThan(0)
  })

  it('超过提醒线的项只列「哪几项」，并写进本机存储', async () => {
    stub(() => XRAY)
    render(<XrayTab refreshKey={0} />)
    await screen.findAllByText('60.0%')

    fireEvent.click(screen.getByRole('button', { name: /提醒线/ }))
    const single = screen.getByLabelText('单只占比提醒线')
    fireEvent.change(single, { target: { value: '50' } })
    fireEvent.blur(single)

    await waitFor(() =>
      expect(window.localStorage.getItem(THRESHOLDS_KEY)).toContain('"single":0.5'),
    )
    expect(await screen.findByText(/有 \d+ 项超过你设的提醒线/)).toBeTruthy()
    expect(screen.getByText(/浦发银行 60\.0%，超过你设的 50\.0%/)).toBeTruthy()
    // 相关性 0.87 默认线 0.8 也超：横幅只报数字，不报建议
    expect(screen.getByText(/相关系数 0\.87/)).toBeTruthy()
    const banner = screen.getByRole('alert')
    expect(/建议|应该/.test(banner.textContent ?? false)).toBe(false)
  })

  it('留空 = 这一项不提醒；非法值不写库', async () => {
    stub(() => XRAY)
    render(<XrayTab refreshKey={0} />)
    await screen.findAllByText('60.0%')
    fireEvent.click(screen.getByRole('button', { name: /提醒线/ }))

    const single = screen.getByLabelText('单只占比提醒线')
    fireEvent.change(single, { target: { value: '' } })
    fireEvent.blur(single)
    await waitFor(() => expect(window.localStorage.getItem(THRESHOLDS_KEY)).toContain('"single":null'))

    fireEvent.change(single, { target: { value: '150' } })
    fireEvent.blur(single)
    expect(await screen.findByText('占比要填 0 到 100 之间的数')).toBeTruthy()
    expect(window.localStorage.getItem(THRESHOLDS_KEY)).toContain('"single":null')
  })

  it('隐私模式遮市值，占比与相关系数照旧（推不出本金的不遮）', async () => {
    stub(() => XRAY)
    setPrivacyMode(true)
    render(<XrayTab refreshKey={0} />)
    await screen.findAllByText('60.0%')

    expect(screen.getAllByText(MASK).length).toBeGreaterThan(0)
    expect(screen.getAllByText('60.0%').length).toBeGreaterThan(0)
    expect(screen.getByText('0.87')).toBeTruthy()
  })

  it('接口没接（404）时不伪造体检', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"no such endpoint"}', { status: 404 })),
    )
    render(<XrayTab refreshKey={0} />)
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryAllByText('60.0%')).toHaveLength(0)
  })
})

/** 前端解析后的形状（纯函数用例直接吃它，不必再走一遍 fetch） */
const VIEW = { ...XRAY, correlation: CORR, metrics: undefined }

describe('xrayView 纯函数', () => {
  it('提醒线：认不出的值当没设，非法 JSON 回默认', () => {
    // 没写 / 写空的项按「不提醒」，不拿默认值盖掉用户留的空
    expect(parseThresholds('{"single":0.5,"top3":null,"corr":2}')).toEqual({
      single: 0.5,
      top3: null,
      industry: null,
      corr: null,
    })
    expect(parseThresholds('not json')).toEqual({ single: 0.3, top3: 0.6, industry: 0.4, corr: 0.8 })
    expect(parseThresholds(null)).toEqual({ single: 0.3, top3: 0.6, industry: 0.4, corr: 0.8 })
  })

  it('只有一只持仓时不比「单只占比」；「未标注」不进行业比较', () => {
    const payload = {
      ...VIEW,
      items: [{ symbol: '600000', name: '浦发银行', market_value: 1200, weight: 1 }],
      top1: 1,
      industry_buckets: [
        { industry: '未标注', labeled: false, market_value: 1200, weight: 1, symbols: ['600000'] },
      ],
      correlation: { ...CORR, max_pair: null, available: false },
    }
    const thresholds = { single: 0.3, top3: 0.6, industry: 0.4, corr: 0.8 }
    expect(findBreaches(payload as never, thresholds).map((b) => b.key)).not.toContain('single')
    expect(findBreaches(payload as never, thresholds).map((b) => b.key)).not.toContain('industry')
  })

  it('持仓不足 4 只时不比「前三合计」（100% 是数学结果不是集中度）', () => {
    const breaches = findBreaches(VIEW as never, { single: null, top3: 0.6, industry: null, corr: null })
    expect(breaches).toHaveLength(0)
  })
})
