import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { Check, Copy, Loader2, Send, Sparkles, Square } from 'lucide-react'

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
import { useAssistantShell } from '../../../lib/assistant/shell'
import {
  buildDockView,
  buildLegacyDockView,
  describeObject,
  type AssistantObject,
  type DockView,
} from '../../../lib/skills/dock'
import {
  Badge,
  Banner,
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
  type AssistantFailureInfo,
  type AssistantPageData,
} from './aiContext'

/**
 * 「问 AI」抽屉（QUOT-454 建，QUOT-475 收成全站唯一实现）。
 *
 * **只有一个抽屉组件**：`Layout` 挂一份，顶栏按钮、移动端抽屉与 `/research/:code`
 * 页内的「研究助手」按钮都打开它。`ResearchAiDock` 只保留触发按钮；
 * 没有宿主时（组件单测、独立挂载）才自己托管一份同样的抽屉。
 *
 * 打开时的输入框**已经预填整段开场**（角色 + 分步说明 + 第 1 步指令，来自
 * `content/skills/<pkg>/steps.json` 的 `dock` 模板），不自动发送；旁边留「复制」后门。
 *
 * 对话通道目前只有 `/api/research/{code}/assistant`：没有代码的页面进入「待接入」态，
 * 只做开场编辑与复制，**不伪造回答、不自己拼后端接口**。
 */

export interface ResearchAiDockProps {
  code: string | null
  name: string | null
  scope?: { tab: string; date: string } | null
  /** 概览 tab 已取到的数据；传入可避免为上下文重复取一次 */
  overview?: ResearchOverview | null
  className?: string
  /** QUOT-475：所在路由。传了才按 `pageBindings` 绑定页面技能（旧调用方不传即沿用旧标题） */
  pageKey?: string
  /** 显式覆盖绑定包（QUOT-474 的步骤级入口用）；缺省按 `pageKey` 推断 */
  boundSkill?: string | null
  /** 页面已经拼好的开场（步骤级「问 AI」）；缺省按包模板渲染 */
  initialDraft?: string
  /** 覆盖包级未接通说明 */
  offlineNotice?: string
}

export interface AssistantDockDrawerProps {
  view: DockView | null
  overview?: ResearchOverview | null
  onClose: () => void
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

type CopyState = 'idle' | 'done' | 'failed'

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

export function AssistantDockDrawer({
  view,
  overview = null,
  onClose,
}: AssistantDockDrawerProps) {
  const open = view !== null
  const code = view?.channel?.code ?? null
  const tab = view?.object.tab ?? 'overview'
  const date = view?.object.date ?? ''
  const name = view?.object.name ?? null
  const pageKey = view?.pageKey ?? ''

  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<AssistantFailureInfo | null>(null)
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)
  const [showContext, setShowContext] = useState(false)
  const [copyState, setCopyState] = useState<CopyState>('idle')

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const requestSeqRef = useRef(0)
  const messageIdRef = useRef(0)
  const sessionRef = useRef<string | null>(null)

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

  // 发给后端的就是这份 JSON：页级摘要 + 三个上下文键（pageKey / boundSkill / object）
  const serializedContext = useMemo(() => {
    if (!context || !view) return ''
    return JSON.stringify(
      {
        ...context,
        pageKey: view.pageKey,
        boundSkill: view.boundSkill,
        object: view.object,
      },
      null,
      2,
    )
  }, [context, view])

  // 换页 / 换标的时旧对话不再属于当前上下文；同一页反复开关则保留草稿与对话
  const sessionKey = view ? `${view.pageKey}|${view.channel?.code ?? ''}` : null
  useEffect(() => {
    if (!sessionKey || sessionRef.current === sessionKey) return
    sessionRef.current = sessionKey
    abortRef.current?.abort()
    abortRef.current = null
    requestSeqRef.current += 1
    setMessages([])
    setFailure(null)
    setFailedQuestion(null)
    setPending(false)
    setShowContext(false)
    setCopyState('idle')
    setDraft(view?.draft ?? '')
    // 焦点落在输入框末尾会把预填的整段开场滚走，首屏只剩第 1 步；开在草稿开头
    if (view?.draft) {
      window.setTimeout(() => {
        const element = inputRef.current
        if (!element) return
        element.setSelectionRange(0, 0)
        element.scrollTop = 0
      }, 60)
    }
  }, [sessionKey, view?.draft])

  useEffect(() => () => abortRef.current?.abort(), [])

  const close = useCallback(() => {
    // 关闭即放弃这一轮：不把「已停止」写进下次打开时的对话
    requestSeqRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setPending(false)
    onClose()
  }, [onClose])

  const ask = useCallback(
    async (raw: string, options: { echo?: boolean } = {}) => {
      const question = raw.trim()
      if (!question || !code || pending || !context || !serializedContext) return
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
            context: serializedContext,
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
    [code, pending, context, serializedContext, date, tab],
  )

  const retry = useCallback(() => {
    if (failedQuestion) void ask(failedQuestion, { echo: false })
  }, [ask, failedQuestion])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const copyDraft = useCallback(async () => {
    if (!draft.trim()) return
    try {
      if (!navigator.clipboard) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(draft)
      setCopyState('done')
    } catch {
      setCopyState('failed')
    }
    window.setTimeout(() => setCopyState('idle'), 1800)
  }, [draft])

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法候选期间的回车归输入法，不能当成发送
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void ask(draft)
  }

  const ready = Boolean(code) && context !== null && Boolean(view && !view.offlineBlocking)
  const questions = view?.quickQuestions ?? []
  const canCopy = Boolean(draft.trim()) && (view?.fallbackCopy ?? true)

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
    <Drawer
      open={open}
      onClose={close}
      title={view?.title ?? ''}
      subtitle={view?.subtitle}
      width="lg"
      fullScreenNarrow
      initialFocusRef={inputRef}
      footer={
        <div className="flex w-full flex-col gap-1">
          <div className="flex items-end gap-2">
            <Textarea
              ref={inputRef}
              rows={view?.draft ? 5 : 2}
              maxLength={4000}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              disabled={false}
              aria-label="向研究助手提问"
              placeholder={
                ready
                  ? `就 ${name ?? code ?? view?.pageLabel ?? '这一页'} 的内容问点什么…`
                  : '开场已备好：可以改，也可以先「复制」交给任意 Agent'
              }
              className="min-w-0 flex-1 resize-none"
            />
            <div className="flex shrink-0 flex-col gap-1">
              {pending ? (
                <Button
                  variant="secondary"
                  onClick={stop}
                  leadingIcon={<Square className="size-3.5" aria-hidden="true" />}
                >
                  停止
                </Button>
              ) : (
                <Button
                  variant="primary"
                  onClick={() => void ask(draft)}
                  disabled={!ready || draft.trim() === ''}
                  leadingIcon={<Send className="size-3.5" aria-hidden="true" />}
                >
                  发送
                </Button>
              )}
              {canCopy && (
                <Button
                  variant="ghost"
                  className="px-2 text-caption"
                  onClick={() => void copyDraft()}
                  leadingIcon={
                    copyState === 'done' ? (
                      <Check className="size-3.5" aria-hidden="true" />
                    ) : (
                      <Copy className="size-3.5" aria-hidden="true" />
                    )
                  }
                  aria-label="复制开场"
                >
                  {copyState === 'done' ? '已复制' : copyState === 'failed' ? '复制失败' : '复制'}
                </Button>
              )}
            </div>
          </div>
          <p className="text-caption text-text-muted">
            {ready
              ? 'Enter 发送 · Shift+Enter 换行 · 回答仅作页面数据解读，不改写行情，也不构成投资建议'
              : 'Enter 发送（通道待接入时不可用）· Shift+Enter 换行 · 不自动发送，看过再发'}
          </p>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {view?.offlineNotice && (
          <Banner
            tone={view.status === 'unconnected' ? 'danger' : 'warn'}
            title={
              view.status === 'unconnected'
                ? `${view.pkgLabel ?? '本页技能'}未接通`
                : `${view.pkgLabel ?? '本页技能'}部分段落未接通`
            }
          >
            {view.offlineNotice}
          </Banner>
        )}

        {view && !view.channel && (
          <Banner tone="info" title="对话通道待接入">
            当前只有「个股深挖」页接好了助手后端。这一页可以先把开场改好，再用「复制」交给任意
            Agent；通道接通后同一个按钮直接生效。
          </Banner>
        )}

        {view && view.missingPlaceholders.length > 0 && (
          <Banner tone="warn" title="开场里还有没填的对象">
            {view.missingPlaceholders.map((key) => `{${key}}`).join(' ')}
            ：当前页取不到，先别编一个名字填进去——把它换成你要看的标的，或直接告诉我代码。
          </Banner>
        )}

        {code ? (
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

            {showContext && serializedContext && (
              <pre className="mt-2 max-h-60 overflow-auto rounded-xs bg-sunken p-2 font-mono text-caption leading-body text-text-secondary">
                {serializedContext}
              </pre>
            )}

            <p className="mt-2 border-t border-border pt-2 text-caption leading-body text-text-muted">
              只发送页面上已展示的行情、K 线区间、财报期次与事件条数；论点正文、持仓与令牌不会进入上下文。
            </p>
          </section>
        ) : (
          <section
            aria-label="本页上下文"
            className="rounded border border-border bg-surface-2 p-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Sparkles className="size-4 text-accent" aria-hidden="true" />
                <h3 className="text-body font-semibold text-text">本页上下文</h3>
                <Badge tone="ok">已绑定</Badge>
              </div>
              <Button
                variant="ghost"
                className="h-7 px-2 text-caption"
                onClick={() => setShowContext((value) => !value)}
              >
                {showContext ? '收起 JSON' : '查看上下文 JSON'}
              </Button>
            </div>

            <dl className="mt-2 grid grid-cols-1 gap-x-3 gap-y-2 sm:grid-cols-2">
              <ContextItem label="页面" value={view?.pageLabel ?? MISSING} note={pageKey || null} />
              <ContextItem
                label="绑定技能"
                value={view?.pkgLabel ?? '开放域'}
                note={view?.boundSkill ?? null}
              />
              <ContextItem
                label="对象"
                value={describeObject(view?.object ?? {}) ?? MISSING}
                note={view?.missingPlaceholders.length ? '开场里还留着占位' : null}
              />
            </dl>

            {showContext && (
              <pre className="mt-2 max-h-60 overflow-auto rounded-xs bg-sunken p-2 font-mono text-caption leading-body text-text-secondary">
                {JSON.stringify({ pageKey, boundSkill: view?.boundSkill ?? null, object: view?.object ?? {} }, null, 2)}
              </pre>
            )}

            <p className="mt-2 border-t border-border pt-2 text-caption leading-body text-text-muted">
              发送时带上 pageKey / boundSkill / object 三个上下文；不读页面正文、持仓与令牌。
            </p>
          </section>
        )}

        <section aria-label="快捷提问" className="flex flex-col gap-1">
          <h3 className="text-caption text-text-muted">
            {questions.some((question) => !question.send) ? '本页研究起手式 / 快捷提问' : '快捷提问'}
          </h3>
          {questions.map((question) => (
            <Button
              key={question.id}
              variant="secondary"
              className="h-auto min-h-control w-full justify-start whitespace-normal text-left"
              onClick={() => {
                if (question.send) void ask(question.text)
                else setDraft(question.text)
              }}
              disabled={question.send && (!ready || pending)}
            >
              {question.send ? question.label : `${question.label}（点一下填入输入框）`}
            </Button>
          ))}
        </section>

        {code ? (
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
        ) : (
          <p className="rounded border border-dashed border-border px-3 py-2 text-caption leading-body text-text-muted">
            这一页还没有对话通道：输入框里的开场可以直接改，按「复制」后贴给任意 Agent 就能开工。
          </p>
        )}
      </div>
    </Drawer>
  )
}

function dockForTrigger(props: ResearchAiDockProps): DockView {
  const { code, name, scope, pageKey, boundSkill, initialDraft, offlineNotice } = props
  const object: AssistantObject = {
    code,
    name,
    date: scope?.date ?? null,
    tab: scope?.tab ?? null,
  }

  if (pageKey) {
    return buildDockView({
      pathname: pageKey,
      object,
      boundSkill,
      initialDraft,
      offlineNotice: offlineNotice ?? null,
    })
  }

  return buildLegacyDockView({
    code,
    name,
    tab: scope?.tab ?? 'overview',
    date: scope?.date ?? '',
    offlineNotice: offlineNotice ?? null,
  })
}

/**
 * 页内「研究助手」触发按钮。有宿主（`Layout`）时打开全站唯一的那个抽屉，
 * 没有宿主时（组件测试、独立挂载）自己托管同一份抽屉。
 */
export function ResearchAiDock(props: ResearchAiDockProps) {
  const { code, overview, className } = props
  const shell = useAssistantShell()
  const [localView, setLocalView] = useState<DockView | null>(null)

  const open = () => {
    const view = dockForTrigger(props)
    if (shell) {
      shell.open({
        pageKey: view.pageKey,
        object: view.object,
        boundSkill: props.boundSkill,
        initialDraft: props.initialDraft,
        offlineNotice: props.offlineNotice ?? null,
        overview: overview ?? null,
      })
      return
    }
    setLocalView(view)
  }

  return (
    <>
      <div className={className}>
        <Button
          variant="secondary"
          leadingIcon={<Sparkles className="size-3.5" aria-hidden="true" />}
          onClick={open}
          disabled={!code}
          disabledReason={code ? undefined : '先选择一只标的'}
          aria-haspopup="dialog"
        >
          研究助手
        </Button>
      </div>
      {!shell && (
        <AssistantDockDrawer
          view={localView}
          overview={overview ?? null}
          onClose={() => setLocalView(null)}
        />
      )}
    </>
  )
}

export default ResearchAiDock
