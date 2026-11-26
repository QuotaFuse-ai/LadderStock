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
  /** F2：用户在计划卡上确认/改过的步骤标题；不传 = 不走计划 */
  plan?: string[]
}

/** F2 计划草案里的一个步骤 */
export interface PlanStep {
  id: string
  title: string
  kind: string
}

export interface PlanDraft {
  ok: boolean
  symbol: string | null
  max_steps: number
  steps: PlanStep[]
}

/** 取计划草案：纯规则，服务端不调模型，通常几十毫秒返回 */
export async function fetchPlan(
  request: { message: string; context?: TurnContext },
  signal?: AbortSignal,
): Promise<PlanDraft> {
  const raw = await requestJson<unknown>(`${CONVERSATION_BASE}/plan`, {
    method: 'POST',
    body: { message: request.message, context: request.context },
    signal,
    timeoutMs: PLAN_TIMEOUT_MS,
  })
  const root = asRecord(raw) ?? {}
  const steps: PlanStep[] = []
  if (Array.isArray(root.steps)) {
    for (const item of root.steps) {
      const entry = asRecord(item)
      if (!entry) continue
      const title = asString(entry.title).trim()
      if (!title) continue
      steps.push({
        id: asString(entry.id) || `s${steps.length + 1}`,
        title,
        kind: asString(entry.kind),
      })
    }
  }
  return {
    ok: root.ok !== false,
    symbol: asString(root.symbol) || null,
    max_steps: typeof root.max_steps === 'number' ? root.max_steps : 6,
    steps,
  }
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
/** 计划草案只跑规则，给 8s 足够；超时就按"没有计划"处理，别卡住提问 */
const PLAN_TIMEOUT_MS = 8000

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

/** 流式等待态的流水账条目（服务端 `status` 帧） */
export interface TurnStreamStatus {
  action: string
  item: string
  state: string
  item_id?: string
  elapsed_ms?: number
}

export interface TurnStreamHandlers {
  /** 每一帧 `status`：给等待态流水账用，不影响正文 */
  onStatus?: (status: TurnStreamStatus) => void
  /** 每一帧 `text`：**累积全文**（整段替换，不要拼接） */
  onText?: (text: string) => void
}

/** 流式总预算：单轮模型 60s + 装配余量；比一次性的 15s 宽得多 */
const STREAM_TIMEOUT_MS = 180000

/** 解一帧 SSE：`event:` 行 + 若干 `data:` 行（JSON 可能跨行） */
function parseSseFrame(chunk: string): { event: string; data: unknown } | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of chunk.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) as unknown }
  } catch {
    return null
  }
}

/**
 * 流式送一轮：同一端点，靠 `Accept: text/event-stream` 分流。
 *
 * 服务端帧序：`status(history done)` → `status(model running)` → `text × n`
 * → `status(model done)` → `done`。`done` 帧与 JSON 路径同形，因此最终返回的
 * `TurnSuccess` 与 `sendTurn()` 完全一致——调用方两条路可以共用同一段收尾逻辑。
 *
 * 服务端若降级（通道没有 token 增量）只会推一帧完整 `text`，本函数不区分，
 * 由 UI 按到达节奏如实展示。
 */
export async function sendTurnStream(
  request: TurnRequest,
  handlers: TurnStreamHandlers = {},
  signal?: AbortSignal,
): Promise<TurnSuccess> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS)
  const relayAbort = () => controller.abort()
  signal?.addEventListener('abort', relayAbort)
  try {
    const response = await fetch(`${CONVERSATION_BASE}/turns`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'text/event-stream',
      },
      body: JSON.stringify(request),
      signal: controller.signal,
    })

    if (!response.ok || !response.body) {
      // 校验类失败（空消息 / 超长 / session_id 非法）仍是普通 JSON，走同一套错误口径
      let raw: unknown = null
      try {
        raw = await response.json()
      } catch {
        raw = null
      }
      const failure = parseFailureBody(asRecord(raw))
      throw new ConversationError(
        'http',
        failure?.detail || '这一轮没有答出来',
        response.status,
        failure?.detail ?? null,
        failure?.code ?? null,
        failure,
      )
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let done: TurnSuccess | null = null

    while (true) {
      const { done: finished, value } = await reader.read()
      if (finished) break
      buffer += decoder.decode(value, { stream: true })
      // 帧以空行分隔；最后一段可能不完整，留在 buffer 里等下一块
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const raw = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')
        const frame = parseSseFrame(raw)
        if (!frame) continue
        const payload = asRecord(frame.data)
        if (frame.event === 'status') {
          handlers.onStatus?.({
            action: asString(payload?.action),
            item: asString(payload?.item),
            state: asString(payload?.state),
            item_id: asString(payload?.item_id) || undefined,
            elapsed_ms:
              typeof payload?.elapsed_ms === 'number' ? payload.elapsed_ms : undefined,
          })
        } else if (frame.event === 'text') {
          handlers.onText?.(asString(payload?.text))
        } else if (frame.event === 'done') {
          if (payload?.ok === false) {
            const failure = parseFailureBody(payload)
            throw new ConversationError(
              'http',
              failure?.detail || '这一轮没有答出来',
              null,
              failure?.detail ?? null,
              failure?.code ?? null,
              failure,
            )
          }
          const sessionId = asString(payload?.session_id)
          if (!sessionId) throw new ConversationError('parse', '对话接口 /turns 缺少 session_id')
          done = {
            ok: true,
            session_id: sessionId,
            reply: asString(payload?.reply),
            blocks: (payload?.blocks as TurnSuccess['blocks']) ?? null,
            model: asString(payload?.model),
            channel: asString(payload?.channel),
            sources: Array.isArray(payload?.sources)
              ? (payload?.sources as TurnContextSource[])
              : [],
            turn_seq: typeof payload?.turn_seq === 'number' ? payload.turn_seq : null,
            status: asStatus(payload?.status),
            fetched_at: asString(payload?.fetched_at),
          }
        }
      }
    }

    if (!done) throw new ConversationError('parse', '对话接口 /turns 的流没有给出 done 帧')
    return done
  } catch (err) {
    if (err instanceof ConversationError) throw err
    if (controller.signal.aborted && !signal?.aborted) {
      throw new ConversationError('timeout', `对话接口 /turns 超时（${STREAM_TIMEOUT_MS}ms）`)
    }
    throw err
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', relayAbort)
  }
}

// ------------------------------------------------------------ F9 观点账本

/** 一条已落账的判断（`data/chat_store.py: research_record` 一行的 JSON 形态） */
export interface LedgerRecord {
  record_id: string
  session_id: string | null
  seq: number | null
  symbol: string | null
  question: string
  direction: string
  horizon_days: number
  base_price: number | null
  base_date: string | null
  falsifier: string | null
  due_date: string | null
  status: string
  outcome: {
    reason?: string
    pct_change?: number | null
    flip_date?: string | null
  } | null
  created_at: string
  updated_at: string
}

/** 账本汇总：`hit_rate` 只算已判的（hit / miss / flipped），`open` 不进分母 */
export interface LedgerStats {
  total: number
  open: number
  hit: number
  miss: number
  flipped: number
  undecided: number
  judged: number
  hit_rate: number | null
}

export interface LedgerPayload {
  stats: LedgerStats
  records: LedgerRecord[]
}

export interface RecallResult {
  ok: boolean
  as_of?: string
  due?: number
  hit?: number
  miss?: number
  flipped?: number
  undecided?: number
  waiting?: number
  skipped?: number
  error?: string
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parseStats(raw: unknown): LedgerStats {
  const root = asRecord(raw) ?? {}
  return {
    total: asNumber(root.total),
    open: asNumber(root.open),
    hit: asNumber(root.hit),
    miss: asNumber(root.miss),
    flipped: asNumber(root.flipped),
    undecided: asNumber(root.undecided),
    judged: asNumber(root.judged),
    hit_rate: asNullableNumber(root.hit_rate),
  }
}

function parseRecord(raw: unknown): LedgerRecord | null {
  const root = asRecord(raw)
  if (!root) return null
  const recordId = asString(root.record_id)
  if (!recordId) return null
  const outcome = asRecord(root.outcome)
  return {
    record_id: recordId,
    session_id: asString(root.session_id) || null,
    seq: typeof root.seq === 'number' ? root.seq : null,
    symbol: asString(root.symbol) || null,
    question: asString(root.question),
    direction: asString(root.direction),
    horizon_days: asNumber(root.horizon_days),
    base_price: asNullableNumber(root.base_price),
    base_date: asString(root.base_date) || null,
    falsifier: asString(root.falsifier) || null,
    due_date: asString(root.due_date) || null,
    status: asString(root.status) || 'open',
    outcome: outcome
      ? {
          reason: asString(outcome.reason) || undefined,
          pct_change: asNullableNumber(outcome.pct_change),
          flip_date: asString(outcome.flip_date) || null,
        }
      : null,
    created_at: asString(root.created_at),
    updated_at: asString(root.updated_at),
  }
}

/** 读账本：`GET /api/conversation/ledger?limit=&symbol=&status=`（只读，库不存在时返回空） */
export async function fetchLedger(
  query: { limit?: number; symbol?: string; status?: string } = {},
  signal?: AbortSignal,
): Promise<LedgerPayload> {
  const params = new URLSearchParams()
  if (query.limit) params.set('limit', String(query.limit))
  if (query.symbol) params.set('symbol', query.symbol)
  if (query.status) params.set('status', query.status)
  const search = params.toString()
  const raw = await requestJson<unknown>(`${CONVERSATION_BASE}/ledger${search ? `?${search}` : ''}`, {
    signal,
    timeoutMs: READ_TIMEOUT_MS,
  })
  const root = asRecord(raw) ?? {}
  const records = Array.isArray(root.records)
    ? root.records.map(parseRecord).filter((item): item is LedgerRecord => item !== null)
    : []
  return { stats: parseStats(root.stats), records }
}

/** 手动跑一次回访：`POST /api/conversation/recall`（到期记录拉日线判对错） */
export async function runRecall(signal?: AbortSignal): Promise<RecallResult> {
  const raw = await requestJson<unknown>(`${CONVERSATION_BASE}/recall`, {
    method: 'POST',
    signal,
    // 回访要逐只取日线，比普通读慢；给 30s 仍不够就让它超时报错，不假装完成
    timeoutMs: 30000,
  })
  const root = asRecord(raw) ?? {}
  return {
    ok: root.ok !== false,
    as_of: asString(root.as_of) || undefined,
    due: asNumber(root.due),
    hit: asNumber(root.hit),
    miss: asNumber(root.miss),
    flipped: asNumber(root.flipped),
    undecided: asNumber(root.undecided),
    waiting: asNumber(root.waiting),
    skipped: asNumber(root.skipped),
    error: asString(root.error) || undefined,
  }
}

export const httpConversation = {
  listSessions,
  listTurns,
  renameSession,
  deleteSession,
  sendTurn,
  sendTurnStream,
  fetchPlan,
  fetchLedger,
  runRecall,
}

export type ConversationClient = typeof httpConversation
