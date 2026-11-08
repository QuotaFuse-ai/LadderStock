// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  api,
  ApiError,
  type BoardPayload,
  type JobHandle,
  type PeerSnapshot,
} from '../src/api'
import StopChecklistDialog from '../src/components/StopChecklistDialog'
import {
  entriesFromBoard,
  originRowLabel,
  ownershipLabel,
  planStop,
  type StopListEntry,
  type StopPreviewPeerLike,
} from '../src/lib/supervisor'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * QUOT-484：「将停清单」确认弹层 + 模块归属/自愈展示。
 *
 * - 归属判定只来自后端账本 origin（482 未落地时缺失 → 「归属未上报」），前端不扫端口。
 * - 清单里出现任何外部接管进程必须弹层 + 二次确认；全是本程序启动的不弹冗余确认。
 * - 取消后模块回正常态，外部接管保留「未停止（外部进程）」可见标记。
 */

// ---------------------------------------------------------------- 纯函数

const SPAWNED: StopListEntry = {
  module: 'tickhub',
  product: 'TickHub-CN',
  pid: 1001,
  ports: [8000, 8787],
  origin: 'spawned',
  cmdline: 'python -m tickhub',
}

const ADOPTED: StopListEntry = {
  module: 'wire',
  product: 'QuotaFuse Wire',
  pid: 2002,
  ports: [8791],
  origin: 'adopted',
  cmdline: 'D:\\workplace\\Wire\\.venv\\Scripts\\python.exe -m quotafuse_wire --config D:\\workplace\\Wire\\config\\wire.env --listen 0.0.0.0:8791 --workers 4',
}

describe('supervisor 纯函数（QUOT-484 契约）', () => {
  it('ownershipLabel：spawned / spawned-via-task → 主程序托管，adopted → 外部接管', () => {
    expect(ownershipLabel('spawned')).toBe('主程序托管')
    expect(ownershipLabel('spawned-via-task')).toBe('主程序托管')
    expect(ownershipLabel('adopted')).toBe('外部接管')
  })

  it('ownershipLabel：null → 非托管（观测），字段缺失 → 归属未上报（不猜）', () => {
    expect(ownershipLabel(null)).toBe('非托管（观测）')
    expect(ownershipLabel(undefined)).toBe('归属未上报')
  })

  it('originRowLabel：清单行来源文字，null → 未记账', () => {
    expect(originRowLabel('spawned')).toBe('本程序启动')
    expect(originRowLabel('spawned-via-task')).toBe('本程序启动（任务）')
    expect(originRowLabel('adopted')).toBe('外部接管')
    expect(originRowLabel(null)).toBe('未记账')
  })

  it('planStop：清单含外部接管 → confirm 模式并列出 adopted；全本程序启动 → direct', () => {
    const confirm = planStop([SPAWNED, ADOPTED])
    expect(confirm.mode).toBe('confirm')
    expect(confirm).toHaveProperty('adopted', [ADOPTED])

    const direct = planStop([SPAWNED, { ...SPAWNED, module: 'senate', pid: 3003 }])
    expect(direct.mode).toBe('direct')
    if (direct.mode === 'direct') expect(direct.entries).toHaveLength(2)
  })

  it('entriesFromBoard：只列 running/starting，pid 取监听属主，origin 缺失如实记 null', () => {
    const boardPeers: StopPreviewPeerLike[] = [
      {
        id: 'tickhub',
        product: 'TickHub-CN',
        status: 'running',
        ports: { mcp: 8000, web: 8787 },
        listening: { mcp: [{ pid: 1001 }], web: [{ pid: 1002 }] },
      },
      {
        id: 'wire',
        product: 'QuotaFuse Wire',
        status: 'running',
        origin: 'adopted',
        cmdline: 'python -m quotafuse_wire',
        ports: { mcp: 8791, web: null },
        listening: { mcp: [{ pid: null }], web: [] },
      },
      {
        id: 'senate',
        product: 'StockSenate',
        status: 'stopped',
        ports: { mcp: 8765, web: null },
        listening: { mcp: [{ pid: 9999 }], web: [] },
      },
    ]
    const entries = entriesFromBoard(boardPeers)
    expect(entries.map((entry) => entry.module)).toEqual(['tickhub', 'wire'])
    expect(entries[0]).toMatchObject({ pid: 1001, ports: [8000, 8787], origin: null, cmdline: null })
    // pid 优先取 mcp 监听属主；mcp 没有属主时回退 web；都没有 → null（不编造）
    expect(entries[1]).toMatchObject({ pid: null, ports: [8791], origin: 'adopted' })
  })
})

// ---------------------------------------------------------------- 弹层组件

function renderDialog(entries: StopListEntry[], handlers?: { onConfirm?: () => void; onCancel?: () => void }) {
  const onConfirm = handlers?.onConfirm ?? vi.fn()
  const onCancel = handlers?.onCancel ?? vi.fn()
  render(
    <StopChecklistDialog
      open
      title="退出：将停止以下进程"
      entries={entries}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  )
  return { onConfirm, onCancel }
}

describe('StopChecklistDialog（D2 弹层）', () => {
  afterEach(cleanup)

  it('逐行展示模块名 / pid / 端口 / 来源，外部接管行与本程序启动行视觉可区分', () => {
    renderDialog([SPAWNED, ADOPTED])
    expect(screen.getByRole('dialog', { name: '退出：将停止以下进程' })).toBeTruthy()
    expect(screen.getByText('TickHub-CN')).toBeTruthy()
    expect(screen.getByText('QuotaFuse Wire')).toBeTruthy()
    expect(screen.getByText('pid 1001')).toBeTruthy()
    expect(screen.getByText('pid 2002')).toBeTruthy()
    expect(screen.getByText('端口 8000 / 8787')).toBeTruthy()
    // 来源标签：文字 + 色调双通道（adopted=warn 外部接管，spawned=info 本程序启动）
    expect(screen.getByText('本程序启动')).toBeTruthy()
    expect(screen.getByText('外部接管')).toBeTruthy()
    expect(screen.getByText('外部接管').closest('li')?.className).toContain('bg-warn-soft')
    expect(screen.getByText('本程序启动').closest('li')?.className).not.toContain('bg-warn-soft')
    // 外部接管提示：取消后会保留并标记
    expect(screen.getByText(/取消后它们会保留在面板上/)).toBeTruthy()
  })

  it('命令行过长默认截断，可展开查看全文', () => {
    renderDialog([ADOPTED])
    const toggle = screen.getByRole('button', { name: '命令行' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(ADOPTED.cmdline as string)).toBeTruthy()
  })

  it('空清单：EmptyState + 确认按钮禁用', () => {
    renderDialog([])
    expect(screen.getByText('没有要停止的进程')).toBeTruthy()
    const confirm = screen.getByRole('button', { name: /确认停止/ })
    expect(confirm.hasAttribute('disabled')).toBe(true)
  })

  it('Esc 关闭弹层 = 取消', () => {
    const { onCancel } = renderDialog([SPAWNED])
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('二次确认：先武装 → 勾选才能点「仍要停止」；返回可回到第一段', () => {
    const { onConfirm } = renderDialog([SPAWNED, ADOPTED])
    expect(screen.queryByText(/我确认要停止清单里的/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '确认停止（2）' }))
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement
    const stillStop = screen.getByRole('button', { name: '仍要停止' })
    expect(stillStop.hasAttribute('disabled')).toBe(true)

    fireEvent.click(checkbox)
    expect(checkbox.checked).toBe(true)
    expect(stillStop.hasAttribute('disabled')).toBe(false)
    fireEvent.click(stillStop)
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('armed 后「返回」可回到第一段，确认数字重新可见', () => {
    renderDialog([SPAWNED, ADOPTED])
    fireEvent.click(screen.getByRole('button', { name: '确认停止（2）' }))
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    expect(screen.getByRole('button', { name: '确认停止（2）' })).toBeTruthy()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})

// ---------------------------------------------------------------- 设置页集成

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
    detail: '端口已监听',
    installed: true,
    version: '1.0.0',
    venv: null,
    ports: { mcp: 8000, web: null, web_label: '管理端' },
    listening: { mcp: [], web: [] },
    health: { ok: true, detail: 'HTTP 200' },
    dist: { wheel: null, editable: false, env_file: '' },
    port_conflicts: [],
    last_job: null,
    open_url: null,
    actions: { can_install: false, can_start: true, can_stop: true, can_open: false },
    ...overrides,
  }
}

function board(peers: PeerSnapshot[]): BoardPayload {
  return {
    ok: true,
    control_enabled: true,
    peers,
    registry: [],
    components: [],
    port_conflicts: [],
    ports: { board: 5950, mcp: 5951 },
  }
}

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={['/settings?tab=peers']}>
      <SettingsScreen />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('设置页归属展示（QUOT-484 D5）', () => {
  it('归属徽章三态：adopted → 外部接管 / null → 非托管（观测）/ 缺失 → 归属未上报', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board([
        peer('wire', 'QuotaFuse Wire', { origin: 'adopted' }),
        peer('financecoffer', 'FinanceCoffer', { origin: null }),
        peer('tickhub', 'TickHub-CN'),
      ]),
    )
    renderSettings()

    expect(await screen.findByText('外部接管')).toBeTruthy()
    expect(screen.getByText('非托管（观测）')).toBeTruthy()
    expect(screen.getByText('归属未上报')).toBeTruthy()
  })

  it('自愈 healing：显示「正在重拉（第 N/3 次）」与进度条；gave_up：红块 + 需人工处理 + 重试', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board([
        peer('a', 'Alpha', { self_heal: { attempts: 2, max: 3, status: 'healing' } }),
        peer('b', 'Beta', { self_heal: { attempts: 3, max: 3, status: 'gave_up' } }),
      ]),
    )
    renderSettings()

    expect(await screen.findByText('正在重拉（第 2/3 次）')).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: '自愈重拉进度' })).toBeTruthy()
    expect(screen.getByText('需人工处理（已停止自愈）')).toBeTruthy()
    expect(screen.getByText(/自动自愈停止/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
    // 超限卡片整卡标红（border-danger）
    expect(screen.getByText('Beta').closest('li')?.className).toContain('border-danger')
  })
})

describe('设置页停止清单流程（QUOT-484 D2）', () => {
  it('退出流程：预览含外部接管 → 弹清单；确认后调 stopAll，kept 非空时卡片带「未停止（外部进程）」', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board([peer('wire', 'QuotaFuse Wire', { origin: 'adopted' }), peer('tickhub', 'TickHub-CN')]),
    )
    vi.spyOn(api.control, 'stopPreview').mockResolvedValue({ entries: [SPAWNED, ADOPTED] })
    const stopAll = vi
      .spyOn(api.control, 'stopAll')
      .mockResolvedValue({ stopped: ['tickhub'], kept: ['wire'] })
    renderSettings()

    const exitButton = await screen.findByRole('button', { name: '退出并停止全部' })
    fireEvent.click(exitButton)

    expect(await screen.findByRole('dialog', { name: '退出：将停止以下进程' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '确认停止（2）' }))
    const checkbox = screen.getByRole('checkbox')
    fireEvent.click(checkbox)
    fireEvent.click(screen.getByRole('button', { name: '仍要停止' }))

    await waitFor(() => expect(stopAll).toHaveBeenCalledOnce())
    // 取消/被保留的外部进程在面板保留可见标记
    expect(await screen.findByText('未停止（外部进程）')).toBeTruthy()
    expect(await screen.findByText(/1 个外部进程已保留并标记/)).toBeTruthy()
  })

  it('全为本程序启动：不弹冗余清单，直接 stopAll 并给出可见说明', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(board([peer('tickhub', 'TickHub-CN')]))
    vi.spyOn(api.control, 'stopPreview').mockResolvedValue({ entries: [{ ...SPAWNED, pid: 1001 }] })
    const stopAll = vi
      .spyOn(api.control, 'stopAll')
      .mockResolvedValue({ stopped: ['tickhub'], kept: [] })
    renderSettings()

    fireEvent.click(await screen.findByRole('button', { name: '退出并停止全部' }))
    await waitFor(() => expect(stopAll).toHaveBeenCalledOnce())
    expect(await screen.findByText(/1 个进程全部为本程序启动/)).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('预览接口 404：回退 board 组装并在弹层如实说明', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board([
        peer('wire', 'QuotaFuse Wire', {
          origin: 'adopted',
          ports: { mcp: 8791, web: null, web_label: 'HTTP' },
          listening: { mcp: [{ pid: 2002, process: 'python.exe' }], web: [] },
        }),
        peer('tickhub', 'TickHub-CN'),
      ]),
    )
    vi
      .spyOn(api.control, 'stopPreview')
      .mockRejectedValue(new ApiError('http', 'Not Found', 404))
    vi.spyOn(api.control, 'stopAll').mockResolvedValue({ stopped: [], kept: [] })
    renderSettings()

    fireEvent.click(await screen.findByRole('button', { name: '退出并停止全部' }))
    const dialog = await screen.findByRole('dialog', { name: '退出：将停止以下进程' })
    expect(dialog).toBeTruthy()
    // fallbackNote：预览未接入的如实说明显示在弹层内
    expect(screen.getByText('supervisor 预览接口未接入（QUOT-482），清单由最近一次模块探测组装；pid 来自监听端口属主')).toBeTruthy()
    // board 回退：running 的两个模块都在清单里，pid 来自监听属主（tickhub 没有监听 → —）
    expect(within(dialog).getByText('QuotaFuse Wire')).toBeTruthy()
    expect(within(dialog).getByText('TickHub-CN')).toBeTruthy()
    expect(within(dialog).getByText('pid 2002')).toBeTruthy()
    expect(within(dialog).getByText('pid —')).toBeTruthy()
  })

  it('取消路径：弹层取消后模块回正常态，外部接管保留「未停止（外部进程）」标记', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board([peer('wire', 'QuotaFuse Wire', { origin: 'adopted' })]),
    )
    vi.spyOn(api.control, 'stopPreview').mockResolvedValue({ entries: [ADOPTED] })
    const stopAll = vi.spyOn(api.control, 'stopAll')
    renderSettings()

    fireEvent.click(await screen.findByRole('button', { name: '退出并停止全部' }))
    await screen.findByRole('dialog', { name: '退出：将停止以下进程' })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(await screen.findByText('未停止（外部进程）')).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(stopAll).not.toHaveBeenCalled()
  })

  it('单停外部接管模块：点「停止」弹清单而不是直接创建任务；确认后才调 stop', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board([peer('wire', 'QuotaFuse Wire', { origin: 'adopted' })]),
    )
    const handle: JobHandle = { job_id: 'job-1', status: 'queued' }
    const stop = vi.spyOn(api.control, 'stop').mockResolvedValue(handle)
    renderSettings()

    fireEvent.click(await screen.findByRole('button', { name: '停止' }))
    const dialog = await screen.findByRole('dialog', { name: '停止 QuotaFuse Wire' })
    expect(dialog).toBeTruthy()
    expect(stop).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '确认停止（1）' }))
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: '仍要停止' }))

    await waitFor(() => expect(stop).toHaveBeenCalledWith('wire'))
    expect(await screen.findByText('已创建任务：停止外部接管模块')).toBeTruthy()
  })
})
