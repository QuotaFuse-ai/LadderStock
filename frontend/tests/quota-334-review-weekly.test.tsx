// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { setDemoPref } from '../src/lib/pageState'
import ReviewScreen from '../src/screens/ReviewScreen'
import WeeklyScreen from '../src/screens/WeeklyScreen'

/**
 * QUOT-337：`/review` 与 `/weekly` 的演示源（UI-SPEC §5.6 / §11.1 / §11.7）。
 *
 * 断言三件事：
 * 1. `?demo=1` 与设置开关两个入口都让两屏首屏就有内容，并出示「演示数据」徽标；
 * 2. 演示态关掉写入口——「重新生成 / 重试失败段」禁用且原因可读，不碰真实控制面；
 * 3. **关开关 + 真实请求失败时，页面不出现任何演示数据**（离线就是离线，不回落到 demo.ts）；
 * 4. `partial` / `empty` 变体的降级与缺失标注：缺什么写什么，不补 0。
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

describe('复盘 / 周度复盘演示源', () => {
  it('/review ?demo=1：首屏出指标与五段叙述，写入口禁用且原因可见', async () => {
    stubOffline()
    renderBoth('/review?demo=1&date=2026-10-30')

    expect(await screen.findByText('演示数据 · ?demo=1')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1, name: '收盘复盘' })).toBeTruthy()

    // 核心指标行 + 主面板首屏可见：三张指标卡来自同一份市场事实
    expect(screen.getByText('68')).toBeTruthy()
    expect(screen.getByText('32.6%')).toBeTruthy()
    expect(bodyText()).toContain('涨停 47 家')

    // 五段固定顺序与 AI 叙述（≥12 行内容的主体）
    expect(screen.getByText('情绪温度与赚钱效应')).toBeTruthy()
    expect(screen.getByText('人工智能仍是强度第一的主线，机器人、半导体紧随其后，三线同日共振是当日情绪上行的直接来源。')).toBeTruthy()

    // 演示态禁用控制面写入口，且给出可读原因（C-03 的禁用原因走 aria-describedby）
    const generate = screen.getByRole('button', { name: /重新生成|重试失败段/ })
    expect(generate.hasAttribute('disabled')).toBe(true)
    const describedBy = generate.getAttribute('aria-describedby')
    expect(describedBy).not.toBeNull()
    expect(document.getElementById(describedBy!)?.textContent).toContain('演示态不触发 AI 生成')

    // 「我的判断」不被演示数据预填、不写本机草稿
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

  it('partial / empty 两个变体：降级与缺失各有明确标注，不补 0', async () => {
    stubOffline()
    renderBoth('/review?demo=1&variant=partial&date=2026-10-30')
    await screen.findByText('演示数据 · ?demo=1')
    // §5.2：`degraded`=走了别的路 → 「已降级」warn；段级同时给出「降级完成」「失败」两档
    expect(bodyText()).toContain('已降级')
    expect(bodyText()).toContain('降级完成')
    expect(bodyText()).toContain('失败')

    cleanup()
    renderBoth('/weekly?demo=1&variant=partial')
    await screen.findByText('演示数据 · ?demo=1')
    // §5.2：`partial`=数据集缺项 → danger；缺的那段写原因，不画空图
    expect(bodyText()).toContain('部分缺失')
    expect(bodyText()).toContain('样本不足')

    cleanup()
    renderBoth('/weekly?demo=1&variant=empty')
    await screen.findByText('演示数据 · ?demo=1')
    // empty 分支仍是「头部 + 指标卡阵」，卡片值一律 `—` 而不是 0
    expect(bodyText()).toContain('还没有可用周报')
    expect(screen.getByText('温度均值')).toBeTruthy()
  })

  it('关闭开关 + 真实请求失败：两屏都不出现任何演示数据', async () => {
    stubOffline()

    renderBoth('/review')
    await screen.findByRole('heading', { level: 1, name: '收盘复盘' })
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
    await screen.findByRole('heading', { level: 1, name: '周度复盘' })
    await waitFor(() => {
      expect(bodyText()).toContain('服务未连接')
    })
    for (const sentinel of DEMO_SENTINELS) {
      expect(bodyText()).not.toContain(sentinel)
    }
  })
})
