/**
 * QUOT-507 代理池卡片视图口径（纯函数，便于单测）。
 *
 * 铁律与 QUOT-484 一致：mapping 的状态 / 归属只翻译后端字段
 * （`mappings[].state`、`mappings[].owner`、`origin`），前端不扫端口、不推断。
 * 这里只负责「字段 → 文案 / 语气 / 折叠摘要」，不产生任何新的判定。
 */

import type {
  ProxyMapping,
  ProxyMappingState,
  ProxyProbeEntry,
  SupervisorModule,
  SupervisorStatePayload,
} from '../api'

export interface ProxyPoolRow {
  mapping: ProxyMapping
  state: ProxyMappingState
}

export interface MappingStateView {
  label: string
  tone: 'ok' | 'warn' | 'danger' | 'flat'
  dot: 'ok' | 'warn' | 'danger' | 'flat'
  /** 冲突态必须带端口号；其它态为 null */
  detail: string | null
  /** 只有「需人工处理」给重试入口；冲突态重试没用（得人去腾端口） */
  retry: boolean
}

const STATE_VIEW: Record<ProxyMappingState, MappingStateView> = {
  running: { label: '运行中', tone: 'ok', dot: 'ok', detail: null, retry: false },
  stopped: { label: '已停止', tone: 'flat', dot: 'flat', detail: null, retry: false },
  conflict: { label: '冲突', tone: 'danger', dot: 'danger', detail: null, retry: false },
  needs_human: {
    label: '需人工处理（已停止自愈）',
    tone: 'danger',
    dot: 'danger',
    detail: null,
    retry: true,
  },
}

/**
 * proxypool 模块在 supervisor 列表里的键。
 * QUOT-506 的模块名是 `proxypool`；当前后端（482 系列）仍叫 `proxyfleet`，
 * 同一产品的两个键都认，避免后端改名期间卡片整块消失。
 */
export function findProxyPoolModule(
  state: SupervisorStatePayload | null | undefined,
): SupervisorModule | null {
  if (!state) return null
  const wanted = ['proxypool', 'proxyfleet']
  for (const name of wanted) {
    const found = state.modules.find((item) => item.module.replace(/-/g, '_') === name)
    if (found) return found
  }
  return null
}

/** 缺 `state` 时按「已停止」呈现：行必须有明确文案，不许留空白。 */
export function mappingState(mapping: ProxyMapping): ProxyMappingState {
  return mapping.state ?? 'stopped'
}

export function buildProxyRows(module: SupervisorModule | null): ProxyPoolRow[] {
  const mappings = module?.mappings ?? []
  return mappings.map((mapping) => ({ mapping, state: mappingState(mapping) }))
}

export function splitByOwner(rows: readonly ProxyPoolRow[]): {
  human: ProxyPoolRow[]
  ai: ProxyPoolRow[]
} {
  return {
    human: rows.filter((row) => row.mapping.owner === 'human'),
    ai: rows.filter((row) => row.mapping.owner === 'ai'),
  }
}

/** AI 行默认折叠成一行摘要：`3 条映射 · 2 条运行中`（有异常时追加条数）。 */
export function aiSummary(rows: readonly ProxyPoolRow[]): string {
  const running = rows.filter((row) => row.state === 'running').length
  const abnormal = rows.filter(
    (row) => row.state === 'conflict' || row.state === 'needs_human',
  ).length
  const base = `${rows.length} 条映射 · ${running} 条运行中`
  return abnormal > 0 ? `${base} · ${abnormal} 条异常` : base
}

export function listenPort(mapping: ProxyMapping): number | null {
  if (typeof mapping.listen === 'number' && Number.isFinite(mapping.listen)) return mapping.listen
  const text = String(mapping.listen).trim()
  const tail = text.includes(':') ? text.slice(text.lastIndexOf(':') + 1) : text
  const port = Number(tail)
  return Number.isFinite(port) && port > 0 ? port : null
}

/** 冲突端口：优先后端 `conflict_port`，缺省才回落 mapping 的监听端口（仍是后端字段）。 */
export function conflictPort(mapping: ProxyMapping): number | null {
  if (typeof mapping.conflict_port === 'number' && mapping.conflict_port > 0) {
    return mapping.conflict_port
  }
  return listenPort(mapping)
}

export function stateView(row: ProxyPoolRow): MappingStateView {
  const base = STATE_VIEW[row.state]
  if (row.state !== 'conflict') return base
  const port = conflictPort(row.mapping)
  return {
    ...base,
    detail: port === null ? '端口冲突（后端未给出端口号）' : `端口 ${port}`,
  }
}

/** 探活文案：真实毫秒数；失败如实写失败，不写「正常」。 */
export function probeText(entry: ProxyProbeEntry | null | undefined, fallbackRtt?: number | null): string | null {
  if (entry) {
    if (!entry.ok) return '探活失败'
    return typeof entry.rtt_ms === 'number' && Number.isFinite(entry.rtt_ms)
      ? `${Math.round(entry.rtt_ms)}ms`
      : '探活成功（后端未给 rtt_ms）'
  }
  if (typeof fallbackRtt === 'number' && Number.isFinite(fallbackRtt)) {
    return `${Math.round(fallbackRtt)}ms`
  }
  return null
}

export function probeEntryId(entry: ProxyProbeEntry): string | null {
  return entry.mapping_id ?? entry.id ?? null
}

export function upstreamText(mapping: ProxyMapping): string {
  return `${mapping.upstream_host}:${mapping.upstream_port}`
}
