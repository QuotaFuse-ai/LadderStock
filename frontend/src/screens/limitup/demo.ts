/**
 * 涨停样本页演示数据（`?demo=1` 显式开启，页面带「演示态」水印）。
 *
 * 形状与 `/api/limit-pool` 契约逐键对齐，用途只有两个：
 * 后端接口上线前验证页面结构与五态、截图取证。
 *
 * **绝不在真实通道里回落到这份数据**：`?demo=1` 是唯一入口，
 * 真实请求 404 / 断连时页面进空态 / 离线态，不展示这里的任何一行。
 */

import type { LimitPoolPayload, LimitPoolRow, LimitPoolSource } from '../../api/limitPool'

export type LimitPoolDemoVariant = 'full' | 'partial' | 'empty'

export const DEMO_LIMIT_POOL_DATE = '2026-10-30'

const ROWS: LimitPoolRow[] = [
  {
    code: '002594',
    name: '比亚迪',
    streak: 3,
    first_seal_time: '101205',
    last_seal_time: '143001',
    reason: '新能源汽车',
    trade_date: DEMO_LIMIT_POOL_DATE,
  },
  {
    code: '600000',
    name: '浦发银行',
    streak: 2,
    first_seal_time: '093015',
    last_seal_time: '142500',
    reason: '银行',
    trade_date: DEMO_LIMIT_POOL_DATE,
  },
  {
    code: '000001',
    name: '平安银行',
    streak: 2,
    first_seal_time: '092945',
    last_seal_time: null,
    reason: '银行',
    trade_date: DEMO_LIMIT_POOL_DATE,
  },
  {
    code: '300750',
    name: '宁德时代',
    streak: 1,
    first_seal_time: '094210',
    last_seal_time: '094210',
    reason: '锂电池',
    trade_date: DEMO_LIMIT_POOL_DATE,
  },
  {
    code: '688981',
    name: '中芯国际',
    streak: 1,
    first_seal_time: '130502',
    last_seal_time: '130502',
    reason: '半导体',
    trade_date: DEMO_LIMIT_POOL_DATE,
  },
  {
    code: '600519',
    name: '贵州茅台',
    streak: null,
    first_seal_time: null,
    last_seal_time: null,
    reason: null,
    trade_date: DEMO_LIMIT_POOL_DATE,
  },
]

function demoPayload(variant: LimitPoolDemoVariant, date: string): LimitPoolPayload {
  if (variant === 'empty') {
    return {
      date,
      available: false,
      reason: '当日涨停池未同步：本机还没有该交易日的同步产物。',
      source_detail: 'limit_pool: 未找到（0 条）',
      count: 0,
      data_source_status: 'missing',
      rows: [],
    }
  }
  const partial = variant === 'partial'
  return {
    date,
    available: true,
    reason: partial ? '部分记录缺少封板时间或涨停原因' : null,
    source_detail: `limit_pool: duckdb（${ROWS.length} 条）`,
    count: ROWS.length,
    data_source_status: partial ? 'partial' : 'ok',
    rows: ROWS,
  }
}

export function demoLimitPoolSource(variant: LimitPoolDemoVariant): LimitPoolSource {
  return {
    load: async (query) => demoPayload(variant, query.date || DEMO_LIMIT_POOL_DATE),
  }
}
