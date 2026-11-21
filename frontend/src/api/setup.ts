/**
 * 首次配置 / 子模块安装向导的客户端。
 *
 * 后端在 `src/ladderstock/web/setup_api.py`，前缀 `/api/setup`。
 * 这里只做三件事：取状态、起安装、轮询进度；不复制任何安装逻辑。
 */

const BASE = '/api/setup'

export type SetupPeer = {
  key: string
  label: string
  target: string
  kind: 'pypi' | 'bundle' | string
  package: string
  version: string
  size_mb: number
  required: boolean
  note: string
  exists: boolean
  /** not_installed | present | ready | failed | installing … */
  state: string
}

export type SetupJobPeer = {
  pct: number
  message: string
  ok: boolean | null
  detail: string
}

export type SetupJob = {
  status: 'running' | 'done' | 'failed'
  current_peer: string | null
  overall: number
  peers: Record<string, SetupJobPeer>
  started_at?: number
  finished_at?: number | null
  error?: string
}

export type SetupStatus = {
  ok: boolean
  modules_root: string
  interpreter: string
  interpreter_origin: string
  portable_python: string
  peers: SetupPeer[]
  missing: string[]
  missing_count: number
  total_mb: number
  total_gb: number
  mirrors: string[]
  job: SetupJob | null
}

/** 服务端返回 `{ok:false, detail}` 时抛这个，页面直接显示 detail。 */
export class SetupError extends Error {
  readonly status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'SetupError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  const text = await response.text()
  let payload: unknown = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    throw new SetupError(`响应不是合法 JSON（HTTP ${response.status}）`, response.status)
  }
  if (!response.ok) {
    const detail = (payload as { detail?: string } | null)?.detail
    throw new SetupError(detail || `请求失败 HTTP ${response.status}`, response.status)
  }
  return payload as T
}

export function getStatus(): Promise<SetupStatus> {
  return request<SetupStatus>('/status')
}

export function getJob(): Promise<{ ok: boolean; job: SetupJob | null }> {
  return request<{ ok: boolean; job: SetupJob | null }>('/job')
}

export function startInstall(
  peers?: string[],
  onlyMissing = true,
): Promise<{ ok: boolean; status?: string; detail?: string; peers?: string[] }> {
  return request('/install', {
    method: 'POST',
    body: JSON.stringify({ peers: peers ?? null, only_missing: onlyMissing }),
  })
}

export function uninstallPeer(
  peer: string,
  confirm: boolean,
): Promise<{ ok: boolean; state: string; detail?: string }> {
  return request('/uninstall', {
    method: 'POST',
    body: JSON.stringify({ peer, confirm }),
  })
}

/** 嵌入模型后下载（便携包默认不带 274MB 权重，首次用记忆时才下）。 */
export type ModelStatus = {
  ok: boolean
  ready: boolean
  models_dir: string
  bundled_dir: string
  model: string
  dirname: string
  size_mb: number
  files: number
  missing: string[]
  mirrors: string[]
  job: ModelJob | null
}

export type ModelJob = {
  status: 'running' | 'done' | 'failed'
  pct: number
  message: string
  models_dir?: string
  started_at?: number
  finished_at?: number | null
  error?: string
}

export function getModelStatus(): Promise<ModelStatus> {
  return request<ModelStatus>('/models')
}

export function startModelFetch(): Promise<{
  ok: boolean
  status?: string
  detail?: string
  job?: ModelJob | null
}> {
  return request('/models/fetch', { method: 'POST', body: '{}' })
}

export function getModelJob(): Promise<{ ok: boolean; job: ModelJob | null }> {
  return request<{ ok: boolean; job: ModelJob | null }>('/models/job')
}

/** 状态文案：围观者只需要知道「装没装、能不能用」。 */
export function describePeerState(state: string): string {
  switch (state) {
    case 'ready':
      return '已安装'
    case 'present':
      return '目录已就位（未登记）'
    case 'failed':
      return '安装失败'
    case 'installing':
    case 'planning':
    case 'fetching':
    case 'preparing':
    case 'verifying':
      return '安装中'
    case 'not_installed':
    default:
      return '未安装'
  }
}
