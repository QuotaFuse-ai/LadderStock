// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConversationError, type SessionSummary } from '../src/api/conversation'
import { describeSessionError, SessionList } from '../src/screens/home/shell/SessionList'

/**
 * QUOT-602 P1：会话三点菜单（重命名 / 删除）。
 *
 * 入口不能只靠 hover：键盘要能开（focus + Enter / ArrowDown）、菜单项要能走（↑↓ / Home / End）、
 * Esc 关闭并把焦点还给触发按钮。删除必须先过确认框，文案写明「不再显示，不能恢复」。
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

function renderList(props: Partial<Parameters<typeof SessionList>[0]> = {}) {
  return render(
    <SessionList
      sessions={[summary(), summary({ session_id: 's-2', title: '中际旭创' })]}
      status="ready"
      activeId={null}
      onSelect={() => {}}
      onNew={() => {}}
      {...props}
    />,
  )
}

function triggerFor(title: string): HTMLButtonElement {
  return screen.getByRole('button', { name: `会话操作：${title}` }) as HTMLButtonElement
}

afterEach(cleanup)

describe('会话三点菜单（QUOT-602 P1）', () => {
  it('点入口开菜单，再点一次收起', () => {
    renderList({ onRename: vi.fn(), onDelete: vi.fn() })
    const trigger = triggerFor('浦发银行怎么看')
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu')
    expect(trigger.getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    const menu = screen.getByRole('menu', { name: '会话操作：浦发银行怎么看' })
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
      expect.stringContaining('重命名'),
      expect.stringContaining('删除'),
    ])
    // 只开这一条的菜单，列表里不出现第二份
    expect(screen.getAllByRole('menu')).toHaveLength(1)

    fireEvent.click(trigger)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('键盘：入口上 ArrowDown 进菜单首项，↓/↑ 循环，Esc 关闭并把焦点还给入口', async () => {
    renderList({ onRename: vi.fn(), onDelete: vi.fn() })
    const trigger = triggerFor('浦发银行怎么看')
    trigger.focus()

    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const menu = await screen.findByRole('menu', { name: '会话操作：浦发银行怎么看' })
    const items = within(menu).getAllByRole('menuitem')
    await waitFor(() => expect(document.activeElement).toBe(items[0]))

    fireEvent.keyDown(items[0], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(items[1])
    fireEvent.keyDown(items[1], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(items[0], { key: 'ArrowUp' })
    expect(document.activeElement).toBe(items[1])
    fireEvent.keyDown(items[1], { key: 'Home' })
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(items[0], { key: 'End' })
    expect(document.activeElement).toBe(items[1])

    fireEvent.keyDown(items[1], { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
    expect(document.activeElement).toBe(trigger)
  })

  it('删除要过确认框，文案写明「不再显示，不能恢复」；确认后回调且收起', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    renderList({ onRename: vi.fn(), onDelete })

    fireEvent.click(triggerFor('中际旭创'))
    fireEvent.click(screen.getByRole('menuitem', { name: /删除/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/删除后这条对话记录不再显示，不能恢复/)).toBeTruthy()
    expect(within(dialog).getByText(/中际旭创/)).toBeTruthy()
    expect(onDelete).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('s-2'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('确认框点取消不删，焦点回到这条会话的菜单入口', async () => {
    const onDelete = vi.fn()
    renderList({ onRename: vi.fn(), onDelete })
    const trigger = triggerFor('浦发银行怎么看')

    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: /删除/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(onDelete).not.toHaveBeenCalled()
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })

  it('删除失败时确认框留在原地并给出错误', async () => {
    const onDelete = vi.fn().mockRejectedValue(new Error('本机服务未连接'))
    renderList({ onRename: vi.fn(), onDelete })

    fireEvent.click(triggerFor('浦发银行怎么看'))
    fireEvent.click(screen.getByRole('menuitem', { name: /删除/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert.textContent).toContain('本机服务未连接')
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('重命名：菜单项开行内输入框（预填标题），保存回传原文，Esc 取消', async () => {
    const onRename = vi.fn().mockResolvedValue({ session_id: 's-1', title: '浦发银行' })
    renderList({ onRename, onDelete: vi.fn() })

    fireEvent.click(triggerFor('浦发银行怎么看'))
    fireEvent.click(screen.getByRole('menuitem', { name: /重命名/ }))
    const input = screen.getByLabelText('会话标题') as HTMLInputElement
    expect(input.value).toBe('浦发银行怎么看')

    fireEvent.change(input, { target: { value: '浦发银行' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(onRename).toHaveBeenCalledWith('s-1', '浦发银行'))
    await waitFor(() => expect(screen.queryByLabelText('会话标题')).toBeNull())

    // Esc 取消：不发第二次请求
    fireEvent.click(triggerFor('浦发银行怎么看'))
    fireEvent.click(screen.getByRole('menuitem', { name: /重命名/ }))
    const second = screen.getByLabelText('会话标题')
    fireEvent.keyDown(second, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByLabelText('会话标题')).toBeNull())
    expect(onRename).toHaveBeenCalledTimes(1)
  })

  it('没传 onRename / onDelete 时不出三点入口（抽屉外的旧用法照旧）', () => {
    renderList()
    expect(screen.queryByRole('button', { name: /会话操作/ })).toBeNull()
  })

  it('别处删掉后列表重取不再含这条：菜单自己收起', async () => {
    const { rerender } = renderList({ onRename: vi.fn(), onDelete: vi.fn() })
    fireEvent.click(triggerFor('浦发银行怎么看'))
    expect(screen.getByRole('menu')).toBeTruthy()

    rerender(
      <SessionList
        sessions={[summary({ session_id: 's-2', title: '中际旭创' })]}
        status="ready"
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
        onRename={vi.fn()}
        onDelete={vi.fn()}
      />,
    )
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  })
})

describe('改名失败的文案', () => {
  it('404 说「这条会话已经不存在了」，其他错误原样透出', () => {
    expect(
      describeSessionError(new ConversationError('http', '对话接口返回 HTTP 404', 404, 'HTTP 404')),
    ).toBe('这条会话已经不存在了')
    expect(describeSessionError(new Error('网络错误'))).toBe('网络错误')
    expect(describeSessionError('什么')).toBe('请求失败')
  })
})
