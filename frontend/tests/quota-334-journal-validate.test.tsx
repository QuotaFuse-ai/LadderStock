// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import JournalScreen from '../src/screens/JournalScreen'
import ValidateScreen from '../src/screens/ValidateScreen'
import { resetValidationSource, setValidationSource } from '../stores/validationMode'
import { setDemoPref } from '../src/lib/pageState'

/**
 * QUOT-338：`/journal` 与 `/validate` 的演示源（UI-SPEC §11.4 / §11.3 / §5.6）。
 *
 * 两条主线各一条正例 + 一条**反例**：
 * - 正例：`?demo=1` 首屏就有一张 ≥12 行的表，并亮「演示数据」徽标；
 * - 反例：关掉开关、真实请求 404 / 断连时，页面进空态 / 离线态，
 *   **屏幕上不出现夹具里的任何一个数字**（这是本任务唯一真正危险的失败模式：
 *   把演示样本当成自己的成交统计或对账条件）。
 *
 * 演示基准交易日固定 `2026-10-30`（同 `limitup/demo.ts`），夹具数字见各屏 `demo.ts`。
 */

afterEach(() => {
  cleanup()
  setDemoPref(false)
  resetValidationSource()
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderRoute(path: string, element: JSX.Element) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path.split('?')[0]} element={element} />
      </Routes>
    </MemoryRouter>,
  )
}

/** 记录每一次真实请求的 URL：用来证明演示态既不发取数请求，也不探测控制面。 */
function stubFetchFailure(urls: string[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown) => {
      urls.push(String(typeof input === 'object' && input && 'url' in input ? (input as Request).url : input))
      throw new TypeError('Failed to fetch')
    }),
  )
}

describe('交易日志演示源', () => {
  it('?demo=1 首屏就是 13 行聚合表，夹具数字自洽且不发真实请求', async () => {
    const urls: string[] = []
    stubFetchFailure(urls)

    renderRoute('/journal?demo=1', <JournalScreen />)

    expect(await screen.findByRole('heading', { name: '交易日志', level: 1 })).toBeTruthy()
    expect(screen.getByText('演示数据 · ?demo=1')).toBeTruthy()
    expect(screen.getByText('本机私有 · 当前是演示样本')).toBeTruthy()

    const table = screen.getByRole('table')
    const bodyRows = within(table).getAllByRole('row').slice(1)
    expect(bodyRows.length).toBeGreaterThanOrEqual(12)

    // 夹具的自洽性直接断言：14 买 + 10 卖 = 24 笔，盈 6 / 亏 3 / 平 1 = 10 个闭环。
    expect(within(table).getByText('成交笔数')).toBeTruthy()
    expect(within(bodyRows.find((row) => row.textContent?.includes('成交笔数'))!).getByText('24')).toBeTruthy()
    expect(within(bodyRows.find((row) => row.textContent?.includes('盈 / 亏 / 平'))!).getByText('6 / 3 / 1')).toBeTruthy()
    expect(table.textContent).toContain('2026-10-01 ~ 2026-10-30')
    expect(urls).toHaveLength(0)
  })

  it('开关关闭 + 真实请求断连：进离线态，屏幕上一个夹具数字都不出现', async () => {
    const urls: string[] = []
    stubFetchFailure(urls)

    renderRoute('/journal', <JournalScreen />)

    expect((await screen.findAllByText(/本机服务未连接/)).length).toBeGreaterThan(0)
    expect(urls.some((url) => url.includes('/api/journal/stats'))).toBe(true)
    expect(screen.queryByText('演示数据 · ?demo=1')).toBeNull()
    expect(screen.queryByText('演示数据 · 设置开关')).toBeNull()
    expect(document.body.textContent).not.toContain('6 / 3 / 1')
    expect(document.body.textContent).not.toContain('演示样本')
    expect(document.body.textContent).not.toContain('2026-10-01 ~ 2026-10-30')
  })

  it('设置页「虚拟数据」开关开启也走演示源（不写 URL），徽标标注来源是开关', async () => {
    const urls: string[] = []
    stubFetchFailure(urls)
    setDemoPref(true)

    renderRoute('/journal', <JournalScreen />)

    expect(await screen.findByText('演示数据 · 设置开关')).toBeTruthy()
    expect(within(screen.getByRole('table')).getAllByRole('row').length - 1).toBeGreaterThanOrEqual(12)
    expect(urls).toHaveLength(0)
  })
})

describe('明日验证演示源', () => {
  it('?demo=1 条件表首屏 ≥12 行，写入与探针全部禁用', async () => {
    const urls: string[] = []
    stubFetchFailure(urls)

    renderRoute('/validate?demo=1', <ValidateScreen />)

    expect(await screen.findByRole('heading', { name: '明日验证', level: 1 })).toBeTruthy()
    expect(screen.getByText('演示数据 · ?demo=1')).toBeTruthy()
    expect(screen.getByText('演示样本 · 本页条件不是你写的')).toBeTruthy()

    // 条件表是 §7.3 的 div 网格（不是 `<table>`，见 check:ui 的 bare-table 口径），
    // 每行必有一个「仓位上限 = —」的结构格，用它数行数最稳。
    const positionCells = screen.getAllByTitle(
      '草稿结构（stores/validationDraft.ts）不记录仓位上限，用备注承载',
    )
    expect(positionCells.length).toBeGreaterThanOrEqual(12)
    expect(screen.getByText('12 条')).toBeTruthy()
    expect(document.body.textContent).toContain('明日情绪温度不低于 53.0（今日 68.0）')

    // 演示态禁用写入：新增 / 保存都在禁用态且给出理由（§4.8）。
    // 同一条理由会出现多次（工具栏说明 + 每个被禁用按钮的 `aria-describedby`），只要有一次都不算「静默置灰」。
    const add = screen.getByRole('button', { name: /新增条件/ }) as HTMLButtonElement
    expect(add.disabled).toBe(true)
    expect(screen.getAllByText('演示态不写控制面，也不落本机草稿').length).toBeGreaterThan(0)
    expect(add.getAttribute('aria-describedby')).toBeTruthy()
    const save = screen.getByRole('button', { name: /保存到后端/ }) as HTMLButtonElement
    expect(save.disabled).toBe(true)

    // 夹具不发任何真实请求：既不取 `/api/validation`，也不探写通道与 `/api/health`。
    expect(urls).toHaveLength(0)
  })

  it('对账视图：命中/未命中来自夹具判定，指标名不直显后端 key', async () => {
    const urls: string[] = []
    stubFetchFailure(urls)

    renderRoute('/validate?demo=1', <ValidateScreen />)
    await screen.findByRole('heading', { name: '明日验证', level: 1 })

    fireEvent.click(screen.getByRole('radio', { name: '收盘对账' }))

    const table = await screen.findByRole('table')
    expect(within(table).getByText('情绪温度')).toBeTruthy()
    expect(within(table).getByText('晋级率')).toBeTruthy()
    // §11.0「后端 key 不直显」：`temperature` / `seal_rate` 只出现在 hover 的 title 里。
    expect(within(table).queryByText('temperature')).toBeNull()
    expect(within(table).getAllByText('命中')).toHaveLength(3)
    expect(within(table).getAllByText('未命中')).toHaveLength(1)
    expect(document.body.textContent).toContain('下一交易日 2026-11-02')
    expect(urls).toHaveLength(0)
  })

  it('关开关 + 真实请求失败：离线态里没有任何演示条件，也不显示演示徽标', async () => {
    const urls: string[] = []
    stubFetchFailure(urls)
    setValidationSource('backend')

    renderRoute('/validate', <ValidateScreen />)

    await waitFor(() => expect(urls.some((url) => url.includes('/api/validation'))).toBe(true))
    expect((await screen.findAllByText(/本机服务未连接/)).length).toBeGreaterThan(0)
    expect(screen.queryByText('演示数据 · ?demo=1')).toBeNull()
    expect(document.body.textContent).not.toContain('明日情绪温度不低于')
    expect(document.body.textContent).not.toContain('演示样本')
  })

  it('partial 档（退出码 8）黄条给出后端原因与数据集，不下结论', async () => {
    const urls: string[] = []
    stubFetchFailure(urls)

    renderRoute('/validate?demo=1&state=partial', <ValidateScreen />)
    await screen.findByRole('heading', { name: '明日验证', level: 1 })

    fireEvent.click(screen.getByRole('radio', { name: '收盘对账' }))

    expect(await screen.findByText('本地还没有下一交易日的涨停池数据，无法对账')).toBeTruthy()
    expect(screen.getByText(/对账 partial/)).toBeTruthy()
    expect(screen.getByText('本次没有生成逐条判定')).toBeTruthy()
    expect(urls).toHaveLength(0)
  })
})
