import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, FlaskConical } from 'lucide-react'

import {
  api,
  describeApiError,
  type AiModelSource,
  type AiModuleOverride,
  type AiModulesPayload,
  type AiModuleTestResult,
} from '../api'
import { Badge, Banner, Button, Field, Input, Select, SkeletonText, type BadgeTone } from './ui'

/**
 * 按模块配置（QUOT-498 / QUOT-504）：子模块与辩论角色各自指定模型。
 *
 * 模块清单、角色清单、模型下拉全集全部来自 `GET /control/ai/modules`，本组件不
 * 硬编码任何键名；每行常驻来源徽标（继承全局 / 本模块生效 / 本角色生效），保存后
 * 用 PUT 响应里的回读状态整体刷新，不只改本地 state。
 */

const SOURCE_BADGE: Record<AiModelSource, { tone: BadgeTone; label: string }> = {
  global: { tone: 'neutral', label: '继承全局' },
  module: { tone: 'info', label: '本模块生效' },
  role: { tone: 'ok', label: '本角色生效' },
}

function SourceBadge({ source, value }: { source: AiModelSource; value: string }) {
  const badge = SOURCE_BADGE[source] ?? SOURCE_BADGE.global
  return (
    <Badge tone={badge.tone}>
      {badge.label}：{value}
    </Badge>
  )
}

interface ModuleDraft {
  model: string
  chain: string
  agentId: string
  kbScope: string
}

interface Drafts {
  modules: Record<string, ModuleDraft>
  /** 键 = `模块/角色`，值 = 角色模型覆盖草稿（空串 = 继承上一级）。 */
  roles: Record<string, string>
}

type RowTest = { ok: true; data: AiModuleTestResult } | { ok: false; error: string }

/** 草稿初始值 = 服务端回读：本级没配覆盖就从「继承上一级」起步，不拿继承值冒充覆盖。 */
function initialDrafts(payload: AiModulesPayload): Drafts {
  const drafts: Drafts = { modules: {}, roles: {} }
  for (const [name, status] of Object.entries(payload.modules)) {
    drafts.modules[name] = {
      model: status.configured ? status.value : '',
      chain: status.fallback_chain.join(', '),
      agentId: status.agent_id,
      kbScope: status.kb_scope,
    }
    for (const [role, roleStatus] of Object.entries(status.roles)) {
      drafts.roles[`${name}/${role}`] = roleStatus.configured ? roleStatus.value : ''
    }
  }
  return drafts
}

function parseChain(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

export function AiModulesConfig() {
  const [expanded, setExpanded] = useState(false)
  const [payload, setPayload] = useState<AiModulesPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Drafts>({ modules: {}, roles: {} })
  const [busy, setBusy] = useState<Record<string, 'save' | 'test' | undefined>>({})
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})
  const [tests, setTests] = useState<Record<string, RowTest | undefined>>({})
  const [notice, setNotice] = useState<string | null>(null)

  const load = async (): Promise<void> => {
    setLoading(true)
    setLoadError(null)
    try {
      setPayload(await api.control.aiModules())
    } catch (error) {
      setLoadError(describeApiError(error).detail)
    } finally {
      setLoading(false)
    }
  }

  // 保存后的 PUT 响应与重读都走这里：草稿整体重置为服务端回读值
  useEffect(() => {
    if (payload) setDrafts(initialDrafts(payload))
  }, [payload])

  const toggle = (): void => {
    const next = !expanded
    setExpanded(next)
    if (next && payload === null && !loading) void load()
  }

  const saveModule = async (name: string): Promise<void> => {
    if (!payload) return
    const draft = drafts.modules[name]
    const init = initialDrafts(payload).modules[name]
    const override: AiModuleOverride = {}
    if (draft.model !== init.model) override.model = draft.model
    if (draft.chain !== init.chain) override.fallback_chain = parseChain(draft.chain)
    const memory: { agent_id?: string; kb_scope?: string } = {}
    if (draft.agentId !== init.agentId) memory.agent_id = draft.agentId
    if (draft.kbScope !== init.kbScope) memory.kb_scope = draft.kbScope
    if (Object.keys(memory).length > 0) override.memory = memory
    if (Object.keys(override).length === 0) return
    setBusy((prev) => ({ ...prev, [name]: 'save' }))
    setRowErrors((prev) => ({ ...prev, [name]: '' }))
    setNotice(null)
    try {
      const fresh = await api.control.saveAiModule(name, override)
      setPayload(fresh)
      setNotice(
        fresh.changed
          ? `已保存并回读：${name}（来源 ${fresh.modules[name]?.source ?? '未知'}）`
          : `已回读：${name} 没有变化`,
      )
    } catch (error) {
      setRowErrors((prev) => ({ ...prev, [name]: describeApiError(error).detail }))
    } finally {
      setBusy((prev) => ({ ...prev, [name]: undefined }))
    }
  }

  const saveRole = async (module: string, role: string): Promise<void> => {
    if (!payload) return
    const key = `${module}/${role}`
    const draft = drafts.roles[key]
    const init = initialDrafts(payload).roles[key]
    if (draft === init) return
    setBusy((prev) => ({ ...prev, [key]: 'save' }))
    setRowErrors((prev) => ({ ...prev, [key]: '' }))
    setNotice(null)
    try {
      const fresh = await api.control.saveAiModule(module, { roles: { [role]: { model: draft } } })
      setPayload(fresh)
      setNotice(`已保存并回读：${module} · ${role}（来源 ${fresh.modules[module]?.roles[role]?.source ?? '未知'}）`)
    } catch (error) {
      setRowErrors((prev) => ({ ...prev, [key]: describeApiError(error).detail }))
    } finally {
      setBusy((prev) => ({ ...prev, [key]: undefined }))
    }
  }

  const test = async (module: string, role?: string): Promise<void> => {
    const key = role === undefined ? module : `${module}/${role}`
    setBusy((prev) => ({ ...prev, [key]: 'test' }))
    setTests((prev) => ({ ...prev, [key]: undefined }))
    try {
      const outcome = await api.control.testAiModule(module, role)
      setTests((prev) => ({
        ...prev,
        [key]: outcome.ok ? { ok: true, data: outcome } : { ok: false, error: outcome.error ?? '探测失败' },
      }))
    } catch (error) {
      setTests((prev) => ({ ...prev, [key]: { ok: false, error: describeApiError(error).detail } }))
    } finally {
      setBusy((prev) => ({ ...prev, [key]: undefined }))
    }
  }

  const modelOptions = payload?.models ?? []

  return (
    <section className="flex flex-col gap-3 rounded border border-border bg-surface-2 px-3 py-2">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={toggle}
        className="flex items-center gap-2 text-left"
      >
        {expanded ? (
          <ChevronDown className="size-4 text-text-muted" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-4 text-text-muted" aria-hidden="true" />
        )}
        <span className="text-body font-medium text-text">按模块配置</span>
        <span className="text-caption text-text-muted">子模块 / 辩论角色各自指定模型，未配置继承上一级</span>
      </button>

      {expanded && (
        <div className="flex flex-col gap-3">
          <p className="text-caption text-text-muted">
            优先级：角色 &gt; 子模块 &gt; 全局；「继承上一级」表示清除本级覆盖，不是关闭。
            写回 <span className="ldst-num">config/ai.yaml</span> 的 <span className="ldst-num">modules:</span> 段。
          </p>
          {loading && <SkeletonText lines={4} />}
          {loadError && (
            <Banner tone="danger" title="读不到按模块配置">
              <span className="flex flex-wrap items-center gap-2">
                {loadError}
                <Button size="md" variant="secondary" onClick={() => void load()}>
                  重试
                </Button>
              </span>
            </Banner>
          )}
          {notice && (
            <Banner tone="ok" title="已保存">
              {notice}
            </Banner>
          )}
          {!loading && !loadError && payload && Object.keys(payload.modules).length === 0 && (
            <p className="text-caption text-text-muted">后端未声明任何子模块。</p>
          )}
          {!loading &&
            payload &&
            Object.entries(payload.modules).map(([name, status]) => {
              const draft = drafts.modules[name]
              if (!draft) return null
              const init = initialDrafts(payload).modules[name]
              const dirty =
                draft.model !== init.model ||
                draft.chain !== init.chain ||
                draft.agentId !== init.agentId ||
                draft.kbScope !== init.kbScope
              const rowBusy = busy[name]
              const rowError = rowErrors[name]
              const rowTest = tests[name]
              const roles = Object.entries(status.roles)
              return (
                <div key={name} role="group" aria-label={`模块 ${name}`} className="flex flex-col gap-2 rounded border border-border bg-surface px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="ldst-num text-body font-medium text-text">{name}</span>
                    <SourceBadge source={status.source} value={status.value} />
                    <div className="ml-auto flex items-center gap-2">
                      <Button
                        size="md"
                        variant="ghost"
                        onClick={() => void test(name)}
                        loading={rowBusy === 'test'}
                      >
                        <FlaskConical className="mr-1 size-3.5" aria-hidden="true" />
                        测试
                      </Button>
                      <Button
                        size="md"
                        variant="secondary"
                        disabled={!dirty || rowBusy !== undefined}
                        loading={rowBusy === 'save'}
                        onClick={() => void saveModule(name)}
                      >
                        保存
                      </Button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
                    <Field label="模型" htmlFor={`ai-mod-${name}-model`}>
                      <Select
                        id={`ai-mod-${name}-model`}
                        value={draft.model}
                        onChange={(event) =>
                          setDrafts((prev) => ({
                            ...prev,
                            modules: { ...prev.modules, [name]: { ...draft, model: event.target.value } },
                          }))
                        }
                      >
                        <option value="">继承上一级</option>
                        {modelOptions.map((model) => (
                          <option key={model} value={model}>
                            {model}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="fallback 链（可选，逗号分隔）" htmlFor={`ai-mod-${name}-chain`}>
                      <Input
                        id={`ai-mod-${name}-chain`}
                        spellCheck={false}
                        placeholder="留空继承上级链"
                        value={draft.chain}
                        onChange={(event) =>
                          setDrafts((prev) => ({
                            ...prev,
                            modules: { ...prev.modules, [name]: { ...draft, chain: event.target.value } },
                          }))
                        }
                      />
                    </Field>
                    <Field label="记忆 agent_id" htmlFor={`ai-mod-${name}-agent`}>
                      <Input
                        id={`ai-mod-${name}-agent`}
                        spellCheck={false}
                        value={draft.agentId}
                        onChange={(event) =>
                          setDrafts((prev) => ({
                            ...prev,
                            modules: { ...prev.modules, [name]: { ...draft, agentId: event.target.value } },
                          }))
                        }
                      />
                    </Field>
                    <Field label="知识库 kb_scope" htmlFor={`ai-mod-${name}-scope`}>
                      <Input
                        id={`ai-mod-${name}-scope`}
                        spellCheck={false}
                        value={draft.kbScope}
                        onChange={(event) =>
                          setDrafts((prev) => ({
                            ...prev,
                            modules: { ...prev.modules, [name]: { ...draft, kbScope: event.target.value } },
                          }))
                        }
                      />
                    </Field>
                  </div>
                  {rowError && (
                    <p role="alert" className="text-caption text-danger">
                      {rowError}
                    </p>
                  )}
                  <TestOutcome result={rowTest} />
                  {roles.length > 0 && (
                    <div className="flex flex-col gap-2 border-t border-border pt-2">
                      <p className="text-caption text-text-secondary">辩论角色 · {roles.length}</p>
                      {roles.map(([role, roleStatus]) => {
                        const key = `${name}/${role}`
                        const roleDraft = drafts.roles[key] ?? ''
                        const roleInit = initialDrafts(payload).roles[key]
                        const roleBusy = busy[key]
                        const roleError = rowErrors[key]
                        const roleTest = tests[key]
                        return (
                          <div key={role} role="group" aria-label={`角色 ${name}/${role}`} className="flex flex-col gap-1 rounded bg-surface-2 px-2 py-1.5">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="ldst-num w-36 truncate text-caption text-text">{role}</span>
                              <SourceBadge source={roleStatus.source} value={roleStatus.value} />
                              <div className="ml-auto flex items-center gap-1">
                                <Button
                                  size="md"
                                  variant="ghost"
                                  onClick={() => void test(name, role)}
                                  loading={roleBusy === 'test'}
                                >
                                  测试
                                </Button>
                                <Button
                                  size="md"
                                  variant="secondary"
                                  disabled={roleDraft === roleInit || roleBusy !== undefined}
                                  loading={roleBusy === 'save'}
                                  onClick={() => void saveRole(name, role)}
                                >
                                  保存
                                </Button>
                              </div>
                            </div>
                            <Field label="模型" htmlFor={`ai-role-${name}-${role}-model`} className="max-w-sm">
                              <Select
                                id={`ai-role-${name}-${role}-model`}
                                value={roleDraft}
                                onChange={(event) =>
                                  setDrafts((prev) => ({
                                    ...prev,
                                    roles: { ...prev.roles, [key]: event.target.value },
                                  }))
                                }
                              >
                                <option value="">继承上一级</option>
                                {modelOptions.map((model) => (
                                  <option key={model} value={model}>
                                    {model}
                                  </option>
                                ))}
                              </Select>
                            </Field>
                            {roleError && (
                              <p role="alert" className="text-caption text-danger">
                                {roleError}
                              </p>
                            )}
                            <TestOutcome result={roleTest} />
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
        </div>
      )}
    </section>
  )
}

function TestOutcome({ result }: { result: RowTest | undefined }) {
  if (!result) return null
  return (
    <div aria-live="polite">
      {result.ok ? (
        <p className="text-caption text-text-secondary">
          <span className="ldst-num">{result.data.model}</span>
          {' · 来源 '}
          {result.data.source}
          {' · 耗时 '}
          <span className="ldst-num">{result.data.latency_ms}ms</span>
          {result.data.reply_head && (
            <>
              {' · 返回 '}
              <span className="ldst-num">{result.data.reply_head}</span>
            </>
          )}
        </p>
      ) : (
        <p role="alert" className="text-caption text-danger">
          {result.error}
        </p>
      )}
    </div>
  )
}