import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  Download,
  ExternalLink,
  FileText,
  Info,
  Play,
  Power,
  RefreshCw,
  Sliders,
  Square,
  Terminal,
  Upload,
} from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  type BoardPayload,
  type ComponentSnapshot,
  type JobView,
  type PeerImportPayload,
  type PeerLogs,
  type PeerSnapshot,
  type PeerStatus,
  type RegistryEntry,
  type SelfAiStatus,
  type HealthPayload,
  type PortConflict,
  type PortOwner,
  type StateConfigPayload,
  type SupervisorModule,
  type SupervisorStatePayload,
} from '../api'
import { useAsync, type AsyncResult } from '../lib/useAsync'
import { setDemoPref, useDemoEnabled, useForcedState, resolveState, type PageState } from '../lib/pageState'
import { MISSING, countOrMissing } from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Checkbox,
  ConfirmDialog,
  DataTable,
  Drawer,
  EmptyState,
  ErrorBlock,
  Field,
  IconButton,
  Input,
  SectionCard,
  Segmented,
  SkeletonText,
  StatusDot,
  Switch,
  Toasts,
  useToasts,
  type Column,
  type DotTone,
  type ToastItem,
} from '../components/ui'
import AiConnectDialog from '../components/AiConnectDialog'
import DiscoveryPanel from '../components/DiscoveryPanel'
import { SenateRoleModels } from '../components/SenateRoleModels'
import { StopListDialog } from '../components/supervisor/StopListDialog'
import { ProxyPoolCard } from '../components/supervisor/ProxyPoolCard'
import { PortDiagnosticsPanel } from '../components/supervisor/PortDiagnosticsPanel'
import { WorkflowHealthMonitor } from '../components/supervisor/WorkflowHealthMonitor'
import { findProxyPoolModule } from '../lib/proxyPoolView'
import {
  buildStopCandidates,
  findModule,
  needsConfirm,
  selfHealView,
  ownershipView,
  avoidanceNote,
  type SelfHealView,
  type StopCandidate,
} from '../lib/supervisorView'
import { setDensityPref, setTheme, useDensity, useThemeChoice } from '../lib/prefs'
import type { Density, ThemeChoice } from '../lib/theme'
import { cn } from '../lib/cn'

/**
 * 设置页（P-07 / DESIGN-SPEC §4.8 + PRD-SETTINGS-CONTROL-PLANE）。
 *
 * 四个分区（左侧 176px 二级菜单，`?tab=` 深链）：
 *   子模块   —— 模块卡（生态 peer + 工具组件）+ 模块注册表；状态**只**取后端
 *              `status` / `status_label`，前端不推断。模块清单与数量一律来自后端
 *              注册表（唯一真源是 config/sources.yaml 的 peers 块，QUOT-403/420）。
 *   AI 接入  —— 端点 / 模型 / 密钥；密钥只写不读回（只显示 `set` + `preview`）。
 *   数据与端口 —— 端口诊断 + 安全自愈面板（QUOT-548，四分类白话诊断 + 一键安全修复）
 *              + 端口占用表（✅ 空闲 / ⚠️ 被占用含 PID+进程 / ❌ 越界）。
 *   关于     —— 版本、数据目录、DEC-006 边界、主题与密度（与侧栏同一状态）。
 *
 * 控制面未接入时：卡片退化为静态目录表 + 全按钮禁用 + 可见原因（§4.8 占位要求），
 * 不编造「已安装 / 运行中」。安装/启动/停止只经后端任务，前端 1s 轮询 job，不执行任何命令。
 */

type Section = 'peers' | 'ai' | 'ports' | 'about'

interface SectionMeta {
  value: Section
  label: string
  hint: string
}

const SECTIONS: readonly SectionMeta[] = [
  { value: 'peers', label: '子模块', hint: '模块清单来自 config/sources.yaml 的 peers: 块' },
  { value: 'ai', label: 'AI 接入', hint: '端点 · 模型 · 密钥' },
  { value: 'ports', label: '数据与端口', hint: '端口占用与配置存在性' },
  { value: 'about', label: '关于', hint: '版本与边界声明' },
]

function isSection(value: string | null): value is Section {
  return value === 'peers' || value === 'ai' || value === 'ports' || value === 'about'
}

const PEER_STATUS_LABEL: Record<PeerStatus, string> = {
  unconfigured: '未配置',
  not_installed: '未安装',
  installed: '已安装',
  starting: '启动中',
  running: '运行中',
  stopped: '已停止',
  failed: '异常',
}

const PEER_DOT: Record<PeerStatus, DotTone> = {
  unconfigured: 'flat',
  not_installed: 'flat',
  installed: 'warn',
  starting: 'accent',
  running: 'ok',
  stopped: 'warn',
  failed: 'danger',
}

const JOB_LABEL: Record<JobView['status'], string> = {
  queued: '排队中',
  running: '进行中',
  ok: '已完成',
  failed: '失败',
}

const ACTION_LABEL: Record<string, string> = {
  install: '安装',
  start: '启动',
  stop: '停止',
}


/**
 * 控制面未接入时的静态基线（PRD §2 事实表，写死）。
 * 只用来显示「目录 / 端口应该是什么」，状态一律不猜。
 */
interface StaticPeer {
  id: string
  product: string
  role: string
  directory: string
  mcp: number
  web: number | null
  webLabel: string
}

const STATIC_PEERS: readonly StaticPeer[] = [
  {
    id: 'tickhub',
    product: 'TickHub-CN',
    role: '数据底座 · A 股',
    directory: '[local-path]\\TickHub-CN',
    mcp: 8000,
    web: 8787,
    webLabel: '管理端',
  },
  {
    id: 'tickhub-us',
    product: 'TickHub-US',
    role: '数据底座 · 美股',
    directory: '[local-path]\\TickHub-US',
    mcp: 8002,
    web: null,
    webLabel: '管理端（本构建未提供）',
  },
  {
    id: 'wire',
    product: 'QuotaFuse Wire',
    role: '资讯感知层',
    directory: '[local-path]\\Wire\\quotafuse-wire',
    mcp: 8791,
    web: 8790,
    webLabel: 'HTTP',
  },
  {
    id: 'senate',
    product: 'StockSenate',
    role: '合议层',
    directory: '[local-path]\\StockSenate',
    mcp: 8765,
    web: 8786,
    webLabel: 'WebUI',
  },
  {
    id: 'financecoffer',
    product: 'FinanceCoffer',
    role: '数据存储：宽表 / 公告检索 / 风险（WSL，只读观测）',
    directory: '[local-path]\\FinanceCoffer',
    mcp: 8100,
    web: null,
    webLabel: 'HTTP 服务（WSL）',
  },
  {
    id: 'proxyfleet',
    product: 'ProxyFleet',
    role: '代理池：出口节点 / 网关 / 拨测',
    directory: '[local-path]\\ProxyFleet',
    mcp: 8789,
    web: 8788,
    webLabel: '控制台',
  },
]

const CONTROL_ABSENT_NOTE = '控制面接口未接入（本轮不实现）：需本机服务已启动并开启控制开关。'

function trimSlash(path: string): string {
  return path.replace(/[\\/]+$/, '')
}

/**
 * PRD §4「安装的确切动作」——五步的**白话描述**（QUOT-561：界面上不再摆命令行，
 * 实际执行仍在后端线程池；用户点「确认安装」即可，不需要抄任何命令）。
 */
function installSteps(peer: Pick<PeerSnapshot, 'directory' | 'dist'>): string[] {
  return [
    `进入安装目录：${trimSlash(peer.directory)}`,
    '准备独立的 Python 运行环境（.venv；已存在则沿用）',
    '把 pip 升级到最新版',
    peer.dist.wheel
      ? '安装这份目录里打包好的安装包'
      : '从这份目录就地安装（开发模式，改动即时生效）',
    '自检：确认模块能被导入并打印版本号',
  ]
}

function uptimeLabel(startedAt: string | null): string | null {
  if (!startedAt) return null
  const start = new Date(startedAt).getTime()
  if (Number.isNaN(start)) return null
  const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours > 0) return `${hours}h${minutes}m`
  if (minutes > 0) return `${minutes}m`
  return `${seconds}s`
}

function ownerText(owner: { pid: number | null; process: string | null } | undefined | null): string {
  if (!owner) return MISSING
  // PID 缺失是「没有这个事实」，显示 `—` 而不是假定值 `?`（§11.18）
  const pid = owner.pid === null ? MISSING : String(owner.pid)
  return `${owner.process ?? '未知进程'} · PID ${pid}`
}

function pendingJob(peerId: string, action: string, jobId: string): JobView {
  return {
    id: jobId,
    peer: peerId,
    action,
    status: 'queued',
    step: '已排队，等待后端线程池',
    progress: 0,
    started_at: new Date().toISOString(),
    ended_at: null,
    log_tail: [],
    error: null,
  }
}

// ---------------------------------------------------------------- 分区一：子模块

function PeerCard({
  peer,
  job,
  controlReady,
  supervisor,
  supervisorReady,
  stoppedExternal,
  acting,
  onInstall,
  onStart,
  onStop,
  onLogs,
  onConfig,
  onMeta,
  onRetryHeal,
  children,
}: {
  peer: PeerSnapshot
  job: JobView | undefined
  controlReady: boolean
  supervisor: SupervisorModule | null
  supervisorReady: boolean
  stoppedExternal: boolean
  acting: string | null
  onInstall: () => void
  onStart: () => void
  onStop: () => void
  onLogs: () => void
  onConfig: () => void
  onMeta: () => void
  onRetryHeal: () => void
  /** 卡片底部的模块专属面板（StockSenate 的辩论角色多模型，QUOT-520）。 */
  children?: ReactNode
}) {
  const statusLabel = peer.status_label || PEER_STATUS_LABEL[peer.status]
  const uptime = peer.last_job?.action === 'start' && peer.last_job.status === 'ok' ? uptimeLabel(peer.last_job.started_at) : null
  const jobActive = job?.status === 'queued' || job?.status === 'running'
  const ownership = ownershipView(supervisor)
  const heal: SelfHealView | null = selfHealView(supervisor)

  return (
    <li
      className={cn(
        'rounded border bg-surface shadow-card',
        heal?.failed ? 'border-danger bg-danger-soft' : 'border-border',
      )}
      data-testid={`peer-card-${peer.id}`}
      data-self-heal={heal?.failed ? 'gave_up' : 'ok'}
    >
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusDot tone={PEER_DOT[peer.status]} pulse={peer.status === 'starting'}>
                {statusLabel}
              </StatusDot>
              <h3 className="truncate text-subtitle font-semibold text-text">{peer.product}</h3>
              {peer.version && <span className="ldst-num text-caption text-text-muted">v{peer.version}</span>}
              {supervisorReady ? (
                <Badge tone={ownership.tone} title={ownership.hint} className="ldst-num">
                  {ownership.label}
                </Badge>
              ) : (
                <Badge tone="flat" title="GET /api/supervisor/state 不可用：归属无法判定">
                  归属未知（supervisor 未接入）
                </Badge>
              )}
            </div>
            <p className="mt-1 text-caption text-text-secondary">{peer.role}</p>
          </div>
          <IconButton
            label={`${peer.product} 安装元数据`}
            icon={<Info className="size-4" aria-hidden="true" />}
            onClick={onMeta}
          />
        </div>

        {heal && (
          <div
            className={cn(
              'flex flex-wrap items-center gap-2 rounded border px-2 py-2',
              heal.failed ? 'border-danger bg-surface' : 'border-border bg-surface-2',
            )}
            role="status"
          >
            {heal.failed && <AlertTriangle className="size-4 shrink-0 text-danger" aria-hidden="true" />}
            <span className={cn('text-body font-medium', heal.failed ? 'text-danger' : 'text-text')}>
              {heal.label}
            </span>
            {!heal.failed && (
              <span className="text-caption text-text-secondary">每 1s 巡检一次，恢复后自动回到「运行中」</span>
            )}
            {heal.failed && (
              <Button
                variant="secondary"
                onClick={onRetryHeal}
                loading={acting === `${peer.id}:heal`}
                disabled={!controlReady}
                disabledReason={controlReady ? undefined : '控制面不可用'}
                leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
                className="ml-auto"
              >
                重试
              </Button>
            )}
          </div>
        )}

        {stoppedExternal && (
          <p className="flex items-center gap-1 text-caption text-warn" data-testid={`not-stopped-${peer.id}`}>
            <Info className="size-3.5 shrink-0" aria-hidden="true" />
            未停止（外部进程）：上次停止清单已取消，该进程仍在运行
          </p>
        )}

        <p className="text-caption text-text-secondary">
          MCP <span className="ldst-num">{peer.ports.mcp}</span> · {peer.ports.web_label}{' '}
          <span className="ldst-num">{peer.ports.web ?? MISSING}</span> ·{' '}
          {peer.installed ? '已安装' : '未安装'} · {statusLabel}
          {uptime && (
            <>
              {' · 运行 '}
              <span className="ldst-num">{uptime}</span>
            </>
          )}
        </p>

        {peer.port_conflicts.length > 0 && (
          <ul className="flex flex-col gap-1">
            {peer.port_conflicts.map((conflict) => {
              const info = conflict.severity === 'info'
              return (
                <li
                  key={conflict.port}
                  className={`flex items-start gap-1 text-caption ${info ? 'text-text-muted' : 'text-warn'}`}
                >
                  {info ? (
                    <Info className="mt-half size-3.5 shrink-0" aria-hidden="true" />
                  ) : (
                    <AlertTriangle className="mt-half size-3.5 shrink-0" aria-hidden="true" />
                  )}
                  <span>
                    端口 <span className="ldst-num">{conflict.port}</span> {conflict.note}
                    {conflict.pid !== null && (
                      <>
                        （PID <span className="ldst-num">{conflict.pid}</span>
                        {conflict.process ? ` · ${conflict.process}` : ''}）
                      </>
                    )}
                  </span>
                </li>
              )
            })}
          </ul>
        )}

        {job && (
          <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-surface-2 px-2 py-2">
            <Badge tone={job.status === 'failed' ? 'danger' : job.status === 'ok' ? 'ok' : 'info'}>
              {ACTION_LABEL[job.action] ?? job.action} · {JOB_LABEL[job.status]}
            </Badge>
            <span className="min-w-0 flex-1 truncate text-caption text-text-secondary">{job.step || MISSING}</span>
            {jobActive && (
              <span
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(job.progress)}
                aria-label="任务进度"
                className="h-1 w-24 overflow-hidden rounded-pill bg-surface-3"
              >
                <span
                  className="block h-full rounded-pill bg-accent transition-all duration-base"
                  style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
                />
              </span>
            )}
            {job.status === 'failed' && (
              <Button variant="ghost" onClick={onLogs}>
                看失败日志
              </Button>
            )}
          </div>
        )}

        {!controlReady && <p className="text-caption text-text-muted">{CONTROL_ABSENT_NOTE}</p>}

        <div className="flex flex-wrap items-center gap-2">
          {peer.actions.can_install && (
            <Button
              variant="secondary"
              disabled={!controlReady || jobActive}
              onClick={onInstall}
              leadingIcon={<Download className="size-3.5" aria-hidden="true" />}
            >
              安装
            </Button>
          )}
          {peer.actions.can_start && (
            <Button
              variant="secondary"
              disabled={!controlReady || jobActive}
              loading={acting === `${peer.id}:start`}
              onClick={onStart}
              leadingIcon={<Play className="size-3.5" aria-hidden="true" />}
            >
              启动
            </Button>
          )}
          {peer.actions.can_stop && (
            <Button
              variant="secondary"
              disabled={!controlReady || jobActive}
              loading={acting === `${peer.id}:stop`}
              onClick={onStop}
              leadingIcon={<Square className="size-3.5" aria-hidden="true" />}
            >
              停止
            </Button>
          )}
          {peer.actions.can_open && peer.open_url && (
            <a
              href={peer.open_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-control items-center gap-1 rounded px-2 text-body text-text-secondary transition-colors duration-fast hover:bg-surface-3 hover:text-text"
            >
              打开
              <ExternalLink className="size-3.5" aria-hidden="true" />
            </a>
          )}
          <Button
            variant="ghost"
            disabled={!controlReady}
            onClick={onLogs}
            leadingIcon={<FileText className="size-3.5" aria-hidden="true" />}
          >
            查看日志
          </Button>
          <Button
            variant="ghost"
            disabled={!controlReady || !peer.actions.can_config}
            disabledReason={
              peer.actions.can_config
                ? undefined
                : '本模块没有可写配置键（控制面配置白名单为空），配置由该模块自行管理'
            }
            onClick={onConfig}
            leadingIcon={<Sliders className="size-3.5" aria-hidden="true" />}
          >
            编辑配置
          </Button>
        </div>

        {children}
      </div>
    </li>
  )
}

function ComponentCard({
  component,
  supervisor,
  supervisorReady,
}: {
  component: ComponentSnapshot
  supervisor: SupervisorModule | null
  supervisorReady: boolean
}) {
  const statusLabel = component.status_label || PEER_STATUS_LABEL[component.status]
  const ownership = ownershipView(supervisor)
  const heal: SelfHealView | null = selfHealView(supervisor)
  return (
    <li
      className={cn(
        'rounded border bg-surface shadow-card',
        heal?.failed ? 'border-danger bg-danger-soft' : 'border-border',
      )}
    >
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusDot tone={PEER_DOT[component.status]} pulse={component.status === 'starting'}>
                {statusLabel}
              </StatusDot>
              <h3 className="truncate text-subtitle font-semibold text-text">{component.product}</h3>
              <Badge tone="info">代理模块</Badge>
              {supervisorReady ? (
                <Badge tone={ownership.tone} title={ownership.hint}>
                  {ownership.label}
                </Badge>
              ) : (
                <Badge tone="flat" title="GET /api/supervisor/state 不可用：归属无法判定">
                  归属未知（supervisor 未接入）
                </Badge>
              )}
            </div>
            <p className="mt-1 text-caption text-text-secondary">{component.role}</p>
          </div>
        </div>

        <p className="text-caption text-text-secondary">
          控制台 <span className="ldst-num">{component.ports.console}</span> · 网关{' '}
          <span className="ldst-num">{component.ports.gateway ?? MISSING}</span> · {statusLabel}
        </p>
        {heal && (
          <p className={cn('text-caption', heal.failed ? 'font-medium text-danger' : 'text-text-secondary')}>
            {heal.label}
          </p>
        )}
        <p className="text-caption text-text-muted">{component.detail}</p>
        <p className="truncate text-caption text-text-muted" title={component.directory}>
          目录 <span className="ldst-num">{component.directory}</span>
        </p>
        <p className="text-caption text-text-muted">托管：{component.managed_by}</p>
        {component.notes.map((note) => (
          <p key={note} className="text-caption text-text-muted">
            {note}
          </p>
        ))}

        <div className="flex flex-wrap items-center gap-2">
          {component.actions.can_open && component.open_url && (
            <a
              href={component.open_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-control items-center gap-1 rounded px-2 text-body text-text-secondary transition-colors duration-fast hover:bg-surface-3 hover:text-text"
            >
              打开控制台
              <ExternalLink className="size-3.5" aria-hidden="true" />
            </a>
          )}
          {!component.actions.can_open && (
            <span className="text-caption text-text-muted">
              未运行；它由本机计划任务托管，开关也在计划任务里，本页不代管启停。
            </span>
          )}
        </div>
      </div>
    </li>
  )
}

function StaticPeerCard({ peer, reason }: { peer: StaticPeer; reason: string }) {
  return (
    <li className="rounded border border-border bg-surface shadow-card">
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusDot tone="flat">未探测</StatusDot>
              <h3 className="truncate text-subtitle font-semibold text-text">{peer.product}</h3>
            </div>
            <p className="mt-1 text-caption text-text-secondary">{peer.role}</p>
          </div>
        </div>
        {/* §11.18：控制面未接入时端口不是已知事实，不能把登记基线当探测值展示 */}
        <p className="text-caption text-text-secondary">
          MCP <span className="ldst-num">{MISSING}</span> · {peer.webLabel}{' '}
          <span className="ldst-num">{MISSING}</span> · 安装状态 {MISSING} · 运行状态 {MISSING}（控制面未接入）
        </p>
        <p className="text-caption text-text-muted">
          登记基线 <span className="ldst-num">{peer.mcp}</span> /{' '}
          <span className="ldst-num">{peer.web ?? MISSING}</span>（来自 config/sources.yaml，非探测值）
        </p>
        <p className="truncate text-caption text-text-muted" title={peer.directory}>
          目录 <span className="ldst-num">{peer.directory}</span>
        </p>
        <p className="text-caption text-text-muted">{reason}</p>
        {/* QUOT-561：这里原来有「安装 / 启动 / 查看日志」三颗永远灰色的按钮——控制面没连上时
            它们什么也做不了，只留下死按钮。现在如实说明：能做的动作在控制面接好后会自动出现，
            不摆按钮、也不给命令指引。 */}
        <p className="text-caption text-text-muted">
          安装、启动、看日志这些动作属于控制面：本机服务连上并开启控制开关后，这张卡片会自动换成可操作的版本；
          在那之前这里只展示登记信息，不做任何操作。
        </p>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------- 端口表

type PortState = 'ok' | 'busy' | 'self' | 'out' | 'unknown'

interface PortRow {
  /** `null` = 没取到（§8）：显示 `—`，不许拿默认端口号当真实读数 */
  port: number | null
  usage: string
  state: PortState
  owner: string
  note: string
}

/* 状态标签一律「文字 + 色调」，不混用表情符号（§5.3：每档都要有文字与图形，图形由 Badge 承担） */
const PORT_STATE: Record<PortState, { tone: 'ok' | 'warn' | 'danger' | 'info' | 'flat'; label: string }> = {
  ok: { tone: 'ok', label: '空闲' },
  busy: { tone: 'warn', label: '被占用' },
  self: { tone: 'info', label: '监听中' },
  out: { tone: 'danger', label: '越界' },
  unknown: { tone: 'flat', label: `${MISSING} 未探测` },
}

const PORT_COLUMNS: Array<Column<PortRow>> = [
  {
    key: 'port',
    header: '端口',
    width: '88px',
    numeric: true,
    render: (row) => <span className="font-medium">{row.port === null ? MISSING : row.port}</span>,
  },
  { key: 'usage', header: '用途', truncate: true, render: (row) => row.usage },
  {
    key: 'state',
    header: '状态',
    width: '144px',
    render: (row) => <Badge tone={PORT_STATE[row.state].tone}>{PORT_STATE[row.state].label}</Badge>,
  },
  {
    key: 'owner',
    header: '占用进程',
    width: '208px',
    truncate: true,
    render: (row) => <span className="ldst-num text-caption text-text-secondary">{row.owner}</span>,
  },
  {
    key: 'note',
    header: '说明',
    truncate: true,
    render: (row) => <span className="text-caption text-text-muted">{row.note}</span>,
  },
]

const REGISTRY_COLUMNS: Array<Column<RegistryEntry>> = [
  {
    key: 'id',
    header: 'ID',
    width: '128px',
    render: (row) => <span className="ldst-num">{row.id}</span>,
  },
  { key: 'product', header: '产品', width: '148px', truncate: true, render: (row) => row.product },
  {
    key: 'package',
    header: '包名',
    width: '168px',
    truncate: true,
    render: (row) => <span className="ldst-num">{row.package || MISSING}</span>,
  },
  {
    key: 'port',
    header: '端口',
    width: '80px',
    numeric: true,
    render: (row) => (row.default_port === null ? MISSING : row.default_port),
  },
  {
    key: 'role',
    header: '职责',
    truncate: true,
    render: (row) => <span className="text-caption text-text-secondary">{row.role || MISSING}</span>,
  },
  {
    key: 'installed',
    header: '安装状态',
    width: '96px',
    render: (row) => {
      // 「已安装」只看后端 install 探测；WSL 托管（只读观测）等服务可能未装 venv 却在运行，
      // 如实写「运行中」，不冒充已安装。
      if (row.installed) return <Badge tone="ok">已安装</Badge>
      if (row.status === 'running') return <Badge tone="ok">运行中</Badge>
      return <Badge tone="warn">未安装</Badge>
    },
  },
]

function buildPortRows(board: BoardPayload | null, probed: boolean): PortRow[] {
  const conflicts = new Map<number, PortConflict & { peer?: string }>()
  board?.port_conflicts.forEach((conflict) => conflicts.set(conflict.port, conflict))

  const rows: PortRow[] = []
  const push = (port: number | null | undefined, usage: string, listening: PortOwner[] | null): void => {
    if (port === null || port === undefined) {
      rows.push({ port: null, usage, state: 'unknown', owner: MISSING, note: `${MISSING} 未取到：本机服务未连接，端口未知` })
      return
    }
    if (port < 1024 || port > 65535) {
      rows.push({ port, usage, state: 'out', owner: MISSING, note: '端口越界（合法范围 1024–65535）' })
      return
    }
    const conflict = conflicts.get(port)
    if (conflict) {
      // severity=info：模块已在服务、只是不是控制面启动的 → 不是冲突
      const info = conflict.severity === 'info'
      rows.push({
        port,
        usage,
        state: info ? 'self' : 'busy',
        owner: ownerText(conflict),
        note: conflict.note || (conflict.foreign ? '外部进程占用' : '已被占用'),
      })
      return
    }
    if (listening && listening.length > 0) {
      rows.push({ port, usage, state: 'self', owner: ownerText(listening[0]), note: '本模块监听中' })
      return
    }
    if (!probed) {
      rows.push({ port, usage, state: 'unknown', owner: MISSING, note: '未探测：本地服务未连接' })
      return
    }
    rows.push({ port, usage, state: 'ok', owner: MISSING, note: '空闲' })
  }

  push(board?.ports.board, '看板 Web', null)
  push(board?.ports.mcp, '看板 MCP', null)

  const live = board?.peers ?? []
  if (live.length > 0) {
    for (const peer of live) {
      push(peer.ports.mcp, `${peer.product} MCP`, peer.listening.mcp)
      push(peer.ports.web, `${peer.product} ${peer.ports.web_label}`, peer.listening.web)
    }
  } else {
    for (const peer of STATIC_PEERS) {
      push(peer.mcp, `${peer.product} MCP`, null)
      push(peer.web, `${peer.product} ${peer.webLabel}`, null)
    }
  }
  for (const component of board?.components ?? []) {
    push(component.ports.console, `${component.product} 控制台`, component.listening.console)
    push(component.ports.gateway, `${component.product} 网关`, component.listening.gateway)
  }
  return rows
}

// ---------------------------------------------------------------- 抽屉

function LogsDrawer({ peer, onClose }: { peer: PeerSnapshot | null; onClose: () => void }) {
  const open = peer !== null
  const logs = useAsync<PeerLogs>(
    (signal) => api.control.logs(peer?.id ?? '', 200, signal),
    [peer?.id],
    { enabled: open && peer !== null },
  )

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="lg"
      title={peer ? `${peer.product} · 日志尾部` : '日志'}
      subtitle={logs.data?.path ? `来源 ${logs.data.path}（已脱敏）` : '最近 200 行，密钥不会出现在日志里'}
    >
      {logs.status === 'loading' && <SkeletonText lines={4} />}
      {logs.status === 'error' && (
        <ErrorBlock {...describeApiError(logs.error)} variant="inline" onRetry={logs.reload} />
      )}
      {logs.status === 'ready' && logs.data && !logs.data.exists && (
        <EmptyState
          compact
          title="还没有这个模块的日志"
          description="进程首次启动后才会生成日志文件；未安装时不会有日志。"
        />
      )}
      {logs.status === 'ready' && logs.data?.exists && (
        <pre className="ldst-num max-h-full overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-sunken p-3 text-caption leading-relaxed text-text-secondary">
          {logs.data.lines.length > 0 ? logs.data.lines.join('\n') : '（日志为空）'}
        </pre>
      )}
    </Drawer>
  )
}

/** §11.0「后端 key 不直显」：已知配置键给中文名，未知键原样保留（不藏内容），键名退到 hint。 */
const CONFIG_KEY_LABEL: Record<string, string> = {
  MCP_HTTP_PORT: 'MCP HTTP 端口',
  ADMIN_PORT: '管理端端口',
  ADMIN_HTTP_PORT: '管理端 HTTP 端口（预留）',
  TICKHUB_CN_LOG_LEVEL: '日志级别（当前不生效）',
  TICKHUB_US_ALLOW_TIER_B_SOURCES: '允许 B 级数据源',
  SEC_CONTACT: '安全联系方式',
  POLYGON_API_KEY: 'Polygon API Key',
  QF_WIRE_HOST: '监听地址',
  QF_WIRE_PORT: 'HTTP 端口',
  QF_WIRE_MCP_PORT: 'MCP 端口',
  QF_WIRE_DIGEST_PROVIDER: '摘要提供方',
  QF_WIRE_API_BASE: 'API Base URL',
  QF_WIRE_DIGEST_MODEL: '摘要模型',
  QF_WIRE_API_KEY: 'Wire API Key',
  // llm_provider / deep_think_llm / quick_think_llm 不在白名单（QUOT-481 缺陷 1）：
  // 后端不再返回这些键，这里不留映射，避免看起来仍可配置
  MINIMAX_API_KEY: 'MiniMax API Key',
}

function configKeyLabel(key: string): string {
  return CONFIG_KEY_LABEL[key] ?? key
}

function ConfigDrawer({
  peer,
  onClose,
  onSaved,
  push,
}: {
  peer: PeerSnapshot | null
  onClose: () => void
  onSaved: () => void
  push: (message: string, tone?: ToastItem['tone']) => void
}) {
  const open = peer !== null
  const config = useAsync(
    (signal) => api.control.config(peer?.id ?? '', signal),
    [peer?.id],
    { enabled: open && peer !== null },
  )
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [cleared, setCleared] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    setDrafts({})
    setCleared([])
    setSaveError(null)
  }, [peer?.id])

  const data = config.data
  const dirty = Object.values(drafts).some((value) => value.trim() !== '') || cleared.length > 0

  const toggleClear = (key: string, next: boolean): void => {
    setCleared((current) => (next ? [...current, key] : current.filter((item) => item !== key)))
  }

  const save = async (): Promise<void> => {
    if (!peer || !data) return
    const values: Record<string, string> = {}
    for (const [key, value] of Object.entries(drafts)) {
      if (value.trim() !== '') values[key] = value
    }
    // 空字符串 = 不修改（PRD §5）；只有显式勾选「清除」才写 clear
    if (Object.keys(values).length === 0 && cleared.length === 0) {
      push('没有需要保存的修改', 'info')
      return
    }
    setSaving(true)
    setSaveError(null)
    try {
      const result = await api.control.saveConfig(peer.id, values, cleared)
      push(`已保存 ${result.updated?.length ?? Object.keys(values).length} 项配置`, 'ok')
      setDrafts({})
      setCleared([])
      onSaved()
      config.reload()
    } catch (error) {
      setSaveError(describeApiError(error).detail)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="lg"
      title={peer ? `${peer.product} · 配置` : '配置'}
      subtitle={data ? `${data.env_file} · 仅白名单键可写` : '读取中…'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="secondary"
            onClick={() => void save()}
            loading={saving}
            disabled={!data || !data.editable || !dirty}
            disabledReason={
              data && !data.editable ? '本模块配置不可编辑' : !dirty ? '没有修改' : undefined
            }
          >
            保存
          </Button>
        </>
      }
    >
      {config.status === 'loading' && <p className="text-body text-text-secondary">正在读取配置…</p>}
      {config.status === 'error' && (
        <ErrorBlock {...describeApiError(config.error)} variant="inline" onRetry={config.reload} />
      )}
      {config.status === 'ready' && data && (
        <div className="flex flex-col gap-4">
          {data.notes.length > 0 && (
            <ul className="flex flex-col gap-1 rounded border border-border bg-surface-2 p-2 text-caption text-text-secondary">
              {data.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {!data.editable ? (
            // QUOT-480：白名单为空（或 env 目录不存在）= 本机无可写配置。
            // 如实说明原因，不出空表单、也不给「保存后创建」的假承诺。
            <div className="flex flex-col gap-2 rounded border border-border bg-surface-2 p-3">
              <p className="text-body text-text-secondary">
                本模块没有可写配置：控制面没有登记它的配置白名单（或其配置文件目录不存在），
                配置由该模块自行管理，本页面不提供写入入口。
              </p>
              <p className="text-caption text-text-muted">该模块的配置文件路径：{data.env_file}</p>
            </div>
          ) : (
            <>
              {!data.exists && (
                <p className="text-caption text-warn">
                  配置文件尚未生成，保存后创建（落盘后仅所有者可读写，0600）。
                </p>
              )}
              {data.keys.map((key) => {
                const entry = data.values[key]
                const isCleared = cleared.includes(key)
                if (!entry) return null
                if (entry.secret) {
                  return (
                    <div key={key} className="flex flex-col gap-2 rounded border border-border p-2">
                      <Field
                        label={configKeyLabel(key)}
                        htmlFor={`cfg-${key}`}
                        hint={`密钥只写不读回 · 配置键 ${key} · 当前 ${entry.set ? entry.preview ?? '已配置' : '未配置'} · 留空表示不修改`}
                      >
                        <Input
                          id={`cfg-${key}`}
                          type="password"
                          autoComplete="new-password"
                          spellCheck={false}
                          placeholder={entry.set ? (entry.preview ?? '已配置') : '未配置'}
                          value={drafts[key] ?? ''}
                          disabled={isCleared}
                          onChange={(event) => setDrafts((current) => ({ ...current, [key]: event.target.value }))}
                        />
                      </Field>
                      <Checkbox checked={isCleared} onChange={(next) => toggleClear(key, next)}>
                        清除该密钥（保存后删除）
                      </Checkbox>
                    </div>
                  )
                }
                return (
                  <Field key={key} label={configKeyLabel(key)} htmlFor={`cfg-${key}`} hint={`配置键 ${key} · 当前值 ${entry.value ?? MISSING}（可覆盖）`}>
                    <Input
                      id={`cfg-${key}`}
                      spellCheck={false}
                      placeholder={entry.value ?? ''}
                      value={drafts[key] ?? ''}
                      onChange={(event) => setDrafts((current) => ({ ...current, [key]: event.target.value }))}
                    />
                  </Field>
                )
              })}
              {saveError && (
                // C-19 Banner danger：失败原因置顶可见，表单内容保持可编辑（§11.18）
                <Banner tone="danger" title="保存失败">
                  {saveError}
                </Banner>
              )}
              <p className="text-caption text-text-muted">
                白名单以外的键不可写；密钥落盘 0600，页面永远不会回显明文。
              </p>
            </>
          )}
        </div>
      )}
    </Drawer>
  )
}

// ---------------------------------------------------------------- 导入模块

interface ImportDraft {
  id: string
  product: string
  package: string
  default_url: string
  health_path: string
  default_port: string
  role: string
  repo: string
}

const EMPTY_IMPORT: ImportDraft = {
  id: '',
  product: '',
  package: '',
  default_url: 'http://127.0.0.1:',
  health_path: '/healthz',
  default_port: '',
  role: '',
  repo: '',
}

/**
 * QUOT-420「导入模块」：只提交一条 peer 定义，后端写 config/sources.yaml
 * （写前备份、冲突 409、不执行任何命令）；成功后立即重绘，无需重启。
 */
function ImportPeerDrawer({
  open,
  onClose,
  onImported,
  push,
}: {
  open: boolean
  onClose: () => void
  onImported: () => void
  push: (message: string, tone?: ToastItem['tone']) => void
}) {
  const [draft, setDraft] = useState<ImportDraft>(EMPTY_IMPORT)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setDraft(EMPTY_IMPORT)
    setError(null)
  }, [open])

  const setField = (key: keyof ImportDraft) => (event: { target: { value: string } }) => {
    setDraft((current) => ({ ...current, [key]: event.target.value }))
  }

  const port = Number(draft.default_port)
  const complete = Object.values(draft).every((value) => value.trim() !== '') && Number.isInteger(port)

  const submit = async (): Promise<void> => {
    if (!complete) return
    const body: PeerImportPayload = {
      id: draft.id.trim(),
      product: draft.product.trim(),
      package: draft.package.trim(),
      default_url: draft.default_url.trim(),
      health_path: draft.health_path.trim(),
      default_port: port,
      role: draft.role.trim(),
      repo: draft.repo.trim(),
    }
    setBusy(true)
    setError(null)
    try {
      await api.control.importPeer(body)
      push(`已导入模块：${body.product}（${body.id}）`, 'ok')
      onImported()
      onClose()
    } catch (caught) {
      setError(describeApiError(caught).detail)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="lg"
      title="导入模块"
      subtitle="写入 config/sources.yaml 的 peers: 块；只追加，不覆盖已有条目"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            loading={busy}
            disabled={!complete}
            disabledReason={complete ? undefined : '八个字段都要填写，端口必须是整数'}
          >
            导入
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="ID" htmlFor="import-id" hint="小写字母开头，可含数字与下划线" required>
            <Input id="import-id" spellCheck={false} value={draft.id} onChange={setField('id')} placeholder="例如 mypeer" />
          </Field>
          <Field label="产品名" htmlFor="import-product" required>
            <Input id="import-product" value={draft.product} onChange={setField('product')} placeholder="例如 MyPeer" />
          </Field>
          <Field label="包名" htmlFor="import-package" required>
            <Input id="import-package" spellCheck={false} value={draft.package} onChange={setField('package')} placeholder="例如 mypeer" />
          </Field>
          <Field label="默认端口" htmlFor="import-port" hint="1-65535" required>
            <Input
              id="import-port"
              type="number"
              inputMode="numeric"
              min={1}
              max={65535}
              value={draft.default_port}
              onChange={setField('default_port')}
              placeholder="例如 8123"
            />
          </Field>
          <Field label="默认地址" htmlFor="import-url" hint="http(s) URL" required>
            <Input id="import-url" spellCheck={false} value={draft.default_url} onChange={setField('default_url')} />
          </Field>
          <Field label="健康检查路径" htmlFor="import-health" hint="以 / 开头" required>
            <Input id="import-health" spellCheck={false} value={draft.health_path} onChange={setField('health_path')} />
          </Field>
          <Field label="仓库" htmlFor="import-repo" hint="owner/name" required>
            <Input id="import-repo" spellCheck={false} value={draft.repo} onChange={setField('repo')} placeholder="例如 QuotaFuse-ai/MyPeer" />
          </Field>
          <Field label="职责" htmlFor="import-role" hint="模块卡上显示的一句话" required>
            <Input id="import-role" value={draft.role} onChange={setField('role')} placeholder="例如 数据源：示例模块" />
          </Field>
        </div>
        {error && (
          <Banner tone="danger" title="导入失败">
            {error}
          </Banner>
        )}
        <p className="text-caption text-text-muted">
          写前会备份为 <span className="ldst-num">sources.yaml.bak.&lt;时间戳&gt;</span>，原有注释与条目顺序保留；
          只接受注册表字段，不接受命令或可执行路径。导入后新模块按「未安装」展示，安装/启动仍需后端任务。
        </p>
      </div>
    </Drawer>
  )
}

// ---------------------------------------------------------------- 分区二：AI 接入

interface AiTarget {
  id: string
  label: string
  /** 写入的配置键；`base` 为 null 表示该模块端点不在白名单（由 llm_provider 决定）；
   * `model` 为 null 表示模型名由该模块自己的配置文件管理（写 env 不生效，QUOT-481） */
  keys: { base: string | null; model: string | null; key: string }
}

const AI_TARGETS: readonly AiTarget[] = [
  {
    id: 'wire',
    label: 'QuotaFuse Wire',
    keys: { base: 'QF_WIRE_API_BASE', model: 'QF_WIRE_DIGEST_MODEL', key: 'QF_WIRE_API_KEY' },
  },
  {
    id: 'senate',
    label: 'StockSenate',
    // llm_provider / deep_think_llm / quick_think_llm 由 Senate 的 config/llm_config.toml
    // 管理（写 env 永远不生效），控制面白名单只保留 MINIMAX_API_KEY（QUOT-481 缺陷 1）
    keys: { base: null, model: null, key: 'MINIMAX_API_KEY' },
  },
]

function AiSection({
  controlReady,
  push,
}: {
  controlReady: boolean
  push: (message: string, tone?: ToastItem['tone']) => void
}) {
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [selected, setSelected] = useState<string[]>(['wire'])
  const [testing, setTesting] = useState(false)
  const [applying, setApplying] = useState(false)
  const [connectOpen, setConnectOpen] = useState(false)
  const selfAi = useAsync((signal) => api.control.selfAi(signal), [], { enabled: controlReady })
  // `latency_ms === null` = 没测出结果（失败路径），不能显示「耗时 0ms」（§11.18）
  const [result, setResult] = useState<{
    ok: boolean
    latency_ms: number | null
    model_seen: string | null
    error: string | null
  } | null>(null)

  const toggle = (id: string, next: boolean): void => {
    setSelected((current) => (next ? [...current, id] : current.filter((item) => item !== id)))
  }

  const test = async (): Promise<void> => {
    const peer = selected[0]
    if (!peer) {
      push('先选择至少一个模块', 'danger')
      return
    }
    setTesting(true)
    setResult(null)
    try {
      const outcome = await api.control.aiTest({
        peer,
        base_url: baseUrl,
        model,
        ...(apiKey.trim() === '' ? {} : { api_key: apiKey }),
      })
      setResult(outcome)
    } catch (error) {
      const info = describeApiError(error)
      setResult({ ok: false, latency_ms: null, model_seen: null, error: `${info.title}：${info.detail}` })
    } finally {
      setTesting(false)
    }
  }

  const apply = async (targets: readonly AiTarget[]): Promise<void> => {
    if (targets.length === 0) {
      push('先选择至少一个模块', 'danger')
      return
    }
    setApplying(true)
    try {
      for (const target of targets) {
        const values: Record<string, string> = {}
        if (baseUrl.trim() !== '' && target.keys.base) values[target.keys.base] = baseUrl.trim()
        if (model.trim() !== '' && target.keys.model) values[target.keys.model] = model.trim()
        if (apiKey.trim() !== '') values[target.keys.key] = apiKey.trim()
        if (Object.keys(values).length === 0) continue
        await api.control.saveConfig(target.id, values)
      }
      push(`已应用到 ${targets.map((target) => target.label).join('、')}`, 'ok')
      setApiKey('')
    } catch (error) {
      push(`应用失败：${describeApiError(error).detail}`, 'danger')
    } finally {
      setApplying(false)
    }
  }

  const chosen = AI_TARGETS.filter((target) => selected.includes(target.id))

  const selfStatus: SelfAiStatus | null = selfAi.data
  const selfError = selfAi.status === 'error' ? describeApiError(selfAi.error) : null

  return (
    <div className="flex flex-col gap-4">
      <DiscoveryPanel onApplied={() => selfAi.reload()} />
      <SectionCard
        title="看板自己的 AI"
        subtitle="复盘生成读取 config/ai.env；一次配置，生成复盘立即生效"
      >
        {selfAi.status === 'loading' && <SkeletonText lines={2} />}
        {selfError && (
          <Banner tone="danger" title="读不到看板 AI 配置">
            {selfError.detail}
          </Banner>
        )}
        {selfStatus && (
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={selfStatus.configured ? 'ok' : 'warn'}>
              {selfStatus.configured ? '已配置' : '未配置'}
            </Badge>
            {selfStatus.model_override && (
              <span className="text-body text-text-secondary">
                模型 <span className="ldst-num text-text">{selfStatus.model_override}</span>
              </span>
            )}
            <span className="text-caption text-text-muted">
              {selfStatus.configured ? '复盘生成会真实调用该模型' : '未配置时只能走纯文本降级'}
            </span>
            <Button variant="secondary" onClick={() => setConnectOpen(true)} className="ml-auto">
              打开接入向导
            </Button>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="统一 AI 配置"
        subtitle="网络端点 / 模型 / 密钥写入各模块自己的 env；密钥只写不读回"
      >
        <div className="flex flex-col gap-4">
          <Field label="端点 Base URL" htmlFor="ai-base" hint="OpenAI 兼容，例如 https://ai.example.com/v1">
            <Input
              id="ai-base"
              spellCheck={false}
              placeholder="https://…/v1"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
          </Field>
          <Field label="模型名" htmlFor="ai-model" hint="例如 deepseek-… / MiniMax-…">
            <Input
              id="ai-model"
              spellCheck={false}
              placeholder="模型标识"
              value={model}
              onChange={(event) => setModel(event.target.value)}
            />
          </Field>
          <Field label="API Key" htmlFor="ai-key" hint="只写不读回；留空表示沿用现有密钥">
            <Input
              id="ai-key"
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              placeholder="sk-…"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </Field>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-caption text-text-secondary">应用目标（可多选，每个模块独立覆盖）</legend>
            {AI_TARGETS.map((target) => (
              <Checkbox
                key={target.id}
                checked={selected.includes(target.id)}
                onChange={(next) => toggle(target.id, next)}
              >
                <span>
                  {target.label}
                  <span className="ml-2 text-caption text-text-muted">
                    写入 {target.keys.base ?? '端点由 llm_provider 决定'} /{' '}
                    {target.keys.model ?? '模型由该模块配置文件管理'} / {target.keys.key}
                  </span>
                </span>
              </Checkbox>
            ))}
            <p className="text-caption text-text-muted">
              TickHub-CN / TickHub-US 不使用大模型，配置项不在白名单内，故不提供。
            </p>
          </fieldset>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={() => void test()} loading={testing} disabled={!controlReady}>
              测试连通性
            </Button>
            <Button variant="secondary" onClick={() => void apply(chosen)} loading={applying} disabled={!controlReady}>
              仅应用到选中
            </Button>
            <Button variant="secondary" onClick={() => void apply(AI_TARGETS)} loading={applying} disabled={!controlReady}>
              应用到全部
            </Button>
            {!controlReady && <span className="text-caption text-text-muted">{CONTROL_ABSENT_NOTE}</span>}
          </div>

          {result && (
            <Banner tone={result.ok ? 'ok' : 'danger'} title={result.ok ? '连通' : '不通'}>
              {result.latency_ms !== null ? (
                <>
                  耗时 <span className="ldst-num">{result.latency_ms}ms</span>
                </>
              ) : (
                '本次没有取到耗时'
              )}
              {result.model_seen && (
                <>
                  {' · 返回模型 '}
                  <span className="ldst-num">{result.model_seen}</span>
                </>
              )}
              {result.error && <span> · {result.error}</span>}
            </Banner>
          )}
        </div>
      </SectionCard>

      <AiConnectDialog
        open={connectOpen}
        onClose={() => setConnectOpen(false)}
        onSaved={() => selfAi.reload()}
      />
    </div>
  )
}

// ---------------------------------------------------------------- 分区三：端口与目录

/**
 * `?tab=ports` 的内容。控制面未启用时也要渲染本段（§11.18「每段有自身
 * loading/empty/error」），端口取不到就逐行 `—`，不显示假定默认值。
 * QUOT-548：顶部新增「端口诊断 + 安全自愈」面板（消费 QUOT-547 诊断 API）；
 * 原「一键体检」占位按钮由面板的「重新诊断」取代（体检接口已落地）。
 */
function PortsSection({
  board,
  portRows,
  stateConfig,
  controlReady,
  productOf,
  push,
  onHealed,
  supervisorState,
}: {
  board: AsyncResult<BoardPayload>
  portRows: PortRow[]
  stateConfig: AsyncResult<StateConfigPayload>
  controlReady: boolean
  productOf: (peerId: string) => string
  push: (message: string, tone?: ToastItem['tone']) => void
  onHealed: () => void
  supervisorState?: SupervisorStatePayload | null
}) {
  const density = useDensity()
  // 同一屏的数字口径：端口区也按注册表实际出卡数走，不写死模块个数
  const moduleCount = board.data ? board.data.peers.length + (board.data.components?.length ?? 0) : null
  // QUOT-594：实际端口只读展示 + 避让说明（信息态，不告警）
  const avoidanceNotes = (supervisorState?.modules ?? [])
    .map((module) => ({ module, note: avoidanceNote(module) }))
    .filter((item): item is { module: SupervisorModule; note: string } => item.note !== null)
  return (
    <div className="flex flex-col gap-4">
      {avoidanceNotes.length > 0 && (
        <SectionCard title="实际端口" subtitle="端口被其它程序占用时应用会自动换一个，无需手动处理">
          <ul className="flex flex-col gap-1">
            {avoidanceNotes.map(({ module, note }) => (
              <li key={module.module} className="text-caption text-text-secondary" data-testid={`avoided-${module.module}`}>
                <Info className="mr-1 inline size-3.5 align-[-2px]" aria-hidden="true" />
                {productOf(module.module) || module.module}：{note}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}
      <PortDiagnosticsPanel
        controlReady={controlReady}
        productOf={productOf}
        push={push}
        onHealed={onHealed}
      />

      <SectionCard
        title="端口占用"
        subtitle={
          <>
            看板 <span className="ldst-num">{countOrMissing(board.data?.ports.board)}</span> / MCP{' '}
            <span className="ldst-num">{countOrMissing(board.data?.ports.mcp)}</span> · 模块清单当前{' '}
            <span className="ldst-num">{moduleCount === null ? MISSING : moduleCount}</span> 个
          </>
        }
        bodyClassName="p-0"
      >
        <DataTable<PortRow>
          columns={PORT_COLUMNS}
          rows={portRows}
          rowKey={(row) => `${row.port}-${row.usage}`}
          caption="端口占用表：空闲 / 被占用（PID + 进程）/ 越界"
          density={density}
          loading={board.status === 'loading'}
          empty={<EmptyState compact title="后端没有返回任何端口" description="端口清单来自 /api/control/board；控制面未接入时本表没有行可展示。" />}
        />
      </SectionCard>

      <SectionCard title="配置与数据目录" subtitle="来自 /api/state/config">
        {stateConfig.status === 'ready' && stateConfig.data ? (
          <div className="flex flex-col gap-2">
            <p className="text-caption text-text-secondary">
              配置目录 <span className="ldst-num break-all">{stateConfig.data.config_dir}</span>
            </p>
            <p className="text-caption text-text-secondary">
              数据目录 <span className="ldst-num break-all">{stateConfig.data.data_dir}</span>
            </p>
            <ul className="mt-half flex flex-wrap gap-2">
              {stateConfig.data.files.map((file) => (
                <li key={file.file}>
                  <Badge tone={file.present ? 'ok' : 'warn'}>
                    {file.present ? '有' : '缺'} <span className="ldst-num">{file.file}</span>
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        ) : stateConfig.status === 'error' ? (
          <ErrorBlock {...describeApiError(stateConfig.error)} variant="inline" onRetry={stateConfig.reload} />
        ) : stateConfig.status === 'loading' ? (
          <SkeletonText lines={2} />
        ) : (
          <EmptyState compact title="没有读到配置目录" description="/api/state/config 返回为空；确认本机服务已启动过一次。" />
        )}
      </SectionCard>
    </div>
  )
}

// ---------------------------------------------------------------- 分区四：关于

const THEME_OPTIONS: ReadonlyArray<{ value: ThemeChoice; label: string }> = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
]

const DENSITY_OPTIONS: ReadonlyArray<{ value: Density; label: string }> = [
  { value: 'default', label: '默认 38' },
  { value: 'compact', label: '紧凑 36' },
  { value: 'comfortable', label: '舒适 40' },
]

function AboutSection({
  health,
  stateConfig,
}: {
  health: AsyncResult<HealthPayload>
  stateConfig: AsyncResult<StateConfigPayload>
}) {
  const theme = useThemeChoice()
  const density = useDensity()
  const demo = useDemoEnabled()
  const [params, setParams] = useSearchParams()
  const info = health.data
  const config = stateConfig.data
  // 每段自己的 loading / error 原因（§11.18）：失败时给真实标题，不写死「本地服务未连接」
  const healthNote = health.status === 'error' ? describeApiError(health.error).title : '读取中…'
  const configNote = stateConfig.status === 'error' ? describeApiError(stateConfig.error).title : '读取中…'

  const rows: Array<{ label: string; value: string; note?: string }> = [
    {
      label: '后端版本',
      value: info ? info.version : MISSING,
      note: info ? undefined : healthNote,
    },
    { label: '构建时间', value: MISSING, note: '前端未注入构建时间戳（构建时未生成）' },
    { label: 'CLI 版本', value: MISSING, note: '数据面未暴露 CLI 版本' },
    {
      label: '数据目录',
      value: config?.data_dir ?? info?.data_dir ?? MISSING,
      note: config || info ? undefined : healthNote,
    },
    { label: '配置目录', value: config?.config_dir ?? MISSING, note: config ? undefined : configNote },
    {
      label: '状态库 schema',
      value: config?.state_schema_version === null || config?.state_schema_version === undefined
        ? MISSING
        : String(config.state_schema_version),
      note: config?.state_index_present === false ? '索引未建立' : undefined,
    },
  ]

  const changeDemo = (next: boolean) => {
    setDemoPref(next)
    // 关掉时连 URL 里的深链参数一起摘掉，否则「?demo=1」会把开关立刻顶回去
    if (!next && params.has('demo')) {
      const copy = new URLSearchParams(params)
      copy.delete('demo')
      setParams(copy, { replace: true })
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="版本与环境" subtitle="只读，来自 /api/health 与 /api/state/config">
        <dl className="grid grid-cols-1 gap-x-5 gap-y-2 desk:grid-cols-2">
          {rows.map((row) => (
            <div key={row.label} className="flex min-w-0 items-baseline justify-between gap-3 border-b border-border pb-1 last:border-b-0">
              <dt className="shrink-0 text-caption text-text-secondary">{row.label}</dt>
              <dd className="min-w-0 text-right">
                <span className="ldst-num break-all text-body text-text">{row.value}</span>
                {row.note && <span className="ml-2 text-caption text-text-muted">{row.note}</span>}
              </dd>
            </div>
          ))}
        </dl>
      </SectionCard>

      <SectionCard title="边界声明" subtitle="DEC-006">
        <p className="text-body leading-relaxed text-text-secondary">
          本应用不采集数据，只消费子模块。看板只读取本机文件与回环接口（127.0.0.1），不联网、不上传、不做多用户与远程访问；
          子模块的抓取、清洗与远程调用由各自项目负责。
        </p>
      </SectionCard>

      <SectionCard title="数据来源" subtitle="虚拟数据只用于预览，不写入本机、不代表行情">
        <div className="flex flex-col gap-3">
          <Switch
            checked={demo}
            onChange={changeDemo}
            label="虚拟数据（演示）"
            description="开启后各页面用内置演示数据填充；关闭后只显示本机真实数据。"
          />
          <p className="text-caption text-text-muted">
            URL 里带 <code className="ldst-num">?demo=1</code> 也会临时开启演示源，等同于这里的开关。
          </p>
        </div>
      </SectionCard>

      <SectionCard title="外观" subtitle="与侧栏底部同一状态，切换不重载页面">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-body text-text">主题</p>
              <p className="text-caption text-text-secondary">跟随系统时会随系统深浅色即时切换</p>
            </div>
            <Segmented<ThemeChoice>
              options={THEME_OPTIONS}
              value={theme}
              onChange={setTheme}
              ariaLabel="主题"
              size="sm"
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            <div>
              <p className="text-body text-text">表格密度</p>
              <p className="text-caption text-text-secondary">只影响表格行高，不影响卡片与图表</p>
            </div>
            <Segmented<Density>
              options={DENSITY_OPTIONS}
              value={density}
              onChange={setDensityPref}
              ariaLabel="表格密度"
              size="sm"
            />
          </div>
        </div>
      </SectionCard>
    </div>
  )
}

// ---------------------------------------------------------------- 主屏

export default function SettingsScreen() {
  const [params, setParams] = useSearchParams()
  const forced = useForcedState()

  const board = useAsync((signal) => api.control.board(signal), [])
  const stateConfig = useAsync((signal) => api.stateConfig(signal), [])
  const health = useAsync((signal) => api.health(signal), [])
  // QUOT-484：归属 / 自愈数据只取自后端账本视图（482-A 合并面）。取不到就如实
  // 显示「归属未知（supervisor 未接入）」，不让整屏失败，也不拿端口猜。
  const supervisor = useAsync((signal) => api.supervisor.state(signal), [])

  const { items, push, dismiss } = useToasts()

  const [jobs, setJobs] = useState<Record<string, JobView>>({})
  const [installPeer, setInstallPeer] = useState<PeerSnapshot | null>(null)
  const [installBusy, setInstallBusy] = useState(false)
  const [installError, setInstallError] = useState<string | null>(null)
  const [logsPeer, setLogsPeer] = useState<PeerSnapshot | null>(null)
  const [configPeer, setConfigPeer] = useState<PeerSnapshot | null>(null)
  const [metaPeer, setMetaPeer] = useState<PeerSnapshot | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [acting, setActing] = useState<string | null>(null)

  // ---- QUOT-484：停止清单 ----
  const [stopList, setStopList] = useState<StopCandidate[] | null>(null)
  const [stopBusy, setStopBusy] = useState(false)
  const [stopError, setStopError] = useState<string | null>(null)
  /** 取消后仍在运行的外部进程（模块名）：卡片上保留可见标记，不静默消失 */
  const [notStoppedExternal, setNotStoppedExternal] = useState<string[]>([])
  const [stopNotice, setStopNotice] = useState<string | null>(null)

  const supervisorReady = supervisor.status === 'ready' && supervisor.data !== null

  const productOf = useCallback(
    (moduleName: string): string => {
      const key = moduleName.replace(/-/g, '_')
      const peer = board.data?.peers.find((item) => item.id.replace(/-/g, '_') === key)
      if (peer) return peer.product
      const component = board.data?.components?.find((item) => item.id.replace(/-/g, '_') === key)
      return component?.product ?? moduleName
    },
    [board.data],
  )

  const tabParam = params.get('tab')
  const section: Section = isSection(tabParam) ? tabParam : 'peers'

  const changeSection = useCallback(
    (next: Section) => {
      const copy = new URLSearchParams(params)
      copy.set('tab', next)
      setParams(copy, { replace: true })
    },
    [params, setParams],
  )

  const peers = useMemo(() => board.data?.peers ?? [], [board.data])
  const components = useMemo(() => board.data?.components ?? [], [board.data])
  const registry = useMemo(() => board.data?.registry ?? [], [board.data])
  // 标题、侧栏说明、端口区用同一个数字：模块 = 注册表里出卡的 peer + 工具组件
  const moduleCount = board.data ? peers.length + components.length : null
  const moduleHint =
    moduleCount === null
      ? '模块清单来自 config/sources.yaml 的 peers: 块'
      : `模块清单来自 config/sources.yaml 的 peers: 块，当前 ${moduleCount} 个`
  const controlError = board.error
  const notConnected = isApiError(controlError) && controlError.kind === 'not-connected'
  const httpStatus = isApiError(controlError) && controlError.kind === 'http' ? controlError.status : null
  const controlOff = board.data ? !board.data.control_enabled : false

  const derived: PageState =
    board.status === 'loading'
      ? 'loading'
      : board.status === 'error'
        ? notConnected
          ? 'offline'
          : 'error'
        : peers.length === 0
          ? 'empty'
          : 'normal'

  const state = resolveState(forced, derived)
  const controlReady = state === 'normal' && board.status === 'ready' && !controlOff
  const failedPeers = peers.filter((peer) => peer.status === 'failed')

  // 记住后端已存在的进行中任务：重进页面仍能看到 running（PRD §4 任务模型）
  useEffect(() => {
    if (peers.length === 0) return
    setJobs((current) => {
      const next = { ...current }
      for (const peer of peers) {
        const job = peer.last_job
        if (!job) continue
        if (!next[peer.id]) next[peer.id] = job
      }
      return next
    })
  }, [peers])

  // 1s 轮询直到终态；终态时刷新一次 board 拿真实状态
  const activeJobs = useMemo(
    () => Object.values(jobs).filter((job) => job.status === 'queued' || job.status === 'running'),
    [jobs],
  )

  useEffect(() => {
    if (activeJobs.length === 0) return
    const timer = window.setInterval(() => {
      for (const job of activeJobs) {
        void api.control
          .job(job.id)
          .then((next) => {
            setJobs((current) => ({ ...current, [job.peer]: next }))
            if (next.status === 'ok' || next.status === 'failed') {
              push(
                `${ACTION_LABEL[next.action] ?? next.action} ${next.status === 'ok' ? '完成' : '失败'}：${next.step || next.id.slice(0, 8)}`,
                next.status === 'ok' ? 'ok' : 'danger',
              )
              board.reload()
            }
          })
          .catch(() => {
            /* 单次轮询失败不终止任务：保持原 job，下一拍再试 */
          })
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [activeJobs, board, push])

  const runAction = async (peer: PeerSnapshot, action: 'start' | 'stop'): Promise<void> => {
    setActing(`${peer.id}:${action}`)
    try {
      const handle = action === 'start' ? await api.control.start(peer.id) : await api.control.stop(peer.id)
      setJobs((current) => ({ ...current, [peer.id]: pendingJob(peer.id, action, handle.job_id) }))
      push(`已创建任务：${ACTION_LABEL[action]} ${peer.product}`, 'info')
      board.reload()
    } catch (error) {
      push(`操作失败：${describeApiError(error).detail}`, 'danger')
    } finally {
      setActing(null)
    }
  }

  const stopFailureText = (error: unknown): string => {
    if (isApiError(error) && error.kind === 'http' && error.status === 404) {
      return '后端未提供停止接口（POST /api/supervisor/stop，482-B 尚未接入）；本次没有停止任何进程。'
    }
    return describeApiError(error).detail
  }

  const executeStop = async (candidates: readonly StopCandidate[], viaDialog: boolean): Promise<void> => {
    if (candidates.length === 0) return
    setStopBusy(true)
    setStopError(null)
    if (viaDialog) setStopNotice(null)
    try {
      await api.supervisor.stop({ confirm: true, modules: candidates.map((item) => item.module) })
      setStopList(null)
      setNotStoppedExternal((current) => current.filter((name) => !candidates.some((item) => item.module === name)))
      push(`已停止 ${candidates.length} 个模块`, 'ok')
      board.reload()
      void supervisor.refresh()
    } catch (error) {
      const message = stopFailureText(error)
      if (viaDialog) setStopError(message)
      else setStopNotice(`停止未执行：${message}`)
    } finally {
      setStopBusy(false)
    }
  }

  /**
   * 打开停止清单。
   * - 指定模块（点卡片「停止」）：总是先给清单确认，不做无提示停止；
   * - 全停（「退出」）：清单里全是本程序启动的进程时按 D2 不弹冗余确认，
   *   改为一行可见说明后直接发起（点了有反应，不误导说有外部进程）。
   */
  const requestStop = (moduleNames?: string[]): void => {
    if (!supervisorReady) {
      setStopNotice('归属数据不可用（supervisor 未接入）：无法生成停止清单，本次没有停止任何进程。')
      return
    }
    const all = buildStopCandidates(supervisor.data, productOf)
    const chosen = moduleNames ? all.filter((item) => moduleNames.includes(item.module)) : all
    setStopError(null)
    setStopNotice(null)
    if (chosen.length === 0) {
      setStopNotice('当前离线：没有正在运行的模块，没有需要停止的进程。')
      return
    }
    if (!moduleNames && !needsConfirm(chosen)) {
      setStopNotice(
        `全部 ${chosen.length} 个模块均为本程序启动，没有需要逐个确认的外部进程；正在直接停止。`,
      )
      void executeStop(chosen, false)
      return
    }
    setStopList(chosen)
  }

  const cancelStop = (): void => {
    const external = (stopList ?? []).filter((item) => item.external)
    if (external.length > 0) {
      setNotStoppedExternal((current) => [
        ...current,
        ...external.map((item) => item.module).filter((name) => !current.includes(name)),
      ])
    }
    setStopList(null)
    setStopError(null)
    push(
      external.length > 0
        ? `已取消停止：${external.length} 个外部进程继续运行`
        : '已取消停止',
      'info',
    )
  }

  const retryHeal = async (peer: PeerSnapshot, moduleName: string): Promise<void> => {
    setActing(`${peer.id}:heal`)
    try {
      await api.supervisor.reset(moduleName)
      push(`已解除人工处理：${peer.product}。自愈窗口已清零，下一轮巡检重新判定。`, 'ok')
      void supervisor.refresh()
      board.reload()
    } catch (error) {
      push(`重试失败：${describeApiError(error).detail}`, 'danger')
    } finally {
      setActing(null)
    }
  }

  const confirmInstall = async (): Promise<void> => {    if (!installPeer) return
    setInstallBusy(true)
    setInstallError(null)
    try {
      const handle = await api.control.install(installPeer.id)
      setJobs((current) => ({ ...current, [installPeer.id]: pendingJob(installPeer.id, 'install', handle.job_id) }))
      push(`安装任务已创建：${installPeer.product}`, 'info')
      setInstallPeer(null)
      board.reload()
    } catch (error) {
      setInstallError(describeApiError(error).detail)
    } finally {
      setInstallBusy(false)
    }
  }

  const portRows = useMemo(() => buildPortRows(board.data, board.status === 'ready'), [board.data, board.status])

  const header = (
    <PageHeader
      title="设置"
      subtitle={
        state === 'offline'
          ? '本地服务未连接'
          : controlOff
            ? '控制面未启用（LDST_CONTROL_ENABLED=0）'
            : board.data
              ? `${moduleCount} 个模块 · 运行中 ${
                  peers.filter((peer) => peer.status === 'running').length +
                  components.filter((item) => item.status === 'running').length
                }`
              : '读取模块状态…'
      }
      badge={<DemoStateBadge state={forced} />}
      refresh={{
        label: '刷新状态',
        loading: board.status === 'loading' && state !== 'loading',
        disabled: state === 'loading',
        onClick: () => {
          board.reload()
          stateConfig.reload()
          health.reload()
          void supervisor.refresh()
        },
      }}
    />
  )

  const menu = (
    <nav aria-label="设置分区" className="w-full shrink-0 desk:w-menu">
      <ul className="flex flex-col gap-1">
        {SECTIONS.map((item) => {
          const active = item.value === section
          return (
            <li key={item.value}>
              <button
                type="button"
                aria-current={active ? 'page' : undefined}
                onClick={() => changeSection(item.value)}
                className={cn(
                  'flex w-full flex-col items-start gap-1 rounded px-3 py-2 text-left transition-colors duration-fast',
                  active ? 'bg-surface-3 text-text' : 'text-text-secondary hover:bg-surface-3 hover:text-text',
                )}
              >
                <span className={cn('text-body', active && 'font-medium')}>{item.label}</span>
                <span className="text-caption text-text-muted">
                  {item.value === 'peers' ? moduleHint : item.hint}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      <p className="mt-3 px-3 text-caption text-text-muted">
        本页只读展示与白名单写入，不执行任何命令；安装 / 启动由后端任务完成。
      </p>
    </nav>
  )

  let body: ReactNode

  if (state === 'loading') {
    body = <ScreenSkeleton metrics={4} rows={4} />
  } else if (state === 'error') {
    // 控制面失败只影响「子模块 / 端口」；AI 与关于的数据源独立，本段照常可看（§11.18）
    body = (
      <div className="flex flex-col gap-4">
        <ErrorBlock {...describeApiError(controlError)} onRetry={board.reload} />
        {section === 'ai' && <AiSection controlReady={false} push={push} />}
        {section === 'ports' && (
          <PortsSection
            board={board}
            portRows={portRows}
            stateConfig={stateConfig}
            controlReady={false}
            productOf={productOf}
            push={push}
            onHealed={() => void board.refresh()}
            supervisorState={supervisor.data}
          />
        )}
        {section === 'about' && <AboutSection health={health} stateConfig={stateConfig} />}
      </div>
    )
  } else if (controlOff || (httpStatus === 404 && state !== 'offline')) {
    body = (
      <div className="flex flex-col gap-4">
        <Banner tone="danger" title="控制面未启用">
          <>
            设置页的安装 / 启动 / 停止全部不可用；数据面（首页、盯盘、复盘、验证）不受影响。在本机服务的配置里打开控制开关并重启服务后恢复。
          </>
        </Banner>
        {section === 'peers' && (
          <ul className="flex flex-col gap-3">
            {STATIC_PEERS.map((peer) => (
              <StaticPeerCard key={peer.id} peer={peer} reason="控制面未启用：模块状态无法探测。" />
            ))}
          </ul>
        )}
        {section === 'ai' && <AiSection controlReady={false} push={push} />}
        {section === 'ports' && (
          <PortsSection
            board={board}
            portRows={portRows}
            stateConfig={stateConfig}
            controlReady={false}
            productOf={productOf}
            push={push}
            onHealed={() => void board.refresh()}
            supervisorState={supervisor.data}
          />
        )}
        {section === 'about' && <AboutSection health={health} stateConfig={stateConfig} />}
      </div>
    )
  } else {
    body = (
      <div className="flex flex-col gap-4">
        {state === 'offline' && <OfflineBanner onRetry={board.reload} />}
        {(state === 'partial' || failedPeers.length > 0) && (
          <Banner tone="warn" title={failedPeers.length > 0 ? (
            <>
              <span className="ldst-num">{failedPeers.length}</span> 个模块异常
            </>
          ) : (
            '部分模块状态不可用'
          )}>
            {failedPeers.length > 0
              ? `${failedPeers.map((peer) => peer.product).join('、')} 启动后端口无监听或 health 连续失败；逐卡「查看日志」看最后 20 行。`
              : '其余分区仍可正常查看。'}
          </Banner>
        )}

        {section === 'peers' && (
          <>
            {/* QUOT-564：工作流健康监控（采集→入库→辩论），独立取数、自带未接入降级 */}
            <WorkflowHealthMonitor />

            <SectionCard
              title="模块注册表"
              subtitle="唯一真源 config/sources.yaml 的 peers: 块；刷新只读探测，不执行命令"
              bodyClassName="p-0"
              actions={
                <Button
                  variant="secondary"
                  leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
                  onClick={() => board.reload()}
                  disabled={board.status === 'loading'}
                >
                  刷新探测
                </Button>
              }
            >
              <DataTable<RegistryEntry>
                columns={REGISTRY_COLUMNS}
                rows={registry}
                rowKey={(row) => row.id}
                caption="模块注册表：id / product / package / 端口 / 职责 / 是否已安装"
                density="compact"
                loading={board.status === 'loading'}
                empty={
                  <EmptyState
                    compact
                    title="注册表不可读"
                    description="没有从 config/sources.yaml 的 peers: 块读到条目；检查文件是否存在、YAML 是否合法。"
                  />
                }
              />
            </SectionCard>

            <SectionCard
              title="子模块"
              subtitle="状态只来自后端探测（目录 → 已安装 → 端口 + health）；前端不推断"
              bodyClassName="p-0"
              actions={
                <>
                  <Button
                    variant="secondary"
                    leadingIcon={<Power className="size-3.5" aria-hidden="true" />}
                    onClick={() => requestStop()}
                    disabled={!supervisorReady}
                    disabledReason={
                      supervisorReady
                        ? undefined
                        : '归属数据不可用（GET /api/supervisor/state 未接入），无法生成停止清单'
                    }
                    data-testid="exit-stop-all"
                  >
                    退出（停止清单）
                  </Button>
                  <Button
                    variant="primary"
                    leadingIcon={<Upload className="size-3.5" aria-hidden="true" />}
                    onClick={() => setImportOpen(true)}
                    disabled={!controlReady}
                    disabledReason={controlReady ? undefined : '控制面不可用'}
                  >
                    导入模块
                  </Button>
                </>
              }
            >
              <ul className="flex flex-col gap-3 p-4">
                {stopNotice && (
                  <li>
                    <Banner tone="info" title="停止清单">
                      {stopNotice}
                    </Banner>
                  </li>
                )}
                {state === 'empty' && (
                  <li>
                    <EmptyState
                      title="后端没有返回任何子模块"
                      description="确认 /api/control/peers 返回数据模块，或检查 config/sources.yaml 的 peers: 块是否登记。"
                      action={
                        <Button variant="secondary" onClick={board.reload}>
                          重新读取
                        </Button>
                      }
                    />
                  </li>
                )}
                {state !== 'empty' && peers.length === 0 && (
                  <li>
                    <EmptyState
                      compact
                      title="控制面不可用"
                      description="本地服务未连接或控制面被禁用，模块目录与端口以静态基线展示。"
                    />
                  </li>
                )}
                {peers.map((peer) => {
                  const supervised = findModule(supervisor.data, peer.id)
                  return (
                    <PeerCard
                      key={peer.id}
                      peer={peer}
                      job={jobs[peer.id]}
                      controlReady={controlReady}
                      supervisor={supervised}
                      supervisorReady={supervisorReady}
                      stoppedExternal={supervised ? notStoppedExternal.includes(supervised.module) : false}
                      acting={acting}
                      onInstall={() => {
                        setInstallError(null)
                        setInstallPeer(peer)
                      }}
                      onStart={() => void runAction(peer, 'start')}
                      onStop={() => {
                        if (supervisorReady && supervised) {
                          requestStop([supervised.module])
                        } else {
                          void runAction(peer, 'stop')
                        }
                      }}
                      onLogs={() => setLogsPeer(peer)}
                      onConfig={() => setConfigPeer(peer)}
                      onMeta={() => setMetaPeer(peer)}
                      onRetryHeal={() => {
                        if (supervised) void retryHeal(peer, supervised.module)
                      }}
                    >
                      {peer.id.replace(/-/g, '_') === 'senate' && (
                        <SenateRoleModels controlReady={controlReady} push={push} />
                      )}
                    </PeerCard>
                  )
                })}
                {components.map((component) => (
                  <ComponentCard
                    key={component.id}
                    component={component}
                    supervisor={findModule(supervisor.data, component.id)}
                    supervisorReady={supervisorReady}
                  />
                ))}
                {/* QUOT-507：代理池卡片与模块卡同列；状态只认后端 mappings[].state / owner */}
                <ProxyPoolCard
                  module={findProxyPoolModule(supervisor.data)}
                  supervisorReady={supervisorReady}
                  onRefresh={() => void supervisor.refresh()}
                />
                {components.length === 0 && state !== 'empty' && (
                  <li>
                    <EmptyState
                      compact
                      title="当前注册表未声明工具组件"
                      description="工具组件（如 ProxyFleet）单独观测、不登记为 peer；为空时只显示 peer 模块。"
                    />
                  </li>
                )}
                {(state === 'offline' || peers.length === 0) &&
                  STATIC_PEERS.map((peer) => (
                    <StaticPeerCard key={peer.id} peer={peer} reason={CONTROL_ABSENT_NOTE} />
                  ))}
              </ul>
            </SectionCard>

            <p className="text-caption text-text-muted">
              端口由后端预检给出：8001 被 WSL 转发占用时 TickHub-US 用 8002；US 本机构建没有管理端，不再占 8789；
              8788/8789 归 ProxyFleet，Senate WebUI 用 8786。前端只展示，不自动改端口。
              「非控制面启动」是归属提示，不是冲突。
            </p>
            <p className="text-caption text-text-muted">
              归属（主程序托管 / 外部接管 / 非托管）与自愈状态只取后端账本视图{' '}
              <code className="ldst-num">GET /api/supervisor/state</code>，前端不扫端口猜归属。
              {supervisor.data?.configured === false &&
                ' 当前启停引擎尚未接线（482-B）：清单可查看，停止动作以后端真实返回为准，未接入时页面不会假装已停。'}
            </p>
          </>
        )}

        {section === 'ai' && <AiSection controlReady={controlReady} push={push} />}

        {section === 'ports' && (
          <PortsSection
            board={board}
            portRows={portRows}
            stateConfig={stateConfig}
            controlReady={controlReady}
            productOf={productOf}
            push={push}
            onHealed={() => {
              // 静默重取：reload 会整屏回 loading 骨架，把自愈结果横幅连同面板一起卸载掉。
              void board.refresh()
              void supervisor.refresh()
            }}
            supervisorState={supervisor.data}
          />
        )}

        {section === 'about' && <AboutSection health={health} stateConfig={stateConfig} />}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      <div className="flex flex-col gap-5 desk:flex-row desk:items-start">
        {menu}
        <div className="min-w-0 flex-1">{body}</div>
      </div>

      <ConfirmDialog
        open={installPeer !== null}
        title={installPeer ? `安装 ${installPeer.product}` : '安装'}
        description="后端会按下面五步完成安装（不会多做什么）。取消不会创建任务、不会写日志。"
        confirmLabel="确认安装"
        loading={installBusy}
        onCancel={() => {
          if (installBusy) return
          setInstallPeer(null)
          setInstallError(null)
        }}
        onConfirm={() => void confirmInstall()}
      >
        {installPeer && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-1 text-caption text-text-secondary">
              <Terminal className="size-3.5" aria-hidden="true" />
              会自动做的事
            </div>
            <ol className="flex list-decimal flex-col gap-1 rounded border border-border bg-sunken p-3 pl-7 text-caption leading-relaxed text-text">
              {installSteps(installPeer).map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            {installError && <Banner tone="danger" title="安装任务创建失败">{installError}</Banner>}
          </div>
        )}
      </ConfirmDialog>

      <LogsDrawer peer={logsPeer} onClose={() => setLogsPeer(null)} />

      <ImportPeerDrawer
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={() => board.reload()}
        push={push}
      />

      <ConfigDrawer
        peer={configPeer}
        onClose={() => setConfigPeer(null)}
        onSaved={() => board.reload()}
        push={push}
      />

      <Drawer
        open={metaPeer !== null}
        onClose={() => setMetaPeer(null)}
        title={metaPeer ? `${metaPeer.product} · 安装元数据` : '安装元数据'}
        subtitle="目录 / venv / 版本 / 打包产物"
      >
        {metaPeer && (
          <dl className="flex flex-col gap-2 text-body">
            {[
              { label: '目录', value: metaPeer.directory, mono: true },
              { label: '目录存在', value: metaPeer.directory_exists ? '是' : '否' },
              { label: '已登记 sources.yaml', value: metaPeer.registered ? '是' : '否' },
              { label: 'venv 路径', value: metaPeer.venv ?? MISSING, mono: true },
              { label: '包版本', value: metaPeer.version ?? MISSING, mono: true },
              { label: 'wheel', value: metaPeer.dist.wheel ?? MISSING, mono: true },
              { label: '可编辑安装', value: metaPeer.dist.editable ? '支持（pip install -e .）' : '不支持' },
              { label: '.env 文件', value: metaPeer.dist.env_file || MISSING, mono: true },
              { label: 'health', value: metaPeer.health.detail || MISSING },
            ].map((row) => (
              <div key={row.label} className="flex items-baseline justify-between gap-3 border-b border-border pb-1 last:border-b-0">
                <dt className="shrink-0 text-caption text-text-secondary">{row.label}</dt>
                <dd className={cn('min-w-0 break-all text-right text-text', row.mono && 'ldst-num text-caption')}>
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </Drawer>

      <StopListDialog
        open={stopList !== null}
        candidates={stopList ?? []}
        loading={stopBusy}
        error={stopError}
        onCancel={cancelStop}
        onConfirm={() => {
          if (stopList) void executeStop(stopList, true)
        }}
      />

      <Toasts items={items} onDismiss={dismiss} />
    </div>
  )
}
