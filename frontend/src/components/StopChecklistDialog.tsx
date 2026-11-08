import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, ShieldAlert } from 'lucide-react'

import { Badge, Button, Drawer, EmptyState } from './ui'
import { ORIGIN_ROW_LABEL, originRowLabel, type StopListEntry } from '../lib/supervisor'
import { MISSING } from '../lib/format'
import { cn } from '../lib/cn'

/**
 * QUOT-484 · D2「将停清单」确认弹层。
 *
 * 用户点「退出全停」或停止一个外部接管模块时弹出：逐行列出模块名 / pid / 端口 /
 * 进程来源 / 命令行摘要（过长可展开）。外部接管与本程序启动的行视觉可区分（文字标签 +
 * 弱化配色，沿用现有 token）。确认按钮需要二次确认（勾选后才能点），避免误触全停；
 * Esc 关闭弹层 = 取消整条停止路径。
 *
 * 数据来源：后端 supervisor 的停止预览（482 账本）。前端不拼装、不猜 pid。
 */

function EntryRow({ entry }: { entry: StopListEntry }) {
  const [expanded, setExpanded] = useState(false)
  const adopted = entry.origin === 'adopted'
  const cmdline = entry.cmdline ?? ''
  const long = cmdline.length > 64

  return (
    <li
      className={cn(
        'rounded border border-border p-2',
        adopted && 'border-warn bg-warn-soft',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {adopted ? (
          <Badge tone="warn">{ORIGIN_ROW_LABEL.adopted}</Badge>
        ) : (
          <Badge tone="info">{originRowLabel(entry.origin)}</Badge>
        )}
        <span className="text-body font-medium text-text">{entry.product}</span>
        <span className="ldst-num text-caption text-text-secondary">
          pid {entry.pid ?? MISSING}
        </span>
        <span className="ldst-num text-caption text-text-secondary">
          {entry.ports.length > 0 ? `端口 ${entry.ports.join(' / ')}` : '端口 —'}
        </span>
        {long && (
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            className="ml-auto inline-flex items-center gap-0.5 rounded px-1 text-caption text-text-secondary transition-colors duration-fast hover:bg-surface-3 hover:text-text"
            aria-expanded={expanded}
          >
            {expanded ? (
              <ChevronDown className="size-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden="true" />
            )}
            命令行
          </button>
        )}
      </div>
      <p
        className={cn(
          'ldst-num mt-1 break-all text-caption text-text-muted',
          !expanded && long && 'truncate',
        )}
        title={cmdline || undefined}
      >
        {cmdline || `${MISSING}（后端未返回命令行）`}
      </p>
    </li>
  )
}

export interface StopChecklistDialogProps {
  open: boolean
  /** 弹层标题，如「退出前将停止」/「停止 QuotaFuse Wire」 */
  title: string
  subtitle?: string
  entries: StopListEntry[]
  /** 预览接口未接入（board 回退组装）时如实告知，不冒充后端预览 */
  fallbackNote?: string
  loading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export default function StopChecklistDialog({
  open,
  title,
  subtitle,
  entries,
  fallbackNote,
  loading = false,
  onConfirm,
  onCancel,
}: StopChecklistDialogProps) {
  const [armed, setArmed] = useState(false)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    if (open) {
      setArmed(false)
      setChecked(false)
    }
  }, [open])

  const adoptedCount = entries.filter((entry) => entry.origin === 'adopted').length

  return (
    <Drawer
      open={open}
      onClose={onCancel}
      width="lg"
      title={title}
      subtitle={subtitle ?? '确认后将逐行停止；未勾选确认不会停止任何进程'}
      footer={
        <>
          {fallbackNote && (
            <p className="mr-auto min-w-0 flex-1 truncate text-caption text-text-muted" title={fallbackNote}>
              {fallbackNote}
            </p>
          )}
          {!armed ? (
            <>
              <Button variant="ghost" onClick={onCancel} disabled={loading}>
                取消
              </Button>
              <Button
                variant="danger"
                onClick={() => setArmed(true)}
                loading={loading}
                disabled={entries.length === 0}
                disabledReason={entries.length === 0 ? '清单为空，没有要停止的进程' : undefined}
              >
                确认停止（{entries.length}）
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => setArmed(false)} disabled={loading}>
                返回
              </Button>
              <Button variant="danger" onClick={onConfirm} loading={loading} disabled={!checked}>
                仍要停止
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {entries.length === 0 ? (
          <EmptyState
            title="没有要停止的进程"
            description="清单为空：可能是全部模块已停止，或后端没有上报任何归属。"
          />
        ) : (
          <>
            {adoptedCount > 0 && (
              <p className="flex items-start gap-1.5 rounded border border-border bg-surface-2 p-2 text-caption text-text-secondary">
                <ShieldAlert className="mt-half size-3.5 shrink-0 text-warn" aria-hidden="true" />
                其中 <span className="ldst-num">{adoptedCount}</span> 个是外部接管进程（不是主程序拉起的）。
                取消后它们会保留在面板上，并带「未停止（外部进程）」标记。
              </p>
            )}
            <ul className="flex flex-col gap-2">
              {entries.map((entry) => (
                <EntryRow key={`${entry.module}-${entry.pid ?? 'na'}`} entry={entry} />
              ))}
            </ul>
          </>
        )}

        {armed && (
          <div className="rounded border border-danger bg-danger-soft p-3">
            <label className="flex items-start gap-2 text-body text-text">
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) => setChecked(event.target.checked)}
                className="mt-half size-4 shrink-0 accent-accent-solid"
              />
              <span>
                我确认要停止清单里的{' '}
                <span className="ldst-num">{entries.length}</span> 个进程
                {adoptedCount > 0 && (
                  <>
                    （含 <span className="ldst-num">{adoptedCount}</span> 个外部接管进程，停止后需要手动重新启动）
                  </>
                )}
                。
              </span>
            </label>
          </div>
        )}
      </div>
    </Drawer>
  )
}
