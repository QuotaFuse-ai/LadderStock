// @vitest-environment jsdom
/**
 * 报告对比 + 分享卡（QUOT-453）界面走查。
 *
 * 三态各有说法，验收要求逐条对着屏幕检查的那几条都在这里断言：
 * - 接口没接 → 错误 + 重试，不是「没有变化」；
 * - 只有一次研究 → 明说「还没有可比的第二次」；
 * - 两次都有 → 差异按四类分开，缺一侧的读数仍是 `—`（不补 0）。
 */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import {
  STAGE_TITLE,
  type ResearchOverview,
  type ResearchRunList,
  type ResearchSource,
  type ResearchStageName,
  type ResearchStatus,
  type StageDocument,
} from '../src/api/research'
import { CompareSection } from '../src/components/research/sections/CompareSection'

const CODE = '600000'
const NEWER = '2026-11-02'
const OLDER = '2026-10-28'

type StageSeed = { markdown: string; facts?: Record<string, unknown>; status?: 'ok' | 'degraded' }

function stageDocument(stage: ResearchStageName, date: string, facts: Record<string, unknown>): StageDocument {
  return {
    code: CODE,
    date,
    stage,
    title: STAGE_TITLE[stage],
    status: 'ok',
    narrative: [],
    notes: [],
    facts,
    provenance: { values: {}, sources: [], unverified: false },
  }
}

function statusWith(date: string, stages: Partial<Record<ResearchStageName, StageSeed>>): ResearchStatus {
  const names = Object.keys(stages) as ResearchStageName[]
  return {
    code: CODE,
    date,
    available: true,
    complete: names.length === 6,
    checkpoint: 'done',
    updated_at: `${date}T16:00:00`,
    stages: names.map((name) => ({
      name,
      title: STAGE_TITLE[name],
      status: stages[name]?.status ?? 'ok',
      artifact: {
        document: stageDocument(name, date, stages[name]?.facts ?? {}),
        markdown: stages[name]?.markdown ?? '',
      },
    })),
    pending: [],
    failed: [],
    source_status: 'ok',
  }
}

const NEWER_STATUS = statusWith(NEWER, {
  company: { markdown: '## 主营业务\n商业银行存贷汇', facts: { employees: 60000 } },
  financial: { markdown: '## 收入\n利息净收入为主', facts: { revenue: 140000000000 } },
  forecast: { markdown: '## 预期\n两家机构给数', facts: { consensus_count: 2 } },
  valuation: { markdown: '## 估值\nPE 5.3', facts: { pe: 5.3 } },
  risk: { markdown: '## 风险\n质押比例 40%', facts: { pledge_ratio: 40 } },
  report: { markdown: '## 结论\n息差仍在收窄', facts: { report_name: '2026年三季报' } },
})

/** 旧的一次：没有盈利预期段，估值读数更低，风险里多一条这次已经不见的事实。 */
const OLDER_STATUS = statusWith(OLDER, {
  company: { markdown: '## 主营业务\n商业银行存贷汇', facts: { employees: 60000 } },
  financial: { markdown: '## 收入\n利息净收入为主', facts: { revenue: 140000000000 } },
  valuation: { markdown: '## 估值\nPE 5.1', facts: { pe: 5.1 } },
  risk: { markdown: '## 风险\n质押比例 40%\n一起未决诉讼', facts: { pledge_ratio: 40, lawsuit: '一起未决诉讼' } },
  report: { markdown: '## 结论\n息差收窄压力缓解', facts: { report_name: '2026年半年报' } },
})

const BOTH: Record<string, ResearchStatus> = { [NEWER]: NEWER_STATUS, [OLDER]: OLDER_STATUS }

function fakeSource(byDate: Record<string, ResearchStatus>): ResearchSource {
  return {
    status: vi.fn(async (code: string, date: string) => {
      const item = byDate[date]
      if (!item) throw new ApiError('parse', `${code} 在 ${date} 没有研究产物`)
      return item
    }),
    overview: vi.fn(async () => {
      throw new ApiError('not-connected', '本机 API 未连接')
    }),
    run: vi.fn(async () => {
      throw new ApiError('not-connected', '本机 API 未连接')
    }),
    job: vi.fn(async () => {
      throw new ApiError('not-connected', '本机 API 未连接')
    }),
  }
}

const EMPTY_OVERVIEW: ResearchOverview = {
  code: CODE,
  name: '浦发银行',
  as_of: '2026-11-02T15:05:09',
  dataSourceStatus: 'ok',
  kline: { bars: [], count: 0, requested: 250, reason: '本地缓存里没有日线' },
  quote: { quote: null, reason: '实时快照没有返回' },
  financials: { financials: null, reason: '概览里没有财务指标' },
}

const twoRuns = (target: string): Promise<ResearchRunList> =>
  Promise.resolve({ code: target, dates: [NEWER, OLDER], latest: NEWER, count: 2 })

function renderSection(options: {
  code?: string | null
  byDate?: Record<string, ResearchStatus>
  overview?: ResearchOverview | null
  demo?: boolean
  loader?: (code: string, signal?: AbortSignal) => Promise<ResearchRunList>
} = {}) {
  const { code = CODE, byDate = BOTH, overview = null, demo = false } = options
  // 演示模式不给 loader：让组件自己那条「用演示日期造两次」的分支跑起来
  const loader = options.loader ?? (demo ? undefined : twoRuns)
  return render(
    <CompareSection
      code={code}
      reportHref={`/research/${CODE}/${NEWER}`}
      source={fakeSource(byDate)}
      date={NEWER}
      demo={demo}
      overview={overview}
      name="浦发银行"
      runsLoader={loader}
    />,
  )
}

/** 段标题与节标题可能同名（估值段里就有「## 估值」一节），所以按「归类 + 段名」定位那一段。 */
function stageBlock(kind: string, title: string): HTMLElement {
  const heading = Array.from(document.querySelectorAll('h4')).find((h4) => {
    const text = h4.textContent ?? ''
    return text.includes(title) && text.includes(kind)
  })
  const node = heading?.closest('section')
  if (!node) throw new Error(`找不到段落：${kind} ${title}`)
  return node as HTMLElement
}

function cardText(): string {
  const entry = screen.getByText('文字版')
  return (entry.closest('details') as HTMLElement).textContent ?? ''
}

afterEach(() => {
  cleanup()
})

describe('未选标的', () => {
  it('两块都只提示先选标的，不猜任何一次产物', () => {
    renderSection({ code: null })
    expect(screen.getByText('先在上面选一只股票')).toBeTruthy()
    expect(screen.getByText('未选中标的')).toBeTruthy()
    expect(screen.queryByText(/六段合计/)).toBeNull()
    expect(document.querySelector('canvas')).toBeNull()
  })
})

describe('三态：接口没接 / 只有一次 / 有两次', () => {
  it('运行清单读不到 → 错误块点名接口，并给重试', async () => {
    renderSection({
      loader: () => Promise.reject(new ApiError('not-connected', '本机 API 未连接')),
    })

    expect(await screen.findByText('研究接口未接入')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
    // 取不到清单不能被读成「两次没有差异」
    expect(screen.queryByText(/六段合计/)).toBeNull()
  })

  it('重试真的再取一次清单，成功后进入对比', async () => {
    let fail = true
    const loader = vi.fn((target: string): Promise<ResearchRunList> =>
      fail
        ? Promise.reject(new ApiError('not-connected', '本机 API 未连接'))
        : Promise.resolve({ code: target, dates: [NEWER, OLDER], latest: NEWER, count: 2 }),
    )
    render(
      <CompareSection
        code={CODE}
        reportHref={null}
        source={fakeSource(BOTH)}
        date={NEWER}
        demo={false}
        overview={null}
        name={null}
        runsLoader={loader}
      />,
    )
    await screen.findByText('研究接口未接入')
    expect(loader).toHaveBeenCalledTimes(1)

    fail = false
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByText(/本机有 2 次研究产物/)
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('还只有一次研究 → 说「还只有这一份」，不说「没有变化」', async () => {
    renderSection({
      loader: (target) => Promise.resolve({ code: target, dates: [NEWER], latest: NEWER, count: 1 }),
    })

    const title = await screen.findByText(new RegExp(`还只有 ${NEWER} 这一份研究`))
    const card = title.closest('section') as HTMLElement
    expect(within(card).getByText(/再跑一次研究/)).toBeTruthy()
    expect(screen.queryByText(/六段合计/)).toBeNull()
    expect(screen.queryByText(/两次一致/)).toBeNull()
  })

  it('一次产物都没有 → 引导先跑研究，附可执行的命令', async () => {
    renderSection({
      loader: (target) => Promise.resolve({ code: target, dates: [], latest: null, count: 0 }),
    })
    expect(await screen.findByText(`${CODE} 还没有研究产物`)).toBeTruthy()
    expect(screen.getByText(/ldst research/)).toBeTruthy()
  })

  it('两次都有 → 按四类分开列，缺的一侧显示 —', async () => {
    renderSection()
    await screen.findByText(/六段合计/)

    // 盈利预期只有新的那次有 → 整段按「本次新增」处理，并点名旧的那次缺产物
    const forecast = stageBlock('本次新增', STAGE_TITLE.forecast)
    expect(within(forecast.querySelector('h4') as HTMLElement).getByText('本次新增')).toBeTruthy()
    expect(within(forecast).getByText(/旧的那次没有这一段产物/)).toBeTruthy()

    // 估值 5.1 → 5.3：是「有变化」，读数为「变了」
    const valuation = stageBlock('有变化', STAGE_TITLE.valuation)
    expect(within(valuation.querySelector('h4') as HTMLElement).getByText('有变化')).toBeTruthy()
    expect(within(valuation).getByText('变了')).toBeTruthy()

    // 风险里旧的一次多一条事实 → 单独标成「消失」，不混进噪音
    const risk = stageBlock('有变化', STAGE_TITLE.risk)
    expect(within(risk).getByText('消失')).toBeTruthy()
    expect(within(risk).getByText('—')).toBeTruthy()

    // 公司画像两次一致 → 说「没变」，不硬凑差异
    const company = stageBlock('没变', STAGE_TITLE.company)
    expect(within(company.querySelector('h4') as HTMLElement).getByText('没变')).toBeTruthy()
    expect(company.textContent).not.toContain('−')
  })

  it('只并列两次写了什么：页面上写着不解读、不重算', async () => {
    renderSection()
    await screen.findByText(/六段合计/)
    expect(screen.getByText(/不解读涨跌、不计算新指标/)).toBeTruthy()
    expect(screen.getByText(new RegExp(`旧 ${OLDER} → 新 ${NEWER}`))).toBeTruthy()
  })

  it('其中一次取不到 → 点名是哪一次没取到，只比取到的那一边', async () => {
    renderSection({ byDate: { [NEWER]: NEWER_STATUS } })
    expect(await screen.findByText(new RegExp(`${OLDER} 那一次没取到`))).toBeTruthy()
    expect(screen.getByText(/只比取到的那一边/)).toBeTruthy()
  })
})

describe('演示模式', () => {
  it('不读盘上清单：用演示日期与前一天造两次，并标注都来自演示数据', async () => {
    renderSection({
      demo: true,
      byDate: { [NEWER]: NEWER_STATUS, '2026-11-01': OLDER_STATUS },
    })
    await screen.findByText(/演示模式：两次都来自演示数据/)
    expect(screen.getByText(/六段合计/)).toBeTruthy()
    expect(screen.getByText(/旧 2026-11-01 → 新 2026-11-02/)).toBeTruthy()
  })
})

describe('分享卡', () => {
  it('概览没数据 → 不生成占位图，说明原因', async () => {
    renderSection({ overview: null })
    expect(await screen.findByText('分享卡还没有数据')).toBeTruthy()
    expect(document.querySelector('canvas')).toBeNull()
  })

  it('概览取不到字段 → 卡片显示 —，原因逐条列进「没取到的」', async () => {
    renderSection({ overview: EMPTY_OVERVIEW })
    await screen.findByText('文字版')
    const text = cardText()
    expect(text).toContain('现价：—')
    expect(text).toContain('没取到的：')
    expect(text).toContain('实时快照没有返回')
    expect(text).not.toMatch(/现价：0/)
    expect(document.querySelector('canvas')).not.toBeNull()
  })

  it('卡片里没有论点与持仓这些私有内容', async () => {
    renderSection({ overview: EMPTY_OVERVIEW })
    await screen.findByText('文字版')
    const text = cardText()
    for (const word of ['论点', '持仓', '成本价']) expect(text).not.toContain(word)
  })
})
