import indexDocument from '../../../../content/skills/index.json'

/**
 * 页面技能包的只读数据层（QUOT-475）。
 *
 * 数据源：仓库根目录 `content/skills/`（JSON 原样保留中文，构建期打包进前端，
 * 运行期不联网）。`index.json` 由 QUOT-474 维护 11 个包的 `name/label/status`；
 * 每个包的 `steps.json` 里另有 `offline_notice` 与 `dock`（开场模板）。
 *
 * 读取一律带兜底：文件缺失 / 字段类型不对只降级成「没有这个包」，
 * 不能因此让顶栏按钮点不开抽屉。
 */

export type SkillStatus = 'ready' | 'partial' | 'unconnected'

export interface SkillStep {
  n: number
  title: string
  prompt: string
}

export interface SkillDock {
  title_template: string | null
  opening: string | null
  draft_template: string | null
  auto_send: boolean
  fallback_copy: boolean
}

export interface SkillPackage {
  name: string
  label: string
  status: SkillStatus
  /** `unconnected` / `partial` 时的一句话说明；读不到就是 null */
  offlineNotice: string | null
  steps: SkillStep[]
  dock: SkillDock | null
}

interface IndexEntry {
  name: string
  label: string
  status: SkillStatus
  offline_notice?: string | null
}

const STATUSES: readonly SkillStatus[] = ['ready', 'partial', 'unconnected']

const DEFAULT_DOCK: SkillDock = {
  title_template: null,
  opening: null,
  draft_template: null,
  auto_send: false,
  fallback_copy: true,
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function asStatus(value: unknown): SkillStatus {
  return STATUSES.includes(value as SkillStatus) ? (value as SkillStatus) : 'ready'
}

function parseSteps(value: unknown): SkillStep[] {
  if (!Array.isArray(value)) return []
  const steps: SkillStep[] = []
  for (const item of value) {
    const record = asRecord(item)
    if (!record) continue
    const prompt = asString(record.prompt)
    const title = asString(record.title)
    const n = typeof record.n === 'number' ? record.n : steps.length + 1
    if (!prompt || !title) continue
    steps.push({ n, title, prompt })
  }
  return steps
}

function parseDock(value: unknown): SkillDock | null {
  const record = asRecord(value)
  if (!record) return null
  return {
    title_template: asString(record.title_template),
    opening: asString(record.opening),
    draft_template: asString(record.draft_template),
    auto_send: record.auto_send === true,
    fallback_copy: record.fallback_copy !== false,
  }
}

/** `../../../../content/skills/<pkg>/steps.json` → `<pkg>` */
function packageNameFromGlobKey(key: string): string {
  const parts = key.split('/')
  return parts[parts.length - 2] ?? ''
}

const stepDocuments = import.meta.glob<Record<string, unknown>>(
  '../../../../content/skills/*/steps.json',
  { eager: true, import: 'default' },
)

function buildStepIndex(): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {}
  for (const [key, value] of Object.entries(stepDocuments)) {
    const name = packageNameFromGlobKey(key)
    const record = asRecord(value)
    if (name && record) out[name] = record
  }
  return out
}

const STEP_INDEX = buildStepIndex()

function indexEntries(): IndexEntry[] {
  const root = asRecord(indexDocument)
  const skills = root?.skills
  if (!Array.isArray(skills)) return []
  const entries: IndexEntry[] = []
  for (const item of skills) {
    const record = asRecord(item)
    if (!record) continue
    const name = asString(record.name)
    const label = asString(record.label)
    if (!name || !label) continue
    entries.push({
      name,
      label,
      status: asStatus(record.status),
      offline_notice: asString(record.offline_notice),
    })
  }
  return entries
}

function buildSkills(): Record<string, SkillPackage> {
  const out: Record<string, SkillPackage> = {}
  for (const entry of indexEntries()) {
    const steps = STEP_INDEX[entry.name] ?? {}
    out[entry.name] = {
      name: entry.name,
      label: entry.label,
      status: entry.status,
      // index.json 优先；QUOT-474 的完整版可能只在 steps.json 里带这句话
      offlineNotice: entry.offline_notice ?? asString(steps.offline_notice),
      steps: parseSteps(steps.steps),
      dock: parseDock(steps.dock),
    }
  }
  return out
}

export const SKILL_PACKAGES: Record<string, SkillPackage> = buildSkills()

export function skillPackage(name: string | null | undefined): SkillPackage | null {
  if (!name) return null
  return SKILL_PACKAGES[name] ?? null
}

export function dockOf(pkg: SkillPackage | null): SkillDock {
  return pkg?.dock ?? DEFAULT_DOCK
}
