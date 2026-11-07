import { navTitleFor } from '../nav'
import { bindingFor } from './pageBindings'
import { dockOf, skillPackage, type SkillPackage, type SkillStep, type SkillStatus } from './catalog'

/**
 * 「问 AI」抽屉的**视图模型**（QUOT-475）。
 *
 * 抽屉只有一个实现，但入口有两种：顶栏/移动端抽屉（按当前路由绑定 skill）与
 * `/research/:code` 页内的「研究助手」按钮（沿用旧 props）。两边都先把
 * 「用什么标题、预填什么开场、发什么快捷问题、能不能发」算成一个 `DockView`，
 * 抽屉组件只负责渲染，不再各自拼文案。
 *
 * 开场按 `steps.json` 的 `dock` 模板渲染（人类 2026-11-07 纠正的口径）：
 * 输入框里是**整段开场**（角色 + 分步说明 + 第 1 步指令），不是空框、也不是只放 prompt。
 * 不自动发送，旁边留「复制」后门。
 */

export interface AssistantObject {
  code?: string | null
  name?: string | null
  sector?: string | null
  date?: string | null
  tab?: string | null
}

export interface DockQuickQuestion {
  id: string
  label: string
  text: string
  /** true = 点了直接发送；false = 只填进输入框（起手式本身就是要改的那段） */
  send: boolean
}

export type DockBindingStatus = SkillStatus | 'unbound'

export interface DockView {
  /** 打开时所在的路由（不带查询串），随请求一起作为 `pageKey` */
  pageKey: string
  boundSkill: string | null
  pageLabel: string
  pkgLabel: string | null
  status: DockBindingStatus
  title: string
  subtitle: string
  /** 输入框预填内容；开放域为空串 */
  draft: string
  /** 包级未接通说明（`unconnected` / `partial`） */
  offlineNotice: string | null
  /** `unconnected` 包直接拦住发送：不许拿占位文案冒充对话能力 */
  offlineBlocking: boolean
  /** 模板里没能替换的占位（`{name}` 这类），提示用户自己补 */
  missingPlaceholders: string[]
  steps: SkillStep[]
  quickQuestions: DockQuickQuestion[]
  object: AssistantObject
  /** 已接通的对话通道：目前只有 `/research/{code}` 的页级助手 */
  channel: { code: string } | null
  autoSend: boolean
  fallbackCopy: boolean
}

export interface BuildDockViewInput {
  pathname: string
  object?: AssistantObject | null
  /** 页面显式指定绑定包（QUOT-474 的步骤级入口用）；缺省按 `pageBindings` 推断 */
  boundSkill?: string | null
  initialDraft?: string
  /** 覆盖包级未接通说明（页面知道得更多时用） */
  offlineNotice?: string | null
}

const DEFAULT_TITLE_TEMPLATE = '研究助手 · {page_label} · {pkg_label}'
const DEFAULT_DRAFT_TEMPLATE = '{opening}\n\n第 {n} 步，{title}：\n{prompt}'

const OBJECT_LABEL: Record<string, string> = {
  name: '名称',
  code: '代码',
  sector: '板块',
  date: '日期',
}

/** 路由里直接能读到的对象：`/research/600519` 的代码、`/industry/电子` 的板块名。 */
export function routeObject(pathname: string): AssistantObject {
  const parts = pathname.split('?')[0].split('/').filter(Boolean)
  if (parts[0] === 'research' && parts[1]) return { code: decodeURIComponent(parts[1]) }
  if (parts[0] === 'industry' && parts[1]) return { sector: decodeURIComponent(parts[1]) }
  return {}
}

export function pageLabelFor(pathname: string): string {
  const path = pathname.split('?')[0] || '/'
  return bindingFor(path)?.label ?? navTitleFor(path)
}

function labelWithStatus(pkg: SkillPackage): string {
  if (pkg.status === 'unconnected') return `${pkg.label}（未接通）`
  if (pkg.status === 'partial') return `${pkg.label}（部分未接通）`
  return pkg.label
}

function quickQuestions(label: string, tab: string): string[] {
  if (tab === 'research') {
    return [
      `${label}这份研究的裁决点是什么`,
      `${label}哪些数据有缺口`,
      `${label}反证部分说了什么`,
    ]
  }
  return [
    `${label}最近的走势怎么样`,
    `${label}下一份财报要看什么`,
    `${label}有哪些值得留意的事件`,
  ]
}

/** `{name}` 这类占位：有值就换掉，没值就原样留着并记一笔。 */
function fillObject(text: string, object: AssistantObject): string {
  let out = text
  for (const key of Object.keys(OBJECT_LABEL)) {
    const value = (object as Record<string, string | null | undefined>)[key]
    if (typeof value === 'string' && value.trim() !== '') {
      out = out.split(`{${key}}`).join(value)
    }
  }
  return out
}

function remainingPlaceholders(text: string): string[] {
  const found = new Set<string>()
  for (const match of text.matchAll(/\{([a-z_]+)\}/g)) found.add(match[1])
  return [...found]
}

function renderDraft(
  pkg: SkillPackage,
  object: AssistantObject,
  initialDraft?: string,
): { draft: string; missing: string[] } {
  if (initialDraft !== undefined) {
    const draft = fillObject(initialDraft, object)
    return { draft, missing: remainingPlaceholders(draft) }
  }

  const dock = dockOf(pkg)
  const step = pkg.steps[0] ?? null
  if (!step) {
    const draft = fillObject(dock.opening ?? '', object)
    return { draft, missing: remainingPlaceholders(draft) }
  }

  const rendered = (dock.draft_template ?? DEFAULT_DRAFT_TEMPLATE)
    .split('{opening}')
    .join(dock.opening ?? '')
    .split('{n}')
    .join(String(step.n))
    .split('{title}')
    .join(step.title)
    .split('{prompt}')
    .join(step.prompt)
  const draft = fillObject(rendered, object)
  return { draft, missing: remainingPlaceholders(draft) }
}

export function buildDockView(input: BuildDockViewInput): DockView {
  const path = input.pathname.split('?')[0] || '/'
  const binding = bindingFor(path)
  const boundSkill = input.boundSkill !== undefined ? input.boundSkill : (binding?.pkg ?? null)
  const pkg = skillPackage(boundSkill)
  const pageLabel = binding?.label ?? pageLabelFor(path)
  const object = input.object ?? {}

  const title = pkg
    ? (pkg.dock?.title_template ?? DEFAULT_TITLE_TEMPLATE)
        .split('{page_label}')
        .join(pageLabel)
        .split('{pkg_label}')
        .join(labelWithStatus(pkg))
    : `研究助手 · ${pageLabel}`

  const offlineNotice = input.offlineNotice ?? pkg?.offlineNotice ?? null
  const offlineBlocking = pkg?.status === 'unconnected'
  const channel = object.code && !offlineBlocking ? { code: object.code } : null

  const rendered = pkg ? renderDraft(pkg, object, input.initialDraft) : { draft: '', missing: [] }
  const draft = rendered.draft

  const fallbackLabel = object.name ?? object.code ?? pkg?.label ?? '这一页'
  const starter: DockQuickQuestion[] =
    draft && pkg?.steps[0]
      ? [
          {
            id: 'starter',
            label: `本页研究起手式 · ${pkg.steps[0].title}`,
            text: draft,
            send: false,
          },
        ]
      : []

  const quick = quickQuestions(fallbackLabel, object.tab ?? 'overview').map((text, index) => ({
    id: `quick-${index}`,
    label: text,
    text,
    send: true,
  }))

  const dock = dockOf(pkg)
  return {
    pageKey: path,
    boundSkill,
    pageLabel,
    pkgLabel: pkg?.label ?? null,
    status: pkg?.status ?? 'unbound',
    title,
    subtitle: pkg
      ? `页面技能 · ${pkg.label}${object.date ? ` · ${object.date}` : ''}`
      : `开放域 · ${pageLabel}`,
    draft,
    offlineNotice,
    offlineBlocking,
    missingPlaceholders: rendered.missing,
    steps: pkg?.steps ?? [],
    quickQuestions: [...starter, ...quick],
    object,
    channel,
    autoSend: dock.auto_send,
    fallbackCopy: dock.fallback_copy,
  }
}

export interface LegacyDockViewInput {
  code: string | null
  name: string | null
  tab: string
  date: string
  /** 页面已经取到的概览，避免为上下文重复取一次 */
  overview?: unknown
  offlineNotice?: string | null
}

/**
 * `/research/:code` 旧 props 的视图（QUOT-454 口径）：标题、快捷问题、上下文摘要
 * 与那个版本逐字一致。`ResearchAiDock` 没拿到 `pageKey` 时走这里，
 * 保证既有调用方（含单测）行为不变。
 */
export function buildLegacyDockView(input: LegacyDockViewInput): DockView {
  const label = input.name ?? input.code ?? '这只'
  const title = `研究助手 · 个股深挖${input.name ? ` · ${input.name}` : input.code ? ` · ${input.code}` : ''}`
  return {
    pageKey: input.code ? `/research/${input.code}` : '/research',
    boundSkill: null,
    pageLabel: '个股深挖',
    pkgLabel: null,
    status: 'unbound',
    title,
    subtitle: input.date ? `页级上下文 · ${input.tab} · ${input.date}` : '页级上下文',
    draft: '',
    offlineNotice: input.offlineNotice ?? null,
    offlineBlocking: false,
    missingPlaceholders: [],
    steps: [],
    quickQuestions: quickQuestions(label, input.tab).map((text, index) => ({
      id: `quick-${index}`,
      label: text,
      text,
      send: true,
    })),
    object: { code: input.code, name: input.name, date: input.date, tab: input.tab },
    channel: input.code ? { code: input.code } : null,
    autoSend: false,
    fallbackCopy: true,
  }
}

/** 抽屉里「对象」那一行的可读文案；缺值不编。 */
export function describeObject(object: AssistantObject): string | null {
  const parts: string[] = []
  if (object.name) parts.push(object.name)
  if (object.code) parts.push(object.code)
  if (object.sector) parts.push(object.sector)
  if (object.date) parts.push(object.date)
  return parts.length > 0 ? parts.join(' · ') : null
}

export { OBJECT_LABEL }
