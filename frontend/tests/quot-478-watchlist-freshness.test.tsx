// @vitest-environment jsdom
/**
 * QUOT-478：`GET /api/watchlist` 顶层 `freshness` 的解析与徽标。
 *
 * 钉住两件事：
 * 1. **后端给了就真出档位**：三档 key 原样透传到 `WatchlistPayload.freshness`，
 *    首页自选卡的徽标从「未知」变成真实档位（`archive` / `cached` / `realtime`），
 *    而不是继续把解析层硬编码成 `null`；
 * 2. **后端不给 / 给了未登记的 key 就诚实兜底**：`null` 或表外 key 一律不回落到
 *    某个默认档，徽标仍是「未知」——不编造来源（R-06）。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { parseWatchlist, type WatchlistPayload } from '../src/api/radar'
import { HomeWatchlistCard } from '../src/screens/home/HomeWatchlistCard'
import type { BlockView } from '../src/screens/home/useHomeSource'

afterEach(cleanup)

const STAMP = '2026-11-06T16:14:48'

const RAW = {
  local_only: true,
  store: 'local_sqlite',
  available: true,
  reason: null,
  watch_count: 2,
  data_source_status: 'ok',
  price_source: 'spot_quote',
  price_as_of: STAMP,
  source_detail: 'tickhub/tentcent_quote_batch',
  items: [
    {
      code: '000530',
      added_at: '2026-11-01T09:30:00+08:00',
      available: true,
      name: '自选000530',
      market: 'sz',
      latest_price: 12.34,
      change: 0.2,
      change_pct: 1.62,
      prev_close: 12.14,
      price_as_of: STAMP,
      price_status: 'ok',
      price_reason: null,
      pe_ttm: null,
      pb: null,
      turnover_pct: null,
      metrics_reason: null,
    },
  ],
}

function viewOf(payload: WatchlistPayload): BlockView<WatchlistPayload> {
  return { state: 'normal', data: payload, reason: null }
}

function cardOf(raw: Record<string, unknown>) {
  return render(
    <MemoryRouter>
      <HomeWatchlistCard view={viewOf(parseWatchlist(raw))} />
    </MemoryRouter>,
  )
}

describe('parseWatchlist 的 freshness', () => {
  it('后端给三档 key 时原样透传', () => {
    for (const key of ['realtime', 'cached', 'archive']) {
      const payload = parseWatchlist({ ...RAW, freshness: key })
      expect(payload.freshness).toBe(key)
    }
  })

  it('后端不给（缺键 / null）时为 null，回落到「未知」', () => {
    expect(parseWatchlist({ ...RAW }).freshness).toBeNull()
    expect(parseWatchlist({ ...RAW, freshness: null }).freshness).toBeNull()
  })

  it('未登记的 key 不猜档位：一律 null', () => {
    expect(parseWatchlist({ ...RAW, freshness: 'daily' }).freshness).toBeNull()
    expect(parseWatchlist({ ...RAW, freshness: '' }).freshness).toBeNull()
    expect(parseWatchlist({ ...RAW, freshness: 7 }).freshness).toBeNull()
  })

  it('信封键仍然一个都不出（DEC-052 平面契约没被这个字段带坏）', () => {
    const parsed = parseWatchlist({ ...RAW, freshness: 'archive' })
    expect(parsed.source_status).toBeNull()
    expect(parsed.fetched_at).toBeNull()
  })
})

describe('首页自选卡的徽标', () => {
  it('后端给 archive 时徽标出「存档」，不再是「未知」', () => {
    cardOf({ ...RAW, freshness: 'archive' })
    expect(screen.getByText('存档')).toBeTruthy()
    expect(screen.queryByText('未知')).not.toBeTruthy()
  })

  it('后端给 cached 时徽标出「缓存」', () => {
    cardOf({ ...RAW, freshness: 'cached' })
    expect(screen.getByText('缓存')).toBeTruthy()
  })

  it('后端不给时徽标仍是「未知」（诚实兜底没有丢）', () => {
    cardOf({ ...RAW, freshness: null })
    expect(screen.getByText('未知')).toBeTruthy()
  })

  it('数据完整档不因 freshness 到位而改变', () => {
    cardOf({ ...RAW, freshness: 'archive' })
    expect(screen.getByText('数据完整')).toBeTruthy()
  })
})
