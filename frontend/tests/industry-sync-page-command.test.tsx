// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

describe('本页命令：同路由两条命令（QUOT-538）', () => {
  it('两条命令都渲染，note 原样展示', async () => {
    renderBar()
    await waitFor(() => expect(screen.getByRole('button', { name: '本页命令' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '本页命令' }))

    const panel = screen.getByRole('dialog', { name: '本页命令' })
    expect(panel.textContent).toContain('重算行业快照')
    expect(panel.textContent).toContain('ldst industry list')
    expect(panel.textContent).toContain('同步补齐行业数据')
    expect(panel.textContent).toContain('ldst sync industry')
    expect(panel.textContent).toContain(NOTE)
    expect(screen.getAllByRole('button', { name: '运行' })).toHaveLength(2)
  })

  it('只读命令直接运行；联网同步命令先二次确认再提交', async () => {
    renderBar()
    await waitFor(() => expect(screen.getByRole('button', { name: '本页命令' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '本页命令' }))

    const runButtons = screen.getAllByRole('button', { name: '运行' })
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
    await waitFor(() => expect(screen.getByRole('button', { name: '执行中' })).toBeTruthy())
  })
})
