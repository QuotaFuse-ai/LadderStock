import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, Copy, MessageSquarePlus, Pencil, Send, Square, X } from 'lucide-react'

import {
  ConversationError,
  httpConversation,
  type ConversationTurn,
  type SessionInfo,
  type TurnStatus,
  type TurnSuccess,
} from '../../../api/conversation'
import { researchHref } from '../../../lib/searchRoutes'
import { normalizeQuery } from '../../../lib/searchIntent'
import { useIsMobile } from '../../../lib/mediaQuery'
import { useAsync } from '../../../lib/useAsync'
import { BlockRenderer } from '../../../components/answer-blocks'
import { Badge, Button, ErrorBlock, IconButton, Textarea } from '../../../components/ui'

/**
 * 首页对话主区（QUOT-582 / F3–F6）。
 *
 * 取数只走 `/api/conversation`（DEC-084），一轮问答是**同步**的：请求返回就是整轮答案，
 * 没有 SSE。因此「流式」相关的五条追加验收按现行契约落成：
 *
 * - 增量追加：每轮消息按 `turn_seq`（没有则本地自增 id）做稳定 key，新消息只 append，
 *   旧消息引用不变；块由阶段 E 的 `BlockRenderer` 在同一个 `blocks` 引用上渲染，
 *   不会因为上一轮进来而重建（第 5 条「按块边界」天然成立：块整块到达，不按行切）。
 * - 节流：同步端点没有逐块到达，没有可合并的帧；这里不引入定时器批处理（会平白
 *   推迟一次整块渲染），也不做打字机。
 * - 停止：同步 POST 的「停止」= 中断请求，服务端把该轮定格为 `aborted` 并保留已到达的块；
 *   页面标「已停止」，下一次 `GET /sessions/{id}/turns` 读回同一状态。
 * - 「转上下文」：QUOT-579 未把对话历史并进 `/api/conversation` 的 `context`，按降级交付——
 *   停止后保留「复制进输入框」，由用户自己决定是否带进下一问。
 * - 重连退避：只在**恢复读取**（`GET /sessions` / `GET /turns`）上做，幂等可重放；
 *   轮询本身会持续重试，所以有上限（`RECOVERY_MAX_ATTEMPTS`），超限后降级为
 *   「重连失败 + 手动重试」，不白屏、不无限重试。`POST /turns` 不自动重放：不做幂等键，
 *   自动重试可能落两行。
 */

export const RECOVERY_MAX_ATTEMPTS = 3
export const RECOVERY_BASE_DELAY_MS = 1000

/** 失败重试的退避表：[1s, 2s, 4s]，再失败就停在上限不再自动重试 */
export function recoveryDelay(attempt: number): number {
  return RECOVERY_BASE_DELAY_MS * 2 ** Math.max(0, attempt - 1)
}

export const SUGGESTIONS = [
  '今天大盘和自选有哪些值得注意的变化？',
  '帮我看看一家公司的基本面与近期风险',
  '这个板块最近有哪些新变化值得跟踪？',
  '帮我核对一个观点，该先查哪些数据？',
] as const

const WELCOME =
  '问一句就行：股票名或代码、板块、或者一个你想核实的观点。助手会先给结论，再列出用到的数据和可以点开的页面。'

export function statusLabel(status: TurnStatus): string {
  switch (status) {
    case 'streaming':
      return '生成中'
    case 'aborted':
      return '已停止'
    case 'failed':
      return '未答出'
    case 'done':
      return ''
  }
}

interface PendingTurn {
  localId: string
  status: 'streaming' | 'aborted'
}

interface ChatPaneProps {
  activeSessionId: string | null
  /** 从聊天里跳到某个标的：交给 `HomeShell` 切到研究台 */
  onOpenResearch: (code: string) => void
  /** 把问题带到研究台：`HomeShell` 切模式并把问题交给研究面板 */
  onAskDesk: (question: string) => void
  /** 一轮问答落地后通知外壳：会话列表的标题 / 轮数要重取 */
  onRoundComplete: () => void
  /**
   * 空态两段式（QUOT-587）：首问发出即回调，外壳据此让会话列表从隐藏转出现。
   * 空态层与消息层在同一页内互斥切换，不跳路由。
   */
  onFirstRoundStarted?: () => void
  /**
   * 首问落地（QUOT-588）：服务端返回新 `session_id` 时回调，外壳 `setActiveSessionId`。
   * 不回传会导致 `activeSessionId` 恒 null：失败时闪回空白、第二问另起新会话（多轮断裂）。
   */
  onSessionCreated?: (sessionId: string) => void
  /**
   * 顶栏「问 AI」聚焦请求（QUOT-590）：计数变化即聚焦 composer，只聚焦不自动发送。
   * chat 模式可见时触发；隐藏态的请求由外壳在聚焦时分流（不会发到这里）。
   */
  focusRequest?: number
}

interface PaneData {
  session: SessionInfo | null
  turns: Array<ConversationTurn & { localId: string }>
}

const EMPTY_PANE: PaneData = { session: null, turns: [] }

export function ChatPane({ activeSessionId, onOpenResearch, onAskDesk, onRoundComplete, onFirstRoundStarted, onSessionCreated, focusRequest = 0 }: ChatPaneProps) {
  const navigate = useNavigate()
  const isMobile = useIsMobile()

  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<PendingTurn | null>(null)
  const [appendTurns, setAppendTurns] = useState<PaneData['turns']>([])
  const [failure, setFailure] = useState<{ detail: string; retryable: boolean } | null>(null)
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)
  const [stoppedQuestion, setStoppedQuestion] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<'idle' | 'done' | 'failed'>('idle')
  const [renaming, setRenaming] = useState(false)
  const [renameDraft, setRenameDraft] = useState('')
  const [renamingBusy, setRenamingBusy] = useState(false)
  const [renameError, setRenameError] = useState<string | null>(null)
  const [recoveryAttempts, setRecoveryAttempts] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const requestSeqRef = useRef(0)
  const localSeqRef = useRef(0)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const recoveryTimerRef = useRef<number | null>(null)

  // 本地标题（改名 / 首问回写）与「新的会话 id」都从取回的数据覆盖显示
  const [localSession, setLocalSession] = useState<{ id: string; title: string } | null>(null)

  const pane = useAsync<PaneData>(
    (signal) =>
      activeSessionId
        ? httpConversation.listTurns(activeSessionId, signal).then((result) => ({
            session: result.session,
            turns: result.turns.map((turn) => ({ ...turn, localId: `t${turn.seq}` })),
          }))
        : Promise.resolve(EMPTY_PANE),
    [activeSessionId],
    { enabled: Boolean(activeSessionId) },
  )

  const reloadPane = pane.reload

  // 换会话：旧请求作废，失败态一起清掉（同一会话的读取结果由 useAsync 保留）
  useEffect(() => {
    requestSeqRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setPending(null)
    setFailure(null)
    setFailedQuestion(null)
    setStoppedQuestion(null)
    setRenaming(false)
    setRenameError(null)
    setRecoveryAttempts(0)
    if (recoveryTimerRef.current !== null) {
      window.clearTimeout(recoveryTimerRef.current)
      recoveryTimerRef.current = null
    }
    // QUOT-588 首问交接不闪空：activeSessionId 落到首问新建的会话时，本地那条已落地
    // 的答案保留到服务端读回（`turns` 按 seq 去重，读回即无缝替换）；其他换会话照旧清掉
    if (activeSessionId === localSession?.id) return
    setAppendTurns([])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId])

  // 「新对话」：首问还没发出去前先算作一条排队中的会话（不发请求、不建空会话）；
  // 等 `activeSessionId` 真的变成 null 才清，避免首问的回跳把临时条目弄丢
  useEffect(() => {
    if (activeSessionId === null) setLocalSession(null)
  }, [activeSessionId])

  useEffect(() => () => abortRef.current?.abort(), [])

  // 顶栏「问 AI」（QUOT-590）：计数变化即聚焦 composer，只聚焦不自动发送
  useEffect(() => {
    if (focusRequest > 0) inputRef.current?.focus()
  }, [focusRequest])

  useEffect(
    () => () => {
      if (recoveryTimerRef.current !== null) window.clearTimeout(recoveryTimerRef.current)
    },
    [],
  )

  const persisted = pane.data?.turns ?? []
  // 读回的服务端轮次带上本地追加里已经落地的那几条：同一 `seq` 只留一份（本地那条带着
  // 刚回来的 `blocks`，不闪一下空白），key 因此始终稳定，列表只增不改
  const turns = useMemo(() => {
    if (appendTurns.length === 0) return persisted
    const appendedSeqs = new Set(appendTurns.filter((turn) => turn.seq > 0).map((turn) => turn.seq))
    return [...persisted.filter((turn) => !appendedSeqs.has(turn.seq)), ...appendTurns]
  }, [persisted, appendTurns])
  const persistedTitle = localSession && localSession.id === activeSessionId
    ? localSession.title
    : (pane.data?.session?.title ?? '')
  const sessionTitle = persistedTitle.trim()
  const hasConversation = turns.length > 0 || pending !== null

  useEffect(() => {
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [turns.length, pending?.status])

  // 恢复读取失败（网络抖动 / 本机服务重启）：退避重取，超限转手动
  const paneFailed = pane.status === 'error' && Boolean(activeSessionId)
  useEffect(() => {
    if (!paneFailed || recoveryAttempts >= RECOVERY_MAX_ATTEMPTS) return
    const timer = window.setTimeout(() => {
      setRecoveryAttempts((count) => count + 1)
      reloadPane()
    }, recoveryDelay(recoveryAttempts + 1))
    recoveryTimerRef.current = timer
    return () => window.clearTimeout(timer)
  }, [paneFailed, recoveryAttempts, reloadPane])

  const retryNow = useCallback(() => {
    setRecoveryAttempts(0)
    reloadPane()
  }, [reloadPane])

  const openResearch = useCallback(
    (code: string) => {
      onOpenResearch(code)
      navigate(researchHref(code))
    },
    [navigate, onOpenResearch],
  )

  // 答案块里的 `action` 目标是站内路由：个股代码走研究台，其余按导航注册表直接跳
  const navigateTarget = useCallback(
    (target: string) => {
      const path = target.split(/[?#]/)[0] ?? ''
      const match = /^\/research\/([^/]+)$/.exec(path)
      if (match) {
        openResearch(decodeURIComponent(match[1]))
        return
      }
      navigate(target)
    },
    [navigate, openResearch],
  )

  const resync = useCallback(() => {
    reloadPane()
  }, [reloadPane])

  const ask = useCallback(
    async (raw: string) => {
      const question = raw.trim()
      if (!question || pending) return

      // 空态两段式：首问发出，消息层接管（外壳会同时让会话列表出现）
      onFirstRoundStarted?.()

      const seq = requestSeqRef.current + 1
      requestSeqRef.current = seq
      localSeqRef.current += 1
      const localId = `local-${localSeqRef.current}`
      const controller = new AbortController()
      abortRef.current = controller

      setAppendTurns((prev) => [
        ...prev,
        { seq: 0, question, blocks: null, status: 'streaming', created_at: '', updated_at: '', localId },
      ])
      setPending({ localId, status: 'streaming' })
      setDraft('')
      setFailure(null)
      setFailedQuestion(null)
      setStoppedQuestion(null)
      setRenaming(false)

      try {
        const reply = await httpConversation.sendTurn(
          {
            message: question,
            session_id: activeSessionId ?? undefined,
            // 契约（D3）：不带 history，会话历史由服务端从 chat.sqlite 读；首页没有页级数据可塞
            context: { page: 'home' },
          },
          controller.signal,
        )
        if (requestSeqRef.current !== seq) return
        applyReply(localId, reply)
      } catch (error) {
        if (requestSeqRef.current !== seq) return
        if (controller.signal.aborted) {
          // 用户点的停止：定格这一轮，保留已填入的问句供复制 / 重发
          setAppendTurns((prev) =>
            prev.map((turn) => (turn.localId === localId ? { ...turn, status: 'aborted' } : turn)),
          )
          setPending(null)
          setStoppedQuestion(question)
          resync()
          return
        }
        const info = describeTurnFailure(error)
        setAppendTurns((prev) => prev.filter((turn) => turn.localId !== localId))
        setFailure({ detail: info.detail, retryable: info.retryable })
        setFailedQuestion(question)
        setPending(null)
        // 连不上 / 超时：服务端可能已经落了一行，重新拉一次按真实状态显示
        if (error instanceof ConversationError && (error.kind === 'not-connected' || error.kind === 'timeout')) {
          resync()
        }
      } finally {
        if (requestSeqRef.current === seq) abortRef.current = null
      }
    },
    [activeSessionId, onFirstRoundStarted, onRoundComplete, pending, resync],
  )

  function applyReply(localId: string, reply: TurnSuccess) {
    setAppendTurns((prev) =>
      prev.map((turn) =>
        turn.localId === localId
          ? { ...turn, seq: reply.turn_seq ?? 0, blocks: reply.blocks, status: reply.status }
          : turn,
      ),
    )
    setPending(null)
    if (reply.session_id !== activeSessionId) {
      // 首问：会话 id 是新的，标题由服务端按首问截 40 码位生成，读回前先留空
      setLocalSession({ id: reply.session_id, title: '' })
      // QUOT-588：把新 id 交回外壳——activeSessionId 落到新会话，第二问续同一会话
      onSessionCreated?.(reply.session_id)
    }
    // 会话列表按服务端 `turns` / `title` 显示：这一轮落地后重取一次
    onRoundComplete()
    resync()
  }

  const retry = useCallback(() => {
    if (failedQuestion) void ask(failedQuestion)
  }, [ask, failedQuestion])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void ask(draft)
  }

  const copyStopped = useCallback(async () => {
    const text = stoppedQuestion ?? draft
    if (!text.trim()) return
    try {
      if (!navigator.clipboard) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(text)
      setCopyState('done')
    } catch {
      // 剪贴板不可用：退回输入框，用户仍能手动带走
      setDraft(text)
      setCopyState('failed')
    }
    window.setTimeout(() => setCopyState('idle'), 1800)
  }, [draft, stoppedQuestion])

  const jumpToResearch = useCallback(() => {
    const code = normalizeQuery(draft || stoppedQuestion || sessionTitle).code
    if (code) openResearch(code)
  }, [draft, stoppedQuestion, sessionTitle, openResearch])

  // 「问研究台」带的是当前这句（没在输入就带最近一条留下的问句），研究台不依赖 AI 通道也能看数
  const askDesk = useCallback(() => {
    const text = draft.trim() || stoppedQuestion || ''
    if (text) onAskDesk(text)
    else if (sessionTitle) onAskDesk(sessionTitle)
  }, [draft, onAskDesk, sessionTitle, stoppedQuestion])

  const beginRename = useCallback(() => {
    setRenaming(true)
    setRenameError(null)
    setRenameDraft(sessionTitle)
  }, [sessionTitle])

  const commitRename = useCallback(async () => {
    const id = activeSessionId ?? localSession?.id ?? null
    const next = renameDraft.trim()
    if (!id || !next || renamingBusy) return
    setRenamingBusy(true)
    setRenameError(null)
    try {
      // 截断与空白折叠都在服务端（40 码位），页面拿返回值显示
      const result = await httpConversation.renameSession(id, next)
      setLocalSession({ id: result.session_id, title: result.title })
      setRenaming(false)
      resync()
    } catch (error) {
      setRenameError(describeTurnFailure(error).detail)
    } finally {
      setRenamingBusy(false)
    }
  }, [activeSessionId, localSession?.id, renameDraft, renamingBusy, resync])

  const cancelRename = useCallback(() => {
    setRenaming(false)
    setRenameError(null)
  }, [])

  const busy = pending !== null
  const titleText = sessionTitle || (hasConversation ? '未命名对话' : '新对话')
  const recoverable = paneFailed && recoveryAttempts >= RECOVERY_MAX_ATTEMPTS
  // 空态两段式（QUOT-587）：冷启动（未选中任何会话、也没有进行中的内容）整区就是
  // 「空白背景 + 居中输入框」；首问发出后消息层接管、输入框落到底部。
  // 选中了会话（含 0 轮的空会话）仍走常规消息层，标题行 / 重命名入口保留。
  // QUOT-588：失败 / 停止态也停在消息层——否则首问失败瞬间（appendTurns 被滤掉、
  // activeSessionId 还是 null）emptyStage 翻 true，失败 + 重试被藏进空白欢迎层。
  const emptyStage =
    activeSessionId === null && !hasConversation && !recoverable && !failure && !failedQuestion && !stoppedQuestion

  const composer = (
    <form
      aria-label="对话输入区"
      data-testid="composer"
      className="flex shrink-0 flex-col gap-2 rounded-2xl border border-border-strong bg-surface p-2 transition-colors duration-fast ease-ldst"
      onSubmit={(event) => {
        event.preventDefault()
        void ask(draft)
      }}
    >
      <Textarea
        ref={inputRef}
        aria-label="向对话提问"
        placeholder={isMobile ? '问一句股票或板块…' : '问一句股票、板块，或一个想核实的观点…（Shift+Enter 换行）'}
        value={draft}
        rows={3}
        maxLength={2000}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        className="min-w-0 resize-none !border-0 bg-transparent px-1.5 pt-0.5 focus-visible:!border-transparent focus-visible:!shadow-none"
      />
      <div className="flex items-center justify-end">
        {busy ? (
          <IconButton
            label="停止"
            variant="secondary"
            onClick={stop}
            icon={<Square className="size-3.5" aria-hidden="true" />}
          />
        ) : (
          <IconButton
            label="发送"
            type="submit"
            disabled={!draft.trim()}
            className="size-9 rounded-full border border-border-strong"
            style={
              draft.trim()
                ? { backgroundColor: 'var(--ldst-accent-solid)', color: '#ffffff', borderColor: 'var(--ldst-accent-solid)' }
                : { backgroundColor: 'var(--ldst-surface-3)', color: 'var(--ldst-text-muted)' }
            }
            icon={<Send className="size-4" aria-hidden="true" />}
          />
        )}
      </div>
    </form>
  )

  if (emptyStage) {
    return (
      <section aria-label="首页对话" className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-4">
        <div className="flex w-full max-w-measure flex-col gap-3">
          <p className="text-center text-body leading-body text-text-secondary">{WELCOME}</p>
          {composer}
          <div className="flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((text) => (
              <button
                key={text}
                type="button"
                onClick={() => void ask(text)}
                className="inline-flex max-w-full items-center rounded-pill border border-border bg-accent-soft px-3 py-1.5 text-left text-13px leading-tight text-text transition-colors duration-fast hover:border-border-strong hover:bg-surface-3"
              >
                {text}
              </button>
            ))}
          </div>
          <p className="text-center text-11px text-text-muted">对话只存本机 · 回车发送，Shift+Enter 换行</p>
        </div>
      </section>
    )
  }

  return (
    <section aria-label="首页对话" className="flex min-h-0 flex-1 flex-col gap-2">
      <header className="flex min-h-control items-center justify-between gap-2">
        {renaming ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              void commitRename()
            }}
          >
            <input
              aria-label="会话标题"
              value={renameDraft}
              autoFocus
              maxLength={120}
              onChange={(event) => setRenameDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') cancelRename()
              }}
              className="h-control min-w-0 flex-1 rounded border border-border-strong bg-sunken px-2 text-body text-text focus-visible:border-accent"
            />
            <Button type="submit" size="md" variant="primary" loading={renamingBusy} disabled={!renameDraft.trim()}>
              保存
            </Button>
            <IconButton label="取消改名" icon={<X className="size-4" aria-hidden="true" />} onClick={cancelRename} />
            {renameError && (
              <span role="alert" className="text-caption text-danger">
                {renameError}
              </span>
            )}
          </form>
        ) : (
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="truncate text-subtitle font-semibold text-text" title={titleText}>
              {titleText}
            </h1>
            {activeSessionId && sessionTitle && (
              <IconButton
                label="重命名会话"
                icon={<Pencil className="size-3.5" aria-hidden="true" />}
                onClick={beginRename}
              />
            )}
          </div>
        )}
        {localSession?.id && localSession.id !== activeSessionId && (
          <Badge tone="flat">会话已建立</Badge>
        )}
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {recoverable ? (
          <ErrorBlock
            title="对话记录没能取回"
            detail={`已自动重连 ${RECOVERY_MAX_ATTEMPTS} 次仍未成功；可以手动重试，已发出的问题服务端都留着。`}
            code="E_RECONNECT"
            onRetry={retryNow}
          />
        ) : (
          <>
            {paneFailed && stopsLikeOffline(pane.error) && (
              <ErrorBlock
                title="本机服务未连接"
                detail="对话记录存在本机服务里；服务恢复后这里会自动重试。"
                variant="banner"
                onRetry={retryNow}
              />
            )}

            {pane.status === 'loading' && !hasConversation && (
              <p className="text-caption text-text-muted" aria-busy="true">
                正在加载对话…
              </p>
            )}

            <ol role="log" aria-live="polite" aria-label="对话记录" className="flex flex-col gap-4 py-2">
              {turns.map((turn) => (
                <li key={turn.localId} className="flex flex-col gap-2">
                  <div className="flex justify-end">
                    <p className="max-w-measure rounded border border-border bg-surface-2 px-3 py-2 text-body leading-body text-text">
                      {turn.question}
                    </p>
                  </div>
                  <div className="flex flex-col gap-1">
                    {statusLabel(turn.status) && (
                      <p className="text-caption text-text-muted">{statusLabel(turn.status)}</p>
                    )}
                    <BlockRenderer
                      blocks={turn.blocks}
                      onRetry={retry}
                      onNavigate={navigateTarget}
                      onOpenStatus={() => navigate('/settings')}
                    />
                  </div>
                </li>
              ))}
              {pending && (
                <li key={pending.localId} className="flex flex-col gap-1">
                  <p className="text-caption text-text-muted" aria-busy="true">
                    生成中…
                  </p>
                </li>
              )}
            </ol>

            {stoppedQuestion && (
              <div role="status" className="mb-2 flex flex-wrap items-center gap-2 rounded border border-border bg-surface-2 px-3 py-2">
                <span className="text-body text-text">已停止本次回答，已到达的内容保留在上面。</span>
                <Button
                  size="md"
                  variant="secondary"
                  onClick={() => void copyStopped()}
                  leadingIcon={
                    copyState === 'done' ? (
                      <Check className="size-3.5" aria-hidden="true" />
                    ) : (
                      <Copy className="size-3.5" aria-hidden="true" />
                    )
                  }
                >
                  {copyState === 'done' ? '已复制' : copyState === 'failed' ? '已放回输入框' : '复制问题'}
                </Button>
                <span className="text-caption text-text-muted">想接着问：复制后改一改，再发一次。</span>
              </div>
            )}

            {failure && (
              <ErrorBlock
                title="这一轮没有答出来"
                detail={failure.detail}
                onRetry={failure.retryable ? retry : undefined}
                retryLabel="重试这一问"
              />
            )}
          </>
        )}
      </div>

      {composer}

      <div className="flex shrink-0 items-center justify-between gap-2 text-11px text-text-muted">
        <span className="truncate">对话只存本机 · 回车发送，Shift+Enter 换行</span>
        <div className="flex shrink-0 items-center gap-1">
          {normalizeQuery(draft).code && (
            <Button size="md" variant="ghost" onClick={jumpToResearch}>
              去研究台看这只
            </Button>
          )}
          <Button size="md" variant="ghost" onClick={askDesk}>
            <span className="inline-flex items-center gap-1">
              <MessageSquarePlus className="size-3.5" aria-hidden="true" />
              问研究台
            </span>
          </Button>
        </div>
      </div>
    </section>
  )
}

function describeTurnFailure(error: unknown): { detail: string; retryable: boolean } {
  if (error instanceof ConversationError) {
    const detail =
      error.body?.detail ||
      error.body?.failure?.message ||
      error.detail ||
      error.message
    return { detail, retryable: error.retryable }
  }
  if (error instanceof Error) return { detail: error.message, retryable: true }
  return { detail: '请求失败', retryable: true }
}

function stopsLikeOffline(error: unknown): boolean {
  return error instanceof ConversationError && error.kind === 'not-connected'
}
