import type { ReactNode } from 'react'
import { Database } from 'lucide-react'

import { Button, Card, EmptyState } from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING } from '../../lib/format'
import { MISSING_CELL_TITLE } from './heat'

/**
 * 本轨（`screens/industry/**`）内共享的键值清单件。
 *
 * 粗糙点清单 M-2 要求把它下沉到 `components/ui`（同一概念此前四处四写法：矩阵用 flex、
 * 详情用三种 `dl`）。组件层不在本轨边界内，先在轨内收敛为一份；`ui` 落地同名件后删除本文件。
 *
 * 约束：左列说明文字、右列数值 —— 右列一律 `text-right + shrink-0 + truncate + title`，
 * 长值（锚点、代码列表）绝不换行把行高顶开。
 */

export interface KeyValueItem {
  key: string
  label: ReactNode
  value: ReactNode
  /** 右列的完整文本，用于 `title`（value 是 ReactNode 时 truncate 后需要它找回） */
  title?: string
  numeric?: boolean
}

export function KeyValueList({
  items,
  className,
}: {
  items: ReadonlyArray<KeyValueItem>
  className?: string
}) {
  return (
    <dl className={cn('flex flex-col gap-1 text-caption leading-body', className)}>
      {items.map((item) => (
        <div key={item.key} className="flex min-w-0 items-baseline justify-between gap-3">
          <dt className="min-w-0 shrink-0 text-text-secondary">{item.label}</dt>
          <dd
            title={item.title}
            className={cn(
              'min-w-0 shrink truncate text-right text-text',
              item.numeric !== false && 'ldst-num',
            )}
          >
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** 分隔块：卡内「上方正文 + 下方补充说明」的同源写法（清单 §7.3 手抄三处的收口）。 */
export function CardNote({ children }: { children: ReactNode }) {
  return <p className="mt-2 border-t border-border pt-2 text-caption leading-body text-text-muted">{children}</p>
}

/**
 * 「接口未接入」空态（C-17）：矩阵与详情此前各写一份、按钮一个叫刷新一个叫重试。
 *
 * 清单 M-2 要求它最终下沉 `components/ui`（全仓第三份在 `/signals`）；组件层不在本轨边界内，
 * 先在产业两屏收敛为一份。
 */
export function EndpointMissingEmpty({
  endpoint,
  onReload,
  extra,
}: {
  endpoint: string
  onReload: () => void
  extra?: ReactNode
}) {
  return (
    <Card>
      <EmptyState
        icon={<Database aria-hidden="true" />}
        title="接口未接入"
        description={
          <>
            页面已按 <code className="ldst-num">{endpoint}</code> 契约实现；后端提供该只读接口后自动接入，
            当前不伪造任何数据。
          </>
        }
        action={
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button variant="secondary" onClick={onReload}>
              刷新
            </Button>
            {extra}
          </div>
        }
      />
    </Card>
  )
}

/**
 * 缺失说明的固定写法：`—`（图形半边）+ 中文原因（文字半边）。
 *
 * §9「状态不靠色」要求图形与文字同现；矩阵单元格与详情卡此前一个有 `—` 一个没有，
 * 同一枚 `MISSING_CELL_TITLE` 出现两种视觉重量。
 */
export function MissingNote({ label, reason }: { label: string; reason?: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 text-caption text-text-muted"
      title={`${label}：${MISSING_CELL_TITLE}${reason ? `（${reason}）` : ''}`}
    >
      <span aria-hidden="true">{MISSING}</span>
      <span>{MISSING_CELL_TITLE}</span>
    </span>
  )
}
