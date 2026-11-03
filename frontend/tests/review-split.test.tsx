// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AiReviewScreen from '../src/screens/AiReviewScreen'
import ReviewScreen from '../src/screens/ReviewScreen'

/**
 * QUOT-344-S2 / DEC-049：复盘**数据面**与**模型面**的边界（验收项「三屏内容互不重叠」+
 * 「不允许把模型未配显示成数据缺失」）。
 *
 * 两屏读同一份 `/api/review` 产物，各取一半：
 * - `/review`：三张指标卡 + 五段 `sections[].metrics` +「我的判断」草稿；无叙述、无模型名、无生成入口；
 * - `/review/ai`：五段 `narrative` + 段级 `status` + `ai_failures[]` + `ai.model` + 控制面入口；无指标卡、无草稿。
 *
 * 「没有 AI 正文」有三种成因，必须分开显示（DEC-049 §4）：产物不存在 → 引导 `ldst review`；
 * 产物在但模型未接入 → 引导「接入 AI」；段级失败 → 逐段标注 + 按段重试。
 */

const THEME_NARRATIVE =
  '人工智能仍是强度第一的主线，机器人、半导体紧随其后，三线同日共振是当日情绪上行的直接来源。'

const UNCONFIGURED_DATE = '2026-11-03'

/** `ai/orchestrator.py:_generate` 未配置凭据分支的真实形状：五段都 `degraded` + `model: null`，指标齐全。 */
function unconfiguredReviewEnvelope(date: string) {
  const sections = [
    {
      name: 'sentiment',
      title: '情绪温度与赚钱效应',
      metrics: {
        available: true,
        temperature: 61,
        promotion_rate: 28.4,
        seal_rate: 80,
        limit_up_count: 39,
        broken_count: 6,
        yesterday_limit_up_count: 41,
        max_board: 4,
        gap: true,
        gap_levels: [6],
        missing: [],
        source_detail: 'limit_pool: duckdb（39 条）',
      },
    },
    { name: 'capital', title: '资金结构与主力行为', metrics: { available: true, records: 39 } },
    { name: 'theme', title: '题材主线与轮动', metrics: { available: true, industry_rows: 28, concept_rows: 35, news_rows: 9 } },
    { name: 'dragon_tiger', title: '龙虎榜席位与性质', metrics: { available: true, board_rows: 31, seat_rows: 74 } },
    { name: 'leader', title: '龙头与梯队结构', metrics: { available: true, height: 4, count: 39, unknown_board: 0 } },
  ].map((section) => ({
    ...section,
    status: 'degraded',
    mode: 'text',
    model: null,
    degraded: true,
    narrative: ['AI 未配置（缺少模型凭据），本段未生成叙事，仅保留程序计算的指标。'],
    notes: ['纯文本降级：未配置模型凭据，不发起任何请求'],
  }))

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
        headline: ['情绪偏暖，涨停家数与最高板同步走高。'],
        sections,
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

function unconfiguredSentimentEnvelope(date: string) {
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
        gap: true,
        gap_levels: [6],
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

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/**
 * 两屏同时挂载（各包一个容器）：重叠内容会被数出来，边界因此是可断言的而非靠读代码。
 * 生产路由一次只渲染一屏，这里挂两屏只是为了对照。
 */
function renderPair(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <div data-testid="data-screen">
        <ReviewScreen />
      </div>
      <div data-testid="model-screen">
        <AiReviewScreen />
      </div>
    </MemoryRouter>,
  )
}

function textOf(testId: string): string {
  return screen.getByTestId(testId).textContent ?? ''
}

/** 段内程序指标行（`dt` + 同排 `dd`），只在数据面存在；按段落定位是因为「最高板」这类 key 多段共有。 */
function metricRow(testId: string, sectionName: string, label: string): string {
  const section = screen.getByTestId(testId).querySelector(`#review-section-${sectionName}`)
  if (!section) throw new Error(`${testId} 里没有 ${sectionName} 段`)
  const dt = Array.from(section.querySelectorAll('dt')).find((node) => node.textContent === label)
  const dd = dt?.parentElement?.querySelector('dd')
  if (!dd) throw new Error(`${sectionName} 段没有指标行「${label}」`)
  return dd.textContent ?? ''
}

describe('复盘两屏边界（DEC-049）', () => {
  it('同一份演示产物：叙述只在模型面，指标与草稿只在数据面', async () => {
    renderPair('/review?demo=1&date=2026-10-30')
    await screen.findByRole('heading', { level: 1, name: 'AI复盘' })

    const dataText = textOf('data-screen')
    const modelText = textOf('model-screen')

    // 数据面：三张指标卡 + 段内程序指标 + 五段草稿
    expect(dataText).toContain('涨停 47 家')
    expect(metricRow('data-screen', 'sentiment', '封板率')).toBe('85.5%')
    expect(within(screen.getByTestId('data-screen')).getAllByRole('textbox').length).toBeGreaterThanOrEqual(5)

    // 模型面：叙述 + 模型名 + 段级状态 + 生成入口
    expect(modelText).toContain(THEME_NARRATIVE)
    expect(modelText).toContain('deepseek-v3')
    expect(within(screen.getByTestId('model-screen')).queryAllByRole('textbox')).toHaveLength(0)

    // 重叠检查：同一段叙述不可能在两屏各出现一次
    expect(screen.getAllByText(THEME_NARRATIVE)).toHaveLength(1)
    expect(screen.queryByText('封板率')).not.toBeNull()
    expect(within(screen.getByTestId('model-screen')).queryByText('封板率')).toBeNull()
    expect(within(screen.getByTestId('model-screen')).queryByText('我的判断 · 情绪')).toBeNull()
    expect(within(screen.getByTestId('data-screen')).queryByText('已完成')).toBeNull()
    expect(within(screen.getByTestId('data-screen')).queryByText('重跑该段')).toBeNull()
    expect(within(screen.getByTestId('data-screen')).queryByText('deepseek-v3')).toBeNull()
  })

  it('模型未接入：AI屏说「缺的是模型」并给接入入口，数据屏照常出五段指标', async () => {
    const review = unconfiguredReviewEnvelope(UNCONFIGURED_DATE)
    const sentiment = unconfiguredSentimentEnvelope(UNCONFIGURED_DATE)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        const body = String(input).includes('/api/review') ? review : sentiment
        return { ok: true, status: 200, json: async () => body }
      }),
    )

    renderPair(`/review/ai?date=${UNCONFIGURED_DATE}`)
    await screen.findByRole('heading', { level: 1, name: 'AI复盘' })

    const modelScreen = screen.getByTestId('model-screen')
    expect(await within(modelScreen).findByText('模型未接入：五段都没有跑过模型')).toBeTruthy()
    expect(within(modelScreen).getAllByText('模型未接入：五段都没有跑过模型')).toHaveLength(1)
    expect(within(modelScreen).getByText(/这里缺的是模型，不是数据/)).toBeTruthy()
    expect(within(modelScreen).getByRole('button', { name: '接入 AI' })).toBeTruthy()

    // 关键反例：这一屏没有把「模型没配」说成「数据没了」
    const modelText = textOf('model-screen')
    expect(modelText).not.toContain('还没有复盘产物')
    expect(modelText).not.toContain('数据缺失')
    expect(modelText).not.toContain('待生成')

    // 数据面不受影响：五段程序指标都在，也不出现模型字样
    expect(metricRow('data-screen', 'dragon_tiger', '上榜个股')).toBe('31')
    expect(within(modelScreen).queryByText('缺数')).toBeNull()
    expect(textOf('data-screen')).not.toContain('未接入')
    expect(textOf('data-screen')).not.toContain('接入 AI')
  })

  it('产物不存在：AI屏只引导 ldst review，不喊「接入 AI」', async () => {
    renderPair('/review/ai?demo=1&variant=empty&date=2026-10-30')
    const modelScreen = screen.getByTestId('model-screen')

    expect(await within(modelScreen).findByText('这一天还没有复盘产物')).toBeTruthy()
    expect(within(modelScreen).getAllByText('ldst review --date 2026-10-30').length).toBeGreaterThan(0)
    expect(within(modelScreen).getByRole('button', { name: '重新检查' })).toBeTruthy()
    expect(within(modelScreen).getByRole('button', { name: '复制命令' })).toBeTruthy()
    expect(within(modelScreen).queryByRole('button', { name: '接入 AI' })).toBeNull()
    // 数据屏同一份事实：也是「没产物」，也是引导跑命令
    expect(textOf('data-screen')).toContain('2026-10-30 尚未生成复盘')
    expect(textOf('data-screen')).toContain('只影响 AI复盘屏')
  })

  it('段级失败：逐段标注 + 按段重跑，不与「未接入」混为一谈', async () => {
    renderPair('/review/ai?demo=1&variant=partial&date=2026-10-30')
    const modelScreen = screen.getByTestId('model-screen')

    expect(await within(modelScreen).findByText('五段中 1 段失败、1 段降级（网络）')).toBeTruthy()
    expect(within(modelScreen).getAllByText('失败').length).toBeGreaterThan(0)
    expect(within(modelScreen).getAllByText('降级完成').length).toBeGreaterThan(0)
    const leader = modelScreen.querySelector<HTMLElement>('#review-ai-section-leader')
    if (!leader) throw new Error('模型面没有龙头段')
    expect(within(leader).getAllByText('重跑该段').length).toBeGreaterThan(0)
    // 有失败段 ≠ 没配模型：这条 case 不给「接入 AI」引导
    expect(within(modelScreen).queryByRole('button', { name: '接入 AI' })).toBeNull()
    expect(textOf('model-screen')).not.toContain('模型未接入')
    // 演示态不碰控制面：生成入口禁用且原因可读
    const generate = within(modelScreen).getByRole('button', { name: /重新生成|重试失败段/ })
    expect(generate.hasAttribute('disabled')).toBe(true)
    const describedBy = generate.getAttribute('aria-describedby')
    expect(describedBy).not.toBeNull()
    expect(document.getElementById(describedBy!)?.textContent).toContain('演示态不触发 AI 生成')
    // 数据面看不到段级成败
    expect(textOf('data-screen')).not.toContain('降级完成')
  })
})
