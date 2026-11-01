import {
  RESEARCH_STAGES,
  STAGE_STATUS_LABEL,
  type ResearchStage,
  type ResearchStageName,
} from '../../api/research'
import { cn } from '../../lib/cn'
import { StatusDot, type DotTone } from '../ui'

/**
 * 六阶段导航（公司画像 → 财务 → 盈利预期 → 估值 → 风险 → 报告合成）。
 *
 * 状态点永远与文字同现；重跑中的段落显示进度百分比，避免「只有颜色在动」。
 * 1280 起为左侧竖排；1024–1279 折成两列 chips，不产生横向滚动。
 */

const TONE: Record<ResearchStage['status'], DotTone> = {
  ok: 'ok',
  degraded: 'warn',
  failed: 'danger',
  pending: 'idle',
}

export interface StageRailProps {
  stages: ResearchStage[]
  active?: ResearchStageName | null
  runningStage?: ResearchStageName | null
  progress?: number
  onSelect: (stage: ResearchStageName) => void
  className?: string
}

export function StageRail({
  stages,
  active = null,
  runningStage = null,
  progress = 0,
  onSelect,
  className,
}: StageRailProps) {
  const byName = new Map(stages.map((stage) => [stage.name, stage]))
  const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100)

  return (
    <nav aria-label="研究阶段" className={cn('grid grid-cols-2 gap-1 desk:grid-cols-1', className)}>
      {RESEARCH_STAGES.map((name, index) => {
        const stage = byName.get(name)
        const status = stage?.status ?? 'pending'
        const title = stage?.title ?? name
        const isActive = active === name
        const isRunning = runningStage === name
        return (
          <button
            key={name}
            type="button"
            onClick={() => onSelect(name)}
            aria-current={isActive ? 'step' : undefined}
            className={cn(
              'flex min-h-control flex-col justify-center gap-half rounded px-2 py-1 text-left transition-colors duration-fast ease-ldst',
              isActive ? 'bg-surface-3' : 'hover:bg-surface-3',
            )}
          >
            <span className="flex min-w-0 items-center gap-1">
              <span
                className={cn(
                  'ldst-num shrink-0 text-caption',
                  // 激活项底色是 surface-3，序号若继续用 text-text-muted 会掉到 3:1 以下；
                  // 层级改由「正文是否加粗 + 是否提亮」承担。
                  isActive ? 'text-text-secondary' : 'text-text-muted',
                )}
              >
                {String(index + 1).padStart(2, '0')}
              </span>
              <span
                className={cn(
                  'truncate text-body',
                  isActive ? 'font-medium text-text' : 'text-text-secondary',
                )}
              >
                {title}
              </span>
            </span>
            <StatusDot tone={isRunning ? 'accent' : TONE[status]} pulse={isRunning}>
              {isRunning
                ? `重跑中 ${pct}%`
                : status === 'degraded'
                  ? '降级'
                  : STAGE_STATUS_LABEL[status]}
            </StatusDot>
            {isRunning && (
              <span
                className="mt-1 block h-1 w-full overflow-hidden rounded-pill bg-surface-3"
                aria-hidden="true"
              >
                <span className="block h-full bg-accent transition-all duration-fast ease-ldst" style={{ width: `${pct}%` }} />
              </span>
            )}
          </button>
        )
      })}
    </nav>
  )
}
