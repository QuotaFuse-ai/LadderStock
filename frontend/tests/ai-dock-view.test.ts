import { describe, expect, it } from 'vitest'

import {
  buildDockView,
  buildLegacyDockView,
  describeObject,
  pageLabelFor,
  routeObject,
} from '../src/lib/skills/dock'
import { skillPackage } from '../src/lib/skills/catalog'

/**
 * QUOT-475：抽屉视图模型（不渲染组件）。
 *
 * 人类 2026-11-07 纠正的口径在这里落到断言上：输入框里是**整段开场**
 * （角色 + 分步说明 + 第 1 步指令），不是空框、也不是只放 prompt；不自动发送；
 * `unconnected` 包拦住发送，`partial` 只提示不拦。
 */

describe('标题与页面绑定', () => {
  it('/market → 市场 · 盘面复盘', () => {
    const view = buildDockView({ pathname: '/market' })
    expect(view.title).toBe('研究助手 · 市场 · 盘面复盘')
    expect(view.pageKey).toBe('/market')
    expect(view.boundSkill).toBe('a-share-tape')
    expect(view.status).toBe('ready')
  })

  it('/my-stocks → 我的股票 · 持仓体检', () => {
    expect(buildDockView({ pathname: '/my-stocks' }).title).toBe(
      '研究助手 · 我的股票 · 持仓体检',
    )
  })

  it('/research/600519 → 研究工具 · 个股全景', () => {
    expect(buildDockView({ pathname: '/research/600519' }).title).toBe(
      '研究助手 · 研究工具 · 个股全景',
    )
  })

  it('/news → 产业链资讯（未接通）', () => {
    const view = buildDockView({ pathname: '/news' })
    expect(view.title).toBe('研究助手 · 市场 · 产业链资讯（未接通）')
    expect(view.status).toBe('unconnected')
    expect(view.offlineBlocking).toBe(true)
    expect(view.offlineNotice).toContain('资讯源未接通')
  })

  it('首页没有包：只写页面名，也不预填开场', () => {
    const view = buildDockView({ pathname: '/' })
    expect(view.title).toBe('研究助手 · 首页')
    expect(view.boundSkill).toBeNull()
    expect(view.draft).toBe('')
    expect(view.status).toBe('unbound')
    expect(view.quickQuestions).toHaveLength(3)
    expect(view.quickQuestions.every((question) => question.send)).toBe(true)
  })

  it('查询串不进标题也不进 pageKey', () => {
    const view = buildDockView({ pathname: '/market?tab=theme' })
    expect(view.title).toBe('研究助手 · 市场 · 盘面复盘')
    expect(view.pageKey).toBe('/market')
  })
})

describe('开场预填（steps.json 的 dock 模板）', () => {
  it('输入框里是整段开场，不是空框也不是单条 prompt', () => {
    const view = buildDockView({ pathname: '/market' })
    const pkg = skillPackage('a-share-tape')
    expect(pkg).not.toBeNull()
    expect(view.draft.startsWith('我们来复盘今天的盘面。')).toBe(true)
    expect(view.draft).toContain(`第 1 步，${pkg!.steps[0].title}：`)
    expect(view.draft).toContain(pkg!.steps[0].prompt)
    expect(view.autoSend).toBe(false)
    expect(view.fallbackCopy).toBe(true)
  })

  it('起手式快捷项只填入输入框（不直接发送）', () => {
    const view = buildDockView({ pathname: '/my-stocks' })
    const starter = view.quickQuestions[0]
    expect(starter.label).toBe('本页研究起手式 · 读持仓与口径')
    expect(starter.send).toBe(false)
    expect(starter.text).toBe(view.draft)
  })

  it('模板里的 {name} 取不到就原样保留并记下来（{code} 从路由取到）', () => {
    const view = buildDockView({ pathname: '/research/600519' })
    expect(view.draft).toContain('{name}')
    expect(view.draft).not.toContain('{code}')
    expect(view.missingPlaceholders).toContain('name')
    expect(view.missingPlaceholders).not.toContain('code')
  })

  it('带对象时占位换成真实值，不再报缺', () => {
    const view = buildDockView({
      pathname: '/research/600519',
      object: { code: '600519', name: '贵州茅台', date: '2026-11-06', tab: 'overview' },
    })
    expect(view.draft).toContain('「贵州茅台（600519）」')
    expect(view.missingPlaceholders).toEqual([])
    expect(view.channel).toEqual({ kind: 'research', code: '600519' })
  })

  it('/industry/电子 的 {sector} 从路由里取', () => {
    const view = buildDockView({
      pathname: '/industry/电子',
      object: routeObject('/industry/电子'),
    })
    expect(view.draft).toContain('我们来梳理「电子」这个板块。')
    expect(view.missingPlaceholders).toEqual([])
  })

  it('initialDraft 优先于包模板（步骤级入口用）', () => {
    const view = buildDockView({
      pathname: '/market',
      initialDraft: '自定义开场：第 {n} 步',
    })
    expect(view.draft).toBe('自定义开场：第 {n} 步')
    expect(view.missingPlaceholders).toEqual(['n'])
  })
})

describe('未接通与部分未接通', () => {
  it('unconnected：说明来自包数据，发送被拦（QUOT-477 起通道不因未接通被夺）', () => {
    const view = buildDockView({ pathname: '/news', object: { code: '600000' } })
    expect(view.offlineBlocking).toBe(true)
    expect(view.channel).toEqual({ kind: 'research', code: '600000' })
    expect(view.offlineNotice).toBe(skillPackage('wire-desk')?.offlineNotice)
  })

  it('partial：只提示「部分段落未接通」，不拦发送', () => {
    const view = buildDockView({ pathname: '/market', boundSkill: 'macro-crossasset' })
    expect(view.status).toBe('partial')
    expect(view.offlineBlocking).toBe(false)
    expect(view.title).toBe('研究助手 · 市场 · 宏观与跨资产（部分未接通）')
    expect(view.offlineNotice).toContain('美债与全球政策利率段未接通')
  })

  it('页面给了更具体的说明时以页面为准', () => {
    const view = buildDockView({
      pathname: '/market',
      boundSkill: 'macro-crossasset',
      offlineNotice: '这一页只有 A 股段。',
    })
    expect(view.offlineNotice).toBe('这一页只有 A 股段。')
  })
})

/**
 * QUOT-476：对话通道从「有没有 code」扩成两种——
 * 个股页走 `/api/research/{code}/assistant`（research），
 * `/skills` 页没有 code，走 `/api/skills/assistant`（skill）。
 *
 * QUOT-477：再扩到第三种 `page`（`/api/page/assistant`，见 page-channel.test.ts），
 * 并且通道判定不再看 `offlineBlocking`：未接通包保留通道、由抽屉拦发送。
 */
describe('QUOT-476：对话通道类型', () => {
  it('/skills 开放域页：skill 通道（没有 code 也能发）', () => {
    const view = buildDockView({ pathname: '/skills/a-share-tape' })
    expect(view.channel).toEqual({ kind: 'skill' })
  })

  it('/skills 绑定已接通包：仍是 skill 通道', () => {
    const view = buildDockView({
      pathname: '/skills/pkg/a-share-tape',
      boundSkill: 'a-share-tape',
    })
    expect(view.status).toBe('ready')
    expect(view.channel).toEqual({ kind: 'skill' })
  })

  it('/skills 绑定未接通包：skill 通道保留，发送被拦', () => {
    const view = buildDockView({ pathname: '/skills/pkg/wire-desk', boundSkill: 'wire-desk' })
    expect(view.offlineBlocking).toBe(true)
    expect(view.channel).toEqual({ kind: 'skill' })
  })

  it('/research 有 code：research 通道（routeObject 提供对象时）', () => {
    expect(
      buildDockView({ pathname: '/research/600519', object: routeObject('/research/600519') })
        .channel,
    ).toEqual({
      kind: 'research',
      code: '600519',
    })
  })

  it('/research 未接通包压在 code 前面：research 通道仍在，发送被拦', () => {
    const view = buildDockView({ pathname: '/research/600519', boundSkill: 'wire-desk' })
    expect(view.offlineBlocking).toBe(true)
    expect(view.channel).toEqual({ kind: 'research', code: '600519' })
  })

  it('非 /skills 的开放域页（首页）仍没有通道', () => {
    expect(buildDockView({ pathname: '/' }).channel).toBeNull()
  })
})

describe('路由对象与旧视图', () => {
  it('routeObject 从路由里读代码 / 板块', () => {
    expect(routeObject('/research/600519')).toEqual({ code: '600519' })
    expect(routeObject('/industry/%E7%94%B5%E5%AD%90')).toEqual({ sector: '电子' })
    expect(routeObject('/market')).toEqual({})
  })

  it('pageLabelFor 与面包屑同口径', () => {
    expect(pageLabelFor('/review/ai')).toBe('复盘')
    expect(pageLabelFor('/nope')).toBe('工作空间')
  })

  it('describeObject 只写有值的字段', () => {
    expect(describeObject({ name: '浦发银行', code: '600000' })).toBe('浦发银行 · 600000')
    expect(describeObject({})).toBeNull()
  })

  it('旧 props 视图保持 QUOT-454 的标题与快捷问题', () => {
    const view = buildLegacyDockView({
      code: '600000',
      name: '浦发银行',
      tab: 'overview',
      date: '2026-11-06',
    })
    expect(view.title).toBe('研究助手 · 个股深挖 · 浦发银行')
    expect(view.draft).toBe('')
    expect(view.quickQuestions.map((question) => question.label)).toEqual([
      '浦发银行最近的走势怎么样',
      '浦发银行下一份财报要看什么',
      '浦发银行有哪些值得留意的事件',
    ])
  })
})
