// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewScreen from '../src/screens/ReviewScreen'
import { selectSlot, useReviewDraft } from '../stores/reviewDraft'

/**
 * QUOT-354 验收缺陷复验用例（BUG-B / BUG-C / BUG-D / BUG-F 的屏侧口径）。
 *
 * BUG-B 是 P1：`?date=` 深链曾被草稿里记的交易日反向覆盖，打开 11-03 却显示 11-02 的正文，
 * 人照着写判断就串到另一天上。这一组用例锁死三条口径：
 * 1. 日期只跟 URL，本机草稿不参与日期解析；
 * 2. 页内「交易日」选择器改的是 URL（可分享、可复现），不是本机状态；
 * 3. 草稿按交易日分槽，切日期不覆盖另一天的笔记。
 */

const DAY_A = '2026-11-02'
const DAY_B = '2026-11-03'
const DRAFT_ON_A = '11-02 的判断：情绪修复但量能不足。'

function sectionMetrics(name: string) {
  if (name === 'sentiment') {
    return {
      available: true,
      temperature: 61,
      promotion_rate: 28.4,
      seal_rate: 80,
      limit_up_count: 39,
      broken_count: 6,
      yesterday_limit_up_count: 41,
      max_board: 4,
      gap: false,
      gap_levels: [],
      missing: [],
      source_detail: 'limit_pool: duckdb（39 条）',
    }
  }
  if (name === 'theme') {
    // BUG-F 的真实形态：段级 available 为真（板块读到了），资讯源那天没有产物，
    // 后端如实给 `news_rows: null`——屏上必须是 `—`，不能是 0（0 会被读成「今天没有资讯」）。
    return {
      available: true,
      industry_rows: 49,
      concept_rows: 100,
      news_rows: null,
      source_detail: 'industry_board: duckdb（49 条）｜concept_board: duckdb（100 条）｜theme_news: none',
    }
  }
  return { available: true, source_detail: '' }
}

function reviewEnvelope(date: string) {
  return {
    domain: 'review',
    topic: 'review',
    event_id: null,
    data_as_of: date,
    payload: {
      date,
      review: {
        title: `${date} 短线复盘`,
        date,
        generated_at: `${date}T15:20:00+08:00`,
        freshness: 'cached',
        source_status: 'ok',
        source_detail: `local review cache: ${date}.json`,
        ai: { model: null, degraded: true },
        headline: [],
        sections: ['sentiment', 'capital', 'theme', 'dragon_tiger', 'leader'].map((name) => ({
          name,
          title: name,
          status: 'ok',
          mode: 'text',
          model: null,
          degraded: false,
          narrative: [],
          notes: [],
          metrics: sectionMetrics(name),
        })),
        ai_failures: [],
        validation: { date, conditions: [] },
        footer: '',
      },
      data_source_status: 'ok',
    },
    source: 'ladderstock',
    source_detail: `local review cache: ${date}.json`,
    fetched_at: `${date}T15:20:04+08:00`,
    freshness: 'cached',
    confidence: 'medium',
    source_status: 'ok',
  }
}

function sentimentEnvelope(date: string) {
  return {
    domain: 'sentiment',
    topic: 'shortline_sentiment',
    event_id: null,
    data_as_of: date,
    payload: {
      sentiment: {
        date,
        available: true,
        reason: null,
        temperature: 61,
        promotion_rate: 28.4,
        seal_rate: 80,
        limit_up_count: 39,
        broken_count: 6,
        yesterday_limit_up_count: 41,
        promoted_count: 12,
        max_board: 4,
        gap: false,
        gap_levels: [],
        missing: [],
        formula: '温度 = 0.45×晋级率 + 0.35×封板率 + 0.20×min(1, 最高板/6)',
        source_detail: 'limit_pool: duckdb（39 条）',
      },
      data_source_status: 'ok',
    },
    source: 'ladderstock',
    source_detail: 'limit_pool: duckdb（39 条）',
    fetched_at: `${date}T15:20:04+08:00`,
    freshness: 'cached',
    confidence: 'medium',
    source_status: 'ok',
  }
}

function stubFetch(): string[] {
  const urls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = String(input)
      urls.push(url)
      const date = url.match(/date=(\d{4}-\d{2}-\d{2})/)?.[1] ?? DAY_B
      const body = url.includes('/api/review') ? reviewEnvelope(date) : sentimentEnvelope(date)
      return { ok: true, status: 200, json: async () => body, headers: { get: () => null } }
    }) as unknown as typeof fetch,
  )
  return urls
}

/** 读当前 URL 的探针：选择器是否真的写了查询串，只能从路由本身验证。 */
function ParamsProbe() {
  const [params] = useSearchParams()
  return <p data-testid="probe">{params.get('date') ?? ''}</p>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/review" element={<ReviewScreen />} />
      </Routes>
      <ParamsProbe />
    </MemoryRouter>,
  )
}

function sentimentDraft(): HTMLTextAreaElement {
  return screen.getByRole('textbox', { name: '我的判断 · 情绪' }) as HTMLTextAreaElement
}

function dateInput(): HTMLInputElement {
  return screen.getByLabelText('交易日') as HTMLInputElement
}

/** 段内程序指标行（`dt` + 同排 `dd`），口径与 `review/SectionMetrics.tsx` 一致。 */
function metricRow(sectionName: string, label: string): string {
  const block = document.getElementById(`review-section-${sectionName}`)
  if (!block) throw new Error(`页面上没有 ${sectionName} 段`)
  const dt = Array.from(block.querySelectorAll('dt')).find((node) => node.textContent === label)
  const dd = dt?.parentElement?.querySelector('dd')
  if (!dd) throw new Error(`${sectionName} 段没有指标行「${label}」`)
  return dd.textContent ?? ''
}

/** 换日期会重新取数，页面先回骨架；等这一天的正文真的上屏了再断言。 */
async function readyOn(date: string) {
  expect(screen.getByTestId('probe').textContent).toBe(date)
  await screen.findByRole('textbox', { name: '我的判断 · 情绪' })
}

async function changeDate(date: string) {
  fireEvent.change(dateInput(), { target: { value: date } })
  await readyOn(date)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

beforeEach(() => {
  useReviewDraft.setState({ slots: {}, activeSection: 'sentiment', updatedAt: null })
})

describe('每日复盘的交易日与草稿归属（QUOT-354 BUG-B / BUG-C）', () => {
  it('?date= 深链优先于本机草稿日期，不串日', async () => {
    useReviewDraft.getState().setSection(DAY_A, 'sentiment', DRAFT_ON_A)
    const urls = stubFetch()

    renderAt(`/review?date=${DAY_B}`)
    await readyOn(DAY_B)

    // 日期以 URL 为准——这是 BUG-B 的核心：此前草稿日期优先级更高
    expect(dateInput().value).toBe(DAY_B)
    // 11-02 的草稿不该出现在 11-03 这一天
    expect(sentimentDraft().value).toBe('')
    expect(screen.queryByText(/已恢复/)).toBeNull()
    // 取数也按 URL 那一天发起，不会退回草稿记着的那天
    expect(urls.some((url) => /review\?date=2026-11-03/.test(url))).toBe(true)
    expect(urls.some((url) => /date=2026-11-02/.test(url))).toBe(false)
  })

  it('切回草稿所在的那天，草稿原样回填', async () => {
    useReviewDraft.getState().setSection(DAY_A, 'sentiment', DRAFT_ON_A)
    stubFetch()

    renderAt(`/review?date=${DAY_B}`)
    await readyOn(DAY_B)

    await changeDate(DAY_A)
    expect(sentimentDraft().value).toBe(DRAFT_ON_A)
    expect(screen.getByText(/已恢复 .* 的草稿/)).toBeTruthy()
  })

  it('页内「交易日」选择器改的是 URL，不是本机状态', async () => {
    stubFetch()
    renderAt(`/review?date=${DAY_B}`)
    await readyOn(DAY_B)

    await changeDate('2026-11-06')

    expect(screen.getByTestId('probe').textContent).toBe('2026-11-06')
    // 分片里不再有「当前交易日」这种本机状态（日期只有一个来源）
    expect(useReviewDraft.getState()).not.toHaveProperty('tradeDate')
  })

  it('草稿按交易日分槽：写 11-03 不会覆盖 11-02（BUG-C）', async () => {
    useReviewDraft.getState().setSection(DAY_A, 'sentiment', DRAFT_ON_A)
    stubFetch()

    renderAt(`/review?date=${DAY_B}`)
    await readyOn(DAY_B)

    fireEvent.change(sentimentDraft(), { target: { value: '11-03：晋级率回升，断层补平。' } })

    const state = useReviewDraft.getState()
    expect(selectSlot(state, DAY_B).sections.sentiment).toBe('11-03：晋级率回升，断层补平。')
    expect(selectSlot(state, DAY_A).sections.sentiment).toBe(DRAFT_ON_A)

    // 换日期后页面上只有这一天的正文
    await changeDate(DAY_A)
    expect(sentimentDraft().value).toBe(DRAFT_ON_A)
  })

  it('「清空当日草稿」只清当前那一天，其它日期的笔记留着', async () => {
    useReviewDraft.getState().setSection(DAY_A, 'sentiment', DRAFT_ON_A)
    useReviewDraft.getState().setSection(DAY_B, 'sentiment', '11-03：晋级率回升，断层补平。')
    stubFetch()

    renderAt(`/review?date=${DAY_B}`)
    await readyOn(DAY_B)

    // 工具栏上那颗（恢复横幅里还有同名的另一颗）
    fireEvent.click(screen.getAllByRole('button', { name: '清空当日草稿' })[0] as HTMLElement)
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '清空当日草稿' }))

    const state = useReviewDraft.getState()
    expect(selectSlot(state, DAY_B).sections.sentiment).toBe('')
    expect(selectSlot(state, DAY_A).sections.sentiment).toBe(DRAFT_ON_A)
    expect(sentimentDraft().value).toBe('')
  })
})

describe('分源行数口径（QUOT-354 BUG-F）', () => {
  it('段级有数据、单个源当天没产物：那一行是 `—`，同段其它读数照常', async () => {
    stubFetch()
    renderAt(`/review?date=${DAY_B}`)
    await readyOn(DAY_B)

    expect(metricRow('theme', '行业板块行数')).toBe('49')
    expect(metricRow('theme', '概念板块行数')).toBe('100')
    // 0 会被读成「今天没有资讯」，而事实是「本机没有这天的这个数据集」
    expect(metricRow('theme', '题材资讯行数')).toBe('—')
    // 缺数原因走已有的「数据来源」行，不新增契约字段
    expect(document.getElementById('review-section-theme')?.textContent).toContain('theme_news: none')
  })
})

describe('取数失败兜底文案（QUOT-354 BUG-D）', () => {
  it('文案里的按钮名与真实按钮一致：重试取数', async () => {
    stubFetch()
    renderAt(`/review?state=error&date=${DAY_B}`)

    const button = await screen.findByRole('button', { name: '重试取数' })
    expect(button.textContent).toBe('重试取数')
    // 文案指向的名字必须真存在，不能写成「重试取数」而按钮叫「重试」
    expect(screen.getAllByText(/重试取数/).length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })
})
