import { Badge, type BadgeTone } from '../../components/ui'

/**
 * `source_status` 徽标（UI-SPEC §5.2 / §5.3）。
 *
 * 屏侧临时件：真源是 QUOT-319-A 正在下沉的 `components/ui/SourceBadges.tsx`（§6 表）。
 * 本文件的 props 与 §6 一致（`{status, detail}`），A 合入后把各屏的 import 换成
 * `../../components/ui` 并删掉本文件即可，调用点不改。
 *
 * 只呈现接口给的 `data_source_status`，不做任何推断：`freshness` 不在 radar / research
 * 的载荷契约里，缺字段就不画徽标（§8「不猜」）。
 */

export type SourceBadgeStatus = 'ok' | 'degraded' | 'partial' | 'missing'

const LABEL: Record<SourceBadgeStatus, string> = {
  ok: '数据完整',
  degraded: '已降级',
  partial: '部分缺失',
  missing: '未取到',
}

/** §5.2：`degraded` 黄、`partial` 橙红；`missing`（数据集整块没有）不轻于 partial。 */
const TONE: Record<SourceBadgeStatus, BadgeTone> = {
  ok: 'ok',
  degraded: 'warn',
  partial: 'danger',
  missing: 'danger',
}

/** 颜色不是唯一通道（§5.3）：形状 + 文字。 */
const GLYPH: Record<SourceBadgeStatus, string> = {
  ok: '●',
  degraded: '◐',
  partial: '◑',
  missing: '○',
}

/** 只认这四个值；后端给别的写法时不猜档位，交给调用点的兜底。 */
export function asSourceBadgeStatus(value: string | null | undefined): SourceBadgeStatus | null {
  return value === 'ok' || value === 'degraded' || value === 'partial' || value === 'missing' ? value : null
}

export function SourceStatusBadge({ status, detail }: { status: string | null | undefined; detail?: string | null }) {
  const key = asSourceBadgeStatus(status)
  if (!key) return null
  return (
    <Badge tone={TONE[key]} title={detail ? `${LABEL[key]}：${detail}` : LABEL[key]}>
      {GLYPH[key]} {LABEL[key]}
    </Badge>
  )
}
