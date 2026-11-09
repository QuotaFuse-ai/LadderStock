// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  api,
  type BoardPayload,
  type PeerSnapshot,
  type ProxyMapping,
  type SupervisorModule,
  type SupervisorStatePayload,
} from '../src/api'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * QUOT-507：代理池卡片。
 *
 * 锁死 issue 里的硬约束：
 * 1. 归属只显示后端 `owner`（AI 使用 / 人工使用）；AI 行默认折叠成一行摘要；
 * 2. 四种状态文案可区分；冲突必须显示端口号且不给重试；needs_human 才给重试；
 * 3. 探活显示真实 rtt_ms（不写「正常」）；
 * 4. 改上游后提示「配置已保存，需手动启动生效」，且**不自动重启**；
 * 5. 未启动态明确写「已停止」，启动按钮可用。
 */

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
  }
}

function board(): BoardPayload {
  return {
    ok: true,
    control_enabled: true,
    peers: [peer('tickhub', 'TickHub-CN')],
    registry: [],
    components: [],
    port_conflicts: [],
    ports: { board: 5950, mcp: 5951 },
  }
}

function mapping(overrides: Partial<ProxyMapping> & Pick<ProxyMapping, 'id'>): ProxyMapping {
  return {
    listen: '8788',
    upstream_host: '127.0.0.1',
    upstream_port: 18080,
    enabled: true,
    owner: 'human',
    state: 'running',
    rtt_ms: null,
    ...overrides,
  }
}

function poolModule(mappings: ProxyMapping[]): SupervisorModule {
  return {
    module: 'proxypool',
    ports: [8788, 8789],
    pid: 900,
    health: 'ok',
    state: 'running',
    restarts_5min: 0,
    restart_count: 0,
    stopping_by_user: false,
    origin: 'spawned',
    cmdline_hash: 'poolhash',
    last_exit_reason: null,
    self_heal: { attempts: 0, max: 3, status: 'ok' },
    ownership: 'managed',
    listening: true,
    mappings,
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
  poolModule([
    mapping({ id: 'h-run', listen: '8788', upstream_host: '10.0.0.1', upstream_port: 18080, owner: 'human', state: 'running', rtt_ms: 12 }),
    mapping({ id: 'h-stopped', listen: '8789', upstream_host: '10.0.0.2', upstream_port: 18081, owner: 'human', state: 'stopped' }),
    mapping({ id: 'h-conflict', listen: '8790', upstream_host: '10.0.0.3', upstream_port: 18082, owner: 'human', state: 'conflict', conflict_port: 8790 }),
    mapping({ id: 'h-needs', listen: '8791', upstream_host: '10.0.0.4', upstream_port: 18083, owner: 'human', state: 'needs_human' }),
    mapping({ id: 'ai-run-1', listen: '8801', upstream_host: '10.1.0.1', upstream_port: 18090, owner: 'ai', state: 'running' }),
    mapping({ id: 'ai-run-2', listen: '8802', upstream_host: '10.1.0.2', upstream_port: 18091, owner: 'ai', state: 'running' }),
    mapping({ id: 'ai-stopped', listen: '8803', upstream_host: '10.1.0.3', upstream_port: 18092, owner: 'ai', state: 'stopped' }),
  ]),
])

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={['/settings?tab=peers']}>
      <SettingsScreen />
    </MemoryRouter>,
  )
}

function mockApis(state: SupervisorStatePayload) {
  vi.spyOn(api.control, 'board').mockResolvedValue(board())
  vi.spyOn(api.supervisor, 'state').mockResolvedValue(state)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('QUOT-507 代理池：AI 归属折叠', () => {
  it('AI 行默认折叠成摘要，展开后才出现明细；人工行始终可见', async () => {
    mockApis(MIXED)
    renderSettings()

    const card = await screen.findByTestId('proxy-pool-card')
    expect(within(card).getByTestId('proxy-row-h-run')).toBeTruthy()
    expect(within(card).queryByTestId('proxy-row-ai-run-1')).toBeNull()

    const toggle = within(card).getByTestId('proxy-ai-toggle')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(within(card).getByText('3 条映射 · 2 条运行中')).toBeTruthy()

    fireEvent.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(within(card).getByTestId('proxy-row-ai-run-1')).toBeTruthy()
    expect(within(card).getByTestId('proxy-row-ai-stopped')).toBeTruthy()
    // 归属文案直接来自 owner 字段
    expect(within(card).getAllByText('AI 使用').length).toBeGreaterThan(0)
    expect(within(card).getAllByText('人工使用').length).toBeGreaterThan(0)
  })
})

describe('QUOT-507 代理池：四种状态', () => {
  it('已停止 / 冲突（带端口、无重试）/ 需人工处理（有重试）文案与入口正确', async () => {
    mockApis(MIXED)
    renderSettings()

    const card = await screen.findByTestId('proxy-pool-card')
    const stopped = within(card).getByTestId('proxy-row-h-stopped')
    expect(within(stopped).getByText('已停止')).toBeTruthy()

    const conflict = within(card).getByTestId('proxy-row-h-conflict')
    expect(within(conflict).getByText('冲突')).toBeTruthy()
    expect(within(conflict).getByText('端口 8790')).toBeTruthy()
    expect(within(conflict).queryByRole('button', { name: /重试/ })).toBeNull()

    const needs = within(card).getByTestId('proxy-row-h-needs')
    expect(within(needs).getByText('需人工处理（已停止自愈）')).toBeTruthy()
    expect(within(needs).getByRole('button', { name: '重试' })).toBeTruthy()
  })

  it('needs_human 的重试调用后端重启动作（pool start 指定 mapping）', async () => {
    mockApis(MIXED)
    const startSpy = vi.spyOn(api.supervisor, 'poolStart').mockResolvedValue({ ok: true, started: ['h-needs'] })
    renderSettings()

    const card = await screen.findByTestId('proxy-pool-card')
    fireEvent.click(within(within(card).getByTestId('proxy-row-h-needs')).getByRole('button', { name: '重试' }))

    await waitFor(() => expect(startSpy).toHaveBeenCalledWith({ mapping_ids: ['h-needs'] }))
  })
})

describe('QUOT-507 代理池：探活 RTT', () => {
  it('显示真实毫秒数，探活后更新为后端返回的 rtt_ms', async () => {
    mockApis(MIXED)
    const probeSpy = vi.spyOn(api.supervisor, 'poolProbe').mockResolvedValue({
      ok: true,
      results: [{ mapping_id: 'h-run', ok: true, rtt_ms: 37 }],
    })
    renderSettings()

    const card = await screen.findByTestId('proxy-pool-card')
    const runRow = within(card).getByTestId('proxy-row-h-run')
    expect(within(runRow).getByText(/12ms/)).toBeTruthy()

    fireEvent.click(within(card).getByTestId('proxy-probe'))

    await waitFor(() => expect(within(runRow).getByText(/37ms/)).toBeTruthy())
    expect(probeSpy).toHaveBeenCalledWith({
      mapping_ids: ['h-run', 'h-stopped', 'h-conflict', 'h-needs', 'ai-run-1', 'ai-run-2', 'ai-stopped'],
    })
    expect(within(card).getByTestId('proxy-notice').textContent).toContain('探活完成')
  })
})

describe('QUOT-507 代理池：改上游', () => {
  it('保存后提示「配置已保存，需手动启动生效」，且不自动启动 / 重启', async () => {
    mockApis(MIXED)
    const saveSpy = vi.spyOn(api.supervisor, 'poolSetUpstream').mockResolvedValue({ ok: true })
    const startSpy = vi.spyOn(api.supervisor, 'poolStart').mockResolvedValue({ ok: true })
    const stopSpy = vi.spyOn(api.supervisor, 'poolStop').mockResolvedValue({ ok: true })
    renderSettings()

    const card = await screen.findByTestId('proxy-pool-card')
    fireEvent.click(within(card).getByTestId('proxy-edit'))

    const host = within(card).getByTestId('proxy-edit-host-h-run') as HTMLInputElement
    fireEvent.change(host, { target: { value: '10.9.9.9' } })
    fireEvent.click(within(card).getByTestId('proxy-edit-save'))

    await waitFor(() => expect(saveSpy).toHaveBeenCalledWith({
      mapping_id: 'h-run',
      upstream_host: '10.9.9.9',
      upstream_port: 18080,
    }))
    expect((await within(card).findByTestId('proxy-notice')).textContent).toBe('配置已保存，需手动启动生效')
    expect(startSpy).not.toHaveBeenCalled()
    expect(stopSpy).not.toHaveBeenCalled()
  })
})

describe('QUOT-507 代理池：未启动态', () => {
  it('整机重启后的静息态：所有映射写「已停止」，启动按钮可用、停止禁用', async () => {
    mockApis(
      supervisor([
        poolModule([
          mapping({ id: 'h-run', listen: '8788', state: 'stopped', rtt_ms: null }),
          mapping({ id: 'ai-run-1', listen: '8801', owner: 'ai', state: 'stopped' }),
        ]),
      ]),
    )
    renderSettings()

    const card = await screen.findByTestId('proxy-pool-card')
    expect(card.getAttribute('data-pool-state')).toBe('stopped')
    // 人工行始终可见；AI 行默认折叠在摘要里
    expect(within(card).getAllByText('已停止').length).toBe(1)
    expect(within(card).getByText('1 条映射 · 0 条运行中')).toBeTruthy()

    const start = within(card).getByTestId('proxy-start') as HTMLButtonElement
    const stop = within(card).getByTestId('proxy-stop') as HTMLButtonElement
    expect(start.disabled).toBe(false)
    expect(stop.disabled).toBe(true)
  })

  it('proxypool 模块缺失时如实占位，不留在加载中', async () => {
    mockApis(supervisor([]))
    renderSettings()

    const card = await screen.findByTestId('proxy-pool-card')
    expect(within(card).getByTestId('proxy-empty').textContent).toContain('supervisor 未返回 proxypool 模块')
  })
})
