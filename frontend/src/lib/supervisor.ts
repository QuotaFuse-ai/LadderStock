/**
 * QUOT-484 · supervisor 生命周期契约（对接 QUOT-482 的进程账本）。
 *
 * 归属判定**只**来自后端账本的 `origin` 字段，前端不做端口扫描、不猜归属。
 * 482 未落地时字段缺失（undefined），界面如实显示「归属未上报」，不编造。
 */

/** 模块归属：`spawned` 主程序拉起 / `spawned-via-task` 经任务拉起 / `adopted` 外部接管 / `null` 未记账。 */
export type ModuleOrigin = 'spawned' | 'adopted' | 'spawned-via-task' | null

/** 自愈状态（QUOT-482-C）：`healing` 重拉进行中 / `gave_up` 超上限放弃，需人工处理。 */
export interface SelfHealState {
  attempts: number
  max: number
  status: 'ok' | 'healing' | 'gave_up'
}

export type OwnershipLabel = '主程序托管' | '外部接管' | '非托管（观测）' | '归属未上报'

/**
 * 归属展示口径：origin 只认账本字段。
 * `spawned` / `spawned-via-task` 都是主程序拉起的 → 「主程序托管」；
 * `adopted` 是外部已在跑、主程序接管的 → 「外部接管」；
 * `null` 是账本明确记的「未纳管」→ 「非托管（观测）」；
 * 字段缺失（482 未落地）→ 「归属未上报」（不猜，等账本上报）。
 */
export function ownershipLabel(origin: ModuleOrigin | undefined): OwnershipLabel {
  if (origin === 'spawned' || origin === 'spawned-via-task') return '主程序托管'
  if (origin === 'adopted') return '外部接管'
  if (origin === null) return '非托管（观测）'
  return '归属未上报'
}

export function isAdopted(origin: ModuleOrigin | undefined): boolean {
  return origin === 'adopted'
}

/** 停止预览里的一行：模块名 / pid / 端口 / 来源 / 命令行。 */
export interface StopListEntry {
  /** 模块 id（sources.yaml 的 peer id） */
  module: string
  /** 展示名（product） */
  product: string
  pid: number | null
  ports: number[]
  origin: ModuleOrigin
  /** 命令行摘要；后端没有给就显示「—」，前端不拼装 */
  cmdline: string | null
}

/** 停止预览返回：「如果现在停，哪些进程会被停」。 */
export interface StopPreview {
  entries: StopListEntry[]
  /** 预览接口是否真实接入；false 时清单由 board 快照组装，仅含已上报归属的模块 */
  source: 'backend' | 'board-fallback'
}

export const ORIGIN_ROW_LABEL: Record<'spawned' | 'spawned-via-task' | 'adopted' | 'unrecorded', string> = {
  spawned: '本程序启动',
  'spawned-via-task': '本程序启动（任务）',
  adopted: '外部接管',
  unrecorded: '未记账',
}

export function originRowLabel(origin: ModuleOrigin): string {
  return ORIGIN_ROW_LABEL[origin ?? 'unrecorded']
}

/** 「外部接管」的停止必须显式确认（D2：绝不允许无提示批量停）。 */
export type StopPlan =
  | { mode: 'direct'; entries: StopListEntry[] }
  | { mode: 'confirm'; entries: StopListEntry[]; adopted: StopListEntry[] }

/**
 * 决定一条停止路径要不要弹清单：清单里出现任何「外部接管」就必须确认；
 * 全是本程序启动的不弹冗余确认，直接执行（调用方仍需给出可见反馈）。
 */
export function planStop(entries: StopListEntry[]): StopPlan {
  const adopted = entries.filter((entry) => entry.origin === 'adopted')
  if (adopted.length === 0) return { mode: 'direct', entries }
  return { mode: 'confirm', entries, adopted }
}

/** board 快照里可组装清单的最小形状（避免整包依赖 api.ts）。 */
export interface StopPreviewPeerLike {
  id: string
  product: string
  status: string
  origin?: ModuleOrigin
  cmdline?: string | null
  ports: { mcp: number; web: number | null }
  listening: { mcp: Array<{ pid: number | null }>; web: Array<{ pid: number | null }> }
}

/**
 * 预览接口（482）未接入时的回退：从最近一次 board 探测快照组装，
 * **只列运行中且后端已上报 origin 的模块**；origin 缺失就如实记 `null`（未记账），
 * pid 取监听端口的属主，前端不做端口扫描。
 */
export function entriesFromBoard(peers: readonly StopPreviewPeerLike[]): StopListEntry[] {
  return peers
    .filter((peer) => peer.status === 'running' || peer.status === 'starting')
    .map((peer) => ({
      module: peer.id,
      product: peer.product,
      pid: peer.listening.mcp[0]?.pid ?? peer.listening.web[0]?.pid ?? null,
      ports: [peer.ports.mcp, ...(peer.ports.web !== null ? [peer.ports.web] : [])],
      origin: peer.origin ?? null,
      cmdline: peer.cmdline ?? null,
    }))
}
