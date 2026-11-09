// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AiModulesPayload } from '../src/api'
import { AiModulesConfig } from '../src/components/AiModulesConfig'

/**
 * QUOT-504 按模块配置区。
 *
 * fixture 形状取自 QUOT-503 后端的真实响应（curl 取证 2026-11-09）：
 * 模块/角色清单、模型全集、三档来源全部 payload 驱动，换一份 payload 断言跟着变。
 */

function roleStatus(value: string, source: 'role' | 'module' | 'global', configured: boolean, inherited: string | null) {
  return { value, source, configured, inherited_value: inherited }
}

const PAYLOAD_A: AiModulesPayload = {
  modules: {
    tickhub: {
      ...roleStatus('openai:qwen-turbo', 'global', false, null),
      fallback_chain: [],
      agent_id: 'ladderstock-tickhub',
      kb_scope: 'all',
      roles: {},
    },
    wire: {
      ...roleStatus('openai:qwen-turbo', 'module', true, 'openai:qwen-turbo'),
      fallback_chain: ['openai:qwen-plus'],
      agent_id: 'ladderstock-wire',
      kb_scope: 'wire',
      roles: {},
    },
    senate: {
      ...roleStatus('openai:deepseek-flash', 'module', true, 'openai:qwen-turbo'),
      fallback_chain: [],
      agent_id: 'ladderstock-senate',
      kb_scope: 'all',
      roles: {
        bull: roleStatus('openai:qwen-plus', 'role', true, 'openai:deepseek-flash'),
        bear: roleStatus('openai:deepseek-flash', 'module', false, null),
        risk_neutral: roleStatus('openai:qwen-turbo', 'global', false, null),
      },
    },
  },
  models: ['openai:qwen-turbo', 'openai:qwen-plus', 'openai:deepseek-flash'],
  sources: ['role', 'module', 'global'],
  config_path: 'D:\\config\\ai.yaml',
}

/** 第二份 payload：模块更少、角色键不同、模型全集不同——证明没有任何硬编码清单。 */
const PAYLOAD_B: AiModulesPayload = {
  modules: {
    tickhub_us: {
      ...roleStatus('backend-x:chat', 'global', false, null),
      fallback_chain: [],
      agent_id: 'ladderstock-tickhub_us',
      kb_scope: 'all',
      roles: {},
    },
    senate: {
      ...roleStatus('backend-x:chat', 'global', false, null),
      fallback_chain: [],
      agent_id: 'ladderstock-senate',
      kb_scope: 'all',
      roles: {
        trader: roleStatus('backend-x:reason', 'role', true, 'backend-x:chat'),
      },
    },
  },
  models: ['backend-x:chat', 'backend-x:reason'],
  sources: ['role', 'module', 'global'],
  config_path: '/tmp/ai.yaml',
}

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

interface FetchRoute {
  get?: AiModulesPayload
  put?: (body: Record<string, unknown>) => { status: number; body: unknown }
  test?: (body: Record<string, unknown>) => unknown
}

function mockFetch(route: FetchRoute) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url)
    const method = init?.method ?? 'GET'
    if (path === '/api/control/ai/modules' && method === 'GET') {
      return jsonOk(route.get ?? PAYLOAD_A)
    }
    if (path === '/api/control/ai/modules' && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      const outcome = route.put?.(body) ?? { status: 200, body: { ...PAYLOAD_A, updated: body.module, changed: true } }
      return jsonOk(outcome.body, outcome.status)
    }
    if (path === '/api/control/ai/modules/test' && method === 'POST') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      return jsonOk(
        route.test?.(body) ?? {
          ok: true,
          module: body.module,
          role: body.role ?? null,
          model: 'openai:qwen-turbo',
          source: 'module',
          latency_ms: 123,
          reply_head: 'ok',
          error: null,
        },
      )
    }
    throw new Error(`unexpected fetch: ${method} ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function expand(payload: AiModulesPayload = PAYLOAD_A) {
  const fetchMock = mockFetch({ get: payload })
  render(<AiModulesConfig />)
  fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
  await screen.findByRole('group', { name: `模块 ${Object.keys(payload.modules)[0]}` })
  return fetchMock
}

function putBodies(fetchMock: ReturnType<typeof mockFetch>): Record<string, unknown>[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => String(url) === '/api/control/ai/modules' && init?.method === 'PUT')
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('按模块配置 · payload 驱动', () => {
  it('默认收起不发请求；展开后 GET 一次，模块行/角色行数量与键名全由 payload 决定', async () => {
    const fetchMock = mockFetch({})
    render(<AiModulesConfig />)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByRole('group', { name: /模块 / })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    await screen.findByRole('group', { name: '模块 tickhub' })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/control/ai/modules')
    expect(screen.getAllByRole('group', { name: /^模块 / })).toHaveLength(3)
    expect(screen.getByRole('group', { name: '模块 wire' })).toBeTruthy()
    expect(screen.getByRole('group', { name: '模块 senate' })).toBeTruthy()
    expect(screen.getAllByRole('group', { name: /^角色 senate\// })).toHaveLength(3)
    expect(screen.getByRole('group', { name: '角色 senate/risk_neutral' })).toBeTruthy()
  })

  it('换一份 payload：模块/角色/模型下拉跟着变，证明清单非硬编码', async () => {
    await expand(PAYLOAD_B)
    expect(screen.getAllByRole('group', { name: /^模块 / })).toHaveLength(2)
    expect(screen.queryByRole('group', { name: '模块 tickhub' })).toBeNull()
    expect(screen.getByRole('group', { name: '模块 tickhub_us' })).toBeTruthy()
    expect(screen.getAllByRole('group', { name: /^角色 senate\// })).toHaveLength(1)
    expect(screen.getByRole('group', { name: '角色 senate/trader' })).toBeTruthy()

    const select = within(screen.getByRole('group', { name: '模块 tickhub_us' })).getByLabelText('模型')
    const options = within(select as HTMLElement).getAllByRole('option')
    expect(options.map((item) => item.textContent)).toEqual(['继承上一级', 'backend-x:chat', 'backend-x:reason'])
  })

  it('模型下拉选项 == payload.models 全集，不含 payload 之外的模型', async () => {
    await expand()
    const row = screen.getByRole('group', { name: '模块 tickhub' })
    const select = within(row).getByLabelText('模型')
    const options = within(select as HTMLElement).getAllByRole('option')
    expect(options.map((item) => item.textContent)).toEqual(['继承上一级', ...PAYLOAD_A.models])
    expect(options.map((item) => item.getAttribute('value') ?? '')).toEqual(['', ...PAYLOAD_A.models])
  })
})

describe('按模块配置 · 来源徽标三档', () => {
  it('global 灰字继承全局；module 本模块生效；role 本角色生效', async () => {
    await expand()
    expect(
      within(screen.getByRole('group', { name: '模块 tickhub' })).getByText('继承全局：openai:qwen-turbo'),
    ).toBeTruthy()
    expect(
      within(screen.getByRole('group', { name: '模块 wire' })).getByText('本模块生效：openai:qwen-turbo'),
    ).toBeTruthy()
    expect(
      within(screen.getByRole('group', { name: '角色 senate/bull' })).getByText('本角色生效：openai:qwen-plus'),
    ).toBeTruthy()
    // 角色继承模块档时徽标说「本模块生效」，继承全局时说「继承全局」
    expect(
      within(screen.getByRole('group', { name: '角色 senate/bear' })).getByText('本模块生效：openai:deepseek-flash'),
    ).toBeTruthy()
  })
})

describe('按模块配置 · 保存', () => {
  it('只提交变更键；保存后用 PUT 回读刷新徽标，不是只改本地 state', async () => {
    const saved: AiModulesPayload = {
      ...PAYLOAD_A,
      modules: {
        ...PAYLOAD_A.modules,
        tickhub: { ...PAYLOAD_A.modules.tickhub, ...roleStatus('openai:qwen-plus', 'module', true, 'openai:qwen-turbo') },
      },
    }
    const fetchMock = mockFetch({ put: () => ({ status: 200, body: { ...saved, updated: 'tickhub', changed: true } }) })
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const row = await screen.findByRole('group', { name: '模块 tickhub' })

    // 未改动时保存不可用
    expect(within(row).getByRole('button', { name: '保存' })).toHaveProperty('disabled', true)

    fireEvent.change(within(row).getByLabelText('模型'), { target: { value: 'openai:qwen-plus' } })
    fireEvent.click(within(row).getByRole('button', { name: '保存' }))
    await screen.findByText('已保存并回读：tickhub（来源 module）')

    const bodies = putBodies(fetchMock)
    expect(bodies).toEqual([{ module: 'tickhub', model: 'openai:qwen-plus' }])
    // 回读前是「继承全局」，回读后必须刷新成「本模块生效」
    expect(within(screen.getByRole('group', { name: '模块 tickhub' })).getByText('本模块生效：openai:qwen-plus')).toBeTruthy()
  })

  it('选「继承上一级」= 提交空串清除覆盖，不是关闭', async () => {
    const fetchMock = mockFetch({})
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const row = await screen.findByRole('group', { name: '模块 wire' })
    fireEvent.change(within(row).getByLabelText('模型'), { target: { value: '' } })
    fireEvent.click(within(row).getByRole('button', { name: '保存' }))
    await screen.findByText(/已保存并回读：wire/)
    expect(putBodies(fetchMock)).toEqual([{ module: 'wire', model: '' }])
  })

  it('只改记忆 agent_id 时只提交 memory.agent_id，其余键不出现', async () => {
    const fetchMock = mockFetch({})
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const row = await screen.findByRole('group', { name: '模块 tickhub' })
    fireEvent.change(within(row).getByLabelText('记忆 agent_id'), { target: { value: 'ladderstock-tickhub-custom' } })
    fireEvent.click(within(row).getByRole('button', { name: '保存' }))
    await screen.findByText(/已保存并回读：tickhub/)
    expect(putBodies(fetchMock)).toEqual([
      { module: 'tickhub', memory: { agent_id: 'ladderstock-tickhub-custom' } },
    ])
  })

  it('角色行保存只提交该角色的 model', async () => {
    const fetchMock = mockFetch({})
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const row = await screen.findByRole('group', { name: '角色 senate/bear' })
    fireEvent.change(within(row).getByLabelText('模型'), { target: { value: 'openai:deepseek-flash' } })
    fireEvent.click(within(row).getByRole('button', { name: '保存' }))
    await screen.findByText(/已保存并回读：senate · bear/)
    expect(putBodies(fetchMock)).toEqual([
      { module: 'senate', roles: { bear: { model: 'openai:deepseek-flash' } } },
    ])
  })

  it('400 键级错误显示在对应行内，不整屏红、不影响其他行', async () => {
    mockFetch({ put: () => ({ status: 400, body: { detail: "ai.yaml: modules.wire.model = 'ghost:x' 使用了未声明的后端" } }) })
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const wire = await screen.findByRole('group', { name: '模块 wire' })
    fireEvent.change(within(wire).getByLabelText('模型'), { target: { value: 'openai:qwen-plus' } })
    fireEvent.click(within(wire).getByRole('button', { name: '保存' }))

    const alert = await within(wire).findByRole('alert')
    expect(alert.textContent).toContain('未声明的后端')
    expect(screen.queryByRole('alert')).toBe(alert)
    expect(within(screen.getByRole('group', { name: '模块 tickhub' })).queryByRole('alert')).toBeNull()
  })
})

describe('按模块配置 · 测试按钮', () => {
  it('成功就地显示 model/source/latency/reply_head；请求带 module 与 role', async () => {
    const fetchMock = mockFetch({})
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const row = await screen.findByRole('group', { name: '角色 senate/bull' })
    fireEvent.click(within(row).getByRole('button', { name: '测试' }))

    const outcome = await within(row).findByText(/耗时/)
    expect(outcome.textContent).toContain('openai:qwen-turbo')
    expect(outcome.textContent).toContain('module')
    expect(outcome.textContent).toContain('123ms')
    expect(outcome.textContent).toContain('ok')
    const call = fetchMock.mock.calls.find(([url]) => String(url) === '/api/control/ai/modules/test')
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ module: 'senate', role: 'bull' })
  })

  it('探针失败（200 ok:false）显示失败原因；HTTP 400 也落在行内', async () => {
    mockFetch({ test: () => ({ ok: false, module: 'tickhub', role: null, model: 'openai:qwen-turbo', source: 'global', latency_ms: 5, reply_head: null, error: 'HTTP 404: model_not_found' }) })
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const row = await screen.findByRole('group', { name: '模块 tickhub' })
    fireEvent.click(within(row).getByRole('button', { name: '测试' }))
    const alert = await within(row).findByRole('alert')
    expect(alert.textContent).toContain('model_not_found')
  })
})

describe('按模块配置 · 状态', () => {
  it('GET 失败给 Banner + 重试，重试成功后正常渲染', async () => {
    let calls = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      calls += 1
      return calls === 1 ? jsonOk({ detail: '读取 AI 配置失败' }, 500) : jsonOk(PAYLOAD_A)
    }))
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    const banner = await screen.findByRole('alert')
    expect(banner.textContent).toContain('读不到按模块配置')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByRole('group', { name: '模块 tickhub' })
  })

  it('空 modules 列表给空态文案', async () => {
    mockFetch({ get: { ...PAYLOAD_A, modules: {} } })
    render(<AiModulesConfig />)
    fireEvent.click(screen.getByRole('button', { name: /按模块配置/ }))
    await screen.findByText('后端未声明任何子模块。')
  })
})