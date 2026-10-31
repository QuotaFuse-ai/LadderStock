// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import WeeklyScreen, { isoWeekLabel, shiftWeek } from '../src/screens/WeeklyScreen'

const WEEK = '2026-W44'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function section(name: string, title: string, mode: string, degraded: boolean) {
  return {
    name,
    title,
    status: degraded ? 'degraded' : 'ok',
    mode,
    model: degraded ? null : 'deepseek-v3',
    degraded,
    narrative: degraded ? [`${title}：情绪温度均值=62.8；晋级率均值=61.1%`] : [`${title}：本周缩量修复。`],
    notes: degraded ? ['模板降级：本段 AI 叙述未生成，以下要点由程序从事实包直出。'] : [],
    metrics: { week: WEEK },
  }
}

function weeklyReport(overrides: Record<string, unknown> = {}) {
  return {
    title: `${WEEK} 周度复盘`,
    week: WEEK,
    range: { start: '2026-10-26', end: '2026-11-01' },
    generated_at: '2026-11-01T18:00:00+00:00',
    available: true as boolean,
    reason: null as string | null,
    headline: ['3 个交易日有数据；情绪温度均值 62.8，52.5 → 45.8（-6.7）', '连板高度 2 → 4，峰值 4 板'],
    ai: { model: 'deepseek-v3', degraded: false },
    checkpoint: 'data/state/weekly_checkpoint/2026-W44.json',
    sections: [
      section('sentiment', '一周情绪温度与晋级率', 'structured', false),
      section('capital', '一周资金流向', 'structured', false),
      section('theme', '一周题材轮动与主线持续', 'structured', false),
      section('dragon_tiger', '一周龙虎榜与席位', 'structured', false),
      section('leader', '一周龙头与梯队', 'structured', false),
    ],
    ai_failures: [],
    facts: {
      week: WEEK,
      range: { start: '2026-10-26', end: '2026-11-01' },
      session_days: ['2026-10-26', '2026-10-27', '2026-10-28'],
      missing_days: [
        { date: '2026-10-29', reason: '涨停池为空，无法计算情绪指标（先运行 ldst sync）', has_report: false },
      ],
      available: true as boolean,
      reason: null as string | null,
      sentiment: {
        series: [
          {
            date: '2026-10-26',
            available: true,
            temperature: 52.5,
            promotion_rate: 50.0,
            seal_rate: 66.7,
            limit_up_count: 2,
            broken_count: 1,
            max_board: 2,
            gap_levels: [],
            reason: null,
            source: 'artifact',
          },
          {
            date: '2026-10-27',
            available: true,
            temperature: 90.0,
            promotion_rate: 100.0,
            seal_rate: 100.0,
            limit_up_count: 4,
            broken_count: 0,
            max_board: 3,
            gap_levels: [2],
            reason: null,
            source: 'recomputed',
          },
          {
            date: '2026-10-28',
            available: true,
            temperature: 45.8,
            promotion_rate: 33.3,
            seal_rate: 50.0,
            limit_up_count: 2,
            broken_count: 2,
            max_board: 4,
            gap_levels: [],
            reason: null,
            source: 'artifact',
          },
        ],
        temperature: { first: 52.5, last: 45.8, change: -6.7, avg: 62.8, samples: 3 },
        promotion_rate: { first: 50.0, last: 33.3, change: -16.7, avg: 61.1, samples: 3 },
        seal_rate: { first: 66.7, last: 50.0, change: -16.7, avg: 72.2, samples: 3 },
        limit_up_total: 8,
        broken_total: 3,
        gap_days: 1,
      },
      ladder: {
        series: [],
        height: { first: 2, last: 4, change: 2, avg: 3, samples: 3 },
        peak_height: 4,
        count_total: 8,
        gap_days: 1,
        unknown_board_total: 0,
        leaders: [
          { code: '600001', name: '甲', boards: 4, date: '2026-10-28' },
          { code: '600010', name: '壬', boards: 3, date: '2026-10-27' },
        ],
      },
      breadth: {
        available: true,
        reason: null,
        bands: [],
        days: [],
        distribution: [
          { band: '涨≥9%', count: 2, share_pct: 18.2 },
          { band: '涨5-9%', count: 1, share_pct: 9.1 },
          { band: '跌≤-9%', count: 1, share_pct: 9.1 },
        ],
        up_count: 6,
        down_count: 4,
        flat_count: 1,
        samples: 11,
        up_share_pct: 54.5,
      },
      themes: {
        available: true,
        reason: null,
        window: 10,
        observed: 4,
        days: [],
        themes: [
          {
            name: 'AI算力',
            days_on_top: 3,
            streak_days: 3,
            first_date: '2026-10-26',
            last_date: '2026-10-28',
            best_change_pct: 8.1,
            avg_change_pct: 6.0,
            days: ['2026-10-26', '2026-10-27', '2026-10-28'],
          },
        ],
      },
      validation: {
        available: true,
        reason: null,
        conditions: 5,
        settled: 3,
        passed: 1,
        failed: 1,
        unknown: 1,
        pending: 2,
        pass_rate: 33.3,
        items: [
          {
            generated_date: '2026-10-26',
            id: 'c1',
            text: 'temperature >= 60',
            metric: 'temperature',
            operator: '>=',
            target: 60,
            actual: 66.0,
            settled: true,
            passed: true,
            next_session: '2026-10-27',
          },
        ],
      },
      ai_coverage: {
        days_with_checkpoint: 2,
        sections_expected: 10,
        sections_done: 8,
        sections_degraded: 1,
        sections_failed: 1,
        sections_pending: 0,
      },
      caliber: { sentiment: '本周逐日情绪温度均值（口径同 core/sentiment.py）' },
      source_detail: 'limit_pool: duckdb（周末日 2026-10-28 共 2 条）',
      ...overrides,
    },
  }
}

function envelope(weekly: unknown) {
  return {
    schema: 'ldst.ladder/1',
    domain: 'review',
    topic: 'weekly_report',
    event_id: null,
    data_as_of: '2026-10-28',
    payload: { week: WEEK, weekly, data_source_status: weekly ? 'ok' : 'missing' },
    source: 'ladderstock',
    source_detail: 'local weekly cache',
    fetched_at: '2026-11-01T18:00:00+00:00',
    freshness: 'cached',
    confidence: 'high',
    source_status: 'ok',
  }
}

function stubFetch(body: unknown, urls: string[] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      urls.push(String(input))
      return {
        ok: true,
        status: 200,
        json: async () => body,
        headers: { get: () => null },
      }
    }) as unknown as typeof fetch,
  )
  return urls
}

function renderAt(path = `/weekly?week=${WEEK}`) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/weekly" element={<WeeklyScreen />} />
        <Route path="/settings" element={<p>设置页</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ISO 周标签', () => {
  it('与后端 weekly/week.py 同口径：周一为一周之始、跨年归 ISO 年', () => {
    expect(isoWeekLabel(new Date(2026, 10, 1))).toBe('2026-W44') // 2026-11-01 周日
    expect(isoWeekLabel(new Date(2026, 9, 26))).toBe('2026-W44') // 周一
    expect(isoWeekLabel(new Date(2024, 11, 30))).toBe('2025-W01')
  })

  it('前后翻周按整周移动，跨年也不滑', () => {
    expect(shiftWeek(WEEK, -1)).toBe('2026-W43')
    expect(shiftWeek(WEEK, 1)).toBe('2026-W45')
    expect(shiftWeek('2025-W01', -1)).toBe('2024-W52')
    expect(shiftWeek('非法', 1)).toBe('非法')
  })
})

describe('周度复盘 /weekly', () => {
  it('渲染五段与事实包指标，请求带 week 参数', async () => {
    const urls = stubFetch(envelope(weeklyReport()))
    renderAt()

    expect(await screen.findByRole('heading', { name: '周度复盘' })).toBeTruthy()
    expect(screen.getByText('2026-W44 · 2026-10-26 ~ 2026-11-01 · 3 个交易日有数据')).toBeTruthy()
    expect(urls[0]).toContain(`/weekly?week=${WEEK}`)

    expect(await screen.findByText('一周情绪温度与晋级率')).toBeTruthy()
    expect(screen.getByText('一周龙头与梯队')).toBeTruthy()
    // 事实包数字（均值 62.8 保留一位小数，不四舍五入成 63）
    expect(screen.getByText('62.8')).toBeTruthy()
    expect(screen.getByText('61.1%')).toBeTruthy()
    expect(screen.getAllByText('4 板').length).toBeGreaterThan(0)
    // 每日序列里的来源标记翻成人话，两个来源都要出现
    expect(screen.getAllByText('当日复盘').length).toBe(2)
    expect(screen.getAllByText('补算').length).toBe(1)
    // 龙头与验证结算
    expect(screen.getByText('AI算力')).toBeTruthy()
    expect(screen.getByText('600001')).toBeTruthy()
    expect(screen.getAllByText(/temperature >= 60/).length).toBeGreaterThan(0)
  })

  it('模板降级段给出可读说明，并把 AI 摘要徽标切成 warn', async () => {
    const degraded = weeklyReport()
    degraded.sections = degraded.sections.map((item, index) =>
      index === 4 ? section('leader', '一周龙头与梯队', 'template', true) : item,
    )
    degraded.ai = { model: 'deepseek-v3', degraded: true }
    stubFetch(envelope(degraded))
    renderAt()

    expect(await screen.findByText('部分段落 AI 降级')).toBeTruthy()
    expect((await screen.findAllByText(/模板降级/)).length).toBeGreaterThan(0)
    expect(screen.getAllByText('降级').length).toBeGreaterThan(0)
  })

  it('没有产物：空态说明怎么生成，不 404 也不假装成功', async () => {
    stubFetch(envelope(null))
    renderAt(`/weekly?week=2026-W45`)

    expect(await screen.findByText('2026-W45 还没有可用周报')).toBeTruthy()
    expect(screen.getByText(/ldst weekly --week 2026-W45/)).toBeTruthy()
  })

  it('整周没有可用读数：用事实包里的 reason 说明原因', async () => {
    const empty = weeklyReport()
    empty.facts = { ...empty.facts, available: false, reason: '2026-W44 没有可用单日指标：涨停池都是空的' }
    empty.available = false
    empty.reason = '2026-W44 没有可用单日指标：涨停池都是空的'
    stubFetch(envelope(empty))
    renderAt()

    expect(await screen.findByText('2026-W44 没有可用单日指标：涨停池都是空的')).toBeTruthy()
  })

  it('缺数据的日子与来源都摆出来，不藏', async () => {
    stubFetch(envelope(weeklyReport()))
    renderAt()

    expect(await screen.findByText(/10-29：涨停池为空/)).toBeTruthy()
    expect(screen.getByText(/limit_pool: duckdb/)).toBeTruthy()
    expect(screen.getByText(/口径同 core\/sentiment.py/)).toBeTruthy()
  })

  it('非法 week 回落到本周，不请求一个不存在的标签', async () => {
    const urls = stubFetch(envelope(null))
    renderAt('/weekly?week=../etc/passwd')

    await waitFor(() => expect(urls.length).toBeGreaterThan(0))
    expect(urls[0]).toContain(`/weekly?week=${isoWeekLabel(new Date())}`)
  })

  it('上周 / 下周按钮改查询串并重新取数', async () => {
    const urls = stubFetch(envelope(weeklyReport()))
    renderAt()

    await screen.findByText('一周龙头与梯队')
    fireEvent.click(screen.getByRole('button', { name: '上一周' }))
    await waitFor(() => expect(urls.some((url) => url.includes('week=2026-W43'))).toBe(true))
  })

  it('服务未连接：离线横幅 + 重试，不显示伪造指标', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }) as unknown as typeof fetch,
    )
    renderAt()

    expect(await screen.findByText(/本机服务未连接/)).toBeTruthy()
    expect(screen.queryByText('62.8')).toBeNull()
  })
})
