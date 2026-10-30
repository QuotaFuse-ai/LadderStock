import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/api'

interface Captured {
  url: string
  method: string
  headers: Record<string, string>
}

function captureFetch(): Captured[] {
  const calls: Captured[] = []
  vi.stubGlobal('fetch', (input: string, init: RequestInit = {}) => {
    calls.push({
      url: input,
      method: init.method ?? 'GET',
      headers: (init.headers ?? {}) as unknown as Record<string, string>,
    })
    return Promise.resolve(
      new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }),
    )
  })
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('控制面写请求头（QUOT-178）', () => {
  it('控制面写请求统一带 X-LDST-Client: 1', async () => {
    const calls = captureFetch()
    await api.control.start('wire')
    await api.control.stop('wire')
    await api.control.install('wire')
    await api.control.saveConfig('wire', { MCP_HTTP_PORT: '8791' })
    expect(calls).toHaveLength(4)
    for (const call of calls) {
      expect(call.headers['X-LDST-Client']).toBe('1')
    }
  })

  it('控制面读请求与数据面无副作用请求不带该头', async () => {
    const calls = captureFetch()
    await api.control.board()
    await api.control.config('wire')
    await api.health()
    expect(calls).toHaveLength(3)
    for (const call of calls) {
      expect(call.method).toBe('GET')
      expect(call.headers['X-LDST-Client']).toBeUndefined()
    }
  })
})
