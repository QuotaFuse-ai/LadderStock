import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, FlaskConical, RefreshCw } from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  type SenateProvider,
  type SenateRoleEntry,
  type SenateRoleModelsPayload,
  type SenateRoleStatus,
  type SenateTestResult,
} from '../api'
import { Badge, Banner, Button, EmptyState, Field, Input, Select, SkeletonText, type BadgeTone, type ToastItem } from './ui'

/**
 * StockSenate 辩论角色多模型（QUOT-520）。
 *
 * 这是 StockSenate 自己的 `config/llm_config.toml` → `[llm.role_llms]`，与
 * 「AI 接入 → 按模块配置」写的 `config/ai.yaml` 是两套配置，互不影响。
 *
 * 角色清单、provider、模型候选全部来自 `GET /control/peers/senate/role-models`，
 * 前端不硬编码任何键名；写回时只提交改动的角色，并带 GET 拿到的 `revision`
 * （`SENATE_STALE` 由调用方保留草稿、提示刷新）。密钥永不进页面：`api_key_env`
 * 只接受环境变量名，保存后写入全局 `[llm].api_key_env`。
 */

/** `SENATE_*` 错误码 → 页面文案；未知码回落服务端 message。 */
export const SENATE_ERROR_TEXT: Record<string, string> = {
  SENATE_UNAVAILABLE: 'StockSenate 目录或 llm_config.toml 不可用',
  SENATE_INVALID_VALUE: '提交的角色或取值不合法',
  SENATE_SECRET_REJECTED: '不接受密钥内容：api_key_env 只能填环境变量名',
  SENATE_STALE: '配置文件已被外部修改，请刷新后重试',
  SENATE_WRITE_FAILED: '写入 llm_config.toml 失败，原文件未改动',
  SENATE_NO_API_KEY: '找不到 API key（只读环境变量与 Senate 自己的 .env）',
  SENATE_NETWORK_DENIED: '目标地址被拒绝（内网 / 保留地址，或 provider 拒绝密钥）',
  SENATE_UNREACHABLE: '模型服务不可达（解析失败、超时、限流或非 2xx）',
}

export function senateErrorText(code: string | null | undefined, fallback: string): string {
  return (code && SENATE_ERROR_TEXT[code]) || fallback
}

function errorCodeOf(error: unknown): string | null {
  return isApiError(error) ? error.code : null
}

interface RoleDraft {
  provider: string
  model: string
  /** 该角色自己的网关地址；留空 = 用全局 / provider 默认。 */
  backendUrl: string
  /** 该角色自己的密钥；留空 = 用全局 api_key_env。 */
  apiKey: string
}

interface PanelNotice {
  tone: 'ok' | 'danger' | 'info'
  text: string
}

const KEY_ENV_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/

function providerByName(providers: SenateProvider[], name: string): SenateProvider | undefined {
  return providers.find((provider) => provider.name === name)
}

function sourceOf(
  role: SenateRoleStatus,
  globalProvider: string | null,
): { tone: BadgeTone; label: string } {
  if (role.configured) return { tone: 'ok', label: '角色 TOML' }
  if (globalProvider) return { tone: 'neutral', label: '全局继承' }
  return { tone: 'warn', label: '未配置' }
}

/** 按 StockSenate `agent_roles` 的 `kind` 分组，顺序即合议流程；未知 kind 归「其他」。
 *  16 个角色平铺是一屏放不下的一长条，分组后默认只展开分析师（7 个）。 */
const ROLE_GROUPS: { kind: string; title: string; note: string }[] = [
  { kind: 'analyst', title: '分析师', note: '7 个分析师并行产出独立观点' },
  { kind: 'researcher', title: '多空研究员', note: '多头 / 空头就分析师结论展开辩论' },
  { kind: 'manager', title: '研究经理 / 投资经理', note: '汇总辩论，出研究结论与组合意见' },
  { kind: 'trader', title: '交易员', note: '按研究结论给出方案（不含交易指令）' },
  { kind: 'risk', title: '风险辩手', note: '激进 / 中立 / 保守三方风险辩论' },
  { kind: 'judge', title: '质量闸门', note: '校验输出质量，未达标不进终审' },
]

const OTHER_GROUP = { kind: 'other', title: '其他角色', note: '后端未标注 kind 的角色' }

function groupRoles(roles: SenateRoleStatus[]): { kind: string; title: string; note: string; roles: SenateRoleStatus[] }[] {
  const buckets = new Map<string, SenateRoleStatus[]>()
  for (const role of roles) {
    const kind = role.kind && role.kind !== '' ? role.kind : 'other'
    const list = buckets.get(kind)
    if (list) list.push(role)
    else buckets.set(kind, [role])
  }
  const ordered = ROLE_GROUPS.map((group) => ({ ...group, roles: buckets.get(group.kind) ?? [] })).filter(
    (group) => group.roles.length > 0,
  )
  const rest = buckets.get(OTHER_GROUP.kind)
  if (rest && rest.length > 0) ordered.push({ ...OTHER_GROUP, roles: rest })
  return ordered
}

export function SenateRoleModels({
  controlReady,
  push,
}: {
  controlReady: boolean
  push?: (message: string, tone?: ToastItem['tone']) => void
}) {
  const [expanded, setExpanded] = useState(true)
  // 分组折叠：默认只展开「分析师（7）」，其余收起，避免 16 条一起铺满。
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ analyst: true })
  const [payload, setPayload] = useState<SenateRoleModelsPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<{ code: string | null; detail: string } | null>(null)
  const [drafts, setDrafts] = useState<Record<string, RoleDraft>>({})
  const [keyEnv, setKeyEnv] = useState('')
  const [busy, setBusy] = useState<Record<string, 'save' | 'test' | undefined>>({})
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})
  const [tests, setTests] = useState<Record<string, SenateTestResult | undefined>>({})
  const [notice, setNotice] = useState<PanelNotice | null>(null)
  const [stale, setStale] = useState(false)
  const [savingAll, setSavingAll] = useState(false)
  /** 当前展开技能勾选面板的角色；null 表示都收起。 */
  const [skillEditing, setSkillEditing] = useState<string | null>(null)
  const [skillDrafts, setSkillDrafts] = useState<Record<string, string[]>>({})
  const [skillBusy, setSkillBusy] = useState<string | null>(null)
  /** 当前打开提示词编辑器的角色；null 表示都收起。 */
  const [promptEditing, setPromptEditing] = useState<string | null>(null)
  const [promptText, setPromptText] = useState('')
  const [promptMeta, setPromptMeta] = useState<{ path: string | null; exists: boolean }>({
    path: null,
    exists: false,
  })
  const [promptBusy, setPromptBusy] = useState(false)

  const load = useCallback(async (): Promise<void> => {
    setLoading(true)
    setLoadError(null)
    setStale(false)
    try {
      const fresh = await api.control.senateRoleModels()
      setPayload(fresh)
      setNotice(null)
    } catch (error) {
      const info = describeApiError(error)
      setLoadError({ code: errorCodeOf(error), detail: info.detail })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!controlReady) return
    void load()
  }, [controlReady, load])

  // 读回 / 保存后的响应都是全量状态：草稿整体重置，不做本地增量拼接。
  useEffect(() => {
    if (!payload) return
    const next: Record<string, RoleDraft> = {}
    for (const role of payload.roles) {
      next[role.role] = {
        provider: role.provider ?? '',
        model: role.model ?? '',
        backendUrl: role.backend_url ?? '',
        apiKey: '',
      }
    }
    setDrafts(next)
    setKeyEnv(payload.global?.api_key_env ?? '')
  }, [payload])

  const providers = payload?.providers ?? []
  const globalProvider = payload?.global?.provider ?? null
  const baselineKeyEnv = payload?.global?.api_key_env ?? ''
  // 全局密钥变量只在顶部填一次（写 `[llm].api_key_env`，所有角色共用），
  // 不再在每个角色行里重复同一个输入框。
  const globalKeySuggestion = providerByName(providers, globalProvider ?? '')?.api_key_env ?? ''

  const dirtyRoles = useMemo(() => {
    if (!payload) return []
    return payload.roles
      .filter((role) => {
        const draft = drafts[role.role]
        if (!draft) return false
        return (
          draft.provider !== (role.provider ?? '') ||
          draft.model !== (role.model ?? '') ||
          draft.backendUrl !== (role.backend_url ?? '') ||
          draft.apiKey.trim() !== ''
        )
      })
      .map((role) => role.role)
  }, [payload, drafts])

  const keyEnvDirty = keyEnv !== baselineKeyEnv
  const hasChanges = dirtyRoles.length > 0 || keyEnvDirty

  const updateDraft = (role: string, patch: Partial<RoleDraft>): void => {
    setDrafts((current) => {
      const draft = current[role]
      if (!draft) return current
      return { ...current, [role]: { ...draft, ...patch } }
    })
    setRowErrors((current) => ({ ...current, [role]: '' }))
  }

  const changeProvider = (role: string, name: string): void => {
    updateDraft(role, { provider: name })
    const provider = providerByName(providers, name)
    // 变量名只在空着时带出建议值，不覆盖用户手填的内容。
    if (provider?.api_key_env && keyEnv.trim() === '') setKeyEnv(provider.api_key_env)
  }

  const buildChanges = (): {
    roles: Record<string, SenateRoleEntry>
    globalKeyEnv: string | null
    errors: Record<string, string>
  } => {
    const roles: Record<string, SenateRoleEntry> = {}
    const errors: Record<string, string> = {}
    if (!payload) return { roles, globalKeyEnv: null, errors }

    for (const name of dirtyRoles) {
      const draft = drafts[name]
      if (!draft) continue
      const provider = draft.provider.trim()
      const model = draft.model.trim()
      if (!provider && !model) continue
      if (!provider || !model) {
        errors[name] = 'provider 与 model 需同时填写；留空两格表示继续继承'
        continue
      }
      const url = draft.backendUrl.trim()
      if (url && !/^https?:\/\//i.test(url)) {
        errors[name] = '角色地址必须以 http:// 或 https:// 开头'
        continue
      }
      const entry: SenateRoleEntry = { provider, model }
      if (url) entry.backend_url = url
      const key = draft.apiKey.trim()
      if (key) entry.api_key = key
      roles[name] = entry
    }

    // 全局密钥变量走独立字段 `global_key_env`，不再塞进某个角色条目。
    const globalKeyEnv = keyEnvDirty ? keyEnv.trim() || null : null
    if (globalKeyEnv && !KEY_ENV_PATTERN.test(globalKeyEnv)) {
      errors.__key_env = 'api_key_env 只能填环境变量名（字母、数字、下划线）'
    }
    return { roles, globalKeyEnv, errors }
  }

  const runTest = async (
    role: string,
    override?: { provider?: string; model?: string; api_key_env?: string },
  ): Promise<SenateTestResult | null> => {
    setBusy((current) => ({ ...current, [role]: 'test' }))
    try {
      const result = await api.control.testSenateRoleModel({
        role,
        ...(override?.provider ? { provider: override.provider } : {}),
        ...(override?.model ? { model: override.model } : {}),
        ...(override?.api_key_env ? { api_key_env: override.api_key_env } : {}),
      })
      setTests((current) => ({ ...current, [role]: result }))
      return result
    } catch (error) {
      const info = describeApiError(error)
      const failed: SenateTestResult = {
        ok: false,
        role,
        provider: override?.provider ?? '',
        model: override?.model ?? '',
        latency_ms: 0,
        error_code: errorCodeOf(error),
        message: info.detail,
      }
      setTests((current) => ({ ...current, [role]: failed }))
      return failed
    } finally {
      setBusy((current) => ({ ...current, [role]: undefined }))
    }
  }

  /** 勾选/取消某个技能：草稿按角色隔离，未改动的角色回落到已保存值。 */
  const toggleSkill = (role: string, name: string): void => {
    setSkillDrafts((current) => {
      const saved = payload?.roles.find((item) => item.role === role)?.skills ?? []
      const base = current[role] ?? saved
      const next = base.includes(name) ? base.filter((item) => item !== name) : [...base, name]
      return { ...current, [role]: next }
    })
  }

  const saveSkills = async (role: string, skills: string[]): Promise<void> => {
    setSkillBusy(role)
    setRowErrors((current) => ({ ...current, [role]: '' }))
    try {
      await api.control.saveSenateRoleSkills(role, skills)
      setSkillEditing(null)
      setSkillDrafts((current) => {
        const next = { ...current }
        delete next[role]
        return next
      })
      await load()
    } catch (error) {
      const info = describeApiError(error)
      setRowErrors((current) => ({
        ...current,
        [role]: senateErrorText(info.code, info.detail),
      }))
    } finally {
      setSkillBusy(null)
    }
  }

  const openPrompt = async (role: string): Promise<void> => {
    if (promptEditing === role) {
      setPromptEditing(null)
      return
    }
    setPromptEditing(role)
    setPromptBusy(true)
    setRowErrors((current) => ({ ...current, [role]: '' }))
    try {
      const data = await api.control.senateRolePrompt(role)
      setPromptText(data.text)
      setPromptMeta({ path: data.path, exists: data.exists })
    } catch (error) {
      const info = describeApiError(error)
      setRowErrors((current) => ({ ...current, [role]: senateErrorText(info.code, info.detail) }))
      setPromptEditing(null)
    } finally {
      setPromptBusy(false)
    }
  }

  const savePrompt = async (role: string): Promise<void> => {
    setPromptBusy(true)
    setRowErrors((current) => ({ ...current, [role]: '' }))
    try {
      const result = await api.control.saveSenateRolePrompt(role, promptText)
      setNotice({
        tone: 'ok',
        text: `已保存 ${role} 的提示词（${result.chars} 字${result.backup ? '，原文件已备份 .bak' : ''}）`,
      })
      setPromptEditing(null)
    } catch (error) {
      const info = describeApiError(error)
      setRowErrors((current) => ({ ...current, [role]: senateErrorText(info.code, info.detail) }))
    } finally {
      setPromptBusy(false)
    }
  }

  const testRow = (role: SenateRoleStatus): void => {
    // 首帧草稿 effect 还没跑完时按已保存值探测，避免丢 provider/model。
    const draft = drafts[role.role] ?? { provider: role.provider ?? '', model: role.model ?? '' }
    const key = keyEnv.trim()
    void runTest(role.role, {
      ...(draft?.provider.trim() ? { provider: draft.provider.trim() } : {}),
      ...(draft?.model.trim() ? { model: draft.model.trim() } : {}),
      ...(key !== '' && key !== baselineKeyEnv ? { api_key_env: key } : {}),
    })
  }

  const submit = async (alsoTest: boolean): Promise<void> => {
    if (!payload) return
    const { roles, globalKeyEnv, errors } = buildChanges()
    if (Object.keys(errors).length > 0) {
      setRowErrors(errors)
      setNotice(null)
      return
    }
    if (Object.keys(roles).length === 0 && !globalKeyEnv) {
      setNotice({ tone: 'info', text: '没有需要保存的改动' })
      return
    }
    setSavingAll(true)
    setStale(false)
    setNotice(null)
    try {
      const fresh = await api.control.saveSenateRoleModels(roles, payload.revision, globalKeyEnv)
      const updated = fresh.updated ?? Object.keys(roles)
      setPayload(fresh)
      setNotice({
        tone: 'ok',
        text:
          `已保存 ${updated.length} 个角色` +
          (fresh.api_key_env_written ? '，并更新全局 api_key_env' : '') +
          `。${fresh.engine.reload_hint}`,
      })
      push?.(`已保存 ${updated.length} 个辩论角色`, 'ok')
      if (alsoTest) {
        const results: SenateTestResult[] = []
        for (const [role, entry] of Object.entries(roles)) {
          const result = await runTest(role, {
            provider: entry.provider,
            model: entry.model,
            ...(entry.api_key_env ? { api_key_env: entry.api_key_env } : {}),
          })
          if (result) results.push(result)
        }
        const failed = results.filter((result) => !result.ok)
        const skipped = results.filter((result) => result.ok && result.skipped).length
        push?.(
          failed.length > 0
            ? `保存成功，但 ${failed.length} 个角色连通性失败`
            : `保存成功，${results.length - skipped} 个角色连通${skipped > 0 ? `，${skipped} 个 offline 已跳过` : ''}`,
          failed.length > 0 ? 'danger' : 'ok',
        )
      }
    } catch (error) {
      const code = errorCodeOf(error)
      const info = describeApiError(error)
      if (code === 'SENATE_STALE') setStale(true)
      setNotice({ tone: 'danger', text: senateErrorText(code, info.detail) })
      push?.(`保存失败：${senateErrorText(code, info.detail)}`, 'danger')
    } finally {
      setSavingAll(false)
    }
  }

  const roleCount = payload?.role_count ?? payload?.roles.length ?? 0
  const configuredCount = payload?.roles.filter((role) => role.configured).length ?? 0

  return (
    <section
      className="mt-1 flex flex-col gap-2 rounded border border-border bg-surface-2 px-3 py-2"
      data-testid="senate-role-models"
      aria-label="StockSenate 辩论角色多模型"
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
        className="flex items-center gap-2 text-left"
      >
        {expanded ? (
          <ChevronDown className="size-4 text-text-muted" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-4 text-text-muted" aria-hidden="true" />
        )}
        <span className="text-body font-medium text-text">辩论角色多模型</span>
        <Badge tone="info">StockSenate 辩论角色</Badge>
        {payload && (
          <span className="text-caption text-text-muted">
            已配置 <span className="ldst-num">{configuredCount}</span> /{' '}
            <span className="ldst-num">{roleCount}</span>
          </span>
        )}
        <span className="ml-auto text-caption text-text-muted">
          写 llm_config.toml，不是 ai.yaml 的按模块配置
        </span>
      </button>

      {expanded && (
        <div className="flex flex-col gap-2">
          <p className="text-caption text-text-secondary">
            这是 <span className="ldst-num">StockSenate 辩论引擎</span> 的角色配置（
            <span className="ldst-num">llm_config.toml → [llm.role_llms]</span>）；「AI 接入 → 按模块配置」写的是{' '}
            <span className="ldst-num">config/ai.yaml</span>，两者互不影响。
          </p>

          {!controlReady && <p className="text-caption text-text-muted">控制面不可用，暂时不能读写 Senate 配置。</p>}
          {controlReady && loading && <SkeletonText lines={4} />}

          {controlReady && !loading && loadError && (
            <div className="flex flex-col gap-2">
              <Banner
                tone={loadError.code === 'SENATE_UNAVAILABLE' ? 'warn' : 'danger'}
                title={
                  loadError.code === 'SENATE_UNAVAILABLE'
                    ? 'StockSenate 辩论配置不可用'
                    : '读不到 StockSenate 辩论角色'
                }
              >
                <span className="flex flex-wrap items-center gap-2">
                  {loadError.code && <Badge tone="flat">{loadError.code}</Badge>}
                  <span>{senateErrorText(loadError.code, loadError.detail)}</span>
                  <span className="text-text-muted">
                    页面只读写 StockSenate 自己的 llm_config.toml，不改其代码与校验规则。
                  </span>
                </span>
              </Banner>
              <div>
                <Button
                  variant="secondary"
                  onClick={() => void load()}
                  leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
                >
                  重试
                </Button>
              </div>
            </div>
          )}

          {controlReady && !loading && !loadError && payload && payload.roles.length === 0 && (
            <EmptyState compact title="后端没有返回辩论角色" description="检查 QUOT-518 的 Senate 桥接是否已接入。" />
          )}

          {controlReady && !loading && !loadError && payload && payload.roles.length > 0 && (
            <>
              {payload.engine.env_override && (
                <Banner tone="warn" title="环境变量覆盖了 TOML：保存暂不生效">
                  {payload.engine.hint}
                </Banner>
              )}
              {stale && (
                <Banner tone="danger" title="配置文件已被外部修改（SENATE_STALE）">
                  <span className="flex flex-wrap items-center gap-2">
                    <span>刷新会重新读取磁盘上的最新版本并丢弃当前草稿。</span>
                    <Button size="md" variant="secondary" onClick={() => void load()}>
                      刷新
                    </Button>
                  </span>
                </Banner>
              )}
              {notice && (
                <Banner
                  tone={notice.tone}
                  title={notice.tone === 'danger' ? '保存失败' : notice.tone === 'ok' ? '已保存' : '提示'}
                >
                  {notice.text}
                </Banner>
              )}

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-text-muted">
                <span>
                  文件 <span className="ldst-num">{payload.engine.config_file}</span>
                  {payload.config_exists === false && '（不存在，保存时新建）'}
                </span>
                <span>
                  生效 provider <Badge tone="neutral">{payload.engine.provider ?? '未设置'}</Badge>
                  {payload.engine.env_override && '（环境变量）'}
                </span>
                <span>
                  revision <span className="ldst-num">{payload.revision}</span>
                </span>
                <span>契约来源 {payload.engine.contract_source}</span>
                {payload.memory && (
                  <span data-testid="senate-memory">
                    记忆{' '}
                    <span className="ldst-num">{payload.memory.count}</span> 条 ·{' '}
                    {payload.memory.backend === 'mem0' ? 'mem0' : '本地 JSONL'}
                    {payload.memory.mem0_url ? `（${payload.memory.mem0_url}）` : ''}
                  </span>
                )}
                {payload.skill_library && (
                  <span>
                    技能库 <span className="ldst-num">{payload.skill_library.length}</span> 个
                  </span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-text-muted">
                <span>
                  全局 deep <span className="ldst-num">{payload.global.deep_think_llm ?? '未设置'}</span>
                </span>
                <span>
                  quick <span className="ldst-num">{payload.global.quick_think_llm ?? '未设置'}</span>
                </span>
                <span>
                  backend_url {payload.global.backend_url_set ? '已设置' : '未设置'}
                </span>
                {Object.keys(payload.aliases).length > 0 && (
                  <span>
                    别名{' '}
                    {Object.entries(payload.aliases).map(([alias, canonical]) => (
                      <Badge key={alias} tone="flat" className="mr-1">
                        {alias} → {canonical}
                      </Badge>
                    ))}
                    （提交别名会归一到 canonical 角色）
                  </span>
                )}
              </div>

              <p className="text-caption text-text-muted">{payload.engine.reload_hint}</p>

              <Field
                label="api_key_env（全局，所有角色共用）"
                htmlFor="senate-global-key"
                hint="只存变量名；写入 [llm].api_key_env。密钥本身留在 .env / 环境变量，不进 TOML、不进页面。"
              >
                <Input
                  id="senate-global-key"
                  spellCheck={false}
                  placeholder={globalKeySuggestion || '例如 MINIMAX_API_KEY'}
                  value={keyEnv}
                  onChange={(event) => {
                    setKeyEnv(event.target.value)
                    setRowErrors((current) => ({ ...current, __key_env: '' }))
                  }}
                />
              </Field>

              <div className="flex flex-col gap-2">
                {groupRoles(payload.roles).map((group) => {
                  const open = openGroups[group.kind] ?? false
                  const configuredInGroup = group.roles.filter((role) => role.configured).length
                  const dirtyInGroup = group.roles.filter((role) => dirtyRoles.includes(role.role)).length
                  return (
                    <section
                      key={group.kind}
                      aria-label={`辩论角色分组 ${group.title}`}
                      data-testid={`senate-group-${group.kind}`}
                      className="flex flex-col overflow-hidden rounded border border-border bg-surface"
                    >
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={() => setOpenGroups((current) => ({ ...current, [group.kind]: !open }))}
                        className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left"
                      >
                        {open ? (
                          <ChevronDown className="size-4 text-text-muted" aria-hidden="true" />
                        ) : (
                          <ChevronRight className="size-4 text-text-muted" aria-hidden="true" />
                        )}
                        <span className="text-body font-medium text-text">{group.title}</span>
                        <Badge tone="flat">{group.roles.length}</Badge>
                        <span className="text-caption text-text-muted">
                          已配置 <span className="ldst-num">{configuredInGroup}</span> /{' '}
                          <span className="ldst-num">{group.roles.length}</span>
                        </span>
                        {dirtyInGroup > 0 && <Badge tone="warn">{dirtyInGroup} 项未保存</Badge>}
                        <span className="ml-auto text-caption text-text-muted">{group.note}</span>
                      </button>

                      {open && (
                        <div className="flex flex-col border-t border-border">
                          <div className="hidden gap-2 px-3 py-1 text-caption text-text-muted lg:grid lg:grid-cols-[minmax(0,1fr)_160px_minmax(0,1fr)_auto_112px]">
                            <span>角色</span>
                            <span>Provider</span>
                            <span>模型</span>
                            <span>来源</span>
                            <span />
                          </div>
                          {group.roles.map((role) => {
                            const draft = drafts[role.role] ?? {
                              provider: role.provider ?? '',
                              model: role.model ?? '',
                              backendUrl: role.backend_url ?? '',
                              apiKey: '',
                            }
                            const source = sourceOf(role, globalProvider)
                            const dirty = dirtyRoles.includes(role.role)
                            const provider = providerByName(providers, draft.provider)
                            const models = provider?.models ?? []
                            const rowBusy = busy[role.role]
                            const rowError = rowErrors[role.role]
                            return (
                              <div
                                key={role.role}
                                role="group"
                                aria-label={`辩论角色 ${role.role}`}
                                data-testid={`senate-role-${role.role}`}
                                className="flex flex-col gap-1 border-b border-border px-3 py-2 last:border-b-0 lg:grid lg:grid-cols-[minmax(0,1fr)_160px_minmax(0,1fr)_auto_112px] lg:items-center lg:gap-2"
                              >
                                <div className="flex flex-col gap-0.5">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="ldst-num text-body text-text">{role.role}</span>
                                    {role.label !== role.role && (
                                      <span className="text-caption text-text-muted">{role.label}</span>
                                    )}
                                    {dirty && <Badge tone="warn">未保存</Badge>}
                                  </div>
                                  {role.description && (
                                    <span className="text-caption text-text-muted">{role.description}</span>
                                  )}
                                </div>

                                <Select
                                  id={`senate-${role.role}-provider`}
                                  aria-label={`${role.label} provider`}
                                  value={draft.provider}
                                  onChange={(event) => changeProvider(role.role, event.target.value)}
                                >
                                  <option value="">{role.configured ? '未选择' : '继承全局'}</option>
                                  {providers.map((item) => (
                                    <option key={item.name} value={item.name}>
                                      {item.label || item.name}
                                    </option>
                                  ))}
                                  {draft.provider !== '' &&
                                    !providers.some((item) => item.name === draft.provider) && (
                                      <option value={draft.provider}>{draft.provider}（后端未列出）</option>
                                    )}
                                </Select>

                                <div className="flex items-center">
                                  <Input
                                    id={`senate-${role.role}-model`}
                                    list={`senate-${role.role}-models`}
                                    aria-label={`${role.label} 模型`}
                                    spellCheck={false}
                                    placeholder={role.configured ? '模型标识' : '继承全局模型'}
                                    value={draft.model}
                                    onChange={(event) => updateDraft(role.role, { model: event.target.value })}
                                  />
                                  <datalist id={`senate-${role.role}-models`}>
                                    {models.map((model) => (
                                      <option key={model} value={model} />
                                    ))}
                                  </datalist>
                                </div>

                                <Badge tone={source.tone}>{source.label}</Badge>

                                <Button
                                  size="md"
                                  variant="ghost"
                                  className="whitespace-nowrap"
                                  leadingIcon={<FlaskConical className="size-3.5" aria-hidden="true" />}
                                  disabled={!controlReady || rowBusy !== undefined}
                                  loading={rowBusy === 'test'}
                                  onClick={() => testRow(role)}
                                  data-testid={`senate-test-${role.role}`}
                                >
                                  测试
                                </Button>

                                <div className="flex flex-col gap-1 lg:col-span-5">
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    <span className="text-caption text-text-muted">接入</span>
                                    <Input
                                      aria-label={`${role.label} 角色地址`}
                                      spellCheck={false}
                                      placeholder="留空 = 全局反代"
                                      value={draft.backendUrl}
                                      onChange={(event) =>
                                        updateDraft(role.role, { backendUrl: event.target.value })
                                      }
                                      data-testid={`senate-url-${role.role}`}
                                    />
                                    <Input
                                      type="password"
                                      aria-label={`${role.label} 角色密钥`}
                                      spellCheck={false}
                                      autoComplete="off"
                                      placeholder={
                                        role.api_key_set ? '已配私有 key（留空不改）' : '留空 = 用全局 key'
                                      }
                                      value={draft.apiKey}
                                      onChange={(event) => updateDraft(role.role, { apiKey: event.target.value })}
                                      data-testid={`senate-key-${role.role}`}
                                    />
                                    <span className="text-caption text-text-subtle">
                                      {role.api_key_set ? '已配私有 key' : '当前用全局 key'}
                                    </span>
                                    <Button
                                      variant="ghost"
                                      disabled={!controlReady}
                                      onClick={() => void openPrompt(role.role)}
                                      data-testid={`senate-prompt-${role.role}`}
                                    >
                                      {promptEditing === role.role ? '收起提示词' : '提示词'}
                                    </Button>
                                  </div>
                                  {promptEditing === role.role && (
                                    <div className="flex flex-col gap-2 rounded border border-border p-2">
                                      <span className="text-caption text-text-muted">
                                        {promptMeta.path ?? '未找到提示词文件'}
                                        {promptMeta.exists ? '' : '（新文件）'}
                                      </span>
                                      <textarea
                                        aria-label={`${role.label} 提示词`}
                                        className="min-h-48 w-full rounded border border-border bg-surface p-2 font-mono text-caption"
                                        value={promptText}
                                        onChange={(event) => setPromptText(event.target.value)}
                                        spellCheck={false}
                                      />
                                      <div className="flex items-center gap-2">
                                        <Button
                                          variant="secondary"
                                          loading={promptBusy}
                                          onClick={() => void savePrompt(role.role)}
                                        >
                                          保存提示词
                                        </Button>
                                        <span className="text-caption text-text-subtle">
                                          {promptText.length} 字符
                                        </span>
                                      </div>
                                    </div>
                                  )}
                                </div>

                                <div className="flex flex-col gap-1 lg:col-span-5">
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    <span className="text-caption text-text-muted">技能</span>
                                    {(role.skills ?? []).length === 0 ? (
                                      <span className="text-caption text-text-subtle">未挂载</span>
                                    ) : (
                                      (role.skills ?? []).map((name) => (
                                        <span
                                          key={name}
                                          className="rounded border border-border px-1.5 text-caption text-text-muted"
                                        >
                                          {name}
                                        </span>
                                      ))
                                    )}
                                    <Button
                                      variant="ghost"
                                      disabled={!controlReady}
                                      onClick={() => {
                                        setSkillEditing(skillEditing === role.role ? null : role.role)
                                        setSkillDrafts((current) => ({
                                          ...current,
                                          [role.role]: current[role.role] ?? role.skills ?? [],
                                        }))
                                      }}
                                      data-testid={`senate-skills-${role.role}`}
                                    >
                                      {skillEditing === role.role ? '收起' : '配置技能'}
                                    </Button>
                                  </div>
                                  {skillEditing === role.role && (
                                    <div className="flex flex-col gap-2 rounded border border-border p-2">
                                      <div className="flex max-h-48 flex-wrap gap-x-3 gap-y-1 overflow-auto">
                                        {(payload?.skill_library ?? []).map((item) => {
                                          const picked = (
                                            skillDrafts[role.role] ??
                                            role.skills ??
                                            []
                                          ).includes(item.name)
                                          return (
                                            <label
                                              key={item.name}
                                              title={item.description}
                                              className="flex items-center gap-1 text-caption text-text"
                                            >
                                              <input
                                                type="checkbox"
                                                checked={picked}
                                                onChange={() => toggleSkill(role.role, item.name)}
                                              />
                                              <span>{item.name}</span>
                                            </label>
                                          )
                                        })}
                                        {(payload?.skill_library ?? []).length === 0 && (
                                          <span className="text-caption text-text-subtle">
                                            技能库为空：检查 Senate 目录下的 skills/
                                          </span>
                                        )}
                                      </div>
                                      <div className="flex items-center gap-2">
                                        <Button
                                          variant="secondary"
                                          loading={skillBusy === role.role}
                                          onClick={() =>
                                            void saveSkills(
                                              role.role,
                                              skillDrafts[role.role] ?? role.skills ?? [],
                                            )
                                          }
                                          data-testid={`senate-skills-save-${role.role}`}
                                        >
                                          保存技能
                                        </Button>
                                        <span className="text-caption text-text-muted">
                                          本次勾选 {(skillDrafts[role.role] ?? role.skills ?? []).length} 个
                                        </span>
                                      </div>
                                    </div>
                                  )}
                                </div>

                                {rowError && (
                                  <p role="alert" className="text-caption text-danger lg:col-span-5">
                                    {rowError}
                                  </p>
                                )}
                                {tests[role.role] && (
                                  <div className="text-caption text-text-muted lg:col-span-5">
                                    <TestOutcome result={tests[role.role]} />
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </section>
                  )
                })}
              </div>
              {rowErrors.__key_env && (
                <p role="alert" className="text-caption text-danger">
                  {rowErrors.__key_env}
                </p>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="secondary"
                  onClick={() => void load()}
                  disabled={loading || savingAll}
                  leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
                >
                  刷新
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => void submit(true)}
                  disabled={!hasChanges || savingAll}
                  loading={savingAll}
                >
                  保存并测试
                </Button>
                <Button
                  variant="primary"
                  onClick={() => void submit(false)}
                  disabled={!hasChanges || savingAll}
                  loading={savingAll}
                  data-testid="senate-save"
                >
                  保存
                </Button>
                {!hasChanges && <span className="text-caption text-text-muted">先修改角色或变量名再保存</span>}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  )
}

function TestOutcome({ result }: { result: SenateTestResult | undefined }) {
  if (!result) return null
  const text = senateErrorText(result.error_code, result.message)
  if (result.ok && result.skipped) {
    return (
      <p aria-live="polite" className="text-caption text-text-secondary">
        <Badge tone="neutral">offline</Badge> 已跳过：{result.message}
      </p>
    )
  }
  if (result.ok) {
    return (
      <p aria-live="polite" className="text-caption text-text-secondary">
        连通 · <span className="ldst-num">{result.provider}</span> /{' '}
        <span className="ldst-num">{result.model}</span> · 耗时{' '}
        <span className="ldst-num">{result.latency_ms}ms</span>
      </p>
    )
  }
  return (
    <p aria-live="polite" role="alert" className="text-caption text-danger">
      不通{result.error_code && <Badge tone="danger">{result.error_code}</Badge>} · {text}
    </p>
  )
}
