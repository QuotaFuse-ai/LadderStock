// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPane } from '../src/screens/home/shell/ChatPane'

/**
 * F6：输入框底行的数据权限档选择 + 发问前确认弹窗。
 *
 * 这里只测前端的「要不要先问」：档位与说明来自 `GET /api/data/policy`（服务端
 * `core/policy.py` 是唯一口径），切换写回 `POST /api/data/policy`；确认弹窗出现时
 * 不发送对话请求，确认后才走 `POST /api/conversation/turns`（伪 SSE）。
 */

const DESCRIBE = {
  confirm_policy: 'ask_on_risk',
  tiers: { read: true, collect: false, admin: false },
  labels: { always_ask: '始终询问', ask_on_risk: '按需确认', allow_all: '全部允许' },
  descriptions: {
    always_ask: '每个操作都先问你，含只读查询',
    ask_on_risk: '只对采集、写库、改配置确认（推荐）',
    allow_all: '不打断；启停服务仍会询问',
  },
}

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

function sseOk(frames: Array<{ event: string; data: unknown }>) {
  const text = frames.map((frame) => `event: ${frame.event}\ndata: ${JSON.stringify(frame.data)}\n\n`).join('')
  const encoder = new TextEncoder()
  let sent = false
  return {
    ok: true,
    status: 200,
    json: async () => ({}),
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return { done: false, value: encoder.encode(text) }
        },
      }),
    },
  }
}

const policyWrites: Array<Record<string, unknown>> = []
const turnCalls: Array<{ url: string; body: unknown }> = []

function stubRoutes(initialPolicy: string) {
  policyWrites.length = 0
  turnCalls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const parsedBody = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined

      if (url.includes('/api/data/policy')) {
        if (method === 'POST') {
          policyWrites.push(parsedBody)
          return jsonOk({
            ok: true,
            confirm_policy: parsedBody?.confirm_policy ?? initialPolicy,
            scope: 'session',
            updated_at: '2026-11-21T18:00:00+08:00',
            describe: { ...DESCRIBE, confirm_policy: parsedBody?.confirm_policy ?? initialPolicy },
          })
        }
        return jsonOk({
          ok: true,
          confirm_policy: initialPolicy,
          scope: 'session',
          updated_at: null,
          describe: DESCRIBE,
        })
      }
      if (url.includes('/api/conversation/turns')) {
        turnCalls.push({ url, body: parsedBody })
        return sseOk([
          { event: 'status', data: { action: 'history', item: '读取会话历史', state: 'done' } },
          {
            event: 'done',
            data: {
              ok: true,
              session_id: 's-f6',
              reply: '按权限档执行。',
              blocks: [],
              turn_seq: 1,
              status: 'done',
            },
          },
        ])
      }
      if (url.includes('/api/schedule/jobs')) {
        return jsonOk({ ok: true, jobs: [], background: [], plans: { ok: true, items: [] } })
      }
      if (url.includes('/api/preferences')) {
        return jsonOk({ ok: true, items: [], preferences: [], total: 0, limit: 200 })
      }
      return jsonOk({})
    }),
  )
}

function renderPane() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <ChatPane
        activeSessionId={null}
        onOpenResearch={() => {}}
        onAskDesk={() => {}}
        onRoundComplete={() => {}}
      />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  window.localStorage.clear()
  stubRoutes('ask_on_risk')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('F6 · 输入框权限选择与确认弹窗', () => {
  it('输入框底行有权限档选择器，初始值来自 /api/data/policy', async () => {
    renderPane()
    const select = (await screen.findByLabelText('数据权限档')) as HTMLSelectElement
    expect(select.value).toBe('ask_on_risk')
    expect(screen.getByTestId('composer').contains(select)).toBe(true)
    // 三档都在（标签用服务端 describe 的文案）
    expect(screen.getByRole('option', { name: '始终询问' })).toBeTruthy()
    expect(screen.getByRole('option', { name: '按需确认' })).toBeTruthy()
    expect(screen.getByRole('option', { name: '全部允许' })).toBeTruthy()
  })

  it('按需确认档：普通只读问题直接发送，不弹确认', async () => {
    renderPane()
    fireEvent.change(await screen.findByLabelText('向对话提问'), { target: { value: '浦发银行怎么看' } })
    fireEvent.submit(screen.getByTestId('composer'))

    await waitFor(() => expect(turnCalls.length).toBe(1))
    expect(screen.queryByRole('dialog', { name: /确认本轮数据权限/ })).toBeNull()
  })

  it('按需确认档：问题带采集动作时先弹确认，取消则不发送', async () => {
    renderPane()
    fireEvent.change(await screen.findByLabelText('向对话提问'), { target: { value: '把 600519 的财报补采进来' } })
    fireEvent.submit(screen.getByTestId('composer'))

    const dialog = await screen.findByRole('dialog', { name: /确认本轮数据权限/ })
    expect(dialog.textContent).toContain('按需确认')
    expect(dialog.textContent).toContain('补采')

    fireEvent.click(screen.getByRole('button', { name: '再想想' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /确认本轮数据权限/ })).toBeNull())
    expect(turnCalls.length).toBe(0)
  })

  it('切到始终询问：切换写回策略，普通问题也要确认，确认后才发送', async () => {
    renderPane()
    const select = (await screen.findByLabelText('数据权限档')) as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'always_ask' } })
    await waitFor(() => expect(policyWrites.at(-1)).toEqual({ confirm_policy: 'always_ask', scope: 'session' }))

    fireEvent.change(screen.getByLabelText('向对话提问'), { target: { value: '浦发银行怎么看' } })
    fireEvent.submit(screen.getByTestId('composer'))

    await screen.findByRole('dialog', { name: /确认本轮数据权限/ })
    expect(turnCalls.length).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: '继续发送' }))
    await waitFor(() => expect(turnCalls.length).toBe(1))
    expect((turnCalls[0].body as { message: string }).message).toBe('浦发银行怎么看')
  })
})
