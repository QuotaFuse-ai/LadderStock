import { useState } from 'react'
import { HeartPulse, RefreshCw, ShieldCheck, Wrench } from 'lucide-react'

import {
  api,
  describeApiError,
  type PeerPortDiagnosis,
  type PortHealResult,
  type PortsDiagnosisPayload,
} from '../../api'
import { useAsync } from '../../lib/useAsync'
import { MISSING } from '../../lib/format'
import {
  healActionTitle,
  portClassView,
  portHealthView,
  portPlainReason,
  portRoleLabel,
  safeActionView,
} from '../../lib/portDiagnosticsView'
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  ErrorBlock,
  SectionCard,
  SkeletonText,
  type ToastItem,
} from '../ui'

/**
 * QUOT-548「端口诊断 + 安全自愈」面板（设置页 · 数据与端口）。
 *
 * 消费 QUOT-547 后端诊断 API（`GET /api/control/ports` /
 * `POST /api/control/ports/{peer}/heal`）：逐模块展示端口占用四分类
 * （自有残留 / 已接管 / 外来进程 / 空闲）+ 健康 + 白话原因；一键安全修复
 * 只对 own_child 启用重拉，adopted_peer 只复检健康，foreign 禁用并说明
 * 「不会终止外来进程」。分类 / 健康 / 动作选择全部来自后端（DEC-079），
 * 前端不重算指标、不硬编码模块清单；技术细节（PID / 命令行）收起可展开。
 */

interface PortDiagnosticsPanelProps {
  controlReady: boolean
  /** 模块产品名解析（来自后端 board 数据；查不到回退 peer key，不硬编码清单） */
  productOf: (peerId: string) => string
  push: (message: string, tone?: ToastItem['tone']) => void
  /** 自愈成功后通知屏侧刷新 board / supervisor 视图 */
  onHealed?: () => void
}

function HealButton({
  diagnosis,
  healing,
  disabled,
  onHeal,
}: {
  diagnosis: PeerPortDiagnosis
  healing: boolean
  disabled: boolean
  onHeal: () => void
}) {
  const action = safeActionView(diagnosis)
  if (action.kind === 'recheck') {
    return (
      <Button
        variant="secondary"
        onClick={onHeal}
        loading={healing}
        disabled={disabled || healing}
        disabledReason={disabled ? '控制面不可用' : undefined}
        leadingIcon={<HeartPulse className="size-3.5" aria-hidden="true" />}
        data-testid={`port-diag-heal-${diagnosis.peer}`}
        title="只重新检查健康并确认接管，不会终止或重启该模块"
      >
        {action.label}
      </Button>
    )
  }
  return (
    <Button
      variant="secondary"
      onClick={onHeal}
      loading={healing}
      disabled={disabled || healing || !action.enabled}
      disabledReason={disabled ? '控制面不可用' : action.reason}
      leadingIcon={<Wrench className="size-3.5" aria-hidden="true" />}
      data-testid={`port-diag-heal-${diagnosis.peer}`}
    >
      {action.label}
    </Button>
  )
}

function PortDiagnosisCard({
  diagnosis,
  product,
  healing,
  disabled,
  result,
  onHeal,
}: {
  diagnosis: PeerPortDiagnosis
  product: string
  healing: boolean
  disabled: boolean
  result: PortHealResult | undefined
  onHeal: () => void
}) {
  const health = portHealthView(diagnosis.health)
  return (
    <li
      className="flex flex-col gap-2 rounded border border-border bg-surface p-4"
      data-testid={`port-diag-peer-${diagnosis.peer}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="truncate text-subtitle font-semibold text-text">{product}</h3>
        <Badge tone={health.tone} title={diagnosis.health.detail || undefined}>
          {health.label}
        </Badge>
        <span className="ml-auto">
          <HealButton diagnosis={diagnosis} healing={healing} disabled={disabled} onHeal={onHeal} />
        </span>
      </div>

      <ul className="flex flex-col gap-2">
        {diagnosis.ports.map((entry) => {
          const classView = portClassView(entry.class)
          return (
            <li
              key={`${entry.port}-${entry.role}`}
              className="flex flex-col gap-1 rounded border border-border bg-surface-2 px-3 py-2"
              data-testid={`port-diag-port-${diagnosis.peer}-${entry.port}`}
            >
              <span className="flex flex-wrap items-center gap-2">
                <span className="ldst-num text-body font-medium text-text">端口 {entry.port}</span>
                <span className="text-caption text-text-secondary">{portRoleLabel(entry.role)}</span>
                <Badge tone={classView.tone} title={classView.hint}>
                  {classView.label}
                </Badge>
              </span>
              <p className="text-caption leading-relaxed text-text-secondary">
                {portPlainReason(entry, product, diagnosis.health.ok)}
              </p>
            </li>
          )
        })}
      </ul>

      {result && (
        <div data-testid={`port-diag-result-${diagnosis.peer}`}>
          <Banner tone={result.ok ? 'ok' : 'danger'} title={healActionTitle(result.action)}>
            {result.detail}
          </Banner>
        </div>
      )}

      <details className="rounded border border-border bg-surface-2 px-3 py-2">
        <summary className="cursor-pointer select-none text-caption text-text-muted">
          技术细节（PID / 命令行 / 后端判定原文）
        </summary>
        <div className="mt-2 flex flex-col gap-2">
          {diagnosis.ports.map((entry) => (
            <dl
              key={`${entry.port}-${entry.role}`}
              className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-caption"
            >
              <dt className="text-text-muted">端口 {entry.port} · PID</dt>
              <dd className="ldst-num break-all text-text-secondary">{entry.pid ?? MISSING}</dd>
              <dt className="text-text-muted">进程</dt>
              <dd className="ldst-num break-all text-text-secondary">{entry.process ?? MISSING}</dd>
              <dt className="text-text-muted">命令行</dt>
              <dd className="ldst-num break-all text-text-secondary">
                {entry.cmdline ? entry.cmdline.join(' ') : MISSING}
              </dd>
              <dt className="text-text-muted">判定原文</dt>
              <dd className="break-all text-text-secondary">{entry.reason}</dd>
            </dl>
          ))}
          <p className="text-caption text-text-muted">
            健康探测：{diagnosis.health.detail || MISSING}
          </p>
        </div>
      </details>
    </li>
  )
}

export function PortDiagnosticsPanel({ controlReady, productOf, push, onHealed }: PortDiagnosticsPanelProps) {
  const diagnosis = useAsync<PortsDiagnosisPayload>(
    (signal) => api.control.ports(signal),
    [],
    { enabled: controlReady },
  )
  const [healing, setHealing] = useState<string | null>(null)
  /** 自愈响应自带后端回读的最新诊断：直接覆盖展示，前端不重算。 */
  const [results, setResults] = useState<Record<string, PortHealResult>>({})

  const reload = (): void => {
    setResults({})
    diagnosis.reload()
  }

  const heal = async (peerId: string): Promise<void> => {
    setHealing(peerId)
    try {
      const result = await api.control.healPorts(peerId)
      setResults((current) => ({ ...current, [peerId]: result }))
      push(
        result.ok ? `${healActionTitle(result.action)}：${result.detail}` : `修复未完成：${result.detail}`,
        result.ok ? 'ok' : 'danger',
      )
      onHealed?.()
    } catch (error) {
      push(`修复失败：${describeApiError(error).detail}`, 'danger')
    } finally {
      setHealing(null)
    }
  }

  const peers = diagnosis.data?.peers ?? []

  return (
    <SectionCard
      title="端口诊断 + 安全自愈"
      subtitle={
        diagnosis.data
          ? `当前 ${peers.length} 个模块 · 为什么没起来一眼看懂，修复只动本程序启动的进程`
          : '为什么模块没起来一眼看懂；修复只动本程序启动的进程'
      }
      actions={
        <Button
          variant="secondary"
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          onClick={reload}
          disabled={!controlReady || diagnosis.status === 'loading' || healing !== null}
          disabledReason={controlReady ? undefined : '控制面不可用'}
          data-testid="port-diag-refresh"
        >
          重新诊断
        </Button>
      }
    >
      <div className="flex flex-col gap-3" data-testid="port-diag-panel">
        {!controlReady ? (
          <EmptyState
            compact
            title="控制面未接入，无法诊断"
            description="需本机 API 已启动（ldst run）且控制面启用；接入后这里会逐模块给出端口占用与白话原因。"
          />
        ) : diagnosis.status === 'loading' ? (
          <SkeletonText lines={4} />
        ) : diagnosis.status === 'error' ? (
          <ErrorBlock {...describeApiError(diagnosis.error)} variant="inline" onRetry={diagnosis.reload} />
        ) : peers.length === 0 ? (
          <EmptyState
            compact
            title="没有可诊断的模块"
            description="后端诊断清单为空：检查 config/sources.yaml 的 peers: 块是否登记。"
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {peers.map((peer) => {
              const result = results[peer.peer]
              const shown: PeerPortDiagnosis = result
                ? { ...peer, ports: result.ports, health: result.health }
                : peer
              return (
                <PortDiagnosisCard
                  key={peer.peer}
                  diagnosis={shown}
                  product={productOf(peer.peer)}
                  healing={healing === peer.peer}
                  disabled={healing !== null}
                  result={result}
                  onHeal={() => void heal(peer.peer)}
                />
              )
            })}
          </ul>
        )}

        <p className="flex items-start gap-1.5 text-caption text-text-muted">
          <ShieldCheck className="mt-half size-3.5 shrink-0" aria-hidden="true" />
          安全边界：只重启本程序启动的进程（自有残留）；已接管的模块只做健康复检，绝不终止或重启；
          外来进程只提示、绝不终止，也不自动换端口。
        </p>
      </div>
    </SectionCard>
  )
}