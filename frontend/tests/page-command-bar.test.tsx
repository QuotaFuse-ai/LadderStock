// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

/**
 * 点开「本页命令」面板。
 *
 * 不能在 waitFor(trigger) 之后紧接着 click：waitFor 轮询期间 RTL 会关掉 React 的
 * act 环境，首次 commands 到达那次提交的 passive effect（路由态复位）会挂在队列里。
 * 若它挂到 click 的 act 里才被冲刷，React 会先应用 click 的 setOpen(true)、再应用这条
 * 迟到的 setOpen(false)，把刚打开的面板关掉——CI 慢机上稳定复现为「dialog 一直不出现」，
 * 这种丢失不是查询过早，findByRole 等到超时也等不到。
 * click 前空跑一次 act 把挂起的 effect 落到点击之前，点击语义就恢复成普通切换。
 */
async function openPanel() {
  await waitFor(() => expect(trigger()).toBeTruthy())
  await act(async () => {})
  fireEvent.click(trigger())
}

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
    await waitFor(() => expect(commands).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).toBeNull()

    await openPanel()

    // popover 以 dialog 角色出现，等它真正进入 DOM 再断言；断言内容不放宽
    const panel = await screen.findByRole('dialog', { name: '本页命令' })
    expect(panel.textContent).toContain('拉取行情数据')
    expect(panel.textContent).toContain('ldst sync --date 2026-11-05')
    expect(panel.textContent).toContain('上次成功')
    expect(panel.textContent).toContain('已完成，数据已落盘')
    expect(trigger().getAttribute('aria-expanded')).toBe('true')
  })

  it('面板里点「运行」走后端队列，并显示排队状态', async () => {
    renderBar()
    await openPanel()
    fireEvent.click(await screen.findByRole('button', { name: '运行' }))

    await waitFor(() => expect(runCommand).toHaveBeenCalledWith('sync'))
    // 排队态来自 runCommand() 的异步回包，等它落到面板上再断言
    const panel = await screen.findByRole('dialog', { name: '本页命令' })
    await waitFor(() => expect(panel.textContent).toContain('已排队'))
    await waitFor(() => expect(screen.getByRole('button', { name: '执行中' })).toBeTruthy())
  })

  it('Esc 收起面板', async () => {
    renderBar()
    await openPanel()
    await screen.findByRole('dialog', { name: '本页命令' })

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
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
