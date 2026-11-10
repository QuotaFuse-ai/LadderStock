// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobView, PageCommand } from '../src/api'
import IndustrySyncPanel, { type IndustryCoverage } from '../src/screens/industry/IndustrySyncPanel'

/**
 * QUOT-538：/industry 卡头「同步补齐」。
 *
 * 契约取 QUOT-533 交付评论（PR #198）：命令 key `industry-sync`、note 原文、
 * job 视图字段、HTTP 403/404/409 与 CLI 退出码。测试只按契约形状造 payload，
 * 不复刻后端实现。
 */

function json(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
  }
}

const NOTE =
  '联网同步（成本非 0）：按 config/industry.yaml 已启用行业的锚点股逐只回补（单数据集上限 100 只，不做全市场拉取），水位增量 + 幂等落库 + QFSD-7001 退避。与只读的「重算行业快照」不同，本命令会真正发起上游请求。'

function syncCommand(lastJob: JobView | null = null): PageCommand {
  return {
    key: 'industry-sync',
    route: '/industry',
    label: '同步补齐行业数据',
    argv: ['sync', 'industry'],
    running: false,
    last_job: lastJob,
    note: NOTE,
  }
}

const readonlyCommand: PageCommand = {
  key: 'industry',
  route: '/industry',
  label: '重算行业快照',
  argv: ['industry', 'list'],
  running: false,
  last_job: null,
}

function job(overrides: Partial<JobView> = {}): JobView {
  return {
    id: 'job-1',
    peer: 'ldst',
    action: 'command',
    status: 'running',
    step: '同步补齐行业数据',
    progress: 0.4,
    started_at: '2026-11-10T17:40:00Z',
    ended_at: null,
    log_tail: ['start sync'],
    error: null,
    ...overrides,
  }
}

let commands: PageCommand[]
let jobReplies: JobView[]
let postFailure: { status: number; detail: string } | null
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  commands = [readonlyCommand, syncCommand()]
  jobReplies = [job()]
  postFailure = null
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/api/control/commands')) return json({ ok: true, commands })
    if (url.endsWith('/api/control/commands/industry-sync') && init?.method === 'POST') {
      if (postFailure) return json({ detail: postFailure.detail }, postFailure.status)
      return json({ job_id: 'job-1', status: 'queued' })
    }
    if (url.includes('/api/control/jobs/')) {
      return json(jobReplies.length > 1 ? jobReplies.shift() : jobReplies[0])
    }
    return json({ detail: 'Not Found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderPanel(coverage: IndustryCoverage | null, onCompleted = vi.fn()) {
  return render(
    <MemoryRouter initialEntries={['/industry']}>
      <IndustrySyncPanel coverage={coverage} onCompleted={onCompleted} />
    </MemoryRouter>,
  )
}

function Harness() {
  const [coverage, setCoverage] = useState<IndustryCoverage>({ total: 12, partial: 2, missing: 1 })
  return (
    <IndustrySyncPanel
      coverage={coverage}
      onCompleted={() => setCoverage({ total: 12, partial: 0, missing: 0 })}
    />
  )
}

async function openAndAcknowledge() {
  fireEvent.click(await screen.findByRole('button', { name: '同步补齐' }))
  const dialog = await screen.findByRole('dialog', { name: '同步补齐行业数据' })
  fireEvent.click(screen.getByRole('checkbox'))
  return dialog
}

describe('/industry 同步补齐（QUOT-538）', () => {
  it('空闲态：卡头按钮打开抽屉，note 原样渲染，未勾选不能开始', async () => {
    renderPanel({ total: 12, partial: 2, missing: 1 })
    fireEvent.click(await screen.findByRole('button', { name: '同步补齐' }))

    const dialog = await screen.findByRole('dialog', { name: '同步补齐行业数据' })
    expect(dialog.textContent).toContain(NOTE)
    expect(dialog.textContent).toContain('重算行业快照 = 用已有数据重新整理')
    expect(dialog.textContent).toContain('同步补齐 = 联网拉取缺失数据')

    const start = screen.getByRole('button', { name: '开始同步' }) as HTMLButtonElement
    expect(start.disabled).toBe(true)

    fireEvent.click(screen.getByRole('checkbox'))
    expect((screen.getByRole('button', { name: '开始同步' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('运行态：提交后显示排队/进度，禁止重复提交', async () => {
    renderPanel({ total: 12, partial: 2, missing: 1 })
    await openAndAcknowledge()
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/control/commands/industry-sync'),
        expect.objectContaining({ method: 'POST' }),
      ),
    )
    const dialog = screen.getByRole('dialog', { name: '同步补齐行业数据' })
    expect(dialog.textContent).toContain('已排队')

    await waitFor(() => expect(screen.getByRole('progressbar', { name: '同步进度' })).toBeTruthy(), {
      timeout: 3000,
    })
    const runningButton = screen.getByRole('button', { name: '同步中' }) as HTMLButtonElement
    expect(runningButton.disabled).toBe(true)
  })

  it('成功态：展示覆盖对比（同步前 → 同步后）并通知父页刷新', async () => {
    jobReplies = [
      job({ status: 'running', progress: 0.5 }),
      job({ status: 'ok', progress: 1, ended_at: '2026-11-10T17:41:00Z', log_tail: ['done: 21 requests'] }),
    ]
    render(
      <MemoryRouter initialEntries={['/industry']}>
        <Harness />
      </MemoryRouter>,
    )
    await openAndAcknowledge()
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }))

    await waitFor(() => expect(screen.getByText('同步完成，数据已落盘。')).toBeTruthy(), {
      timeout: 4000,
    })
    expect(screen.getByText('覆盖对比（同步前 → 同步后，只数后端给的状态桶）')).toBeTruthy()
    expect(screen.getByText(/2 → 0 个/)).toBeTruthy()
    expect(screen.getByText(/1 → 0 个/)).toBeTruthy()
    expect(screen.getByText('done: 21 requests')).toBeTruthy()
  })

  it('错误态：超限（退出码 2）给中文结论并保留服务端原文', async () => {
    jobReplies = [
      job({
        status: 'failed',
        progress: 0,
        ended_at: '2026-11-10T17:41:00Z',
        error: '同步补齐行业数据失败（退出码 2）：单数据集上限 100 只，请缩小 --codes',
      }),
    ]
    renderPanel({ total: 12, partial: 2, missing: 1 })
    await openAndAcknowledge()
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }))

    await waitFor(() => expect(screen.getByText('超出单次同步上限或参数不合法，未发起请求。')).toBeTruthy(), {
      timeout: 3000,
    })
    expect(screen.getByText(/退出码 2/)).toBeTruthy()
    expect((screen.getByRole('button', { name: '再次同步' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('错误态：已有命令在执行（409）说清一次只允许一个任务', async () => {
    postFailure = { status: 409, detail: '已有页面命令在执行，请等待结束' }
    renderPanel({ total: 12, partial: 2, missing: 1 })
    await openAndAcknowledge()
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }))

    expect(
      await screen.findByText('已有页面命令在执行，请等它结束后再试（一次只允许一个任务）。'),
    ).toBeTruthy()
  })

  it('控制面未启用（404）或没有 industry-sync 命令时整颗不渲染', async () => {
    commands = [readonlyCommand]
    renderPanel({ total: 12, partial: 2, missing: 1 })
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: '同步补齐' })).toBeNull()
  })
})
