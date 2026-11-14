import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { MoreHorizontal, MessageSquarePlus, Pencil, Plus, Trash2 } from 'lucide-react'

import { ConversationError, type SessionSummary } from '../../../api/conversation'
import { formatInboxTime } from '../../../lib/format'
import { cn } from '../../../lib/cn'
import { useMediaQuery } from '../../../lib/mediaQuery'
import { Button, ConfirmDialog, EmptyState, Skeleton } from '../../../components/ui'

/**
 * 会话列表（QUOT-582 / F3）：标题 + 时间 + 一行预览，数据来自阶段 C 的
 * `GET /api/conversation/sessions`（`turns` 是轮数，预览就写它，不臆造正文摘要）。
 *
 * QUOT-602 起每条会话带三点菜单（重命名 / 删除）：hover / focus / 选中时出现，
 * 触屏（`hover: none`）常显；键盘可开（`aria-haspopup="menu"` + ←↑↓→ / Home / End），
 * Esc 关闭并把焦点还给触发按钮。改名截断与空白折叠都在服务端（40 码位），这里只传原文。
 * 窄屏由 `HomeShell` 塞进抽屉，本组件不知道自己在哪。
 */

export function sessionPreview(summary: SessionSummary): string {
  const turns = Math.max(0, summary.turns)
  return turns === 0 ? '还没有提问' : `${turns} 轮对话`
}

export function sessionTitle(summary: SessionSummary): string {
  return summary.title.trim() || '未命名对话'
}

/**
 * 改名失败的口径：服务端 404（别处已经删了）也要说人话，而不是「对话接口返回 HTTP 404」。
 * 删除走幂等接口（重复删也是 200），所以这里只需要处理改名。
 */
export function describeSessionError(error: unknown): string {
  if (error instanceof ConversationError) {
    if (error.status === 404) return '这条会话已经不存在了'
    return error.body?.detail || error.detail || error.message
  }
  if (error instanceof Error) return error.message
  return '请求失败'
}

export interface SessionListProps {
  sessions: SessionSummary[]
  status: 'loading' | 'ready' | 'error'
  activeId: string | null
  onSelect: (sessionId: string) => void
  onNew: () => void
  /**
   * 改名（QUOT-602）：不传就不显示「重命名」。返回值用 `{ session_id, title }`，
   * 与 `PATCH /api/conversation/sessions/{id}` 一致；抛错时菜单里原地显示错误、不关。
   */
  onRename?: (sessionId: string, title: string) => Promise<{ session_id: string; title: string }>
  /** 删除（QUOT-602）：确认后由外壳落库并刷新列表；重复删不报错（服务端幂等） */
  onDelete?: (sessionId: string) => Promise<void>
  className?: string
}

type MenuState =
  | { kind: 'menu'; sessionId: string }
  | { kind: 'rename'; sessionId: string; draft: string; busy: boolean; error: string | null }
  | { kind: 'delete'; sessionId: string; busy: boolean; error: string | null }
  | null

export function SessionList({
  sessions,
  status,
  activeId,
  onSelect,
  onNew,
  onRename,
  onDelete,
  className,
}: SessionListProps) {
  // 触屏没有 hover：菜单入口常显，不依赖悬停才出现
  const noHover = useMediaQuery('(hover: none)')
  const [menu, setMenu] = useState<MenuState>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRefs = useRef(new Map<string, HTMLButtonElement>())
  const itemRefs = useRef(new Map<string, HTMLButtonElement>())
  const menuId = useId()

  // 列表重取后，开了菜单的那条如果没了（别处删了 / 淘汰），菜单自己收起来
  useEffect(() => {
    if (!menu) return
    if (!sessions.some((session) => session.session_id === menu.sessionId)) setMenu(null)
  }, [menu, sessions])

  const closeMenu = useCallback((sessionId: string) => {
    setMenu(null)
    triggerRefs.current.get(sessionId)?.focus()
  }, [])

  // 点菜单外面收起；不抢焦点（点哪儿焦点去哪儿），Esc / 选中项才把焦点还给触发按钮
  useEffect(() => {
    if (!menu || menu.kind !== 'menu') return
    const onPointerDown = (event: PointerEvent) => {
      const node = rootRef.current
      if (node && event.target instanceof Node && node.contains(event.target)) {
        const trigger = triggerRefs.current.get(menu.sessionId)
        const list = document.getElementById(menuId)
        if (trigger?.contains(event.target) || list?.contains(event.target)) return
      }
      setMenu(null)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [menu, menuId])

  const menuButtons = useCallback((): HTMLButtonElement[] => {
    const list = document.getElementById(menuId)
    if (!list) return []
    return Array.from(list.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))
  }, [menuId])

  const openMenu = useCallback(
    (sessionId: string, focus: 'first' | 'last' | null) => {
      setMenu({ kind: 'menu', sessionId })
      if (!focus) return
      // 渲染出来后再聚焦：同步 `focus()` 时菜单还没挂上
      requestAnimationFrame(() => {
        const items = menuButtons()
        const target = focus === 'first' ? items[0] : items[items.length - 1]
        target?.focus()
      })
    },
    [menuButtons],
  )

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>, sessionId: string) => {
    const items = menuButtons()
    if (items.length === 0) return
    const index = items.indexOf(document.activeElement as HTMLButtonElement)
    const focusAt = (next: number) => {
      event.preventDefault()
      items[(next + items.length) % items.length].focus()
    }
    switch (event.key) {
      case 'Escape':
        event.preventDefault()
        closeMenu(sessionId)
        break
      case 'ArrowDown':
        focusAt(index + 1)
        break
      case 'ArrowUp':
        focusAt(index < 0 ? items.length - 1 : index - 1)
        break
      case 'Home':
        focusAt(0)
        break
      case 'End':
        focusAt(items.length - 1)
        break
      case 'Tab':
        // Tab 关掉菜单再走默认顺序，不让焦点落在已卸载的项上
        setMenu(null)
        break
      default:
        break
    }
  }

  const beginRename = (session: SessionSummary) => {
    setMenu({ kind: 'rename', sessionId: session.session_id, draft: sessionTitle(session), busy: false, error: null })
  }

  const cancelRename = (sessionId: string) => {
    setMenu(null)
    // 取消改名回到列表项本身（菜单入口可能已随 hover 消失，落在行上更稳）
    requestAnimationFrame(() => itemRefs.current.get(sessionId)?.focus())
  }

  const commitRename = async (sessionId: string) => {
    if (!onRename || menu?.kind !== 'rename' || menu.sessionId !== sessionId) return
    const draft = menu.draft.trim()
    if (!draft || menu.busy) return
    setMenu({ ...menu, busy: true, error: null })
    try {
      const result = await onRename(sessionId, draft)
      // 服务端折叠空白并截到 40 码位：以返回值为准，不在前端做第二份截断
      setMenu(null)
      requestAnimationFrame(() => {
        const trigger = triggerRefs.current.get(sessionId)
        if (trigger) trigger.focus()
        else itemRefs.current.get(result.session_id)?.focus()
      })
    } catch (error) {
      setMenu({ ...menu, busy: false, error: describeSessionError(error) })
    }
  }

  const confirmDelete = async (sessionId: string) => {
    if (!onDelete || menu?.kind !== 'delete' || menu.sessionId !== sessionId) return
    if (menu.busy) return
    setMenu({ ...menu, busy: true, error: null })
    try {
      await onDelete(sessionId)
      // 列表项由外壳刷新后原地消失；焦点先落回列表项（还在）或「新对话」入口
      setMenu(null)
      requestAnimationFrame(() => itemRefs.current.get(sessionId)?.focus())
    } catch (error) {
      setMenu({ ...menu, busy: false, error: describeSessionError(error) })
    }
  }

  const deleting = menu?.kind === 'delete'
    ? sessions.find((session) => session.session_id === menu.sessionId) ?? null
    : null
  const deleteBusy = menu?.kind === 'delete' && menu.busy
  const deleteError = menu?.kind === 'delete' ? menu.error : null

  return (
    <nav aria-label="对话会话" className={cn('min-h-0 flex-col gap-2', className)}>
      <div className="flex items-center justify-between gap-2 px-1">
        <span className="text-caption font-medium text-text-secondary">最近对话</span>
        <Button
          size="md"
          variant="ghost"
          onClick={onNew}
          leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
        >
          新对话
        </Button>
      </div>

      <div ref={rootRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {status === 'loading' ? (
          <div className="flex flex-col gap-2 px-1 py-2" aria-busy="true" aria-live="polite">
            <span className="sr-only">正在加载对话列表…</span>
            {[0, 1, 2].map((index) => (
              <div key={index} className="flex flex-col gap-1.5">
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-2.5 w-1/2" />
              </div>
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <EmptyState
            compact
            icon={<MessageSquarePlus aria-hidden="true" />}
            title="还没有对话"
            description="在右边问一句试试：说股票名或代码，助手会据此给出结论和可跳转的页面。"
          />
        ) : (
          <ul className="flex flex-col gap-half">
            {sessions.map((session) => {
              const sessionId = session.session_id
              const selected = sessionId === activeId
              const menuOpen = menu?.sessionId === sessionId && menu.kind === 'menu'
              const renaming = menu?.kind === 'rename' && menu.sessionId === sessionId
              return (
                <li key={sessionId} className="group relative" data-session-id={sessionId}>
                  <button
                    ref={(node) => {
                      if (node) itemRefs.current.set(sessionId, node)
                      else itemRefs.current.delete(sessionId)
                    }}
                    type="button"
                    aria-current={selected ? 'true' : undefined}
                    onClick={() => onSelect(sessionId)}
                    data-testid="session-item"
                    className={cn(
                      'flex w-full flex-col gap-half rounded border py-1.5 pl-2 text-left transition-colors duration-fast ease-ldst',
                      // 右侧留出菜单入口的位置，标题不压到三点下面
                      'pr-9',
                      selected
                        ? 'border-border-strong bg-surface-3'
                        : 'border-transparent hover:border-border hover:bg-surface-2',
                    )}
                  >
                    <span className={cn('truncate text-body', selected ? 'text-text' : 'text-text-secondary')}>
                      {sessionTitle(session)}
                    </span>
                    <span className="flex items-center gap-1.5 text-11px text-text-muted">
                      <span className="ldst-num shrink-0">{formatInboxTime(session.updated_at)}</span>
                      <span aria-hidden="true">·</span>
                      <span className="truncate">{sessionPreview(session)}</span>
                    </span>
                  </button>

                  {(onRename || onDelete) && !renaming && (
                    <button
                      ref={(node) => {
                        if (node) triggerRefs.current.set(sessionId, node)
                        else triggerRefs.current.delete(sessionId)
                      }}
                      type="button"
                      aria-label={`会话操作：${sessionTitle(session)}`}
                      title="重命名 / 删除"
                      aria-haspopup="menu"
                      aria-expanded={menuOpen}
                      aria-controls={menuOpen ? menuId : undefined}
                      onClick={(event) => {
                        event.stopPropagation()
                        if (menuOpen) setMenu(null)
                        else openMenu(sessionId, 'first')
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== 'ArrowDown') return
                        event.preventDefault()
                        if (menuOpen) {
                          menuButtons()[0]?.focus()
                        } else {
                          openMenu(sessionId, 'first')
                        }
                      }}
                      className={cn(
                        'absolute right-1 top-1.5 inline-flex size-7 items-center justify-center rounded text-text-muted transition-opacity duration-fast ease-ldst',
                        'hover:bg-surface-3 hover:text-text focus-visible:opacity-100',
                        menuOpen ? 'opacity-100' : 'opacity-0',
                        // hover / 键盘 focus / 选中态出现；触屏常显（没有 hover 可依赖）
                        'group-hover:opacity-100 group-focus-within:opacity-100',
                        selected && 'opacity-100',
                        noHover && 'opacity-100',
                      )}
                    >
                      <MoreHorizontal className="size-4" aria-hidden="true" />
                    </button>
                  )}

                  {menuOpen && (
                    <div
                      id={menuId}
                      role="menu"
                      aria-label={`会话操作：${sessionTitle(session)}`}
                      className="absolute right-1 top-9 z-overlay flex min-w-32 flex-col gap-half rounded border border-border bg-surface p-1 shadow-overlay"
                      onKeyDown={(event) => onMenuKeyDown(event, sessionId)}
                    >
                      {onRename && (
                        <button
                          type="button"
                          role="menuitem"
                          tabIndex={-1}
                          onClick={() => beginRename(session)}
                          className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-body text-text-secondary transition-colors duration-fast ease-ldst hover:bg-surface-2 hover:text-text focus-visible:bg-surface-2 focus-visible:text-text"
                        >
                          <Pencil className="size-3.5" aria-hidden="true" />
                          重命名
                        </button>
                      )}
                      {onDelete && (
                        <button
                          type="button"
                          role="menuitem"
                          tabIndex={-1}
                          onClick={() => setMenu({ kind: 'delete', sessionId, busy: false, error: null })}
                          className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-body text-text-secondary transition-colors duration-fast ease-ldst hover:bg-danger-soft hover:text-danger focus-visible:bg-danger-soft focus-visible:text-danger"
                        >
                          <Trash2 className="size-3.5" aria-hidden="true" />
                          删除
                        </button>
                      )}
                    </div>
                  )}

                  {renaming && (
                    <form
                      className="flex flex-col gap-1 rounded border border-border-strong bg-surface p-1.5"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void commitRename(sessionId)
                      }}
                    >
                      <label className="sr-only" htmlFor={`${menuId}-rename-${sessionId}`}>
                        会话标题
                      </label>
                      <input
                        id={`${menuId}-rename-${sessionId}`}
                        value={menu.draft}
                        autoFocus
                        maxLength={120}
                        disabled={menu.busy}
                        onChange={(event) =>
                          setMenu({ ...menu, draft: event.target.value })
                        }
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') {
                            event.preventDefault()
                            cancelRename(sessionId)
                          }
                        }}
                        className="h-control min-w-0 rounded border border-border-strong bg-sunken px-2 text-body text-text focus-visible:border-accent"
                      />
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => cancelRename(sessionId)}
                          disabled={menu.busy}
                          className="h-7 rounded px-2 text-caption text-text-secondary hover:bg-surface-3 hover:text-text disabled:opacity-50"
                        >
                          取消
                        </button>
                        <button
                          type="submit"
                          disabled={menu.busy || !menu.draft.trim()}
                          className="h-7 rounded bg-accent-solid px-2 text-caption font-medium text-accent-contrast hover:bg-accent-solid-hover disabled:opacity-50"
                        >
                          {menu.busy ? '保存中…' : '保存'}
                        </button>
                      </div>
                      {menu.error && (
                        <span role="alert" className="text-caption text-danger">
                          {menu.error}
                        </span>
                      )}
                    </form>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {deleting && (
        <ConfirmDialog
          open
          tone="danger"
          title="删除这条对话？"
          description={
            <span>
              「{sessionTitle(deleting)}」删除后这条对话记录不再显示，不能恢复。
            </span>
          }
          confirmLabel={deleteBusy ? '删除中…' : '删除'}
          cancelLabel="取消"
          loading={deleteBusy}
          onConfirm={() => void confirmDelete(deleting.session_id)}
          onCancel={() => {
            if (deleteBusy) return
            setMenu(null)
            // `ConfirmDialog` 关闭时已经把焦点还给触发菜单项的按钮——它随菜单一起卸载了，
            // 这里落回这条会话的菜单入口（再按 Enter 能原路回来）
            requestAnimationFrame(() => triggerRefs.current.get(deleting.session_id)?.focus())
          }}
        >
          {deleteError && (
            <p role="alert" className="text-caption text-danger">
              {deleteError}
            </p>
          )}
        </ConfirmDialog>
      )}
    </nav>
  )
}
