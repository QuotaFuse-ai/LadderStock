/**
 * QUOT-564：工作流阶段事件契约（消费 B1/QUOT-562 定义、B2/QUOT-563 复用的统一 schema）。
 *
 * 铁律（QUOT-500 前端不重算）：本模块只做「传输层解析 + 字段 → 文案」翻译，
 * 不产生任何新的健康判定——health / reason / as_of 全部原样来自后端 stage 事件。
 *
 * 信封契约（契约先行，B1/B2 落地前的消费口径）：
 * 事件经 S3 健康通道（QUOT-556）随 `GET /api/supervisor/state` 下发，两种信封都认：
 *   1. `stage_events: StageEvent[]`（每模块每阶段最新一条的快照数组）；
 *   2. `events: [{ type: 'stage', ...StageEvent }]`（S3 类型化事件流，payload 嵌套也认）。
 * 字段缺失 / 枚举非法的条目直接丢弃（无法如实渲染），缺事件的模块显示「等待事件」。
 */

export type WorkflowModuleClass = 'data' | 'cog'
export type WorkflowHealth = 'ok' | 'warn' | 'down'

/** 统一阶段事件 schema（字段名/枚举值即契约，不得各写一套）。 */
export interface WorkflowStageEvent {
  /** 模块标识："collect" / "ingest" / "debate"（P2："recall" / "conclusion"） */
  module: string
  /** data=数据管线（蓝），cog=认知层（紫） */
  module_class: WorkflowModuleClass
  /** 阶段标识，如 collect_script / ingest_watermark / debate_speak */
  stage: string
  health: WorkflowHealth
  /** 白话原因键（普通人能懂），原样展示 */
  reason: string
  /** 该阶段数据/状态的时间戳（ISO8601，本机 Asia/Shanghai） */
  as_of: string
  /** 串行/并行分组或序号，供编排粒子流向与阶段排序 */
  phase: string
}

export interface WorkflowNode {
  id: string
  label: string
  moduleClass: WorkflowModuleClass
  /** 1=本批实线（采集→入库→辩论），2=虚线占位（召回→结论） */
  batch: 1 | 2
}

/**
 * 第一批管线信息架构（QUOT-564 已批准设计）：节点顺序与分批是产品定义，
 * 不是模块注册表——每个节点的健康内容仍全部由后端事件驱动，无事件如实「等待事件」。
 */
export const WORKFLOW_PIPELINE: readonly WorkflowNode[] = [
  { id: 'collect', label: '采集', moduleClass: 'data', batch: 1 },
  { id: 'ingest', label: '入库', moduleClass: 'data', batch: 1 },
  { id: 'debate', label: '辩论', moduleClass: 'cog', batch: 1 },
  { id: 'recall', label: '召回', moduleClass: 'cog', batch: 2 },
  { id: 'conclusion', label: '结论', moduleClass: 'cog', batch: 2 },
]

/** 契约内阶段的白话标签；未知阶段原样显示 stage id（后端新增阶段仍能如实渲染）。 */
const STAGE_LABELS: Record<string, string> = {
  collect_script: '采集脚本',
  collect_process: '采集进程',
  collect_store: '采集库',
  collect_success: '是否成功',
  ingest_watermark: '同步水位',
  ingest_persist: '落库',
  ingest_idempotent: '幂等校验',
  debate_speak: '角色发言',
  debate_clash: '交锋',
  debate_converge: '收敛',
}

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

export interface StageHealthView {
  tone: 'ok' | 'warn' | 'danger'
  label: string
}

/** 健康三态 → 徽标语气/文案（纯翻译；ok=绿 warn=黄 down=红走系统状态令牌）。 */
export function stageHealthView(health: WorkflowHealth): StageHealthView {
  if (health === 'ok') return { tone: 'ok', label: '正常' }
  if (health === 'warn') return { tone: 'warn', label: '降级' }
  return { tone: 'danger', label: '异常' }
}

const MODULE_CLASSES = new Set<string>(['data', 'cog'])
const HEALTHS = new Set<string>(['ok', 'warn', 'down'])

function parseStageEvent(entry: unknown): WorkflowStageEvent | null {
  if (typeof entry !== 'object' || entry === null) return null
  const record = entry as Record<string, unknown>
  const { module, module_class: moduleClass, stage, health, reason, as_of: asOf, phase } = record
  if (typeof module !== 'string' || module.length === 0) return null
  if (typeof moduleClass !== 'string' || !MODULE_CLASSES.has(moduleClass)) return null
  if (typeof stage !== 'string' || stage.length === 0) return null
  if (typeof health !== 'string' || !HEALTHS.has(health)) return null
  if (typeof reason !== 'string') return null
  if (typeof asOf !== 'string' || asOf.length === 0) return null
  if (typeof phase !== 'string') return null
  return {
    module,
    module_class: moduleClass as WorkflowModuleClass,
    stage,
    health: health as WorkflowHealth,
    reason,
    as_of: asOf,
    phase,
  }
}

/**
 * 从 supervisor state 载荷提取 stage 事件（两种信封，见文件头）。
 * 同模块同阶段重复出现时只取第一条；非法条目丢弃，绝不补默认值。
 */
export function extractStageEvents(payload: unknown): WorkflowStageEvent[] {
  if (typeof payload !== 'object' || payload === null) return []
  const record = payload as Record<string, unknown>
  const raw: unknown[] = []
  if (Array.isArray(record.stage_events)) raw.push(...record.stage_events)
  if (Array.isArray(record.events)) {
    for (const item of record.events) {
      if (typeof item !== 'object' || item === null) continue
      const envelope = item as Record<string, unknown>
      if (envelope.type !== 'stage') continue
      const nested = envelope.payload
      raw.push(typeof nested === 'object' && nested !== null ? nested : envelope)
    }
  }
  const seen = new Set<string>()
  const events: WorkflowStageEvent[] = []
  for (const entry of raw) {
    const event = parseStageEvent(entry)
    if (!event) continue
    const key = `${event.module}::${event.stage}`
    if (seen.has(key)) continue
    seen.add(key)
    events.push(event)
  }
  return events
}

/** phase 自然排序：数字段按数值比（"2" < "10"），其余按字典序；纯呈现排序，不涉及健康。 */
const PHASE_CHUNKS = /\d+|\D+/g

function comparePhase(a: string, b: string): number {
  const left = a.match(PHASE_CHUNKS) ?? []
  const right = b.match(PHASE_CHUNKS) ?? []
  for (let i = 0; i < Math.min(left.length, right.length); i += 1) {
    const x = left[i]
    const y = right[i]
    const bothNumeric = /^\d/.test(x) && /^\d/.test(y)
    const diff = bothNumeric ? Number(x) - Number(y) : x.localeCompare(y)
    if (diff !== 0) return diff
  }
  return left.length - right.length
}

/** 某模块的阶段事件，按后端 phase 排序（数字感知；纯呈现排序，不涉及健康）。 */
export function eventsOfModule(events: readonly WorkflowStageEvent[], module: string): WorkflowStageEvent[] {
  return events
    .filter((event) => event.module === module)
    .sort((a, b) => comparePhase(a.phase, b.phase) || a.stage.localeCompare(b.stage))
}

/**
 * 粒子流动的唯一驱动：该模块存在后端报 ok 的阶段事件。
 * ok 是后端给的判定，前端只把它映射成「有成功产出 → 粒子流动」；
 * 全部 warn/down 或无事件 → 静止。前端不从 as_of 推断新鲜度、不聚合模块级健康。
 */
export function hasOkEvent(events: readonly WorkflowStageEvent[]): boolean {
  return events.some((event) => event.health === 'ok')
}