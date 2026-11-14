// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPane } from '../src/screens/home/shell/ChatPane'

/**
 * QUOT-582 / F3：会话改名。
 *
 * 改名只走 `PATCH /api/conversation/sessions/{id}`，请求体就是用户输入；**截断与空白折叠
 * 全在服务端（40 码位）**，页面显示服务端返回的标题（单一口径，前端不再截一遍）。
 * 改名入口只在「有会话且有标题」时出现；取消 / 失败都不改标题。
 */

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

interface RouteCall {
  url: string
  method: string
  body: unknown
}

const calls: RouteCall[] = []

function stubRoutes(patch: (sessionId: string, title: string) => unknown) {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      calls.push({ url, method, body })
      if (method === 'PATCH') {
        const match = /\/api\/conversation\/sessions\/([^/]+)$/.exec(url)
        return jsonOk(patch(decodeURIComponent(match?.[1] ?? ''), String(body?.title ?? '')))
      }
      return jsonOk({
        session: {
          session_id: 's-1',
          title: '浦发银行怎么看',
          created_at: '2026-11-06T09:00:00',
          updated_at: '2026-11-06T09:30:00',
        },
        turns: [],
      })
    }),
  )
}

function renderPane(activeSessionId: string | null) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <ChatPane
        activeSessionId={activeSessionId}
        onOpenResearch={() => {}}
        onAskDesk={() => {}}
        onRoundComplete={() => {}}
      />
    </MemoryRouter>,
  )
}

async function openRename() {
  const trigger = await screen.findByRole('button', { name: '重命名会话' })
  fireEvent.click(trigger)
  return await screen.findByLabelText('会话标题')
}

beforeEach(() => {
  window.localStorage.clear()
  stubRoutes((sessionId, title) => ({ session_id: sessionId, title }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('会话改名（F3）', () => {
  it('改名入口预填当前标题，保存后 PATCH 请求体就是输入并显示服务端返回值', async () => {
    // 服务端会折叠空白并截到 40 码位：这里返回一个与输入不同的标题，验证页面用返回值
    stubRoutes((sessionId, title) => ({
      session_id: sessionId,
      title: title.replace(/\s+/g, ' ').trim().slice(0, 40),
    }))
    renderPane('s-1')

    const input = (await openRename()) as HTMLInputElement
    expect(input.value).toBe('浦发银行怎么看')

    fireEvent.change(input, { target: { value: '  浦发银行   分红与净息差  ' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      const patch = calls.find((call) => call.method === 'PATCH')
      expect(patch).toBeTruthy()
      expect(patch?.url).toBe('/api/conversation/sessions/s-1')
      expect(patch?.body).toEqual({ title: '浦发银行   分红与净息差' })
    })

    // 输入框收起，标题行显示服务端折叠后的结果
    await screen.findByRole('heading', { name: '浦发银行 分红与净息差' })
    expect(screen.queryByLabelText('会话标题')).toBeNull()
  })

  it('取消改名不发请求，标题保持原样', async () => {
    renderPane('s-1')
    const input = (await openRename()) as HTMLInputElement
    fireEvent.change(input, { target: { value: '改到一半不要了' } })
    fireEvent.click(screen.getByRole('button', { name: '取消改名' }))

    expect(screen.queryByLabelText('会话标题')).toBeNull()
    expect(screen.getByRole('heading', { name: '浦发银行怎么看' })).toBeTruthy()
    expect(calls.some((call) => call.method === 'PATCH')).toBe(false)
  })

  it('空白标题不能保存', async () => {
    renderPane('s-1')
    const input = await openRename()
    fireEvent.change(input, { target: { value: '   ' } })

    const save = screen.getByRole('button', { name: '保存' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.submit(save.closest('form') as HTMLFormElement)
    expect(calls.some((call) => call.method === 'PATCH')).toBe(false)
  })

  it('保存失败时留在编辑态并给出提示', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = init?.method ?? 'GET'
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
        if (method === 'PATCH') {
          return jsonOk(
            { ok: false, session_id: 's-1', code: 'not_found', detail: '会话不存在', retryable: false },
            404,
          )
        }
        return jsonOk({
          session: { session_id: 's-1', title: '浦发银行怎么看', created_at: '', updated_at: '' },
          turns: [],
        })
      }),
    )
    renderPane('s-1')
    const input = await openRename()
    fireEvent.change(input, { target: { value: '新名字' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    const alert = (await screen.findByRole('alert')) as HTMLElement
    expect(alert.textContent).toContain('会话不存在')
    expect((screen.getByLabelText('会话标题') as HTMLInputElement).value).toBe('新名字')
  })

  it('没有会话时不出改名入口', async () => {
    renderPane(null)
    // QUOT-587：冷启动渲染两段式空态（居中输入 + 欢迎语），不再有「新对话」标题条
    await screen.findByText(/问一句就行/)
    expect(screen.getByTestId('composer')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '重命名会话' })).toBeNull()
  })
})
