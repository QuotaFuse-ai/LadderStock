// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  api,
  type BoardPayload,
  type PeerPortDiagnosis,
  type PeerSnapshot,
  type PortClass,
  type PortDiagnosisEntry,
  type PortHealResult,
  type PortsDiagnosisPayload,
} from '../src/api'
import SettingsScreen from '../src/screens/SettingsScreen'
import {
  portClassView,
  portHealthView,
  portPlainReason,
  safeActionView,
} from '../src/lib/portDiagnosticsView'

/**
 * QUOT-548：端口诊断 + 安全自愈面板。
 *
 * 锁死五件事：
 * 1. 四分类（自有残留 / 已接管 / 外来进程 / 空闲）+ 健康 + 白话原因只翻译后端
 *    `GET /api/control/ports`，前端不重算；adopted peer 显示「已接管 / 正常」，
 *    不再出现「被占用」误导文案；
 * 2. 安全按钮策略与后端 heal 优先级一致：重启只对 own_child 启用；adopted_peer
 *    只有「复检健康」；foreign 禁用并说明「不会终止外来进程」；
 * 3. 修复 / 复检只经 `POST /api/control/ports/{peer}/heal`，绝不触碰 stop / start /
 *    supervisor.stop（QUOT-545 / DEC-079 adopt-only 红线）；
 * 4. 自愈结果用后端回读的最新诊断刷新展示（不本地重算）；
 * 5. 模块清单来自后端响应，不硬编码；技术细节（PID / 命令行）默认收起。
 */

function entry(
  port: number,
  role: string,
  portClass: PortClass,
  overrides: Partial<PortDiagnosisEntry> = {},
): PortDiagnosisEntry {
  return {
    port,
    role,
    class: portClass,
    pid: portClass === 'free' ? null : 10000 + port,
    process: portClass === 'free' ? null : 'peer.exe',
    cmdline: portClass === 'free' ? null : ['peer.exe', 'start', '--port', String(port)],
    listening: portClass !== 'free',
    reason: `后端判定原文（${portClass}）`,
    ...overrides,
  }
}

function peerDiag(
  peer: string,
  ports: PortDiagnosisEntry[],
  health: { ok: boolean | null; detail: string } = { ok: true, detail: 'HTTP 200' },
): PeerPortDiagnosis {
  return { peer, health, ports }
}

/** 现网同款场景：tickhub 8000 是外部启动、已被认领的 peer（DEC-079 滞后标记态）。 */
function diagnosis(): PortsDiagnosisPayload {
  return {
    ok: true,
    peers: [
      peerDiag('tickhub', [
        entry(8000, 'mcp', 'adopted_peer', { process: 'th-cn.exe', cmdline: ['th-cn.exe', 'start-all', '--port', '8000'] }),
        entry(8787, 'web', 'adopted_peer'),
      ]),
      peerDiag('wire', [entry(8791, 'mcp', 'own_child'), entry(8790, 'web', 'own_child')], {
        ok: false,
        detail: '健康检查失败',
      }),
      peerDiag('senate', [entry(8765, 'mcp', 'foreign')], { ok: false, detail: '连接被拒绝' }),
      peerDiag('financecoffer', [entry(8100, 'mcp', 'free')], { ok: null, detail: '端口无监听，未探测' }),
    ],
  }
}

function peer(id: string, product: string): PeerSnapshot {
  return {
    id,
    product,
    role: `${product} 职责`,
    directory: `D:/workplace/${id}`,
    directory_exists: true,
    registered: true,
    status: 'running',
    status_label: '运行中',
    detail: '',
    installed: true,
    version: null,
    venv: null,
    ports: { mcp: 8000, web: null, web_label: '管理端' },
    listening: { mcp: [], web: [] },
    health: { ok: true, detail: '' },
    dist: { wheel: null, editable: false, env_file: '' },
    port_conflicts: [],
    last_job: null,
    open_url: null,
    actions: { can_install: false, can_start: false, can_stop: false, can_open: false, can_config: false },
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

function mockApis(payload: PortsDiagnosisPayload = diagnosis()) {
  vi.spyOn(api.control, 'board').mockResolvedValue(board())
  vi.spyOn(api.control, 'ports').mockResolvedValue(payload)
  vi.spyOn(api.supervisor, 'state').mockResolvedValue({
    ok: true,
    engine: { running: true, window_seconds: 300, max_restarts: 3, backoff: [5, 15, 45] },
    modules: [],
    configured: true,
  })
}

function renderPorts() {
  return render(
    <MemoryRouter initialEntries={['/settings?tab=ports']}>
      <SettingsScreen />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('QUOT-548 四分类白话展示', () => {
  it('四分类同屏可辨：已接管 / 自有残留 / 外来进程 / 空闲，健康三态只翻译后端', async () => {
    mockApis()
    renderPorts()

    const panel = await screen.findByTestId('port-diag-panel')
    await waitFor(() => expect(within(panel).getAllByText('已接管').length).toBe(2))
    expect(within(panel).getAllByText('自有残留').length).toBe(2)
    expect(within(panel).getByText('外来进程')).toBeTruthy()
    expect(within(panel).getByText('空闲')).toBeTruthy()
    // 健康：tickhub ok → 正常；wire false → 异常；financecoffer null → 未探测
    expect(within(panel).getByText('正常')).toBeTruthy()
    expect(within(panel).getAllByText('异常').length).toBe(2)
    expect(within(panel).getByText('未探测')).toBeTruthy()
  })

  it('8000 的已接管 peer 显示「已接管 / 正常」白话，不再出现「被占用」误导', async () => {
    mockApis()
    renderPorts()

    const card = await screen.findByTestId('port-diag-peer-tickhub')
    await waitFor(() => expect(within(card).getAllByText('已接管').length).toBe(2))
    expect(within(card).getByText('正常')).toBeTruthy()
    expect(
      within(card).getAllByText('这是你自己启动的 TickHub-CN，已被平台接管，不会终止或重启它，运行正常'),
    ).toHaveLength(2)
    expect(within(card).queryByText(/被占用/)).toBeNull()
    expect(within(card).queryByText(/被其他进程占用/)).toBeNull()
  })

  it('模块清单来自后端响应：只渲染返回的模块，不硬编码补位', async () => {
    mockApis({ ok: true, peers: [peerDiag('wire', [entry(8791, 'mcp', 'own_child')])] })
    renderPorts()

    await screen.findByTestId('port-diag-peer-wire')
    expect(screen.queryByTestId('port-diag-peer-tickhub')).toBeNull()
    expect(screen.queryByTestId('port-diag-peer-senate')).toBeNull()
    expect(screen.getAllByTestId(/^port-diag-peer-/).length).toBe(1)
  })

  it('技术细节（PID / 命令行 / 判定原文）默认收起，展开后可见', async () => {
    mockApis()
    renderPorts()

    const card = await screen.findByTestId('port-diag-peer-tickhub')
    await waitFor(() => expect(within(card).getAllByText('已接管').length).toBe(2))
    const details = card.querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    fireEvent.click(within(card).getByText(/技术细节/))
    expect(details.open).toBe(true)
    expect(within(card).getByText(/th-cn\.exe start-all --port 8000/)).toBeTruthy()
    expect(within(card).getAllByText('后端判定原文（adopted_peer）')).toHaveLength(2)
    expect(within(card).getByText('18000')).toBeTruthy()
  })
})

describe('QUOT-548 安全按钮策略（红线：绝不终止 peer / foreign）', () => {
  it('own_child 启用「一键安全修复」；adopted_peer 只有「复检健康」；foreign / free 禁用并给理由', async () => {
    mockApis()
    renderPorts()

    const wire = await screen.findByTestId('port-diag-peer-wire')
    await waitFor(() => expect(within(wire).getByTestId('port-diag-heal-wire')).toBeTruthy())

    // own_child：修复按钮启用
    const repair = within(wire).getByTestId('port-diag-heal-wire') as HTMLButtonElement
    expect(repair.disabled).toBe(false)
    expect(repair.textContent).toContain('一键安全修复')

    // adopted_peer：只有复检健康，没有修复 / 停止 / 重启按钮
    const tickhub = screen.getByTestId('port-diag-peer-tickhub')
    const recheck = within(tickhub).getByTestId('port-diag-heal-tickhub') as HTMLButtonElement
    expect(recheck.disabled).toBe(false)
    expect(recheck.textContent).toContain('复检健康')
    expect(within(tickhub).queryByText('一键安全修复')).toBeNull()
    expect(within(tickhub).queryByRole('button', { name: /停止|重启/ })).toBeNull()

    // foreign：禁用 + 可见理由「不会终止外来进程」（按钮旁的禁用原因，§4.8）
    const senate = screen.getByTestId('port-diag-peer-senate')
    const blocked = within(senate).getByTestId('port-diag-heal-senate') as HTMLButtonElement
    expect(blocked.disabled).toBe(true)
    expect(within(senate).getByText('不会终止外来进程：请人工确认占用者后处理')).toBeTruthy()

    // free：禁用 + 无需修复
    const coffer = screen.getByTestId('port-diag-peer-financecoffer')
    const idle = within(coffer).getByTestId('port-diag-heal-financecoffer') as HTMLButtonElement
    expect(idle.disabled).toBe(true)
    expect(within(coffer).getByText(/端口空闲，无需修复/)).toBeTruthy()
  })

  it('own_child 修复只调 heal 端点，绝不触碰 stop / start / supervisor.stop', async () => {
    mockApis()
    const healSpy = vi.spyOn(api.control, 'healPorts').mockResolvedValue({
      ok: true,
      peer: 'wire',
      action: 'restart',
      detail: '已停止并重拉本方子进程（own_child）',
      ports: [entry(8791, 'mcp', 'own_child', { pid: 55999 }), entry(8790, 'web', 'own_child', { pid: 55999 })],
      health: { ok: true, detail: 'HTTP 200' },
    } satisfies PortHealResult)
    const stopSpy = vi.spyOn(api.control, 'stop')
    const startSpy = vi.spyOn(api.control, 'start')
    const superStopSpy = vi.spyOn(api.supervisor, 'stop')
    renderPorts()

    const wire = await screen.findByTestId('port-diag-peer-wire')
    const repair = await within(wire).findByTestId('port-diag-heal-wire')
    fireEvent.click(repair)

    await waitFor(() => expect(healSpy).toHaveBeenCalledWith('wire'))
    // 结果横幅用后端 detail；展示刷新为后端回读的新 PID（不本地重算）
    const result = await screen.findByTestId('port-diag-result-wire')
    expect(result.textContent).toContain('已停止并重拉本方子进程（own_child）')
    expect(within(wire).getAllByText('55999').length).toBe(2)
    expect(within(wire).getByText('正常')).toBeTruthy()
    expect(stopSpy).not.toHaveBeenCalled()
    expect(startSpy).not.toHaveBeenCalled()
    expect(superStopSpy).not.toHaveBeenCalled()
  })

  it('adopted_peer 复检只调 heal（后端 adopt + 复检），绝不终止 / 重启 peer', async () => {
    mockApis()
    const healSpy = vi.spyOn(api.control, 'healPorts').mockResolvedValue({
      ok: true,
      peer: 'tickhub',
      action: 'adopt',
      detail: '已认领外部 peer 并复检健康（绝不终止 / 重启）',
      ports: diagnosis().peers[0].ports,
      health: { ok: true, detail: 'mcp-protocol 存活' },
    } satisfies PortHealResult)
    const stopSpy = vi.spyOn(api.control, 'stop')
    const startSpy = vi.spyOn(api.control, 'start')
    const superStopSpy = vi.spyOn(api.supervisor, 'stop')
    renderPorts()

    const tickhub = await screen.findByTestId('port-diag-peer-tickhub')
    const recheck = await within(tickhub).findByTestId('port-diag-heal-tickhub')
    fireEvent.click(recheck)

    await waitFor(() => expect(healSpy).toHaveBeenCalledWith('tickhub'))
    const result = await screen.findByTestId('port-diag-result-tickhub')
    expect(result.textContent).toContain('已认领外部 peer 并复检健康（绝不终止 / 重启）')
    expect(stopSpy).not.toHaveBeenCalled()
    expect(startSpy).not.toHaveBeenCalled()
    expect(superStopSpy).not.toHaveBeenCalled()
  })

  it('foreign 的禁用按钮点击无效：不发起任何 heal / 停止请求', async () => {
    mockApis()
    const healSpy = vi.spyOn(api.control, 'healPorts')
    renderPorts()

    const senate = await screen.findByTestId('port-diag-peer-senate')
    const blocked = await within(senate).findByTestId('port-diag-heal-senate')
    fireEvent.click(blocked)

    expect(healSpy).not.toHaveBeenCalled()
    expect(screen.queryByTestId('port-diag-result-senate')).toBeNull()
  })

  it('后端 heal 返回 ok=false 时如实展示失败原因，不假装已修复', async () => {
    mockApis()
    vi.spyOn(api.control, 'healPorts').mockResolvedValue({
      ok: false,
      peer: 'wire',
      action: 'restart',
      detail: '重拉失败：端口仍被占用',
      ports: diagnosis().peers[1].ports,
      health: { ok: false, detail: '健康检查失败' },
    } satisfies PortHealResult)
    renderPorts()

    const wire = await screen.findByTestId('port-diag-peer-wire')
    fireEvent.click(await within(wire).findByTestId('port-diag-heal-wire'))

    const result = await screen.findByTestId('port-diag-result-wire')
    expect(result.textContent).toContain('重拉失败：端口仍被占用')
    expect(result.querySelector('[role="alert"]')).toBeTruthy()
  })
})

describe('QUOT-548 面板状态与刷新', () => {
  it('控制面未接入：面板如实说明无法诊断，不发起诊断请求', async () => {
    vi.spyOn(api.control, 'board').mockRejectedValue(new ApiError('not-connected', 'offline'))
    const portsSpy = vi.spyOn(api.control, 'ports')
    renderPorts()

    expect(await screen.findByText('控制面未接入，无法诊断')).toBeTruthy()
    expect(portsSpy).not.toHaveBeenCalled()
  })

  it('诊断接口失败：段内错误块 + 重试，不整屏失败', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(board())
    vi.spyOn(api.control, 'ports').mockRejectedValue(new ApiError('http', 'HTTP 500', 500, '诊断失败'))
    vi.spyOn(api.supervisor, 'state').mockResolvedValue({
      ok: true,
      engine: { running: true, window_seconds: 300, max_restarts: 3, backoff: [5, 15, 45] },
      modules: [],
      configured: true,
    })
    renderPorts()

    expect(await screen.findByText('诊断失败')).toBeTruthy()
    // 端口占用表仍照常渲染（段间互不 gate）
    expect(screen.getByText('端口占用')).toBeTruthy()
  })

  it('「重新诊断」重取诊断并清掉上一次自愈结果', async () => {
    mockApis()
    vi.spyOn(api.control, 'healPorts').mockResolvedValue({
      ok: true,
      peer: 'wire',
      action: 'restart',
      detail: '已停止并重拉本方子进程（own_child）',
      ports: diagnosis().peers[1].ports,
      health: { ok: true, detail: 'HTTP 200' },
    } satisfies PortHealResult)
    renderPorts()

    const wire = await screen.findByTestId('port-diag-peer-wire')
    fireEvent.click(await within(wire).findByTestId('port-diag-heal-wire'))
    await screen.findByTestId('port-diag-result-wire')

    fireEvent.click(screen.getByTestId('port-diag-refresh'))
    await waitFor(() => expect(screen.queryByTestId('port-diag-result-wire')).toBeNull())
    expect(api.control.ports).toHaveBeenCalledTimes(2)
  })
})

describe('QUOT-548 视图口径纯函数（只翻译，不判定）', () => {
  it('四分类标签与语气固定', () => {
    expect(portClassView('own_child').label).toBe('自有残留')
    expect(portClassView('adopted_peer').label).toBe('已接管')
    expect(portClassView('foreign').label).toBe('外来进程')
    expect(portClassView('free').label).toBe('空闲')
  })

  it('健康三态：true 正常 / false 异常 / null 未探测', () => {
    expect(portHealthView({ ok: true })).toEqual({ label: '正常', tone: 'ok' })
    expect(portHealthView({ ok: false })).toEqual({ label: '异常', tone: 'danger' })
    expect(portHealthView({ ok: null })).toEqual({ label: '未探测', tone: 'flat' })
  })

  it('安全动作优先级与后端 heal_ports 一致：adopted > own_child > foreign > free', () => {
    const mixed = peerDiag('x', [entry(1, 'mcp', 'own_child'), entry(2, 'web', 'adopted_peer')])
    expect(safeActionView(mixed).kind).toBe('recheck')
    const ownForeign = peerDiag('x', [entry(1, 'mcp', 'own_child'), entry(2, 'web', 'foreign')])
    expect(safeActionView(ownForeign).kind).toBe('restart')
    expect(safeActionView(ownForeign).enabled).toBe(true)
    const foreignOnly = peerDiag('x', [entry(1, 'mcp', 'foreign')])
    expect(safeActionView(foreignOnly).enabled).toBe(false)
    expect(safeActionView(foreignOnly).reason).toContain('不会终止外来进程')
    const freeOnly = peerDiag('x', [entry(1, 'mcp', 'free')])
    expect(safeActionView(freeOnly).kind).toBe('idle')
    expect(safeActionView(freeOnly).enabled).toBe(false)
  })

  it('白话原因：已接管 + 健康正常 → 运行正常；健康失败 → 引导复检', () => {
    const adopted = entry(8000, 'mcp', 'adopted_peer')
    expect(portPlainReason(adopted, 'TickHub-CN', true)).toContain('运行正常')
    expect(portPlainReason(adopted, 'TickHub-CN', false)).toContain('复检健康')
    expect(portPlainReason(adopted, 'TickHub-CN', null)).not.toContain('运行正常')
    expect(portPlainReason(entry(1, 'mcp', 'foreign'), 'X', false)).toContain('不会终止外来进程')
  })
})