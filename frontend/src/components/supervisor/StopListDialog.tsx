/**
 * QUOT-484 D2：「将停清单」确认弹层。
 *
 * 必须是**看得见的文本列表**（不是隐晦图标）：模块 / pid / 端口 / 来源 / 命令行摘要。
 * 来源用文字标签 + 现有 token 的弱化配色区分，不引入新强调色。
 * Esc 取消（ConfirmDialog 的 useModalChrome），确认需勾选二次确认（防误触全停）。
 */

import { useState } from 'react'

import type { SupervisorOrigin } from '../../api'
import { Badge, Banner, ConfirmDialog } from '../ui'
import { originText, type StopCandidate } from '../../lib/supervisorView'
import { cn } from '../../lib/cn'

function originTone(origin: SupervisorOrigin): 'flat' | 'warn' | 'neutral' {
  if (origin === 'spawned') return 'flat'
  if (origin === 'adopted' || origin === 'spawned-via-task') return 'warn'
  return 'neutral'
}

function portText(ports: number[]): string {
  return ports.length > 0 ? ports.join(' / ') : '—'
}

function StopRow({ candidate }: { candidate: StopCandidate }) {
  const [expanded, setExpanded] = useState(false)
  const long = (candidate.cmdline?.length ?? 0) > 64

  return (
    <li
      className={cn(
        'flex flex-col gap-1 rounded border p-2',
        candidate.external ? 'border-warn bg-warn-soft' : 'border-border bg-surface-2',
      )}
      data-testid={`stop-row-${candidate.module}`}
      data-origin={candidate.origin ?? 'none'}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body font-medium text-text">{candidate.product}</span>
        <span className="ldst-num text-caption text-text-muted">{candidate.module}</span>
        <Badge tone={originTone(candidate.origin)}>{originText(candidate.origin)}</Badge>
      </div>
      <p className="text-caption text-text-secondary">
        PID <span className="ldst-num">{candidate.pid ?? '—'}</span> · 端口{' '}
        <span className="ldst-num">{portText(candidate.ports)}</span>
      </p>
      {candidate.cmdline ? (
        <div className="flex flex-col gap-1">
          <p
            className={cn(
              'ldst-num text-caption text-text-muted',
              !expanded && 'truncate',
              expanded && 'break-all whitespace-pre-wrap',
            )}
            title={candidate.cmdline}
          >
            {candidate.cmdline}
          </p>
          {long && (
            <button
              type="button"
              className="self-start text-caption text-text-secondary underline underline-offset-2 hover:text-text"
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? '收起命令行' : '展开完整命令行'}
            </button>
          )}
        </div>
      ) : (
        <p className="text-caption text-text-muted">命令行摘要：后端本次未提供（只回传 cmdline_hash）</p>
      )}
    </li>
  )
}

export interface StopListDialogProps {
  open: boolean
  candidates: readonly StopCandidate[]
  loading?: boolean
  error?: string | null
  onCancel: () => void
  onConfirm: () => void
}

export function StopListDialog({
  open,
  candidates,
  loading = false,
  error = null,
  onCancel,
  onConfirm,
}: StopListDialogProps) {
  const spawned = candidates.filter((candidate) => !candidate.external).length
  const external = candidates.length - spawned

  return (
    <ConfirmDialog
      open={open}
      title="停止确认：以下进程即将被停止"
      description={
        <>
          共 <span className="ldst-num">{candidates.length}</span> 个模块：本程序启动{' '}
          <span className="ldst-num">{spawned}</span> 个，外部接管{' '}
          <span className="ldst-num">{external}</span> 个。外部接管的进程不是本次会话拉起的，
          取消后保持运行并在面板标「未停止（外部进程）」。
        </>
      }
      confirmLabel="确认停止"
      cancelLabel="取消"
      tone="danger"
      loading={loading}
      requireCheckbox={{
        label:
          external > 0
            ? `我确认停止以上 ${candidates.length} 个进程（含外部接管的 ${external} 个）`
            : `我确认停止以上 ${candidates.length} 个由本程序启动的进程`,
      }}
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      <div className="flex flex-col gap-2" data-testid="stop-list">
        {candidates.length === 0 ? (
          <p className="rounded border border-border bg-surface-2 p-3 text-body text-text-secondary">
            当前没有正在运行的模块，没有需要停止的进程。
          </p>
        ) : (
          <>
            <p className="text-caption text-text-secondary">
              清单按后端账本顺序：模块 / PID / 端口 / 来源 / 命令行
            </p>
            <ul className="flex max-h-[45vh] flex-col gap-2 overflow-auto" aria-label="即将停止的进程清单">
              {candidates.map((candidate) => (
                <StopRow key={candidate.module} candidate={candidate} />
              ))}
            </ul>
          </>
        )}
        {error && (
          <Banner tone="danger" title="停止未执行">
            {error}
          </Banner>
        )}
        <p className="text-caption text-text-muted">
          停止按依赖反向顺序执行；单模块失败不会牵连其它模块。页面不会在没有确认的情况下批量停止进程。
        </p>
      </div>
    </ConfirmDialog>
  )
}
