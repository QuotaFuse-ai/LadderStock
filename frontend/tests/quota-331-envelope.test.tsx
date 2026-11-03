// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { parseIndustryList } from '../src/api/industry'
import { parseLimitPool } from '../src/api/limitPool'
import { FreshnessBadge } from '../src/components/ui'
import LimitUpScreen from '../src/screens/limitup/LimitUpScreen'

/**
 * QUOT-331：`LadderEnvelope` 外壳的 `freshness` / `source_status` / `data_as_of` / `fetched_at`
 * 必须透传进解析层，`/limit-up` 的新度徽标才不是「未知」。
 *
 * 下面两份响应照 `docs/specs/SPEC-LIMIT-POOL.md` §6.1 / §6.2 的契约示例逐键构造
 * （有数据态 / 当日缓存没同步态），键序与取值都跟 `web/server.py:_envelope()` 出站一致，
 * 只是把 §6.1 里省略的 46 行收成 2 行。用例钉的是同一件事的两面：后端给了就照实渲染，
 * 没给就出「未知」——解析层不补 `cached`、不猜档位（§5.5 R-06）。
 */

const WITH_ROWS = {
  domain: 'sentiment',
  topic: 'limit_pool',
  event_id: null,
  data_as_of: '2026-10-30',
  source: 'ladderstock',
  source_detail: 'limit_pool: duckdb（2 条）',
  fetched_at: '2026-10-31T13:33:30.245459Z',
  freshness: 'cached',
  confidence: 'medium',
  source_status: 'ok',
  payload: {
    date: '2026-10-30',
    available: true,
    reason: null,
    source_detail: 'limit_pool: duckdb（2 条）',
    count: 2,
    rows: [
      {
        code: '600000',
        name: '浦发银行',
        streak: 2,
        first_seal_time: '093015',
        last_seal_time: '142500',
        reason: '银行',
        trade_date: '2026-10-30',
      },
      {
        code: '600519',
        name: '贵州茅台',
        streak: 1,
        first_seal_time: null,
        last_seal_time: null,
        reason: null,
        trade_date: null,
      },
    ],
    data_source_status: 'ok',
  },
}

const UNAVAILABLE = {
  domain: 'sentiment',
  topic: 'limit_pool',
  event_id: null,
  data_as_of: null,
  source: 'ladderstock',
  source_detail: 'limit_pool: none（0 条）',
  fetched_at: '2026-10-31T13:33:30.352987Z',
  freshness: 'cached',
  confidence: 'low',
  source_status: 'partial',
  payload: {
    date: '2026-10-28',
    available: false,
    reason: '涨停池为空，当日尚未同步（先运行 ldst sync）',
    source_detail: 'limit_pool: none（0 条）',
    count: 0,
    rows: [],
    data_source_status: 'missing',
  },
}

/** 外壳缺 `freshness` / `source_status` 那一档：后端没给，屏侧就只能出「未知」。 */
function withoutShellKeys(...keys: string[]): Record<string, unknown> {
  const copy: Record<string, unknown> = { ...WITH_ROWS }
  for (const key of keys) delete copy[key]
  return copy
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** 徽标外层 `<span>`：Badge 把 children 包在 `.ldst-num` 里，所以从文字节点往上跳一层。 */
function badgeOf(name: string): HTMLElement {
  const node = screen.getByText(name).parentElement
  if (!node) throw new Error(`没有找到「${name}」这枚徽标`)
  return node
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/limit-up" element={<LimitUpScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

function stubEndpoint(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, status: 200, json: async () => body })),
  )
}

/** 页头副标题（`components/PageShell.tsx:30` 的 `<p>`）：来源与数据时点都在这一行。 */
function subtitleText(): string {
  const p = screen
    .getByRole('heading', { name: '涨停样本', level: 1 })
    .parentElement?.querySelector('p')
  if (!p) throw new Error('页头没有副标题')
  return p.textContent ?? ''
}

describe('QUOT-331 · 外壳溯源字段透传（limitPool 只取 root.payload 的那一段）', () => {
  it('后端给了就照实带上：freshness / source_status / data_as_of / fetched_at', () => {
    const payload = parseLimitPool(WITH_ROWS)

    expect(payload.freshness).toBe(WITH_ROWS.freshness)
    expect(payload.source_status).toBe(WITH_ROWS.source_status)
    expect(payload.data_as_of).toBe(WITH_ROWS.data_as_of)
    expect(payload.fetched_at).toBe(WITH_ROWS.fetched_at)
    // 载荷级的细档不动：屏侧完整度徽标仍读它
    expect(payload.data_source_status).toBe('ok')
    expect(payload.rows).toHaveLength(2)
  })

  it('无数据态信封：data_as_of 是 null，不拿请求日期顶替', () => {
    const payload = parseLimitPool(UNAVAILABLE)

    expect(payload.freshness).toBe('cached')
    expect(payload.data_as_of).toBeNull()
    expect(payload.source_status).toBe('partial')
    expect(payload.data_source_status).toBe('missing')
  })

  it('外壳缺 freshness / source_status → null，不补 cached、不猜档位', () => {
    const payload = parseLimitPool(withoutShellKeys('freshness', 'source_status'))

    expect(payload.freshness).toBeNull()
    expect(payload.source_status).toBeNull()
    expect(payload.data_as_of).toBe('2026-10-30')
  })

  it('未登记的 freshness key 原样透传（同 radar 的 freshness: string），由徽标退「未知」', () => {
    expect(parseLimitPool({ ...WITH_ROWS, freshness: 'daily' }).freshness).toBe('daily')
  })

  it('平面响应（没有外壳）→ 四键全 null，载荷本身照常解析', () => {
    const payload = parseLimitPool(WITH_ROWS.payload)

    expect(payload.freshness).toBeNull()
    expect(payload.source_status).toBeNull()
    expect(payload.data_as_of).toBeNull()
    expect(payload.fetched_at).toBeNull()
    expect(payload.count).toBe(2)
  })
})

describe('QUOT-331 · 同一族：industry 解析层也读得到外壳', () => {
  const listBody = {
    available: true,
    count: 1,
    industries: [{ name: '银行', available: true, data_source_status: 'ok' }],
  }

  it('带外壳 → freshness / source_status / fetched_at 不再停在外壳上', () => {
    const payload = parseIndustryList({
      domain: 'sentiment',
      topic: 'industry_metrics',
      event_id: null,
      data_as_of: '2026-10-30',
      payload: listBody,
      source: 'ladderstock',
      source_detail: 'industry: duckdb',
      fetched_at: '2026-10-31T13:33:30.245459Z',
      freshness: 'cached',
      confidence: 'medium',
      source_status: 'partial',
    })

    expect(payload.freshness).toBe('cached')
    expect(payload.source_status).toBe('partial')
    expect(payload.fetched_at).toBe('2026-10-31T13:33:30.245459Z')
    // 载荷里没有的同名键，回落到外壳那一份（`web/industry.py` 把两者写的是同一个值）
    expect(payload.data_as_of).toBe('2026-10-30')
    expect(payload.source_detail).toBe('industry: duckdb')
    expect(payload.industries).toHaveLength(1)
  })

  it('平面响应 → 三键 null，不编造', () => {
    const payload = parseIndustryList(listBody)

    expect(payload.freshness).toBeNull()
    expect(payload.source_status).toBeNull()
    expect(payload.fetched_at).toBeNull()
    expect(payload.data_as_of).toBeNull()
  })
})

describe('QUOT-331 · /limit-up 徽标真的用上 freshness', () => {
  it('真实通道（契约响应）：出「缓存」+「数据完整」，副标题带数据时间与取数时刻', async () => {
    stubEndpoint(WITH_ROWS)
    renderAt('/limit-up?date=2026-10-30')

    expect(await screen.findByText('缓存')).toBeTruthy()
    expect(screen.getByText('数据完整')).toBeTruthy()
    expect(badgeOf('缓存').getAttribute('title')).toContain('2026-10-30')
    expect(subtitleText()).toContain('数据时间 2026-10-30')
    expect(subtitleText()).toContain('取数')
    expect(screen.queryByText('未知')).toBeNull()
  })

  it('外壳缺 freshness → 徽标出「未知」并写明后端没给，不回落成「缓存」', async () => {
    stubEndpoint(withoutShellKeys('freshness'))
    renderAt('/limit-up?date=2026-10-30')

    expect(await screen.findByText('未知')).toBeTruthy()
    expect(badgeOf('未知').getAttribute('title')).toContain('后端未提供 freshness')
    expect(screen.queryByText('缓存')).toBeNull()
  })

  it('?state=partial 缺数态：两枚徽标都有文字标签，不再出现「未知」（§5.2 / §5.3）', async () => {
    renderAt('/limit-up?demo=1&state=partial')

    expect(await screen.findByText('部分涨停池数据缺失')).toBeTruthy()
    expect(badgeOf('缓存').getAttribute('class')).toContain('bg-warn-soft')
    expect(badgeOf('部分缺失').getAttribute('class')).toContain('bg-danger-soft')
    expect(screen.queryByText('未知')).toBeNull()
    expect(screen.queryByText('来源状态未知')).toBeNull()
  })

  it('无数据态：完整度用载荷级的「未取到」，不被外壳的 partial 盖过去', async () => {
    stubEndpoint(UNAVAILABLE)
    renderAt('/limit-up?date=2026-10-28')

    expect(await screen.findByText('当日涨停池未同步')).toBeTruthy()
    expect(screen.getByText('未取到')).toBeTruthy()
    expect(screen.getByText('缓存')).toBeTruthy()
  })

  it('未登记的 freshness 直传给徽标也不上屏英文 key（§5.5 R-06）', () => {
    render(<FreshnessBadge freshness="daily" asOf="2026-10-30" />)

    expect(screen.getByText('未知')).toBeTruthy()
    expect(screen.queryByText('daily')).toBeNull()
    expect(badgeOf('未知').getAttribute('title')).toContain('未登记的 freshness：daily')
  })
})
