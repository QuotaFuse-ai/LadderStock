import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ExternalLink, RefreshCw, Wrench } from 'lucide-react'

import { Button } from '../../components/ui'
import { CATALOG_STATUS_LABEL, readCatalog, type CatalogItem } from '../../lib/skills'
import { cn } from '../../lib/cn'
import {
  HOME_SHORTCUT_COUNT,
  pickHomeShortcuts,
  shortcutHref,
} from './homeShortcuts'
import { HomePathLabel } from './HomePathLabel'

/**
 * 首页 B 块「研究工具快捷入口」（QUOT-531）。
 *
 * 从 `content/skills/catalog.json` 按组随机抽 4 条可点条目，2×2 卡片，
 * 点一下直达 `/skills/:id` 或已承载页面（如 `/limit-up`、`/watch`）。
 * 与上一层的 AI 起步问题建议卡并存：这边只做跳转，不碰对话通道与「本轮来源」口径。
 *
 * 随机只在挂载与「换一批」时发生；刷新首页会重新挂载，自然换一批。
 */

export interface HomeResearchShortcutsProps {
  className?: string
  /** 注入随机源（测试用）；默认 `Math.random`。 */
  random?: () => number
}

const TITLE_ID = 'ldst-home-research-shortcuts-title'

function subtitle(item: CatalogItem): string {
  if (item.status === 'existing_page') return `${item.group} · 已在页面`
  if (item.status === 'unconnected') return `${item.group} · 未接通`
  return item.group
}

export function HomeResearchShortcuts({ className, random }: HomeResearchShortcutsProps) {
  const catalog = useMemo(() => readCatalog(), [])
  const [items, setItems] = useState<CatalogItem[]>(() =>
    pickHomeShortcuts(catalog, { count: HOME_SHORTCUT_COUNT, random }),
  )

  const refresh = useCallback(() => {
    setItems(pickHomeShortcuts(catalog, { count: HOME_SHORTCUT_COUNT, random }))
  }, [catalog, random])

  // 清单读不到时整块不渲染：`/skills` 页已有明确的错误块，这里不重复造空态
  if (items.length === 0) return null

  return (
    <section
      aria-labelledby={TITLE_ID}
      className={cn('flex flex-col gap-2 border-t border-border pt-3', className)}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-half">
          <HomePathLabel step="2" tone="secondary">
            开现成工具 · 点一下直达
          </HomePathLabel>
          <div className="flex items-center gap-2">
            <Wrench className="size-4 shrink-0 text-accent" aria-hidden="true" />
            <h3 id={TITLE_ID} className="text-13px font-semibold text-text">
              研究工具快捷入口
            </h3>
          </div>
          <p className="text-caption text-text-secondary">
            不想提问？按分类随机 {HOME_SHORTCUT_COUNT} 条，点一下直接打开对应研究工具。
          </p>
        </div>
        <Button
          variant="ghost"
          className="h-7 px-2 text-caption"
          onClick={refresh}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          换一批
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {items.map((item) => {
          const href = shortcutHref(item)
          if (!href) return null
          const external = item.status === 'existing_page'
          return (
            <Link
              key={item.id}
              to={href}
              aria-label={`${item.label}（${CATALOG_STATUS_LABEL[item.status]}）`}
              className="group relative flex min-h-16 items-start rounded-lg border border-border bg-surface p-3 text-left transition-colors duration-fast hover:border-accent hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <span className="flex min-w-0 flex-col gap-1 pr-5">
                <span className="whitespace-normal text-13px leading-tight text-text">
                  {item.label}
                </span>
                <span className="text-11px text-text-muted">{subtitle(item)}</span>
              </span>
              {external ? (
                <ExternalLink
                  className="absolute right-2 top-2 size-4 shrink-0 text-text-muted transition-colors duration-fast group-hover:text-accent"
                  aria-hidden="true"
                />
              ) : (
                <ArrowRight
                  className="absolute right-2 top-2 size-4 shrink-0 text-text-muted transition-colors duration-fast group-hover:text-accent"
                  aria-hidden="true"
                />
              )}
            </Link>
          )
        })}
      </div>
    </section>
  )
}
