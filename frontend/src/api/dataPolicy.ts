/**
 * 数据权限档客户端（`/api/data/policy`，F5 的后端接口、F6 的输入框下拉）。
 *
 * 三档语义只在服务端定义一次（`core/policy.py` 的 `describe`），这里只透传，
 * 不复制判断表。前端唯一自己算的是 `needsConfirm`：一轮问答是同步端点，
 * 回答中途没机会弹窗，所以把「要不要先问」前置到发问之前——按档位 + 问题的
 * 动作面决定是否先弹一次确认。
 */

import { ApiError } from '../api'

export const POLICY_ORDER = ['always_ask', 'ask_on_risk', 'allow_all'] as const
export type ConfirmPolicy = (typeof POLICY_ORDER)[number]

export interface PolicyDescribe {
  confirm_policy: string
  /** tier → 是否已放行（true = 不弹窗） */
  tiers: Record<string, boolean>
  labels: Record<string, string>
  descriptions: Record<string, string>
}

export interface DataPolicy {
  confirm_policy: ConfirmPolicy
  scope: 'session' | 'global'
  updated_at: string | null
  describe: PolicyDescribe
}

/** 读取失败/旧值都归一到安全一侧（默认 ask_on_risk，宁可多问一句）。 */
export function normalizePolicy(raw: unknown): ConfirmPolicy {
  const text = String(raw ?? '').trim().toLowerCase()
  return (POLICY_ORDER as readonly string[]).includes(text) ? (text as ConfirmPolicy) : 'ask_on_risk'
}

/** 没拿到服务端 describe 时的兜底文案（与 `core/policy.py` 的 labels 一致）。 */
export const POLICY_FALLBACK_LABELS: Record<ConfirmPolicy, string> = {
  always_ask: '始终询问',
  ask_on_risk: '按需确认',
  allow_all: '全部允许',
}

export const POLICY_FALLBACK_HINTS: Record<ConfirmPolicy, string> = {
  always_ask: '每个操作都先问你，含只读查询',
  ask_on_risk: '只对采集、写库、改配置确认（推荐）',
  allow_all: '不打断；启停服务仍会询问',
}

/**
 * 这个档位下，这句问题要不要先弹确认。
 *
 * `always_ask` 一律要问；`allow_all` 不问（启停服务不在对话里做）；
 * `ask_on_risk` 只在问题带「采集类动作」时才问——只读问答不打断。
 */
const RISK_ACTIONS =
  /(补采|补数|采集|入库|写库|回填|建表|同步|更新数据|更新库|拉取|下载|批量|导入)/

export function needsConfirm(policy: ConfirmPolicy, question: string): boolean {
  if (policy === 'always_ask') return true
  if (policy === 'allow_all') return false
  return RISK_ACTIONS.test(question)
}

const TIMEOUT_MS = 10000

function parseError(message: string): ApiError {
  return new ApiError('parse', message)
}

async function readJson<T>(response: Response, path: string): Promise<T> {
  if (!response.ok) {
    let detail: string | null = null
    try {
      const payload = (await response.json()) as { detail?: unknown }
      detail = typeof payload.detail === 'string' ? payload.detail : null
    } catch {
      detail = null
    }
    throw new ApiError(
      'http',
      `权限档接口 ${path} 返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`,
      response.status,
      detail,
    )
  }
  try {
    return (await response.json()) as T
  } catch {
    throw parseError(`权限档接口 ${path} 返回的内容不是合法 JSON`)
  }
}

async function call<T>(
  path: string,
  init: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  const relay = () => controller.abort()
  signal?.addEventListener('abort', relay)
  try {
    const response = await fetch(path, { ...init, signal: controller.signal, cache: 'no-store' })
    return await readJson<T>(response, path)
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `权限档接口 ${path} 超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', `权限档接口 ${path} 不可达`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', relay)
  }
}

function shape(payload: DataPolicy & { ok?: boolean }): DataPolicy {
  const describe = payload?.describe
  if (!describe || typeof describe !== 'object') {
    throw parseError('权限档响应缺少 describe')
  }
  return { ...payload, confirm_policy: normalizePolicy(payload.confirm_policy) }
}

export async function fetchDataPolicy(signal?: AbortSignal): Promise<DataPolicy> {
  const payload = await call<DataPolicy & { ok?: boolean }>(
    '/api/data/policy',
    { method: 'GET', headers: { accept: 'application/json' } },
    signal,
  )
  return shape(payload)
}

export async function saveDataPolicy(
  confirmPolicy: ConfirmPolicy,
  scope: 'session' | 'global' = 'session',
  signal?: AbortSignal,
): Promise<DataPolicy> {
  const payload = await call<DataPolicy & { ok?: boolean }>(
    '/api/data/policy',
    {
      method: 'POST',
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
        'X-LDST-Client': '1',
      },
      body: JSON.stringify({ confirm_policy: confirmPolicy, scope }),
    },
    signal,
  )
  return shape(payload)
}

export function describePolicyError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === 'not-connected') return '权限档接口不可达（确认主程序已启动）'
    if (error.kind === 'timeout') return '权限档读取超时'
    return error.detail ?? error.message
  }
  return error instanceof Error ? error.message : String(error)
}
