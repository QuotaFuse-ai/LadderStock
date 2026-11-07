// @vitest-environment jsdom
/**
 * 分享卡（QUOT-453）：卡片上的数字必须与页面同源，缺数保留 `—` 并逐条写原因。
 *
 * 断言的是「发出去的那张图/那段文字里到底写了什么」：
 * 不补 0、不重算、不含论点与持仓；主题跟随页面。
 */

import { describe, expect, it } from 'vitest'

import type { OverviewFinancials, OverviewQuote, ResearchOverview } from '../src/api/research'
import {
  SHARE_CARD_SIZE,
  buildShareCard,
  drawShareCard,
  shareStatement,
  shareSummary,
} from '../src/lib/shareCard'
import { cssVar } from '../src/charts/cssVar'

function quote(patch: Partial<OverviewQuote> = {}): OverviewQuote {
  return {
    name: '浦发银行',
    price: 9.2,
    prev_close: 9.1,
    change: 0.1,
    change_pct: 1.1,
    open: 9.15,
    high: 9.3,
    low: 9.05,
    volume: 120000,
    amount: 1.1e9,
    timestamp: '2026-11-02T15:00:00',
    source_detail: { peer: 'tickhub-cn', tool: 'stock_kline', fetched_at: '2026-11-02T15:05:09' },
    ...patch,
  }
}

function financials(patch: Partial<OverviewFinancials> = {}): OverviewFinancials {
  return {
    report_name: '2026年三季报',
    report_date: '2026-09-30',
    eps: 1.2,
    revenue: 1.4e11,
    net_profit: 4.6e10,
    roe: 9.4,
    revenue_yoy: 3.2,
    profit_yoy: -1.5,
    ...patch,
  }
}

function overview(patch: Partial<ResearchOverview> = {}): ResearchOverview {
  return {
    code: '600000',
    name: '浦发银行',
    as_of: '2026-11-02T15:05:09',
    dataSourceStatus: 'ok',
    kline: {
      bars: [
        { date: '2026-10-09', open: 9, high: 9.2, low: 8.9, close: 9.1, volume: 100000 },
        { date: '2026-11-02', open: 9.15, high: 9.3, low: 9.05, close: 9.2, volume: 120000 },
      ],
      count: 2,
      requested: 250,
      reason: null,
    },
    quote: { quote: quote(), reason: null },
    financials: { financials: financials(), reason: null },
    ...patch,
  }
}

function value(data: ReturnType<typeof buildShareCard>, label: string): string | undefined {
  return data.rows.find((row) => row.label === label)?.value
}

describe('buildShareCard：有数', () => {
  const data = buildShareCard({ code: '600000', name: '浦发银行', date: '2026-11-02', overview: overview(), generatedAt: '2026-11-02 15:10' })

  it('卡片里的数字就是概览里的那份，不重算', () => {
    expect(value(data, '现价')).toContain('9.2')
    // 涨跌幅与页面同一格式化入口（红涨绿跌那套符号规则），卡片不另写一份
    expect(value(data, '涨跌幅')).toBe('+1.10%')
    expect(value(data, 'K 线区间')).toBe('2026-10-09 至 2026-11-02')
    expect(value(data, '日 K 根数')).toBe('2')
    expect(value(data, '最新报告期')).toBe('2026年三季报')
    expect(value(data, '净利润同比')).toBe('-1.50%')
    expect(data.gaps).toEqual([])
  })

  it('来源与取数时间是我们自己的通道，不是抄来的说法', () => {
    expect(data.source).toBe('tickhub-cn · stock_kline')
    expect(data.fetchedAt).toBe('2026-11-02 15:05')
  })

  it('文字版与图同源：缺数写 —、来源与声明都在', () => {
    const lines = shareSummary(data).join('\n')
    expect(lines).toContain('浦发银行（600000） · 2026-11-02')
    expect(lines).toContain(shareStatement())
    expect(lines).toContain('没取到的：无')
    expect(lines).toContain('来源：tickhub-cn · stock_kline · 取数 2026-11-02 15:05')
  })
})

describe('buildShareCard：缺数', () => {
  it('没取到的一律显示 —，并逐条写原因，不补 0', () => {
    const data = buildShareCard({
      code: '600000',
      name: null,
      date: '2026-11-02',
      overview: overview({
        quote: { quote: quote({ price: null, amount: null }), reason: null },
        kline: { bars: [], count: 0, requested: 250, reason: '本地缓存里没有日线' },
        financials: { financials: null, reason: '概览里没有财务指标' },
      }),
    })

    expect(value(data, '现价')).toBe('—')
    expect(value(data, '成交额（元）')).toBe('—')
    expect(value(data, 'K 线区间')).toBe('—')
    expect(value(data, '最新报告期')).toBe('—')
    expect(data.rows.filter((row) => row.value === '0' || row.value === '0.00')).toEqual([])

    const gaps = data.gaps.join('\n')
    expect(gaps).toContain('成交额：实时快照里没有这一项。')
    expect(gaps).toContain('K 线：本地缓存里没有日线')
    expect(gaps).toContain('财务指标：概览里没有财务指标')
  })

  it('概览整个没返回时明说，不画一张有数的假图', () => {
    const data = buildShareCard({ code: '600000', name: null, date: '2026-11-02', overview: null })

    expect(data.gaps).toContain('概览接口还没有返回数据（加载中或失败）。')
    expect(data.gaps).toContain('当日行情：概览里没有实时快照')
    expect(data.rows.every((row) => row.value === '—' || row.label === 'K 线区间')).toBe(true)
  })

  it('入参里没有论点、持仓这些字段：卡片不可能带出私有内容', () => {
    const input: Record<string, unknown> = { code: '600000', name: null, date: '2026-11-02', overview: overview() }
    expect(Object.keys(input).sort()).toEqual(['code', 'date', 'name', 'overview'])

    const text = shareSummary(buildShareCard(input as never)).join('\n')
    for (const word of ['论点', '持仓', '仓位']) expect(text).not.toContain(word)
  })
})

describe('drawShareCard', () => {
  /** jsdom 没有真画布：录下每次 fillText 的文本，看画面上出现了什么字 */
  function fakeCtx() {
    const texts: Array<{ text: string; color: string | null }> = []
    const ctx = {
      fillText: (text: string, x: number, y: number) => {
        texts.push({ text: String(text), color: (ctx as never as { fillStyle: string }).fillStyle })
        void x
        void y
      },
      fillRect: () => undefined,
      fill: () => undefined,
      stroke: () => undefined,
      save: () => undefined,
      restore: () => undefined,
      beginPath: () => undefined,
      moveTo: () => undefined,
      lineTo: () => undefined,
      roundRect: () => undefined,
      measureText: (text: string) => ({ width: String(text).length * 8 }),
      fillStyle: '#000000',
      strokeStyle: '#000000',
      font: '',
      textBaseline: 'top',
    }
    return { ctx: ctx as never as CanvasRenderingContext2D, texts }
  }

  it('缺数在图上仍是 —，缺口原因也画进「没取到的」', () => {
    const data = buildShareCard({
      code: '600000',
      name: '浦发银行',
      date: '2026-11-02',
      overview: overview({ quote: { quote: quote({ price: null }), reason: null } }),
    })
    const { ctx, texts } = fakeCtx()
    drawShareCard(ctx, data, 'portrait')

    const drawn = texts.map((item) => item.text)
    expect(drawn).toContain('—')
    expect(drawn.some((text) => text.includes('没取到的'))).toBe(true)
    expect(drawn.some((text) => text.includes(shareStatement()))).toBe(true)
  })

  it('两种版式都画得出内容，行数与卡片数据一致', () => {
    const data = buildShareCard({ code: '600000', name: null, date: '2026-11-02', overview: overview() })
    for (const layout of ['portrait', 'landscape'] as const) {
      const { ctx, texts } = fakeCtx()
      drawShareCard(ctx, data, layout)
      expect(SHARE_CARD_SIZE[layout].width).toBeGreaterThan(0)
      // 每个读数都被写到画布上
      for (const item of data.rows) expect(texts.some((t) => t.text === item.value)).toBe(true)
    }
  })

  it('颜色一律来自设计令牌：页面切主题，导出的图跟着变', () => {
    const data = buildShareCard({
      code: '600000',
      name: null,
      date: '2026-11-02',
      overview: overview({ quote: { quote: quote({ price: null }), reason: null } }),
    })
    const { ctx, texts } = fakeCtx()
    drawShareCard(ctx, data, 'portrait')

    // 页眉小字用的就是 --ldst-text-muted，不是写在源码里的色值
    expect(texts[0]?.color).toBe(cssVar('--ldst-text-muted'))
    // 缺数那格用警告色标出来，取的也是令牌
    const missing = texts.find((item) => item.text === '—')
    expect(missing?.color).toBe(cssVar('--ldst-warn'))
  })
})
