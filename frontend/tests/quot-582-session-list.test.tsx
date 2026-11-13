// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionSummary } from '../src/api/conversation'
import { sessionPreview, sessionTitle, SessionList } from '../src/screens/home/shell/SessionList'

/**
 * QUOT-582 / F3：会话列表。
 *
 * 列表只回答「有哪些对话 / 选哪一条 / 开新对话」，改名在聊天主区标题行（另有用例）。
 * 预览按阶段 C 的 `turns` 轮数写（不臆造正文摘要）；标题为空时显「未命名对话」。
 */

function summary(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: 's-1',
    title: '浦发银行怎么看',
    created_at: '2026-11-06T09:00:00',
    updated_at: '2026-11-06T09:30:00',
    turns: 2,
    ...overrides,
  }
}

afterEach(cleanup)

describe('会话列表（F3）', () => {
  it('标题为空显示「未命名对话」，轮数为 0 显示「还没有提问」', () => {
    expect(sessionTitle(summary({ title: '   ' }))).toBe('未命名对话')
    expect(sessionTitle(summary())).toBe('浦发银行怎么看')
    expect(sessionPreview(summary({ turns: 0 }))).toBe('还没有提问')
    expect(sessionPreview(summary({ turns: 3 }))).toBe('3 轮对话')
  })

  it('加载中显示骨架（aria-busy），不出空态', () => {
    render(
      <SessionList
        sessions={[]}
        status="loading"
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
      />,
    )
    const nav = screen.getByRole('navigation', { name: '对话会话' })
    expect(nav.querySelector('[aria-busy="true"]')).not.toBeNull()
    expect(within(nav).queryByText('还没有对话')).toBeNull()
  })

  it('没有会话时出空态引导，不渲染列表项', () => {
    render(
      <SessionList
        sessions={[]}
        status="ready"
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
      />,
    )
    expect(screen.getByText('还没有对话')).toBeTruthy()
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('列出标题 / 时间 / 轮数预览，选中行带 aria-current', () => {
    render(
      <SessionList
        sessions={[
          summary(),
          summary({ session_id: 's-2', title: '', turns: 0, updated_at: '2026-11-05T15:00:00' }),
        ]}
        status="ready"
        activeId="s-1"
        onSelect={() => {}}
        onNew={() => {}}
      />,
    )
    const nav = screen.getByRole('navigation', { name: '对话会话' })
    const list = within(nav).getByRole('list')
    expect(list.children).toHaveLength(2)

    const first = within(nav).getByRole('button', { name: /浦发银行怎么看/ })
    expect(first.getAttribute('aria-current')).toBe('true')
    expect(first.textContent).toContain('2 轮对话')

    const second = within(nav).getByRole('button', { name: /未命名对话/ })
    expect(second.getAttribute('aria-current')).toBeNull()
    expect(second.textContent).toContain('还没有提问')
  })

  it('点会话与「新对话」各自回调', () => {
    const onSelect = vi.fn()
    const onNew = vi.fn()
    render(
      <SessionList
        sessions={[summary()]}
        status="ready"
        activeId={null}
        onSelect={onSelect}
        onNew={onNew}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /浦发银行怎么看/ }))
    expect(onSelect).toHaveBeenCalledWith('s-1')

    fireEvent.click(screen.getByRole('button', { name: /新对话/ }))
    expect(onNew).toHaveBeenCalledTimes(1)
  })
})
