// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// jsdom 没有 canvas / matchMedia，研究台图表组件挂载会炸，桩掉 uPlot 即可
vi.mock('uplot', () => ({
  default: class {
    constructor(_opts: unknown, _data: unknown, el: HTMLElement) { void el }
    destroy() {}
    setSize() {}
    setData() {}
  },
}))

import App from '../src/App'
import { ChatPane } from '../src/screens/home/shell/ChatPane'
import { HOME_MODE_STORAGE_KEY } from '../src/lib/homeMode'

/**
 * QUOT-588：586/587 合并后两处回归的回归测。
 *
 * ① 待办异动顶栏入口：chat 模式顶栏只一颗「待办异动」、无「刷新全部」；desk 模式两颗都在。
 *    入口由 `HomeShell` 在 shell 层显式全局挂载，不再依赖 hidden-desk 的 portal 泄漏。
 * ② 对话首问：失败 / 停止停在消息层（不闪回空白欢迎层）；成功后 `onSessionCreated`
 *    把新 `session_id` 交回外壳，第二问续同一会话。
 */

function jsonOk(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

const posted: Array<{ url: string; body: { session_id?: string } }> = []

beforeEach(() => {
  window.localStorage.clear()
  posted.length = 0
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('QUOT-588 回归 ①：待办异动顶栏入口全局唯一、刷新 desk-only', () => {
  it('chat 模式顶栏只有一颗「待办异动」，无「刷新全部」', async () => {
    window.localStorage.setItem(HOME_MODE_STORAGE_KEY, 'chat')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('offline'))),
    )
    render(<MemoryRouter><App /></MemoryRouter>)
    await screen.findByTestId('composer')
    // 等两模式都常驻挂载（HomeScreen 数据取数失败也无所谓，入口仍渲染）
    await vi.waitFor(() => expect(screen.getAllByText(/待办 \d+ · 异动 \d+/).length).toBe(1))
    expect(screen.queryByRole('button', { name: /刷新全部/ })).toBeNull()
    // 入口在顶栏槽（header.workspace-topbar）里，不在 hidden 容器里
    const topbar = document.querySelector('header.workspace-topbar')
    expect(topbar?.textContent).toContain('待办')
  })

  it('desk 模式顶栏「待办异动」与「刷新全部」都在，且各只一颗', async () => {
    window.localStorage.setItem(HOME_MODE_STORAGE_KEY, 'desk')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('offline'))),
    )
    render(<MemoryRouter><App /></MemoryRouter>)
    await vi.waitFor(() => expect(screen.getAllByText(/待办 \d+ · 异动 \d+/).length).toBe(1))
    expect(screen.getAllByRole('button', { name: /刷新全部/ }).length).toBe(1)
    expect(screen.getAllByText(/待办 \d+ · 异动 \d+/).length).toBe(1)
  })
})

describe('QUOT-588 回归 ②：首问失败停在消息层 / 成功后会话续上', () => {
  function renderPane(props: Partial<Parameters<typeof ChatPane>[0]> = {}) {
    return render(
      <MemoryRouter initialEntries={['/']}>
        <ChatPane
          activeSessionId={null}
          onOpenResearch={() => {}}
          onAskDesk={() => {}}
          onRoundComplete={() => {}}
          {...props}
        />
      </MemoryRouter>,
    )
  }

  it('首问失败：停在消息层显示失败 + 重试，不闪回空白欢迎层', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonOk({ detail: 'AI 通道不可用', code: 'E_CHANNEL' }, 502)),
    )
    renderPane()
    fireEvent.change(screen.getByLabelText('向对话提问'), { target: { value: '浦发银行怎么看' } })
    fireEvent.submit(screen.getByTestId('composer'))

    await vi.waitFor(() => expect(screen.getByText('这一轮没有答出来')).toBeTruthy())
    expect(screen.getByRole('button', { name: '重试这一问' })).toBeTruthy()
    // 空态两段式的欢迎文案不回来（没有闪回空白欢迎层）
    expect(screen.queryByText(/问一句就行/)).toBeNull()
    expect(screen.queryByText('暂无对话')).toBeNull()
  })

  it('首问成功：onSessionCreated 收到新 session_id，第二问带同一 id（会话续上）', async () => {
    let replySessionId = 's-new'
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        if (method === 'POST') {
          posted.push({ url: String(input), body: JSON.parse(String(init?.body ?? '{}')) })
          return jsonOk({
            ok: true,
            session_id: replySessionId,
            reply: '结论：持观望态度。',
            blocks: [],
            model: 'demo',
            channel: 'demo',
            sources: [],
            turn_seq: 1,
            status: 'done',
            fetched_at: '',
          })
        }
        return jsonOk({
          session: { session_id: 's-new', title: '浦发银行怎么看', created_at: '', updated_at: '' },
          turns: [
            { seq: 1, question: '浦发银行怎么看', blocks: [], status: 'done', created_at: '', updated_at: '' },
          ],
        })
      }),
    )
    const onSessionCreated = vi.fn()
    const { rerender } = renderPane({ onSessionCreated })

    fireEvent.change(screen.getByLabelText('向对话提问'), { target: { value: '浦发银行怎么看' } })
    fireEvent.submit(screen.getByTestId('composer'))
    await vi.waitFor(() => expect(onSessionCreated).toHaveBeenCalledWith('s-new'))

    // 外壳把 activeSessionId 落到新 id，第二问带上同一 session_id
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <ChatPane
          activeSessionId="s-new"
          onOpenResearch={() => {}}
          onAskDesk={() => {}}
          onRoundComplete={() => {}}
          onSessionCreated={onSessionCreated}
        />
      </MemoryRouter>,
    )
    // 首问答案在交接期间不闪没：服务端读回的轮次与本地那条按 seq 去重
    await vi.waitFor(() => expect(screen.getByText('浦发银行怎么看')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('向对话提问'), { target: { value: '它的分红怎么样' } })
    fireEvent.submit(screen.getByTestId('composer'))
    await vi.waitFor(() => expect(posted.length).toBeGreaterThanOrEqual(2))
    const second = posted[posted.length - 1]
    expect(second.body.session_id).toBe('s-new')
  })

  it('停止：停在消息层显示「已停止 + 复制」，不闪回空白', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise((resolve) => {
            // 永不返回；停止靠 AbortSignal 触发
            init?.signal?.addEventListener('abort', () => {
              resolve(jsonOk({ detail: 'aborted' }, 599))
            })
          }),
      ),
    )
    renderPane()
    fireEvent.change(screen.getByLabelText('向对话提问'), { target: { value: '光大证券怎么看' } })
    fireEvent.submit(screen.getByTestId('composer'))
    fireEvent.click(screen.getByRole('button', { name: '停止' }))

    await vi.waitFor(() => expect(screen.getByText(/已停止本次回答/)).toBeTruthy())
    expect(screen.getByRole('button', { name: /复制问题/ })).toBeTruthy()
    expect(screen.queryByText(/问一句就行/)).toBeNull()
  })
})
