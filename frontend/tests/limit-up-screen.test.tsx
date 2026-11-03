// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LimitUpScreen from '../src/screens/limitup/LimitUpScreen'

/**
 * 涨停样本 `/limit-up`：五态齐备 + 连板排序 + 缺失值/接口未接入表现。
 * 演示源 `?demo=1` 固定在 2026-10-30；强制态走 `?state=`。
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/limit-up" element={<LimitUpScreen />} />
      </Routes>
    </MemoryRouter>,
  )
}

function dataRows(): HTMLElement[] {
  const table = screen.getByRole('table')
  return within(table).getAllByRole('row').slice(1)
}

describe('涨停样本五态', () => {
  it('normal / loading / empty / error / partial / offline 各一条', async () => {
    const normal = renderAt('/limit-up?demo=1')
    expect(await screen.findByRole('heading', { name: '涨停样本', level: 1 })).toBeTruthy()
    expect(screen.queryByText(/演示态：/)).toBeNull()
    normal.unmount()

    const loading = renderAt('/limit-up?demo=1&state=loading')
    expect(await waitFor(() => document.querySelector('[aria-busy="true"]'))).toBeTruthy()
    expect(screen.getByText('演示态：加载中（?state=loading）')).toBeTruthy()
    loading.unmount()

    const empty = renderAt('/limit-up?demo=1&state=empty')
    expect(await screen.findByText('当日涨停池未同步')).toBeTruthy()
    expect(screen.queryByText('暂无数据')).toBeNull()
    empty.unmount()

    const error = renderAt('/limit-up?demo=1&state=error')
    expect(await screen.findByText('涨停样本取数失败')).toBeTruthy()
    error.unmount()

    const partial = renderAt('/limit-up?demo=1&state=partial')
    expect(await screen.findByText('部分涨停池数据缺失')).toBeTruthy()
    expect(within(screen.getByRole('table')).getAllByRole('row').length).toBeGreaterThan(1)
    partial.unmount()

    const offline = renderAt('/limit-up?demo=1&state=offline')
    expect((await screen.findAllByText('本机服务未连接')).length).toBeGreaterThan(0)
    expect(screen.getByText('当日涨停池未同步')).toBeTruthy()
    offline.unmount()
  })

  it('深链 ?date= 刷新后复现同一视图', async () => {
    renderAt('/limit-up?demo=1&date=2026-10-29')
    expect(await screen.findByText(/2026-10-29/)).toBeTruthy()
  })
})

describe('涨停样本表格', () => {
  it('默认按连板数降序，点击表头切换为升序', async () => {
    renderAt('/limit-up?demo=1')
    await screen.findByRole('heading', { name: '涨停样本', level: 1 })

    expect(within(dataRows()[0]).getByText('002594')).toBeTruthy()

    fireEvent.click(within(screen.getByRole('table')).getByRole('button', { name: /连板数/ }))
    await waitFor(() => {
      const codes = dataRows().map((row) => row.textContent ?? '')
      expect(codes.findIndex((text) => text.includes('300750'))).toBeLessThan(
        codes.findIndex((text) => text.includes('002594')),
      )
    })
    expect(screen.getByRole('columnheader', { name: /连板数/ }).getAttribute('aria-sort')).toBe('ascending')
  })

  it('缺失值显示「—」+ 原因，不补 0；封板时间格式化为 HH:mm:ss', async () => {
    renderAt('/limit-up?demo=1')
    await screen.findByRole('heading', { name: '涨停样本', level: 1 })

    const maotai = dataRows().find((row) => within(row).queryByText('600519'))
    expect(maotai).toBeTruthy()
    expect(within(maotai!).getAllByText('—')).toHaveLength(4)
    expect(screen.getByTitle('后端未提供连板数')).toBeTruthy()
    expect(screen.getByTitle('后端未提供首次封板时间')).toBeTruthy()
    expect(screen.getAllByTitle('后端未提供末次封板时间')).toHaveLength(2)
    expect(screen.getByTitle('后端未提供涨停原因')).toBeTruthy()

    // UI-SPEC v1.1 §8 / §3.15：后端裸 HHMMSS 必须格式化成 HH:mm:ss（同 §11.15 第一条）。
    expect(screen.getByText('09:30:15')).toBeTruthy()
    expect(screen.getByText('14:30:01')).toBeTruthy()
  })
})

describe('真实通道', () => {
  it('接口 404 按「数据源暂缺」进空态，不回落演示数据', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })),
    )
    renderAt('/limit-up')

    expect(await screen.findByText('当日涨停池未同步')).toBeTruthy()
    expect(screen.getByText(/数据源暂缺/)).toBeTruthy()
    expect(screen.queryByText('浦发银行')).toBeNull()
    expect(screen.queryByText('涨停样本取数失败')).toBeNull()
  })

  it('服务未连接进离线态，不是整屏报错', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    renderAt('/limit-up')

    expect((await screen.findAllByText('本机服务未连接')).length).toBeGreaterThan(0)
    expect(screen.getByText('当日涨停池未同步')).toBeTruthy()
    expect(screen.queryByText('涨停样本取数失败')).toBeNull()
  })
})
