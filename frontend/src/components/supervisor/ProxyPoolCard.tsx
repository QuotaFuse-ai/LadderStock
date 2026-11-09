import { useState } from 'react'
import { AlertTriangle, ChevronDown, Play, RefreshCw, Sliders, Square } from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  type ProxyMapping,
  type ProxyProbeEntry,
  type SupervisorModule,
} from '../../api'
import { Button, Input } from '../ui'
import { cn } from '../../lib/cn'
import { ownershipView, selfHealView } from '../../lib/supervisorView'
import {
  aiSummary,
  buildProxyRows,
  probeEntryId,
  probeText,
  splitByOwner,
  stateView,
  upstreamText,
  type ProxyPoolRow,
} from '../../lib/proxyPoolView'

/**
 * QUOT-507 代理池卡片。
 *
 * 数据只来自 `GET /api/supervisor/state` 的 proxypool 条目（mappings / state / owner /
 * origin），动作只走 QUOT-490 的四个接口。前端不扫端口、不推断归属、不改端口。
 *
 * 视觉沿用模块卡片（`rounded border bg-surface shadow-card` + Button / Badge 语气），
 * 字级只用 20 / 13 / 11 三档（QUOT-479 定稿）。
 */

interface EditDraft {
  host: string
  port: string
}

export interface ProxyPoolCardProps {
  module: SupervisorModule | null
  supervisorReady: boolean
  onRefresh: () => void
}

function actionErrorText(error: unknown): string {
  if (isApiError(error) && error.kind === 'http' && (error.status === 404 || error.status === 501)) {
    return '后端未接入代理池动作接口（HTTP 404），本次未执行任何动作。'
  }
  return describeApiError(error).detail
}

function sameDraft(mapping: ProxyMapping, draft: EditDraft): boolean {
  return (
    draft.host.trim() === mapping.upstream_host &&
    Number(draft.port) === mapping.upstream_port
  )
}

function OwnerTag({ owner }: { owner: 'ai' | 'human' }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-xs px-2 py-0 text-11px font-medium',
        owner === 'ai' ? 'bg-info-soft text-info' : 'bg-flat-soft text-flat',
      )}
    >
      {owner === 'ai' ? 'AI 使用' : '人工使用'}
    </span>
  )
}

function StateMark({ row, probe }: { row: ProxyPoolRow; probe: ProxyProbeEntry | undefined }) {
  const view = stateView(row)
  const rtt = probeText(probe, row.mapping.rtt_ms)
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1.5 text-13px text-text">
        <span
          aria-hidden="true"
          className={cn(
            'inline-block size-2 shrink-0 rounded-pill',
            view.dot === 'ok' && 'bg-ok',
            view.dot === 'warn' && 'bg-warn',
            view.dot === 'danger' && 'bg-danger',
            view.dot === 'flat' && 'border border-border-strong bg-transparent',
          )}
        />
        <span className={cn(view.tone === 'danger' && 'text-danger')}>{view.label}</span>
      </span>
      {view.detail && <span className="text-11px text-danger">{view.detail}</span>}
      {rtt && (
        <span
          className={cn('text-11px', probe && !probe.ok ? 'text-danger' : 'text-text-secondary')}
          title={probe?.error ?? undefined}
        >
          · {rtt}
        </span>
      )}
    </span>
  )
}

function MappingRow({
  row,
  probe,
  retrying,
  onRetry,
}: {
  row: ProxyPoolRow
  probe: ProxyProbeEntry | undefined
  retrying: boolean
  onRetry: () => void
}) {
  const view = stateView(row)
  return (
    <tr className="align-top" data-testid={`proxy-row-${row.mapping.id}`}>
      <td className="py-2 pr-3 text-13px text-text">
        <span className="ldst-num">{String(row.mapping.listen)}</span>
        {!row.mapping.enabled && <span className="ml-1 text-11px text-text-muted">已禁用</span>}
      </td>
      <td className="py-2 pr-3 text-13px text-text">
        <span className="ldst-num">{upstreamText(row.mapping)}</span>
      </td>
      <td className="py-2 pr-3">
        <div className="flex flex-wrap items-center gap-2">
          <StateMark row={row} probe={probe} />
          {view.retry && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              data-testid={`proxy-retry-${row.mapping.id}`}
              className="text-11px font-medium text-danger underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {retrying ? '重试中…' : '重试'}
            </button>
          )}
        </div>
      </td>
      <td className="py-2">
        <OwnerTag owner={row.mapping.owner} />
      </td>
    </tr>
  )
}

export function ProxyPoolCard({ module, supervisorReady, onRefresh }: ProxyPoolCardProps) {
  const rows = buildProxyRows(module)
  const { human, ai } = splitByOwner(rows)
  const [aiOpen, setAiOpen] = useState(false)
  const [probes, setProbes] = useState<Record<string, ProxyProbeEntry>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'warn' | 'danger' | 'info'; text: string } | null>(null)
  const [editing, setEditing] = useState(false)
  const [drafts, setDrafts] = useState<Record<string, EditDraft>>({})
  const [editError, setEditError] = useState<string | null>(null)

  const ownership = ownershipView(module)
  const heal = selfHealView(module)
  const mappingsMissing = module !== null && !Array.isArray(module.mappings)

  const runningRows = rows.filter((row) => row.state === 'running')
  // 冲突态重试没用（得人去腾端口）：整池启动也不把冲突行算进去
  const startRows = rows.filter((row) => row.state === 'stopped' || row.state === 'needs_human')
  const enabledRows = rows.filter((row) => row.mapping.enabled)
  const canStart = startRows.length > 0
  const canStop = runningRows.length > 0
  const canProbe = enabledRows.length > 0
  const canEdit = rows.length > 0

  const emptyText = !supervisorReady
    ? '代理池数据不可用（GET /api/supervisor/state 未接入）；前端不扫端口猜状态。'
    : module === null
      ? 'supervisor 未返回 proxypool 模块，代理池暂无状态。'
      : '后端未提供映射清单（mappings 缺失）；接口就位后这里逐条显示。'

  const refresh = (): void => {
    onRefresh()
  }

  const runPool = async (kind: 'start' | 'stop' | 'probe'): Promise<void> => {
    if (busy) return
    setBusy(kind)
    setNotice(null)
    try {
      if (kind === 'start') {
        const ids = startRows.map((row) => row.mapping.id)
        await api.supervisor.poolStart({ mapping_ids: ids })
        setNotice({ tone: 'ok', text: `已请求启动 ${ids.length} 条映射；状态以后端刷新为准。` })
      } else if (kind === 'stop') {
        const ids = runningRows.map((row) => row.mapping.id)
        await api.supervisor.poolStop({ mapping_ids: ids })
        setNotice({ tone: 'ok', text: `已请求停止 ${ids.length} 条映射；状态以后端刷新为准。` })
      } else {
        const ids = enabledRows.map((row) => row.mapping.id)
        const result = await api.supervisor.poolProbe({ mapping_ids: ids })
        const next: Record<string, ProxyProbeEntry> = {}
        for (const entry of result.results ?? []) {
          const id = probeEntryId(entry)
          if (id) next[id] = entry
        }
        setProbes(next)
        const failed = Object.values(next).filter((entry) => !entry.ok).length
        setNotice({
          tone: failed > 0 ? 'warn' : 'ok',
          text:
            Object.keys(next).length === 0
              ? '后端未返回探活结果（results 为空）。'
              : `探活完成：${Object.keys(next).length - failed} 条成功${failed > 0 ? `，${failed} 条失败` : ''}。`,
        })
      }
      refresh()
    } catch (error) {
      setNotice({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusy(null)
    }
  }

  const retryMapping = async (mappingId: string): Promise<void> => {
    if (busy) return
    setBusy(`retry:${mappingId}`)
    setNotice(null)
    try {
      await api.supervisor.poolStart({ mapping_ids: [mappingId] })
      setNotice({ tone: 'ok', text: `已请求重启映射 ${mappingId}；结果以后端刷新为准。` })
      refresh()
    } catch (error) {
      setNotice({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusy(null)
    }
  }

  const openEdit = (): void => {
    const next: Record<string, EditDraft> = {}
    for (const row of rows) {
      next[row.mapping.id] = {
        host: row.mapping.upstream_host,
        port: String(row.mapping.upstream_port),
      }
    }
    setDrafts(next)
    setEditError(null)
    setEditing(true)
    setNotice(null)
  }

  const saveEdits = async (): Promise<void> => {
    if (busy) return
    const changed = rows.filter((row) => {
      const draft = drafts[row.mapping.id]
      return draft && !sameDraft(row.mapping, draft)
    })
    if (changed.length === 0) {
      setNotice({ tone: 'info', text: '没有改动，未发送保存请求。' })
      setEditing(false)
      return
    }
    for (const row of changed) {
      const draft = drafts[row.mapping.id]
      const port = Number(draft.port)
      if (!draft.host.trim() || !Number.isInteger(port) || port <= 0 || port > 65535) {
        setEditError(`映射 ${row.mapping.id} 的上游地址不合法：host 不能为空，端口需在 1–65535。`)
        return
      }
    }
    setBusy('save')
    setEditError(null)
    try {
      for (const row of changed) {
        const draft = drafts[row.mapping.id]
        await api.supervisor.poolSetUpstream({
          mapping_id: row.mapping.id,
          upstream_host: draft.host.trim(),
          upstream_port: Number(draft.port),
        })
      }
      setEditing(false)
      // 契约：set_upstream 不自动重启；提示必须可见，不许静默成功。
      setNotice({ tone: 'ok', text: '配置已保存，需手动启动生效' })
      refresh()
    } catch (error) {
      setNotice({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusy(null)
    }
  }

  const statusText = rows.length > 0 ? `共 ${rows.length} 条映射 · 运行中 ${runningRows.length} 条` : null

  return (
    <li
      className="rounded border border-border bg-surface shadow-card"
      data-testid="proxy-pool-card"
      data-pool-state={runningRows.length > 0 ? 'running' : 'stopped'}
    >
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="text-section font-semibold text-text">代理池</h4>
              <span className="inline-flex items-center rounded-xs bg-info-soft px-2 text-11px font-medium text-info">
                代理模块
              </span>
              {supervisorReady && module ? (
                <span
                  title={ownership.hint}
                  className={cn(
                    'inline-flex items-center rounded-xs px-2 text-11px font-medium',
                    ownership.tone === 'ok' && 'bg-ok-soft text-ok',
                    ownership.tone === 'warn' && 'bg-warn-soft text-warn',
                    (ownership.tone === 'neutral' || ownership.tone === 'flat') &&
                      'bg-flat-soft text-flat',
                  )}
                >
                  {ownership.label}
                </span>
              ) : (
                <span className="inline-flex items-center rounded-xs border border-border px-2 text-11px text-text-secondary">
                  {supervisorReady ? '归属未知（proxypool 未接入）' : '归属未知（supervisor 未接入）'}
                </span>
              )}
            </div>
            <p className="mt-1 text-13px text-text-secondary">
              {module && module.ports.length > 0 ? (
                <>
                  模块端口 <span className="ldst-num">{module.ports.join(' / ')}</span>
                </>
              ) : (
                '代理端口映射清单'
              )}
              {statusText && <> · {statusText}</>}
            </p>
          </div>
        </div>

        {heal && (
          <p className={cn('flex items-center gap-1 text-11px', heal.failed ? 'text-danger' : 'text-warn')}>
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
            {heal.label}
          </p>
        )}

        {notice && (
          <p
            role="status"
            data-testid="proxy-notice"
            className={cn(
              'rounded border px-2 py-1 text-13px',
              notice.tone === 'ok' && 'border-ok bg-ok-soft text-ok',
              notice.tone === 'warn' && 'border-warn bg-warn-soft text-warn',
              notice.tone === 'danger' && 'border-danger bg-danger-soft text-danger',
              notice.tone === 'info' && 'border-info bg-info-soft text-info',
            )}
          >
            {notice.text}
          </p>
        )}

        {rows.length === 0 ? (
          <p className="text-13px text-text-secondary" data-testid="proxy-empty">
            {emptyText}
            {mappingsMissing && ' 当前卡片是如实占位，不会一直转圈。'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-13px">
              <thead>
                <tr className="border-b border-border text-left text-11px text-text-muted">
                  <th className="pb-1 pr-3 font-medium">监听端口</th>
                  <th className="pb-1 pr-3 font-medium">上游 host:port</th>
                  <th className="pb-1 pr-3 font-medium">状态</th>
                  <th className="pb-1 font-medium">归属</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {human.map((row) => (
                  <MappingRow
                    key={row.mapping.id}
                    row={row}
                    probe={probes[row.mapping.id]}
                    retrying={busy === `retry:${row.mapping.id}`}
                    onRetry={() => void retryMapping(row.mapping.id)}
                  />
                ))}
                {ai.length > 0 && (
                  <tr data-testid="proxy-ai-summary">
                    <td colSpan={4} className="py-2">
                      <button
                        type="button"
                        aria-expanded={aiOpen}
                        aria-controls="proxy-ai-detail"
                        onClick={() => setAiOpen((open) => !open)}
                        data-testid="proxy-ai-toggle"
                        className="flex w-full items-center gap-2 text-left"
                      >
                        <ChevronDown
                          className={cn(
                            'size-3.5 shrink-0 text-text-secondary transition-transform duration-fast',
                            !aiOpen && '-rotate-90',
                          )}
                          aria-hidden="true"
                        />
                        <span className="text-13px text-text">AI 使用</span>
                        <span className="text-11px text-text-muted">{aiSummary(ai)}</span>
                        <span className="ml-auto text-11px text-text-muted">
                          {aiOpen ? '收起' : '展开'}
                        </span>
                      </button>
                      {aiOpen && (
                        <table className="mt-1 w-full border-collapse" id="proxy-ai-detail">
                          <tbody className="divide-y divide-border border-t border-border">
                            {ai.map((row) => (
                              <MappingRow
                                key={row.mapping.id}
                                row={row}
                                probe={probes[row.mapping.id]}
                                retrying={busy === `retry:${row.mapping.id}`}
                                onRetry={() => void retryMapping(row.mapping.id)}
                              />
                            ))}
                          </tbody>
                        </table>
                      )}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {editing && (
          <div
            className="flex flex-col gap-2 rounded border border-border bg-surface-2 p-2"
            data-testid="proxy-edit-panel"
          >
            <p className="text-11px text-text-muted">
              改上游不会自动重启；保存后需手动启动生效。
            </p>
            {rows.map((row) => {
              const draft = drafts[row.mapping.id] ?? { host: '', port: '' }
              const hostId = `proxy-edit-host-label-${row.mapping.id}`
              const portId = `proxy-edit-port-label-${row.mapping.id}`
              return (
                <div key={row.mapping.id} className="grid grid-cols-2 gap-2">
                  <div className="flex flex-col gap-1">
                    <label htmlFor={hostId} className="text-11px text-text-secondary">
                      {row.mapping.id} · 上游 host（监听 {String(row.mapping.listen)}）
                    </label>
                    <Input
                      id={hostId}
                      className="!text-13px"
                      value={draft.host}
                      data-testid={`proxy-edit-host-${row.mapping.id}`}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [row.mapping.id]: { ...draft, host: event.target.value },
                        }))
                      }
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label htmlFor={portId} className="text-11px text-text-secondary">
                      上游端口
                    </label>
                    <Input
                      id={portId}
                      className="!text-13px"
                      inputMode="numeric"
                      value={draft.port}
                      data-testid={`proxy-edit-port-${row.mapping.id}`}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [row.mapping.id]: { ...draft, port: event.target.value },
                        }))
                      }
                    />
                  </div>
                </div>
              )
            })}
            {editError && (
              <p role="alert" className="text-11px text-danger">
                {editError}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                className="!text-13px"
                loading={busy === 'save'}
                onClick={() => void saveEdits()}
                data-testid="proxy-edit-save"
              >
                保存配置
              </Button>
              <Button
                variant="ghost"
                className="!text-13px"
                disabled={busy === 'save'}
                onClick={() => setEditing(false)}
              >
                取消
              </Button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            className="!text-13px"
            leadingIcon={<Play className="size-3.5" aria-hidden="true" />}
            disabled={!canStart || busy !== null}
            loading={busy === 'start'}
            onClick={() => void runPool('start')}
            data-testid="proxy-start"
          >
            启动
          </Button>
          <Button
            variant="secondary"
            className="!text-13px"
            leadingIcon={<Square className="size-3.5" aria-hidden="true" />}
            disabled={!canStop || busy !== null}
            loading={busy === 'stop'}
            onClick={() => void runPool('stop')}
            data-testid="proxy-stop"
          >
            停止
          </Button>
          <Button
            variant="secondary"
            className="!text-13px"
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
            disabled={!canProbe || busy !== null}
            loading={busy === 'probe'}
            onClick={() => void runPool('probe')}
            data-testid="proxy-probe"
          >
            探活
          </Button>
          <Button
            variant="ghost"
            className="!text-13px"
            leadingIcon={<Sliders className="size-3.5" aria-hidden="true" />}
            disabled={!canEdit || busy !== null}
            onClick={openEdit}
            data-testid="proxy-edit"
          >
            编辑配置
          </Button>
          {!rows.length && (
            <span className="text-11px text-text-muted">
              {supervisorReady ? '映射清单未就位，动作不可用' : 'supervisor 未接入，动作不可用'}
            </span>
          )}
        </div>
      </div>
    </li>
  )
}
