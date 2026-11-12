/**
 * 标的档案只读客户端（QUOT-567）。
 *
 * 一条接口：`GET /api/knowledge/{market}/{symbol}`（QUOT-488 交付，契约在
 * `src/ladderstock/web/knowledge.py`）。载荷是扁平 JSON，不是 `LadderEnvelope`：
 *
 *   { market, symbol, name, as_of, status, valid_days, sources[], body, recalled }
 *
 * 404 的 `detail` 固定是「档案不存在：SH:600000」——这是业务空态（还没攒到这只），
 * 与「接口没接」（FastAPI 默认 404）必须分开，否则用户会把「没存过」看成「页面坏了」。
 *
 * `status` 由后端按 `as_of + valid_days` 现算（`Dossier.computed_status`）：
 * `fresh` 有效、`stale` 已过期、`refuted` 已被新数据推翻。页面不自己算日期。
 *
 * 错误口径与 `api/research.ts` 同形：同一个 `ApiError`、同一套 kind，
 * 文案翻译见 `describeKnowledgeError`（与 `describeAssistantError` 一样「先结论后原因」）。
 */

import { ApiError } from '../api'

export const KNOWLEDGE_MARKETS = ['SH', 'SZ', 'BJ'] as const
export type KnowledgeMarket = (typeof KNOWLEDGE_MARKETS)[number]

export type KnowledgeStatus = 'fresh' | 'stale' | 'refuted'

export interface KnowledgeSource {
  source: string | null
  tool: string | null
  confidence: string | null
  fetched_at: string | null
  trace_id: string | null
}

export interface KnowledgeRecalled {
  as_of: string | null
  status: string | null
  valid_days: number | null
  truncated: boolean
  text: string
}

export interface KnowledgeDossier {
  market: KnowledgeMarket
  symbol: string
  name: string
  as_of: string
  status: KnowledgeStatus
  valid_days: number | null
  sources: KnowledgeSource[]
  body: string
  recalled: KnowledgeRecalled | null
}

export const MARKET_LABEL: Record<KnowledgeMarket, string> = {
  SH: '沪市',
  SZ: '深市',
  BJ: '北交所',
}

export const STATUS_LABEL: Record<KnowledgeStatus, string> = {
  fresh: '有效',
  stale: '可能过期',
  refuted: '已被推翻',
}

const SYMBOL_PATTERN = /^\d{6}$/

export function isKnowledgeMarket(value: unknown): value is KnowledgeMarket {
  return typeof value === 'string' && (KNOWLEDGE_MARKETS as readonly string[]).includes(value.toUpperCase())
}

export function normalizeKnowledgeMarket(raw: string | null | undefined): KnowledgeMarket | null {
  const text = String(raw ?? '')
    .trim()
    .toUpperCase()
  return isKnowledgeMarket(text) ? text : null
}

/** 6 位数字才接受；带后缀的写法（`600000.SH`）在落地页里拆开，不从这里猜。 */
export function normalizeKnowledgeSymbol(raw: string | null | undefined): string | null {
  const text = String(raw ?? '').trim()
  return SYMBOL_PATTERN.test(text) ? text : null
}

/**
 * 按代码前缀推断市场（QUOT-567 落地页的默认值，可手改）：
 * 60 / 68 → 沪市，00 / 30 → 深市，8 / 4 → 北交所（含 43 / 83 / 87 / 920 开头）。
 * 认不出来返回 `null`，由页面让用户自己选，不硬猜一个。
 */
export function inferKnowledgeMarket(code: string | null | undefined): KnowledgeMarket | null {
  const text = String(code ?? '').trim()
  if (!SYMBOL_PATTERN.test(text)) return null
  if (text.startsWith('60') || text.startsWith('68')) return 'SH'
  if (text.startsWith('00') || text.startsWith('30')) return 'SZ'
  if (text.startsWith('8') || text.startsWith('4')) return 'BJ'
  return null
}

// ---------------------------------------------------------------- 解析

function asRecord(value: unknown, what: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError('parse', `档案接口返回的${what}不是 JSON 对象`)
  }
  return value as Record<string, unknown>
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parseStatus(value: unknown): KnowledgeStatus {
  if (value === 'fresh' || value === 'stale' || value === 'refuted') return value
  throw new ApiError('parse', `档案状态不是 fresh / stale / refuted：${String(value)}`)
}

function parseSources(value: unknown): KnowledgeSource[] {
  if (!Array.isArray(value)) return []
  return value
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      source: asText(item.source),
      tool: asText(item.tool),
      confidence: asText(item.confidence),
      fetched_at: asText(item.fetched_at),
      trace_id: asText(item.trace_id),
    }))
}

function parseRecalled(value: unknown): KnowledgeRecalled | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const root = value as Record<string, unknown>
  return {
    as_of: asText(root.as_of),
    status: asText(root.status),
    valid_days: asNumberOrNull(root.valid_days),
    truncated: root.truncated === true,
    text: typeof root.text === 'string' ? root.text : '',
  }
}

export function parseKnowledgeDossier(payload: unknown): KnowledgeDossier {
  const root = asRecord(payload, '内容')
  const market = normalizeKnowledgeMarket(asText(root.market))
  const symbol = normalizeKnowledgeSymbol(asText(root.symbol))
  if (!market || !symbol) {
    throw new ApiError('parse', '档案接口返回的市场或代码不合法')
  }
  return {
    market,
    symbol,
    name: asText(root.name) ?? symbol,
    as_of: asText(root.as_of) ?? '',
    status: parseStatus(root.status),
    valid_days: asNumberOrNull(root.valid_days),
    sources: parseSources(root.sources),
    body: typeof root.body === 'string' ? root.body : '',
    recalled: parseRecalled(root.recalled),
  }
}

// ---------------------------------------------------------------- 传输

const TIMEOUT_MS = 12000

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      cache: 'no-store',
      headers: { accept: 'application/json' },
    })
    if (!response.ok) {
      let detail: string | null = null
      try {
        const parsed = (await response.json()) as { detail?: unknown }
        detail = typeof parsed.detail === 'string' ? parsed.detail : null
      } catch {
        detail = null
      }
      throw new ApiError(
        'http',
        `档案接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw new ApiError('parse', `档案接口 ${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `档案接口 ${path} 超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `档案接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

export function knowledgePath(market: KnowledgeMarket, symbol: string): string {
  return `/api/knowledge/${encodeURIComponent(market)}/${encodeURIComponent(symbol)}`
}

/** 页面取数通道：测试可注入，真实通道只有 HTTP。 */
export type KnowledgeLoader = (
  market: KnowledgeMarket,
  symbol: string,
  signal?: AbortSignal,
) => Promise<KnowledgeDossier>

export const loadKnowledge: KnowledgeLoader = async (market, symbol, signal) =>
  parseKnowledgeDossier(await request<unknown>(knowledgePath(market, symbol), signal))

// ---------------------------------------------------------------- 错误文案

/**
 * 404 的两种语义（与 `classify404` 同一做法）：后端用 `detail` 文案区分。
 * 「档案不存在」= 业务空态；其余 404（FastAPI 默认 `Not Found`）= 接口没接。
 */
export function isKnowledgeMissing(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.kind === 'http' &&
    error.status === 404 &&
    (error.detail ?? '').includes('档案不存在')
  )
}

/** 「结论 + 原因 + 代码」，与 `describeAssistantError` / `describeResearchError` 同款。 */
export function describeKnowledgeError(error: unknown): {
  title: string
  detail: string
  code: string | null
  retryable: boolean
} {
  if (error instanceof ApiError) {
    if (error.kind === 'not-connected') {
      return {
        title: '本机服务未连接',
        detail: '档案存在本机，由 LadderStock 本机服务读取；服务恢复后点「重试」即可，数据不会丢。',
        code: null,
        retryable: true,
      }
    }
    if (error.kind === 'timeout') {
      return { title: '读取档案超时', detail: error.message, code: 'TIMEOUT', retryable: true }
    }
    if (error.kind === 'parse') {
      return { title: '档案内容解析失败', detail: error.message, code: 'E_PARSE', retryable: true }
    }
    if (error.status === 400) {
      return {
        title: '市场或代码不合法',
        detail: error.detail ?? '市场只能是 SH / SZ / BJ，代码是 6 位数字。',
        code: 'HTTP 400',
        retryable: false,
      }
    }
    if (error.status === 403) {
      return {
        title: '档案只在本地可用',
        detail: error.detail ?? '档案接口只接受本机页面访问；请从 LadderStock 窗口内打开。',
        code: 'HTTP 403',
        retryable: false,
      }
    }
    if (error.status === 500) {
      return {
        title: '档案内容被拒绝返回',
        detail: error.detail ?? '档案可能被外部改动过；请重新生成后再看。',
        code: 'HTTP 500',
        retryable: false,
      }
    }
    return {
      title: '读取档案失败',
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
      retryable: true,
    }
  }
  return {
    title: '读取档案失败',
    detail: error instanceof Error ? error.message : String(error),
    code: null,
    retryable: true,
  }
}
