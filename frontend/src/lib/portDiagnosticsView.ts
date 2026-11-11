/**
 * QUOT-548：端口诊断视图口径（纯函数，便于单测）。
 *
 * 铁律：占用分类 / 健康 / 可修复性全部来自后端 `GET /api/control/ports`
 * （QUOT-547 `classify_port_owner`，DEC-079 origin 口径、失败关闭）。
 * 前端不扫端口、不重算指标、不推断归属；这里只做「字段 → 文案 / 语气」的翻译，
 * 以及安全按钮的展示策略（与后端 `heal_ports` 的动作优先级一一对应）。
 */

import type { PeerPortDiagnosis, PortClass, PortDiagnosisEntry } from '../api'

export type PortClassTone = 'ok' | 'warn' | 'danger' | 'info' | 'flat'

export interface PortClassView {
  label: string
  tone: PortClassTone
  hint: string
}

/** 四分类的普通用户标签：已接管 / 自有残留 / 外来进程 / 空闲（派单正文口径）。 */
const CLASS_VIEW: Record<PortClass, PortClassView> = {
  own_child: {
    label: '自有残留',
    tone: 'info',
    hint: '本程序启动并记录的进程：可以安全停止并重拉',
  },
  adopted_peer: {
    label: '已接管',
    tone: 'ok',
    hint: '你自己启动、已被平台接管的模块：只观测与健康复检，绝不终止 / 重启',
  },
  foreign: {
    label: '外来进程',
    tone: 'warn',
    hint: '与本程序无关的进程占用：只报告与引导，绝不终止，也不自动换端口',
  },
  free: {
    label: '空闲',
    tone: 'flat',
    hint: '端口没有进程监听',
  },
}

export function portClassView(portClass: PortClass): PortClassView {
  return CLASS_VIEW[portClass]
}

export interface PortHealthView {
  label: '正常' | '异常' | '未探测'
  tone: 'ok' | 'danger' | 'flat'
}

/** 健康三态只翻译后端 `health.ok`：null = 没探测，不冒充正常或异常。 */
export function portHealthView(health: { ok: boolean | null }): PortHealthView {
  if (health.ok === true) return { label: '正常', tone: 'ok' }
  if (health.ok === false) return { label: '异常', tone: 'danger' }
  return { label: '未探测', tone: 'flat' }
}

const ROLE_LABEL: Record<string, string> = {
  mcp: '数据服务',
  web: '管理页面',
  extra: '附加端口',
}

export function portRoleLabel(role: string): string {
  return ROLE_LABEL[role] ?? role
}

/**
 * 逐端口白话原因：把后端分类翻译成普通用户能懂的一句话。
 * 技术原文（`entry.reason`）收进「技术细节」，不直接抛给用户。
 */
export function portPlainReason(entry: PortDiagnosisEntry, product: string, healthOk: boolean | null): string {
  switch (entry.class) {
    case 'adopted_peer': {
      const base = `这是你自己启动的 ${product}，已被平台接管，不会终止或重启它`
      if (healthOk === true) return `${base}，运行正常`
      if (healthOk === false) return `${base}；这次健康复检没通过，可点「复检健康」再查一次`
      return base
    }
    case 'own_child':
      return `这是本程序启动的 ${product} 进程，工作不正常时可以一键安全重启`
    case 'foreign':
      return '这个端口被别的程序占用了。平台不会终止外来进程，请先确认那个程序是否可以关闭'
    case 'free':
      return '端口空闲，模块当前没有在这个端口上运行'
  }
}

export type SafeActionKind = 'restart' | 'recheck' | 'blocked' | 'idle'

export interface SafeActionView {
  kind: SafeActionKind
  label: string
  enabled: boolean
  /** 禁用必须给看得见的理由（§4.8） */
  reason?: string
}

/**
 * 安全按钮展示策略——与后端 `heal_ports` 动作优先级逐条对应（只翻译，不判定）：
 * adopted_peer → 只「复检健康」（后端 adopt + 复检，绝不终止 / 重启）；
 * own_child → 「一键安全修复」（后端停止并重拉自有子进程）；
 * foreign → 禁用 + 引导（后端只报告，绝不自动杀）；free → 无动作。
 *
 * 红线：重启类动作只对 own_child 启用；adopted_peer / foreign 永远没有
 * 重启 / 停止按钮（QUOT-545 / DEC-079 adopt-only 边界）。
 */
export function safeActionView(diagnosis: PeerPortDiagnosis): SafeActionView {
  const classes = new Set(diagnosis.ports.map((entry) => entry.class))
  if (classes.has('adopted_peer')) {
    return { kind: 'recheck', label: '复检健康', enabled: true }
  }
  if (classes.has('own_child')) {
    return { kind: 'restart', label: '一键安全修复', enabled: true }
  }
  if (classes.has('foreign')) {
    return {
      kind: 'blocked',
      label: '一键安全修复',
      enabled: false,
      reason: '不会终止外来进程：请人工确认占用者后处理',
    }
  }
  return { kind: 'idle', label: '一键安全修复', enabled: false, reason: '端口空闲，无需修复' }
}

/** 自愈结果的一句话标题（后端 `action` 的白话翻译）。 */
export function healActionTitle(action: string): string {
  if (action === 'restart') return '已安全重启'
  if (action === 'adopt') return '已复检健康'
  if (action === 'report') return '只报告，未做处置'
  return '无需处置'
}