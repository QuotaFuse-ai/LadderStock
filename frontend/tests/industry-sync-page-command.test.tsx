// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobView, PageCommand } from '../src/api'
import PageCommandBar from '../src/components/PageCommandBar'

/**
 * QUOT-538：同一路由两条命令（只读重算 + 联网同步）必须都列出，各自独立展示状态。
 * 带 `note` 的命令点「运行」先出二次确认，不直接提交。
 */

function json(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

const NOTE = '联网同步（成本非 0）：单数据集上限 100 只，不做全市场拉取。'

const readonly: PageCommand = {
  key: 'industry',
  route: '/industry',
  label: '重算行业快照',
  argv: ['industry', 'list'],
  running: false,
  last_job: null,
}

const sync: PageCommand = {
  key: 'industry-sync',
  route: '/industry',
  label: '同步补齐行业数据',
  argv: ['sync', 'industry'],
  running: false,
  last_job: null,
  note: NOTE,
}

const queued: JobView = {
  id: 'job-9',
  peer: 'ldst',
  action: 'command',
  status: 'queued',
  step: '已排队，等待后端线程池',
  progress: 0,
  started_at: '2026-11-10T17:40:00Z',
  ended_at: null,
  log_tail: [],
  error: null,
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/api/control/commands')) return json({ ok: true, commands: [readonly, sync] })
    if (url.includes('/api/control/commands/') && init?.method === 'POST') {
      return json({ job_id: 'job-9', status: 'queued' })
    }
    if (url.includes('/api/control/jobs/')) return json(queued)
    return json({ detail: 'Not Found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderBar(path = '/industry') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PageCommandBar />
    </MemoryRouter>,
  )
}

/**
 * 与 page-command-bar.test.tsx 的 openPanel 同口径：click 前空跑一次 act，把首次
 * commands 到达那次提交挂起的 passive effect（路由态复位）落到点击之前。真实浏览器
 * 里 passive effect 先于下一次用户事件冲刷，用户点不到这个窗口；测试在 waitFor 轮询
 * 里紧接着 click 才会把迟到的 setOpen(false) 挂进 click 的 act，关掉刚开的面板
 * （CI 慢机复现为 dialog 不出现）。空跑 act 是恢复点击语义，不是放宽断言。
 */
async function openPanel() {
  await waitFor(() => expect(screen.getByRole('button', { name: '本页数据同步' })).toBeTruthy())
  await act(async () => {})
  fireEvent.click(screen.getByRole('button', { name: '本页数据同步' }))
}

describe('本页数据同步：同路由两条命令（QUOT-538）', () => {
  it('两条命令都渲染，note 原样展示', async () => {
    renderBar()
    await openPanel()

    const panel = screen.getByRole('dialog', { name: '本页数据同步' })
    expect(panel.textContent).toContain('重算行业快照')
    expect(panel.textContent).toContain('同步补齐行业数据')
    expect(panel.textContent).toContain(NOTE)
    // QUOT-559：面板只展示白话名称与进度，argv 技术串一律不出现
    expect(panel.textContent).not.toContain('ldst industry list')
    expect(panel.textContent).not.toContain('ldst sync industry')
    expect(screen.getAllByRole('button', { name: '立即开始' })).toHaveLength(2)
  })

  it('只读命令直接运行；联网同步命令先二次确认再提交', async () => {
    renderBar()
    await openPanel()

    const runButtons = screen.getAllByRole('button', { name: '立即开始' })
    fireEvent.click(runButtons[1])

    expect(screen.getByRole('button', { name: '确认同步' })).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('/control/commands/industry-sync'),
      expect.objectContaining({ method: 'POST' }),
    )

    fireEvent.click(screen.getByRole('button', { name: '确认同步' }))
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/control/commands/industry-sync'),
        expect.objectContaining({ method: 'POST' }),
      ),
    )
    await waitFor(() => expect(screen.getByRole('button', { name: '进行中' })).toBeTruthy())
  })
})
