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

export function SenateRoleModels({
  controlReady,
  push,
}: {
  controlReady: boolean
  push?: (message: string, tone?: ToastItem['tone']) => void
}) {
  const [expanded, setExpanded] = useState(true)
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
      next[role.role] = { provider: role.provider ?? '', model: role.model ?? '' }
    }
    setDrafts(next)
    setKeyEnv(payload.global?.api_key_env ?? '')
  }, [payload])

  const providers = payload?.providers ?? []
  const globalProvider = payload?.global?.provider ?? null
  const baselineKeyEnv = payload?.global?.api_key_env ?? ''

  const dirtyRoles = useMemo(() => {
    if (!payload) return []
    return payload.roles
      .filter((role) => {
        const draft = drafts[role.role]
        if (!draft) return false
        return draft.provider !== (role.provider ?? '') || draft.model !== (role.model ?? '')
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
    errors: Record<string, string>
  } => {
    const roles: Record<string, SenateRoleEntry> = {}
    const errors: Record<string, string> = {}
    if (!payload) return { roles, errors }

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
      roles[name] = { provider, model }
    }

    const trimmedKeyEnv = keyEnv.trim()
    if (keyEnvDirty && trimmedKeyEnv !== '' && !KEY_ENV_PATTERN.test(trimmedKeyEnv)) {
      errors.__key_env = 'api_key_env 只能填环境变量名（字母、数字、下划线）'
    }
    if (keyEnvDirty && errors.__key_env === undefined && Object.keys(roles).length === 0) {
      // `role_llms` 必须至少带一个角色；只改变量名时用第一个已配置角色的现值占位。
      const configured = payload.roles.find((role) => role.configured && role.provider && role.model)
      if (configured) {
        roles[configured.role] = {
          provider: configured.provider ?? '',
          model: configured.model ?? '',
        }
      } else {
        errors.__key_env = '先为至少一个角色选择 provider 与 model，再保存全局 api_key_env'
      }
    }
    if (keyEnvDirty && trimmedKeyEnv !== '' && Object.keys(roles).length > 0) {
      roles[Object.keys(roles)[0]] = { ...roles[Object.keys(roles)[0]], api_key_env: trimmedKeyEnv }
    }
    return { roles, errors }
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

  const testRow = (role: SenateRoleStatus): void => {
    const draft = drafts[role.role]
    const key = keyEnv.trim()
    void runTest(role.role, {
      ...(draft?.provider.trim() ? { provider: draft.provider.trim() } : {}),
      ...(draft?.model.trim() ? { model: draft.model.trim() } : {}),
      ...(key !== '' && key !== baselineKeyEnv ? { api_key_env: key } : {}),
    })
  }

  const submit = async (alsoTest: boolean): Promise<void> => {
    if (!payload) return
    const { roles, errors } = buildChanges()
    if (Object.keys(errors).length > 0) {
      setRowErrors(errors)
      setNotice(null)
      return
    }
    if (Object.keys(roles).length === 0) {
      setNotice({ tone: 'info', text: '没有需要保存的改动' })
      return
    }
    setSavingAll(true)
    setStale(false)
    setNotice(null)
    try {
      const fresh = await api.control.saveSenateRoleModels(roles, payload.revision)
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

              <ul className="flex flex-col gap-2">
                {payload.roles.map((role) => {
                  const draft = drafts[role.role] ?? { provider: role.provider ?? '', model: role.model ?? '' }
                  const source = sourceOf(role, globalProvider)
                  const dirty = dirtyRoles.includes(role.role)
                  const provider = providerByName(providers, draft.provider)
                  const models = provider?.models ?? []
                  const rowBusy = busy[role.role]
                  const rowError = rowErrors[role.role]
                  const suggestion = provider?.api_key_env ?? ''
                  return (
                    <li
                      key={role.role}
                      role="group"
                      aria-label={`辩论角色 ${role.role}`}
                      data-testid={`senate-role-${role.role}`}
                      className="flex flex-col gap-2 rounded border border-border bg-surface px-3 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="ldst-num text-body font-medium text-text">{role.role}</span>
                        {role.label !== role.role && (
                          <span className="text-caption text-text-muted">{role.label}</span>
                        )}
                        <Badge tone={source.tone}>{source.label}</Badge>
                        {dirty && <Badge tone="warn">未保存</Badge>}
                        <div className="ml-auto">
                          <Button
                            size="md"
                            variant="ghost"
                            disabled={!controlReady || rowBusy !== undefined}
                            loading={rowBusy === 'test'}
                            onClick={() => testRow(role)}
                            data-testid={`senate-test-${role.role}`}
                          >
                            <FlaskConical className="mr-1 size-3.5" aria-hidden="true" />
                            测试
                          </Button>
                        </div>
                      </div>

                      <div className="grid gap-2 lg:grid-cols-[150px_minmax(0,1.3fr)_minmax(0,1fr)]">
                        <Field label="Provider" htmlFor={`senate-${role.role}-provider`}>
                          <Select
                            id={`senate-${role.role}-provider`}
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
                        </Field>

                        <Field
                          label="Model"
                          htmlFor={`senate-${role.role}-model`}
                          hint={models.length > 0 ? '候选来自该 provider，可自由填写' : '先选 provider 才有候选；可自由填写'}
                        >
                          <Input
                            id={`senate-${role.role}-model`}
                            list={`senate-${role.role}-models`}
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
                        </Field>

                        <Field label="api_key_env（变量名）" htmlFor={`senate-${role.role}-key`}>
                          <Input
                            id={`senate-${role.role}-key`}
                            spellCheck={false}
                            placeholder={suggestion || '例如 MINIMAX_API_KEY'}
                            value={keyEnv}
                            onChange={(event) => {
                              setKeyEnv(event.target.value)
                              setRowErrors((current) => ({ ...current, __key_env: '' }))
                            }}
                          />
                        </Field>
                      </div>

                      <p className="text-caption text-text-muted">
                        只存变量名；写入全局 <span className="ldst-num">[llm].api_key_env</span>，所有角色共用。密钥本身留在
                        .env / 环境变量，不进 TOML、不进页面。
                        {suggestion && keyEnv.trim() === '' && (
                          <>
                            {' '}
                            {draft.provider} 推荐 <span className="ldst-num">{suggestion}</span>。
                          </>
                        )}
                      </p>
                      {rowError && (
                        <p role="alert" className="text-caption text-danger">
                          {rowError}
                        </p>
                      )}
                      <TestOutcome result={tests[role.role]} />
                    </li>
                  )
                })}
              </ul>
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
