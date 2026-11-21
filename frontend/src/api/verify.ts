/**
 * `/api/verify` F10 截图 / PDF 查证面。
 *
 *   GET  /api/verify/capabilities   本机能做什么（PDF 文本层 / 图片 vision / 本地 OCR）
 *   POST /api/verify/file           multipart 上传一份 PDF / 截图 → 抽取 + 对账
 *   GET  /api/verify/history        最近查证记录
 *
 * 口径：
 * - 上传用 `FormData`，**不手写 `content-type`**（boundary 由浏览器补）；
 * - 识别不了不算成功：`ok=false` + `extract.status=ocr_unavailable|needs_ocr`，
 *   页面必须把 `extract.reason` 原样显示，不画假数字；
 * - 解析缺字段就是 `null` / 空数组，不补 0。
 */

import { ApiError, isApiError } from '../api'

const READ_TIMEOUT_MS = 8000
const UPLOAD_TIMEOUT_MS = 30000

export interface VerifyCapabilities {
  pdf_text: boolean
  pdf_reason: string
  image_vision: boolean
  image_reason: string
  local_ocr: boolean
  local_ocr_reason: string
  supported_image_mime: string[]
  tolerance: Record<string, number>
  max_bytes: number
}

export interface VerifyFigure {
  field: string
  label: string
  value: number | string
  unit: string
  raw: string
  page: number | null
}

export interface VerifyRow {
  field: string
  label: string
  doc_value: number | string
  doc_unit?: string
  doc_raw?: string
  local_value: number | null
  local_date?: string
  local_source: string
  verdict: 'match' | 'mismatch' | 'unverifiable' | string
  note?: string
}

export interface VerifyReconciled {
  code: string | null
  rows: VerifyRow[]
  comparable: number
  matched: number
  mismatched: number
  unverifiable: number
  accuracy: number | null
  accuracy_formula: string
}

export interface VerifyResult {
  ok: boolean
  filename: string
  mime: string
  sha1: string
  bytes: number
  generated_at: string
  extract: { status: string; pages?: number | null; chars?: number | null; reason: string }
  dates: string[]
  figures: VerifyFigure[]
  reconciled: VerifyReconciled
  text_preview: string
  saved?: string | null
  save_error?: string
  capabilities?: VerifyCapabilities
}

export interface VerifyHistoryItem {
  filename: string
  sha1: string
  status: string
  figures: number
  code: string | null
  accuracy: number | null
  generated_at: string
  path: string
}

// ---------------------------------------------------------------- 请求

interface RequestOptions {
  method?: 'GET' | 'POST'
  signal?: AbortSignal
  timeoutMs?: number
  body?: FormData
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', signal, timeoutMs = READ_TIMEOUT_MS, body } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  signal?.addEventListener('abort', abortFromCaller)
  try {
    const headers: Record<string, string> = { accept: 'application/json', 'X-LDST-Client': '1' }
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers,
      body,
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
      if (signal?.aborted) throw new ApiError('timeout', `请求已取消：${path}`)
      throw new ApiError('timeout', `${path} 超时（${timeoutMs}ms）`)
    }
    throw new ApiError('not-connected', `本机接口不可达：${path}`)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
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
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function boolOr(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') return value
  return fallback
}

export function parseCapabilities(raw: unknown): VerifyCapabilities {
  const row = record(raw) ?? {}
  return {
    pdf_text: boolOr(row.pdf_text, false),
    pdf_reason: text(row.pdf_reason),
    image_vision: boolOr(row.image_vision, false),
    image_reason: text(row.image_reason),
    local_ocr: boolOr(row.local_ocr, false),
    local_ocr_reason: text(row.local_ocr_reason),
    supported_image_mime: list(row.supported_image_mime).map((item) => text(item)),
    tolerance: Object.fromEntries(
      Object.entries(record(row.tolerance) ?? {})
        .filter(([, value]) => typeof value === 'number')
        .map(([key, value]) => [key, value as number]),
    ),
    max_bytes: numberOrNull(row.max_bytes) ?? 8 * 1024 * 1024,
  }
}

function parseFigure(value: unknown): VerifyFigure | null {
  const row = record(value)
  if (!row) return null
  const raw = text(row.value)
  if (!raw) return null
  return {
    field: text(row.field),
    label: text(row.label, text(row.field)),
    value: typeof row.value === 'number' ? row.value : raw,
    unit: text(row.unit),
    raw: text(row.raw),
    page: numberOrNull(row.page),
  }
}

function parseRow(value: unknown): VerifyRow | null {
  const row = record(value)
  if (!row) return null
  const field = text(row.field)
  if (!field) return null
  return {
    field,
    label: text(row.label, field),
    doc_value: typeof row.doc_value === 'number' ? row.doc_value : text(row.doc_value),
    doc_unit: text(row.doc_unit),
    doc_raw: text(row.doc_raw),
    local_value: numberOrNull(row.local_value),
    local_date: text(row.local_date),
    local_source: text(row.local_source),
    verdict: text(row.verdict, 'unverifiable'),
    note: text(row.note),
  }
}

function parseReconciled(value: unknown): VerifyReconciled {
  const row = record(value) ?? {}
  return {
    code: text(row.code).trim() || null,
    rows: list(row.rows)
      .map(parseRow)
      .filter((item): item is VerifyRow => item !== null),
    comparable: numberOrNull(row.comparable) ?? 0,
    matched: numberOrNull(row.matched) ?? 0,
    mismatched: numberOrNull(row.mismatched) ?? 0,
    unverifiable: numberOrNull(row.unverifiable) ?? 0,
    accuracy: numberOrNull(row.accuracy),
    accuracy_formula: text(row.accuracy_formula),
  }
}

export function parseResult(raw: unknown): VerifyResult {
  const root = record(raw)
  if (!root) throw new ApiError('parse', '查证响应不是对象')
  const extract = record(root.extract) ?? {}
  return {
    ok: boolOr(root.ok, false),
    filename: text(root.filename),
    mime: text(root.mime),
    sha1: text(root.sha1),
    bytes: numberOrNull(root.bytes) ?? 0,
    generated_at: text(root.generated_at),
    extract: {
      status: text(extract.status, 'failed'),
      pages: numberOrNull(extract.pages),
      chars: numberOrNull(extract.chars),
      reason: text(extract.reason),
    },
    dates: list(root.dates).map((item) => text(item)),
    figures: list(root.figures)
      .map(parseFigure)
      .filter((item): item is VerifyFigure => item !== null),
    reconciled: parseReconciled(root.reconciled),
    text_preview: text(root.text_preview),
    saved: text(root.saved).trim() || null,
    save_error: text(root.save_error),
    capabilities: root.capabilities ? parseCapabilities(root.capabilities) : undefined,
  }
}

// ---------------------------------------------------------------- 页面调用

export async function fetchVerifyCapabilities(signal?: AbortSignal): Promise<VerifyCapabilities> {
  return parseCapabilities(await request<unknown>('/api/verify/capabilities', { signal }))
}

export async function fetchVerifyHistory(
  limit = 8,
  signal?: AbortSignal,
): Promise<VerifyHistoryItem[]> {
  const raw = await request<unknown>(`/api/verify/history?limit=${limit}`, { signal })
  return list(record(raw)?.items).map((value) => {
    const row = record(value) ?? {}
    return {
      filename: text(row.filename),
      sha1: text(row.sha1),
      status: text(row.status),
      figures: numberOrNull(row.figures) ?? 0,
      code: text(row.code).trim() || null,
      accuracy: numberOrNull(row.accuracy),
      generated_at: text(row.generated_at),
      path: text(row.path),
    }
  })
}

export async function verifyFile(
  file: File,
  options: { code?: string; signal?: AbortSignal } = {},
): Promise<VerifyResult> {
  const form = new FormData()
  form.append('file', file, file.name)
  if (options.code) form.append('code', options.code)
  const raw = await request<unknown>('/api/verify/file', {
    method: 'POST',
    signal: options.signal,
    timeoutMs: UPLOAD_TIMEOUT_MS,
    body: form,
  })
  return parseResult(raw)
}
