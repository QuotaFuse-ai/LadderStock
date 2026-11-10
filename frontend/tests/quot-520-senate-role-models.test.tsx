// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  api,
  type BoardPayload,
  type PeerSnapshot,
  type SenateRoleModelsPayload,
  type SenateTestResult,
  type SupervisorStatePayload,
} from '../src/api'
import { SenateRoleModels, senateErrorText } from '../src/components/SenateRoleModels'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * QUOT-520：StockSenate 辩论角色多模型面板。
 *
 * 锁死五件事：
 * 1. 16 个角色、provider 选项、模型候选全部来自 GET 响应（换一份 payload 跟着变）；
 * 2. 来源徽标三态：角色 TOML / 全局继承 / 未配置；
 * 3. 保存只提交改动角色，并带 GET 拿到的 expected_revision；
 * 4. SENATE_STALE 保留草稿并给出刷新入口；
 * 5. 测试结果按 ok / skipped / error_code 分色，错误码映射成中文。
 */

const ROLES = [
  'market',
  'social',
  'news',
  'fundamentals',
  'policy',
  'hot_money',
  'lockup',
  'bull',
  'bear',
  'research_manager',
  'trader',
  'risk_aggressive',
  'risk_neutral',
  'risk_conservative',
  'quality_gate',
  'portfolio_manager',
] as const

const CONFIGURED: Record<string, { provider: string; model: string }> = {
  market: { provider: 'minimax', model: 'MiniMax-M2' },
  social: { provider: 'deepseek', model: 'deepseek-chat' },
}

function roleStatus(
  role: string,
  value: { provider: string; model: string } | null,
): SenateRoleModelsPayload['roles'][number] {
  return {
    role,
    label: role,
    configured: value !== null,
    provider: value?.provider ?? null,
    model: value?.model ?? null,
    api_key_env: value ? 'MINIMAX_API_KEY' : null,
  }
}

function payload(overrides: Partial<SenateRoleModelsPayload> = {}): SenateRoleModelsPayload {
  return {
    engine: {
      authority: 'env',
      reload: 'webui_per_job',
      provider: 'minimax',
      file_provider: 'minimax',
      env_override: false,
      config_file: 'D:\\workplace\\StockSenate\\config\\llm_config.toml',
      contract_source: 'package',
      hint: 'StockSenate 的 llm_provider 只从 llm_config.toml 读；本页写的是 TOML。',
      reload_hint: '写入 llm_config.toml 在下次进程启动时生效；运行中的 Senate 需重启。',
    },
    roles: ROLES.map((role) => roleStatus(role, CONFIGURED[role] ?? null)),
    unconfigured_roles: ROLES.filter((role) => !(role in CONFIGURED)),
    role_count: ROLES.length,
    global: {
      provider: 'minimax',
      deep_think_llm: 'MiniMax-M2',
      quick_think_llm: 'MiniMax-M2',
      backend_url_set: false,
      api_key_env: 'MINIMAX_API_KEY',
    },
    providers: [
      { name: 'minimax', label: 'minimax', api_key_env: 'MINIMAX_API_KEY', base_url: null, models: ['MiniMax-M2', 'MiniMax-Text-01'] },
      { name: 'deepseek', label: 'deepseek', api_key_env: 'DEEPSEEK_API_KEY', base_url: null, models: ['deepseek-chat', 'deepseek-reasoner'] },
      { name: 'openai', label: 'openai', api_key_env: 'OPENAI_API_KEY', base_url: null, models: ['gpt-5', 'gpt-5-mini'] },
      { name: 'dashscope', label: 'dashscope', api_key_env: 'DASHSCOPE_API_KEY', base_url: null, models: ['qwen-max'] },
      { name: 'qwen', label: 'qwen', api_key_env: 'QWEN_API_KEY', base_url: null, models: ['qwen-plus'] },
      { name: 'moonshot', label: 'moonshot', api_key_env: 'MOONSHOT_API_KEY', base_url: null, models: ['kimi-k2'] },
      { name: 'zhipu', label: 'zhipu', api_key_env: 'ZHIPU_API_KEY', base_url: null, models: ['glm-4.6'] },
      { name: 'offline', label: 'offline（本地确定性引擎，不发网络）', api_key_env: null, base_url: null, models: ['offline'] },
    ],
    aliases: { technical: 'market', basic: 'fundamentals', sentiment: 'social', unlock: 'lockup' },
    directory_exists: true,
    config_exists: true,
    env_file: 'D:\\workplace\\StockSenate\\.env',
    env_keys: ['MINIMAX_API_KEY'],
    revision: 'rev-1',
    ...overrides,
  }
}

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

interface SenateRoute {
  get?: SenateRoleModelsPayload
  put?: (body: Record<string, unknown>) => { status: number; body: unknown }
  test?: (body: Record<string, unknown>) => SenateTestResult
}

function mockFetch(route: SenateRoute) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    const method = init?.method ?? 'GET'
    if (path === '/api/control/peers/senate/role-models' && method === 'GET') {
      return jsonOk(route.get ?? payload())
    }
    if (path === '/api/control/peers/senate/role-models' && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      const outcome = route.put?.(body)
      if (!outcome) throw new Error('unexpected PUT')
      return jsonOk(outcome.body, outcome.status)
    }
    if (path === '/api/control/peers/senate/role-models/test' && method === 'POST') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      const outcome = route.test?.(body)
      if (!outcome) throw new Error('unexpected POST test')
      return jsonOk(outcome)
    }
    throw new Error(`unexpected fetch: ${method} ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function putCalls(fetchMock: ReturnType<typeof mockFetch>) {
  return fetchMock.mock.calls
    .filter(([url, init]) => String(url) === '/api/control/peers/senate/role-models' && init?.method === 'PUT')
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>)
}

function testCalls(fetchMock: ReturnType<typeof mockFetch>) {
  return fetchMock.mock.calls
    .filter(
      ([url, init]) =>
        String(url) === '/api/control/peers/senate/role-models/test' && init?.method === 'POST',
    )
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('SenateRoleModels', () => {
  it('renders all 16 roles with provider options and three source badges from the payload', async () => {
    mockFetch({})
    const { container } = render(<SenateRoleModels controlReady />)

    const market = await screen.findByTestId('senate-role-market')
    expect(within(market).getByText('角色 TOML')).toBeTruthy()
    const options = within(market)
      .getAllByRole('option')
      .map((option) => option.textContent)
    expect(options).toContain('minimax')
    expect(options).toContain('offline（本地确定性引擎，不发网络）')
    expect(options).not.toContain('azure')

    const news = screen.getByTestId('senate-role-news')
    expect(within(news).getByText('全局继承')).toBeTruthy()

    expect(screen.getAllByRole('group', { name: /^辩论角色 / })).toHaveLength(16)
    expect(container.textContent).toContain('llm_config.toml')
    expect(container.textContent).toContain('不是 ai.yaml 的按模块配置')
  })

  it('labels unconfigured roles as 未配置 when there is no global provider', async () => {
    mockFetch({
      get: payload({
        global: {
          provider: null,
          deep_think_llm: null,
          quick_think_llm: null,
          backend_url_set: false,
          api_key_env: null,
        },
        roles: ROLES.map((role) => roleStatus(role, null)),
        unconfigured_roles: [...ROLES],
      }),
    })
    render(<SenateRoleModels controlReady />)
    const market = await screen.findByTestId('senate-role-market')
    expect(within(market).getByText('未配置')).toBeTruthy()
  })

  it('saves only changed roles with expected_revision and refreshes from the PUT response', async () => {
    const fetchMock = mockFetch({
      put: (body) => {
        const roles = body.roles as Record<string, { provider: string; model: string }>
        return {
          status: 200,
          body: payload({
            roles: ROLES.map((role) =>
              roleStatus(role, roles[role] ?? CONFIGURED[role] ?? null),
            ),
            revision: 'rev-2',
            updated: Object.keys(roles),
          }),
        }
      },
    })
    render(<SenateRoleModels controlReady />)

    const market = await screen.findByTestId('senate-role-market')
    fireEvent.change(within(market).getByLabelText('Provider'), { target: { value: 'openai' } })
    fireEvent.change(within(market).getByLabelText('Model'), { target: { value: 'gpt-5-mini' } })
    fireEvent.click(screen.getByTestId('senate-save'))

    await screen.findByText(/已保存 1 个角色/)
    expect(putCalls(fetchMock)).toEqual([
      {
        roles: { market: { provider: 'openai', model: 'gpt-5-mini' } },
        expected_revision: 'rev-1',
      },
    ])
    expect(screen.getByText(/rev-2/)).toBeTruthy()
    expect(screen.getAllByText(/下次进程启动时生效/).length).toBeGreaterThan(0)
  })

  it('keeps the draft and offers a refresh on SENATE_STALE', async () => {
    mockFetch({
      put: () => ({
        status: 409,
        body: { detail: { code: 'SENATE_STALE', message: 'llm_config.toml 已被外部修改，请刷新后重试' } },
      }),
    })
    render(<SenateRoleModels controlReady />)

    const market = await screen.findByTestId('senate-role-market')
    const model = within(market).getByLabelText('Model') as HTMLInputElement
    fireEvent.change(model, { target: { value: 'MiniMax-Text-01' } })
    fireEvent.click(screen.getByTestId('senate-save'))

    await screen.findByText(/配置文件已被外部修改（SENATE_STALE）/)
    expect(model.value).toBe('MiniMax-Text-01')
    expect(screen.getAllByRole('button', { name: '刷新' }).length).toBeGreaterThan(0)
  })

  it('sends the row draft to the test endpoint and maps ok / skipped / error_code', async () => {
    const fetchMock = mockFetch({
      test: (body) => {
        const role = String(body.role)
        if (role === 'market') {
          return {
            ok: true,
            role,
            provider: 'minimax',
            model: 'MiniMax-M2',
            latency_ms: 42,
            error_code: null,
            message: 'ok',
          }
        }
        if (role === 'social') {
          return {
            ok: false,
            role,
            provider: 'deepseek',
            model: 'deepseek-chat',
            latency_ms: 0,
            error_code: 'SENATE_NO_API_KEY',
            message: '未找到 DEEPSEEK_API_KEY',
          }
        }
        return {
          ok: true,
          skipped: true,
          role,
          provider: 'offline',
          model: 'offline',
          latency_ms: 0,
          error_code: null,
          message: 'llm_provider = offline：已跳过连通性探测',
        }
      },
    })
    render(<SenateRoleModels controlReady />)

    const market = await screen.findByTestId('senate-role-market')
    const offline = screen.getByTestId('senate-role-news')

    fireEvent.click(within(market).getByTestId('senate-test-market'))
    await screen.findByText(/耗时/)
    expect(screen.getByText('42ms')).toBeTruthy()

    fireEvent.click(within(screen.getByTestId('senate-role-social')).getByTestId('senate-test-social'))
    await screen.findByText(/找不到 API key/)
    expect(screen.getByText('SENATE_NO_API_KEY')).toBeTruthy()

    fireEvent.click(within(offline).getByTestId('senate-test-news'))
    await screen.findByText(/已跳过/)

    expect(testCalls(fetchMock)).toEqual([
      { role: 'market', provider: 'minimax', model: 'MiniMax-M2' },
      { role: 'social', provider: 'deepseek', model: 'deepseek-chat' },
      { role: 'news' },
    ])
  })

  it('maps every SENATE_* code to Chinese copy and falls back to the server message', () => {
    expect(senateErrorText('SENATE_STALE', 'x')).toContain('刷新')
    expect(senateErrorText('SENATE_SECRET_REJECTED', 'x')).toContain('环境变量名')
    expect(senateErrorText('SENATE_UNKNOWN', '服务端原文')).toBe('服务端原文')
  })
})

// ---------------------------------------------------------------- 挂载位置

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
    detail: '端口已监听',
    installed: true,
    version: null,
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
    peers: [peer('tickhub', 'TickHub-CN'), peer('senate', 'StockSenate')],
    registry: [],
    components: [],
    port_conflicts: [],
    ports: { board: 5950, mcp: 5951 },
  }
}

function supervisor(): SupervisorStatePayload {
  return {
    ok: true,
    engine: { running: true, window_seconds: 300, max_restarts: 3, backoff: [5, 15, 45] },
    modules: [],
    configured: true,
  }
}

it('mounts the panel inside the StockSenate peer card on the peers tab', async () => {
  mockFetch({})
  vi.spyOn(api.control, 'board').mockResolvedValue(board())
  vi.spyOn(api.supervisor, 'state').mockResolvedValue(supervisor())

  render(
    <MemoryRouter initialEntries={['/settings?tab=peers']}>
      <SettingsScreen />
    </MemoryRouter>,
  )

  const card = await screen.findByTestId('peer-card-senate')
  await waitFor(() => expect(within(card).getByTestId('senate-role-models')).toBeTruthy())
  expect(within(card).getByText('辩论角色多模型')).toBeTruthy()
})
