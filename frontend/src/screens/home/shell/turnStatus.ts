/**
 * 对话等待态（QUOT-585）：L1 阶段指示 + L2 已用时跳动。
 *
 * 帧源形状按 QUOT-581 的 `event: status` JSON 写死（并行落地，前端不等待）：
 *   { "stage": "received" | "reading_data" | "computing" | "generating",
 *     "label": string, "seq": number, "elapsed_ms": number, "heartbeat"?: boolean }
 *
 * 接口设计为**可注入**：`TurnStatusFeed` 是一个「注册监听 → 返回注销」的最小面，
 * 真后端落地前由 mock 帧源（测试里发假帧）驱动；没有 status 帧时本地计时器兜底，
 * 每 100ms 刷新「已用时」，任何 2 秒窗口内画面都有可见变化（E5 红线）。
 */

/** 阶段序号：received=1 / reading_data=2 / computing=3 / generating=4 */
export const STATUS_STAGES = ['received', 'reading_data', 'computing', 'generating'] as const
export type StatusStage = (typeof STATUS_STAGES)[number]

/** 后端 status 帧原样形状（QUOT-581 契约） */
export interface StatusFrame {
  stage: StatusStage | string
  label: string
  seq: number
  elapsed_ms: number
  heartbeat?: boolean
}

export interface TurnStatusFeed {
  /** 订阅当前轮的状态帧；返回注销函数 */
  subscribe(listener: (frame: StatusFrame) => void): () => void
}

/** 本地兜底计时步长：100ms —— 数字持续跳动，reduced-motion 下也"在动" */
export const TICK_INTERVAL_MS = 100

export function stageNumber(stage: string | undefined): number {
  const index = STATUS_STAGES.indexOf((stage ?? '') as StatusStage)
  return index >= 0 ? index + 1 : 1
}

/** 展示态：阶段文案 / 阶段序号 / 基准耗时（由帧给，本地在其上累加） */
export interface StatusDisplay {
  label: string
  stageNo: number
  stageTotal: number
  /** 最近一帧的 seq（用于乱序丢弃；无帧时为 -1） */
  seq: number
  baseElapsedMs: number
  /** 基准时刻（performance.now 口径），本地计时器用 now - baseAt 推已用时 */
  baseAt: number
}

const DEFAULT_LABELS: Record<string, string> = {
  received: '已收到，正在处理',
  reading_data: '正在取数',
  computing: '正在计算',
  generating: '正在生成',
}

export function toDisplay(frame: StatusFrame, now: number): StatusDisplay {
  return {
    label: frame.label || DEFAULT_LABELS[frame.stage] || '已收到，正在处理',
    stageNo: stageNumber(frame.stage),
    stageTotal: STATUS_STAGES.length,
    seq: frame.seq,
    baseElapsedMs: Math.max(0, frame.elapsed_ms),
    baseAt: now,
  }
}

/** 无帧兜底：已用时从发送时刻起算 */
export function fallbackDisplay(startedAt: number): StatusDisplay {
  return {
    label: '已收到，正在处理',
    stageNo: 1,
    stageTotal: STATUS_STAGES.length,
    seq: -1,
    baseElapsedMs: 0,
    baseAt: startedAt,
  }
}

/**
 * 帧归并：heartbeat 帧不改文案与阶段，只刷新已用时基准；普通帧整体覆盖。
 * 崩序（seq 小于已见最大值）丢弃，防乱序把阶段打回去。
 */
export function mergeFrame(
  prev: StatusDisplay | null,
  frame: StatusFrame,
  now: number,
): StatusDisplay {
  if (prev && frame.seq < prev.seq) return prev
  if (frame.heartbeat && prev) {
    return { ...prev, baseElapsedMs: Math.max(0, frame.elapsed_ms), baseAt: now }
  }
  return toDisplay(frame, now)
}
