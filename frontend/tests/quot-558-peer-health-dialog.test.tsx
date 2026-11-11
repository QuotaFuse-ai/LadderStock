// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SupervisorHealthEvent } from '../src/api'
import PeerHealthAlert, {
  PeerHealthDialog,
  eventTitle,
} from '../src/components/supervisor/PeerHealthDialog'
import {
  dismissAlert,
  nextAlerts,
  pickActive,
  usePeerHealth,
  type PeerHealthApi,
} from '../src/lib/usePeerHealth'

/**
 * 子模块健康全局弹窗 + 一键确认重启（QUOT-558，拆自 QUOT-519）。
 *
 * 三层各测各的：状态机是纯函数（recovered 清表 / 坏事件按 seq 覆盖 /
 * dismiss 按 module 记 seq）；取数层验证「首拍只对齐不弹历史」与
 * recovered 自动关窗；组件层验证白话文案、确认重启接真
 * `POST /api/supervisor/restart {confirm:true}`、外部占用绝不暗示可终止、
 * 弹窗内零 CLI。
 */

function ev(partial: Partial<SupervisorHealthEvent> & { seq: number; module: string }): SupervisorHealthEvent {
  return {
    ts: '2026-11-12T09:00:00+00:00',
    event: 'crash',
    health: 'crashed',
    state: 'failed',
    reason: '行情中心进程意外退出，已尝试自动恢复未成功。',
    pid: 1234,
    ports: [8101],
    origin: 'spawned',
    adopted_peer: false,
    conflicts: [],
    ...partial,
  }
}

// --------------------------------------------------------------------------- //
// 纯状态机
// --------------------------------------------------------------------------- //

describe('nextAlerts', () => {
  it('坏事件入表，同模块新 seq 覆盖旧 seq', () => {
    const first = nextAlerts({}, [ev({ seq: 5, module: 'tickhub' })])
    expect(Object.keys(first)).toEqual(['tickhub'])

    const newer = ev({ seq: 7, module: 'tickhub', event: 'offline' })
    expect(Object.keys(nextAlerts(first, [newer]))).toEqual(['tickhub'])
    expect(nextAlerts(first, [newer]).tickhub?.seq).toBe(7)

    // 乱序到达的旧事件不回退弹窗
    const older = ev({ seq: 4, module: 'tickhub', reason: '旧' })
    expect(nextAlerts(first, [older]).tickhub?.seq).toBe(5)
  })

  it('recovered 清掉对应模块，别的模块不受牵连', () => {
    const alerts = nextAlerts({}, [
      ev({ seq: 5, module: 'tickhub' }),
      ev({ seq: 6, module: 'wire', event: 'offline' }),
    ])
    expect(Object.keys(alerts).sort()).toEqual(['tickhub', 'wire'])

    const after = nextAlerts(alerts, [ev({ seq: 9, module: 'tickhub', event: 'recovered' })])
    expect(Object.keys(after)).toEqual(['wire'])
  })

  it('没有活跃坏事件时 recovered 是空操作；非坏非恢复事件不消费', () => {
    expect(nextAlerts({}, [ev({ seq: 1, module: 'tickhub', event: 'recovered' })])).toEqual({})
    // 'recovered' 之外只有四种坏事件；其它取值一律不入表
    const alerts = nextAlerts({}, [
      ev({ seq: 2, module: 'tickhub', event: 'crash' }),
      ev({ seq: 3, module: 'tickhub', event: 'offline' }),
    ])
    expect(alerts.tickhub?.event).toBe('offline')
  })
})

describe('dismissAlert / pickActive', () => {
  it('dismiss 记录 module → seq；旧 seq 不回退', () => {
    const d1 = dismissAlert({}, ev({ seq: 5, module: 'tickhub' }))
    expect(d1.tickhub).toBe(5)
    expect(dismissAlert(d1, ev({ seq: 4, module: 'tickhub' }))).toBe(d1)
    const d2 = dismissAlert(d1, ev({ seq: 6, module: 'tickhub' }))
    expect(d2.tickhub).toBe(6)
  })

  it('pickActive 只给未被关闭的最新事件；全关了 → null', () => {
    const tickhub = ev({ seq: 5, module: 'tickhub' })
    const wire = ev({ seq: 6, module: 'wire', event: 'offline' })
    const alerts = nextAlerts({}, [tickhub, wire])

    expect(pickActive(alerts, {})?.module).toBe('wire')

    // 关掉 wire 后轮到 tickhub；两条都关 → null
    let dismissed = dismissAlert({}, wire)
    expect(pickActive(alerts, dismissed)?.module).toBe('tickhub')
    dismissed = dismissAlert(dismissed, tickhub)
    expect(pickActive(alerts, dismissed)).toBeNull()

    // 同模块新 seq（> dismissed）仍会弹
    const newer = ev({ seq: 8, module: 'wire', event: 'port_conflict' })
    expect(pickActive(nextAlerts(alerts, [newer]), dismissed)?.seq).toBe(8)
  })
})

// --------------------------------------------------------------------------- //
// 取数层：首拍只对齐不弹历史；recovered 自动关窗；通道停用即停轮询
// --------------------------------------------------------------------------- //

type PeerHealthOptions = NonNullable<Parameters<typeof usePeerHealth>[0]>

function Harness({
  apiRef,
  intervalMs = 1000,
  fetchEvents,
  restartModule,
}: {
  apiRef?: { current: PeerHealthApi | null }
  intervalMs?: number
  fetchEvents?: PeerHealthOptions['fetchEvents']
  restartModule?: PeerHealthOptions['restartModule']
}) {
  const api = usePeerHealth({ intervalMs, fetchEvents, restartModule })
  if (apiRef) apiRef.current = api
  return (
    <div>
      <span data-testid="active">{api.active ? `${api.active.module}:${api.active.seq}` : '-'}</span>
      <span data-testid="pending">{api.pendingCount}</span>
    </div>
  )
}

function eventsPayload(latest: number, events: SupervisorHealthEvent[]) {
  return { ok: true, configured: true, latest, events }
}

describe('usePeerHealth 轮询', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
  })

  it('首拍只对齐 latest 不消费历史；下一拍新坏事件才弹', async () => {
    const history = ev({ seq: 5, module: 'tickhub' })
    let call = 0
    const fetchEvents = vi.fn(async () => {
      call += 1
      return call === 1 ? eventsPayload(5, [history]) : eventsPayload(6, [])
    })

    render(<Harness fetchEvents={fetchEvents} intervalMs={1000} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    // 页面打开前的历史事件（用户没见过现场）不打扰
    expect(screen.getByTestId('active').textContent).toBe('-')
    expect(fetchEvents).toHaveBeenLastCalledWith(0)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    // 第二拍起从 latest 继续，不重复弹旧事件
    expect(screen.getByTestId('active').textContent).toBe('-')
    expect(fetchEvents).toHaveBeenLastCalledWith(5)
  })

  it('会话内新坏事件入表弹窗，recovered 自动关窗', async () => {
    const crash = ev({ seq: 6, module: 'wire' })
    let call = 0
    const fetchEvents = vi.fn(async () => {
      call += 1
      if (call === 1) return eventsPayload(5, [])
      if (call === 2) return eventsPayload(6, [crash])
      return eventsPayload(7, [ev({ seq: 7, module: 'wire', event: 'recovered' })])
    })

    render(<Harness fetchEvents={fetchEvents} intervalMs={1000} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(screen.getByTestId('active').textContent).toBe('wire:6')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(screen.getByTestId('active').textContent).toBe('-')
  })

  it('configured=false 停止轮询，不再发请求', async () => {
    let call = 0
    const fetchEvents = vi.fn(async () => {
      call += 1
      return call === 1 ? eventsPayload(5, []) : { ok: true, configured: false, latest: 0, events: [] }
    })

    render(<Harness fetchEvents={fetchEvents} intervalMs={1000} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    // 第二拍发现通道未接线 → 停；记录此时的调用数
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    const calls = fetchEvents.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fetchEvents.mock.calls.length).toBe(calls)
  })

  it('确认重启成功 → POST 对应模块后弹窗关闭；失败 → 原因如实展示', async () => {
    const crash = ev({ seq: 6, module: 'tickhub' })
    let call = 0
    const fetchEvents = vi.fn(async () => {
      call += 1
      return call === 1 ? eventsPayload(5, []) : eventsPayload(6, [crash])
    })
    const restartModule = vi.fn(async (module: string) =>
      module === 'tickhub' ? { ok: false, error: '该端口仍被外部程序占用' } : { ok: true, error: null },
    )

    const apiRef = { current: null as PeerHealthApi | null }
    render(<Harness apiRef={apiRef} fetchEvents={fetchEvents} restartModule={restartModule} intervalMs={1000} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(apiRef.current?.active?.module).toBe('tickhub')

    await act(async () => {
      await apiRef.current?.confirmRestart()
    })
    expect(restartModule).toHaveBeenCalledWith('tickhub')
    expect(apiRef.current?.restartError).toBe('该端口仍被外部程序占用')
    // 失败不关窗：用户还得看到原因
    expect(apiRef.current?.active?.module).toBe('tickhub')
  })
})

// --------------------------------------------------------------------------- //
// 弹窗组件：白话文案 / 确认重启 / 外部占用口径 / 零 CLI
// --------------------------------------------------------------------------- //

const CLI_PATTERN = /ldst\s+\w|qfw\s+\w|taskkill|kill\s+-|argv|--port|命令行|复制命令|终端里|在终端/

function renderDialog(partial: Partial<Parameters<typeof PeerHealthDialog>[0]> = {}) {
  const props: Parameters<typeof PeerHealthDialog>[0] = {
    event: ev({ seq: 6, module: 'tickhub' }),
    onConfirm: vi.fn(),
    onClose: vi.fn(),
    ...partial,
  }
  render(<PeerHealthDialog {...props} />)
  return props
}

describe('PeerHealthDialog', () => {
  afterEach(cleanup)

  it('crash：白话标题 + 后端 reason 直出', () => {
    renderDialog({ productName: () => '行情中心' })
    expect(screen.getByText('行情中心已崩溃退出')).toBeTruthy()
    expect(screen.getByText(/进程意外退出/)).toBeTruthy()
  })

  it('offline / port_conflict / needs_human 标题各有白话', () => {
    const label = '行情中心'
    const base = { module: 'tickhub', seq: 1 }
    expect(eventTitle({ ...ev(base), event: 'offline' }, label)).toBe('行情中心已掉线')
    expect(eventTitle({ ...ev(base), event: 'port_conflict' }, label)).toBe(
      '行情中心的服务端口被其它程序占用',
    )
    expect(eventTitle({ ...ev(base), event: 'needs_human' }, label)).toBe(
      '行情中心自动恢复失败，需要确认后重启',
    )
  })

  it('主操作是「确认重启」，点击触发 onConfirm；「稍后再说」触发 onClose', () => {
    const props = renderDialog()
    fireEvent.click(screen.getByRole('button', { name: '确认重启' }))
    expect(props.onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '稍后再说' }))
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it('重启中 loading：两个按钮都禁用，防重复点击', () => {
    renderDialog({ restarting: true })
    for (const name of ['确认重启', '稍后再说']) {
      const button = screen.getByRole('button', { name })
      expect(button.hasAttribute('disabled')).toBe(true)
    }
  })

  it('adopted peer：标「外部启动」，说明确认只拉替代实例且进程活着会被拒绝', () => {
    renderDialog({
      event: ev({ seq: 6, module: 'proxyfleet', adopted_peer: true, origin: 'adopted' }),
      productName: () => '代理舰队',
    })
    expect(screen.getByText('外部启动')).toBeTruthy()
    expect(screen.getByText(/不会终止任何外部程序/)).toBeTruthy()
    expect(screen.getByText(/重启会被拒绝并说明原因/)).toBeTruthy()
  })

  it('端口冲突 foreign：引导确认，绝不暗示可终止外部程序', () => {
    renderDialog({
      event: ev({
        seq: 6,
        module: 'tickhub',
        event: 'port_conflict',
        reason: '行情中心需要的端口被其它程序先占了。',
        conflicts: [{ port: 8101, owner: 'foreign', pid: 4242 }],
      }),
    })
    const line = screen.getByTestId('conflict-8101').textContent ?? ''
    expect(line).toContain('无关的外部程序')
    expect(line).toContain('不会终止它')
    expect(line).not.toMatch(/结束它|杀掉|强制关闭.*外部/)
  })

  it('端口冲突 own_child：说明确认重启可安全恢复', () => {
    renderDialog({
      event: ev({
        seq: 6,
        module: 'tickhub',
        event: 'port_conflict',
        conflicts: [{ port: 8101, owner: 'own_child', pid: 1001 }],
      }),
    })
    expect(screen.getByTestId('conflict-8101').textContent).toContain('安全重启该进程恢复')
  })

  it('重启失败原因在弹窗内如实展示（role=alert），不静默', () => {
    renderDialog({ restartError: '该端口仍被外部程序占用' })
    const alert = screen.getByTestId('peer-restart-error')
    expect(alert.textContent).toContain('重启未成功')
    expect(alert.textContent).toContain('该端口仍被外部程序占用')
    expect(screen.getByRole('alert')).toBeTruthy()
  })

  it('多个模块同时坏：给出剩余数量的预期', () => {
    renderDialog({ pendingCount: 3 })
    expect(screen.getByTestId('peer-pending-more').textContent).toContain('2 个模块')
  })

  it('零 CLI：弹窗全文不出现命令行 / 复制命令 / argv / 终端指引', () => {
    renderDialog({
      event: ev({
        seq: 6,
        module: 'tickhub',
        event: 'port_conflict',
        conflicts: [{ port: 8101, owner: 'foreign', pid: 4242 }],
      }),
      restartError: '该端口仍被外部程序占用',
      pendingCount: 2,
    })
    // 「进程号」是白话；数字 pid 允许出现，但命令行语法不允许
    expect(document.body.textContent ?? '').not.toMatch(CLI_PATTERN)
  })

  it('无活跃事件时不渲染', () => {
    render(<PeerHealthDialog event={null} onConfirm={vi.fn()} onClose={vi.fn()} />)
    expect(screen.queryByText(/确认重启/)).toBeNull()
  })
})

// --------------------------------------------------------------------------- //
// 全链路（挂载层 PeerHealthAlert，stub fetch 走真 api 层）：
// crash 弹窗自动出现 → 确认重启 POST /api/supervisor/restart {confirm:true} → 弹窗关闭
// --------------------------------------------------------------------------- //

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

describe('PeerHealthAlert 全链路', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    cleanup()
  })

  /** fake timers 下 waitFor 会与定时器探测打架；确认重启的 fetch 链只走微任务，直接冲刷 */
  async function flushMicrotasks() {
    await act(async () => {
      for (let i = 0; i < 10; i += 1) await Promise.resolve()
    })
  }

  it('crash → 全局弹窗出现（白话产品名）→ 确认重启带 confirm:true → 成功自动关窗', async () => {
    const crash = ev({ seq: 6, module: 'tickhub', reason: '行情中心进程意外退出。' })
    let tick = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/supervisor/events')) {
        tick += 1
        return jsonResponse(
          tick === 1 ? eventsPayload(5, []) : eventsPayload(6, [crash]),
        )
      }
      if (url.endsWith('/control/peers')) {
        return jsonResponse({ ok: true, peers: [{ id: 'tickhub', product: '行情中心' }], components: [], registry: [] })
      }
      if (url.endsWith('/supervisor/restart')) {
        expect(init?.method).toBe('POST')
        expect(JSON.parse(String(init?.body))).toEqual({ modules: ['tickhub'], confirm: true })
        return jsonResponse({ ok: true, modules: { tickhub: { ok: true, error: null } } })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<PeerHealthAlert />)
    // 首拍对齐：无历史弹窗
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.queryByText(/已崩溃退出/)).toBeNull()

    // 第二拍收到 crash → 全局弹窗自动出现，产品名来自 board
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000)
    })
    expect(screen.getByText('行情中心已崩溃退出')).toBeTruthy()
    expect(screen.getByText('行情中心进程意外退出。')).toBeTruthy()

    // 确认重启：接真 /restart，body 带 confirm:true
    fireEvent.click(screen.getByRole('button', { name: '确认重启' }))
    await flushMicrotasks()
    // 成功即视为已处理：弹窗自动关闭
    expect(screen.queryByRole('dialog')).toBeNull()
    const restartCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith('/supervisor/restart'),
    )
    expect(restartCalls.length).toBe(1)
  })

  it('重启失败：弹窗保持打开并展示后端原因', async () => {
    const crash = ev({ seq: 6, module: 'tickhub' })
    let tick = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/supervisor/events')) {
        tick += 1
        return jsonResponse(
          tick === 1 ? eventsPayload(5, []) : eventsPayload(6, [crash]),
        )
      }
      if (url.endsWith('/control/board')) {
        return jsonResponse({ ok: true, peers: [], components: [], registry: [] })
      }
      if (url.endsWith('/supervisor/restart')) {
        return jsonResponse({ ok: true, modules: { tickhub: { ok: false, error: '强杀后端口仍被占用' } } })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<PeerHealthAlert />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000)
    })
    fireEvent.click(screen.getByRole('button', { name: '确认重启' }))
    await flushMicrotasks()
    expect(screen.getByTestId('peer-restart-error').textContent).toContain('强杀后端口仍被占用')
    // 失败不关窗
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})
