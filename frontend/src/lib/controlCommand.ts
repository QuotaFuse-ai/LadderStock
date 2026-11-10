import { isApiError, type JobView } from '../api'

/**
 * 控制面页面命令（`/api/control/commands`）的共用口径。
 *
 * 两条命令共用一条 job 队列：同一时刻只允许一条在跑（后端 409 兜底）。
 * 错误文案按 QUOT-533 契约映射：HTTP 403/404/409 是「没提交上」，
 * job 退出码是「提交了但没成功」，两者分开说，页面不自己推断原因。
 */

export const COMMAND_ACTIVE_STATUSES: ReadonlySet<JobView['status']> = new Set(['queued', 'running'])

export function isJobActive(job: JobView | null | undefined): boolean {
  return Boolean(job && COMMAND_ACTIVE_STATUSES.has(job.status))
}

export function jobStatusText(job: JobView): string {
  if (job.status === 'queued') return '已排队'
  if (job.status === 'running') return '执行中'
  if (job.status === 'ok') return '已完成'
  return '失败'
}

/** 提交动作失败（HTTP 层）：按契约的错误码说人话。 */
export function describeCommandSubmitError(error: unknown): string {
  if (isApiError(error)) {
    if (error.status === 409) {
      return '已有页面命令在执行，请等它结束后再试（一次只允许一个任务）。'
    }
    if (error.status === 403) {
      return '控制面拒绝了这次请求：只接受本机回环来源，且 Host 与来源校验必须通过。'
    }
    if (error.status === 404) {
      return '控制面未启用（LDST_CONTROL_ENABLED=0）或该命令尚未上线，暂时不能同步。'
    }
    if (error.kind === 'timeout') {
      return '提交同步请求超时；任务可能已排队，请稍后在「本页命令」确认状态。'
    }
    return error.detail ?? error.message
  }
  return error instanceof Error ? error.message : String(error)
}

export interface JobFailureCopy {
  /** 一句话结论（页面主文案） */
  conclusion: string
  /** 服务端原文（含退出码），作为可展开的原因 */
  detail: string | null
}

/** 任务失败（job 层）：契约里 CLI 退出码 → 人话。 */
export function describeJobFailure(job: JobView): JobFailureCopy {
  const raw = job.error?.trim() || null
  const has = (needle: string) => Boolean(raw?.includes(needle))

  if (has('退出码 -9') || has('超过 1800s')) {
    return { conclusion: '同步超时：超过 1800 秒未结束，任务已被中止。', detail: raw }
  }
  if (has('退出码 10')) {
    return { conclusion: '本地数据库锁等待超时，这次没有写入；稍后可重试。', detail: raw }
  }
  if (has('退出码 4')) {
    return { conclusion: '全部数据源同步失败，这次没有补到数据。', detail: raw }
  }
  if (has('退出码 3')) {
    return { conclusion: '锚点池为空或配置边界不满足，未发起任何请求。', detail: raw }
  }
  if (has('退出码 2')) {
    return { conclusion: '超出单次同步上限或参数不合法，未发起请求。', detail: raw }
  }
  if (has('QFSD-7001')) {
    return { conclusion: '上游限流退避（QFSD-7001），请稍后再试。', detail: raw }
  }
  return { conclusion: '同步失败。', detail: raw }
}

/** 退出码 8：job 仍是 ok，但日志里注明部分数据源失败。 */
export function hasPartialSourceFailure(job: JobView): boolean {
  return job.status === 'ok' && job.log_tail.some((line) => line.includes('退出码 8') || line.includes('部分数据源'))
}
