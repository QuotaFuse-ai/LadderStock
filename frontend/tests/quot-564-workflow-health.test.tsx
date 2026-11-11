// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, type SupervisorStatePayload } from '../src/api'
import { WorkflowHealthMonitor } from '../src/components/supervisor/WorkflowHealthMonitor'
import { formatTime } from '../src/lib/format'
import type { WorkflowStageEvent } from '../src/lib/workflowStages'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * QUOT-564：粒子化工作流健康监控组件。
 *
 * 锁死五件事：
 * 1. 健康环/详情只渲染后端 stage 事件的 health/reason/as_of（前端不重算，QUOT-500）；
 * 2. 粒子只在有后端 ok 事件时流动；warn/down/无事件一律静止，绝不伪造流动；
 * 3. 事件未接入如实降级「未接入 / 等待事件」，不编 ok、不回落演示数据（§5.6）；
 * 4. 键盘可达（原生 button + aria-pressed）、详情 aria-live、reduced-motion 静态降级；
 * 5. P2（召回→结论）虚线占位不接事件；挂载在设置·子模块页，与 S5/S6/S8 不同文件。
 */

function stageEvent(overrides: Partial<WorkflowStageEvent> = {}): WorkflowStageEvent {
  return {
    module: 'collect',
    module_class: 'data',
    stage: 'collect_script',
    health: 'ok',
    reason: '采集脚本已就绪',
    as_of: '2026-11-11T09:30:00+08:00',
    phase: '1',
    ...overrides,
  }
}

/** 全管线注入：collect 三态混合、ingest 全 ok、debate warn/down/ok。 */
function liveEvents(): WorkflowStageEvent[] {
  return [
    stageEvent({ stage: 'collect_script', health: 'ok', reason: '采集脚本已就绪', phase: '1' }),
    stageEvent({ stage: 'collect_process', health: 'warn', reason: '采集进程响应变慢', phase: '2' }),
    stageEvent({ stage: 'collect_store', health: 'down', reason: '采集库今日无记录', phase: '3' }),
    stageEvent({ stage: 'collect_success', health: 'ok', reason: '本次采集成功产出当日数据', phase: '4' }),
    stageEvent({ module: 'ingest', stage: 'ingest_watermark', health: 'ok', reason: '同步水位已到今天', phase: '1' }),
    stageEvent({ module: 'ingest', stage: 'ingest_persist', health: 'ok', reason: '已写入本机 SQLite', phase: '2' }),
    stageEvent({ module: 'ingest', stage: 'ingest_idempotent', health: 'ok', reason: '重复同步无重复落库', phase: '3' }),
    stageEvent({ module: 'debate', module_class: 'cog', stage: 'debate_speak', health: 'warn', reason: '等待角色发言', phase: '1' }),
    stageEvent({ module: 'debate', module_class: 'cog', stage: 'debate_clash', health: 'down', reason: '交锋未开始', phase: '2' }),
    stageEvent({ module: 'debate', module_class: 'cog', stage: 'debate_converge', health: 'ok', reason: '已收敛出结论', phase: '3' }),
  ]
}

function statePayload(events?: WorkflowStageEvent[]): SupervisorStatePayload {
  return {
    ok: true,
    engine: { running: true, window_seconds: 300, max_restarts: 3, backoff: [5, 15, 45] },
    modules: [],
    configured: true,
    ...(events ? { stage_events: events } : {}),
  } as SupervisorStatePayload
}

function mockState(events?: WorkflowStageEvent[]) {
  vi.spyOn(api.supervisor, 'state').mockResolvedValue(statePayload(events))
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('契约渲染：健康环 / 粒子 / 详情只反映后端值', () => {
  it('ok/warn/down 各态注入 → 环弧逐段直渲 health，详情给白话 reason + as_of', async () => {
    mockState(liveEvents())
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-monitor')
    // collect 四段弧：ok/warn/down/ok，颜色口径由 data-health 直读后端值
    await waitFor(() => expect(screen.getByTestId('wf-arc-collect-collect_script').getAttribute('data-health')).toBe('ok'))
    expect(screen.getByTestId('wf-arc-collect-collect_process').getAttribute('data-health')).toBe('warn')
    expect(screen.getByTestId('wf-arc-collect-collect_store').getAttribute('data-health')).toBe('down')
    expect(screen.getByTestId('wf-arc-ingest-ingest_persist').getAttribute('data-health')).toBe('ok')
    expect(screen.getByTestId('wf-arc-debate-debate_clash').getAttribute('data-health')).toBe('down')

    // 默认选中 collect：详情逐条给后端 reason 原文 + as_of（title 保留 ISO 原文）
    const detail = screen.getByTestId('wf-stage-detail')
    expect(within(detail).getByText('采集脚本已就绪')).toBeTruthy()
    expect(within(detail).getByText('采集进程响应变慢')).toBeTruthy()
    expect(within(detail).getByText('采集库今日无记录')).toBeTruthy()
    const scriptRow = within(detail).getByTestId('wf-stage-collect-collect_script')
    expect(within(scriptRow).getByText('正常')).toBeTruthy()
    const time = within(scriptRow).getByTitle('2026-11-11T09:30:00+08:00')
    expect(time.textContent).toBe(formatTime('2026-11-11T09:30:00+08:00'))
    // 阶段按后端 phase 排序（1→4），未知字段不编造
    const rows = within(detail).getAllByTestId(/^wf-stage-collect-/)
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual([
      'wf-stage-collect-collect_script',
      'wf-stage-collect-collect_process',
      'wf-stage-collect-collect_store',
      'wf-stage-collect-collect_success',
    ])
  })

  it('粒子：上游有 ok 才流动；debate→recall（P2）虚线永不流动', async () => {
    mockState(liveEvents())
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-edge-collect-ingest')
    expect(screen.getByTestId('wf-edge-collect-ingest').getAttribute('data-flow')).toBe('on')
    expect(screen.getByTestId('wf-edge-ingest-debate').getAttribute('data-flow')).toBe('on')
    expect(screen.getByTestId('wf-edge-debate-recall').getAttribute('data-flow')).toBe('off')
    expect(screen.getByTestId('wf-edge-recall-conclusion').getAttribute('data-flow')).toBe('off')
    // 两条 P1 边各 3 粒，P2 边 0 粒
    expect(screen.getAllByTestId('wf-particle')).toHaveLength(6)
  })

  it('前端不重算：as_of 很旧但后端报 ok 仍渲染正常；reason 原样透传', async () => {
    mockState([
      stageEvent({ health: 'ok', reason: '上游无当日数据', as_of: '2020-01-01T00:00:00+08:00' }),
    ])
    render(<WorkflowHealthMonitor />)

    const detail = await screen.findByTestId('wf-stage-detail')
    // 不按 as_of 推断陈旧：后端说 ok 就是正常
    expect(within(detail).getByText('正常')).toBeTruthy()
    expect(within(detail).getByText('上游无当日数据')).toBeTruthy()
    expect(screen.getByTestId('wf-arc-collect-collect_script').getAttribute('data-health')).toBe('ok')
  })

  it('部分模块无事件：该模块等待事件、其下游边静止，不编造健康', async () => {
    mockState([stageEvent()])
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-edge-collect-ingest')
    expect(screen.getByTestId('wf-edge-collect-ingest').getAttribute('data-flow')).toBe('on')
    expect(screen.getByTestId('wf-edge-ingest-debate').getAttribute('data-flow')).toBe('off')
    expect(screen.getByTestId('wf-ring-ingest-empty')).toBeTruthy()
    expect(screen.getByTestId('wf-ring-debate-empty')).toBeTruthy()
    expect(within(screen.getByTestId('wf-node-ingest')).getByText('等待事件')).toBeTruthy()
    expect(screen.getAllByTestId('wf-particle')).toHaveLength(3)
  })

  it('非法事件条目被丢弃：只渲染合法事件，不补默认健康态', async () => {
    mockState([
      stageEvent(),
      { ...stageEvent({ stage: 'collect_process' }), health: 'healthy' } as unknown as WorkflowStageEvent,
    ])
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-arc-collect-collect_script')
    expect(screen.queryByTestId('wf-arc-collect-collect_process')).toBeNull()
    expect(within(screen.getByTestId('wf-node-collect')).getByText('1 个阶段')).toBeTruthy()
  })
})

describe('如实降级：未接入 / 等待事件，绝不编 ok、不伪造流动', () => {
  it('通道已连接但无 stage 事件 → 等待事件横幅 + 全节点等待 + 零粒子零健康弧', async () => {
    mockState()
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-banner-waiting')
    for (const id of ['collect', 'ingest', 'debate']) {
      expect(within(screen.getByTestId(`wf-node-${id}`)).getByText('等待事件')).toBeTruthy()
      expect(screen.getByTestId(`wf-ring-${id}-empty`)).toBeTruthy()
    }
    expect(screen.queryAllByTestId('wf-particle')).toHaveLength(0)
    expect(screen.queryAllByTestId(/^wf-arc-/)).toHaveLength(0)
    const detail = screen.getByTestId('wf-stage-detail')
    expect(within(detail).getByText(/还没有阶段事件/)).toBeTruthy()
    expect(within(detail).queryByText('正常')).toBeNull()
  })

  it('通道未接入（API 未连接）→ 未接入横幅 + 骨架，不回落演示数据', async () => {
    vi.spyOn(api.supervisor, 'state').mockRejectedValue(new ApiError('not-connected', '本地服务未连接'))
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-banner-offline')
    expect(screen.getByTestId('wf-banner-offline').textContent).toContain('未接入')
    expect(screen.queryAllByTestId('wf-particle')).toHaveLength(0)
    expect(screen.queryAllByTestId(/^wf-arc-/)).toHaveLength(0)
    expect(within(screen.getByTestId('wf-node-collect')).getByText('等待事件')).toBeTruthy()
  })

  it('events 信封的 stage 事件同样被消费（S3 类型化事件流）', async () => {
    vi.spyOn(api.supervisor, 'state').mockResolvedValue({
      ok: true,
      engine: null,
      modules: [],
      events: [
        { type: 'crash', module: 'collect' },
        { type: 'stage', payload: stageEvent({ module: 'debate', module_class: 'cog', stage: 'debate_speak', health: 'warn', reason: '等待角色发言' }) },
      ],
    } as unknown as SupervisorStatePayload)
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-arc-debate-debate_speak')
    expect(screen.queryByTestId('wf-banner-waiting')).toBeNull()
    expect(screen.getByTestId('wf-arc-debate-debate_speak').getAttribute('data-health')).toBe('warn')
  })
})

describe('可达性与主题降级', () => {
  it('节点是原生 button：键盘可达，点击切换详情，aria-pressed / aria-live 就位', async () => {
    mockState(liveEvents())
    render(<WorkflowHealthMonitor />)

    const debateNode = await screen.findByTestId('wf-node-debate')
    expect(debateNode.tagName).toBe('BUTTON')
    expect(debateNode.getAttribute('aria-controls')).toBe('wf-stage-detail')
    expect(debateNode.getAttribute('aria-pressed')).toBe('false')

    debateNode.focus()
    expect(document.activeElement).toBe(debateNode)
    fireEvent.click(debateNode)

    expect(debateNode.getAttribute('aria-pressed')).toBe('true')
    const detail = screen.getByTestId('wf-stage-detail')
    expect(detail.getAttribute('aria-live')).toBe('polite')
    expect(detail.getAttribute('role')).toBe('region')
    await waitFor(() => expect(within(detail).getByText('等待角色发言')).toBeTruthy())
    expect(within(detail).getByText('已收敛出结论')).toBeTruthy()
    // 切换后 collect 节点不再选中
    expect(screen.getByTestId('wf-node-collect').getAttribute('aria-pressed')).toBe('false')
  })

  it('P2 召回/结论是非交互虚线占位徽章，不进键盘序', async () => {
    mockState(liveEvents())
    render(<WorkflowHealthMonitor />)

    for (const id of ['recall', 'conclusion']) {
      const node = await screen.findByTestId(`wf-node-${id}`)
      expect(node.tagName).toBe('DIV')
      expect(node.className).toContain('border-dashed')
      expect(within(node).getByText('P2 · 规划中')).toBeTruthy()
    }
  })

  it('prefers-reduced-motion：粒子动画换成静态流向点', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    }))
    mockState(liveEvents())
    render(<WorkflowHealthMonitor />)

    await screen.findByTestId('wf-edge-collect-ingest')
    expect(screen.getAllByTestId('wf-particle-static')).toHaveLength(6)
    expect(screen.queryAllByTestId('wf-particle')).toHaveLength(0)
    vi.unstubAllGlobals()
  })
})

describe('轮询与挂载', () => {
  it('5 秒节拍静默轮询健康通道（与 S5 同一通道）', async () => {
    vi.useFakeTimers()
    try {
      const spy = vi.spyOn(api.supervisor, 'state').mockResolvedValue(statePayload())
      render(<WorkflowHealthMonitor />)
      await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(1))
      await vi.advanceTimersByTimeAsync(5000)
      expect(spy).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(5000)
      expect(spy).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('挂载在设置·子模块页顶部（peers 分区第一块）', async () => {
    mockState()
    vi.spyOn(api.control, 'board').mockResolvedValue({
      ok: true,
      control_enabled: true,
      peers: [],
      registry: [],
      components: [],
      port_conflicts: [],
      ports: { board: 5950, mcp: 5951 },
    })
    vi.spyOn(api, 'stateConfig').mockRejectedValue(new ApiError('not-connected', 'offline'))
    vi.spyOn(api, 'health').mockRejectedValue(new ApiError('not-connected', 'offline'))
    render(
      <MemoryRouter initialEntries={['/settings?tab=peers']}>
        <SettingsScreen />
      </MemoryRouter>,
    )

    const monitor = await screen.findByTestId('wf-monitor')
    expect(monitor).toBeTruthy()
    expect(screen.getByTestId('wf-banner-waiting')).toBeTruthy()
  })
})