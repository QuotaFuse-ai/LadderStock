// @vitest-environment jsdom
/**
 * QUOT-336：首页 / 盯盘的演示源 + 首屏密度。
 *
 * 核心验收口径（UI-SPEC §5.6 / §11.2）：
 * - `?demo=1` 与「设置 → 虚拟数据」两个入口都要让首屏真的出内容；
 * - **真实请求 404 / 断连只进空态 / 离线态，绝不回落到 demo.ts**（正向断言，不是「没测到」）；
 * - 演示行不写 `watch_snapshot` 分片，本机回放口径（TTL / 交易日 / 时钟漂移）一条旧值都不出。
 */

import 'fake-indexeddb/auto'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import HomeScreen from '../src/screens/HomeScreen'
import WatchScreen from '../src/screens/WatchScreen'
import { KV_STORE, closeStateDatabase, idbDelete, idbGet } from '../persist/indexeddb'
import { localDateIso } from '../src/lib/format'
import { DEMO_DATA_STORAGE_KEY, setDemoPref } from '../src/lib/pageState'
import { useWatchSnapshot, WATCH_SNAPSHOT_TTL_SEC } from '../stores/watchSnapshot'
import { DEMO_TRADE_DATE } from '../src/screens/watch/demo'

const STALE_SEC = WATCH_SNAPSHOT_TTL_SEC + 120

async function waitFlush(ms = 120): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

/** 真实通道：404（服务在跑，但这一天 / 这个数据集没有）。 */
function stub404(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'dataset not found' }),
    })),
  )
}

/** 真实通道：连不上本机服务（`fetch` 直接抛）。 */
function stubDisconnected(): void {
  vi.stubGlobal('fetch', vi.fn(async () => {
    throw new TypeError('offline')
  }))
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      {path.startsWith('/watch') ? <WatchScreen /> : <HomeScreen />}
    </MemoryRouter>,
  )
}

/** 数据行：空态 / 错误态是单个跨列 `<td colSpan>` 的提示行，不算数据行。 */
function dataRowCount(): number {
  return Array.from(document.querySelectorAll('tbody tr')).filter((row) => row.querySelectorAll('td').length > 1).length
}

function rowByText(text: string): HTMLTableRowElement | undefined {
  return Array.from(document.querySelectorAll<HTMLTableRowElement>('tbody tr')).find((row) =>
    row.textContent?.includes(text),
  )
}

/** L0 四格：按 `<dt>` 找同格的 `<dd>`，缺值口径要落在 `dd` 上。 */
function fieldOf(label: string): string {
  const dt = Array.from(document.querySelectorAll('dt')).find((item) => item.textContent === label)
  const dd = dt?.parentElement?.querySelector('dd')
  return dd?.textContent ?? ''
}

beforeEach(async () => {
  stubDisconnected()
  setDemoPref(false)
  window.localStorage.clear()
  useWatchSnapshot.setState({ tradeDate: null, fetchedAt: null, sourceDetail: null, rows: [], updatedAt: null })
  await waitFlush()
  await closeStateDatabase()
  await idbDelete(KV_STORE, 'watch_snapshot')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setDemoPref(false)
  window.localStorage.clear()
})

describe('首页演示源', () => {
  it('?demo=1 首屏直接出内容：四块都在，交易日跟随演示基准日', async () => {
    renderAt('/?demo=1')

    expect(await screen.findByText('演示数据')).toBeTruthy()
    // QUOT-559：来源细节从徽标文本移到 hover title
    expect(screen.getByText('演示数据').closest('[title]')?.getAttribute('title')).toContain('网址开关')
    // A 块指数条、C 块自选、D 块持仓都落到首屏（v2 已无 5 卡阵与连板梯队表）
    expect(await screen.findByText('上证指数')).toBeTruthy()
    expect(await screen.findByText('贵州茅台')).toBeTruthy()
    await waitFor(() => expect(dataRowCount()).toBeGreaterThan(0))
    // 演示态根本不碰网络：fetch 全程是断连桩，首屏照样有内容
    const calls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
    const apiCalls = calls.filter(([url]) => String(url).includes('/api/'))
    expect(apiCalls).toHaveLength(0)
  })

  it('「设置 → 虚拟数据」开关（不写 URL）同样出内容', async () => {
    setDemoPref(true)
    renderAt('/')

    expect(await screen.findByText('演示数据')).toBeTruthy()
    expect(screen.getByText('演示数据').closest('[title]')?.getAttribute('title')).toContain('设置页')
    expect(await screen.findByText('上证指数')).toBeTruthy()
    await waitFor(() => expect(dataRowCount()).toBeGreaterThan(0))
  })

  it('五块按 SPEC-HOME-V2 v2.0 顺序装配，且旧三块不再出现', async () => {
    renderAt('/?demo=1')
    await screen.findByText('上证指数')

    // QUOT-552 两行工作台：row1 大盘与全球指数 → 待办与异动（合并表）；
    // row2 我的自选 → 我的持仓 → 今天，想研究什么（研究面板）。DOM 顺序即单栏保底顺序。
    const headings = ['大盘与全球指数', '待办与异动', '我的自选', '我的持仓', '今天，想研究什么？'].map((text) =>
      document.body.textContent?.indexOf(text) ?? -1,
    )
    headings.forEach((index) => expect(index).toBeGreaterThan(-1))
    expect(headings).toEqual([...headings].sort((a, b) => a - b))

    // 移除项（§1.3）：情绪温度条 / 5 张指标卡 / 连板梯队 Top 10 / 题材强度 Top 5 一律不在首页 DOM
    expect(document.body.textContent).not.toContain('连板梯队 Top 10')
    expect(document.body.textContent).not.toContain('题材强度 Top 5')
    expect(document.body.textContent).not.toContain('情绪温度')
  })

  it('真实 404：进错误态，绝不回落演示数据', async () => {
    stub404()
    renderAt('/')

    expect(await screen.findAllByText('取数失败')).toBeTruthy()
    expect(document.body.textContent).not.toContain('贵州茅台')
    expect(document.body.textContent).not.toContain('上证指数')
    expect(dataRowCount()).toBe(0)
  })

  it('断连：进离线态，绝不出现演示行', async () => {
    stubDisconnected()
    renderAt('/')

    expect((await screen.findAllByText('本地服务未连接')).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toContain('宁德时代')
    expect(document.body.textContent).not.toContain('演示数据')
    expect(document.body.textContent).not.toContain('上证指数')
  })

  it('演示源 empty 变体：明确空态 + 引导，而不是整屏「—」', async () => {
    renderAt('/?demo=1&variant=empty')

    expect(await screen.findByText(/没有可用数据/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '去行情页同步' })).toBeTruthy()
    expect(document.body.textContent).not.toContain('上证指数')
    expect(dataRowCount()).toBe(0)
  })
})

describe('盯盘演示源', () => {
  it('?demo=1 一屏 ≥12 行，覆盖触发 / 未触发 / 停牌多态', async () => {
    renderAt('/watch?demo=1')

    expect(await screen.findByText('演示数据')).toBeTruthy()
    // QUOT-559：来源细节从徽标文本移到 hover title
    expect(screen.getByText('演示数据').closest('[title]')?.getAttribute('title')).toContain('网址开关')
    await waitFor(() => expect(dataRowCount()).toBeGreaterThanOrEqual(12))
    expect(screen.getByText('比亚迪')).toBeTruthy()
    expect(screen.getByText('隆基绿能')).toBeTruthy()

    // 封板 / 开板行的触发值是价格不是涨跌幅 → 「—」，不填 0
    const sealRow = rowByText('688981')
    expect(sealRow?.textContent).toContain('—')
    expect(sealRow?.textContent).not.toContain('0.00%')
    // 名称没有来源就是「—」，不拿代码顶上
    const unnamedRow = rowByText('002460')
    expect(unnamedRow?.textContent).toContain('—')
  })

  /**
   * 表高口径（UI-SPEC §7.3）：具名值 `calc(100vh - 280px)`，不是 `420px` 字面量。
   * jsdom 不排版，所以这里锁的是口径本身——14 行是否真进首屏由 1440×900 取证图与探针实测负责。
   */
  it('信号流表高走 §7.3 具名档位，不回落 420px 字面量', async () => {
    renderAt('/watch?demo=1')
    await waitFor(() => expect(dataRowCount()).toBeGreaterThanOrEqual(12))

    const scroller = document.querySelector('table')?.closest('.overflow-auto') as HTMLElement | null
    expect(scroller?.style.maxHeight).toBe('calc(100vh - 280px)')
  })

  it('演示态本机没有回放缓存：剩余 TTL 如实「—」，且不写 watch_snapshot 分片', async () => {
    renderAt('/watch?demo=1')
    await waitFor(() => expect(dataRowCount()).toBeGreaterThanOrEqual(12))

    expect(fieldOf('剩余 TTL')).toContain('—')
    expect(fieldOf('交易日')).toContain(DEMO_TRADE_DATE)

    await waitFlush()
    expect(useWatchSnapshot.getState().rows).toEqual([])
    expect(await idbGet<string>(KV_STORE, 'watch_snapshot')).toBeUndefined()
  })

  it('真实 404：错误态，绝不出现演示行', async () => {
    stub404()
    renderAt('/watch')

    expect(await screen.findAllByText('取数失败')).toBeTruthy()
    expect(document.body.textContent).not.toContain('比亚迪')
    expect(dataRowCount()).toBe(0)
  })

  it('真实断连：离线空态说清「本机没有可回放的信号」，绝不出现演示行', async () => {
    stubDisconnected()
    renderAt('/watch')

    expect(await screen.findByText('离线 · 本机没有可回放的信号')).toBeTruthy()
    expect(document.body.textContent).not.toContain('隆基绿能')
    expect(dataRowCount()).toBe(0)
  })

  it('真实快照超过 TTL：一条旧值都不渲染（§11.2 零旧值）', async () => {
    stub404()
    useWatchSnapshot.setState({
      tradeDate: localDateIso(),
      fetchedAt: new Date(Date.now() - STALE_SEC * 1000).toISOString(),
      sourceDetail: 'watch/status.py: 本机状态文件',
      rows: [
        { code: '002594', name: '旧值行', limitUpCount: 3, changePct: 6.85, status: '3 分钟拉升' },
      ],
      updatedAt: null,
    })

    // 后端返回「没有可用快照」，本机只剩超时的旧行
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          domain: 'sentiment',
          topic: 'watch_snapshot',
          event_id: null,
          data_as_of: null,
          payload: { trade_date: null, rows: [], data_source_status: 'missing' },
          source: 'ladderstock',
          source_detail: 'watch/status.py: 未找到状态文件',
          fetched_at: new Date().toISOString(),
          freshness: 'cached',
          confidence: 'low',
          source_status: 'partial',
        }),
      })),
    )

    renderAt('/watch')

    await waitFor(() => expect(screen.getByText('暂无可用信号')).toBeTruthy())
    expect(document.body.textContent).not.toContain('旧值行')
    expect(dataRowCount()).toBe(0)
  })

  it('设置开关口径与 URL 一致', async () => {
    setDemoPref(true)
    renderAt('/watch')

    expect(await screen.findByText('演示数据')).toBeTruthy()
    expect(screen.getByText('演示数据').closest('[title]')?.getAttribute('title')).toContain('设置页')
    await waitFor(() => expect(dataRowCount()).toBeGreaterThanOrEqual(12))
    expect(window.localStorage.getItem(DEMO_DATA_STORAGE_KEY)).toBe('1')
  })
})
