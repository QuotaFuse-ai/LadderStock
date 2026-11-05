// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, type BoardPayload, type ComponentSnapshot, type PeerSnapshot, type RegistryEntry } from '../src/api'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * QUOT-420：设置页模块数只有一个口径——注册表实际出卡数。
 *
 * 曾经标题按 peers+components 算、侧栏写死「六个模块」，同一屏两个数字。
 * 这组用例锁死：标题、侧栏说明、卡片数三者同源；导入的 peer 未安装就写「未安装」。
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
    ports: { mcp: 8000, web: null, web_label: '管理端' },
    listening: { mcp: [], web: [] },
    health: { ok: true, detail: 'HTTP 200' },
    dist: { wheel: null, editable: false, env_file: '' },
    port_conflicts: [],
    last_job: null,
    open_url: null,
    actions: { can_install: false, can_start: false, can_stop: true, can_open: false },
  }
}

function component(id: string, product: string): ComponentSnapshot {
  return {
    id,
    kind: 'component',
    product,
    role: `${product} 职责`,
    directory: `D:/workplace/${id}`,
    directory_exists: true,
    status: 'running',
    status_label: '运行中',
    detail: 'HTTP 200',
    ports: { console: 8788, gateway: 8789 },
    listening: { console: [], gateway: [] },
    health: { ok: true, detail: 'HTTP 200' },
    open_url: null,
    managed_by: 'Windows 计划任务',
    notes: [],
    actions: { can_open: false },
  }
}

function registryRow(id: string, installed: boolean): RegistryEntry {
  return {
    id,
    product: id,
    package: id,
    default_url: `http://127.0.0.1:8000/mcp`,
    health_path: '/healthz',
    default_port: 8000,
    role: `${id} 职责`,
    repo: `QuotaFuse-ai/${id}`,
    installed,
    status: installed ? 'running' : 'not_installed',
    status_label: installed ? '运行中' : '未安装',
  }
}

function board(overrides: Partial<BoardPayload> = {}): BoardPayload {
  return {
    ok: true,
    control_enabled: true,
    peers: [peer('alpha', 'Alpha'), peer('beta', 'Beta')],
    registry: [registryRow('alpha', true), registryRow('beta', true)],
    components: [],
    port_conflicts: [],
    ports: { board: 5950, mcp: 5951 },
    ...overrides,
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

describe('设置页模块数口径（QUOT-420）', () => {
  it('标题、侧栏说明、卡片数三处一致，且不再出现写死的「六个模块」', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(board())
    renderSettings()

    expect((await screen.findByText('2 个模块 · 运行中 2')).textContent).toContain('2 个模块')
    expect(screen.getByText(/peers: 块，当前 2 个/).textContent).toContain('当前 2 个')
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(2)
    expect(screen.queryByText(/六个模块/)).toBeNull()
  })

  it('注册表条目数与标题同源：导入一条未安装 peer 后三处一起变成 3', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board({
        peers: [
          peer('alpha', 'Alpha'),
          peer('beta', 'Beta'),
          {
            ...peer('gamma', 'Gamma'),
            status: 'not_installed',
            status_label: '未安装',
            installed: false,
          },
        ],
        registry: [registryRow('alpha', true), registryRow('beta', true), registryRow('gamma', false)],
      }),
    )
    renderSettings()

    expect((await screen.findByText('3 个模块 · 运行中 2')).textContent).toContain('3 个模块')
    expect(screen.getByText(/peers: 块，当前 3 个/).textContent).toContain('当前 3 个')
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(3)
    await waitFor(() => expect(screen.getAllByText('未安装').length).toBeGreaterThan(0))
  })

  it('components 为空时给出「当前注册表未声明工具组件」，不留白', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(board())
    renderSettings()

    expect(await screen.findByText('当前注册表未声明工具组件')).toBeTruthy()
  })

  it('工具组件计入模块数：2 peer + 1 component = 3 张卡', async () => {
    vi.spyOn(api.control, 'board').mockResolvedValue(
      board({ components: [component('proxyfleet', 'ProxyFleet')] }),
    )
    renderSettings()

    expect((await screen.findByText('3 个模块 · 运行中 3')).textContent).toContain('3 个模块')
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(3)
  })
})
