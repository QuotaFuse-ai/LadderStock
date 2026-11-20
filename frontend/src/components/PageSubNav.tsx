import { useLocation } from 'react-router-dom'

import { NAV_SECTIONS } from '../lib/nav'
import { NavPills } from './ui'

/**
 * 栏目内子导航（2026-11-16 返工）：渲染在**顶栏面包屑右侧**，不再占内容区一行。
 *
 * 背景：原来 `PageHeader` 会在内容区第一行再渲染一遍同一份 `NAV_SECTIONS`
 * （`SectionNavPills`），于是页面出现「侧栏 + 内容区」两处同组入口；组内只有一页时
 * 第二处又不出现，全站看上去有的页面两个导航、有的一个。现在只保留两处：
 * 侧栏（全局）+ 顶栏（当前栏目内的兄弟页），且顶栏这条与面包屑同一行，不额外占高。
 *
 * 组内只有一页（首页 / 日志 / 个股深挖 / 设置）时返回 `null`，不渲染空 `<nav>`。
 */
function sharedPath(pages: readonly { to: string }[]): string | undefined {
  const counts = new Map<string, number>()
  for (const page of pages) {
    const path = page.to.split('?')[0]
    counts.set(path, (counts.get(path) ?? 0) + 1)
  }
  return [...counts].find(([, count]) => count > 1)?.[0]
}

export function PageSubNav({ className }: { className?: string }) {
  const { pathname } = useLocation()
  const section = NAV_SECTIONS.find((entry) =>
    entry.pages.some((page) => page.to.split('?')[0] === pathname),
  )
  if (!section || section.pages.length < 2) return null
  return (
    <NavPills
      items={section.pages}
      basePath={sharedPath(section.pages)}
      size="sm"
      className={className}
    />
  )
}
