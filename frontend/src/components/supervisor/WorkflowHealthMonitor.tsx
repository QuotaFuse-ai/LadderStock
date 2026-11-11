import { useMemo, useState, type CSSProperties } from 'react'
import { BookOpen, Database, Download, Flag, MessagesSquare, RefreshCw } from 'lucide-react'

import { api, describeApiError } from '../../api'
import { cn } from '../../lib/cn'
import { MISSING, formatTime } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'
import { useMediaQuery } from '../../lib/mediaQuery'
import { usePolling } from '../../lib/usePolling'
import {
  WORKFLOW_PIPELINE,
  eventsOfModule,
  extractStageEvents,
  hasOkEvent,
  stageHealthView,
  stageLabel,
  type WorkflowNode,
  type WorkflowStageEvent,
} from '../../lib/workflowStages'
import { Badge, Button, SectionCard, SkeletonText, StatusDot } from '../ui'

/**
 * QUOT-564「粒子化工作流健康监控」（设置 · 子模块页顶部）。
 *
 * 第一批管线：采集 → 入库 → 辩论（P1 实线），召回 → 结论（P2 虚线占位）。
 * 蓝=数据管线（module_class=data），紫=认知层（cog）；阶段健康环 ok=绿 / warn=黄 / down=红。
 *
 * 契约先行 + 如实降级：消费 B1（QUOT-562）/ B2（QUOT-563）在 S3 健康通道
 * （QUOT-556，随 /api/supervisor/state 下发）的统一 stage 事件。事件未接入时
 * 渲染管线骨架 +「未接入 / 等待事件」，绝不编造 ok、绝不伪造粒子流动。
 * 前端不重算指标、不推断健康态（QUOT-500）：健康环逐段直渲后端 health，
 * 粒子只在「上游模块存在后端报 ok 的阶段事件」时流动（hasOkEvent 纯映射）。
 *
 * 可达性：节点是原生 button（键盘可达），详情区 aria-live；明暗主题走令牌；
 * prefers-reduced-motion 用静态流向点替代粒子动画；<640px 竖排，320px 不裁切。
 */

const NODE_ICONS: Record<string, typeof Download> = {
  collect: Download,
  ingest: Database,
  debate: MessagesSquare,
  recall: BookOpen,
  conclusion: Flag,
}

const HEALTH_STROKE: Record<WorkflowStageEvent['health'], string> = {
  ok: 'var(--ldst-ok)',
  warn: 'var(--ldst-warn)',
  down: 'var(--ldst-danger)',
}

const CLASS_TEXT: Record<WorkflowNode['moduleClass'], string> = {
  data: 'text-info',
  cog: 'text-chart-series-2',
}

const CLASS_DOT: Record<WorkflowNode['moduleClass'], string> = {
  data: 'bg-info',
  cog: 'bg-chart-series-2',
}

const CLASS_LABEL: Record<WorkflowNode['moduleClass'], string> = {
  data: '数据管线',
  cog: '认知层',
}

/** 阶段健康环：每段弧 = 一个后端阶段事件，颜色直渲 health；无事件 = 中性虚线环。 */
function StageRing({ module, events }: { module: string; events: readonly WorkflowStageEvent[] }) {
  const count = events.length
  const slice = count > 0 ? 100 / count : 0
  const gap = Math.min(4, slice / 2)
  return (
    <svg viewBox="0 0 48 48" className="size-12" aria-hidden="true" data-testid={`wf-ring-${module}`}>
      <circle cx="24" cy="24" r="20" fill="none" stroke="var(--ldst-border)" strokeWidth="3" />
      {count === 0 ? (
        <circle
          cx="24"
          cy="24"
          r="20"
          fill="none"
          stroke="var(--ldst-border-strong)"
          strokeWidth="3"
          pathLength={100}
          strokeDasharray="3 4"
          data-testid={`wf-ring-${module}-empty`}
        />
      ) : (
        events.map((event, index) => (
          <circle
            key={event.stage}
            cx="24"
            cy="24"
            r="20"
            fill="none"
            stroke={HEALTH_STROKE[event.health]}
            strokeWidth="3.5"
            pathLength={100}
            strokeDasharray={`${slice - gap} ${100 - slice + gap}`}
            strokeDashoffset={-(index * slice + gap / 2)}
            transform="rotate(-90 24 24)"
            data-testid={`wf-arc-${module}-${event.stage}`}
            data-health={event.health}
          />
        ))
      )}
    </svg>
  )
}

/**
 * 节点间连接器：<640px 竖直（对齐节点环心 x=36px），≥640px 水平（对齐环心 y=32px）。
 * 粒子仅 active（上游有后端 ok 事件）时渲染；P2 虚线永不流动；
 * reduced-motion 渲染静态流向点（CSS 媒体查询兜底，见 index.css）。
 */
function FlowConnector({
  from,
  to,
  active,
  reducedMotion,
}: {
  from: WorkflowNode
  to: WorkflowNode
  active: boolean
  reducedMotion: boolean
}) {
  const dashed = to.batch === 2
  return (
    <div
      aria-hidden="true"
      data-testid={`wf-edge-${from.id}-${to.id}`}
      data-flow={active ? 'on' : 'off'}
      className="relative ml-9 h-6 w-0.5 -translate-x-1/2 sm:ml-0 sm:mt-8 sm:h-0.5 sm:w-auto sm:flex-1 sm:-translate-y-1/2 sm:translate-x-0"
    >
      <span
        className={cn(
          'absolute inset-y-0 left-0 sm:hidden',
          dashed ? 'border-l-2 border-dashed border-border-strong' : 'w-0.5 bg-border-strong',
        )}
      />
      <span
        className={cn(
          'absolute inset-x-0 top-0 hidden sm:block',
          dashed ? 'border-t-2 border-dashed border-border-strong' : 'h-0.5 bg-border-strong',
        )}
      />
      {active &&
        [0, 1, 2].map((index) => (
          <span
            key={index}
            data-testid={reducedMotion ? 'wf-particle-static' : 'wf-particle'}
            className={cn('absolute size-1.5 rounded-pill', CLASS_DOT[from.moduleClass], reducedMotion ? 'ldst-wf-dot-static' : 'ldst-wf-dot')}
            style={
              reducedMotion
                ? ({ '--wf-pos': `${25 * (index + 1)}%` } as CSSProperties)
                : { animationDelay: `${index * -600}ms` }
            }
          />
        ))}
    </div>
  )
}

function NodeBody({ node, events }: { node: WorkflowNode; events: readonly WorkflowStageEvent[] }) {
  const Icon = NODE_ICONS[node.id] ?? Download
  const placeholder = node.batch === 2
  return (
    <>
      <span className="relative inline-flex size-12 shrink-0 items-center justify-center">
        <StageRing module={node.id} events={events} />
        <Icon className={cn('absolute size-5', placeholder ? 'text-text-muted' : CLASS_TEXT[node.moduleClass])} aria-hidden="true" />
      </span>
      <span className="flex min-w-0 flex-col gap-0.5 sm:items-center">
        <span className="text-body font-medium text-text">{node.label}</span>
        <span className={cn('text-caption', placeholder ? 'text-text-muted' : CLASS_TEXT[node.moduleClass])}>
          {CLASS_LABEL[node.moduleClass]}
        </span>
        {placeholder ? (
          <Badge tone="flat">P2 · 规划中</Badge>
        ) : (
          <span className="text-caption text-text-muted">
            {events.length === 0 ? '等待事件' : `${events.length} 个阶段`}
          </span>
        )}
      </span>
    </>
  )
}

/** 阶段详情：stage 列表 + 白话 reason + as_of，全部原样来自后端事件。 */
function StageDetail({ node, events }: { node: WorkflowNode; events: readonly WorkflowStageEvent[] }) {
  return (
    <div
      id="wf-stage-detail"
      role="region"
      aria-live="polite"
      aria-label={`${node.label}阶段详情`}
      data-testid="wf-stage-detail"
      className="rounded border border-border bg-surface-2 p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-body font-semibold text-text">{node.label}</h3>
        <span className={cn('text-caption', CLASS_TEXT[node.moduleClass])}>{CLASS_LABEL[node.moduleClass]}</span>
        <span className="text-caption text-text-muted">
          {events.length === 0 ? '等待事件' : `${events.length} 个阶段`}
        </span>
      </div>
      {events.length === 0 ? (
        <p className="mt-2 text-caption leading-relaxed text-text-secondary">
          该模块还没有阶段事件：等待后端健康通道发布（B1 采集/入库、B2 辩论）。接入后这里按真实事件显示，当前不显示任何健康态。
        </p>
      ) : (
        <ul className="mt-1 flex flex-col">
          {events.map((event) => {
            const view = stageHealthView(event.health)
            return (
              <li
                key={event.stage}
                data-testid={`wf-stage-${event.module}-${event.stage}`}
                className="flex flex-col gap-1 border-b border-border py-2 last:border-b-0"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-body font-medium text-text">{stageLabel(event.stage)}</span>
                  <Badge tone={view.tone}>{view.label}</Badge>
                  <span className="ldst-num text-caption text-text-muted" title={event.as_of}>
                    {formatTime(event.as_of)}
                  </span>
                </div>
                <p className="break-words text-caption leading-relaxed text-text-secondary">{event.reason || MISSING}</p>
                <p className="break-all text-caption text-text-muted">
                  阶段 <span className="ldst-num">{event.stage}</span> · 分组 <span className="ldst-num">{event.phase || MISSING}</span>
                </p>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export function WorkflowHealthMonitor() {
  const state = useAsync((signal) => api.supervisor.state(signal), [])
  // 与 S5 弹窗同一健康通道：5s 静默轮询，失败指数退避（usePolling），后台标签页跳过。
  usePolling({ intervalMs: 5000, enabled: true, onTick: () => state.refresh() })
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const [selected, setSelected] = useState<string>(WORKFLOW_PIPELINE[0].id)

  const events = useMemo(() => extractStageEvents(state.data), [state.data])
  const selectedNode = WORKFLOW_PIPELINE.find((node) => node.id === selected) ?? WORKFLOW_PIPELINE[0]
  const selectedEvents = eventsOfModule(events, selectedNode.id)

  const initialLoading = state.status === 'loading' && state.data === null && state.failures === 0
  const channelDown = state.data === null && !initialLoading
  const waiting = state.data !== null && events.length === 0
  const errorDetail = state.error ? describeApiError(state.error).detail : null

  return (
    <SectionCard
      title="工作流健康监控"
      subtitle="采集 → 入库 → 辩论 · 蓝=数据管线 / 紫=认知层 · 健康只来自后端阶段事件，前端不重算"
      actions={
        <Button
          variant="secondary"
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          onClick={() => state.reload()}
          loading={state.status === 'loading'}
          data-testid="wf-refresh"
        >
          刷新
        </Button>
      }
    >
      <div className="flex flex-col gap-3" data-testid="wf-monitor">
        {initialLoading ? (
          <SkeletonText lines={4} />
        ) : (
          <>
            {channelDown && (
              <div data-testid="wf-banner-offline">
                <Badge tone="warn">未接入</Badge>
                <p className="mt-1 text-caption leading-relaxed text-text-secondary">
                  阶段事件通道未接入：{errorDetail ?? '本机 API 未连接。'}
                  下方管线按契约渲染骨架，接入后按真实事件点亮；当前不显示任何健康态、不流动粒子。
                </p>
              </div>
            )}
            {waiting && (
              <div data-testid="wf-banner-waiting">
                <Badge tone="info">等待事件</Badge>
                <p className="mt-1 text-caption leading-relaxed text-text-secondary">
                  健康通道已连接，但后端尚未发布 stage 事件（B1 采集/入库、B2 辩论）。接入后自动按真实事件渲染，当前不编造健康态。
                </p>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-text-secondary">
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-pill bg-info" aria-hidden="true" />
                数据管线
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-pill bg-chart-series-2" aria-hidden="true" />
                认知层
              </span>
              <StatusDot tone="ok">正常</StatusDot>
              <StatusDot tone="warn">降级</StatusDot>
              <StatusDot tone="danger">异常</StatusDot>
              <StatusDot tone="idle">未接入</StatusDot>
            </div>

            <ol className="flex flex-col sm:flex-row sm:items-start" data-testid="wf-pipeline">
              {WORKFLOW_PIPELINE.map((node, index) => {
                const nodeEvents = eventsOfModule(events, node.id)
                const isSelected = node.id === selectedNode.id
                const next = WORKFLOW_PIPELINE[index + 1]
                const flowActive = next !== undefined && next.batch === 1 && hasOkEvent(nodeEvents)
                return (
                  <li
                    key={node.id}
                    className={cn('flex flex-col sm:flex-row sm:items-start', next !== undefined && 'sm:flex-1')}
                  >
                    {node.batch === 2 ? (
                      <div
                        data-testid={`wf-node-${node.id}`}
                        className="flex w-full items-center gap-3 rounded border border-dashed border-border-strong bg-surface-2 px-3 py-2 opacity-75 sm:w-24 sm:flex-col sm:gap-1 sm:text-center"
                      >
                        <NodeBody node={node} events={[]} />
                      </div>
                    ) : (
                      <button
                        type="button"
                        aria-pressed={isSelected}
                        aria-controls="wf-stage-detail"
                        onClick={() => setSelected(node.id)}
                        data-testid={`wf-node-${node.id}`}
                        className={cn(
                          'flex w-full items-center gap-3 rounded border px-3 py-2 text-left transition-colors duration-fast',
                          'sm:w-24 sm:flex-col sm:gap-1 sm:text-center',
                          isSelected
                            ? 'border-accent bg-surface-3'
                            : 'border-border bg-surface-2 hover:bg-surface-3',
                        )}
                      >
                        <NodeBody node={node} events={nodeEvents} />
                        <span className="sr-only">
                          {nodeEvents.length === 0
                            ? '暂无阶段事件'
                            : nodeEvents.map((event) => `${stageLabel(event.stage)}${stageHealthView(event.health).label}`).join('，')}
                          ，按回车查看阶段详情
                        </span>
                      </button>
                    )}
                    {next !== undefined && (
                      <FlowConnector from={node} to={next} active={flowActive} reducedMotion={reducedMotion} />
                    )}
                  </li>
                )
              })}
            </ol>

            <StageDetail node={selectedNode} events={selectedEvents} />

            <p className="text-caption text-text-muted">
              每 5 秒自动刷新
              {state.updatedAt !== null && ` · 更新于 ${formatTime(new Date(state.updatedAt).toISOString())}`}
              {state.failures > 0 && state.data !== null && ' · 最近刷新失败，展示上一次成功数据'}
              。粒子只在有后端报「正常」的阶段时流动；P2（召回 → 结论）为占位，本批不接事件。
            </p>
          </>
        )}
      </div>
    </SectionCard>
  )
}