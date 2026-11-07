import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { Link } from 'react-router-dom'
import { Loader2, Send, Sparkles, Square, Trash2 } from 'lucide-react'

import { askHomeAssistant } from '../../api/research'
import { cn } from '../../lib/cn'
import { Button, ErrorBlock, Textarea } from '../../components/ui'
import {
  describeAssistantError,
  type AssistantFailureInfo,
} from '../../components/research/sections/aiContext'
import {
  serializeHomeAssistantContext,
  type HomeAssistantBundle,
  type HomeContextSource,
} from './homeAssistant'

/**
 * 首页 B 块的 AI 研究对话（QUOT-472）。
 *
 * 通道与降级都是 QUOT-454 那一套：`POST /api/research/{scope}/assistant`、
 * `describeAssistantError` 的四类说法（未连接 / 未配置 / 超时 / 上游失败）、中止与重试。
 * 本组件只多做一件事：把「这一轮实际发出去的数据」渲染成回答下面的「本轮来源」。
 *
 * 三条红线：
 * - 回答只是文本，**不把模型输出的数字写回页面任何数据位**；
 * - 来源清单来自发送那一刻的上下文快照，一条都没有就直说「本轮未取数」，不补默认值；
 * - 不承诺联网搜索 / 后台研究任务——通道没有这两项能力，文案就不写。
 */

export interface HomeResearchChatProps {
  assistant: HomeAssistantBundle
  date: string
  className?: string
}

interface ChatTurn {
  id: number
  role: 'user' | 'assistant'
  text: string
  /** 仅 assistant：这一轮实际发给模型的数据来源快照 */
  sources?: HomeContextSource[]
  stopped?: boolean
}

const SUGGESTIONS = [
  '今天大盘和自选有哪些值得注意的变化？',
  '帮我看一家公司的估值和风险，该先问哪几个问题？',
  '产业链最近有哪些新变化值得跟踪？',
  '核对一份研报的结论，该先查哪些证据？',
]

function SourceList({ sources }: { sources: HomeContextSource[] }) {
  if (sources.length === 0) {
    return (
      <p className="text-caption text-text-muted">
        本轮未取数：首页这一块还没取到可用的行情，回答只基于你的问题。
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-half">
      {sources.map((source) => (
        <li key={source.id} className="flex flex-wrap items-baseline gap-x-2 text-caption">
          <span className="text-text-secondary">{source.label}</span>
          {source.count !== null && <span className="ldst-num text-text-muted">{source.count} 项</span>}
          <span className="ldst-num text-text-muted">{source.detail}</span>
          {source.href ? (
            <Link
              to={source.href}
              className="text-accent underline-offset-2 hover:underline focus-visible:underline"
            >
              查看
            </Link>
          ) : (
            <span className="text-text-muted">{source.location}</span>
          )}
        </li>
      ))}
    </ul>
  )
}

export function HomeResearchChat({ assistant, date, className }: HomeResearchChatProps) {
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<AssistantFailureInfo | null>(null)
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const seqRef = useRef(0)

  const serializedContext = useMemo(
    () => serializeHomeAssistantContext(assistant.context),
    [assistant.context],
  )

  useEffect(() => () => abortRef.current?.abort(), [])

  // 新回答到达后把对话滚到底；用户回头翻旧对话时不打扰（只在条数变化时触发）
  useEffect(() => {
    const node = logRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [turns.length, pending])

  const ask = useCallback(
    async (raw: string, options: { echo?: boolean } = {}) => {
      const question = raw.trim()
      if (!question || pending) return
      const echo = options.echo ?? true

      const seq = seqRef.current + 1
      seqRef.current = seq
      const controller = new AbortController()
      abortRef.current = controller
      // 快照必须在 await 之前取：取回来时页面可能已经刷新过数据
      const sourcesAtSend = assistant.sources

      if (echo) {
        setTurns((prev) => [...prev, { id: seq * 2 - 1, role: 'user', text: question }])
        setDraft('')
      }
      setFailure(null)
      setFailedQuestion(null)
      setPending(true)

      try {
        const reply = await askHomeAssistant(
          { question, context: serializedContext, date },
          controller.signal,
        )
        if (seqRef.current !== seq) return
        setTurns((prev) => [
          ...prev,
          { id: seq * 2, role: 'assistant', text: reply.reply, sources: sourcesAtSend },
        ])
      } catch (error) {
        if (seqRef.current !== seq) return
        if (controller.signal.aborted) {
          setTurns((prev) => [
            ...prev,
            { id: seq * 2, role: 'assistant', text: '已停止本次回答。', stopped: true },
          ])
        } else {
          setFailure(describeAssistantError(error))
          setFailedQuestion(question)
        }
      } finally {
        if (seqRef.current === seq) {
          abortRef.current = null
          setPending(false)
        }
      }
    },
    [assistant.sources, date, pending, serializedContext],
  )

  const retry = useCallback(() => {
    if (failedQuestion) void ask(failedQuestion, { echo: false })
  }, [ask, failedQuestion])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const clear = useCallback(() => {
    seqRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setTurns([])
    setFailure(null)
    setFailedQuestion(null)
    setPending(false)
    inputRef.current?.focus()
  }, [])

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法候选期间的回车归输入法，不能当成发送
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void ask(draft)
  }

  const pickSuggestion = (text: string) => {
    setDraft(text)
    inputRef.current?.focus()
  }

  const hasTurns = turns.length > 0

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-half">
          <div className="flex items-center gap-2">
            <Sparkles className="size-4 shrink-0 text-accent" aria-hidden="true" />
            <h2 className="text-subtitle font-semibold text-text">今天，想研究什么？</h2>
          </div>
          <p className="text-caption text-text-secondary">
            直接说要研究什么。回答只依据首页已取到的数据，并注明用了哪些、什么时候取的。
          </p>
        </div>
        {hasTurns && (
          <Button
            variant="ghost"
            className="h-7 px-2 text-caption"
            onClick={clear}
            leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
          >
            清空
          </Button>
        )}
      </div>

      <div
        ref={logRef}
        role="log"
        aria-live="polite"
        aria-label="研究对话"
        className="flex max-h-96 flex-col gap-3 overflow-y-auto"
      >
        {!hasTurns && !pending && !failure && (
          <div className="flex flex-col gap-2">
            <p className="text-caption text-text-muted">
              四条起步问题，点一下填进输入框：
            </p>
            <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
              {SUGGESTIONS.map((suggestion) => (
                <Button
                  key={suggestion}
                  variant="secondary"
                  className="h-auto min-h-control justify-start whitespace-normal text-left text-caption"
                  onClick={() => pickSuggestion(suggestion)}
                >
                  {suggestion}
                </Button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn) => (
          <div
            key={turn.id}
            className={cn('flex', turn.role === 'user' ? 'justify-end pl-8' : 'justify-start pr-8')}
          >
            <div
              className={cn(
                'max-w-full whitespace-pre-wrap rounded px-3 py-2 text-body leading-body',
                turn.role === 'user'
                  ? 'bg-accent-soft text-text'
                  : turn.stopped
                    ? 'border border-border bg-surface text-text-muted'
                    : 'border border-border bg-surface-2 text-text',
              )}
            >
              {turn.role === 'assistant' && (
                <span className="mb-1 block text-caption text-text-muted">研究助手</span>
              )}
              {turn.text}
              {turn.role === 'assistant' && !turn.stopped && (
                <div className="mt-2 border-t border-border pt-2">
                  <p className="mb-1 text-caption text-text-muted">本轮来源</p>
                  <SourceList sources={turn.sources ?? []} />
                </div>
              )}
            </div>
          </div>
        ))}

        {pending && (
          <div className="flex items-center gap-2 text-caption text-text-muted" aria-busy="true">
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
      </div>

      <div className="flex flex-col gap-1">
        <div className="flex items-end gap-2">
          <Textarea
            ref={inputRef}
            rows={2}
            maxLength={2000}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            aria-label="向研究助手提问"
            placeholder="说说要研究什么…（Shift+Enter 换行）"
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
              variant="secondary"
              className="shrink-0"
              onClick={() => void ask(draft)}
              disabled={draft.trim() === ''}
              leadingIcon={<Send className="size-3.5" aria-hidden="true" />}
            >
              发送
            </Button>
          )}
        </div>
        <p className="text-caption text-text-muted">
          Enter 发送 · Shift+Enter 换行 · 回答只是解读，行情数字以页面为准，不构成投资建议
        </p>
      </div>
    </div>
  )
}
