// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  api,
  type BoardPayload,
  type PeerSnapshot,
  type SupervisorModule,
  type SupervisorStatePayload,
} from '../src/api'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * QUOT-484：停止清单确认 + 模块归属 / 自愈状态展示。
 *
 * 锁死四件事：
 * 1. 归属只翻译后端 `origin` / `ownership`（spawned → 主程序托管，adopted → 外部接管，
 *    external → 非托管（观测）），前端不扫端口；
 * 2. 停止弹层是可见文本列表（模块 / PID / 端口 / 来源 / 命令行），两类来源同屏可辨；
 * 3. 取消后外部进程保留「未停止（外部进程）」可见标记；
 * 4. 全是本程序启动的进程时不弹冗余确认，但给出可见说明。
 */

function peer(id: string, product: string, overrides: Partial<PeerSnapshot> = {}): PeerSnapshot {
  return {
    id,
    product,
    role: `${product} 职责`,
    directory: `D:/workplace/${id}`,
    directory_exists: true,
    registered: true,
    status: 'running',
    status_label: '运行中',
    detail: '端口已监听，健康检查通过',
    installed: true,
    version: '1.0.0',
    venv: null,
    ports: { mcp: 8000, web: 8787, web_label: '管理端' },
    listening: { mcp: [], web: [] },
    health: { ok: true, detail: 'HTTP 200' },
    dist: { wheel: null, editable: false, env_file: '' },
    port_conflicts: [],
    last_job: null,
    open_url: null,
    actions: { can_install: false, can_start: false, can_stop: true, can_open: false, can_config: true },
    ...overrides,
  }
}

function board(): BoardPayload {
  return {
    ok: true,
    control_enabled: true,
    peers: [
      peer('tickhub', 'TickHub-CN'),
      peer('wire', 'QuotaFuse Wire'),
      peer('senate', 'StockSenate'),
      peer('financecoffer', 'FinanceCoffer'),
    ],
    registry: [],
    components: [],
    port_conflicts: [],
    ports: { board: 5950, mcp: 5951 },
  }
}

function moduleState(overrides: Partial<SupervisorModule> & Pick<SupervisorModule, 'module'>): SupervisorModule {
  return {
    ports: [8000],
    pid: 101,
    health: 'ok',
    state: 'running',
    restarts_5min: 0,
    restart_count: 0,
    stopping_by_user: false,
    origin: 'spawned',
    cmdline_hash: 'hash',
    last_exit_reason: null,
    self_heal: { attempts: 0, max: 3, status: 'ok' },
    ...overrides,
  }
}

function supervisor(modules: SupervisorModule[]): SupervisorStatePayload {
  return {
    ok: true,
    engine: { running: true, window_seconds: 300, max_restarts: 3, backoff: [5, 15, 45] },
    modules,
    configured: true,
  }
}

const MIXED = supervisor([
  moduleState({ module: 'tickhub', pid: 101, ports: [8000, 8787], origin: 'spawned', ownership: 'managed' }),
  moduleState({
    module: 'wire',
    pid: 202,
    ports: [8790, 8791],
    origin: 'adopted',
    ownership: 'managed',
    cmdline: 'python -m quotafuse_wire.serve --port 8790 --mcp-port 8791 --config data/wire.yaml',
  }),
  moduleState({ module: 'senate', pid: 303, ports: [8765, 8786], origin: null, ownership: 'external', evidence: 'port_taken_by_unknown' }),
  moduleState({
    module: 'financecoffer',
    pid: 404,
    ports: [8100],
    state: 'needs_human',
    health: 'needs_human',
    origin: 'spawned',
    ownership: 'managed',
    self_heal: { attempts: 3, max: 3, status: 'gave_up' },
    last_exit_reason: '连续崩溃 3 次',
  }),
])

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={['/settings?tab=peers']}>
      <SettingsScreen />
    </MemoryRouter>,
  )
}

/** 等归属数据落地、退出按钮解禁后再点击（首帧还在 loading 时按钮是禁用的）。 */
async function clickExit(): Promise<void> {
  const button = (await screen.findByTestId('exit-stop-all')) as HTMLButtonElement
  await waitFor(() => expect(button.disabled).toBe(false))
  fireEvent.click(button)
}

function mockApis(state: SupervisorStatePayload) {
  vi.spyOn(api.control, 'board').mockResolvedValue(board())
  vi.spyOn(api.supervisor, 'state').mockResolvedValue(state)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('QUOT-484 归属展示', () => {
  it('三档归属都来自后端 origin/ownership，三类同屏可辨', async () => {
    mockApis(MIXED)
    renderSettings()

    expect((await screen.findAllByText('主程序托管')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('外部接管').length).toBeGreaterThan(0)
    expect(screen.getByText('非托管（观测）')).toBeTruthy()
    // 自愈超限：卡片标红 + 文案 + 重试入口
    expect(screen.getByText('需人工处理（已停止自愈）')).toBeTruthy()
    expect(screen.getByTestId('peer-card-financecoffer').getAttribute('data-self-heal')).toBe('gave_up')
    expect(screen.getByRole('button', { name: /重试/ })).toBeTruthy()
  })

  it('后端 ownership=external 时优先判为非托管，即使账本 origin 还是 spawned', async () => {
    mockApis(
      supervisor([
        moduleState({
          module: 'tickhub',
          pid: 101,
          origin: 'spawned',
          ownership: 'external',
          evidence: 'cmdline_mismatch',
        }),
      ]),
    )
    renderSettings()

    const card = await screen.findByTestId('peer-card-tickhub')
    expect(within(card).getByText('非托管（观测）')).toBeTruthy()
    expect(within(card).queryByText('主程序托管')).toBeNull()
  })

  it('自愈进行中显示「正在重拉（第 N/M 次）」', async () => {
    mockApis(
      supervisor([
        moduleState({
          module: 'wire',
          pid: 202,
          state: 'restarting',
          health: 'restarting',
          origin: 'spawned',
          ownership: 'managed',
          self_heal: { attempts: 2, max: 3, status: 'healing' },
        }),
      ]),
    )
    renderSettings()

    expect(await screen.findByText('正在重拉（第 2/3 次）')).toBeTruthy()
  })
})

describe('QUOT-484 停止清单', () => {
  it('退出时弹出可见文本清单：模块 / PID / 端口 / 来源 / 命令行，且确认需二次勾选', async () => {
    mockApis(MIXED)
    renderSettings()

    await clickExit()

    const list = await screen.findByTestId('stop-list')
    expect(within(list).getByText('TickHub-CN')).toBeTruthy()
    expect(within(list).getByText('QuotaFuse Wire')).toBeTruthy()
    // 两类来源同屏可辨
    expect(within(list).getByText('本程序启动')).toBeTruthy()
    expect(within(list).getByText('外部接管')).toBeTruthy()
    // pid / 端口
    expect(within(list).getByText('202')).toBeTruthy()
    expect(within(list).getByText('8790 / 8791')).toBeTruthy()
    // 命令行摘要可展开看全
    expect(within(list).getByText(/quotafuse_wire\.serve/)).toBeTruthy()
    expect(within(list).getByText('展开完整命令行')).toBeTruthy()

    // 未勾选 → 确认按钮禁用（二次确认，防误触全停）
    const confirm = screen.getByRole('button', { name: '确认停止' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(confirm.disabled).toBe(false)
  })

  it('取消后外部进程保留「未停止（外部进程）」标记，不静默消失', async () => {
    mockApis(MIXED)
    const stopSpy = vi.spyOn(api.supervisor, 'stop')
    renderSettings()

    await clickExit()
    fireEvent.click(await screen.findByRole('button', { name: '取消' }))

    await waitFor(() => expect(screen.queryByTestId('stop-list')).toBeNull())
    expect(screen.getByTestId('not-stopped-wire').textContent).toContain('未停止（外部进程）')
    // 本程序启动的模块不会被误标
    expect(screen.queryByTestId('not-stopped-tickhub')).toBeNull()
    expect(screen.getByTestId('peer-card-wire')).toBeTruthy()
    expect(stopSpy).not.toHaveBeenCalled()
  })

  it('全是本程序启动时：不弹冗余确认，给出可见说明', async () => {
    mockApis(
      supervisor([
        moduleState({ module: 'tickhub', pid: 101, origin: 'spawned', ownership: 'managed' }),
        moduleState({ module: 'wire', pid: 202, origin: 'spawned', ownership: 'managed' }),
      ]),
    )
    vi.spyOn(api.supervisor, 'stop').mockResolvedValue({ ok: true, stopped: ['wire', 'tickhub'], skipped: [] })
    renderSettings()

    await clickExit()

    expect(await screen.findByText(/全部 2 个模块均为本程序启动/)).toBeTruthy()
    expect(screen.queryByTestId('stop-list')).toBeNull()
    await waitFor(() =>
      expect(api.supervisor.stop).toHaveBeenCalledWith({ confirm: true, modules: ['tickhub', 'wire'] }),
    )
  })

  it('点单个模块「停止」也先给清单确认', async () => {
    mockApis(MIXED)
    renderSettings()

    const card = await screen.findByTestId('peer-card-wire')
    fireEvent.click(within(card).getByRole('button', { name: '停止' }))

    const list = await screen.findByTestId('stop-list')
    expect(within(list).getByText('QuotaFuse Wire')).toBeTruthy()
    expect(within(list).queryByText('TickHub-CN')).toBeNull()
  })

  it('后端没有停止接口（404）时如实报未执行，不假装已停', async () => {
    mockApis(MIXED)
    const { ApiError } = await import('../src/api')
    vi.spyOn(api.supervisor, 'stop').mockRejectedValue(new ApiError('http', 'HTTP 404', 404, 'Not Found'))
    renderSettings()

    await clickExit()
    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: '确认停止' }))

    expect((await screen.findByText(/482-B 尚未接入/)).textContent).toContain('本次没有停止任何进程')
  })
})
