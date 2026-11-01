import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  Download,
  ExternalLink,
  FileText,
  Info,
  Play,
  RefreshCw,
  Sliders,
  Square,
  Terminal,
} from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  type BoardPayload,
  type JobView,
  type PeerLogs,
  type PeerSnapshot,
  type PeerStatus,
  type HealthPayload,
  type PortConflict,
  type PortOwner,
  type StateConfigPayload,
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
import { setDensityPref, setTheme, useDensity, useThemeChoice } from '../lib/prefs'
import type { Density, ThemeChoice } from '../lib/theme'
import { cn } from '../lib/cn'

/**
 * 设置页（P-07 / DESIGN-SPEC §4.8 + PRD-SETTINGS-CONTROL-PLANE）。
 *
 * 四个分区（左侧 176px 二级菜单，`?tab=` 深链）：
 *   子模块   —— 四张 peer 状态卡；状态**只**取后端 `status` / `status_label`，前端不推断。
 *   AI 接入  —— 端点 / 模型 / 密钥；密钥只写不读回（只显示 `set` + `preview`）。
 *   数据与端口 —— 端口占用表（✅ 空闲 / ⚠️ 被占用含 PID+进程 / ❌ 越界）。
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
  { value: 'peers', label: '子模块', hint: '四个数据 / 资讯 / 合议模块' },
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

/** PRD §5 白名单键对应的 Python 模块名，仅用于展示安装命令的最后一步。 */
const PEER_MODULE: Record<string, string> = {
  tickhub: 'tickhub',
  'tickhub-us': 'tickhub_us',
  wire: 'quotafuse_wire',
  senate: 'stocksenate',
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
  web: number
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
    web: 8789,
    webLabel: '管理端',
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
    web: 8788,
    webLabel: 'WebUI',
  },
]

const CONTROL_ABSENT_NOTE = '控制面接口未接入（本轮不实现）：先运行 `ldst run`，或确认 LDST_CONTROL_ENABLED=1。'

function trimSlash(path: string): string {
  return path.replace(/[\\/]+$/, '')
}

/**
 * PRD §4「安装的确切动作」——五步，写死，不允许扩展。
 * 前端只**展示**这段命令，实际执行在后端线程池；前端不执行任何命令。
 */
function installCommands(peer: PeerSnapshot): string[] {
  const python = `${trimSlash(peer.directory)}\\.venv\\Scripts\\python`
  const module = PEER_MODULE[peer.id] ?? peer.id.replace(/-/g, '_')
  const target = peer.dist.wheel ? `"${peer.dist.wheel}"` : '-e .'
  return [
    `cd ${peer.directory}`,
    'python -m venv .venv            # 已存在则跳过',
    `${python} -m pip install -U pip`,
    `${python} -m pip install ${target}`,
    `${python} -c "import ${module}; print(${module}.__version__)"`,
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
  acting,
  onInstall,
  onStart,
  onStop,
  onLogs,
  onConfig,
  onMeta,
}: {
  peer: PeerSnapshot
  job: JobView | undefined
  controlReady: boolean
  acting: string | null
  onInstall: () => void
  onStart: () => void
  onStop: () => void
  onLogs: () => void
  onConfig: () => void
  onMeta: () => void
}) {
  const statusLabel = peer.status_label || PEER_STATUS_LABEL[peer.status]
  const uptime = peer.last_job?.action === 'start' && peer.last_job.status === 'ok' ? uptimeLabel(peer.last_job.started_at) : null
  const jobActive = job?.status === 'queued' || job?.status === 'running'

  return (
    <li className="rounded border border-border bg-surface shadow-card">
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusDot tone={PEER_DOT[peer.status]} pulse={peer.status === 'starting'}>
                {statusLabel}
              </StatusDot>
              <h3 className="truncate text-subtitle font-semibold text-text">{peer.product}</h3>
              {peer.version && <span className="ldst-num text-caption text-text-muted">v{peer.version}</span>}
            </div>
            <p className="mt-1 text-caption text-text-secondary">{peer.role}</p>
          </div>
          <IconButton
            label={`${peer.product} 安装元数据`}
            icon={<Info className="size-4" aria-hidden="true" />}
            onClick={onMeta}
          />
        </div>

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
            {peer.port_conflicts.map((conflict) => (
              <li key={conflict.port} className="flex items-start gap-1 text-caption text-warn">
                <AlertTriangle className="mt-half size-3.5 shrink-0" aria-hidden="true" />
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
            ))}
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
            disabled={!controlReady}
            onClick={onConfig}
            leadingIcon={<Sliders className="size-3.5" aria-hidden="true" />}
          >
            编辑配置
          </Button>
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
          <span className="ldst-num">{peer.web}</span>（来自 config/sources.yaml，非探测值）
        </p>
        <p className="truncate text-caption text-text-muted" title={peer.directory}>
          目录 <span className="ldst-num">{peer.directory}</span>
        </p>
        <p className="text-caption text-text-muted">{reason}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled disabledReason="控制面未接入">
            安装
          </Button>
          <Button variant="secondary" disabled disabledReason="控制面未接入">
            启动
          </Button>
          <Button variant="ghost" disabled disabledReason="控制面未接入">
            查看日志
          </Button>
        </div>
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
      rows.push({
        port,
        usage,
        state: 'busy',
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
  llm_provider: 'LLM 提供方',
  deep_think_llm: '深度思考模型',
  quick_think_llm: '快速思考模型',
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
            disabled={!data || !dirty}
            disabledReason={!dirty ? '没有修改' : undefined}
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
          {!data.exists && (
            <p className="text-caption text-warn">配置文件尚未生成，保存后创建（权限 0600）。</p>
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
        </div>
      )}
    </Drawer>
  )
}

// ---------------------------------------------------------------- 分区二：AI 接入

interface AiTarget {
  id: string
  label: string
  /** 写入的配置键；`base` 为 null 表示该模块端点不在白名单（由 llm_provider 决定） */
  keys: { base: string | null; model: string; key: string }
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
    keys: { base: null, model: 'deep_think_llm', key: 'MINIMAX_API_KEY' },
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
        if (model.trim() !== '') values[target.keys.model] = model.trim()
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

  return (
    <div className="flex flex-col gap-4">
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
                    写入 {target.keys.base ?? '端点由 llm_provider 决定'} / {target.keys.model} / {target.keys.key}
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
    </div>
  )
}

// ---------------------------------------------------------------- 分区三：端口与目录

/**
 * `?tab=ports` 的内容。控制面未启用时也要渲染本段（§11.18「每段有自身
 * loading/empty/error」），端口取不到就逐行 `—`，不显示假定默认值。
 */
function PortsSection({
  board,
  portRows,
  stateConfig,
}: {
  board: AsyncResult<BoardPayload>
  portRows: PortRow[]
  stateConfig: AsyncResult<StateConfigPayload>
}) {
  const density = useDensity()
  return (
    <div className="flex flex-col gap-4">
      <SectionCard
        title="端口占用"
        subtitle={
          <>
            看板 <span className="ldst-num">{countOrMissing(board.data?.ports.board)}</span> / MCP{' '}
            <span className="ldst-num">{countOrMissing(board.data?.ports.mcp)}</span> · 四个模块
          </>
        }
        bodyClassName="p-0"
        actions={
          <Button variant="secondary" disabled disabledReason="本轮无体检接口，请运行 ldst doctor">
            一键体检
          </Button>
        }
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
          <EmptyState compact title="没有读到配置目录" description="/api/state/config 返回为空；确认本机已跑过一次 ldst run。" />
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
    { label: 'ldst 版本', value: MISSING, note: '数据面未暴露 CLI 版本' },
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
      note: config?.state_index_present === false ? '索引未建立（ldst state rebuild）' : undefined,
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

  const { items, push, dismiss } = useToasts()

  const [jobs, setJobs] = useState<Record<string, JobView>>({})
  const [installPeer, setInstallPeer] = useState<PeerSnapshot | null>(null)
  const [installBusy, setInstallBusy] = useState(false)
  const [installError, setInstallError] = useState<string | null>(null)
  const [logsPeer, setLogsPeer] = useState<PeerSnapshot | null>(null)
  const [configPeer, setConfigPeer] = useState<PeerSnapshot | null>(null)
  const [metaPeer, setMetaPeer] = useState<PeerSnapshot | null>(null)
  const [acting, setActing] = useState<string | null>(null)

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

  const confirmInstall = async (): Promise<void> => {
    if (!installPeer) return
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
              ? `${peers.length} 个模块 · 运行中 ${peers.filter((peer) => peer.status === 'running').length}`
              : '读取模块状态…'
      }
      badge={<DemoStateBadge state={forced} />}
      actions={
        <Button
          variant="primary"
          loading={board.status === 'loading' && state !== 'loading'}
          disabled={state === 'loading'}
          onClick={() => {
            board.reload()
            stateConfig.reload()
            health.reload()
          }}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          刷新状态
        </Button>
      }
    />
  )

  const menu = (
    <nav aria-label="设置分区" className="w-menu shrink-0">
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
                <span className="text-caption text-text-muted">{item.hint}</span>
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
        {section === 'ports' && <PortsSection board={board} portRows={portRows} stateConfig={stateConfig} />}
        {section === 'about' && <AboutSection health={health} stateConfig={stateConfig} />}
      </div>
    )
  } else if (controlOff || (httpStatus === 404 && state !== 'offline')) {
    body = (
      <div className="flex flex-col gap-4">
        <Banner tone="danger" title="控制面未启用（LDST_CONTROL_ENABLED=0）">
          <>
            设置页的安装 / 启动 / 停止全部不可用；数据面（首页、盯盘、复盘、验证）不受影响。以{' '}
            <span className="ldst-num">LDST_CONTROL_ENABLED=1</span> 重启 <span className="ldst-num">ldst run</span> 后恢复。
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
        {section === 'ports' && <PortsSection board={board} portRows={portRows} stateConfig={stateConfig} />}
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
            <SectionCard
              title="子模块"
              subtitle="状态只来自后端探测（目录 → 已安装 → 端口 + health）；前端不推断"
              bodyClassName="p-0"
            >
              <ul className="flex flex-col gap-3 p-4">
                {state === 'empty' && (
                  <li>
                    <EmptyState
                      title="后端没有返回任何子模块"
                      description="确认 `/api/control/peers` 返回四个 peer（tickhub / tickhub-us / wire / senate），或检查 config/sources.yaml 是否登记。"
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
                      description="本地服务未连接或控制面被禁用，四个模块的目录与端口以静态基线展示。"
                    />
                  </li>
                )}
                {peers.map((peer) => (
                  <PeerCard
                    key={peer.id}
                    peer={peer}
                    job={jobs[peer.id]}
                    controlReady={controlReady}
                    acting={acting}
                    onInstall={() => {
                      setInstallError(null)
                      setInstallPeer(peer)
                    }}
                    onStart={() => void runAction(peer, 'start')}
                    onStop={() => void runAction(peer, 'stop')}
                    onLogs={() => setLogsPeer(peer)}
                    onConfig={() => setConfigPeer(peer)}
                    onMeta={() => setMetaPeer(peer)}
                  />
                ))}
                {(state === 'offline' || peers.length === 0) &&
                  STATIC_PEERS.map((peer) => (
                    <StaticPeerCard key={peer.id} peer={peer} reason={CONTROL_ABSENT_NOTE} />
                  ))}
              </ul>
            </SectionCard>

            <p className="text-caption text-text-muted">
              端口冲突由后端预检给出：8001 被外部进程占用时 TickHub-US 用 8002；admin 8787 与 CN 冲突时 US 用 8789。
              前端只展示，不自动改端口。
            </p>
          </>
        )}

        {section === 'ai' && <AiSection controlReady={controlReady} push={push} />}

        {section === 'ports' && <PortsSection board={board} portRows={portRows} stateConfig={stateConfig} />}

        {section === 'about' && <AboutSection health={health} stateConfig={stateConfig} />}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      <div className="flex items-start gap-5">
        {menu}
        <div className="min-w-0 flex-1">{body}</div>
      </div>

      <ConfirmDialog
        open={installPeer !== null}
        title={installPeer ? `安装 ${installPeer.product}` : '安装'}
        description="后端将按下面五步执行（不允许扩展）。取消不会创建任务、不会写日志。"
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
              将执行的命令
            </div>
            <pre className="ldst-num overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-sunken p-3 text-caption leading-relaxed text-text">
              {installCommands(installPeer).join('\n')}
            </pre>
            {installError && <Banner tone="danger" title="安装任务创建失败">{installError}</Banner>}
          </div>
        )}
      </ConfirmDialog>

      <LogsDrawer peer={logsPeer} onClose={() => setLogsPeer(null)} />

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

      <Toasts items={items} onDismiss={dismiss} />
    </div>
  )
}
