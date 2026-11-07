import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, ExternalLink, Wrench } from 'lucide-react'

import { DemoStateBadge, PageHeader } from '../../components/PageShell'
import { Badge, Card, CardBody, ErrorBlock, SectionCard } from '../../components/ui'
import { cn } from '../../lib/cn'
import {
  CATALOG_STATUS_LABEL,
  existingPageTarget,
  priorityTone,
  readCatalog,
  type CatalogItem,
  type CatalogStatus,
} from '../../lib/skills'

/**
 * `/skills` 研究工具总览（QUOT-474 / DEC-065）。
 *
 * 「研究工具」一级组的第一项：列表列的是 **方向条目（70 条）**，不是 11 个可执行包
 * ——一条方向 = 某个包的一段步骤（`package` + `steps` 映射）。
 * 数据只读入库的 `content/skills/catalog.json`（打回项 3），页面不写第二份清单、不编演示数据。
 *
 * 交互：
 * - 顶部 12 类分组 chip（可横向滚动）+ 「全部」，默认「全部」，落进 `?group=` 便于分享；
 * - 每条给 编号 · 中文名 · 优先级 · 状态 · 归属包 · 一句话说明；
 * - 点 `ready` / `partial` 条目 → `/skills/:id`（目录条目 id）；
 * - `unconnected` 条目 → 仍可进 `/skills/:id` 看步骤与提示词，只显示未接通说明；
 * - `existing_page` 条目 → 直达对应页面（例：涨停样本 → `/limit-up`）；
 * - 点归属包名 → `/skills/pkg/:name` 看整包全貌。
 */

const STATUS_TONE: Record<CatalogStatus, 'ok' | 'warn' | 'neutral' | 'info' | 'danger'> = {
  ready: 'ok',
  partial: 'warn',
  unconnected: 'neutral',
  planned: 'neutral',
  existing_page: 'info',
  not_supported: 'danger',
}

/** 可直接点进条目详情的状态（`planned` / `not_supported` 无包无步骤，不值得进详情）。 */
const DETAIL_STATUSES: readonly CatalogStatus[] = ['ready', 'partial', 'unconnected']

function StatusBadge({ status, note }: { status: CatalogStatus; note: string }) {
  return (
    <Badge tone={STATUS_TONE[status]} title={note || CATALOG_STATUS_LABEL[status]}>
      {CATALOG_STATUS_LABEL[status]}
    </Badge>
  )
}

/** 单条方向：编号 · 中文名 · 优先级 · 状态 · 归属包 · 一句话说明。 */
function CatalogRow({ item }: { item: CatalogItem }) {
  const target = existingPageTarget(item)
  // 右侧动作：能看详情 → `/skills/:id`；已在页面 → 直达该页；无包 → 只有静态说明
  const canOpen = DETAIL_STATUSES.includes(item.status) && item.package !== ''
  const whole = (
    <CardBody className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="ldst-num text-caption text-text-muted">#{item.id}</span>
        <span className="text-body font-medium text-text">{item.label}</span>
        <Badge tone={priorityTone(item.priority)}>{item.priority}</Badge>
        <StatusBadge status={item.status} note={item.note} />
        {item.package && (
          <span className="ldst-num rounded-xs border border-border px-1 text-caption text-text-secondary">
            {item.package}
            {item.steps.length > 0 && ` · 第 ${item.steps.join('/')} 步`}
          </span>
        )}
      </div>
      {item.note && <p className="text-caption leading-body text-text-muted">{item.note}</p>}
    </CardBody>
  )

  if (canOpen) {
    return (
      <Link
        to={`/skills/${item.id}`}
        className="group block rounded focus-visible:outline-none"
        aria-label={`${item.label}（${CATALOG_STATUS_LABEL[item.status]}）`}
      >
        <Card className="flex items-center transition-colors duration-fast hover:border-border-strong hover:bg-surface-2">
          <div className="min-w-0 flex-1">{whole}</div>
          <ArrowRight
            className="mr-3 size-4 shrink-0 text-text-muted transition-transform duration-fast group-hover:translate-x-0.5 group-hover:text-accent"
            aria-hidden="true"
          />
        </Card>
      </Link>
    )
  }

  if (target) {
    return (
      <Link
        to={target}
        className="group block rounded focus-visible:outline-none"
        aria-label={`${item.label}（已在页面，前往 ${target}）`}
      >
        <Card className="flex items-center border-info transition-colors duration-fast hover:border-border-strong hover:bg-surface-2">
          <div className="min-w-0 flex-1">{whole}</div>
          <ExternalLink
            className="mr-3 size-4 shrink-0 text-info transition-colors duration-fast group-hover:text-accent"
            aria-hidden="true"
          />
        </Card>
      </Link>
    )
  }

  return (
    <Card
      className={cn(
        'flex items-center',
        // 待建：虚线框；本阶段不做：压暗
        item.status === 'planned' && 'border-dashed',
        item.status === 'not_supported' && 'opacity-75',
      )}
    >
      <div className="min-w-0 flex-1">{whole}</div>
    </Card>
  )
}

export default function SkillsIndexScreen() {
  // 读入即定型：`content/skills/` 是构建期静态数据，没有异步与失败态；
  // 空清单只有一种可能——catalog.json 缺失或被改坏，此时出空态，不补假数据。
  const catalog = useMemo(() => readCatalog(), [])
  const [params, setParams] = useSearchParams()
  const activeGroup = params.get('group') ?? '全部'

  const groups = useMemo(
    () => ['全部', ...catalog.groups.map((group) => group.key)],
    [catalog.groups],
  )
  const groupLabel = useMemo(() => {
    const map = new Map(catalog.groups.map((group) => [group.key, group.label]))
    return (key: string) => map.get(key) ?? key
  }, [catalog.groups])

  const items = useMemo(
    () =>
      activeGroup === '全部'
        ? catalog.items
        : catalog.items.filter((item) => item.group === activeGroup),
    [catalog.items, activeGroup],
  )

  const selectGroup = (key: string) => {
    const next = new URLSearchParams(params)
    if (key === '全部') next.delete('group')
    else next.set('group', key)
    setParams(next, { replace: true })
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="研究工具"
        subtitle="把多步研究封成一次对话：按方向挑一条，照步骤走，或把某一步直接交给 AI。"
        badge={<DemoStateBadge state={null} />}
        meta={
          catalog.items.length > 0 ? (
            <span className="text-caption text-text-secondary">
              共 <span className="font-medium text-text">{catalog.items.length}</span> 条方向 ·{' '}
              <span className="font-medium text-text">{catalog.groups.length}</span> 类
            </span>
          ) : undefined
        }
      />

      {catalog.items.length === 0 ? (
        // 唯一可能：`content/skills/catalog.json` 读不到或被改坏。明确说出来，不静默出空列表。
        <ErrorBlock
          variant="panel"
          title="未能读到研究工具清单"
          detail="预期文件为 content/skills/catalog.json（随构建入库）。请确认该文件存在且为合法 JSON，然后重新构建。"
          code="E_SKILLS_CATALOG"
        />
      ) : (
        <>
          {/* 12 类分组筛选：横向可滚动的 chip，默认「全部」。落进 `?group=` 便于分享。 */}
          <div
            role="tablist"
            aria-label="研究工具分类"
            className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1"
          >
            {groups.map((key) => {
              const active = key === activeGroup
              const count =
                key === '全部'
                  ? catalog.items.length
                  : (catalog.groups.find((group) => group.key === key)?.count ?? 0)
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => selectGroup(key)}
                  title={key === '全部' ? '全部分类' : groupLabel(key)}
                  className={cn(
                    'inline-flex h-control shrink-0 items-center gap-1.5 whitespace-nowrap rounded-pill border px-3 text-caption font-medium transition-colors duration-fast',
                    active
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-border-strong bg-surface text-text-secondary hover:bg-surface-3 hover:text-text',
                  )}
                >
                  <span>{key}</span>
                  <span className="ldst-num text-text-muted">{count}</span>
                </button>
              )
            })}
          </div>

          {items.length === 0 ? (
            <p className="rounded border border-border bg-surface p-4 text-body text-text-secondary">
              「{activeGroup}」这一类下暂时没有条目。
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {items.map((item) => (
                <CatalogRow key={item.id} item={item} />
              ))}
            </div>
          )}

          <SectionCard title="这些方向是什么" subtitle="来源、状态口径与包的关系" tone="info">
            <div className="flex flex-col gap-3 text-body leading-body text-text-secondary">
              <p>
                一条方向 = 某个可执行包的一段步骤。点击可用 / 部分可用的条目进入步骤与提示词，
                点归属包名可看整包全貌。数据落在{' '}
                <code className="ldst-num">content/skills/</code>，页面只读不改写。
              </p>
              <div className="flex flex-wrap gap-2">
                {(Object.keys(CATALOG_STATUS_LABEL) as CatalogStatus[]).map((status) => (
                  <span key={status} className="flex items-center gap-1.5 text-caption">
                    <StatusBadge status={status} note="" />
                  </span>
                ))}
              </div>
              <p className="flex items-center gap-2 text-caption text-text-muted">
                <Wrench className="size-3.5 shrink-0" aria-hidden="true" />
                未接通、待建、本阶段不做的条目都不隐藏：步骤与提示词照常可读，执行时返回「未接通」，
                不输出任何数字。
              </p>
            </div>
          </SectionCard>
        </>
      )}
    </div>
  )
}
