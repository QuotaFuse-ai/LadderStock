import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, KeyRound, Link2, Sparkles, Terminal } from 'lucide-react'

import {
  api,
  describeApiError,
  type AiTestResult,
  type CliChannel,
  type CliState,
  type CliTestResult,
  type SelfAiStatus,
} from '../api'
import { cn } from '../lib/cn'
import { Badge, Banner, Button, Drawer, Field, Input, Segmented, SkeletonText, type BadgeTone } from './ui'

/**
 * 接入 AI（QUOT-200 / QUOT-401）：看板自己的复盘模型的一站式配置。
 *
 * 两条通道并存，先订阅后 API——多数人手上已经有 Codex / Claude / WorkBuddy 的订阅，
 * 不该为了跑一次复盘再去开一个 API Key：
 *
 * 1. **订阅（本机 CLI）**：复用本机已登录的 CLI，后端 `ai/cli_provider.py` 负责探测与调用；
 * 2. **API 端点**：OpenAI 兼容 / Anthropic Messages，写 `config/ai.env` 的 `LDST_LLM_*`。
 *
 * 两者都只写本机，密钥不回显。CLI 通道**不支持 tool calling**（选股等链路仍需 API），
 * 这条限制在界面上直说，不在背后偷偷降级。
 */

interface Preset {
  id: string
  label: string
  base: string
  model: string
  hint: string
}

type Channel = 'cli' | 'api'

const PRESETS: readonly Preset[] = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    base: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    hint: 'OpenAI 兼容',
  },
  {
    id: 'qwen',
    label: '通义千问',
    base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
    hint: '百炼兼容模式',
  },
  {
    id: 'kimi',
    label: 'Kimi',
    base: 'https://api.moonshot.cn/v1',
    model: 'moonshot-v1-8k',
    hint: 'Moonshot 兼容',
  },
  {
    id: 'minimax',
    label: 'MiniMax',
    base: 'https://api.minimax.chat/v1',
    model: 'MiniMax-Text-01',
    hint: 'OpenAI 兼容',
  },
  {
    id: 'ollama',
    label: '本机 Ollama',
    base: 'http://127.0.0.1:11434/v1',
    model: 'qwen2.5:7b',
    hint: '免密钥',
  },
  {
    id: 'custom',
    label: '自定义',
    base: '',
    model: '',
    hint: '任意兼容端点',
  },
]

/** 四态 → 徽标语气与文案。`ok` 之外一律给出原因，不灰不假装。 */
const CLI_BADGE: Record<CliState, { tone: BadgeTone; text: string }> = {
  ok: { tone: 'ok', text: '可用' },
  no_login: { tone: 'warn', text: '未登录' },
  broken: { tone: 'danger', text: '跑不起来' },
  missing: { tone: 'neutral', text: '未安装' },
}

export interface AiConnectDialogProps {
  open: boolean
  onClose: () => void
  onSaved?: (status: SelfAiStatus) => void
}

export default function AiConnectDialog({ open, onClose, onSaved }: AiConnectDialogProps) {
  const [status, setStatus] = useState<SelfAiStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [channel, setChannel] = useState<Channel>('cli')
  const [channels, setChannels] = useState<CliChannel[]>([])
  const [activeCli, setActiveCli] = useState<string | null>(null)
  const [retesting, setRetesting] = useState(false)
  const [cliBusy, setCliBusy] = useState<string | null>(null)
  const [cliResults, setCliResults] = useState<Record<string, CliTestResult>>({})
  const [cliNotice, setCliNotice] = useState<string | null>(null)

  const [preset, setPreset] = useState('deepseek')
  const [baseUrl, setBaseUrl] = useState(PRESETS[0].base)
  const [model, setModel] = useState(PRESETS[0].model)
  const [apiKey, setApiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<AiTestResult | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const loadChannels = async (): Promise<void> => {
    try {
      const payload = await api.control.selfAiCli()
      setChannels(payload.channels)
      setActiveCli(payload.active)
    } catch (error) {
      setCliNotice(`探测订阅通道失败：${describeApiError(error).detail}`)
    }
  }

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    setNotice(null)
    setCliNotice(null)
    setResult(null)
    setApiKey('')
    setCliResults({})
    void api.control
      .selfAi()
      .then((payload) => {
        if (cancelled) return
        setStatus(payload)
        setChannels(payload.cli_channels ?? [])
        setActiveCli(payload.active_cli ?? null)
        // 已经选了订阅通道就直接落在订阅页，不让人再找一遍
        setChannel(payload.active_cli ? 'cli' : 'api')
        const current = payload.backends?.[0]
        if (current?.base_url) {
          setBaseUrl(current.base_url)
          setPreset(PRESETS.some((item) => item.base === current.base_url) ? presetFor(current.base_url) : 'custom')
        }
        // 只回填用户实际保存过的模型；ai.yaml 里的 user-model/model-a 是占位，
        // 回填到表单会让「选 DeepSeek 却提交 user-model」看起来像预置错了。
        if (payload.model_override) setModel(payload.model_override)
      })
      .catch((error) => {
        if (!cancelled) setLoadError(describeApiError(error).detail)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    void loadChannels()
    return () => {
      cancelled = true
    }
    // 只在打开时拉一次状态；重新打开会刷新
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const choosePreset = (item: Preset): void => {
    setPreset(item.id)
    setBaseUrl(item.base)
    setModel(item.model)
    setResult(null)
    setNotice(null)
  }

  const test = async (): Promise<void> => {
    setTesting(true)
    setResult(null)
    setNotice(null)
    try {
      const outcome = await api.control.testSelfAi({
        base_url: baseUrl.trim(),
        model: model.trim(),
        ...(apiKey.trim() === '' ? {} : { api_key: apiKey.trim() }),
      })
      setResult(outcome)
    } catch (error) {
      const info = describeApiError(error)
      setResult({ ok: false, latency_ms: 0, model_seen: null, error: `${info.title}：${info.detail}` })
    } finally {
      setTesting(false)
    }
  }

  const save = async (): Promise<void> => {
    setSaving(true)
    setNotice(null)
    setResult(null)
    try {
      const values: Record<string, string> = {
        LDST_LLM_BASE_URL: baseUrl.trim(),
        LDST_LLM_MODEL: model.trim(),
      }
      if (apiKey.trim() !== '') values.LDST_LLM_API_KEY = apiKey.trim()
      // 手动切 API 端点时顺手把订阅通道摘掉，避免两条通道同时生效说不清
      const payload = await api.control.saveSelfAi(values, ['LDST_LLM_CLI'])
      setStatus(payload)
      setActiveCli(payload.active_cli ?? null)
      setApiKey('')
      setNotice(
        payload.configured
          ? '已保存并启用，下一次复盘生成会使用这个模型。'
          : '已保存，但还缺密钥：补上 API Key 后才会真正发请求。',
      )
      onSaved?.(payload)
    } catch (error) {
      const info = describeApiError(error)
      setNotice(`保存失败：${info.detail}`)
    } finally {
      setSaving(false)
    }
  }

  const testChannel = async (id: string): Promise<void> => {
    setCliBusy(id)
    setCliNotice(null)
    try {
      const outcome = await api.control.testSelfAiCli(id)
      setCliResults((prev) => ({ ...prev, [id]: outcome }))
      if (outcome.channel) {
        setChannels((prev) => prev.map((item) => (item.id === id ? (outcome.channel as CliChannel) : item)))
      }
    } catch (error) {
      const info = describeApiError(error)
      setCliResults((prev) => ({
        ...prev,
        [id]: { ok: false, latency_ms: 0, model_seen: null, error: `${info.title}：${info.detail}` },
      }))
    } finally {
      setCliBusy(null)
    }
  }

  const useChannel = async (id: string): Promise<void> => {
    setCliBusy(id)
    setCliNotice(null)
    try {
      const payload = await api.control.saveSelfAi({ LDST_LLM_CLI: id })
      setStatus(payload)
      setActiveCli(payload.active_cli ?? id)
      setCliNotice(`已切到订阅通道「${labelOf(channels, id)}」：下一次复盘直接用它，不需要重启。`)
      onSaved?.(payload)
    } catch (error) {
      setCliNotice(`切换失败：${describeApiError(error).detail}`)
    } finally {
      setCliBusy(null)
    }
  }

  const backToApi = async (): Promise<void> => {
    setCliBusy('__clear__')
    setCliNotice(null)
    try {
      const payload = await api.control.saveSelfAi({}, ['LDST_LLM_CLI'])
      setStatus(payload)
      setActiveCli(null)
      setCliNotice('已切回 API 端点通道。')
      onSaved?.(payload)
    } catch (error) {
      setCliNotice(`切换失败：${describeApiError(error).detail}`)
    } finally {
      setCliBusy(null)
    }
  }

  const retest = async (): Promise<void> => {
    setRetesting(true)
    setCliResults({})
    await loadChannels()
    setRetesting(false)
  }

  const configured = status?.configured ?? false
  const activeLabel = activeCli ? labelOf(channels, activeCli) : null

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="lg"
      title={
        <span className="flex items-center gap-2">
          <Sparkles className="size-4 text-accent" aria-hidden="true" />
          接入 AI
        </span>
      }
      subtitle="给看板自己的复盘生成配一个模型；可以用本机已登录的订阅 CLI，也可以填 API 端点"
      footer={
        channel === 'api' ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
            <Button
              onClick={() => void save()}
              loading={saving}
              disabled={loading || baseUrl.trim() === '' || model.trim() === ''}
            >
              保存并启用
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
            <Button variant="secondary" onClick={() => void retest()} loading={retesting} disabled={loading}>
              重新探测
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-5">
        <section className="flex flex-wrap items-center gap-2 rounded border border-border bg-surface-2 px-3 py-2">
          <span className="text-body text-text-secondary">当前状态</span>
          {configured ? (
            <Badge tone="ok">
              <CheckCircle2 className="mr-1 size-3" aria-hidden="true" />
              已配置
            </Badge>
          ) : (
            <Badge tone="warn">未配置</Badge>
          )}
          {activeLabel ? (
            <Badge tone="info">订阅 · {activeLabel}</Badge>
          ) : (
            status?.model_override && (
              <span className="ldst-num text-caption text-text-muted">模型 {status.model_override}</span>
            )
          )}
          {status?.env_file && (
            <span className="ml-auto truncate text-caption text-text-muted" title={status.env_file}>
              写入 {status.env_file}
            </span>
          )}
        </section>

        <Segmented<Channel>
          ariaLabel="接入方式"
          value={channel}
          onChange={setChannel}
          options={[
            { value: 'cli', label: '订阅（本机 CLI）' },
            { value: 'api', label: 'API 端点' },
          ]}
        />

        {loading && <SkeletonText lines={3} />}
        {loadError && (
          <Banner tone="danger" title="读不到 AI 配置">
            {loadError}
          </Banner>
        )}

        {!loading && channel === 'cli' && (
          <section className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <h3 className="text-body font-medium text-text">用本机已登录的订阅</h3>
              <p className="text-caption text-text-muted">
                直接复用这台机器上已经登录的 CLI，不用另外准备 API Key。探测只看 PATH 与登录态文件，
                不读取凭据内容。
              </p>
            </div>

            {channels.length === 0 && <SkeletonText lines={3} />}

            <div className="flex flex-col gap-2">
              {channels.map((item) => {
                const badge = CLI_BADGE[item.state] ?? CLI_BADGE.missing
                const isActive = activeCli === item.id
                const testResult = cliResults[item.id]
                return (
                  <div
                    key={item.id}
                    className={cn(
                      'flex flex-col gap-2 rounded border px-3 py-2',
                      isActive ? 'border-accent bg-accent-soft' : 'border-border bg-surface',
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Terminal className="size-3.5 text-text-muted" aria-hidden="true" />
                      <span className="text-body font-medium text-text">{item.label}</span>
                      <Badge tone={badge.tone}>{badge.text}</Badge>
                      {isActive && <Badge tone="info">使用中</Badge>}
                      {item.version && (
                        <span className="ldst-num text-caption text-text-muted">{item.version}</span>
                      )}
                      <div className="ml-auto flex items-center gap-2">
                        <Button
                          size="md"
                          variant="ghost"
                          onClick={() => void testChannel(item.id)}
                          loading={cliBusy === item.id}
                        >
                          测试
                        </Button>
                        {isActive ? (
                          <Button
                            size="md"
                            variant="secondary"
                            onClick={() => void backToApi()}
                            loading={cliBusy === '__clear__'}
                          >
                            切回 API
                          </Button>
                        ) : (
                          <Button
                            size="md"
                            variant="secondary"
                            disabled={item.state !== 'ok'}
                            onClick={() => void useChannel(item.id)}
                            loading={cliBusy === item.id}
                          >
                            使用此通道
                          </Button>
                        )}
                      </div>
                    </div>
                    <p className="text-caption leading-body text-text-muted">{item.detail}</p>
                    <p className="truncate text-caption text-text-muted" title={item.exe ?? ''}>
                      {item.exe ? `可执行：${item.exe}` : '可执行：未找到'}
                    </p>
                    <div aria-live="polite">
                      {testResult && (
                        <Banner tone={testResult.ok ? 'ok' : 'danger'} title={testResult.ok ? '连通' : '不通'}>
                          {testResult.ok ? (
                            <>
                              耗时 <span className="ldst-num">{testResult.latency_ms}ms</span>
                              {testResult.model_seen && (
                                <>
                                  {' · 返回 '}
                                  <span className="ldst-num">{testResult.model_seen}</span>
                                </>
                              )}
                            </>
                          ) : (
                            (testResult.error ?? '没有取到错误详情')
                          )}
                        </Banner>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            {cliNotice && (
              <Banner tone={cliNotice.includes('失败') ? 'danger' : 'ok'} title={cliNotice.includes('失败') ? '操作失败' : '已更新'}>
                {cliNotice}
              </Banner>
            )}

            <section className="rounded border border-border bg-surface-2 px-3 py-2 text-caption leading-body text-text-muted">
              <p className="flex items-start gap-1.5">
                <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                <span>
                  订阅通道只做文本补全，<span className="text-text-secondary">不支持 tool calling</span>
                  ：选股这类要调工具的链路请留在 API 端点；复盘 / 周报生成不受影响。
                </span>
              </p>
              <p className="mt-1">
                切换后写进 <span className="ldst-num">config/ai.env</span> 的 <span className="ldst-num">LDST_LLM_CLI</span>
                ，下一次生成直接生效，不需要重启。
              </p>
            </section>
          </section>
        )}

        {!loading && channel === 'api' && (
          <section className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <h3 className="text-body font-medium text-text">选择提供方</h3>
              <p className="text-caption text-text-muted">预置只填端点与常用模型，仍可手动改。</p>
              <div className="grid grid-cols-3 gap-2 max-lg:grid-cols-2">
                {PRESETS.map((item) => {
                  const active = preset === item.id
                  return (
                    <button
                      key={item.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => choosePreset(item)}
                      className={cn(
                        'flex h-auto flex-col items-start gap-0.5 rounded border px-3 py-2 text-left transition-colors duration-fast',
                        active
                          ? 'border-accent bg-accent-soft text-text'
                          : 'border-border bg-surface text-text-secondary hover:border-border-strong hover:bg-surface-3',
                      )}
                    >
                      <span className="text-body font-medium">{item.label}</span>
                      <span className="text-caption text-text-muted">{item.hint}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="flex flex-col gap-4">
              <Field label="端点 Base URL" htmlFor="ai-connect-base" hint="OpenAI 兼容地址，通常以 /v1 结尾">
                <Input
                  id="ai-connect-base"
                  spellCheck={false}
                  placeholder="https://api.deepseek.com/v1"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                />
              </Field>
              <Field label="模型名" htmlFor="ai-connect-model" hint="例如 deepseek-chat / qwen-plus">
                <Input
                  id="ai-connect-model"
                  spellCheck={false}
                  placeholder="模型标识"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                />
              </Field>
              <Field
                label="API Key"
                htmlFor="ai-connect-key"
                hint={configured ? '已存密钥；留空表示沿用，填写则覆盖' : '只写入本机 config/ai.env，页面不会回显'}
              >
                <Input
                  id="ai-connect-key"
                  type="password"
                  autoComplete="new-password"
                  spellCheck={false}
                  placeholder={configured ? '留空沿用已存密钥' : 'sk-…'}
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                />
              </Field>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="secondary"
                  onClick={() => void test()}
                  loading={testing}
                  disabled={baseUrl.trim() === '' || model.trim() === ''}
                >
                  <Link2 className="mr-1 size-4" aria-hidden="true" />
                  测试连通性
                </Button>
                <span className="flex items-center gap-1 text-caption text-text-muted">
                  <KeyRound className="size-3.5" aria-hidden="true" />
                  密钥只存本机，接口只回「已配置」
                </span>
              </div>

              <div aria-live="polite">
                {result && (
                  <Banner tone={result.ok ? 'ok' : 'danger'} title={result.ok ? '连通' : '不通'}>
                    {result.ok ? (
                      <>
                        耗时 <span className="ldst-num">{result.latency_ms}ms</span>
                        {result.model_seen && (
                          <>
                            {' · 返回模型 '}
                            <span className="ldst-num">{result.model_seen}</span>
                          </>
                        )}
                      </>
                    ) : (
                      result.error ?? '没有取到错误详情'
                    )}
                  </Banner>
                )}
                {notice && !result && (
                  <Banner
                    tone={notice.startsWith('保存失败') ? 'danger' : 'ok'}
                    title={notice.startsWith('保存失败') ? '保存失败' : '已保存'}
                  >
                    {notice.replace(/^保存失败：/, '')}
                  </Banner>
                )}
              </div>
            </div>

            <section className="rounded border border-border bg-surface-2 px-3 py-2 text-caption leading-body text-text-muted">
              <p>
                保存后写入 <span className="ldst-num">config/ai.env</span>，下一次「生成复盘」直接生效，不需要重启。
                进程环境变量若已显式设置，优先级高于这里的值。
              </p>
            </section>
          </section>
        )}
      </div>
    </Drawer>
  )
}

function presetFor(base: string): string {
  return PRESETS.find((item) => item.base === base)?.id ?? 'custom'
}

function labelOf(channels: CliChannel[], id: string): string {
  return channels.find((item) => item.id === id)?.label ?? id
}
