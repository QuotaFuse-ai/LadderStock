/**
 * 对话会话客户端（QUOT-582 阶段 F，消费 QUOT-580 的 `/api/conversation`）。
 *
 * 四个接口，形状以 `ladderstock/web/assistant.py:build_conversation_router` 为准
 * （任务书 §5.2 的示例有出入，以代码为准）：
 *
 *   GET   /api/conversation/sessions                → { sessions: [{session_id,title,created_at,updated_at,turns}] }
 *   GET   /api/conversation/sessions/{id}/turns     → { session: {...}, turns: [{seq,question,blocks,status,created_at,updated_at}] }
 *   PATCH /api/conversation/sessions/{id}           { title } → { session_id, title }
 *   DELETE /api/conversation/sessions/{id}          → { session_id, deleted }（QUOT-602，幂等）
 *   POST  /api/conversation/turns                   { message, session_id?, context? } → 同步一次返回整轮
 *
 * 边界（红线）：
 * - **同步端点，没有 SSE**：不在这里自造流式协议，整轮 blocks 一次到手；
 * - 前端**只消费不重算**：`blocks` 原样交给 `BlockRenderer`，`reply` 原样兜底；
 * - 失败体 `{ ok: false, session_id, code, detail, retryable, failure }` 按 HTTP 502 /
 *   400 回来，抛 `ConversationError`（继承 `ApiError`，`kind` 仍走既有的四档）。
 */

import { ApiError } from '../api'

export const CONVERSATION_BASE = '/api/conversation'

/** 轮次状态（`data/chat_store.py` 四态）；前端只按它显示，不在 `notice.kind` 里造状态 */
export type TurnStatus = 'streaming' | 'done' | 'aborted' | 'failed'

export const TURN_STATUSES: readonly TurnStatus[] = ['streaming', 'done', 'aborted', 'failed']

export interface SessionSummary {
  session_id: string
  title: string
  created_at: string
  updated_at: string
  turns: number
}

export interface SessionInfo {
  session_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationTurn {
  seq: number
  question: string
  /** 后端原样块数组（未解析）；缺失 / 坏形状交给 `BlockRenderer` 兜底 */
  blocks: unknown
  status: TurnStatus
  created_at: string
  updated_at: string
}

export interface TurnContextSource {
  name: string
  fetched_at: string
  url?: string | null
  symbol?: string | null
  as_of?: string | null
}

/** 结构化页级上下文；**不带 `history`**（QUOT-579 落点 A 是给 `/api/research` 的） */
export interface TurnContext {
  page?: string | null
  data?: Record<string, unknown> | null
  sources?: TurnContextSource[]
}

export interface TurnRequest {
  message: string
  session_id?: string
  context?: TurnContext
}

export interface TurnSuccess {
  ok: true
  session_id: string
  reply: string
  blocks: unknown
  model: string
  channel: string
  sources: TurnContextSource[]
  turn_seq: number | null
  status: TurnStatus
  fetched_at: string
}

export interface TurnFailureBody {
  ok: false
  session_id: string
  code: string
  detail: string
  retryable: boolean
  failure: {
    kind?: string | null
    reason?: string | null
    message?: string | null
    action?: string | null
    retryable?: boolean
    detail?: string | null
  }
}

/** 失败：保留 HTTP 状态与结构化体，页面据此出「失败 + 可重试」 */
export class ConversationError extends ApiError {
  readonly body: TurnFailureBody | null

  constructor(
    kind: ApiError['kind'],
    message: string,
    status: number | null = null,
    detail: string | null = null,
    code: string | null = null,
    body: TurnFailureBody | null = null,
  ) {
    super(kind, message, status, detail, code)
    this.name = 'ConversationError'
    this.body = body
  }

  get retryable(): boolean {
    if (this.body) return Boolean(this.body.retryable ?? this.body.failure?.retryable)
    // 网络 / 超时类可重试；HTTP 4xx 校验失败（空消息、会话不存在）不重试
    return this.kind === 'timeout' || this.kind === 'not-connected' || this.status === null || this.status >= 500
  }
}

// ---------------------------------------------------------------- 形状校验

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function asStatus(value: unknown): TurnStatus {
  return typeof value === 'string' && (TURN_STATUSES as readonly string[]).includes(value)
    ? (value as TurnStatus)
    : 'done'
}

function parseFailureBody(raw: unknown): TurnFailureBody | null {
  const root = asRecord(raw)
  if (!root || root.ok !== false) return null
  const failure = asRecord(root.failure)
  return {
    ok: false,
    session_id: asString(root.session_id),
    code: asString(root.code),
    detail: asString(root.detail) || asString(failure?.message),
    retryable: Boolean(root.retryable ?? failure?.retryable),
    failure: {
      kind: asString(failure?.kind) || null,
      reason: asString(failure?.reason) || null,
      message: asString(failure?.message) || null,
      action: asString(failure?.action) || null,
      retryable: Boolean(failure?.retryable),
      detail: asString(failure?.detail) || null,
    },
  }
}

function parseSessionSummary(raw: unknown): SessionSummary | null {
  const root = asRecord(raw)
  const id = asString(root?.session_id)
  if (!root || !id) return null
  return {
    session_id: id,
    title: asString(root.title),
    created_at: asString(root.created_at),
    updated_at: asString(root.updated_at),
    turns: asNumber(root.turns),
  }
}

function parseSessions(raw: unknown): SessionSummary[] {
  const root = asRecord(raw)
  const list = root?.sessions
  if (!Array.isArray(list)) return []
  return list.map(parseSessionSummary).filter((item): item is SessionSummary => item !== null)
}

function parseTurn(raw: unknown): ConversationTurn | null {
  const root = asRecord(raw)
  if (!root) return null
  return {
    seq: asNumber(root.seq),
    question: asString(root.question),
    blocks: root.blocks ?? null,
    status: asStatus(root.status),
    created_at: asString(root.created_at),
    updated_at: asString(root.updated_at),
  }
}

// ---------------------------------------------------------------- 传输

const READ_TIMEOUT_MS = 8000
/**
 * 一轮问答是同步的（前端取消 = 中断请求、服务端按 `aborted` 定格），默认给 15s：
 * 短于它页面能自己收口，长于它服务端还在跑、页面却先报超时。
 */
const TURN_TIMEOUT_MS = 15000

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
}

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs = READ_TIMEOUT_MS } = options
  if (signal?.aborted) throw new ConversationError('timeout', `请求已取消：${path}`)

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      // 写方法带同源证据；`_guard` 对 POST / PATCH 校验 Origin / Sec-Fetch-Site。
      // 不用 `X-LDST-Client`：浏览器直连同源时它反而是多余的旁路。
      headers: {
        accept: 'application/json',
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      payload = null
    }
    if (!response.ok) {
      const failure = parseFailureBody(payload)
      const detail =
        failure?.detail ||
        asString(asRecord(payload)?.detail) ||
        `HTTP ${response.status}`
      throw new ConversationError(
        'http',
        `对话接口 ${path} 返回 HTTP ${response.status}：${detail}`,
        response.status,
        detail,
        failure?.code ?? asString(asRecord(payload)?.code) ?? null,
        failure,
      )
    }
    if (payload === null) {
      throw new ConversationError('parse', `对话接口 ${path} 返回的内容不是合法 JSON`)
    }
    return payload as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ConversationError('timeout', `请求已取消：${path}`)
      throw new ConversationError('timeout', `对话接口 ${path} 超时（${timeoutMs}ms）`)
    }
    throw new ConversationError('not-connected', `对话接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

function sessionPath(sessionId: string): string {
  return `${CONVERSATION_BASE}/sessions/${encodeURIComponent(sessionId)}`
}

// ---------------------------------------------------------------- 出口

export async function listSessions(signal?: AbortSignal): Promise<SessionSummary[]> {
  return parseSessions(await requestJson<unknown>(`${CONVERSATION_BASE}/sessions`, { signal }))
}

export interface SessionTurns {
  session: SessionInfo
  turns: ConversationTurn[]
}

export async function listTurns(sessionId: string, signal?: AbortSignal): Promise<SessionTurns> {
  const raw = await requestJson<unknown>(`${sessionPath(sessionId)}/turns`, { signal })
  const root = asRecord(raw)
  const session = asRecord(root?.session)
  const turnsRaw = root?.turns
  const turns = Array.isArray(turnsRaw)
    ? turnsRaw.map(parseTurn).filter((item): item is ConversationTurn => item !== null)
    : []
  return {
    session: {
      session_id: asString(session?.session_id) || sessionId,
      title: asString(session?.title),
      created_at: asString(session?.created_at),
      updated_at: asString(session?.updated_at),
    },
    turns,
  }
}

export interface RenameResult {
  session_id: string
  title: string
}

/** 改名：标题由服务端折叠空白并截到 40 码点，**前端不做同一份截断**（单一口径） */
export async function renameSession(
  sessionId: string,
  title: string,
  signal?: AbortSignal,
): Promise<RenameResult> {
  const raw = await requestJson<unknown>(sessionPath(sessionId), {
    method: 'PATCH',
    body: { title },
    signal,
  })
  const root = asRecord(raw)
  return {
    session_id: asString(root?.session_id) || sessionId,
    title: asString(root?.title),
  }
}

export interface DeleteResult {
  session_id: string
  /** 服务端真删到了 `true`；本来就没有（含重复删）是 `false`，同样算成功 */
  deleted: boolean
}

/** 删除一条会话（服务端幂等）：`deleted: false` 不是失败，刷新列表即可 */
export async function deleteSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<DeleteResult> {
  const raw = await requestJson<unknown>(sessionPath(sessionId), { method: 'DELETE', signal })
  const root = asRecord(raw)
  return {
    session_id: asString(root?.session_id) || sessionId,
    deleted: root?.deleted === true,
  }
}

/** 成功路径；失败抛 `ConversationError`（`status` / `body.code` / `retryable` 可用） */
export async function sendTurn(
  request: TurnRequest,
  signal?: AbortSignal,
): Promise<TurnSuccess> {
  const raw = await requestJson<unknown>(`${CONVERSATION_BASE}/turns`, {
    method: 'POST',
    body: request,
    signal,
    timeoutMs: TURN_TIMEOUT_MS,
  })
  const root = asRecord(raw)
  if (!root) throw new ConversationError('parse', '对话接口 /turns 返回了空内容')
  if (root.ok === false) {
    const failure = parseFailureBody(root)
    throw new ConversationError(
      'http',
      failure?.detail || '这一轮没有答出来',
      null,
      failure?.detail ?? null,
      failure?.code ?? null,
      failure,
    )
  }
  const sessionId = asString(root.session_id)
  if (!sessionId) throw new ConversationError('parse', '对话接口 /turns 缺少 session_id')
  return {
    ok: true,
    session_id: sessionId,
    reply: asString(root.reply),
    blocks: root.blocks ?? null,
    model: asString(root.model),
    channel: asString(root.channel),
    sources: Array.isArray(root.sources)
      ? (root.sources as TurnContextSource[])
      : [],
    turn_seq: typeof root.turn_seq === 'number' ? root.turn_seq : null,
    status: asStatus(root.status),
    fetched_at: asString(root.fetched_at),
  }
}

export const httpConversation = {
  listSessions,
  listTurns,
  renameSession,
  deleteSession,
  sendTurn,
}

export type ConversationClient = typeof httpConversation
