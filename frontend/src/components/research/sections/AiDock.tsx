import { useState } from 'react'
import { Sparkles } from 'lucide-react'

import { Badge, Button, Drawer, EmptyState } from '../../ui'

/**
 * 页级研究助手抽屉（QUOT-448 只落挂载点）。
 *
 * props 契约（后续 QUOT-454 只改本文件）：
 * - `code` / `name`：当前页上下文，抽屉标题按参考库写「研究助手 · 个股深挖 · {名称}」；
 * - `scope`：页级上下文摘要（`tab` / `date`），接入后随问答一起发送。
 *
 * 参考库里这是 `Layout` 级的 `FinanceAiDock`，绑定当前页上下文。本单只给一个
 * 可开合的入口和一个明确的「待接入」空抽屉，不接模型、不发请求。
 */

export interface ResearchAiDockProps {
  code: string | null
  name: string | null
  scope?: { tab: string; date: string } | null
  className?: string
}

export function ResearchAiDock({ code, name, scope, className }: ResearchAiDockProps) {
  const [open, setOpen] = useState(false)
  const title = `研究助手 · 个股深挖${name ? ` · ${name}` : code ? ` · ${code}` : ''}`

  return (
    <div className={className}>
      <Button
        variant="secondary"
        leadingIcon={<Sparkles className="size-3.5" aria-hidden="true" />}
        onClick={() => setOpen(true)}
      >
        研究助手
      </Button>
      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title={title}
        subtitle={scope ? `${scope.tab} · ${scope.date} · 页级上下文` : '页级上下文'}
        footer={
          <div className="flex items-center justify-between gap-2">
            <Badge tone="warn">待接入</Badge>
            <Button variant="primary" disabled title="研究助手接入后再启用">
              发送
            </Button>
          </div>
        }
      >
        <EmptyState
          compact
          icon={<Sparkles aria-hidden="true" />}
          title="研究助手尚未接入"
          description="快捷问题、页级上下文绑定与对话由 QUOT-454 接入；接入前这里不会调用任何模型，也不会上传页面数据。"
        />
      </Drawer>
    </div>
  )
}
