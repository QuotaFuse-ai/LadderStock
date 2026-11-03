/**
 * 「盯盘 · 竞价与盘中」演示数据（`?demo=1` 显式开启，页面带「演示态」水印）。
 *
 * 形状与 `docs/specs/SPEC-INTRADAY-READ` §4 / §5 逐键对齐，用途只有两个：
 * 复现五种界面状态与六种时点状态、截图取证。
 *
 * **绝不在真实通道里回落到这份数据。** 时点行的时刻随请求日期回显（不是写死的表），
 * 这样 `?date=` 深链在演示态下也能复现同一视图；`variant=mixed` 专门给「六个时点状态
 * 各占一格」的走查（`ok / degraded / already / skipped / missing / failed`）。
 */

import type {
  IntradaySlotRow,
  IntradaySnapshotPayload,
  IntradaySnapshot,
  IntradaySource,
  IntradaySummaryPayload,
} from '../../api/intraday'

export type IntradayDemoVariant = 'full' | 'partial' | 'empty' | 'mixed'

export const DEMO_INTRADAY_DATE = '2026-10-30'

/** 采集侧的 6 个时点名（`core/intraday.py:SLOTS`）——只用于造夹具，渲染层不看它。 */
const DEMO_SLOTS = ['09:25', '09:35', '10:00', '11:30', '14:00', '15:00']

function fileName(slot: string): string {
  return `${slot.replace(':', '')}.json`
}

function moment(date: string, slot: string, seconds = 0): string {
  return `${date}T${slot}:${String(seconds).padStart(2, '0')}+08:00`
}

interface SlotSeed {
  status: string
  stored: boolean
  recorded?: boolean
  detail?: string | null
  reason?: string | null
  price_field?: string | null
  stocks?: number | null
  fresh?: number | null
  /** 写状态的时刻偏移，用来让「未跑过的时点」`at` 为 null */
  atSeconds?: number | null
}

function row(date: string, slot: string, seed: SlotSeed): IntradaySlotRow {
  const recorded = seed.recorded ?? true
  return {
    slot,
    status: seed.status,
    recorded,
    available: seed.stored,
    reason: seed.stored ? null : (seed.reason ?? seed.detail ?? null),
    file: seed.stored ? fileName(slot) : null,
    detail: seed.detail ?? null,
    price_field: seed.stored ? (seed.price_field ?? 'price') : null,
    stocks: seed.stored ? (seed.stocks ?? 3) : null,
    fresh: seed.stored ? (seed.fresh ?? 3) : null,
    at: recorded ? moment(date, slot, seed.atSeconds ?? 0) : null,
  }
}

// ---------------------------------------------------------------- 时点行

/** 完整的一天：6 个时点全部落盘（状态含 `degraded` 与 `already`），无缺格。 */
function fullRows(date: string): IntradaySlotRow[] {
  return [
    row(date, DEMO_SLOTS[0], { status: 'ok', stored: true, price_field: 'open', detail: '3/3 只昨日涨停股有今天读数' }),
    row(date, DEMO_SLOTS[1], { status: 'ok', stored: true, detail: '3/3 只昨日涨停股有今天读数' }),
    row(date, DEMO_SLOTS[2], { status: 'degraded', stored: true, detail: '情绪面取数失败，只写昨日涨停池' }),
    row(date, DEMO_SLOTS[3], { status: 'ok', stored: true, detail: '3/3 只昨日涨停股有今天读数' }),
    row(date, DEMO_SLOTS[4], { status: 'already', stored: true, detail: '1400.json 已存在，未覆盖' }),
    row(date, DEMO_SLOTS[5], { status: 'ok', stored: true, detail: '3/3 只昨日涨停股有今天读数' }),
  ]
}

/** 部分形态：6 个时点里恰好 2 个 `missing`，其余有快照（验收要求的形状）。 */
function partialRows(date: string): IntradaySlotRow[] {
  return [
    row(date, DEMO_SLOTS[0], { status: 'ok', stored: true, price_field: 'open', detail: '3/3 只昨日涨停股有今天读数' }),
    row(date, DEMO_SLOTS[1], { status: 'ok', stored: true, detail: '3/3 只昨日涨停股有今天读数' }),
    row(date, DEMO_SLOTS[2], { status: 'degraded', stored: true, detail: '情绪面缺，只写昨日涨停池' }),
    row(date, DEMO_SLOTS[3], { status: 'ok', stored: true, detail: '3/3 只昨日涨停股有今天读数' }),
    row(date, DEMO_SLOTS[4], {
      status: 'missing',
      stored: false,
      detail: '3 只昨日涨停股没有一条今天的报价：行情日期不是今天或上游未更新',
    }),
    row(date, DEMO_SLOTS[5], {
      status: 'missing',
      stored: false,
      detail: '3 只昨日涨停股没有一条今天的报价：行情日期不是今天或上游未更新',
    }),
  ]
}

/** 六种时点状态各占一格（走查用：每格的状态要各自可辨）。 */
function mixedRows(date: string): IntradaySlotRow[] {
  return [
    row(date, DEMO_SLOTS[0], { status: 'ok', stored: true, price_field: 'open', detail: '3/3 只昨日涨停股有今天读数' }),
    row(date, DEMO_SLOTS[1], { status: 'degraded', stored: true, detail: '情绪面取数失败，只写昨日涨停池' }),
    row(date, DEMO_SLOTS[2], { status: 'already', stored: true, detail: '1000.json 已存在，未覆盖' }),
    row(date, DEMO_SLOTS[3], {
      status: 'skipped',
      stored: false,
      detail: '11:39:12 不在有效窗口 11:30:00–11:37:59，未落快照（不补跑）',
    }),
    row(date, DEMO_SLOTS[4], { status: 'missing', stored: false, detail: '昨日涨停池为空，没有可存的读数' }),
    row(date, DEMO_SLOTS[5], { status: 'failed', stored: false, detail: '快照抓取失败：peer 拒绝连接' }),
  ]
}

/** 空态：`status.json` 不存在，6 行全是没有记录。 */
function emptyRows(date: string): IntradaySlotRow[] {
  return DEMO_SLOTS.map((slot) =>
    row(date, slot, {
      status: 'missing',
      stored: false,
      recorded: false,
      reason: `status.json 里没有 ${slot} 的记录：该时点没跑过，或没落进有效窗口（不补跑，见 DEC-035 §2.3）`,
    }),
  )
}

export function demoSummary(date: string, variant: IntradayDemoVariant): IntradaySummaryPayload {
  if (variant === 'empty') {
    const slots = emptyRows(date)
    return {
      date,
      available: false,
      reason: `status.json 不存在：这台机器上没有 ${date} 的快照记录（调度器未运行过这一天，或 data_dir 不是这个）`,
      has_status_file: false,
      slot_count: slots.length,
      snapshot_count: 0,
      status_updated_at: null,
      data_source_status: 'missing',
      slots,
    }
  }
  const slots = variant === 'partial' ? partialRows(date) : variant === 'mixed' ? mixedRows(date) : fullRows(date)
  const held = slots.filter((item) => item.available).length
  return {
    date,
    available: held > 0,
    reason: null,
    has_status_file: true,
    slot_count: slots.length,
    snapshot_count: held,
    status_updated_at: moment(date, slots[slots.length - 1].slot),
    data_source_status: 'ok',
    slots,
  }
}

// ---------------------------------------------------------------- 快照原文

/** 昨日涨停池的市场级聚合：数字全部来自采集侧，页面不重算。 */
function limitUpBlock(priceField: string, change: number): IntradaySnapshot['yesterday_limit_up'] {
  return {
    count: 3,
    fresh: 3,
    stale: 0,
    missing: [],
    overall: { count: 3, avg_change_pct: change, up: 2, flat: 0, down: 1, limit_up: 1 },
    by_board: [
      { board: '首板', count: 2, avg_change_pct: change - 1.2 },
      { board: '三板及以上', count: 1, avg_change_pct: change + 5.4 },
    ],
    top_board: {
      streak: 4,
      stocks: [
        {
          code: '001219',
          name: null,
          yesterday_streak: 4,
          reading_field: priceField,
          reading: 19.94,
          change_pct: change + 5.4,
          at_limit_up: true,
          fresh: true,
          reason: null,
        },
      ],
    },
    stocks: [
      {
        code: '001219',
        name: null,
        yesterday_streak: 4,
        reading_field: priceField,
        reading: 19.94,
        change_pct: change + 5.4,
        at_limit_up: true,
        fresh: true,
        reason: null,
      },
      {
        code: '600745',
        name: null,
        yesterday_streak: 1,
        reading_field: priceField,
        reading: 7.92,
        change_pct: change - 4.6,
        at_limit_up: false,
        fresh: true,
        reason: null,
      },
      {
        code: '002195',
        name: null,
        yesterday_streak: 1,
        reading_field: priceField,
        reading: 12.36,
        change_pct: change + 1.8,
        at_limit_up: false,
        fresh: true,
        reason: null,
      },
    ],
  }
}

function sentimentBlock(available: boolean): IntradaySnapshot['sentiment'] {
  if (!available) {
    return {
      available: false,
      reason: 'market_sentiment 当日 data_as_of 不是今天，情绪面不并入',
      limit_up: null,
      broken: null,
      limit_down: null,
      max_streak: null,
      seal_rate_pct: null,
      broken_rate_pct: null,
      one_to_two: null,
    }
  }
  return {
    available: true,
    reason: null,
    limit_up: 47,
    broken: 31,
    limit_down: 8,
    max_streak: 5,
    seal_rate_pct: 60.3,
    broken_rate_pct: 39.7,
    one_to_two: {
      available: true,
      yesterday_first_board: 2,
      today_second_board: 2,
      promoted: 0,
      rate_pct: 0.0,
      reason: null,
    },
  }
}

function snapshotOf(date: string, slot: string, priceField: string, sentiment: boolean): IntradaySnapshot {
  return {
    date,
    slot,
    captured_at: moment(date, slot, 6),
    price_field: priceField,
    source: {
      dataset: 'spot_quote',
      tool: 'tencent_quote_batch',
      source_detail: 'spot_quote_stub@stub',
      trail: ['spot_quote_stub@stub', 'yesterday_limit_up_pool@stub'],
    },
    yesterday_limit_up: limitUpBlock(priceField, priceField === 'open' ? 1.24 : 2.43),
    sentiment: slot === DEMO_SLOTS[0] ? null : sentimentBlock(sentiment),
    extra: {},
  }
}

export function demoSnapshot(date: string, slot: string, variant: IntradayDemoVariant): IntradaySnapshotPayload {
  const rows =
    variant === 'empty' ? emptyRows(date) : variant === 'partial' ? partialRows(date) : variant === 'mixed' ? mixedRows(date) : fullRows(date)
  const target = rows.find((item) => item.slot === slot) ?? rows[0]
  const file = fileName(target.slot)
  if (!target.available) {
    return {
      date,
      slot: target.slot,
      available: false,
      reason: `${file} 不存在：${date} 的 ${target.slot} 没有落盘快照（逐时点状态见 GET /api/intraday?date=${date}）`,
      data_source_status: 'missing',
      snapshot: null,
    }
  }
  return {
    date,
    slot: target.slot,
    available: true,
    reason: null,
    data_source_status: 'ok',
    snapshot: snapshotOf(date, target.slot, target.price_field ?? 'price', target.status !== 'degraded'),
  }
}

export function demoIntradaySource(variant: IntradayDemoVariant): IntradaySource {
  return {
    summary: async (query) => demoSummary(query.date, variant),
    snapshot: async (query) => demoSnapshot(query.date, query.slot, variant),
  }
}
