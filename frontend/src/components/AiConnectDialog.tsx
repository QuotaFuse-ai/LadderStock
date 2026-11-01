import { useEffect, useState } from 'react'
import { CheckCircle2, KeyRound, Link2, Sparkles } from 'lucide-react'

import { api, describeApiError, type AiTestResult, type SelfAiStatus } from '../api'
import { cn } from '../lib/cn'
import { Badge, Banner, Button, Drawer, Field, Input, SkeletonText } from './ui'

/**
 * 接入 AI（QUOT-200）：看板自己的复盘模型的一站式配置。
 *
 * 与「设置 → AI 接入」里的 peer 目标分开：这里写的是 `config/ai.env`，
 * 也就是 `ldst review` 真正读取的 `LDST_LLM_*`。密钥只写不读回。
 *
 * 只提供当前后端实现的 HTTP API（OpenAI 兼容 / Anthropic Messages）；
 * 订阅版 CLI（Codex / Claude Code / WorkBuddy）没有实现，不放假入口。
 */

interface Preset {
  id: string
  label: string
  base: string
  model: string
  hint: string
}

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

export interface AiConnectDialogProps {
  open: boolean
  onClose: () => void
  onSaved?: (status: SelfAiStatus) => void
}

export default function AiConnectDialog({ open, onClose, onSaved }: AiConnectDialogProps) {
  const [status, setStatus] = useState<SelfAiStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [preset, setPreset] = useState('deepseek')
  const [baseUrl, setBaseUrl] = useState(PRESETS[0].base)
  const [model, setModel] = useState(PRESETS[0].model)
  const [apiKey, setApiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<AiTestResult | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    setNotice(null)
    setResult(null)
    setApiKey('')
    void api.control
      .selfAi()
      .then((payload) => {
        if (cancelled) return
        setStatus(payload)
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
      const payload = await api.control.saveSelfAi(values)
      setStatus(payload)
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

  const configured = status?.configured ?? false

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
      subtitle="给看板自己的复盘生成配一个模型；密钥只存本机，只写不读回"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            关闭
          </Button>
          <Button onClick={() => void save()} loading={saving} disabled={loading || baseUrl.trim() === '' || model.trim() === ''}>
            保存并启用
          </Button>
        </>
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
          {status?.model_override && <span className="ldst-num text-caption text-text-muted">模型 {status.model_override}</span>}
          {status?.env_file && (
            <span className="ml-auto truncate text-caption text-text-muted" title={status.env_file}>
              写入 {status.env_file}
            </span>
          )}
        </section>

        {loading && <SkeletonText lines={3} />}
        {loadError && (
          <Banner tone="danger" title="读不到 AI 配置">
            {loadError}
          </Banner>
        )}

        {!loading && !loadError && (
          <>
            <section className="flex flex-col gap-2">
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
            </section>

            <section className="flex flex-col gap-4">
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
                  <Banner tone={notice.startsWith('保存失败') ? 'danger' : 'ok'} title={notice.startsWith('保存失败') ? '保存失败' : '已保存'}>
                    {notice.replace(/^保存失败：/, '')}
                  </Banner>
                )}
              </div>
            </section>

            <section className="rounded border border-border bg-surface-2 px-3 py-2 text-caption leading-body text-text-muted">
              <p>
                保存后写入 <span className="ldst-num">config/ai.env</span>，下一次「生成复盘」直接生效，不需要重启。
                进程环境变量若已显式设置，优先级高于这里的值。
              </p>
              <p className="mt-1">
                订阅版 CLI（Codex / Claude Code / WorkBuddy）当前后端没有实现，因此不提供入口；需要请走 API
                方式或在部署侧配置。
              </p>
            </section>
          </>
        )}
      </div>
    </Drawer>
  )
}

function presetFor(base: string): string {
  return PRESETS.find((item) => item.base === base)?.id ?? 'custom'
}
