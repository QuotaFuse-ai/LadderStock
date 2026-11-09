/**
 * QUOT-484：supervisor 视图口径（纯函数，便于单测）。
 *
 * 铁律：归属判定只用后端 `GET /api/supervisor/state` 的 `origin` / `ownership`
 * （482-A 账本 + cmdline 指纹），**前端不扫端口、不推断**。
 * 这里只做「字段 → 文案 / 语气」的翻译，不产生任何新的判定。
 */

import type {
  SelfHealInfo,
  SupervisorModule,
  SupervisorOrigin,
  SupervisorOwnership,
  SupervisorStatePayload,
} from '../api'

export type BadgeTone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info' | 'flat'

export interface OwnershipView {
  label: '主程序托管' | '外部接管' | '非托管（观测）'
  tone: BadgeTone
  hint: string
}

export interface SelfHealView {
  label: string
  tone: BadgeTone
  /** true = 需要人工处理的失败终态（卡片标红） */
  failed: boolean
}

export interface StopCandidate {
  module: string
  product: string
  pid: number | null
  ports: number[]
  origin: SupervisorOrigin
  cmdline: string | null
  /** 不是本任 supervisor 亲手拉起（adopted / spawned-via-task）：D2 要求逐个确认 */
  external: boolean
}

const RUNNING_STATES = new Set(['starting', 'running', 'stopping', 'failed'])

export function moduleKey(id: string): string {
  return id.replace(/-/g, '_')
}

export function findModule(
  state: SupervisorStatePayload | null | undefined,
  peerId: string,
): SupervisorModule | null {
  if (!state) return null
  const key = moduleKey(peerId)
  return state.modules.find((item) => moduleKey(item.module) === key) ?? null
}

/** 卡片归属：只翻译后端 `origin` + `ownership`，没有推断分支。 */
export function ownershipView(module: SupervisorModule | null): OwnershipView {
  if (!module) {
    return {
      label: '非托管（观测）',
      tone: 'neutral',
      hint: '后端账本没有这个模块的记录：只观测，不代管',
    }
  }
  if (module.ownership === 'external') {
    // 后端 cmdline 指纹对不上（cmdline_mismatch / port_taken_by_unknown）：
    // 归属结论优先于账本里可能过期的 origin。
    return {
      label: '非托管（观测）',
      tone: 'neutral',
      hint: '账本无法证明是本控制面拉起的进程：只观测，不代为停止',
    }
  }
  if (module.origin === 'adopted') {
    return {
      label: '外部接管',
      tone: 'warn',
      hint: '上一任主程序留下的进程：账本已对账确认，可托管但停止前需要确认',
    }
  }
  if (module.origin === 'spawned-via-task') {
    return {
      label: '外部接管',
      tone: 'warn',
      hint: '由 SYSTEM 计划任务垫片拉起（账本 task.origin）：停止前需要确认',
    }
  }
  if (module.origin === 'spawned' || module.ownership === 'managed') {
    return {
      label: '主程序托管',
      tone: 'ok',
      hint: '本任 supervisor 拉起且账本 PID + 命令行指纹匹配',
    }
  }
  return {
    label: '非托管（观测）',
    tone: 'neutral',
    hint: '账本无法证明是本控制面拉起的进程：只观测，不代为停止',
  }
}

/** 自愈状态（D5）：ok / healing（第 N/M 次）/ gave_up（需人工处理）。 */
export function selfHealView(module: SupervisorModule | null): SelfHealView | null {
  if (!module) return null
  const heal: SelfHealInfo | undefined = module.self_heal
  const status = heal?.status
  if (module.health === 'needs_human' || module.state === 'needs_human' || status === 'gave_up') {
    return { label: '需人工处理（已停止自愈）', tone: 'danger', failed: true }
  }
  if (status === 'healing' || module.health === 'restarting' || module.state === 'restarting') {
    const attempts = heal?.attempts ?? module.restarts_5min
    const max = heal?.max ?? 3
    return { label: `正在重拉（第 ${attempts}/${max} 次）`, tone: 'warn', failed: false }
  }
  return null
}

function originLabel(origin: SupervisorOrigin): string {
  if (origin === 'spawned') return '本程序启动'
  if (origin === 'adopted') return '外部接管'
  if (origin === 'spawned-via-task') return '计划任务拉起（外部）'
  return '非托管（观测）'
}

export function originText(origin: SupervisorOrigin): string {
  return originLabel(origin)
}

export function isExternalOrigin(origin: SupervisorOrigin): boolean {
  return origin === 'adopted' || origin === 'spawned-via-task'
}

/**
 * 「将停清单」：列出此刻真的会被停的模块。
 *
 * 只收录账本里仍有 pid、且处于 starting/running/stopping/failed 的模块——
 * 已 stopped 的不进清单（停它没有动作，列出来只会制造噪音）。
 * `external` 标记来自账本 `origin`，不是端口扫描。
 */
export function buildStopCandidates(
  state: SupervisorStatePayload | null | undefined,
  productOf: (module: string) => string,
): StopCandidate[] {
  if (!state) return []
  return state.modules
    .filter((module) => {
      if (module.pid === null || !RUNNING_STATES.has(module.state)) return false
      // 账本证明不了是本控制面拉起的（external）不进清单：引擎不会停它，
      // 列进「将停清单」就是谎报。
      if (module.ownership) return module.ownership === 'managed'
      return module.origin !== null
    })
    .map((module) => ({
      module: module.module,
      product: productOf(module.module),
      pid: module.pid,
      ports: module.ports,
      origin: module.origin,
      cmdline: module.cmdline ?? null,
      external: isExternalOrigin(module.origin),
    }))
}

/** 是否必须弹确认：只有「外部接管 / 计划任务」这类不是本任拉起的进程才需要 D2 确认。 */
export function needsConfirm(candidates: readonly StopCandidate[]): boolean {
  return candidates.some((candidate) => candidate.external)
}

export function supervisorTone(ownership: SupervisorOwnership | undefined): BadgeTone {
  return ownership === 'external' ? 'neutral' : 'ok'
}
