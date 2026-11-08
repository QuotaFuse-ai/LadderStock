// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PageCommandBar from '../src/components/PageCommandBar'
import type { PageCommand } from '../src/api'

const command: PageCommand = {
  key: 'sync',
  route: '/market',
  label: '拉取行情数据',
  argv: ['sync', '--date', '2026-11-05'],
  running: false,
  last_job: {
    id: 'job-1',
    peer: 'ldst',
    action: 'command',
    status: 'ok',
    step: '完成',
    progress: 100,
    started_at: '2026-11-05T01:02:03Z',
    ended_at: '2026-11-05T01:02:31Z',
    log_tail: ['synced 2026-11-05: 47 limit-up rows'],
    error: null,
  },
}

const { commands, runCommand, job } = vi.hoisted(() => ({
  commands: vi.fn(),
  runCommand: vi.fn(),
  job: vi.fn(),
}))

vi.mock('../src/api', () => ({
  api: { control: { commands, runCommand, job } },
  describeApiError: () => ({ detail: '失败了' }),
}))

function renderBar(path = '/market') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PageCommandBar />
    </MemoryRouter>,
  )
}

const trigger = () => screen.getByRole('button', { name: '本页命令' })

beforeEach(() => {
  commands.mockResolvedValue({ ok: true, commands: [command] })
  runCommand.mockResolvedValue({ job_id: 'job-2' })
  job.mockResolvedValue({
    ...command.last_job!,
    id: 'job-2',
    status: 'running',
    step: '同步行情',
    log_tail: ['start'],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('顶栏本页命令（QUOT-457）', () => {
  it('控制面有本页命令时渲染一颗图标按钮，点击展开面板', async () => {
    renderBar()
    await waitFor(() => expect(trigger()).toBeTruthy())
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(trigger())

    // commands 首次到达时 command?.key 的 effect 会执行一次 setOpen(false)，
    // 与点击存在时序竞态：waitFor 轮询到面板稳定后再断言，而非同步断言第一帧
    const panel = await waitFor(() => screen.getByRole('dialog', { name: '本页命令' }))
    expect(panel.textContent).toContain('拉取行情数据')
    expect(panel.textContent).toContain('ldst sync --date 2026-11-05')
    expect(panel.textContent).toContain('上次成功')
    expect(panel.textContent).toContain('已完成，数据已落盘')
    expect(trigger().getAttribute('aria-expanded')).toBe('true')
  })

  it('面板里点「运行」走后端队列，并显示排队状态', async () => {
    renderBar()
    await waitFor(() => expect(trigger()).toBeTruthy())
    fireEvent.click(trigger())
    await waitFor(() => expect(screen.getByRole('button', { name: '运行' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '运行' }))

    await waitFor(() => expect(runCommand).toHaveBeenCalledWith('sync'))
    const panel = screen.getByRole('dialog', { name: '本页命令' })
    expect(panel.textContent).toContain('已排队')
    expect(screen.getByRole('button', { name: '执行中' })).toBeTruthy()
  })

  it('Esc 收起面板', async () => {
    renderBar()
    await waitFor(() => expect(trigger()).toBeTruthy())
    fireEvent.click(trigger())
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('本页没有命令或处于演示态时整颗不渲染', async () => {
    renderBar('/positions')
    await waitFor(() => expect(commands).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: '本页命令' })).toBeNull()

    cleanup()
    vi.clearAllMocks()
    renderBar('/market?demo=1')
    expect(commands).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '本页命令' })).toBeNull()
  })
})
