// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { setDemoPref } from '../src/lib/pageState'
import ReviewScreen from '../src/screens/ReviewScreen'
import WeeklyScreen from '../src/screens/WeeklyScreen'

/**
 * QUOT-337 / QUOT-344-S2：`/review`（数据面）与 `/weekly` 的演示源（UI-SPEC §5.6 / §11.1 / §11.7）。
 *
 * 断言四件事：
 * 1. `?demo=1` 与设置开关两个入口都让两屏首屏就有内容，并出示「演示数据」徽标；
 * 2. `/review` 只有程序指标 +「我的判断」：AI 段落、模型名、段级成败、生成入口一个都不在本屏
 *    （DEC-049 §2 的边界，模型面在 `/review/ai`，另有用例）；
 * 3. **关开关 + 真实请求失败时，页面不出现任何演示数据**（离线就是离线，不回落到 demo.ts）；
 * 4. `partial` / `empty` 变体的缺失标注：缺什么写什么，不补 0、不显示 AI 的降级/失败字样。
 *
 * 演示事实基线与 `/limit-up` 等屏同一份：交易日 2026-10-30、情绪温度 68、涨停 47 家、最高 5 板。
 */

const DEMO_SENTINELS = [
  '演示数据',
  '科大讯飞',
  '人工智能仍是强度第一的主线',
  '一周龙头与梯队',
  '情绪偏暖且较昨日继续抬升',
]

/** 只属于模型面的哨兵：数据面出现任何一条就是两屏重叠（验收项「三屏内容互不重叠」）。 */
const MODEL_ONLY = ['deepseek-v3', '已完成', '降级完成', '重跑该段']

afterEach(() => {
  cleanup()
  setDemoPref(false)
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function stubOffline() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }),
  )
}

function renderBoth(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/review" element={<ReviewScreen />} />
        <Route path="/weekly" element={<WeeklyScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

function bodyText(): string {
  return document.body.textContent ?? ''
}

/** 段内程序指标行：`dt` 标签 + 同排 `dd` 取值（`review/SectionMetrics.tsx`）。按段落定位，因为「最高板」「断层档位」这类 key 多段共有。 */
function metricRow(sectionName: string, label: string): string {
  const block = document.getElementById(`review-section-${sectionName}`)
  if (!block) throw new Error(`页面上没有 ${sectionName} 段`)
  const dt = Array.from(block.querySelectorAll('dt')).find((node) => node.textContent === label)
  const dd = dt?.parentElement?.querySelector('dd')
  if (!dd) throw new Error(`${sectionName} 段没有指标行「${label}」`)
  return dd.textContent ?? ''
}

describe('每日复盘（数据面）/ 周度复盘演示源', () => {
  it('/review ?demo=1：指标卡与五段程序指标首屏可见，模型内容一律不在本屏', async () => {
    stubOffline()
    renderBoth('/review?demo=1&date=2026-10-30')

    expect(await screen.findByText('演示数据 · ?demo=1')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1, name: '每日复盘' })).toBeTruthy()

    // 三张指标卡与段内指标同口径：同一个读数在两处必须相等（68 / 32.6% 各出现两次）
    expect(screen.getAllByText('68')).toHaveLength(2)
    expect(screen.getAllByText('32.6%')).toHaveLength(2)
    expect(bodyText()).toContain('涨停 47 家')

    // 五段固定顺序 + 段内程序指标（与指标卡同口径，不是 AI 复述）
    expect(screen.getByText('情绪温度与赚钱效应')).toBeTruthy()
    expect(metricRow('sentiment', '封板率')).toBe('85.5%')
    expect(metricRow('sentiment', '断层档位')).toBe('无断层')
    expect(metricRow('dragon_tiger', '上榜个股')).toBe('38')

    // 「我的判断」归数据面：五段可编辑草稿（DEC-049 §3 的裁定）
    expect(document.querySelectorAll('textarea')).toHaveLength(5)
    expect(screen.getByRole('textbox', { name: '我的判断 · 情绪' })).toBeTruthy()

    for (const sentinel of MODEL_ONLY) {
      expect(bodyText()).not.toContain(sentinel)
    }
    expect(screen.queryByRole('button', { name: /重新生成|重试失败段/ })).toBeNull()
    expect(bodyText()).not.toContain('人工智能仍是强度第一的主线')

    // 演示态「我的判断」既不预填也不写本机草稿（隐私内容不靠演示数据冒充）
    expect(screen.getByRole('textbox', { name: '我的判断 · 情绪' }).hasAttribute('readonly')).toBe(true)
    expect(bodyText()).not.toContain('已恢复')
  })

  it('/weekly 设置开关：不写 URL 也出周度事实与每日序列，徽标标注来源是设置开关', async () => {
    stubOffline()
    setDemoPref(true)
    renderBoth('/weekly')

    expect(await screen.findByText('演示数据 · 设置开关')).toBeTruthy()
    expect(screen.queryByText('演示数据 · ?demo=1')).toBeNull()
    expect(bodyText()).toContain('2026-W44 · 2026-10-26 ~ 2026-11-01 · 5 个交易日有数据')

    // empty 分支同构的指标卡阵 + 每日序列 5 行（行高沿用 h-row）
    expect(screen.getByText('温度均值')).toBeTruthy()
    const table = document.querySelector('table')
    expect(table).not.toBeNull()
    expect(table?.querySelectorAll('tbody tr').length).toBe(5)
    expect(table?.textContent).toContain('10-30')
    expect(bodyText()).toContain('科大讯飞')
    // 末日与复盘页同一份事实
    expect(bodyText()).toContain('68')
  })

  it('partial / empty 两个变体：缺数段落逐段标注、计数按 — 显示，不补 0 也不借模型的词', async () => {
    stubOffline()
    renderBoth('/review?demo=1&variant=partial&date=2026-10-30')
    await screen.findByText('演示数据 · ?demo=1')
    // 数据面的 partial = 某段数据集没落盘，与段级 AI 成败是两件事（§5.2）
    // 演示源是异步 load：只等徽标会在慢机上抢跑（CI 实测），同下方周度分支
    await waitFor(() => expect(bodyText()).toContain('五段中 1 段数据缺失'))
    expect(screen.getByText('缺数')).toBeTruthy()
    expect(metricRow('dragon_tiger', '上榜个股')).toBe('—')
    expect(metricRow('dragon_tiger', '席位行数')).toBe('—')
    expect(bodyText()).toContain('本段数据缺失：本地缓存没有龙虎榜数据')
    // 段级降级/失败属于模型面，本屏不出现
    for (const sentinel of MODEL_ONLY) {
      expect(bodyText()).not.toContain(sentinel)
    }
    expect(bodyText()).not.toContain('重新生成')

    cleanup()
    renderBoth('/weekly?demo=1&variant=partial')
    await screen.findByText('演示数据 · ?demo=1')
    // 演示源是异步 load：必须等变体内容落地，只等徽标会在慢机上抢跑（CI 实测）
    await waitFor(() => expect(bodyText()).toContain('部分缺失'))
    expect(bodyText()).toContain('样本不足')

    cleanup()
    renderBoth('/weekly?demo=1&variant=empty')
    await screen.findByText('演示数据 · ?demo=1')
    // empty 分支仍是「头部 + 指标卡阵」，卡片值一律 `—` 而不是 0
    await waitFor(() => expect(bodyText()).toContain('还没有可用周报'))
    expect(screen.getByText('温度均值')).toBeTruthy()
  })

  it('关闭开关 + 真实请求失败：两屏都不出现任何演示数据', async () => {
    stubOffline()

    renderBoth('/review')
    await screen.findByRole('heading', { level: 1, name: '每日复盘' })
    await waitFor(() => {
      expect(bodyText()).toContain('服务未连接')
    })
    for (const sentinel of DEMO_SENTINELS) {
      expect(bodyText()).not.toContain(sentinel)
    }
    // 缺失值一律「—」，不用演示数字或 0 顶替
    expect(bodyText()).toContain('—')

    cleanup()
    renderBoth('/weekly')
    await screen.findByRole('heading', { level: 1, name: 'AI周报' })
    await waitFor(() => {
      expect(bodyText()).toContain('服务未连接')
    })
    for (const sentinel of DEMO_SENTINELS) {
      expect(bodyText()).not.toContain(sentinel)
    }
  })
})
