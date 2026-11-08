import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import {
  askPageAssistant,
  PAGE_ASSISTANT_ENDPOINT,
  type SkillAssistantPayload,
} from '../src/api/research'
import { buildDockView } from '../src/lib/skills/dock'
import { bindingFor, PAGE_SKILL_BINDINGS } from '../src/lib/skills/pageBindings'
import { describeAssistantError } from '../src/components/research/sections/aiContext'

/**
 * QUOT-477：绑定页的页面助手通道。
 *
 * 15 个绑定前缀（`PAGE_SKILL_BINDINGS` 里 `pkg` 非空、且不是 `/research`）都要拿到
 * `kind === 'page'` 的通道，开放域（`/`、`/settings`）保持无通道；
 * `/research/{code}` 与 `/skills` 的既有通道不许被 page 抢走。
 */

const PAGE_PREFIXES = PAGE_SKILL_BINDINGS.filter(
  (binding) => binding.pkg !== null && binding.prefix !== '/research',
).map((binding) => binding.prefix)

describe('page 通道判定 · 15 个绑定前缀', () => {
  it('绑定前缀清单与任务点名的一致', () => {
    expect(PAGE_PREFIXES).toEqual([
      '/market',
      '/watch',
      '/intraday',
      '/review',
      '/weekly',
      '/pool',
      '/journal',
      '/validate',
      '/limit-up',
      '/my-stocks',
      '/calendar',
      '/inbox',
      '/industry',
      '/signals',
      '/news',
    ])
  })

  it('逐页断言：channel 非空且 kind === page，endpoint 指向页面助手', () => {
    for (const prefix of PAGE_PREFIXES) {
      const view = buildDockView({ pathname: prefix })
      expect(view.channel, `${prefix} 应有 page 通道`).not.toBeNull()
      expect(view.channel?.kind, `${prefix}`).toBe('page')
      if (view.channel?.kind === 'page') {
        expect(view.channel.endpoint, `${prefix}`).toBe(PAGE_ASSISTANT_ENDPOINT)
        expect(view.channel.endpoint).toBe('/api/page/assistant')
      }
      expect(view.pageKey).toBe(prefix)
    }
  })

  it('子路径同样拿到 page 通道，pageKey 保留完整路径', () => {
    for (const view of [
      buildDockView({ pathname: '/review/ai' }),
      buildDockView({ pathname: '/industry/电子' }),
      buildDockView({ pathname: '/market?tab=theme' }),
    ]) {
      expect(view.channel?.kind).toBe('page')
    }
    expect(buildDockView({ pathname: '/market?tab=theme' }).pageKey).toBe('/market')
  })

  it('开放域（/ 与 /settings）不接通道，仍走编辑 + 复制', () => {
    for (const path of ['/', '/settings']) {
      const view = buildDockView({ pathname: path })
      expect(view.channel, path).toBeNull()
    }
  })

  it('/research/600000 仍是 research 通道，不被 page 抢走', () => {
    const view = buildDockView({ pathname: '/research/600000' })
    expect(view.channel).toEqual({ kind: 'research', code: '600000' })
  })

  it('/skills 仍是 skill 通道，不被 page 抢走', () => {
    expect(buildDockView({ pathname: '/skills' }).channel).toEqual({ kind: 'skill' })
  })

  it('unconnected 包（/news）不夺通道：channel 仍是 page，但 offlineBlocking 照样拦发送', () => {
    const view = buildDockView({ pathname: '/news' })
    expect(view.channel?.kind).toBe('page')
    expect(view.offlineBlocking).toBe(true)
    expect(view.offlineNotice).toContain('资讯源未接通')
  })
})

describe('askPageAssistant 载荷', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubReply(body: unknown) {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  it('打 /api/page/assistant，载荷带 question / page_key / bound_skill / context', async () => {
    const fetchMock = stubReply({ reply: '好', model: 'test-model', fetched_at: '2026-11-08T12:00:00' })
    const payload: SkillAssistantPayload = {
      question: '今天的盘面怎么看',
      context: '{"pageKey":"/review"}',
      pageKey: '/review',
      boundSkill: 'review-journal',
    }

    const reply = await askPageAssistant(payload)
    expect(reply.reply).toBe('好')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/page/assistant')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      question: '今天的盘面怎么看',
      context: '{"pageKey":"/review"}',
      page_key: '/review',
      bound_skill: 'review-journal',
    })
  })

  it('缺省 page_key / bound_skill 时发 null，不编造', async () => {
    const fetchMock = stubReply({ reply: 'ok' })
    await askPageAssistant({ question: 'q', context: 'c' })
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    const body = JSON.parse(String(init.body)) as Record<string, unknown>
    expect(body.page_key).toBeNull()
    expect(body.bound_skill).toBeNull()
  })

  it('后端 403 / 503 的 detail 原样抛成 ApiError，供降级文案使用', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({ detail: '写请求缺少来源证据：需要本机页面 Origin' }),
          { status: 403, headers: { 'content-type': 'application/json' } },
        ),
      ),
    )
    const forbidden = await askPageAssistant({ question: 'q', context: 'c' }).catch(
      (error: unknown) => error,
    )
    expect(forbidden).toBeInstanceOf(ApiError)
    expect((forbidden as ApiError).status).toBe(403)
    expect((forbidden as ApiError).detail).toContain('来源证据')

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({ detail: '未配置 AI 通道：缺少 LDST_LLM_API_KEY。' }),
          { status: 503, headers: { 'content-type': 'application/json' } },
        ),
      ),
    )
    const unconfigured = await askPageAssistant({ question: 'q', context: 'c' }).catch(
      (error: unknown) => error,
    )
    expect((unconfigured as ApiError).status).toBe(503)
  })

  it('空回答按解析失败处理，不显示成成功', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ reply: '' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )
    await expect(askPageAssistant({ question: 'q', context: 'c' })).rejects.toThrow(/空回答/)
  })
})

describe('页面助手降级文案', () => {
  it('403（来源校验）标题明确、带服务端原话、不给重试按钮', () => {
    const info = describeAssistantError(
      new ApiError('http', 'x', 403, '写请求缺少来源证据：需要本机页面 Origin，或显式头 X-LDST-Client: 1'),
    )
    expect(info.title).toBe('请求来源校验未通过')
    expect(info.detail).toContain('来源证据')
    expect(info.code).toBe('HTTP 403')
    expect(info.retryable).toBe(false)
  })

  it('403 缺 detail 时给兜底说法，不吞掉', () => {
    const info = describeAssistantError(new ApiError('http', 'x', 403))
    expect(info.detail).toContain('LadderStock 页面内')
    expect(info.code).toBe('HTTP 403')
  })

  it('502（上游 / 网络）可重试，kind 与既有通道口径一致', () => {
    const info = describeAssistantError(new ApiError('http', 'x', 502, 'AI 请求未送达（网络/代理）'))
    expect(info.kind).toBe('upstream')
    expect(info.title).toBe('AI 请求未送达')
    expect(info.retryable).toBe(true)
  })

  it('503 未配置 Key 仍是 no-key 口径', () => {
    const info = describeAssistantError(
      new ApiError('http', 'x', 503, '未配置 AI 通道：缺少 LDST_LLM_API_KEY。'),
    )
    expect(info.kind).toBe('no-key')
    expect(info.detail).toContain('LDST_LLM_API_KEY')
  })

  it('bindingFor：/review/ai 的 page_key 前缀归到 /review（发送时用它）', () => {
    expect(bindingFor('/review/ai')?.prefix).toBe('/review')
    expect(bindingFor('/watch')?.prefix).toBe('/watch')
  })
})
