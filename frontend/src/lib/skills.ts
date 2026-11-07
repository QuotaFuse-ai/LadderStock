import catalog from '../../../content/skills/catalog.json'
import index from '../../../content/skills/index.json'

/**
 * 「研究工具」数据层（QUOT-474 / DEC-067）。
 *
 * 入库数据是 `content/skills/`（人类拍板产物），前端只读、不改写、不写第二份清单：
 * - `catalog.json` —— **方向条目清单（70 条）**，`/skills` 列表页与 `/skills/:id` 条目详情只读这一份；
 *   每条带 `id` / `group` / `priority` / `package` / `steps` / `status` / `note`；
 * - `index.json` —— **包清单（11 条）**，卡片与 `/skills/pkg/:name` 包详情用；
 * - `<name>/SKILL.md` —— 六段式说明，前端只取 frontmatter 的 `description` 做一句话定位；
 * - `<name>/steps.json` —— 步骤机读版，时间轴与提示词来自这里。
 *
 * 关系：一条方向 = 某个包的一段步骤（`steps` 是包内步号数组）。
 *
 * **不在代码里手写第二份清单**（打回项 3）：`steps.json` / `SKILL.md` 全文用
 * `import.meta.glob` 一次性收进来，改数据只改 `content/skills/`，前端跟着走。
 * 没有演示数据、没有回退数据：读不到就返回空数组 / null，由页面出空态。
 */

/** 包级状态（`index.json` / `steps.json` 的 status）。 */
export type SkillStatus = 'ready' | 'partial' | 'unconnected'

/** 目录条目状态（`catalog.json` 的 status）：比包级多三种「非包」态。 */
export type CatalogStatus =
  | 'ready'
  | 'partial'
  | 'unconnected'
  | 'planned'
  | 'existing_page'
  | 'not_supported'

/** `index.json` / `steps.json` 里的单个包。 */
export interface SkillPackageEntry {
  name: string
  label: string
  status: SkillStatus
  step_count: number
  tool_count: number
  path: string
  entry: { page: string; button: string; action: string }
  blocked_by: string
}

export interface SkillStep {
  n: number
  title: string
  output: string
  tools: string[]
  prompt: string
}

export interface SkillPackageDetail extends SkillPackageEntry {
  /** `unconnected` / `partial` 包抽屉顶部的说明；无则空串 */
  offline_notice: string
  steps: SkillStep[]
  /** `SKILL.md` frontmatter 的 description；读不到为空串 */
  description: string
}

/** `catalog.json` 的一条方向条目。`package` 为空表示不落在任何包上（如已由页面承载 / 本阶段不做）。 */
export interface CatalogItem {
  id: number
  slug: string
  label: string
  group: string
  priority: string
  package: string
  /** 包内步号数组；不挂包时为空数组 */
  steps: number[]
  status: CatalogStatus
  note: string
}

export interface CatalogGroup {
  key: string
  label: string
  count: number
}

export interface SkillCatalog {
  version: number
  updated: string
  total: number
  groups: CatalogGroup[]
  items: CatalogItem[]
}

const PACKAGE_STATUS_VALUES: readonly SkillStatus[] = ['ready', 'partial', 'unconnected']

const CATALOG_STATUS_VALUES: readonly CatalogStatus[] = [
  'ready',
  'partial',
  'unconnected',
  'planned',
  'existing_page',
  'not_supported',
]

function asPackageStatus(value: unknown): SkillStatus {
  return typeof value === 'string' && (PACKAGE_STATUS_VALUES as readonly string[]).includes(value)
    ? (value as SkillStatus)
    : 'unconnected'
}

function asCatalogStatus(value: unknown): CatalogStatus {
  return typeof value === 'string' && (CATALOG_STATUS_VALUES as readonly string[]).includes(value)
    ? (value as CatalogStatus)
    : 'planned'
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asSteps(value: unknown): SkillStep[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const record = item as Record<string, unknown>
    return [
      {
        n: typeof record.n === 'number' ? record.n : 0,
        title: asString(record.title),
        output: asString(record.output),
        tools: Array.isArray(record.tools)
          ? record.tools.filter((tool): tool is string => typeof tool === 'string')
          : [],
        prompt: asString(record.prompt),
      },
    ]
  })
}

/** `steps` 字段可能是数组，也可能是 `""`（不挂包的条目）；统一成数字数组。 */
function asStepNumbers(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  return value.filter((n): n is number => typeof n === 'number')
}

function asPackageEntry(value: unknown): SkillPackageEntry | null {
  if (typeof value !== 'object' || value === null) return null
  const record = value as Record<string, unknown>
  const name = asString(record.name)
  if (!name) return null
  const entry =
    typeof record.entry === 'object' && record.entry !== null
      ? (record.entry as Record<string, unknown>)
      : {}
  return {
    name,
    label: asString(record.label),
    status: asPackageStatus(record.status),
    step_count: typeof record.step_count === 'number' ? record.step_count : 0,
    tool_count: typeof record.tool_count === 'number' ? record.tool_count : 0,
    path: asString(record.path),
    entry: {
      page: asString(entry.page),
      button: asString(entry.button),
      action: asString(entry.action),
    },
    blocked_by: asString(record.blocked_by),
  }
}

function asCatalogItem(value: unknown): CatalogItem | null {
  if (typeof value !== 'object' || value === null) return null
  const record = value as Record<string, unknown>
  const id = typeof record.id === 'number' ? record.id : NaN
  const label = asString(record.label)
  if (!Number.isFinite(id) || !label) return null
  return {
    id,
    slug: asString(record.slug),
    label,
    group: asString(record.group),
    priority: asString(record.priority),
    package: asString(record.package),
    steps: asStepNumbers(record.steps),
    status: asCatalogStatus(record.status),
    note: asString(record.note),
  }
}

/** `index.json` 原样读入；结构不对时给空清单，不补假数据。 */
function readIndex(): SkillPackageEntry[] {
  const raw = (index as { skills?: unknown }).skills
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    const entry = asPackageEntry(item)
    return entry ? [entry] : []
  })
}

/** `catalog.json` 原样读入；`groups` 与 `items` 分别兜底成空数组。 */
export function readCatalog(): SkillCatalog {
  const raw = catalog as Record<string, unknown>
  const groupsRaw = Array.isArray(raw.groups) ? raw.groups : []
  const itemsRaw = Array.isArray(raw.items) ? raw.items : []
  return {
    version: typeof raw.version === 'number' ? raw.version : 0,
    updated: asString(raw.updated),
    total: typeof raw.total === 'number' ? raw.total : itemsRaw.length,
    groups: groupsRaw.flatMap((item) => {
      if (typeof item !== 'object' || item === null) return []
      const record = item as Record<string, unknown>
      const key = asString(record.key)
      if (!key) return []
      return [
        {
          key,
          label: asString(record.label),
          count: typeof record.count === 'number' ? record.count : 0,
        },
      ]
    }),
    items: itemsRaw.flatMap((item) => {
      const entry = asCatalogItem(item)
      return entry ? [entry] : []
    }),
  }
}

/**
 * 收齐全部 `steps.json` 全文。用 `import.meta.glob` 而不是逐个 `import`：
 * 新增一个 skill 目录后前端自动带上，不在本文件维护一张名单（避免第二份清单）。
 */
const STEP_MODULES = import.meta.glob<Record<string, unknown>>('../../../content/skills/*/steps.json', {
  eager: true,
  import: 'default',
})

/** `../../../content/skills/a-share-dossier/steps.json` → `a-share-dossier` */
function nameFromGlobPath(path: string): string {
  return path.split('/').slice(-2)[0] ?? ''
}

/**
 * `SKILL.md` 是 Markdown，Vite 的 `?raw` 会在构建期把它作为字符串内联。
 * 前端只解析 frontmatter 里的 `description`，不渲染整篇 SKILL.md。
 */
const SKILL_MD_MODULES = import.meta.glob<string>('../../../content/skills/*/SKILL.md', {
  eager: true,
  query: '?raw',
  import: 'default',
})

/** 取 frontmatter 里的 `description:` 单行值（不做完整 YAML 解析，够用即可）。 */
function descriptionFromMarkdown(markdown: string): string {
  const match = /^---\n([\s\S]*?)\n---/.exec(markdown)
  if (!match) return ''
  for (const line of match[1].split('\n')) {
    if (line.startsWith('description:')) return line.slice('description:'.length).trim()
  }
  return ''
}

function buildPackageMap(): Map<string, SkillPackageDetail> {
  const map = new Map<string, SkillPackageDetail>()
  const indexByName = new Map(readIndex().map((entry) => [entry.name, entry]))

  for (const [path, raw] of Object.entries(STEP_MODULES)) {
    const name = nameFromGlobPath(path)
    const entry = indexByName.get(name)
    const stepCount = asSteps(raw.steps).length
    map.set(name, {
      name,
      label: asString(raw.label) || entry?.label || name,
      status: asPackageStatus(raw.status),
      // 步数 / 工具数优先取 `index.json`（与卡片一致），缺省回落到 `steps.json`
      step_count: entry?.step_count ?? stepCount,
      tool_count:
        entry?.tool_count ?? (Array.isArray(raw.tools_allow) ? raw.tools_allow.length : 0),
      path: entry?.path ?? `skills/${name}`,
      entry: entry?.entry ?? { page: '', button: '', action: '' },
      blocked_by: asString(raw.blocked_by) || entry?.blocked_by || '',
      offline_notice: asString(raw.offline_notice),
      steps: asSteps(raw.steps),
      description: '',
    })
  }

  for (const [path, markdown] of Object.entries(SKILL_MD_MODULES)) {
    const name = nameFromGlobPath(path)
    const detail = map.get(name)
    if (detail) detail.description = descriptionFromMarkdown(markdown)
  }

  return map
}

let packageCache: Map<string, SkillPackageDetail> | null = null

function packages(): Map<string, SkillPackageDetail> {
  if (!packageCache) packageCache = buildPackageMap()
  return packageCache
}

/** 包清单：按 `index.json` 顺序返回（人类拍板的展示顺序）。 */
export function listPackages(): SkillPackageDetail[] {
  const map = packages()
  return readIndex().flatMap((entry) => {
    const detail = map.get(entry.name)
    return detail ? [detail] : []
  })
}

/** 单包详情：`name` 是 `content/skills/` 下的目录名，也是 `index.json` 的 `name`。 */
export function getPackage(name: string | undefined): SkillPackageDetail | null {
  if (!name) return null
  return packages().get(name) ?? null
}

/** 目录条目按 `id` 取；`id` 是 `/skills/:id` 的路由参数。 */
export function getCatalogItem(id: string | number | undefined): CatalogItem | null {
  if (id === undefined) return null
  const target = typeof id === 'number' ? id : Number.parseInt(id, 10)
  if (!Number.isFinite(target)) return null
  return readCatalog().items.find((item) => item.id === target) ?? null
}

/** 包级状态徽标文案（`unconnected` 不隐藏，灰卡显示「未接通」）。 */
export const SKILL_STATUS_LABEL: Record<SkillStatus, string> = {
  ready: '可用',
  partial: '部分可用',
  unconnected: '未接通',
}

/** 目录条目状态徽标文案（六种）。 */
export const CATALOG_STATUS_LABEL: Record<CatalogStatus, string> = {
  ready: '可用',
  partial: '部分可用',
  unconnected: '未接通',
  planned: '待建',
  existing_page: '已在页面',
  not_supported: '本阶段不做',
}

/**
 * 「已在页面」条目点击直达的页面（打回项：例，涨停样本 → `/limit-up`）。
 * 这张表只登记**页面归属**，不是第二份清单——`catalog.json` 仍是唯一数据源，
 * 这里只把 `note` 里那句「已由 X 页承载」落成可跳转的路径。
 */
const EXISTING_PAGE_TARGET: Record<string, string> = {
  'skill-57': '/limit-up',
  'skill-59': '/watch',
}

export function existingPageTarget(item: CatalogItem): string | null {
  return EXISTING_PAGE_TARGET[item.slug] ?? null
}

/** `priority` 徽标色调：P0/P1 强调，P2/P3 弱化。 */
export function priorityTone(priority: string): 'danger' | 'warn' | 'neutral' {
  if (priority === 'P0') return 'danger'
  if (priority === 'P1') return 'warn'
  return 'neutral'
}
