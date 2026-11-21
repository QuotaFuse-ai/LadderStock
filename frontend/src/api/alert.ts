/**
 * `/api/alert` 提醒引擎（DEC-087 后端，A 包前端面）。
 *
 * 规则 / 渠道 / 手动跑一轮 / 触发历史。读写都是**本机隐私面**的平面 JSON：
 * 回环 + Host 白名单读；写面再加同源来源证据（浏览器页面同源自动满足，
 * 所以这里只发普通 fetch；后端 `X-LDST-Client` 只约束 curl / 脚本）。
 *
 *   GET    /api/alert/rules            规则清单（含运行态）
 *   POST   /api/alert/rules            新增规则
 *   PUT    /api/alert/rules/{id}       改规则
 *   DELETE /api/alert/rules/{id}       删规则
 *   POST   /api/alert/rules/{id}/toggle 启停
 *   GET    /api/alert/channels         渠道清单（URL 已在后端打码）
 *   POST   /api/alert/channels         新增渠道
 *   PUT    /api/alert/channels/{id}    改渠道
 *   DELETE /api/alert/channels/{id}    删渠道
 *   POST   /api/alert/channels/{id}/test 真发一条测试消息
 *   POST   /api/alert/run              手动跑一轮（与调度 / CLI 同一个执行体）
 *   GET    /api/alert/history          最近触发历史
 *
 * 解析口径与 `api/inbox.ts` 一致：缺字段就是 `null` / 空数组，不补 0；
 * 响应不是对象属于契约破坏，抛 `ApiError('parse')`，屏幕显示原因而不是空白。
 */

import { ApiError, isApiError } from '../api'

const TIMEOUT_MS = 8000
const RUN_TIMEOUT_MS = 60000 // 手动跑一轮要真取数（现价 / 历史），给足一分钟

// ---------------------------------------------------------------- 类型

export type RuleKind =
  | 'price_above'
  | 'price_below'
  | 'ma_break_down'
  | 'ma_break_up'
  | 'volume_spike'
  | 'pnl_drop'
  | 'pnl_gain'

export interface RuleState {
  rule_id: string
  status: 'armed' | 'cooldown' | 'fired' | string
  last_value: number | null
  fired_at: string | null
  cooldown_until: string | null
  updated_at: string
}

export interface AlertRule {
  id: string
  kind: RuleKind | string
  symbol: string
  threshold: number
  window: number
  cooldown_min: number
  channels: string[]
  note: string
  enabled: boolean
  created_at: string
  updated_at: string
  /** 列表接口额外附带的运行态（详情页缺失时为 null） */
  state?: RuleState | null
}

export interface RuleDraft {
  kind: string
  symbol: string
  threshold: number
  window?: number | null
  cooldown_min?: number
  channels?: string[]
  note?: string
}

export interface AlertChannel {
  id: string
  kind: string
  name: string
  /** 读面永远只有打码 URL / 非密钥字段（后端 public_config 投影） */
  config: Record<string, unknown>
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface ChannelDraft {
  id: string
  kind: string
  name?: string
  config: Record<string, unknown>
}

export interface DeliveryView {
  channel: string
  ok: boolean
  detail?: string | null
  degraded?: boolean
  sent_at?: string | null
}

export interface FiredView {
  rule_id: string
  symbol: string
  kind: string
  title: string
  value: number | null
  detail: string
  deliveries: DeliveryView[]
}

export interface RunReport {
  ran_at: string
  checked: number
  status: 'ok' | 'partial' | 'skipped' | string
  detail: string
  fired: FiredView[]
  results?: Array<{
    rule_id: string
    symbol: string
    kind: string
    triggered: boolean
    status: string
    value: number | null
    detail: string
    fired: boolean
    state: string
  }>
}

export interface HistoryItem {
  id: string
  rule_id: string
  fired_at: string
  title: string
  body: string
  deliveries: DeliveryView[]
}

// ---------------------------------------------------------------- 请求

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, timeoutMs = TIMEOUT_MS } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: body === undefined ? { accept: 'application/json' } : {
        accept: 'application/json',
        'content-type': 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
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
        `${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    try {
      return (await response.json()) as T
    } catch {
      throw new ApiError('parse', `${path} 返回的内容不是合法 JSON`)
    }
  } catch (error) {
    if (isApiError(error)) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `本机接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
  }
}

// ---------------------------------------------------------------- 解析

function record(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function boolOr(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function parseChannel(row: unknown): AlertChannel {
  const src = record(row)
  const config = record(src?.config) ?? {}
  return {
    id: text(src?.id),
    kind: text(src?.kind),
    name: text(src?.name),
    config,
    enabled: boolOr(src?.enabled, true),
    created_at: text(src?.created_at),
    updated_at: text(src?.updated_at),
  }
}

function parseDelivery(row: unknown): DeliveryView {
  const src = record(row)
  return {
    channel: text(src?.channel),
    ok: boolOr(src?.ok, false),
    detail: typeof src?.detail === 'string' ? src.detail : null,
    degraded: boolOr(src?.degraded, false),
    sent_at: typeof src?.sent_at === 'string' ? src.sent_at : null,
  }
}

function parseFired(row: unknown): FiredView {
  const src = record(row)
  return {
    rule_id: text(src?.rule_id),
    symbol: text(src?.symbol),
    kind: text(src?.kind),
    title: text(src?.title),
    value: numberOrNull(src?.value),
    detail: text(src?.detail),
    deliveries: list(src?.deliveries).map(parseDelivery),
  }
}

function parseHistory(row: unknown): HistoryItem {
  const src = record(row)
  return {
    id: text(src?.id),
    rule_id: text(src?.rule_id),
    fired_at: text(src?.fired_at),
    title: text(src?.title),
    body: text(src?.body),
    deliveries: list(src?.deliveries).map(parseDelivery),
  }
}

function parseRule(row: unknown): AlertRule {
  const src = record(row)
  const stateRow = src?.state === undefined ? undefined : record(src?.state)
  return {
    id: text(src?.id),
    kind: text(src?.kind),
    symbol: text(src?.symbol),
    threshold: numberOrNull(src?.threshold) ?? 0,
    window: numberOrNull(src?.window) ?? 0,
    cooldown_min: numberOrNull(src?.cooldown_min) ?? 60,
    channels: list(src?.channels).map((item) => text(item)).filter(Boolean),
    note: text(src?.note),
    enabled: boolOr(src?.enabled, true),
    created_at: text(src?.created_at),
    updated_at: text(src?.updated_at),
    state: stateRow
      ? {
          rule_id: text(stateRow.rule_id),
          status: text(stateRow.status, 'armed'),
          last_value: numberOrNull(stateRow.last_value),
          fired_at: typeof stateRow.fired_at === 'string' ? stateRow.fired_at : null,
          cooldown_until:
            typeof stateRow.cooldown_until === 'string' ? stateRow.cooldown_until : null,
          updated_at: text(stateRow.updated_at),
        }
      : null,
  }
}

// ---------------------------------------------------------------- API

export interface RulesPayload {
  total: number
  kinds: string[]
  rules: AlertRule[]
}

export async function fetchRules(): Promise<RulesPayload> {
  const parsed = await request<unknown>('/api/alert/rules')
  const src = record(parsed)
  if (!src) throw new ApiError('parse', '/api/alert/rules 返回的不是对象')
  return {
    total: numberOrNull(src.total) ?? 0,
    kinds: list(src.kinds).map((item) => text(item)),
    rules: list(src.rules).map(parseRule),
  }
}

export async function createRule(draft: RuleDraft): Promise<AlertRule> {
  const parsed = await request<unknown>('/api/alert/rules', { method: 'POST', body: draft })
  const src = record(parsed)
  const rule = record(src?.rule)
  if (!rule) throw new ApiError('parse', '新增规则的响应缺 rule 字段')
  return parseRule(rule)
}

export async function updateRule(id: string, patch: Partial<RuleDraft>): Promise<AlertRule> {
  const parsed = await request<unknown>(`/api/alert/rules/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: patch,
  })
  const src = record(parsed)
  const rule = record(src?.rule)
  if (!rule) throw new ApiError('parse', '更新规则的响应缺 rule 字段')
  return parseRule(rule)
}

export async function deleteRule(id: string): Promise<void> {
  await request<unknown>(`/api/alert/rules/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function toggleRule(id: string, enabled: boolean): Promise<AlertRule> {
  const parsed = await request<unknown>(
    `/api/alert/rules/${encodeURIComponent(id)}/toggle`,
    { method: 'POST', body: { enabled } },
  )
  const src = record(parsed)
  const rule = record(src?.rule)
  if (!rule) throw new ApiError('parse', '启停规则的响应缺 rule 字段')
  return parseRule(rule)
}

export interface ChannelsPayload {
  total: number
  channels: AlertChannel[]
}

export async function fetchChannels(): Promise<ChannelsPayload> {
  const parsed = await request<unknown>('/api/alert/channels')
  const src = record(parsed)
  if (!src) throw new ApiError('parse', '/api/alert/channels 返回的不是对象')
  return {
    total: numberOrNull(src.total) ?? 0,
    channels: list(src.channels).map(parseChannel),
  }
}

export async function createChannel(draft: ChannelDraft): Promise<AlertChannel> {
  const parsed = await request<unknown>('/api/alert/channels', { method: 'POST', body: draft })
  const src = record(parsed)
  const channel = record(src?.channel)
  if (!channel) throw new ApiError('parse', '新增渠道的响应缺 channel 字段')
  return parseChannel(channel)
}

export async function updateChannel(
  id: string,
  patch: { name?: string; config?: Record<string, unknown>; enabled?: boolean },
): Promise<AlertChannel> {
  const parsed = await request<unknown>(`/api/alert/channels/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: patch,
  })
  const src = record(parsed)
  const channel = record(src?.channel)
  if (!channel) throw new ApiError('parse', '更新渠道的响应缺 channel 字段')
  return parseChannel(channel)
}

export async function deleteChannel(id: string): Promise<void> {
  await request<unknown>(`/api/alert/channels/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export interface ChannelTestResult {
  ok: boolean
  detail: string
}

export async function testChannel(id: string): Promise<ChannelTestResult> {
  const parsed = await request<unknown>(
    `/api/alert/channels/${encodeURIComponent(id)}/test`,
    { method: 'POST', timeoutMs: 15000 },
  )
  const src = record(parsed)
  const result = record(src?.result)
  return {
    ok: boolOr(src?.ok, false),
    detail: text(result?.detail),
  }
}

export async function runAlerts(): Promise<RunReport> {
  const parsed = await request<unknown>('/api/alert/run', {
    method: 'POST',
    timeoutMs: RUN_TIMEOUT_MS,
  })
  const src = record(parsed)
  if (!src) throw new ApiError('parse', '/api/alert/run 返回的不是对象')
  return {
    ran_at: text(src.ran_at),
    checked: numberOrNull(src.checked) ?? 0,
    status: text(src.status, 'ok'),
    detail: text(src.detail),
    fired: list(src.fired).map(parseFired),
    results: list(src.results).map((row) => {
      const item = record(row)
      return {
        rule_id: text(item?.rule_id),
        symbol: text(item?.symbol),
        kind: text(item?.kind),
        triggered: boolOr(item?.triggered, false),
        status: text(item?.status),
        value: numberOrNull(item?.value),
        detail: text(item?.detail),
        fired: boolOr(item?.fired, false),
        state: text(item?.state),
      }
    }),
  }
}

export interface HistoryPayload {
  items: HistoryItem[]
}

export async function fetchHistory(limit = 50): Promise<HistoryPayload> {
  const parsed = await request<unknown>(`/api/alert/history?limit=${limit}`)
  const src = record(parsed)
  if (!src) throw new ApiError('parse', '/api/alert/history 返回的不是对象')
  return { items: list(src.items).map(parseHistory) }
}

// ---------------------------------------------------------------- 展示口径

/** 七类规则的人话标签（与后端 `alert/rules.py:KIND_LABELS` 同一份口径） */
export const RULE_KIND_LABELS: Record<string, string> = {
  price_above: '价格涨到',
  price_below: '价格跌破',
  ma_break_down: '跌破均线',
  ma_break_up: '站上均线',
  volume_spike: '放量',
  pnl_drop: '浮亏超过',
  pnl_gain: '浮盈超过',
}

export const CHANNEL_KIND_LABELS: Record<string, string> = {
  console: '终端',
  desktop: '桌面通知',
  email: '邮件（SMTP）',
  webhook: 'Webhook',
  dingtalk: '钉钉机器人',
  feishu: '飞书机器人',
  wecom: '企业微信机器人',
}

export function describeRule(rule: AlertRule): string {
  if (rule.kind === 'ma_break_down' || rule.kind === 'ma_break_up') {
    return `${rule.symbol} ${RULE_KIND_LABELS[rule.kind] ?? rule.kind} MA${rule.window}`
  }
  if (rule.kind === 'volume_spike') {
    return `${rule.symbol} 放量 ${rule.threshold} 倍（前 ${rule.window} 日均量）`
  }
  if (rule.kind === 'pnl_drop' || rule.kind === 'pnl_gain') {
    const sign = rule.kind === 'pnl_drop' ? '−' : '+'
    return `${rule.symbol} ${RULE_KIND_LABELS[rule.kind] ?? ''} ${sign}${rule.threshold}%`
  }
  return `${rule.symbol} ${RULE_KIND_LABELS[rule.kind] ?? rule.kind} ${rule.threshold}`
}
