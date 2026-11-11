/**
 * QUOT-558（S5）：peer 级健康事件全局弹窗 + 一键确认重启。
 *
 * 挂在 Layout（所有路由共享，不止设置页），订阅 `usePeerHealth`
 * （QUOT-556 事件通道轮询）：
 * - crash / offline / port_conflict / needs_human 主动弹窗，`recovered` 自动关；
 * - 文案全白话：后端 `reason` 直出 + 前端固定文案，**零 CLI**——不出现
 *   命令行、不打印 argv / 技术栈（QUOT-558 验收要求弹窗内可 grep）；
 * - 单一主操作「确认重启」接真 `POST /api/supervisor/restart {confirm:true}`：
 *   只授权拉起替代实例，绝不终止任何外部进程（adopt-only，DEC-078/080/081）；
 *   外部认领的 peer 进程仍在运行时，后端会拒绝重启并说明——弹窗照实转述；
 * - 端口冲突按 S4 占用者分类给口径：own_child 可安全重启恢复；
 *   adopted_peer / foreign 只引导确认，绝不暗示可以终止外部程序；
 * - 重启中 loading 防重复点击，失败原因在弹窗内如实展示，不静默。
 */

import { useEffect, useRef, useState } from 'react'

import { api, type SupervisorConflictOwner, type SupervisorHealthEvent } from '../../api'
import { usePeerHealth } from '../../lib/usePeerHealth'
import { Badge, Banner, ConfirmDialog } from '../ui'

/** 事件类型 → 弹窗标题（白话，不含技术词）。 */
export function eventTitle(event: SupervisorHealthEvent, label: string): string {
  switch (event.event) {
    case 'crash':
      return `${label}已崩溃退出`
    case 'offline':
      return `${label}已掉线`
    case 'port_conflict':
      return `${label}的服务端口被其它程序占用`
    case 'needs_human':
      return `${label}自动恢复失败，需要确认后重启`
    case 'recovered':
      return `${label}已恢复`
  }
}

/** 逐冲突端口按占用者分类的白话说明（S4 口径；绝不暗示可终止外部程序）。 */
function conflictLine(owner: SupervisorConflictOwner, pid: number | null): string {
  const who = pid === null ? '' : `（进程号 ${pid}）`
  switch (owner) {
    case 'own_child':
      return `被本应用自己拉起的进程占用${who}：确认重启可安全重启该进程恢复`
    case 'adopted_peer':
      return `被外部启动、已由本应用接管的服务占用${who}：不会终止它`
    case 'foreign':
      return `被无关的外部程序占用${who}：不会终止它，也不会自动换端口`
    default:
      return `占用者未知（${owner}）${who}：为安全起见不会自动处置`
  }
}

export interface PeerHealthDialogProps {
  event: SupervisorHealthEvent | null
  /** 未关闭的坏事件总数（含当前这条）：多个模块同时坏时给个预期 */
  pendingCount?: number
  restarting?: boolean
  restartError?: string | null
  /** 模块代号 → 产品名（白话名的真源是控制面 board）；拉不到时回退模块代号 */
  productName?: (module: string) => string
  onConfirm: () => void
  onClose: () => void
}

export function PeerHealthDialog({
  event,
  pendingCount = 1,
  restarting = false,
  restartError = null,
  productName = (module) => module,
  onConfirm,
  onClose,
}: PeerHealthDialogProps) {
  if (!event) return null
  const label = productName(event.module) || event.module
  const adopted = event.adopted_peer || event.origin === 'adopted' || event.origin === 'spawned-via-task'
  const conflicts = event.event === 'port_conflict' ? event.conflicts : []

  return (
    <ConfirmDialog
      open
      title={eventTitle(event, label)}
      description={event.reason}
      confirmLabel="确认重启"
      cancelLabel="稍后再说"
      loading={restarting}
      onConfirm={onConfirm}
      onCancel={onClose}
    >
      <div className="flex flex-col gap-2" data-testid="peer-health-dialog">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={adopted ? 'warn' : 'flat'}>{adopted ? '外部启动' : '本程序启动'}</Badge>
          {event.pid !== null && (
            <span className="ldst-num text-caption text-text-muted">进程号 {event.pid}</span>
          )}
          {event.ports.length > 0 && (
            <span className="ldst-num text-caption text-text-muted">
              端口 {event.ports.join(' / ')}
            </span>
          )}
        </div>

        {conflicts.length > 0 && (
          <ul className="flex flex-col gap-1 rounded border border-warn bg-warn-soft p-2 text-caption text-text-secondary">
            {conflicts.map((conflict) => (
              <li key={conflict.port} data-testid={`conflict-${conflict.port}`}>
                端口 <span className="ldst-num">{conflict.port}</span>
                {conflictLine(conflict.owner, conflict.pid)}
              </li>
            ))}
          </ul>
        )}

        {restartError && (
          <div data-testid="peer-restart-error">
            <Banner tone="danger" title="重启未成功">
              {restartError}
            </Banner>
          </div>
        )}

        <p className="text-caption text-text-muted">
          确认重启只会重新拉起「{label}」的替代实例，绝不会终止任何外部程序。
          {adopted && '该模块是外部启动的：若它的进程仍在运行，重启会被拒绝并说明原因。'}
        </p>
        {pendingCount > 1 && (
          <p className="text-caption text-text-muted" data-testid="peer-pending-more">
            另有 {pendingCount - 1} 个模块也报了问题，处理完这条会接着提示。
          </p>
        )}
      </div>
    </ConfirmDialog>
  )
}

/** 全局挂载层：订阅事件通道 + 首次弹窗时拉一次模块白话名映射。 */
export default function PeerHealthAlert() {
  const { active, pendingCount, restarting, restartError, confirmRestart, dismiss } = usePeerHealth()
  const [products, setProducts] = useState<Record<string, string>>({})
  // 只发一次：放进 ref 而不是 effect 依赖，effect 重跑不会取消 in-flight 请求
  const namesRef = useRef<Promise<unknown> | null>(null)

  useEffect(() => {
    if (!active || namesRef.current) return
    namesRef.current = api.control
      .board()
      .then((payload) => {
        const map: Record<string, string> = {}
        for (const peer of payload.peers ?? []) map[peer.id] = peer.product
        for (const item of payload.components ?? []) map[item.id] = item.product
        for (const item of payload.registry ?? []) {
          if (map[item.id] === undefined) map[item.id] = item.product
        }
        setProducts(map)
      })
      .catch(() => {
        // 白话名拉不到就显示模块代号，弹窗本身不受影响（不弹全局错误）
      })
  }, [active])

  return (
    <PeerHealthDialog
      event={active}
      pendingCount={pendingCount}
      restarting={restarting}
      restartError={restartError}
      productName={(module) => products[module] ?? module}
      onConfirm={() => void confirmRestart()}
      onClose={dismiss}
    />
  )
}
