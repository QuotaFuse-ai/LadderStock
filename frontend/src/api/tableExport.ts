/**
 * `POST /api/table-export`（C 包第二半 / DEC-090）：把**页面已读到的行集**
 * 转成 CSV / XLSX 下载件。
 *
 * 数据在请求体里（不是 URL），所以是 POST；写面语义要求同源来源证据——
 * 浏览器页面同源 `Origin` 自动满足。
 *
 *   POST /api/table-export  { name, format: 'csv'|'xlsx', rows: [...], only?: [...] }
 *
 * 行数上限 2000、体 4MB；超限 413、空行 400、缺 openpyxl 503——原样把人话
 * 抛给页面，不静默换格式。
 */

import { ApiError, isApiError } from '../api'

const TIMEOUT_MS = 30000
const BODY_BYTE_LIMIT = 4 * 1024 * 1024

export type TableExportFormat = 'csv' | 'xlsx'

export interface TableExportRequest {
  name: string
  format: TableExportFormat
  rows: Array<Record<string, unknown>>
  /** 列白名单（可选）：只导这些键，防内部字段一起带走 */
  only?: string[]
}

function buildBody(request: TableExportRequest): string {
  const payload: Record<string, unknown> = {
    name: request.name,
    format: request.format,
    rows: request.rows,
  }
  if (request.only && request.only.length > 0) {
    payload.only = request.only
  }
  return JSON.stringify(payload)
}

export async function downloadTableExport(request: TableExportRequest): Promise<void> {
  const body = buildBody(request)
  if (body.length > BODY_BYTE_LIMIT) {
    throw new ApiError('parse', '导出内容超过 4MB 上限：请缩小范围或分批导出')
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const response = await fetch('/api/table-export', {
      method: 'POST',
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json' },
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
        `导出失败：HTTP ${response.status}${detail ? `：${detail}` : ''}`,
        response.status,
        detail,
      )
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${request.name}.${request.format}`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    if (isApiError(error)) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('timeout', `导出超时（${TIMEOUT_MS}ms）`)
    }
    throw new ApiError('not-connected', '本机接口不可达：表格导出接口')
  } finally {
    clearTimeout(timer)
  }
}