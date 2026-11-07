import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { Loader2, Send, Sparkles, Square } from 'lucide-react'

import {
  askResearchAssistant,
  type ResearchOverview,
} from '../../../api/research'
import { cn } from '../../../lib/cn'
import { useAsync } from '../../../lib/useAsync'
import {
  formatChangePct,
  formatMoney,
  MISSING,
} from '../../../lib/format'
import {
  Badge,
  Button,
  Drawer,
  EmptyState,
  ErrorBlock,
  Textarea,
} from '../../ui'
import {
  buildAssistantContext,
  describeAssistantError,
  loadAssistantPageData,
  serializeAssistantContext,
  type AssistantFailureInfo,
  type AssistantPageData,
} from './aiContext'

/**
 * 页级研究助手抽屉（QUOT-454）。
 *
 * 绑定当前 `/research/:code` 的页级上下文：代码 / 名称 / 研究日 / 当前视图 +
 * 页面已展示的行情、K 线区间、财报期次与事件条数（白名单见 `aiContext.ts`）。
 * 模型调用走后端既有的 `/api/research/{code}/assistant` 通道，前端不拼提示词、
 * 不持有任何 key；回答只作文本解读，不回写页面数据。
 *
 * 降级分四种说法：本机未连接、未配置模型、回答超时、上游失败；都带重试。
 */

export interface ResearchAiDockProps {
  code: string | null
  name: string | null
  scope?: { tab: string; date: string } | null
  /** 概览 tab 已取到的数据；传入可避免为上下文重复取一次 */
  overview?: ResearchOverview | null
  className?: string
}

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  stopped?: boolean
}

const EMPTY_DATA: AssistantPageData = {
  overview: null,
  earnings: null,
  events: null,
  issues: [],
}

function quickQuestions(label: string, tab: string): string[] {
  if (tab === 'research') {
    return [
      `${label}这份研究的裁决点是什么`,
      `${label}哪些数据有缺口`,
      `${label}反证部分说了什么`,
    ]
  }
  return [
    `${label}最近的走势怎么样`,
    `${label}下一份财报要看什么`,
    `${label}有哪些值得留意的事件`,
  ]
}

function ContextItem({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note?: string | null
}) {
  return (
    <div className="flex min-w-0 flex-col gap-half">
      <dt className="text-caption text-text-muted">{label}</dt>
      <dd className="truncate text-body text-text" title={value}>
        {value}
      </dd>
      {note && (
        <span className="truncate text-caption text-text-muted" title={note}>
          {note}
        </span>
      )}
    </div>
  )
}

/** 行情时间戳可能是 `YYYYMMDDHHmmss` 原值，展示时排一下版；不认识的格式原样显示。 */
function displayTimestamp(value: string | null | undefined): string | null {
  if (!value) return null
  const matched = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(value)
  return matched
    ? `${matched[1]}-${matched[2]}-${matched[3]} ${matched[4]}:${matched[5]}:${matched[6]}`
    : value
}

export function ResearchAiDock({
  code,
  name,
  scope,
  overview = null,
  className,
}: ResearchAiDockProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<AssistantFailureInfo | null>(null)
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)
  const [showContext, setShowContext] = useState(false)

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const requestSeqRef = useRef(0)
  const messageIdRef = useRef(0)

  const tab = scope?.tab ?? 'overview'
  const date = scope?.date ?? ''
  const label = name ?? code ?? '这只'
  const title = `研究助手 · 个股深挖${name ? ` · ${name}` : code ? ` · ${code}` : ''}`

  const pageData = useAsync<AssistantPageData>(
    (signal) =>
      code ? loadAssistantPageData(code, signal, overview) : Promise.resolve(EMPTY_DATA),
    [code, overview],
    { enabled: open && Boolean(code) },
  )

  const context = useMemo(() => {
    if (!code) return null
    return buildAssistantContext({
      code,
      name,
      date,
      tab,
      overview: pageData.data?.overview ?? overview,
      earnings: pageData.data?.earnings ?? null,
      events: pageData.data?.events ?? null,
    })
  }, [code, name, date, tab, overview, pageData.data])

  const serializedContext = useMemo(
    () => (context ? serializeAssistantContext(context) : ''),
    [context],
  )

  // 换标的时旧对话不再属于当前上下文，直接清掉并中止在途请求
  useEffect(() => {
    abortRef.current?.abort()
    abortRef.current = null
    requestSeqRef.current += 1
    setMessages([])
    setFailure(null)
    setFailedQuestion(null)
    setPending(false)
  }, [code])

  useEffect(() => () => abortRef.current?.abort(), [])

  const close = useCallback(() => {
    // 关闭即放弃这一轮：不把「已停止」写进下次打开时的对话
    requestSeqRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setPending(false)
    setOpen(false)
  }, [])

  const ask = useCallback(
    async (raw: string, options: { echo?: boolean } = {}) => {
      const question = raw.trim()
      if (!question || !code || pending || !context) return
      const echo = options.echo ?? true

      const seq = requestSeqRef.current + 1
      requestSeqRef.current = seq
      const controller = new AbortController()
      abortRef.current = controller

      if (echo) {
        messageIdRef.current += 1
        setMessages((prev) => [
          ...prev,
          { id: messageIdRef.current, role: 'user', text: question },
        ])
        setDraft('')
      }
      setFailure(null)
      setFailedQuestion(null)
      setPending(true)

      try {
        const reply = await askResearchAssistant(
          code,
          {
            question,
            context: serializeAssistantContext(context),
            date: date || undefined,
            tab,
          },
          controller.signal,
        )
        if (requestSeqRef.current !== seq) return
        messageIdRef.current += 1
        setMessages((prev) => [
          ...prev,
          { id: messageIdRef.current, role: 'assistant', text: reply.reply },
        ])
      } catch (error) {
        if (requestSeqRef.current !== seq) return
        if (controller.signal.aborted) {
          messageIdRef.current += 1
          setMessages((prev) => [
            ...prev,
            {
              id: messageIdRef.current,
              role: 'assistant',
              text: '已停止本次回答。',
              stopped: true,
            },
          ])
        } else {
          setFailure(describeAssistantError(error))
          setFailedQuestion(question)
        }
      } finally {
        if (requestSeqRef.current === seq) {
          abortRef.current = null
          setPending(false)
        }
      }
    },
    [code, pending, context, date, tab],
  )

  const retry = useCallback(() => {
    if (failedQuestion) void ask(failedQuestion, { echo: false })
  }, [ask, failedQuestion])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法候选期间的回车归输入法，不能当成发送
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void ask(draft)
  }

  const ready = Boolean(code) && context !== null
  const questions = quickQuestions(label, tab)

  const data = pageData.data
  const quoteText = context?.quote
    ? `${formatMoney(context.quote.price)} · ${formatChangePct(context.quote.changePct).text}`
    : MISSING
  const klineText = context?.kline
    ? `${context.kline.start ?? MISSING} → ${context.kline.end ?? MISSING}`
    : MISSING
  const earningsText = context?.earnings?.latestReportDate ?? MISSING
  const eventTypes =
    context?.events?.types
      .filter((item) => item.count > 0)
      .map((item) => `${item.label} ${item.count}`)
      .join(' · ') ?? ''

  return (
    <div className={className}>
      <Button
        variant="secondary"
        leadingIcon={<Sparkles className="size-3.5" aria-hidden="true" />}
        onClick={() => setOpen(true)}
        disabled={!code}
        disabledReason={code ? undefined : '先选择一只标的'}
        aria-haspopup="dialog"
      >
        研究助手
      </Button>

      <Drawer
        open={open}
        onClose={close}
        title={title}
        subtitle={scope ? `页级上下文 · ${scope.tab} · ${scope.date}` : '页级上下文'}
        width="lg"
        fullScreenNarrow
        initialFocusRef={inputRef}
        footer={
          <div className="flex w-full flex-col gap-1">
            <div className="flex items-end gap-2">
              <Textarea
                ref={inputRef}
                rows={2}
                maxLength={2000}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={onKeyDown}
                disabled={!ready}
                aria-label="向研究助手提问"
                placeholder={
                  ready ? `就 ${label} 这一页的内容问点什么…` : '正在准备页级上下文…'
                }
                className="min-w-0 flex-1 resize-none"
              />
              {pending ? (
                <Button
                  variant="secondary"
                  className="shrink-0"
                  onClick={stop}
                  leadingIcon={<Square className="size-3.5" aria-hidden="true" />}
                >
                  停止
                </Button>
              ) : (
                <Button
                  variant="primary"
                  className="shrink-0"
                  onClick={() => void ask(draft)}
                  disabled={!ready || draft.trim() === ''}
                  leadingIcon={<Send className="size-3.5" aria-hidden="true" />}
                >
                  发送
                </Button>
              )}
            </div>
            <p className="text-caption text-text-muted">
              Enter 发送 · Shift+Enter 换行 · 回答仅作页面数据解读，不改写行情，也不构成投资建议
            </p>
          </div>
        }
      >
        <div className="flex flex-col gap-4">
          <section
            aria-label="页级上下文摘要"
            className="rounded border border-border bg-surface-2 p-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Sparkles className="size-4 text-accent" aria-hidden="true" />
                <h3 className="text-body font-semibold text-text">本页上下文</h3>
                <Badge tone={context ? 'ok' : 'neutral'}>
                  {context ? '已绑定' : '未就绪'}
                </Badge>
              </div>
              <Button
                variant="ghost"
                className="h-7 px-2 text-caption"
                onClick={() => setShowContext((value) => !value)}
                disabled={!context}
              >
                {showContext ? '收起 JSON' : '查看上下文 JSON'}
              </Button>
            </div>

            {pageData.status === 'loading' ? (
              <p className="mt-2 text-caption text-text-muted" aria-busy="true">
                正在读取行情 / 财报 / 事件摘要…
              </p>
            ) : (
              <dl className="mt-2 grid grid-cols-1 gap-x-3 gap-y-2 sm:grid-cols-2">
                <ContextItem
                  label="行情"
                  value={quoteText}
                  note={displayTimestamp(context?.quote?.asOf)}
                />
                <ContextItem
                  label="K 线区间"
                  value={klineText}
                  note={
                    context?.kline
                      ? `${context.kline.bars} 根 · 收盘 ${formatMoney(context.kline.lowClose)}–${formatMoney(context.kline.highClose)}`
                      : null
                  }
                />
                <ContextItem
                  label="财报期次"
                  value={earningsText}
                  note={
                    context?.earnings
                      ? `${context.earnings.periods} 期 · 一致预期 ${
                          context.earnings.consensus?.analystCount ?? MISSING
                        } 家`
                      : null
                  }
                />
                <ContextItem
                  label="事件条数"
                  value={context?.events ? `${context.events.total} 条` : MISSING}
                  note={eventTypes || null}
                />
              </dl>
            )}

            {data && data.issues.length > 0 && (
              <ul className="mt-2 flex list-disc flex-col gap-half pl-4 text-caption text-warn">
                {data.issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            )}

            {showContext && context && (
              <pre className="mt-2 max-h-60 overflow-auto rounded-xs bg-sunken p-2 font-mono text-caption leading-body text-text-secondary">
                {serializedContext}
              </pre>
            )}

            <p className="mt-2 border-t border-border pt-2 text-caption leading-body text-text-muted">
              只发送页面上已展示的行情、K 线区间、财报期次与事件条数；论点正文、持仓与令牌不会进入上下文。
            </p>
          </section>

          <section aria-label="快捷提问" className="flex flex-col gap-1">
            <h3 className="text-caption text-text-muted">快捷提问</h3>
            {questions.map((question) => (
              <Button
                key={question}
                variant="secondary"
                className="h-auto min-h-control w-full justify-start whitespace-normal text-left"
                onClick={() => void ask(question)}
                disabled={!ready || pending}
              >
                {question}
              </Button>
            ))}
          </section>

          <section
            aria-label="对话"
            role="log"
            aria-live="polite"
            className="flex flex-col gap-3"
          >
            {messages.length === 0 && !pending && !failure && (
              <EmptyState
                compact
                icon={<Sparkles aria-hidden="true" />}
                title="从这一页问起"
                description="回答只依据上方页级上下文；页面没取到的行情 / 财报 / 事件，助手会直说，不会用一般知识补。"
              />
            )}

            {messages.map((message) => (
              <div
                key={message.id}
                className={cn(
                  'flex',
                  message.role === 'user' ? 'justify-end' : 'justify-start',
                )}
              >
                <div
                  className={cn(
                    'max-w-[88%] whitespace-pre-wrap rounded px-3 py-2 text-body leading-body',
                    message.role === 'user'
                      ? 'bg-accent-soft text-text'
                      : message.stopped
                        ? 'border border-border bg-surface text-text-muted'
                        : 'border border-border bg-surface-2 text-text',
                  )}
                >
                  {message.role === 'assistant' && (
                    <span className="mb-1 block text-caption text-text-muted">研究助手</span>
                  )}
                  {message.text}
                </div>
              </div>
            ))}

            {pending && (
              <div className="flex items-center gap-2 text-caption text-text-muted">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                研究助手正在回答…
              </div>
            )}

            {failure && (
              <ErrorBlock
                variant="inline"
                title={failure.title}
                detail={failure.detail}
                code={failure.code}
                onRetry={failure.retryable && failedQuestion ? retry : undefined}
              />
            )}
          </section>
        </div>
      </Drawer>
    </div>
  )
}
