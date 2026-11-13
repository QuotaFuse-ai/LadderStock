// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPane } from '../src/screens/home/shell/ChatPane'

/**
 * QUOT-584：首页对话输入区一体式 composer。
 *
 * 发送 / 停止控件必须位于 composer 容器（`data-testid="composer"` 的 form）**内部**，
 * 容器外不再存在独立的发送按钮。回车发送、maxLength、aria-label 等行为契约不变。
 */

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

const posted: Array<{ url: string; body: unknown }> = []

function stubRoutes() {
  posted.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      if (method === 'POST') posted.push({ url, body })
      return jsonOk({
        session: { session_id: 's-1', title: '', created_at: '', updated_at: '' },
        turns: [],
      })
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
  stubRoutes()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('一体式 composer（QUOT-584）', () => {
  it('发送控件在 composer 容器内，容器外没有独立发送按钮', () => {
    renderPane()
    const composer = screen.getByTestId('composer')
    expect(composer.tagName).toBe('FORM')

    const send = screen.getByRole('button', { name: '发送' })
    expect(composer.contains(send)).toBe(true)
    // 空草稿不能发送
    expect(send.hasAttribute('disabled')).toBe(true)

    // 容器内只有发送这一个控件，且容器之外不再有同名按钮
    expect(screen.getAllByRole('button', { name: '发送' }).length).toBe(1)
    const outsideButtons = Array.from(screen.getAllByRole('button')).filter(
      (button) => !composer.contains(button),
    )
    expect(outsideButtons.some((button) => button.textContent?.includes('发送'))).toBe(false)
  })

  it('输入后发送图标可提交，走既有同步契约（POST /api/conversation）', async () => {
    renderPane()
    const input = screen.getByLabelText('向对话提问') as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: '浦发银行怎么看' } })
    expect(input.maxLength).toBe(2000)

    const send = screen.getByRole('button', { name: '发送' })
    expect(send.hasAttribute('disabled')).toBe(false)
    fireEvent.click(send)

    await vi.waitFor(() => {
      expect(posted.some((call) => call.url === '/api/conversation/turns')).toBe(true)
    })
    // QUOT-587：发送后从冷启动空态切到消息层，composer 重挂载——重新查询后断言输入被清空
    const clearedInput = screen.getByLabelText('向对话提问') as HTMLTextAreaElement
    expect(clearedInput.value).toBe('')
  })

  it('busy 时停止控件在同一位置（composer 容器内）', () => {
    renderPane()
    const composer = screen.getByTestId('composer')
    fireEvent.change(screen.getByLabelText('向对话提问'), { target: { value: '在途的一问' } })
    fireEvent.submit(composer)

    const stop = screen.getByRole('button', { name: '停止' })
    // QUOT-587：首问让空态切到消息层，composer 重挂载——用当前实例重新查询
    const activeComposer = screen.getByTestId('composer')
    expect(activeComposer.contains(stop)).toBe(true)
    expect(screen.queryByRole('button', { name: '发送' })).toBeNull()
  })
})
