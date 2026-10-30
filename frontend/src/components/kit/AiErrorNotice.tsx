import { useState } from 'react'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  FileWarning,
  Gauge,
  Scissors,
  WifiOff,
  XCircle,
} from 'lucide-react'

import type { AiFailureKind } from '../../api'
import { cn } from '../../lib/cn'
import { Badge, Button } from '../ui'

/**
 * AI 失败面板（UI-SPEC §5.4 / §6）：四类失败 + `internal` 兜底 + 段内 `degraded`。
 *
 * 组件只接收数据与回调，不发请求、不读 store；颜色全部来自 token。
 * `truncated` 不出「重试」按钮：原样重试必然再截断，先调参数再重跑（与后端
 * `error_taxonomy._RETRYABLE` 一致）。
 */

export type AiNoticeKind = AiFailureKind | 'degraded'

type ActionKey = 'retry' | 'switchModel' | 'status' | 'export'

interface KindMeta {
  title: string
  tone: 'danger' | 'warn'
  retryable: boolean
  /** 按优先级排列；最终只渲染调用方真正传了回调的动作 */
  actions: ActionKey[]
  hint?: string
  icon: typeof WifiOff
}

const META: Record<AiNoticeKind, KindMeta> = {
  network: {
    title: '网络请求失败',
    tone: 'danger',
    retryable: true,
    actions: ['retry', 'switchModel', 'status'],
    icon: WifiOff,
  },
  quota: {
    title: '额度或频率受限',
    tone: 'warn',
    retryable: true,
    actions: ['switchModel', 'retry', 'status'],
    icon: Gauge,
  },
  format: {
    title: '结构化输出解析失败',
    tone: 'danger',
    retryable: true,
    actions: ['retry', 'switchModel', 'status'],
    icon: FileWarning,
  },
  truncated: {
    title: '输出被截断',
    tone: 'warn',
    retryable: false,
    actions: ['status', 'export'],
    hint: '原样重试仍会被截断：先提高 max_tokens（改配置后重启）或缩小该段输入，再重跑该段。',
    icon: Scissors,
  },
  internal: {
    title: 'AI 调用未归类失败',
    tone: 'danger',
    retryable: false,
    actions: ['status'],
    hint: '检查环境变量中的模型密钥与权限；密钥只走环境变量，不写入配置。',
    icon: XCircle,
  },
  degraded: {
    title: 'AI 叙述降级',
    tone: 'warn',
    retryable: false,
    actions: ['status'],
    hint: '已用备用路径 / 本地回放完成该段；叙述可能比正常路径简略，程序指标不受影响。',
    icon: AlertTriangle,
  },
}

export interface AiErrorNoticeProps {
  kind: AiNoticeKind
  /** 段名（如「资金」）；段内失败时给，页面级汇总时省略 */
  section?: string
  /** 后端 `message`（已脱敏，可能带类别标题） */
  message?: string
  /** 后端 `detail`；缺省时从 `message` 去掉类别标题后展示 */
  detail?: string
  /** 后端给出的可行动建议：按钮接不上时作为文字兜底 */
  action?: string
  /** 该段上一次成功结果仍在展示（§5.4「已保留上一次结果」） */
  keptPrevious?: boolean
  /** 覆盖重试按钮文案（例如复盘页的「重新取数」） */
  retryLabel?: string
  onRetry?: () => void
  onSwitchModel?: () => void
  onOpenStatus?: () => void
  onExport?: () => void
  className?: string
  id?: string
}

const TONE_BOX: Record<KindMeta['tone'], string> = {
  danger: 'border-danger bg-danger-soft',
  warn: 'border-warn bg-warn-soft',
}

const TONE_TEXT: Record<KindMeta['tone'], string> = {
  danger: 'text-danger',
  warn: 'text-warn',
}

const ACTION_LABEL: Record<ActionKey, string> = {
  retry: '重试该段',
  switchModel: '切换备用模型',
  status: '查看 AI 状态',
  export: '导出 Markdown',
}

/** `message` 已带「类别：原因」前缀时只留原因，避免与标题重复。 */
function detailOf(kind: AiNoticeKind, message?: string, detail?: string): string {
  const raw = (detail ?? message ?? '').trim()
  const prefix = `${META[kind].title}：`
  return raw.startsWith(prefix) ? raw.slice(prefix.length).trim() : raw
}

export function AiErrorNotice({
  kind,
  section,
  message,
  detail,
  action,
  keptPrevious = false,
  retryLabel,
  onRetry,
  onSwitchModel,
  onOpenStatus,
  onExport,
  className,
  id,
}: AiErrorNoticeProps) {
  const [expanded, setExpanded] = useState(false)
  const meta = META[kind]
  const Icon = meta.icon
  const body = detailOf(kind, message, detail)

  const handlers: Record<ActionKey, (() => void) | undefined> = {
    // 截断 / 未归类 / 降级不接受「原样重试」，即使调用方传了 onRetry 也不渲染
    retry: meta.retryable ? onRetry : undefined,
    switchModel: onSwitchModel,
    status: onOpenStatus,
    export: onExport,
  }
  const actions = meta.actions.filter((key) => handlers[key])
  const labels: Record<ActionKey, string> = {
    ...ACTION_LABEL,
    retry: retryLabel ?? ACTION_LABEL.retry,
  }

  // 长摘要默认折叠两行；短摘要不出现「查看详情」
  const canExpand = body.length > 80

  return (
    <div
      id={id}
      role={meta.tone === 'danger' ? 'alert' : 'status'}
      className={cn('rounded border px-3 py-2 text-body leading-body', TONE_BOX[meta.tone], className)}
    >
      <div className="flex items-start gap-2">
        <Icon className={cn('mt-0.5 size-4 shrink-0', TONE_TEXT[meta.tone])} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className={cn('font-medium', TONE_TEXT[meta.tone])}>{meta.title}</p>
            {section && <Badge tone="neutral">{section}</Badge>}
            {keptPrevious && <Badge tone="info">已保留上一次结果</Badge>}
          </div>

          {body && (
            <p
              className={cn(
                'mt-1 whitespace-pre-line break-words text-caption leading-body text-text-secondary',
                !expanded && 'line-clamp-2',
              )}
            >
              {body}
            </p>
          )}
          {canExpand && (
            <button
              type="button"
              className="mt-1 inline-flex items-center gap-0.5 text-caption text-text-muted underline-offset-2 hover:text-text-secondary hover:underline"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? (
                <ChevronUp className="size-3" aria-hidden="true" />
              ) : (
                <ChevronDown className="size-3" aria-hidden="true" />
              )}
              {expanded ? '收起详情' : '查看详情'}
            </button>
          )}

          {meta.hint && <p className="mt-1 text-caption text-text-muted">{meta.hint}</p>}

          {actions.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {actions.map((key, index) => (
                <Button
                  key={key}
                  size="md"
                  variant={index === 0 ? 'secondary' : 'ghost'}
                  onClick={handlers[key]}
                >
                  {labels[key]}
                </Button>
              ))}
            </div>
          ) : (
            action && <p className="mt-1 text-caption text-text-muted">建议：{action}</p>
          )}
        </div>
      </div>
    </div>
  )
}
